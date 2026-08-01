"""Checking a CV: machine-readability for ATS robots and visual readability for people.

Two parts, because two very different readers open the document:
- An ATS (Greenhouse, Workable, Lever…) parses the text. The design means nothing
  to it, but columns, tables, text inside images and unusual section headings
  break the parse — the "experience" field arrives empty, and a person never sees
  the CV at all.
- A recruiter looks for six to ten seconds. Here hierarchy, density and length matter.

The technical checks are computed locally; the visual ones by the model from a
picture of the page.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, i18n, llm, profiles

# Headings of the key sections, in the languages our CVs turn up in.
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
    """The original CV (pdf/docx/txt) in the profile directory, if one was uploaded."""
    for path in sorted(profiles.dir().glob("cv.*")):
        if path.suffix.lower() not in (".txt", ".json"):
            return path
    return None


def _render_pages(pdf: Path, out_dir: Path, limit: int = 3) -> list:
    """PDF → PNG page by page (sips can only do the first page, so we split first)."""
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
    """The text exactly as an ordinary parser gets it — without our repairs.
    This is what an ATS will see: it will not fix the extraction on its side."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # noqa: BLE001
        return ""


def technical_checks(text: str, pdf: Path = None) -> dict:
    """The checks that are computed locally, without the model."""
    issues, ok = [], []
    pages = 1
    images = 0
    if pdf and pdf.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf))
            pages = len(reader.pages)
            images = sum(len(p.images) for p in reader.pages)
        except Exception:  # noqa: BLE001 — a broken PDF must not bring the check down
            pass
        raw = raw_pdf_text(pdf)
        if raw:
            text = raw  # judge what the robot will get, not our cleaned-up text
    words = text.split()

    # 1. Does any text come out at all — the worst failure: an ATS sees emptiness
    per_page = len(text) / max(pages, 1)
    if per_page < 200:
        issues.append(("critical", "no_text"))
    else:
        ok.append("text_extractable")

    # 2. Letter-spacing (designer templates) — the text parses into mush
    single = sum(1 for w in words if len(w) == 1)
    if words and single / len(words) > 0.4:
        issues.append(("critical", "letter_spacing"))

    # 3. Size and length
    if pages > 3:
        issues.append(("warn", "too_long"))
    elif len(words) < 150:
        issues.append(("warn", "too_short"))
    else:
        ok.append("length_ok")

    # 4. The key sections — an ATS lays the CV out into fields by them
    low = text.lower()
    missing = [name for name, pat in SECTION_PATTERNS.items() if not re.search(pat, low)]
    if missing:
        issues.append(("warn", "missing_sections:" + ",".join(missing)))
    else:
        ok.append("sections_ok")

    # 5. Contacts
    no_contact = [name for name, pat in CONTACT_PATTERNS.items() if not re.search(pat, text, re.I)]
    if "email" in no_contact:
        issues.append(("critical", "no_email"))
    if len(no_contact) >= 2:
        issues.append(("warn", "few_contacts:" + ",".join(no_contact)))
    else:
        ok.append("contacts_ok")

    # 6. Years in the experience — without dates an ATS cannot build a timeline
    if len(re.findall(r"\b(19|20)\d{2}\b", text)) < 2:
        issues.append(("warn", "no_dates"))
    else:
        ok.append("dates_ok")

    # 7. Images: photos and icons often carry text away with them
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
        llm=cfg["llm"],
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
    """Will the CV pass a keyword screen against the person's real target jobs."""
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
        llm=cfg["llm"],
        timeout=600,
    )
    return data if isinstance(data, dict) else {}


def analyze(cfg: dict) -> dict:
    """The full check of the uploaded CV: technical plus visual.

    The technical part is worked out here, on this computer, and always succeeds.
    The other two ask the model, and the model may be out of reach — that used to
    take the whole check down with it, the finished technical findings included.
    A page of real answers was thrown away because an extra could not be had.

    Now what fails is remembered and told, and everything that did work is kept.
    """
    text = config.cv_text()
    pdf = cv_file()
    if not text and not pdf:
        return {"error": "no_cv"}
    tech = technical_checks(text, pdf)
    lang = cfg.get("ui", {}).get("lang", "en")
    unfinished = []

    visual = {}
    if pdf and pdf.suffix.lower() == ".pdf" and shutil.which("sips"):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pages = _render_pages(pdf, Path(tmp))
                visual = visual_review(pages, cfg)
        except Exception as e:  # noqa: BLE001 — an extra must not sink the rest
            unfinished.append(i18n.err(lang, e))

    from . import db
    try:
        keywords = keyword_check(cfg, text, db.matched_jobs(limit=5, min_score=0, sort="score"))
    except Exception as e:  # noqa: BLE001
        keywords = {}
        unfinished.append(i18n.err(lang, e))

    result = {"tech": tech, "visual": visual, "keywords": keywords,
              "unfinished": unfinished[:1],
              "filename": (config.cv_meta() or {}).get("filename", "")}
    # the final score: the mechanics and the layout matter more than the looks —
    # an ATS cuts you off without a word
    ats_llm = visual.get("ats_score")
    ats = round((tech["score"] + ats_llm) / 2) if isinstance(ats_llm, (int, float)) else tech["score"]
    result["ats_total"] = ats
    result["visual_total"] = visual.get("visual_score")
    try:
        (profiles.dir() / "cv_check.json").write_text(json.dumps(result, ensure_ascii=False),
                                                  encoding="utf-8")
    except OSError:
        pass
    return result


def last_result() -> dict:
    path = profiles.dir() / "cv_check.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}
