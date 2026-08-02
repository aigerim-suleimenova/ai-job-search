"""Exporting the results: CSV (to keep track) and an HTML report (to read or print to PDF)."""
import csv
import html
import io
import json


def _advice(job: dict) -> dict:
    try:
        return json.loads(job["advice"]) if job.get("advice") else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def to_csv(jobs: list) -> str:
    buf = io.StringIO()
    buf.write("﻿")  # a BOM, so non-Latin text opens correctly in Excel
    w = csv.writer(buf)
    w.writerow(["Score %", "Verified", "Title", "Company", "Location", "Posted", "URL", "Source",
                "Reason", "Salary", "CV changes", "LinkedIn changes", "Cover hint"])
    for j in jobs:
        a = _advice(j)
        w.writerow([
            j.get("score", ""),
            "yes" if j.get("verified") else "preliminary",
            j.get("title", ""), j.get("company", ""), j.get("location", ""),
            j.get("posted_at", ""),
            j.get("url", ""), j.get("source", ""),
            j.get("reason", ""),
            a.get("salary_estimate", ""),
            " • ".join(a.get("cv_changes", []) or []),
            " • ".join(a.get("linkedin_changes", []) or []),
            a.get("cover_hint", ""),
        ])
    return buf.getvalue()


def to_markdown(jobs: list, candidate: str, when: str, min_score: int, note: str = "") -> str:
    """Тот же отчёт разметкой. Её можно вставить куда угодно — в заметки, в
    письмо, в переписку — и она останется читаемой даже там, где разметку не
    понимают."""
    строки = [f"# Подходящие вакансии — {candidate}", "",
              f"{when} · вакансий: {len(jobs)}"
              + (f" · {note}" if note else f" · оценка ≥ {min_score}%"), ""]
    for j in jobs:
        a = _advice(j)
        назв = j.get("title") or "—"
        строки.append(f"## {j.get('score', '—')}% · "
                      + (f"[{назв}]({j['url']})" if j.get("url") else назв))
        хвост = " · ".join(x for x in (j.get("company"), j.get("location"),
                                       j.get("posted_at"),
                                       "проверено" if j.get("verified") else "предварительно") if x)
        строки += [f"*{хвост}*", ""]
        if j.get("reason"):
            строки += [j["reason"], ""]
        if a.get("salary_estimate"):
            строки += [f"**Зарплата:** {a['salary_estimate']}", ""]
        for заголовок, ключ in (("Правки в CV", "cv_changes"),
                                ("Правки в LinkedIn", "linkedin_changes")):
            if a.get(ключ):
                строки.append(f"**{заголовок}:**")
                строки += [f"- {c}" for c in a[ключ]]
                строки.append("")
        if a.get("cover_hint"):
            строки += [f"**В отклике:** {a['cover_hint']}", ""]
    if not jobs:
        строки.append("Показывать нечего.")
    return "\n".join(строки)


def to_json(jobs: list, candidate: str, when: str, min_score: int, note: str = "") -> str:
    """Для тех, кто хочет обработать выгрузку своей программой.

    Советы разворачиваются из строки в настоящие поля: в базе они лежат текстом
    с JSON внутри, и отдавать наружу текст, который надо разбирать второй раз,
    значило бы перекладывать нашу работу на человека.
    """
    наружу = []
    for j in jobs:
        a = _advice(j)
        наружу.append({
            "score": j.get("score"),
            "verified": bool(j.get("verified")),
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "posted_at": j.get("posted_at", ""),
            "url": j.get("url", ""),
            "source": j.get("source", ""),
            "is_direct": bool(j.get("is_direct")),
            "reason": j.get("reason", ""),
            "salary_estimate": a.get("salary_estimate", ""),
            "company_insights": a.get("company_insights", []) or [],
            "cv_changes": a.get("cv_changes", []) or [],
            "linkedin_changes": a.get("linkedin_changes", []) or [],
            "cover_hint": a.get("cover_hint", ""),
        })
    return json.dumps({"candidate": candidate, "generated": when,
                       "min_score": min_score, "note": note,
                       "count": len(наружу), "jobs": наружу},
                      ensure_ascii=False, indent=2)


def _e(s) -> str:
    return html.escape(str(s or ""))


def to_html(jobs: list, candidate: str, when: str, min_score: int, note: str = "",
            print_label: str = "Print / Save as PDF") -> str:
    rows = []
    for j in jobs:
        a = _advice(j)
        parts = [
            f'<div class="job">',
            f'<div class="head"><span class="score">{_e(j.get("score"))}%</span>'
            f'<div><h3><a href="{_e(j.get("url"))}">{_e(j.get("title"))}</a></h3>'
            f'<p class="meta">{_e(j.get("company"))} · {_e(j.get("location") or "—")}'
            f'{" · 📅 " + _e(j.get("posted_label") or j.get("posted_at")) if j.get("posted_at") else ""}'
            f'{" · ✓ verified" if j.get("verified") else " · preliminary"}</p></div></div>',
        ]
        if j.get("reason"):
            parts.append(f'<p>💬 {_e(j["reason"])}</p>')
        if a.get("salary_estimate"):
            parts.append(f'<p><b>💰 Salary:</b> {_e(a["salary_estimate"])}</p>')
        if a.get("cv_changes"):
            parts.append("<p><b>📝 CV:</b></p><ul>" +
                         "".join(f"<li>{_e(c)}</li>" for c in a["cv_changes"]) + "</ul>")
        if a.get("linkedin_changes"):
            parts.append("<p><b>💼 LinkedIn:</b></p><ul>" +
                         "".join(f"<li>{_e(c)}</li>" for c in a["linkedin_changes"]) + "</ul>")
        if a.get("cover_hint"):
            parts.append(f'<p><b>✉️ Application:</b> {_e(a["cover_hint"])}</p>')
        parts.append("</div>")
        rows.append("".join(parts))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Job search — {_e(candidate)}</title>
<style>
  body {{ font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 900px; margin: 0 auto; padding: 24px; color: #1c2430; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #67707e; margin: 0 0 24px; }}
  .job {{ border: 1px solid #e3e7ee; border-radius: 12px; padding: 16px 20px; margin-bottom: 16px;
          page-break-inside: avoid; }}
  .head {{ display: flex; gap: 14px; align-items: flex-start; }}
  .score {{ font-size: 20px; font-weight: 700; color: #2563eb; min-width: 54px; text-align: center;
            background: #eff3fe; border-radius: 10px; padding: 8px 4px; }}
  h3 {{ margin: 0; font-size: 16px; }} h3 a {{ color: #1c2430; text-decoration: none; }}
  .meta {{ color: #67707e; font-size: 13px; margin: 4px 0 0; }}
  ul {{ margin: 4px 0; }} p {{ margin: 8px 0; }}
  @media print {{ .job {{ border-color: #ccc; }} .noprint {{ display: none; }} }}
  .noprint button {{ font: inherit; padding: 8px 16px; border-radius: 8px; cursor: pointer;
                     border: 1px solid #2563eb; background: #2563eb; color: #fff; }}
</style></head><body>
<h1>Job matches — {_e(candidate)}</h1>
<p class="sub">Generated {_e(when)} · {len(jobs)} jobs{f" · {_e(note)}" if note else f" with score ≥ {min_score}%"}</p>
<!-- Кнопка, а не совет искать печать в меню браузера. PDF из этой страницы и
     получается — «сохранить как PDF» стоит в том же окне печати. Сама кнопка при
     печати не видна: ей на бумаге делать нечего. -->
<p class="noprint"><button onclick="window.print()">{_e(print_label)}</button></p>
{"".join(rows) or "<p>No jobs to show.</p>"}
</body></html>"""


def cv_html(cv: dict, job_title: str = "", company: str = "") -> str:
    """Renders the tailored CV into a tidy one-page HTML (for printing to PDF)."""
    skills = cv.get("skills") or []
    skills_html = " · ".join(_e(s) for s in skills) if skills else ""
    exp_html = []
    for e in cv.get("experience") or []:
        bullets = "".join(f"<li>{_e(b)}</li>" for b in (e.get("bullets") or []))
        meta = " · ".join(filter(None, [_e(e.get("period")), _e(e.get("location"))]))
        exp_html.append(
            f'<div class="exp"><div class="exp-head"><b>{_e(e.get("role"))}</b>'
            f'<span class="muted">{_e(e.get("company"))}</span></div>'
            f'<div class="muted small">{meta}</div><ul>{bullets}</ul></div>')
    edu_html = "".join(
        f'<div class="small">{_e(x.get("degree"))} — {_e(x.get("place"))} '
        f'<span class="muted">{_e(x.get("period"))}</span></div>'
        for x in (cv.get("education") or []))
    extra_html = "".join(
        f'<div class="small"><b>{_e(x.get("label"))}:</b> {_e(x.get("value"))}</div>'
        for x in (cv.get("extra") or []))
    tag = f'{_e(job_title)} @ {_e(company)}' if job_title else ''
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>CV — {_e(cv.get("name"))}</title>
<style>
  body {{ font: 13.5px/1.5 -apple-system, "Segoe UI", sans-serif; max-width: 820px;
         margin: 0 auto; padding: 32px; color: #1c2430; }}
  h1 {{ font-size: 24px; margin: 0; }}
  .role {{ font-size: 16px; color: #2563eb; font-weight: 600; margin: 2px 0 6px; }}
  .contact {{ color: #67707e; font-size: 13px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: #2563eb;
        border-bottom: 1px solid #e3e7ee; padding-bottom: 4px; margin: 20px 0 10px; }}
  .muted {{ color: #67707e; }} .small {{ font-size: 12.5px; }}
  .exp {{ margin-bottom: 12px; page-break-inside: avoid; }}
  .exp-head {{ display: flex; justify-content: space-between; gap: 12px; }}
  ul {{ margin: 4px 0 0; padding-left: 18px; }} li {{ margin: 2px 0; }}
  .tailored {{ background: #eff3fe; color: #2563eb; font-size: 12px; border-radius: 6px;
               padding: 6px 10px; margin-bottom: 16px; }}
  @media print {{ .tailored {{ display: none; }} body {{ padding: 0; }} }}
</style></head><body>
{f'<div class="tailored">Adapted for: {tag} · Print → Save as PDF</div>' if tag else ''}
<h1>{_e(cv.get("name"))}</h1>
<div class="role">{_e(cv.get("title"))}</div>
<div class="contact">{_e(cv.get("contact"))}</div>
{f'<h2>Summary</h2><p>{_e(cv.get("summary"))}</p>' if cv.get("summary") else ''}
{f'<h2>Skills</h2><p>{skills_html}</p>' if skills_html else ''}
{f'<h2>Experience</h2>{"".join(exp_html)}' if exp_html else ''}
{f'<h2>Education</h2>{edu_html}' if edu_html else ''}
{f'<h2>Additional</h2>{extra_html}' if extra_html else ''}
</body></html>"""


def report_html(jobs: list, candidate: str, when: str, min_score: int, note: str = "") -> str:
    return to_html(jobs, candidate, when, min_score, note)
