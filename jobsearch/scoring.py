"""Скоринг: дешёвый лексический отбор → LLM-триаж пачками → глубокий разбор топа."""
import json
import re

from . import config, i18n, llm



def _lk(log, key: str, **fmt) -> None:
    """Строка журнала на языке интерфейса (log приходит из пайплайна)."""
    text = i18n.t(config.load()["ui"]["lang"], key)
    log(text.format(**fmt) if fmt else text)


STOPWORDS = {
    "and", "the", "for", "with", "you", "are", "our", "have", "will", "that", "this",
    "your", "who", "not", "all", "can", "als", "und", "der", "die", "das", "для",
    "как", "что", "или", "the", "job", "work", "team", "experience", "years",
}


def profile_terms(cfg: dict, cv: str) -> set:
    # при приоритете на навыки повторяем навыки, чтобы поднять их вес в отборе
    prio = cfg["search"].get("match_priority", "both")
    skills = cfg["profile"].get("skills", "")
    skills_block = (skills + " ") * (3 if prio == "skills" else 1)
    raw = " ".join([
        cfg["profile"].get("roles", "") if prio != "skills" else "",
        skills_block,
        cfg["profile"].get("summary", ""),
        cfg["search"].get("keywords_include", ""),
        cv[:4000],
    ]).lower()
    words = re.findall(r"[a-zа-яё0-9+#.]{3,}", raw)
    return {w for w in words if w not in STOPWORDS}


def lexical_score(job: dict, terms: set) -> int:
    title_words = set(re.findall(r"[a-zа-яё0-9+#.]{3,}", (job.get("title") or "").lower()))
    desc_words = set(re.findall(r"[a-zа-яё0-9+#.]{3,}", (job.get("description") or "")[:3000].lower()))
    return 3 * len(title_words & terms) + len(desc_words & terms)


_PRIORITY_NOTE = {
    "role": "ПРИОРИТЕТ ОЦЕНКИ: в первую очередь совпадение по РОЛИ/должности. "
            "Навыки — вторичны.",
    "skills": "ПРИОРИТЕТ ОЦЕНКИ: в первую очередь совпадение по НАВЫКАМ/технологиям кандидата, "
              "а не по названию роли. Вакансия с другим названием должности, но подходящая по "
              "навыкам кандидата, — это хорошее совпадение (напр. кандидат SAP-консультант со "
              "знанием Java подходит на Java Integration Engineer).",
    "both": "ПРИОРИТЕТ ОЦЕНКИ: учитывай И роль, И навыки. Совпадение по навыкам может "
            "компенсировать неточное совпадение по названию должности.",
}


def _lang_banner(cfg: dict) -> str:
    """Языковой баннер в НАЧАЛО промпта. Промпты написаны по-русски, поэтому при
    другом языке результатов инструкцию в середине текста модель часто игнорирует —
    явное требование первой строкой работает надёжно."""
    code = cfg.get("ui", {}).get("output_lang", "ru")
    if code == "ru":
        return ""
    return (f"ЯЗЫК ОТВЕТА (КРИТИЧНО): все текстовые значения в JSON пиши {i18n.out_lang(cfg)}. "
            f"По-русски НЕ писать, независимо от языка этой инструкции.\n\n")


def _profile_block(cfg: dict) -> str:
    p, s = cfg["profile"], cfg["search"]
    visa = "нужна виза/спонсорство" if p.get("visa_required") else "виза не нужна"
    if p.get("visa_note"):
        visa += f" ({p['visa_note']})"
    priority = _PRIORITY_NOTE.get(s.get("match_priority", "both"), _PRIORITY_NOTE["both"])
    return (
        f"Роли: {p.get('roles') or '—'}\n"
        f"Ключевые навыки/технологии: {p.get('skills') or '—'}\n"
        f"Уровень: {p.get('seniority') or '—'}\n"
        f"О кандидате: {p.get('summary') or '—'}\n"
        f"Зарплата: {p.get('salary') or '—'}\n"
        f"Формат: {p.get('work_format')}\n"
        f"Языки: {p.get('languages') or '—'}\n"
        f"Локации поиска: {s.get('locations')}\n"
        f"Право на работу: {visa}\n"
        f"{priority}"
    )


TRIAGE_PROMPT = """Ты — ассистент по поиску работы. Профиль кандидата:

{profile}

Выдержка из CV кандидата:
{cv}

Ниже вакансии. Для каждой оцени совпадение с кандидатом 0–100 и укажи,
похоже ли это на объявление рекрутингового агентства (а не самой компании).
Учитывай роль, стек, уровень, локацию.
ЖЁСТКИЕ ОГРАНИЧЕНИЯ (сильно снижай балл, если нарушены):
- право на работу: если вакансия только для конкретной страны/региона, где у кандидата
  нет права на работу (напр. «US only», «must be authorized to work in the US»), а визу/
  спонсорство кандидат не запрашивает — балл не выше 30;
- язык: если требуется рабочий язык, которым кандидат не владеет на нужном уровне
  (смотри «Языки» в профиле), — балл не выше 40.
ВСЕГДА возвращай ТОЛЬКО JSON-массив, без вопросов и пояснений — даже если данных
о кандидате мало, оценивай по тому, что есть (в первую очередь по CV):
[{{"i": <номер>, "match": <0-100>, "agency": true/false, "reason": "<1 короткая фраза {lang}>"}}]

ВАКАНСИИ:
{jobs}"""


def triage(jobs: list, cfg: dict, log, cv: str = "") -> list:
    """Проставляет job['score'], job['is_agency'], job['reason'] — пачками параллельно."""
    model = cfg["llm"].get("triage_model", "haiku")
    claude_bin = cfg["llm"].get("claude_bin", "claude")
    workers = int(cfg["search"].get("parallelism", 5))
    lang = i18n.out_lang(cfg)
    profile = _profile_block(cfg)
    cv_excerpt = (cv or "").strip()[:2500] or "(CV не загружено)"
    batch_size = 8
    batches = [jobs[s:s + batch_size] for s in range(0, len(jobs), batch_size)]
    done = {"n": 0}

    def process(batch):
        listing = "\n\n".join(
            f"[{i}] {j.get('title', '')} — {j.get('company', '')} ({j.get('location') or 'локация не указана'})\n"
            f"{(j.get('description') or '')[:700]}"
            for i, j in enumerate(batch)
        )
        result = llm.ask_json(
            _lang_banner(cfg) + TRIAGE_PROMPT.format(profile=profile, cv=cv_excerpt, jobs=listing, lang=lang),
            model=model, claude_bin=claude_bin, timeout=300,
        )
        for item in result if isinstance(result, list) else []:
            try:
                j = batch[int(item["i"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            j["score"] = max(0, min(100, int(item.get("match", 0))))
            j["is_agency"] = bool(item.get("agency")) or j.get("is_agency", False)
            j["reason"] = str(item.get("reason", ""))[:300 * (2 if "-" in cfg.get("ui", {}).get("output_lang", "ru") else 1)]
        done["n"] += 1
        _lk(log, "log_triage_batch", done=done["n"], total=len(batches))

    for r in llm.pmap(process, batches, workers=workers):
        if isinstance(r, Exception):
            done["n"] += 1
            _lk(log, "log_triage_batch_err", error=r)
    return jobs


PROFILE_FROM_CV_PROMPT = """Ниже CV кандидата. Заполни поля профиля для поиска работы.
Верни ТОЛЬКО JSON-объект:
{{
  "roles": "<2-4 подходящие должности через запятую, по-английски>",
  "skills": "<8-15 ключевых навыков/технологий кандидата через запятую, по-английски: языки, фреймворки, инструменты, домены>",
  "seniority": "<уровень: Junior/Middle/Senior/Staff/Lead>",
  "summary": "<3-4 предложения {lang}: опыт, стек, сильные стороны>",
  "languages": "<языки кандидата, если указаны, иначе пустая строка>"
}}

CV:
{cv}"""


def profile_from_cv(cfg: dict, cv: str) -> dict:
    """Заполняет пустые поля профиля из CV. Возвращает обновлённый cfg."""
    data = llm.ask_json(
        PROFILE_FROM_CV_PROMPT.format(cv=cv[:6000], lang=i18n.out_lang(cfg)),
        model=cfg["llm"].get("triage_model", "haiku"),
        claude_bin=cfg["llm"].get("claude_bin", "claude"),
        provider=cfg["llm"].get("provider", "claude_cli"),
        timeout=300,
    )
    if isinstance(data, dict):
        for key in ("roles", "skills", "seniority", "summary", "languages"):
            if data.get(key) and not cfg["profile"].get(key):
                cfg["profile"][key] = str(data[key]).strip()
    return cfg


DEEP_PROMPT = """Ты — карьерный консультант. Профиль кандидата:

{profile}

CV кандидата:
{cv}

Вакансия: {title} — {company} ({location})
Ссылка: {url}
Описание:
{description}

ВСЕ текстовые поля пиши {lang}.
Верни ТОЛЬКО JSON-объект:
{{
  "match": <0-100, честная оценка совпадения>,
  "reason": "<2-3 предложения: почему подходит и что может помешать>",
  "cv_changes": ["<конкретная правка CV под эту вакансию>", ...],
  "linkedin_changes": ["<конкретная правка профиля LinkedIn>", ...],
  "cover_hint": "<1-2 предложения: на что сделать упор в отклике>"
}}"""

RESEARCH_BLOCK = """
Дополнительно поищи в интернете то, чего обычно нет в самом объявлении о вакансии:
- вилку зарплаты для этой роли/уровня/локации у этой компании, а если по компании
  ничего нет — вилку по рынку (укажи явно, что это оценка по рынку, а не по компании);
  ищи на Glassdoor, Kununu (особенно хорош для немецких и европейских компаний),
  levels.fyi, Payscale;
- размер компании, стадию (стартап/скейлап/устоявшаяся), последний раунд
  финансирования, если есть (Crunchbase и подобные);
- рейтинг сотрудников и заметные плюсы/минусы из отзывов (Glassdoor/Kununu).

Указывай ТОЛЬКО то, что реально нашёл со ссылкой на источник. Если по конкретному
пункту ничего надёжного не нашлось — так и напиши («данные не найдены»), не выдумывай
цифры и факты. Текст в salary_estimate и company_insights пиши {lang}.

К JSON-объекту выше добавь поля:
  "salary_estimate": "<вилка с валютой и явной пометкой company-specific/рыночная оценка, или 'не найдено'>",
  "company_insights": ["<факт о компании с опорой на найденное: размер, стадия, финансирование, рейтинг, культура>", ...],
  "sources": ["<URL, на которые опирался>", ...]
"""


def deep_analyze(job: dict, cfg: dict, cv: str, log, research: bool = True) -> None:
    """Уточняет score и добавляет advice для одной вакансии (пишет в job).
    При research=True дополнительно ищет в интернете зарплату и факты о компании
    (Glassdoor/Kununu/levels.fyi и т.п.) — медленнее, но даёт то, чего нет в вакансии."""
    description = job.get("description") or ""
    if len(description) < 300 and job.get("url"):
        from .collectors import crawler
        fetched = crawler.fetch_job_text(job["url"])
        if len(fetched) > len(description):
            description = fetched
    lang = i18n.out_lang(cfg)
    prompt = _lang_banner(cfg) + DEEP_PROMPT.format(
        profile=_profile_block(cfg),
        cv=cv[:6000] or "(CV не загружено)",
        title=job.get("title", ""), company=job.get("company", ""),
        location=job.get("location", ""), url=job.get("url", ""),
        description=description[:6000] or "(описания нет)",
        lang=lang,
    )
    if research:
        prompt += RESEARCH_BLOCK.format(lang=lang)
    try:
        result = llm.ask_json(
            prompt,
            model=cfg["llm"].get("deep_model", ""),
            claude_bin=cfg["llm"].get("claude_bin", "claude"),
            provider=cfg["llm"].get("provider", "claude_cli"),
            timeout=900 if research else 600,
            allowed_tools=["WebSearch", "WebFetch"] if research else None,
        )
    except llm.AuthError:
        raise      # без входа в модель прогон смысла не имеет
    except llm.ClaudeError as e:
        _lk(log, "log_deep_job_err", title=job.get("title"), error=e)
        return
    if not isinstance(result, dict):
        return
    job["verified"] = True  # балл подтверждён глубоким разбором (не только триаж)
    if isinstance(result.get("match"), (int, float)):
        job["score"] = max(0, min(100, int(result["match"])))
    # двуязычный вывод («it-en») примерно вдвое длиннее — иначе текст обрывается на полуслове
    k = 2 if "-" in cfg.get("ui", {}).get("output_lang", "ru") else 1
    if result.get("reason"):
        job["reason"] = str(result["reason"])[:1000 * k]
    job["advice"] = json.dumps(
        {
            "cv_changes": result.get("cv_changes", []),
            "linkedin_changes": result.get("linkedin_changes", []),
            "cover_hint": result.get("cover_hint", ""),
            "salary_estimate": str(result.get("salary_estimate", ""))[:500 * k],
            "company_insights": [str(x)[:300 * k] for x in (result.get("company_insights") or [])][:8],
            "sources": [str(x)[:300] for x in (result.get("sources") or [])][:8],
        },
        ensure_ascii=False,
    )


TAILOR_CV_PROMPT = """Ты — эксперт по составлению резюме. Составь адаптированное под конкретную
вакансию резюме кандидата: переставь и переформулируй так, чтобы максимально совпадать с
требованиями вакансии, вынеси релевантный опыт и навыки вперёд, примени рекомендации ниже.

СТРОГО: НЕ выдумывай опыт, компании, навыки или достижения, которых нет в оригинале —
только переупаковка и расстановка акцентов реального опыта. Резюме пиши на английском языке.

Оригинальное CV кандидата:
{cv}

Целевая вакансия: {title} — {company}
Описание вакансии:
{description}

Рекомендации по адаптации (учитывай их):
{recs}

Верни ТОЛЬКО JSON-объект:
{{
  "name": "<имя>",
  "title": "<целевая должность под эту вакансию>",
  "contact": "<email · phone · location · ссылки — одной строкой>",
  "summary": "<3-4 предложения, заточенные под вакансию>",
  "skills": ["<навык или группа навыков>", ...],
  "experience": [
    {{"role": "...", "company": "...", "period": "...", "location": "...",
      "bullets": ["<достижение/обязанность, релевантные вакансии>", ...]}}
  ],
  "education": [{{"degree": "...", "place": "...", "period": "..."}}],
  "extra": [{{"label": "Languages", "value": "..."}}, {{"label": "Links", "value": "..."}}]
}}"""


def generate_cv(job: dict, cfg: dict, cv: str) -> dict:
    """Генерирует адаптированное под вакансию CV (структурированный dict) по CV + рекомендациям."""
    recs = ""
    try:
        adv = json.loads(job.get("advice") or "{}")
        recs = "\n".join(f"- {c}" for c in (adv.get("cv_changes") or [])) or "(нет)"
    except (json.JSONDecodeError, TypeError):
        recs = "(нет)"
    description = job.get("description") or ""
    if len(description) < 300 and job.get("url"):
        from .collectors import crawler
        fetched = crawler.fetch_job_text(job["url"])
        if len(fetched) > len(description):
            description = fetched
    data = llm.ask_json(
        TAILOR_CV_PROMPT.format(
            cv=cv[:8000] or "(CV не загружено)",
            title=job.get("title", ""), company=job.get("company", ""),
            description=description[:5000] or "(описания нет)",
            recs=recs,
        ),
        model=cfg["llm"].get("deep_model", ""),
        claude_bin=cfg["llm"].get("claude_bin", "claude"),
        provider=cfg["llm"].get("provider", "claude_cli"),
        timeout=600,
    )
    return data if isinstance(data, dict) else {}
