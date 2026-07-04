"""Скоринг: дешёвый лексический отбор → LLM-триаж пачками → глубокий разбор топа."""
import json
import re

from . import llm

STOPWORDS = {
    "and", "the", "for", "with", "you", "are", "our", "have", "will", "that", "this",
    "your", "who", "not", "all", "can", "als", "und", "der", "die", "das", "для",
    "как", "что", "или", "the", "job", "work", "team", "experience", "years",
}


def profile_terms(cfg: dict, cv: str) -> set:
    raw = " ".join([
        cfg["profile"].get("roles", ""),
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


def _profile_block(cfg: dict) -> str:
    p, s = cfg["profile"], cfg["search"]
    visa = "нужна виза/спонсорство" if p.get("visa_required") else "виза не нужна"
    if p.get("visa_note"):
        visa += f" ({p['visa_note']})"
    return (
        f"Роли: {p.get('roles') or '—'}\n"
        f"Уровень: {p.get('seniority') or '—'}\n"
        f"О кандидате: {p.get('summary') or '—'}\n"
        f"Зарплата: {p.get('salary') or '—'}\n"
        f"Формат: {p.get('work_format')}\n"
        f"Языки: {p.get('languages') or '—'}\n"
        f"Локации поиска: {s.get('locations')}\n"
        f"Право на работу: {visa}"
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
[{{"i": <номер>, "match": <0-100>, "agency": true/false, "reason": "<1 короткая фраза по-русски>"}}]

ВАКАНСИИ:
{jobs}"""


def triage(jobs: list, cfg: dict, log, cv: str = "") -> list:
    """Проставляет job['score'], job['is_agency'], job['reason'] — пачками параллельно."""
    model = cfg["llm"].get("triage_model", "haiku")
    claude_bin = cfg["llm"].get("claude_bin", "claude")
    workers = int(cfg["search"].get("parallelism", 5))
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
            TRIAGE_PROMPT.format(profile=profile, cv=cv_excerpt, jobs=listing),
            model=model, claude_bin=claude_bin, timeout=300,
        )
        for item in result if isinstance(result, list) else []:
            try:
                j = batch[int(item["i"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            j["score"] = max(0, min(100, int(item.get("match", 0))))
            j["is_agency"] = bool(item.get("agency")) or j.get("is_agency", False)
            j["reason"] = str(item.get("reason", ""))[:300]
        done["n"] += 1
        log(f"триаж: {done['n']} из {len(batches)} пачек готово")

    for r in llm.pmap(process, batches, workers=workers):
        if isinstance(r, Exception):
            done["n"] += 1
            log(f"триаж (пачка): {r}")
    return jobs


PROFILE_FROM_CV_PROMPT = """Ниже CV кандидата. Заполни поля профиля для поиска работы.
Верни ТОЛЬКО JSON-объект:
{{
  "roles": "<2-4 подходящие должности через запятую, по-английски>",
  "seniority": "<уровень: Junior/Middle/Senior/Staff/Lead>",
  "summary": "<3-4 предложения по-русски: опыт, стек, сильные стороны>",
  "languages": "<языки кандидата, если указаны, иначе пустая строка>"
}}

CV:
{cv}"""


def profile_from_cv(cfg: dict, cv: str) -> dict:
    """Заполняет пустые поля профиля из CV. Возвращает обновлённый cfg."""
    data = llm.ask_json(
        PROFILE_FROM_CV_PROMPT.format(cv=cv[:6000]),
        model=cfg["llm"].get("triage_model", "haiku"),
        claude_bin=cfg["llm"].get("claude_bin", "claude"),
        timeout=300,
    )
    if isinstance(data, dict):
        for key in ("roles", "seniority", "summary", "languages"):
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

Верни ТОЛЬКО JSON-объект:
{{
  "match": <0-100, честная оценка совпадения>,
  "reason": "<2-3 предложения по-русски: почему подходит и что может помешать>",
  "cv_changes": ["<конкретная правка CV под эту вакансию>", ...],
  "linkedin_changes": ["<конкретная правка профиля LinkedIn>", ...],
  "cover_hint": "<1-2 предложения: на что сделать упор в отклике>"
}}"""


def deep_analyze(job: dict, cfg: dict, cv: str, log) -> None:
    """Уточняет score и добавляет advice для одной вакансии (пишет в job)."""
    description = job.get("description") or ""
    if len(description) < 300 and job.get("url"):
        from .collectors import crawler
        fetched = crawler.fetch_job_text(job["url"])
        if len(fetched) > len(description):
            description = fetched
    try:
        result = llm.ask_json(
            DEEP_PROMPT.format(
                profile=_profile_block(cfg),
                cv=cv[:6000] or "(CV не загружено)",
                title=job.get("title", ""), company=job.get("company", ""),
                location=job.get("location", ""), url=job.get("url", ""),
                description=description[:6000] or "(описания нет)",
            ),
            model=cfg["llm"].get("deep_model", ""),
            claude_bin=cfg["llm"].get("claude_bin", "claude"),
            timeout=600,
        )
    except llm.ClaudeError as e:
        log(f"разбор «{job.get('title')}»: {e}")
        return
    if not isinstance(result, dict):
        return
    if isinstance(result.get("match"), (int, float)):
        job["score"] = max(0, min(100, int(result["match"])))
    if result.get("reason"):
        job["reason"] = str(result["reason"])[:1000]
    job["advice"] = json.dumps(
        {
            "cv_changes": result.get("cv_changes", []),
            "linkedin_changes": result.get("linkedin_changes", []),
            "cover_hint": result.get("cover_hint", ""),
        },
        ensure_ascii=False,
    )
