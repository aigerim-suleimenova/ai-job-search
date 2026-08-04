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

# Профессии для примера «чужая». Берётся та, что не пересекается по словам с
# ролями кандидата, — чтобы образец не оказался про него самого.
_ЧУЖИЕ_ПРОФЕССИИ = [
    ("Frontend-разработчик (React)", "вёрстка и браузер"),
    ("Бухгалтер", "отчётность и проводки"),
    ("Медицинская сестра", "уход за больными"),
    ("Повар", "кухня и заготовки"),
]


def _examples_block(cfg: dict) -> str:
    """Три примера рассуждения — про профессию самого кандидата.

    Общие примеры не работают, и это измерено: на пятнадцати настоящих вакансиях
    примеры про бухгалтера дали ровно столько же, сколько их отсутствие — шесть
    верных из пятнадцати. Примеры про профессию кандидата — десять из пятнадцати,
    и, что важнее, ни одна чужая вакансия не поднялась выше 50, то есть до порога
    в 70 не дошла ни одна. Образец должен быть про то, чем человек занимается,
    иначе модель его не переносит.

    Роли берутся из профиля. Если их нет, примеров не будет: выдуманная роль
    увела бы оценку в сторону сильнее, чем отсутствие примера.
    """
    роли = (cfg["profile"].get("roles") or "").split(",")
    своя = роли[0].strip()
    if not своя:
        return ""
    низ = своя.lower()
    чужая, чем = next(((п, ч) for п, ч in _ЧУЖИЕ_ПРОФЕССИИ
                       if not set(re.findall(r"\w{4,}", п.lower())) & set(re.findall(r"\w{4,}", низ))),
                      _ЧУЖИЕ_ПРОФЕССИИ[-1])
    return (
        f"ПРИМЕРЫ рассуждения для этого кандидата:\n"
        f"- «{своя}» → своя: та же профессия. match 90.\n"
        f"- «{чужая}» → чужая: другая профессия ({чем}). Даже если в описании "
        f"попались знакомые кандидату слова, своей она от этого не становится. match 10.\n"
        f"- роль из соседней области, где часть опыта кандидата переносится, "
        f"→ смежная. match 55.\n\n"
    )


# Порядок полей здесь — не оформление, а порядок рассуждения.
#
# Модель пишет ответ слева направо, и при выводе по схеме иначе не может. Когда
# match стоял первым, число называлось до того, как модель успевала подумать, —
# а reason потом подгонялся под уже названное. Отсюда и объяснения вида
# «ключевые навыки все присутствуют» у вакансии на Ruby on Rails: число уже
# стояло, оставалось его оправдать. Теперь сперва вердикт и довод, число — после.
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
    """Сколько вакансий отдавать модели за раз.

    Восемь — сколько отдавалось всем — малой местной модели не по силам, и это
    измерено, а не предположено. На восьми вакансиях с заранее известным ответом
    mistral:7b путал номера: балл, предназначенный одной, доставался другой — так
    фронтенд у SAP-интегратора и получал 85. На трёх — восемь верных из восьми.

    Три, а не одна: по одной вышло медленнее вдвое и не точнее. Модель, которой
    показывают соседние вакансии, видит, что они разные, — а оставшись с одной,
    сравнивать ей не с чем.

    Облачным моделям пачка не мешает, и дробить её для них — значит платить за
    те же вакансии втрое больше запросов. Поэтому дробим только там, где нужно.
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
            # Профессия по общеевропейскому справочнику, если источник её назвал.
            # Объявление бывает на любом языке ЕС, и местная модель его не читает;
            # это название — на английском и одно и то же для всех языков.
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
  "skills": "<8-15 ключевых навыков/технологий кандидата через запятую, по-английски: языки, фреймворки, инструменты, домены>",
  "seniority": "<уровень: Junior/Middle/Senior/Staff/Lead>",
  "summary": "<3-4 предложения {lang}: опыт, стек, сильные стороны>",
  "languages": "<языки кандидата, если указаны, иначе пустая строка>"
}}

CV:
{cv}"""


def _строкой(значение) -> str:
    """Ответ модели — в поле профиля, которое везде считается строкой.

    Просят у модели «должности через запятую», а слабая присылает список. Через
    str() он превращался в «['Purchasing Manager', 'Supply Chain Manager']» — и
    так и записывался в профиль. Дальше это разбиралось по запятой, и в
    источники уходили запросы «['Purchasing Manager'» и «'Logistics Manager'», а
    модель получала пример «['Purchasing Manager'» → своя. Померено на резюме
    руководителя ОМТС: испорчены были все шесть поисковых слов и все примеры.
    """
    if isinstance(значение, (list, tuple)):
        части = [str(x).strip() for x in значение if str(x).strip()]
    else:
        части = [str(значение).strip()]
    # Скобки и кавычки могли прийти и внутри строки — модель иногда пишет список
    # текстом. Убираем их с краёв, а не отовсюду: в «C++ (advanced)» скобки свои.
    очищенные = [ч.strip(" \t\"'[]") for ч in части]
    return ", ".join(ч for ч in очищенные if ч)


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
                cfg["profile"][key] = _строкой(data[key])
    return cfg


# Порядок полей здесь — не оформление, а сама суть.
#
# Разбор ставил оценку ПЕРВЫМ полем, до всякого рассуждения. Модель отвечает
# слева направо и придумывает число раньше, чем успевает подумать; дальше reason
# подгоняется под уже названную цифру. У триажа это давно исправлено — там
# сначала verdict, потом reason и только потом match, — а до разбора починка не
# дошла, и он тихо портил то, что триаж уже сделал правильно.
#
# Померено на настоящем прогоне: конструктор белья Регина Мохова. Триаж ставил
# fashion-вакансиям 90, а чужому софту 55. После разбора fashion опускалось до
# 80, а «Staff Software Engineer» поднимался до 85 — и получал галочку
# «проверено». Из тридцати пяти вакансий выше порога по её профессии было
# четыре; она посмотрела все двадцать шесть страниц и написала: «остальные все
# прям мимо, там всё с IT связанное».
#
# Опорные числа и примеры — те же, что в триаже: без них слабая модель ставит
# «на глаз», а на глаз у неё выходит щедро.
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
  "match": <0-100, в пределах, назначенных вердикту выше>,
  "cv_changes": ["<конкретная правка CV под эту вакансию>", ...],
  "linkedin_changes": ["<конкретная правка профиля LinkedIn>", ...],
  "cover_hint": "<1-2 предложения: на что сделать упор в отклике>"
}}"""

# Схемы у разбора не было вовсе — в отличие от триажа. Без неё слабая модель
# отвечает связным текстом без нужных полей, а порядок, который мы задали
# словами, ничем не удержан. Со схемой поля есть всегда и идут как написано.
DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["своя", "смежная", "чужая"]},
        "reason": {"type": "string"},
        "match": {"type": "integer", "minimum": 0, "maximum": 100},
        "cv_changes": {"type": "array", "items": {"type": "string"}},
        "linkedin_changes": {"type": "array", "items": {"type": "string"}},
        "cover_hint": {"type": "string"},
    },
    "required": ["verdict", "reason", "match", "cv_changes", "linkedin_changes",
                 "cover_hint"],
}

# Служебные слова английского — и только те, что не встречаются в соседних
# языках. Коротких вроде «of», «in», «is», «a» здесь нет намеренно: все они
# заодно и нидерландские, и объявление из Роттердама сходило за английское.
_АНГЛИЙСКИЕ_СЛОВА = {
    "the", "and", "with", "you", "your", "our", "we", "will", "are", "have",
    "this", "that", "from", "they", "their", "what", "which", "who", "been",
    "were", "would", "should", "about", "into", "more", "than", "also",
    "work", "team", "experience", "skills", "role", "job", "position",
    "candidate", "required", "must", "join", "looking",
}


def _occupation_block(cfg: dict, job: dict) -> str:
    """Профессия вакансии по справочнику — и рядом профессия кандидата.

    Порознь они не работают. Одно название, поданное без пары, слабая модель
    принимает за профессию кандидата и объявляет вакансию своей. Названные
    рядом, они превращаются в вопрос, на который у неё есть ответ: одно это
    ремесло или разные.
    """
    занятие = (job.get("occupation") or "").strip()
    if not занятие:
        return ""
    своя = (cfg["profile"].get("roles") or "").split(",")[0].strip()
    строки = [f"Профессия ЭТОЙ ВАКАНСИИ по общеевропейскому справочнику: «{занятие}»."]
    if своя:
        строки.append(f"Профессия кандидата: «{своя}».")
        строки.append("Если это разные ремёсла — вердикт «чужая», "
                      "как бы ни было написано само объявление.")
    return "\n".join(строки) + "\n"


def _по_английски(текст: str) -> bool:
    """Написано ли объявление по-английски — то есть на языке, который местная
    модель и правда читает."""
    слова = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", (текст or "").lower())
    # Считаем разные слова, а не все. Объявление бывает и не связным текстом, а
    # перечнем: «Ruby on Rails, RSpec, PostgreSQL» — по-английски, но служебных
    # слов в нём нет ни одного, и по ним язык не определить. Раз определить
    # нельзя — не придираемся: молча снижать балл за то, чего мы не выяснили,
    # хуже, чем не снижать.
    разные = set(слова)
    if len(разные) < 12:
        return True
    свои = sum(1 for w in слова if w in _АНГЛИЙСКИЕ_СЛОВА)
    return свои / len(слова) >= 0.08


# Потолок балла по вердикту. Модель называет и вердикт, и число, и они у неё
# расходятся: на прогоне Регины она писала «отвечает за разработку технических
# решений для игр, а не одежды» — и ставила 55. Рассуждение верное, число нет.
# Верим рассуждению: оно идёт первым и стоит модели дороже.
ПОТОЛОК_ПО_ВЕРДИКТУ = {"чужая": 20, "смежная": 60}

# Потолок для объявления, которого модель не прочитала и по которому справочник
# профессию не назвал. Не «ноль»: мы не знаем, что там, — может быть, и её
# работа. Но и выше того, что модель поняла своими глазами, такому стоять не за
# что. Пятьдесят пять — ниже порога по умолчанию, но видно, если порог опустить.
ПОТОЛОК_НЕПРОЧИТАННОГО = 55

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
        потолок = ПОТОЛОК_ПО_ВЕРДИКТУ.get(str(result.get("verdict", "")).strip().lower())
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
        понятно = bool(job.get("occupation")) or _по_английски(
            f"{job.get('title', '')} {description[:1500]}")
        if not понятно:
            job["verified"] = False
            if балл > ПОТОЛОК_НЕПРОЧИТАННОГО:
                _lk(log, "log_deep_unreadable", title=job.get("title"),
                    was=балл, now=ПОТОЛОК_НЕПРОЧИТАННОГО)
                job["score"] = ПОТОЛОК_НЕПРОЧИТАННОГО
        else:
            job["verified"] = True
    # bilingual output ("it-en") is about twice as long — without this the text
    # breaks off mid-sentence
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
