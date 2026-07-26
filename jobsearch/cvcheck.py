"""Проверка CV: машиночитаемость для ATS-роботов и визуальная читаемость для людей.

Две части, потому что читают документ два разных «получателя»:
- ATS (Greenhouse, Workable, Lever...) парсит текст. Ему безразличен дизайн, но
  колонки, таблицы, текст внутри картинок и нестандартные заголовки секций ломают
  разбор — поле «опыт» приезжает пустым, и человек резюме уже не увидит.
- Рекрутер смотрит 6-10 секунд. Здесь важны иерархия, плотность, длина.

Технические проверки считаются локально, визуальные — моделью по картинке страницы.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, i18n, llm, profiles

# Заголовки ключевых секций на языках, которые встречаются в наших CV.
SECTION_PATTERNS = {
    "experience": r"(work\s+)?experience|employment|empleo|erfahrung|berufserfahrung|"
                  r"esperienz|expérience|опыт работы|опыт",
    "education": r"education|ausbildung|formazione|istruzione|educación|formation|образование",
    "skills": r"skills|competenc|kenntnisse|f[äa]higkeiten|habilidades|compétences|навыки|"
              r"технологии|abilit",
}
CONTACT_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "phone": r"(\+\d[\d\s().-]{7,}|\b\d{3}[\s.-]\d{3}[\s.-]\d{2,4}\b)",
    "linkedin": r"linkedin\.com/[\w/-]+",
}


def cv_file():
    """Оригинал CV (pdf/docx/txt) в каталоге профиля, если загружен."""
    for path in sorted(profiles.dir().glob("cv.*")):
        if path.suffix.lower() not in (".txt", ".json"):
            return path
    return None


def _render_pages(pdf: Path, out_dir: Path, limit: int = 3) -> list:
    """PDF → PNG постранично (sips умеет только первую страницу, поэтому режем)."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return []
    pages = []
    reader = PdfReader(str(pdf))
    for i, page in enumerate(reader.pages[:limit]):
        single = out_dir / f"page{i + 1}.pdf"
        writer = PdfWriter()
        writer.add_page(page)
        with open(single, "wb") as fh:
            writer.write(fh)
        png = out_dir / f"page{i + 1}.png"
        r = subprocess.run(["sips", "-s", "format", "png", "-Z", "1600",
                            str(single), "--out", str(png)],
                           capture_output=True, text=True)
        if r.returncode == 0 and png.exists():
            pages.append(png)
    return pages


def raw_pdf_text(pdf: Path) -> str:
    """Текст ровно так, как его достаёт обычный парсер — без наших исправлений.
    Именно это увидит ATS: чинить извлечение на своей стороне он не будет."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # noqa: BLE001
        return ""


def technical_checks(text: str, pdf: Path = None) -> dict:
    """Проверки, которые считаются локально, без модели."""
    issues, ok = [], []
    pages = 1
    images = 0
    if pdf and pdf.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf))
            pages = len(reader.pages)
            images = sum(len(p.images) for p in reader.pages)
        except Exception:  # noqa: BLE001 — битый PDF не должен ронять проверку
            pass
        raw = raw_pdf_text(pdf)
        if raw:
            text = raw  # оцениваем то, что получит робот, а не наш вычищенный текст
    words = text.split()

    # 1. Извлекается ли текст вообще — самый тяжёлый отказ: ATS увидит пустоту
    per_page = len(text) / max(pages, 1)
    if per_page < 200:
        issues.append(("critical", "no_text"))
    else:
        ok.append("text_extractable")

    # 2. Разрядка букв (дизайнерские шаблоны) — текст парсится в кашу
    single = sum(1 for w in words if len(w) == 1)
    if words and single / len(words) > 0.4:
        issues.append(("critical", "letter_spacing"))

    # 3. Объём и длина
    if pages > 3:
        issues.append(("warn", "too_long"))
    elif len(words) < 150:
        issues.append(("warn", "too_short"))
    else:
        ok.append("length_ok")

    # 4. Ключевые секции — по ним ATS раскладывает резюме на поля
    low = text.lower()
    missing = [name for name, pat in SECTION_PATTERNS.items() if not re.search(pat, low)]
    if missing:
        issues.append(("warn", "missing_sections:" + ",".join(missing)))
    else:
        ok.append("sections_ok")

    # 5. Контакты
    no_contact = [name for name, pat in CONTACT_PATTERNS.items() if not re.search(pat, text, re.I)]
    if "email" in no_contact:
        issues.append(("critical", "no_email"))
    if len(no_contact) >= 2:
        issues.append(("warn", "few_contacts:" + ",".join(no_contact)))
    else:
        ok.append("contacts_ok")

    # 6. Годы в опыте — без дат ATS не построит таймлайн
    if len(re.findall(r"\b(19|20)\d{2}\b", text)) < 2:
        issues.append(("warn", "no_dates"))
    else:
        ok.append("dates_ok")

    # 7. Картинки: фото и иконки часто уносят с собой текст
    if images > 3:
        issues.append(("warn", f"many_images:{images}"))

    score = 100
    for severity, _ in issues:
        score -= 30 if severity == "critical" else 10
    return {"score": max(0, score), "issues": issues, "ok": ok,
            "pages": pages, "words": len(words), "images": images}


VISUAL_PROMPT = """Ты — рекрутер и одновременно эксперт по ATS (системам отбора резюме).
Перед тобой страницы резюме кандидата в виде изображений: {files}
Открой каждое изображение инструментом Read и посмотри на него.

Оцени по двум осям.

1) ATS-совместимость (как машина разберёт файл): многоколоночная вёрстка,
таблицы, текст внутри графики, иконки вместо подписей, нестандартные заголовки
секций, шрифты, врезки и колонтитулы — всё это ломает автоматический разбор.

2) Читаемость для человека за 7 секунд: видно ли сразу имя, целевую роль,
последнее место работы; иерархия и контраст заголовков; плотность и «воздух»;
длина строк; единообразие; выделены ли достижения и цифры.

Верни ТОЛЬКО JSON:
{{
  "ats_score": <0-100>,
  "visual_score": <0-100>,
  "layout": "<одна фраза: одна колонка / две колонки / сложная сетка>",
  "ats_risks": ["<конкретная проблема вёрстки и чем грозит>", ...],
  "visual_issues": ["<что мешает читать>", ...],
  "strengths": ["<что уже хорошо>", ...],
  "fixes": ["<конкретная правка в порядке важности>", ...],
  "verdict": "<2-3 предложения: годится ли как есть, что сделать в первую очередь>"
}}
Все тексты пиши {lang}."""


def visual_review(pages: list, cfg: dict) -> dict:
    if not pages:
        return {}
    files = ", ".join(str(p) for p in pages)
    data = llm.ask_json(
        VISUAL_PROMPT.format(files=files, lang=i18n.out_lang(cfg)),
        model=cfg["llm"].get("deep_model", ""),
        claude_bin=cfg["llm"].get("claude_bin", "claude"),
        provider=cfg["llm"].get("provider", "claude_cli"),
        timeout=600,
        allowed_tools=["Read"],
    )
    return data if isinstance(data, dict) else {}


KEYWORDS_PROMPT = """Ты — ATS-система отбора резюме и одновременно рекрутер.

Резюме кандидата:
{cv}

Вакансии, на которые кандидат метит (заголовок + требования):
{jobs}

Оцени, пройдёт ли это резюме автоматический отбор по ключевым словам. ATS сопоставляет
термины из описания вакансии с текстом резюме: точные формулировки, названия технологий,
инструментов, методик, сертификатов, отраслевые термины. Синоним, которого нет в резюме
буквально, машина не засчитает.

Верни ТОЛЬКО JSON:
{{
  "keyword_score": <0-100: доля важных терминов вакансий, реально присутствующих в резюме>,
  "present": ["<важный термин, который есть в резюме>", ...],
  "missing": ["<важный термин из вакансий, которого в резюме НЕТ, но опыт кандидата его допускает>", ...],
  "cannot_claim": ["<термин, который требуют, но у кандидата такого опыта нет — выдумывать нельзя>", ...],
  "verdict": "<2-3 предложения: пройдёт ли фильтр, что добавить в первую очередь>"
}}
Все тексты пиши {lang}."""


def keyword_check(cfg: dict, cv: str, jobs: list) -> dict:
    """Пройдёт ли резюме отбор по ключевым словам под реальные вакансии кандидата."""
    if not jobs or not cv:
        return {}
    listing = "\n\n".join(
        f"— {j.get('title', '')} @ {j.get('company', '')}\n{(j.get('description') or '')[:1200]}"
        for j in jobs[:5]
    )
    data = llm.ask_json(
        KEYWORDS_PROMPT.format(cv=cv[:6000], jobs=listing, lang=i18n.out_lang(cfg)),
        model=cfg["llm"].get("deep_model", ""),
        claude_bin=cfg["llm"].get("claude_bin", "claude"),
        provider=cfg["llm"].get("provider", "claude_cli"),
        timeout=600,
    )
    return data if isinstance(data, dict) else {}


def analyze(cfg: dict) -> dict:
    """Полная проверка загруженного CV: техническая + визуальная."""
    text = config.cv_text()
    pdf = cv_file()
    if not text and not pdf:
        return {"error": "no_cv"}
    tech = technical_checks(text, pdf)
    visual = {}
    if pdf and pdf.suffix.lower() == ".pdf" and shutil.which("sips"):
        with tempfile.TemporaryDirectory() as tmp:
            pages = _render_pages(pdf, Path(tmp))
            visual = visual_review(pages, cfg)
    from . import db
    keywords = keyword_check(cfg, text, db.matched_jobs(limit=5, min_score=0, sort="score"))
    result = {"tech": tech, "visual": visual, "keywords": keywords,
              "filename": (config.cv_meta() or {}).get("filename", "")}
    # итоговый балл: техника и вёрстка важнее эстетики — ATS отсекает молча
    ats_llm = visual.get("ats_score")
    ats = round((tech["score"] + ats_llm) / 2) if isinstance(ats_llm, (int, float)) else tech["score"]
    result["ats_total"] = ats
    result["visual_total"] = visual.get("visual_score")
    try:
        (profiles.dir() / "cv_check.json").write_text(json.dumps(result, ensure_ascii=False))
    except OSError:
        pass
    return result


def last_result() -> dict:
    path = profiles.dir() / "cv_check.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}
