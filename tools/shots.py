"""Screenshots for the site and the README.

Screenshots go stale silently: the interface changes while last year's picture
stays on the product page. Hence this script — shoot them all afresh with one
command, rather than collecting them one by one by hand and forgetting half.

There are two sets, and it was the second that kept being forgotten. docs/img
holds the Russian screenshots — the ones the README and the page in this
repository show. The product page at mrwd.github.io is in English, its
screenshots are named differently, live in another repository, and so fell
furthest behind: while these were being updated, that page kept a year-old
version with four providers out of eight.

    python tools/shots.py                     # docs/img, in Russian
    python tools/shots.py --site FOLDER       # the product page, in English

The second set prints the sizes: its markup has the width and height written in
by hand, and if they are not corrected the page jumps as it loads.

The data is made up and lives in a temporary folder: a real profile must not be
touched, and there is no reason to show somebody's jobs with somebody's CV on a
product page. Every page is shot twice, in the light theme and in the dark.

Chrome or Edge is needed — they are on any Windows anyway. It uses their own
headless mode: installing playwright with its half a gigabyte of browsers for the
sake of eight pictures is not worth it.
"""
import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
sys.path.insert(0, str(ROOT))

# The same width as the previous screenshots: they are already embedded in the
# README and on the page, and changing it would mean re-laying-out both.
WIDTH = 1180
PAGES = [
    # (file, address, height, whether the top job has its deep advice)
    #
    # The advice is always expanded — <details open> — and rightly so: it is what
    # the whole thing was built for. But in a shot of the list it fills the entire
    # screen, and instead of a list you get one card. So the list is shot in the
    # state the program is in right after a quick search: the scores are there,
    # the deep analysis has not run yet. And the advice gets a shot of its own,
    # taller.
    ("results", "/results", 1120, False),
    ("advice", "/results", 1240, True),
    ("quick-search", "/simple", 820, False),
    # There are eight providers; 860 fitted six — the third row was cut in half.
    ("models", "/models", 1330, False),
]

# What to call the files: (in the light theme, in the dark). The product page has
# names of its own that were settled earlier — renaming them would mean editing
# its markup as well.
FILENAMES = {
    "repo": {"results": ("results", "results-dark"),
             "advice": ("advice", "advice-dark"),
             "quick-search": ("quick-search", "quick-search-dark"),
             "models": ("models", "models-dark")},
    "site": {"results": ("results-light", "results-dark"),
             "advice": ("advice-light", "advice-dark"),
             "quick-search": ("start-light", "start-dark"),
             "models": ("model-light", "model-dark")},
}

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def browser() -> str:
    for path in BROWSERS:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
        raise SystemExit("neither Chrome nor Edge was found — nothing to shoot with")


def version_number() -> str:
    """The latest released tag — what a person will see once they download it."""
    try:
        out = subprocess.run(["git", "tag", "--sort=-v:refname"], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if line.startswith("v"):
                return line[1:].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "0.0.0"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ADVICE_EN = json.dumps({
    "salary_estimate": "€72,000 – 88,000 a year",
    "company_insights": [
        "Their own product company, not an agency: 140 people, office in Berlin.",
        "3.9 out of 5 on Kununu — praised for flexible hours, criticised for slow hiring.",
        "Remote from anywhere in the EU, with two office visits a year.",
    ],
    "sources": ["https://www.kununu.com/de/northwind",
                "https://www.glassdoor.de/northwind"],
    "cv_changes": [
        "Move the design system into the first line of your experience — the ad puts it first too.",
        "Put a number on the build work: «build time from 4 minutes to 1.5» beats the word «faster».",
    ],
    "linkedin_changes": [
        "Add «Design Systems» to your headline — that is what recruiters there search for.",
    ],
    "cover_hint": "Mention moving the team to a monorepo: their ad lists it as a separate point.",
}, ensure_ascii=False)

JOBS_EN = [
    dict(key="a1", title="Senior Frontend Engineer", company="Northwind Labs",
         location="Berlin · remote", url="https://northwind.example/careers/fe-senior",
         source="greenhouse", is_direct=1, is_agency=0, score=91, verified=1,
         posted_at="2026-07-28",
         description="React, TypeScript, design system, a team of six.",
         reason="Your React and TypeScript experience lines up almost exactly, and the design "
                "system in their requirements is precisely what you have spent the last two "
                "years on. No German needed — the team works in English.",
         advice=""),
    dict(key="a2", title="Staff Software Engineer, Platform", company="Helios",
         location="Amsterdam · hybrid", url="https://helios.example/jobs/staff-platform",
         source="lever", is_direct=1, is_agency=0, score=84, verified=1,
         posted_at="2026-07-26",
         description="Kubernetes, Go, internal platforms for forty teams.",
         reason="Your infrastructure and mentoring experience matches. Go is in the "
                "requirements and it is your second language — that is the one weak spot.",
         advice=""),
    dict(key="a3", title="Full-stack Developer (SAP Integration)", company="Vandelay AG",
         location="Munich · on site", url="https://vandelay.example/karriere/fullstack",
         source="personio", is_direct=1, is_agency=0, score=76, verified=0,
         posted_at="2026-07-24",
         description="SAP PI/PO, Java, REST, logistics integration.",
         reason="SAP PI/PO and Java are among your key skills. The worrying part is German "
                "at B2 — your CV says A2.", advice=""),
    dict(key="a4", title="Frontend Engineer", company="Kestrel Studio",
         location="remote, EU", url="https://kestrel.example/careers/frontend",
         source="workable", is_direct=1, is_agency=0, score=72, verified=0,
         posted_at="2026-07-21",
         description="Vue 3, Nuxt, small product teams.",
         reason="A neighbouring stack: Vue instead of React. Not long to pick up, but the ad "
                "asks for Nuxt experience specifically, and you have none.", advice=""),
]

CV_EN = """Viktor Lavrov — Senior Frontend Engineer

Eight years in frontend, the last two on design systems and builds.
React, TypeScript, Vite, Storybook. Led a team of twelve through a move
to a monorepo, build time from four minutes down to one and a half.

Experience
2022–2026  Frontend Lead, Orbita — a design system of 60 components,
           used by four product teams.
2019–2022  Senior Frontend Engineer, Meridian — the account area,
           300k users a month.
2018–2019  Frontend Developer, Sable — markup and integrations.

Languages: Russian (native), English (C1), German (A2).
"""

PROFILE_EN = dict(
    name="Viktor Lavrov",
    summary="Senior Frontend Engineer, design systems and builds",
    roles="Frontend Engineer, Frontend Lead",
    skills="React, TypeScript, Vite, Storybook, monorepos",
    salary="from €75,000 a year",
    languages="English C1, German A2",
    locations="Germany, the Netherlands, remote in the EU",
    cv_file="Lavrov CV.pdf",
)

ADVICE_RU = json.dumps({
    "salary_estimate": "72 000 – 88 000 € в год",
    "company_insights": [
        "Своя продуктовая компания, не аутсорс: 140 человек, офис в Берлине.",
        "На Kununu 3.9 из 5, хвалят гибкий график и ругают медленный найм.",
        "Удалённо можно из любой страны ЕС, два раза в год приезжать в офис.",
    ],
    "sources": ["https://www.kununu.com/de/northwind",
                "https://www.glassdoor.de/northwind"],
    "cv_changes": [
        "Вынести дизайн-систему в первую строку опыта — в jobs это главное требование.",
        "Добавить цифру к оптимизации сборки: «время сборки с 4 до 1.5 минут» весомее слова «ускорил».",
    ],
    "linkedin_changes": [
        "В заголовок профиля добавить «Design Systems» — по нему ищут рекрутеры этой компании.",
    ],
    "cover_hint": "Упомяните переход команды на монорепозиторий: у них в jobs это отдельным пунктом.",
}, ensure_ascii=False)

JOBS_RU = [
    dict(key="a1", title="Senior Frontend Engineer", company="Northwind Labs",
         location="Berlin · удалённо", url="https://northwind.example/careers/fe-senior",
         source="greenhouse", is_direct=1, is_agency=0, score=91, verified=1,
         posted_at="2026-07-28",
         description="React, TypeScript, дизайн-система, команда из шести человек.",
         reason="Ваш опыт с React и TypeScript совпадает почти полностью, а дизайн-система "
                "в требованиях — ровно то, чем вы занимались последние два года. "
                "Немецкий не требуется, команда говорит по-английски.",
         advice=""),
    dict(key="a2", title="Staff Software Engineer, Platform", company="Helios",
         location="Амстердам · гибрид", url="https://helios.example/jobs/staff-platform",
         source="lever", is_direct=1, is_agency=0, score=84, verified=1,
         posted_at="2026-07-26",
         description="Kubernetes, Go, внутренние платформы для сорока команд.",
         reason="Совпадает опыт с инфраструктурой и наставничеством. Go в требованиях, "
                "а у вас он второй lang — это единственное слабое where.", advice=""),
    dict(key="a3", title="Full-stack Developer (SAP Integration)", company="Vandelay AG",
         location="Мюнхен · офис", url="https://vandelay.example/karriere/fullstack",
         source="personio", is_direct=1, is_agency=0, score=76, verified=0,
         posted_at="2026-07-24",
         description="SAP PI/PO, Java, REST, интеграция с логистикой.",
         reason="SAP PI/PO и Java у вас в ключевых навыках. Настораживает требование "
                "немецкого на уровне B2 — в вашем CV указан A2.", advice=""),
    dict(key="a4", title="Frontend Engineer", company="Kestrel Studio",
         location="удалённо, ЕС", url="https://kestrel.example/careers/frontend",
         source="workable", is_direct=1, is_agency=0, score=72, verified=0,
         posted_at="2026-07-21",
         description="Vue 3, Nuxt, небольшие продуктовые команды.",
         reason="Стек соседний: Vue вместо React. Переучиваться недолго, но в jobs "
                "просят именно опыт с Nuxt, а его у вас нет.", advice=""),
]

CV = """Виктор Лаврентьев — Senior Frontend Engineer

Восемь лет во фронтенде, последние два — дизайн-системы и сборка.
React, TypeScript, Vite, Storybook. Вёл переход команды из двенадцати
человек на монорепозиторий, время сборки с четырёх минут до полутора.

Опыт
2022–2026  Frontend Lead, Orbita — дизайн-система на 60 компонентов,
           её используют четыре продуктовые команды.
2019–2022  Senior Frontend Engineer, Meridian — личный кабинет, 300k
           пользователей в месяц.
2018–2019  Frontend Developer, Sable — вёрстка и интеграции.

Языки: русский (родной), английский (C1), немецкий (A2).
"""

PROFILE_RU = dict(
    name="Виктор",
    summary="Senior Frontend Engineer, дизайн-системы и сборка",
    roles="Frontend Engineer, Frontend Lead",
    skills="React, TypeScript, Vite, Storybook, монорепозитории",
    salary="от 75 000 € в год",
    languages="английский C1, немецкий A2",
    locations="Германия, Нидерланды, удалённо в ЕС",
    cv_file="Лаврентьев CV.pdf",
)

SHOWN = {
    "ru": (PROFILE_RU, CV, JOBS_RU, ADVICE_RU),
    "en": (PROFILE_EN, CV_EN, JOBS_EN, ADVICE_EN),
}


def fill_in(data: Path, lang: str) -> None:
    """Кладёт во временную папку то, что должно быть видно на снимках."""
    os.environ["AIJS_DATA_DIR"] = str(data)
    from jobsearch import appstate, config, db, profiles

    profile, cv, jobs, _ = SHOWN[lang]

    # Заполняем profile по умолчанию, а не заводим новый: сервер приходит без
    # cookie и показывает именно его. Свежесозданный profile на снимке был пуст,
    # а рядом в переключателе стоял чужой — «Ничего с такой оценкой нет».
    profiles.ensure_migrated()
    slug = profiles.default_slug()
    profiles.rename(slug, profile["name"])
    profiles.set_active(slug)
    db.init()

    cfg = config.load()
    cfg["profile"].update(summary=profile["summary"], roles=profile["roles"],
                          skills=profile["skills"], seniority="senior",
                          salary=profile["salary"], work_format="remote",
                          languages=profile["languages"])
    cfg["search"].update(locations=profile["locations"], threshold=70)
    cfg["ui"]["lang"] = lang
    config.save(cfg)
    config.cv_text_path().write_text(cv, encoding="utf-8")
    config.cv_meta_path().write_text(
        json.dumps({"filename": profile["cv_file"], "chars": len(cv)}, ensure_ascii=False),
        encoding="utf-8")

    run = db.start_run()
    for job in jobs:
        db.save_job(job, run)
    db.finish_run(run, found=214, fresh=37, matched=len(jobs), status="ok", log="")
    appstate.mark_setup_done(cfg["llm"])
    return slug


def serve(data: Path, port: int):
    env = dict(os.environ, AIJS_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1).read()
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("the application did not come up")


def shoot(chrome: str, url: str, out: Path, height: int, profile: Path,
          lang: str = "ru") -> None:
    subprocess.run([
        chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--user-data-dir={profile}",
        # The file-picker button is drawn by the browser itself, and it takes its
        # own language rather than the page's. That is why a Russian «Выберите
        # файл» hung on the English product page — the shots were taken on a
        # Russian Windows.
        f"--lang={ {'ru': 'ru', 'en': 'en-US'}[lang] }",
        f"--window-size={WIDTH},{height}",
        # The page finishes drawing itself with scripts; without a pause the shot
        # catches it half-assembled — cards not yet shown and an empty header.
        "--virtual-time-budget=4000",
        f"--screenshot={out}", url,
    ], check=True, capture_output=True)


def advice(present: bool, lang: str) -> None:
    """Turns the deep advice on the top job on and off.

    The server opens the database afresh on every request, so it can be changed
    from here while everything is running.
    """
    from jobsearch import db
    with db.conn() as c:
        c.execute("UPDATE jobs SET advice=? WHERE key='a1'",
                  (SHOWN[lang][3] if present else "",))


def theme(which: str) -> None:
    """The theme is held by the server, not the browser: headless Chrome arrives
    with a clean profile every time, and the switch in the window would not help
    it. It is written to the state file, which the server re-reads on every
    render."""
    from jobsearch import appstate
    appstate.set_theme(which)


def size(png: Path) -> str:
    width, height = struct.unpack(">II", png.read_bytes()[16:24])
    return f"{width}x{height}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", metavar="FOLDER",
                        help="shoot the product-page set, in English")
    args = parser.parse_args()

    flavour = "site" if args.site else "repo"
    lang = "en" if args.site else "ru"
    target = Path(args.site).resolve() if args.site else OUT
    if not target.is_dir():
        raise SystemExit(f"there is no folder {target} — nowhere to shoot into")

    chrome = browser()
    data = Path(tempfile.mkdtemp(prefix="aijs-shots-"))
    browser_profile = Path(tempfile.mkdtemp(prefix="aijs-chrome-"))
    print(f"flavour: {flavour} ({lang}) → {target}")
    fill_in(data, lang)
    # Without this file the program calls itself "dev", and that shows in the
    # header on every shot. On a product page such a label looks unfinished.
    version = ROOT / "VERSION.txt"
    before = version.read_text(encoding="utf-8") if version.exists() else None
    version.write_text(version_number(), encoding="utf-8")
    port = free_port()
    proc = serve(data, port)
    try:
        for which, where in (("light", 0), ("dark", 1)):
            theme(which)
            for name, path, height, with_advice in PAGES:
                advice(with_advice, lang)
                out = target / f"{FILENAMES[flavour][name][where]}.png"
                shoot(chrome, f"http://127.0.0.1:{port}{path}", out, height,
                      browser_profile, lang)
                print(f"  {out.name}: {out.stat().st_size // 1024} KB, {size(out)}")
    finally:
        proc.terminate()
        version.write_text(before, encoding="utf-8") if before else version.unlink(missing_ok=True)
        shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(browser_profile, ignore_errors=True)


if __name__ == "__main__":
    main()
