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

# Изучение компании идёт отдельным запросом, и в нём НЕТ ни CV, ни профиля.
#
# Причина простая. Описание вакансии пишет посторонний человек — разместить
# объявление на агрегаторе может кто угодно, и его текст попадает в запрос как
# есть, до шести тысяч знаков. Раньше в том же запросе лежали резюме, зарплатные
# ожидания и виза, а модели были разрешены WebSearch и WebFetch — и разрешены
# без единого вопроса, потому что запуск идёт без человека. Указание, спрятанное
# в объявлении, могло увести всё это в адрес чужого сайта, и человек увидел бы
# обычную карточку вакансии.
#
# Теперь в сеть ходит запрос, где кроме названия компании и должности красть
# нечего, а всё личное разбирается вторым запросом, которому инструменты не даны
# вовсе. Разделение стоит одного лишнего вызова модели.
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

# Выдача поиска, добытая приложением: модель её только пересказывает, а сама в
# сеть не ходит — инструментов ей при этом не выдаётся вовсе.
FOUND_ONLINE_BLOCK = """

Вот что нашлось в интернете. Опирайся ТОЛЬКО на это, ничего не добавляй от себя:
{found}
"""

# Что нашлось про компанию, передаётся во второй запрос уже готовым текстом.
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

    # 1. В сеть — без единой личной строчки. Красть отсюда нечего: название
    #    компании и должность злоумышленник и так знает, он их сам и написал.
    found = {}
    if research:
        # Модель ищет сама только у Claude Code. У остальных ищет приложение и
        # отдаёт выдачу текстом — тогда модель её просто пересказывает, а
        # сетевых инструментов не получает вовсе.
        сама_ищет = providers.supports_web_search(cfg["llm"].get("provider", "claude_cli"))
        prompt = _lang_banner(cfg) + RESEARCH_PROMPT.format(
            company=job.get("company", ""), title=job.get("title", ""),
            location=job.get("location", ""), lang=lang)
        выдача = ""
        if not сама_ищет:
            запрос = f"{job.get('company', '')} salary glassdoor kununu levels.fyi"
            try:
                выдача = websearch.as_text(websearch.search(cfg, запрос, n=8))
            except websearch.SearchError:
                выдача = ""
            if выдача:
                prompt += FOUND_ONLINE_BLOCK.format(found=выдача)
        # Ничего не нашлось — спрашивать не о чем: без выдачи модель может только
        # сочинить, а разбор под кандидата ниже имеет смысл и без этих сведений.
        if сама_ищет or выдача:
            try:
                found = llm.ask_json(
                    prompt,
                    timeout=900,
                    allowed_tools=["WebSearch", "WebFetch"] if сама_ищет else None, **ask)
            except llm.AuthError:
                raise
            except llm.ClaudeError as e:
                # без сведений о компании разбор всё равно имеет смысл — идём дальше
                _lk(log, "log_deep_job_err", title=job.get("title"), error=e)
        if not isinstance(found, dict):
            found = {}

    # 2. С CV и профилем — но без инструментов, так что уводить их некуда.
    английское = _in_english(f"{job.get('title', '')} {description[:1500]}")
    prompt = _lang_banner(cfg) + DEEP_PROMPT.format(
        profile=_profile_block(cfg),
        cv=cv[:6000] or "(CV не загружено)",
        title=job.get("title", ""), company=job.get("company", ""),
        location=job.get("location", ""), url=job.get("url", ""),
        # Чья это профессия — говорим прямо. Первая редакция писала просто
        # «Профессия по справочнику: CNC machine operator», и слабая модель
        # читала это как профессию КАНДИДАТА: оператор станка получал «своя» и
        # девяносто процентов, тогда как без этой строки — «смежная» и
        # пятьдесят пять. Померено на прогоне конструктора белья: подсказка
        # делала модель не умнее, а увереннее в неправоте.
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
    # то, что нашла первая часть, кладём рядом с оценкой второй
    for поле in ("salary_estimate", "company_insights", "sources"):
        if found.get(поле):
            result.setdefault(поле, found[поле])
    # «Проверено» — только если разбор и правда назвал оценку.
    #
    # Пометка ставилась просто по факту ответа, а оценка бралась, лишь когда в
    # ответе было поле match. Слабые модели сплошь и рядом отвечают связным
    # текстом без него — и тогда на карточке стоял балл от быстрого триажа с
    # галочкой «проверено» рядом. Триаж видит семьсот знаков описания и восемь
    # вакансий за раз, он для того и быстрый; галочка говорила, что этот балл
    # подтверждён вторым, внимательным проходом, а он его не подтверждал.
    if isinstance(result.get("match"), (int, float)):
        балл = max(0, min(100, int(result["match"])))
        # Вердикт и число у модели расходятся, и тогда верим вердикту: он идёт
        # первым, то есть придуман раньше, чем она успела подогнать цифру. Без
        # этого «чужая» уживалась с 85% и галочкой «проверено».
        потолок = CEILING_BY_VERDICT.get(str(result.get("verdict", "")).strip().lower())
        if потолок is not None and балл > потолок:
            _lk(log, "log_deep_verdict_caps", title=job.get("title"),
                verdict=result.get("verdict"), was=балл, now=потолок)
            балл = потолок
        job["score"] = балл
        # «Проверено» — только если модель и правда могла прочитать объявление.
        #
        # Не поняв текста, она не говорит «не поняла»: пересказывает профиль
        # кандидата и ставит балл из примера. Конструктору белья столяр, маляр,
        # садовник и кладовщик достались по девяносто процентов — все с галочкой
        # «проверено», все на польском. Галочка означает «второй, внимательный
        # проход подтвердил»; тут подтверждать было нечем.
        #
        # Название профессии по справочнику снимает вопрос: оно приходит
        # по-английски, и по нему судить можно, даже когда само объявление
        # непрочитано.
        понятно = bool(job.get("occupation")) or английское
        if not понятно:
            job["verified"] = False
            if балл > UNREAD_CEILING:
                _lk(log, "log_deep_unreadable", title=job.get("title"),
                    was=балл, now=UNREAD_CEILING)
                job["score"] = UNREAD_CEILING
        else:
            # И ещё одно условие: довод должен опираться на резюме, а не на
            # выдумку. Модель написала конструктору белья «кандидат имеет опыт и
            # образование в области электротехники» — и это была вакансия
            # лектора по электротехнике на 90% с галочкой. Никакой электротехники
            # у человека нет. Теперь она обязана привести кусок резюме, а мы
            # проверяем, что он там и правда есть.
            цитата = str(result.get("quote", "")).strip()
            if цитата and not _quote_is_real(цитата, cv):
                _lk(log, "log_quote_not_found", title=job.get("title"), quote=цитата[:70])
                job["verified"] = False
            else:
                job["verified"] = True
    # bilingual output ("it-en") is about twice as long — without this the text
    # breaks off mid-sentence
    k = 2 if "-" in cfg.get("ui", {}).get("output_lang", "ru") else 1
    if result.get("reason"):
        job["reason"] = str(result["reason"])[:1000 * k]
    # Второй проход: советы проверяются отдельным вопросом, без вакансии — чтобы
    # не было соблазна подогнать ответ под её требования. Запрет выдумывать
    # стоит и в самом промпте разбора, но слабая модель его перебивает.
    #
    # Оба списка одним вопросом: на местной модели каждый вызов стоит минуту, а
    # вакансий на разбор бывает по тридцать. Два вопроса вместо одного добавляли
    # к прогону около часа и ничего не уточняли — резюме-то одно и то же.
    из_cv = [str(с).strip() for с in (result.get("cv_changes") or []) if str(с).strip()]
    из_linkedin = [str(с).strip() for с in (result.get("linkedin_changes") or []) if str(с).strip()]
    честные = set(keep_honest_advice(из_cv + из_linkedin, cv, ask, log))
    правки = [с for с in из_cv if с in честные]
    в_профиль = [с for с in из_linkedin if с in честные]
    job["advice"] = json.dumps(
        {
            "cv_changes": правки,
            "linkedin_changes": в_профиль,
            "cover_hint": result.get("cover_hint", ""),
            "salary_estimate": str(result.get("salary_estimate", ""))[:500 * k],
            "company_insights": [str(x)[:300 * k] for x in (result.get("company_insights") or [])][:8],
            # Ссылки от модели уходят прямо в href на странице результатов.
            # Экранирование там есть, но оно бережёт от разрыва атрибута, а не от
            # схемы: «javascript:...» — совершенно законное значение href, и один
            # щелчок по номеру источника выполнил бы чужой сценарий на нашей же
            # странице. Соседний разбор вакансий схему проверяет; здесь не проверял.
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
