"""Scoring: a cheap word-level sift → model triage in batches → deep analysis of the top."""
import json
import re

from . import config, i18n, llm, providers, websearch



def _lk(log, key: str, **fmt) -> None:
    """A log line in the interface language (log comes in from the pipeline).

    An exception among the substitutions is text for a person too. Our own errors
    carry a translation key rather than a sentence, and without unwrapping it the
    key's own name went into the log: a person whose Ollama had gone read
    "prov_err_ollama_unreachable" and was none the wiser.
    """
    lang = config.load()["ui"]["lang"]
    fmt = {k: (i18n.err(lang, v) if isinstance(v, BaseException) else v)
           for k, v in fmt.items()}
    text = i18n.t(lang, key)
    log(text.format(**fmt) if fmt else text)


STOPWORDS = {
    "and", "the", "for", "with", "you", "are", "our", "have", "will", "that", "this",
    "your", "who", "not", "all", "can", "als", "und", "der", "die", "das", "для",
    "как", "что", "или", "the", "job", "work", "team", "experience", "years",
}


def profile_terms(cfg: dict, cv: str) -> set:
    # when skills take priority we repeat them, to raise their weight in the sift
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
    """A language banner for the START of the prompt. The prompts are written in
    Russian, so when the results are wanted in another language the model often
    ignores an instruction buried mid-text — an explicit demand on the first line
    works reliably."""
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

Ниже вакансии. Для каждой сначала реши, та ли это профессия, потом поставь балл.
Учитывай роль, стек, уровень, локацию.

verdict — про профессию, а не про отдельные слова:
  «своя» — та же роль и тот же стек;
  «смежная» — соседняя область, часть опыта переносится;
  «чужая» — другая профессия, даже если отдельные технологии в описании совпали.

Балл ставь по вердикту: чужая — не выше 20, смежная — 30–60, своя — 70–100.
ЖЁСТКИЕ ОГРАНИЧЕНИЯ (сильно снижай балл, если нарушены):
- право на работу: если вакансия только для конкретной страны/региона, где у кандидата
  нет права на работу (напр. «US only», «must be authorized to work in the US»), а визу/
  спонсорство кандидат не запрашивает — балл не выше 30;
- язык: если требуется рабочий язык, которым кандидат не владеет на нужном уровне
  (смотри «Языки» в профиле), — балл не выше 40.
ВСЕГДА возвращай ТОЛЬКО JSON-массив, без вопросов и пояснений — даже если данных
о кандидате мало, оценивай по тому, что есть (в первую очередь по CV):
[{{"i": <номер>, "verdict": "<своя|смежная|чужая>", "reason": "<1 короткая фраза {lang}>", "match": <0-100>, "agency": true/false}}]

{examples}
Разряд решает профессия целиком, а не отдельные совпавшие слова.

Все текстовые поля — {lang}.

ВАКАНСИИ:
{jobs}"""

# Trades for the "someone else's" example. The one chosen shares no words with
# the candidate's roles, so the example does not end up being about them.
# The strings stay Russian: they go into the prompt, and the prompt is Russian.
_OTHER_TRADES = [
    ("Frontend-разработчик (React)", "вёрстка и браузер"),
    ("Бухгалтер", "отчётность и проводки"),
    ("Медицинская сестра", "уход за больными"),
    ("Повар", "кухня и заготовки"),
]


def _examples_block(cfg: dict) -> str:
    """Three worked examples of reasoning — about the candidate's own trade.

    Generic examples do not work, and that is measured: across fifteen real jobs,
    examples about an accountant gave exactly as much as no examples at all — six
    right out of fifteen. Examples about the candidate's own trade gave ten out of
    fifteen, and, more importantly, not one job from another trade rose above 50,
    meaning none reached the threshold of 70. The example has to be about what the
    person actually does, or the model will not carry it across.

    The roles come from the profile. With no roles there are no examples: an
    invented role would pull the score further astray than no example at all.
    """
    roles = [r.strip() for r in (cfg["profile"].get("roles") or "").split(",") if r.strip()]
    if not roles:
        return ""
    own = roles[0]
    lowered = " ".join(roles).lower()
    other, other_about = next(((t, a) for t, a in _OTHER_TRADES
                               if not set(re.findall(r"\w{4,}", t.lower()))
                               & set(re.findall(r"\w{4,}", lowered))),
                              _OTHER_TRADES[-1])
    # Every role, not the first. The example used to be built from one, and "own
    # trade" ended up tied to its exact wording: the lingerie designer got the job
    # "H&M Kids Fashion Designer" scored 10, with the reasoning "the profession
    # does not match: Fashion Designer vs Lingerie Technical Designer". One and
    # the same job is called different things at different companies and in
    # different countries, and that has to be said outright — otherwise the model
    # compares words rather than trades.
    called = "», «".join(roles[:4])
    return (
        f"ПРИМЕРЫ рассуждения для этого кандидата:\n"
        f"- «{called}» → своя: та же профессия. match 90.\n"
        f"- «{other}» → чужая: другая профессия ({other_about}). Даже если в описании "
        f"попались знакомые кандидату слова, своей она от этого не становится. match 10.\n"
        f"- роль из соседней области, где часть опыта кандидата переносится, "
        f"→ смежная. match 55.\n"
        f"«Своя» — это то же ремесло, а не то же название: одна и та же работа "
        f"в разных компаниях и странах зовётся по-разному. Другое название той "
        f"же работы — своя.\n\n"
    )


# The order of the fields here is not formatting but the order of the reasoning.
#
# The model writes its answer left to right, and with schema-constrained output it
# cannot do otherwise. When match came first, the number was named before the
# model had time to think — and reason was then fitted to what had already been
# said. Hence explanations like "all the key skills are present" for a Ruby on
# Rails job: the number was already there, and all that was left was to justify
# it. Now the verdict and the reasoning come first, and the number after.
TRIAGE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["своя", "смежная", "чужая"]},
            "reason": {"type": "string"},
            "match": {"type": "integer", "minimum": 0, "maximum": 100},
            "agency": {"type": "boolean"},
        },
        "required": ["i", "verdict", "reason", "match", "agency"],
    },
}


def batch_for(provider: str) -> int:
    """How many jobs to hand the model at once.

    Eight — which is what everyone used to get — is beyond a small local model,
    and that is measured rather than assumed. On eight jobs with a known-good
    answer, mistral:7b mixed up the numbers: a score meant for one landed on
    another — which is how the front-end job got 85 from the SAP integrator's
    profile. On three: eight right out of eight.

    Three rather than one: one at a time came out twice as slow and no more
    accurate. A model shown neighbouring jobs sees that they differ — left with
    one, it has nothing to compare against.

    A batch does not trouble cloud models, and splitting it for them means paying
    three times as many requests for the same jobs. So we split only where it is
    needed.
    """
    return 3 if provider == "ollama" else 8


def triage(jobs: list, cfg: dict, log, cv: str = "", on_batch=None) -> list:
    """Fills in job['score'], job['is_agency'], job['reason'] — in parallel batches.

    on_batch(batch) is called as soon as each batch is scored: the pipeline puts
    it into the database, and jobs appear on the results page during the run
    rather than all at once at the end.

    Returns the errors that batches failed with. One batch going wrong is not a
    reason to stop — but the caller has to be able to tell "nothing suitable was
    found" from "nothing was ever looked at", and for that it needs to know
    whether anybody answered at all.
    """
    model = cfg["llm"].get("triage_model", "haiku")
    claude_bin = cfg["llm"].get("claude_bin", "claude")
    provider = cfg["llm"].get("provider", "claude_cli")
    workers = int(cfg["search"].get("parallelism", 5))
    lang = i18n.out_lang(cfg)
    profile = _profile_block(cfg)
    cv_excerpt = (cv or "").strip()[:2500] or "(CV не загружено)"
    batch_size = batch_for(provider)
    batches = [jobs[s:s + batch_size] for s in range(0, len(jobs), batch_size)]
    done = {"n": 0}

    def process(batch):
        listing = "\n\n".join(
            f"[{i}] {j.get('title', '')} — {j.get('company', '')} ({j.get('location') or 'локация не указана'})\n"
            # The occupation from the Europe-wide taxonomy, if the source named
            # it. A posting can be in any EU language, and the local model does
            # not read it; this name is in English and the same for every language.
            + (f"профессия по справочнику: {j['occupation']}\n" if j.get("occupation") else "")
            + f"{(j.get('description') or '')[:700]}"
            for i, j in enumerate(batch)
        )
        result = llm.ask_json(
            _lang_banner(cfg) + TRIAGE_PROMPT.format(
                profile=profile, cv=cv_excerpt, jobs=listing, lang=lang,
                examples=_examples_block(cfg)),
            model=model, claude_bin=claude_bin, provider=provider,
            llm=cfg["llm"], timeout=300, schema=TRIAGE_SCHEMA,
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
        if on_batch:
            on_batch(batch, done["n"], len(batches))
        _lk(log, "log_triage_batch", done=done["n"], total=len(batches))

    failures = []
    for r in llm.pmap(process, batches, workers=workers):
        if isinstance(r, Exception):
            done["n"] += 1
            failures.append(r)
            _lk(log, "log_triage_batch_err", error=r)
    return failures


PROFILE_FROM_CV_PROMPT = """Ниже CV кандидата. Заполни поля профиля для поиска работы.
Верни ТОЛЬКО JSON-объект:
{{
  "roles": "<2-4 подходящие должности через запятую, по-английски>",
  "skills": "<8-15 ключевых навыков/технологий кандидата через запятую, по-английски: языки программирования, фреймворки, инструменты, домены. НЕ включай сюда языки общения — для них есть поле languages>",
  "seniority": "<уровень: Junior/Middle/Senior/Staff/Lead>",
  "summary": "<3-4 предложения {lang}: опыт, стек, сильные стороны>",
  "languages": "<языки кандидата, если указаны, иначе пустая строка>"
}}

CV:
{cv}"""


def _as_text(value) -> str:
    """The model's answer, into a profile field that is treated as a string everywhere.

    We ask the model for "job titles separated by commas", and a weak one sends a
    list. Through str() that turned into "['Purchasing Manager', 'Supply Chain
    Manager']" — and was written into the profile exactly so. It was then split on
    commas, and the queries "['Purchasing Manager'" and "'Logistics Manager'" went
    off to the sources, while the model was handed the example "['Purchasing
    Manager'" → own trade. Measured on a supply-department head's CV: all six
    search words and every example were spoiled.
    """
    if isinstance(value, (list, tuple)):
        parts = [str(x).strip() for x in value if str(x).strip()]
    else:
        parts = [str(value).strip()]
    # Brackets and quotes could arrive inside the string too — the model sometimes
    # writes a list as text. We strip them from the edges, not from everywhere: in
    # "C++ (advanced)" the brackets belong there.
    cleaned = [p.strip(" \t\"'[]") for p in parts]
    return ", ".join(p for p in cleaned if p)


def profile_from_cv(cfg: dict, cv: str) -> dict:
    """Fills the empty profile fields from the CV. Returns the updated cfg."""
    data = llm.ask_json(
        PROFILE_FROM_CV_PROMPT.format(cv=cv[:6000], lang=i18n.out_lang(cfg)),
        model=cfg["llm"].get("triage_model", "haiku"),
        claude_bin=cfg["llm"].get("claude_bin", "claude"),
        provider=cfg["llm"].get("provider", "claude_cli"),
        llm=cfg["llm"],
        timeout=300,
    )
    if isinstance(data, dict):
        for key in ("roles", "skills", "seniority", "summary", "languages"):
            if data.get(key) and not cfg["profile"].get(key):
                cfg["profile"][key] = _as_text(data[key])
    return cfg


# The order of the fields here is not formatting but the whole point.
#
# The deep analysis used to put the score FIRST, before any reasoning at all. The
# model answers left to right and invents the number before it has time to think;
# reason is then fitted to the figure already named. In triage this was fixed long
# ago — verdict first, then reason, and only then match — but the fix never
# reached the deep analysis, and it quietly spoiled what triage had already got
# right.
#
# Measured on a real run: Regina Mokhova, a lingerie designer. Triage gave fashion
# jobs 90 and other people's software 55. After the deep analysis fashion came
# down to 80, while "Staff Software Engineer" rose to 85 — and got a "verified"
# tick. Of the thirty-five jobs above the threshold, four were in her profession;
# she looked through all twenty-six pages and wrote: "the rest are completely off,
# it is all IT-related there".
#
# The anchor numbers and the examples are the same as in triage: without them a
# weak model scores by eye, and by eye it comes out generous.
DEEP_PROMPT = """Ты — карьерный консультант. Профиль кандидата:

{profile}

CV кандидата:
{cv}

Вакансия: {title} — {company} ({location})
{occupation}Ссылка: {url}
Описание:
{description}

Сначала реши, чья это профессия, и лишь потом ставь балл:
- «своя» — та же профессия, что у кандидата. match 80-95.
- «смежная» — соседняя область, часть опыта переносится. match 40-60.
- «чужая» — другая профессия. match 0-20, даже если в описании попались
  знакомые кандидату слова и даже если у него есть диплом в этой области.
  Образование — не профессия: человек работает тем, кем работает.

{examples}СТРОГО: правки — только перестановка и переформулировка того, что в CV уже есть.
НЕ предлагай упоминать навыки, опыт или инструменты, которых у кандидата нет:
это совет соврать, и на собеседовании он обернётся против него. Если ключевого
для вакансии опыта нет — так и скажи в reason, а не выдавай его отсутствие за
то, что нужно «подчеркнуть».

ВСЕ текстовые поля пиши {lang}.
Верни ТОЛЬКО JSON-объект, поля строго в этом порядке:
{{
  "verdict": "<своя|смежная|чужая>",
  "reason": "<2-3 предложения: почему подходит и что может помешать>",
  "quote": "<дословный кусок из CV кандидата, 3-10 слов, подтверждающий reason. Скопируй буква в букву, не пересказывай. Нечего процитировать — пустая строка>",
  "match": <0-100, в пределах, назначенных вердикту выше>,
  "cv_changes": ["<конкретная правка CV под эту вакансию>", ...],
  "linkedin_changes": ["<конкретная правка профиля LinkedIn>", ...],
  "cover_hint": "<1-2 предложения: на что сделать упор в отклике>"
}}"""

# The deep analysis had no schema at all — unlike triage. Without one a weak model
# answers with fluent prose and none of the required fields, and the order we set
# out in words is held by nothing. With a schema the fields are always there and
# come in the order written.
DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["своя", "смежная", "чужая"]},
        "reason": {"type": "string"},
        # A quotation from the CV — against invention. The model told the lingerie
        # designer that "the candidate has experience and education in electrical
        # engineering", and that was a lectureship in electrical engineering at
        # 90% with a "verified" tick. The person has no electrical engineering
        # whatsoever. So: make it name the place in the CV the claim came from —
        # and check that the place is really there.
        "quote": {"type": "string"},
        "match": {"type": "integer", "minimum": 0, "maximum": 100},
        "cv_changes": {"type": "array", "items": {"type": "string"}},
        "linkedin_changes": {"type": "array", "items": {"type": "string"}},
        "cover_hint": {"type": "string"},
    },
    "required": ["verdict", "reason", "quote", "match", "cv_changes",
                 "linkedin_changes", "cover_hint"],
}


def _quote_is_real(quote: str, cv: str) -> bool:
    """Whether this fragment is really in the CV.

    We compare the fraction of words rather than the whole string. I tried
    demanding an exact match, and it came out badly: for Viktor Belonogov the
    quotation «специалист с 10-летним опыром работы» was not found because of a
    one-letter typo — the CV says «опытом». For Viktor Lavrov a hundred and two
    quotations out of a hundred and twenty-five were rejected this way, almost all
    of them for paraphrasing instead of copying. A small model cannot copy letter
    for letter, and punishing it for that means taking the tick away from honest
    reasoning.

    Eight tenths of the words is the line where a typo or a reordering still gets
    through and an invention does not: "experience in electrical engineering", in
    a CV that has none, will not reach even half.
    """
    words = [w for w in re.findall(r"\w+", (quote or "").lower()) if len(w) > 2]
    if len(words) < 3:
        return False
    in_cv = set(re.findall(r"\w+", (cv or "").lower()))
    found = sum(1 for w in words if w in in_cv)
    return found / len(words) >= 0.8

# English function words — and only those that do not occur in the neighbouring
# languages. Short ones like "of", "in", "is", "a" are deliberately absent: every
# one of them is Dutch as well, and a posting from Rotterdam passed for English.
_ENGLISH_WORDS = {
    "the", "and", "with", "you", "your", "our", "we", "will", "are", "have",
    "this", "that", "from", "they", "their", "what", "which", "who", "been",
    "were", "would", "should", "about", "into", "more", "than", "also",
    "work", "team", "experience", "skills", "role", "job", "position",
    "candidate", "required", "must", "join", "looking",
}


# A second pass: check the suggestions against the CV with a separate question.
#
# The ban on inventing is in the deep-analysis prompt itself — "edits are only a
# reordering of what is already in the CV" — and a weak model talks over it. It
# advised the supply-department head to "add experience with Salesforce Commerce
# Cloud to the Professional Experience section", and the lingerie designer to
# "emphasize the candidate's knowledge of materials such as leather", though
# neither is anywhere in the CV.
#
# We ask separately and without the job: only the CV and the list of suggestions.
# That way the model has no temptation to fit its answer to the job's
# requirements — there is exactly one question, is this in the CV.
ADVICE_CHECK_PROMPT = """Ниже резюме кандидата и советы по его правке.

Резюме:
{cv}

Советы:
{advice}

Для каждого совета ответь, опирается ли он ТОЛЬКО на то, что в резюме уже есть.
Совет переставить, выделить или переформулировать имеющееся — опирается.
Совет добавить опыт, навык или инструмент, которых в резюме нет, — не опирается,
это предложение соврать.

Верни ТОЛЬКО JSON: {{"ok": [<номера советов, которые опираются на резюме>]}}"""

ADVICE_CHECK_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "array", "items": {"type": "integer"}}},
    "required": ["ok"],
}


def keep_honest_advice(advice: list, cv: str, ask: dict, log) -> list:
    """Keeps only the suggestions that rest on the CV."""
    advice = [str(a).strip() for a in (advice or []) if str(a).strip()]
    if not advice or not (cv or "").strip():
        return advice
    listing = "\n".join(f"{i}. {a}" for i, a in enumerate(advice))
    try:
        answer = llm.ask_json(ADVICE_CHECK_PROMPT.format(cv=cv[:6000], advice=listing),
                              timeout=300, schema=ADVICE_CHECK_SCHEMA, **ask)
    except (llm.ClaudeError, llm.AuthError):
        return advice     # could not check — we do not throw away someone's work
    if not isinstance(answer, dict) or not isinstance(answer.get("ok"), list):
        return advice
    good = {i for i in answer["ok"] if isinstance(i, int)}
    kept = [a for i, a in enumerate(advice) if i in good]
    if len(kept) < len(advice):
        for i, a in enumerate(advice):
            if i not in good:
                _lk(log, "log_advice_dropped", advice=a[:90])
    # It rejected everything — more likely it did not understand the question than
    # that every suggestion is bad.
    return kept or advice


def _occupation_block(cfg: dict, job: dict) -> str:
    """The job's occupation from the taxonomy — and the candidate's beside it.

    Apart they do not work. A single name given without its pair is taken by a
    weak model for the candidate's own occupation, and it declares the job theirs.
    Named side by side, the two turn into a question it can answer: is this one
    trade or two.
    """
    occupation = (job.get("occupation") or "").strip()
    if not occupation:
        return ""
    own = (cfg["profile"].get("roles") or "").split(",")[0].strip()
    lines = [f"Профессия ЭТОЙ ВАКАНСИИ по общеевропейскому справочнику: «{occupation}»."]
    if own:
        lines.append(f"Профессия кандидата: «{own}».")
        lines.append("Если это разные ремёсла — вердикт «чужая», "
                     "как бы ни было написано само объявление.")
    return "\n".join(lines) + "\n"


def _in_english(text: str) -> bool:
    """Whether the posting is written in English — that is, in a language the
    local model really does read."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", (text or "").lower())
    # We count distinct words, not all of them. A posting is sometimes not prose
    # at all but a list: "Ruby on Rails, RSpec, PostgreSQL" — English, yet without
    # a single function word in it, so the language cannot be told from them. And
    # if it cannot be told, we do not quibble: silently lowering a score for
    # something we never established is worse than not lowering it.
    distinct = set(words)
    if len(distinct) < 12:
        return True
    ours = sum(1 for w in words if w in _ENGLISH_WORDS)
    return ours / len(words) >= 0.08


# A ceiling on the score by verdict. The model names both the verdict and the
# number, and the two disagree with each other: on Regina's run it wrote
# "responsible for developing technical solutions for games, not clothing" — and
# gave it 55. The reasoning is right, the number is not. We believe the reasoning:
# it comes first and costs the model more.
CEILING_BY_VERDICT = {"чужая": 20, "смежная": 60}

# A ceiling for a posting the model did not read and for which the taxonomy named
# no occupation. Not zero: we do not know what is in there — it may well be their
# work. But neither has it earned the right to stand above what the model
# understood with its own eyes. Fifty-five is below the default threshold, but
# visible if the threshold is lowered.
UNREAD_CEILING = 55

# Researching the company is a separate request, and it contains NEITHER the CV
# nor the profile.
#
# The reason is simple. A job description is written by an outsider — anyone at
# all can put a posting on an aggregator, and its text goes into the prompt as it
# is, up to six thousand characters. The same prompt used to carry the CV, the
# salary expectations and the visa situation, and the model was allowed WebSearch
# and WebFetch — allowed without a single question, because the run happens with
# nobody watching. An instruction hidden in the posting could have carried all of
# that off to somebody else's address, and the person would have seen an ordinary
# job card.
#
# Now what goes to the network is a request with nothing to steal beyond the
# company's name and the job title, while everything personal is worked through by
# a second request that is given no tools at all. The separation costs one extra
# call to the model.
RESEARCH_PROMPT = """Найди в интернете сведения о компании и вилке зарплат.

Компания: {company}
Должность: {title}
Локация: {location}

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

Верни ТОЛЬКО JSON-объект:
{{
  "salary_estimate": "<вилка с валютой и явной пометкой company-specific/рыночная оценка, или 'не найдено'>",
  "company_insights": ["<факт о компании с опорой на найденное: размер, стадия, финансирование, рейтинг, культура>", ...],
  "sources": ["<URL, на которые опирался>", ...]
}}"""

# Search results fetched by the application: the model only retells them and does
# not go to the network itself — it is handed no tools whatsoever for this.
FOUND_ONLINE_BLOCK = """

Вот что нашлось в интернете. Опирайся ТОЛЬКО на это, ничего не добавляй от себя:
{found}
"""

# What was found about the company is passed to the second request as ready text.
FOUND_BLOCK = """
Уже известно о компании (найдено отдельно, можешь опираться):
{facts}
"""


def deep_analyze(job: dict, cfg: dict, cv: str, log, research: bool = True) -> None:
    """Refines the score and adds advice for one job (writing into job).
    With research=True it also looks up the salary and facts about the company
    online (Glassdoor/Kununu/levels.fyi and the like) — slower, but it gives what
    the posting itself does not."""
    description = job.get("description") or ""
    if len(description) < 300 and job.get("url"):
        from .collectors import crawler
        fetched = crawler.fetch_job_text(job["url"])
        if len(fetched) > len(description):
            description = fetched
    lang = i18n.out_lang(cfg)
    ask = dict(model=cfg["llm"].get("deep_model", ""),
               claude_bin=cfg["llm"].get("claude_bin", "claude"),
               provider=cfg["llm"].get("provider", "claude_cli"),
               llm=cfg["llm"])

    # 1. To the network — without a single personal line. There is nothing here to
    #    steal: an attacker already knows the company name and the job title,
    #    having written them themselves.
    found = {}
    if research:
        # Only Claude Code searches by itself. For everyone else the application
        # searches and hands over the results as text — then the model merely
        # retells them, and gets no network tools at all.
        searches_itself = providers.supports_web_search(cfg["llm"].get("provider", "claude_cli"))
        prompt = _lang_banner(cfg) + RESEARCH_PROMPT.format(
            company=job.get("company", ""), title=job.get("title", ""),
            location=job.get("location", ""), lang=lang)
        results = ""
        if not searches_itself:
            query = f"{job.get('company', '')} salary glassdoor kununu levels.fyi"
            try:
                results = websearch.as_text(websearch.search(cfg, query, n=8))
            except websearch.SearchError:
                results = ""
            if results:
                prompt += FOUND_ONLINE_BLOCK.format(found=results)
        # Nothing found — nothing to ask about: with no results the model can only
        # make things up, and the candidate-specific analysis below is worth doing
        # without these facts anyway.
        if searches_itself or results:
            try:
                found = llm.ask_json(
                    prompt,
                    timeout=900,
                    allowed_tools=["WebSearch", "WebFetch"] if searches_itself else None, **ask)
            except llm.AuthError:
                raise
            except llm.ClaudeError as e:
                # without facts about the company the analysis still makes sense —
                # so we carry on
                _lk(log, "log_deep_job_err", title=job.get("title"), error=e)
        if not isinstance(found, dict):
            found = {}

    # 2. With the CV and the profile — but with no tools, so there is nowhere to
    #    carry them off to.
    in_english = _in_english(f"{job.get('title', '')} {description[:1500]}")
    prompt = _lang_banner(cfg) + DEEP_PROMPT.format(
        profile=_profile_block(cfg),
        cv=cv[:6000] or "(CV не загружено)",
        title=job.get("title", ""), company=job.get("company", ""),
        location=job.get("location", ""), url=job.get("url", ""),
        # Whose occupation this is, said outright. The first version simply wrote
        # "occupation from the taxonomy: CNC machine operator", and a weak model
        # read that as the CANDIDATE'S occupation: the machine operator job came
        # out "own trade" at ninety percent, whereas without that line it was
        # "adjacent" at fifty-five. Measured on the lingerie designer's run: the
        # hint made the model not cleverer but more confident in being wrong.
        occupation=_occupation_block(cfg, job),
        description=description[:6000] or "(описания нет)",
        lang=lang, examples=_examples_block(cfg),
    )
    facts = "\n".join(str(x) for x in (found.get("company_insights") or []))
    if found.get("salary_estimate"):
        facts = f"{found['salary_estimate']}\n{facts}".strip()
    if facts:
        prompt += FOUND_BLOCK.format(facts=facts[:2000])
    try:
        result = llm.ask_json(prompt, timeout=600, allowed_tools=None,
                              schema=DEEP_SCHEMA, **ask)
    except llm.AuthError:
        raise      # without a signed-in model the run makes no sense
    except llm.ClaudeError as e:
        _lk(log, "log_deep_job_err", title=job.get("title"), error=e)
        return
    if not isinstance(result, dict):
        return
    # what the first part found goes beside the second part's score
    for field in ("salary_estimate", "company_insights", "sources"):
        if found.get(field):
            result.setdefault(field, found[field])
    # "Verified" — only if the deep analysis really did name a score.
    #
    # The mark used to be set on the mere fact of an answer, while the score was
    # taken only when the answer carried a match field. Weak models answer with
    # fluent prose and no such field over and over — and then the card showed the
    # score from the fast triage with a "verified" tick beside it. Triage sees
    # seven hundred characters of description and eight jobs at a time, that is
    # what makes it fast; the tick said this score had been confirmed by a second,
    # careful pass, and it had not.
    if isinstance(result.get("match"), (int, float)):
        score = max(0, min(100, int(result["match"])))
        # The model's verdict and its number disagree, and then we believe the
        # verdict: it comes first, meaning it was thought of before the model had
        # a chance to fit the figure. Without this, "someone else's trade" lived
        # happily alongside 85% and a "verified" tick.
        ceiling = CEILING_BY_VERDICT.get(str(result.get("verdict", "")).strip().lower())
        if ceiling is not None and score > ceiling:
            _lk(log, "log_deep_verdict_caps", title=job.get("title"),
                verdict=result.get("verdict"), was=score, now=ceiling)
            score = ceiling
        job["score"] = score
        # "Verified" — only if the model could really read the posting.
        #
        # Having failed to understand the text, it does not say "I did not
        # understand": it retells the candidate's profile and puts down a score
        # from the example. The lingerie designer got a joiner, a painter, a
        # gardener and a storekeeper at ninety percent each — all with a
        # "verified" tick, all in Polish. The tick means "a second, careful pass
        # confirmed this"; here there was nothing to confirm with.
        #
        # The occupation name from the taxonomy settles the question: it arrives
        # in English, and it can be judged by even when the posting itself was
        # never read.
        understood = bool(job.get("occupation")) or in_english
        if not understood:
            job["verified"] = False
            if score > UNREAD_CEILING:
                _lk(log, "log_deep_unreadable", title=job.get("title"),
                    was=score, now=UNREAD_CEILING)
                job["score"] = UNREAD_CEILING
        else:
            # And one more condition: the reasoning must rest on the CV rather
            # than on invention. The model told the lingerie designer that "the
            # candidate has experience and education in electrical engineering" —
            # and that was a lectureship in electrical engineering at 90% with a
            # tick. The person has no electrical engineering whatsoever. Now it is
            # obliged to quote a piece of the CV, and we check that the piece is
            # really there.
            quote = str(result.get("quote", "")).strip()
            if quote and not _quote_is_real(quote, cv):
                _lk(log, "log_quote_not_found", title=job.get("title"), quote=quote[:70])
                job["verified"] = False
            else:
                job["verified"] = True
    # bilingual output ("it-en") is about twice as long — without this the text
    # breaks off mid-sentence
    k = 2 if "-" in cfg.get("ui", {}).get("output_lang", "ru") else 1
    if result.get("reason"):
        job["reason"] = str(result["reason"])[:1000 * k]
    # A second pass: the suggestions are checked with a separate question, without
    # the job — so there is no temptation to fit the answer to its requirements.
    # The ban on inventing is in the deep-analysis prompt too, but a weak model
    # talks over it.
    #
    # Both lists in one question: on a local model every call costs a minute, and
    # there can be thirty jobs to analyse. Two questions instead of one added about
    # an hour to a run and settled nothing further — the CV is the same either way.
    from_cv = [str(a).strip() for a in (result.get("cv_changes") or []) if str(a).strip()]
    from_linkedin = [str(a).strip() for a in (result.get("linkedin_changes") or [])
                     if str(a).strip()]
    honest = set(keep_honest_advice(from_cv + from_linkedin, cv, ask, log))
    cv_edits = [a for a in from_cv if a in honest]
    profile_edits = [a for a in from_linkedin if a in honest]
    job["advice"] = json.dumps(
        {
            "cv_changes": cv_edits,
            "linkedin_changes": profile_edits,
            "cover_hint": result.get("cover_hint", ""),
            "salary_estimate": str(result.get("salary_estimate", ""))[:500 * k],
            "company_insights": [str(x)[:300 * k] for x in (result.get("company_insights") or [])][:8],
            # Links from the model go straight into an href on the results page.
            # Escaping is done there, but it guards against breaking out of the
            # attribute, not against the scheme: "javascript:..." is a perfectly
            # legal href value, and one click on a source number would have run
            # somebody else's script on our own page. The job parsing next door
            # checks the scheme; this did not.
            "sources": [u for u in (str(x)[:300] for x in (result.get("sources") or []))
                        if u.lower().startswith(("http://", "https://"))][:8],
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
    """Builds a CV tailored to the job (as a structured dict) from the CV and the advice."""
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
        llm=cfg["llm"],
        timeout=600,
    )
    return data if isinstance(data, dict) else {}
