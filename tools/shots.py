"""Снимки для сайта и README.

Скриншоты живут в docs/img и устаревают молча: интерфейс меняется, а на
странице продукта остаётся прошлогодний. Отсюда скрипт — снять всё заново
одной командой, а не собирать по одному руками и не забывать половину.

Данные подставные и лежат во временной папке: настоящий профиль трогать нельзя,
да и показывать чужие вакансии с чужим CV на странице продукта незачем. Каждая
страница снимается дважды, в светлой теме и в тёмной.

    python tools/shots.py

Нужен Chrome или Edge — они и так есть на любой Windows. Пользуется их же
безголовым режимом: ставить ради восьми картинок playwright с его полугигабайтом
браузеров не стоит.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
sys.path.insert(0, str(ROOT))

# Ширина как у прежних снимков: они уже вставлены в README и на страницу, и
# менять её значило бы переверстать обе.
WIDTH = 1180
СТРАНИЦЫ = [
    # (файл, адрес, высота, есть ли разбор у верхней вакансии)
    #
    # Разбор раскрыт всегда — <details open>, и правильно: ради него всё и
    # затевалось. Но на снимке списка он занимает весь экран, и вместо списка
    # выходит одна карточка. Поэтому список снимается в том состоянии, в каком
    # программа бывает сразу после быстрого поиска: оценки есть, глубокий разбор
    # ещё не прошёл. А разбор — своим снимком и повыше.
    ("results", "/results", 1120, False),
    ("advice", "/results", 1240, True),
    ("quick-search", "/simple", 820, False),
    # Провайдеров восемь, в 860 влезало шесть — третий ряд обрезался посередине.
    ("models", "/models", 1330, False),
]

БРАУЗЕРЫ = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def браузер() -> str:
    for path in БРАУЗЕРЫ:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
        raise SystemExit("не нашёлся ни Chrome, ни Edge — снимать нечем")


def номер_версии() -> str:
    """Последний выпущенный тег — то, что человек и увидит, скачав программу."""
    try:
        out = subprocess.run(["git", "tag", "--sort=-v:refname"], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if line.startswith("v"):
                return line[1:].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "0.0.0"


def свободный_порт() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


РАЗБОР = json.dumps({
    "salary_estimate": "72 000 – 88 000 € в год",
    "company_insights": [
        "Своя продуктовая компания, не аутсорс: 140 человек, офис в Берлине.",
        "На Kununu 3.9 из 5, хвалят гибкий график и ругают медленный найм.",
        "Удалённо можно из любой страны ЕС, два раза в год приезжать в офис.",
    ],
    "sources": ["https://www.kununu.com/de/northwind",
                "https://www.glassdoor.de/northwind"],
    "cv_changes": [
        "Вынести дизайн-систему в первую строку опыта — в вакансии это главное требование.",
        "Добавить цифру к оптимизации сборки: «время сборки с 4 до 1.5 минут» весомее слова «ускорил».",
    ],
    "linkedin_changes": [
        "В заголовок профиля добавить «Design Systems» — по нему ищут рекрутеры этой компании.",
    ],
    "cover_hint": "Упомяните переход команды на монорепозиторий: у них в вакансии это отдельным пунктом.",
}, ensure_ascii=False)

ВАКАНСИИ = [
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
                "а у вас он второй язык — это единственное слабое место.", advice=""),
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
         reason="Стек соседний: Vue вместо React. Переучиваться недолго, но в вакансии "
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


def заполнить(data: Path) -> None:
    """Кладёт во временную папку то, что должно быть видно на снимках."""
    os.environ["AIJS_DATA_DIR"] = str(data)
    from jobsearch import appstate, config, db, profiles

    # Заполняем профиль по умолчанию, а не заводим новый: сервер приходит без
    # cookie и показывает именно его. Свежесозданный профиль на снимке был пуст,
    # а рядом в переключателе стоял чужой — «Ничего с такой оценкой нет».
    profiles.ensure_migrated()
    slug = profiles.default_slug()
    profiles.rename(slug, "Виктор")
    profiles.set_active(slug)
    db.init()

    cfg = config.load()
    cfg["profile"].update(summary="Senior Frontend Engineer, дизайн-системы и сборка",
                          roles="Frontend Engineer, Frontend Lead",
                          skills="React, TypeScript, Vite, Storybook, монорепозитории",
                          seniority="senior", salary="от 75 000 € в год",
                          work_format="remote", languages="английский C1, немецкий A2")
    cfg["search"].update(locations="Германия, Нидерланды, удалённо в ЕС", threshold=70)
    cfg["ui"]["lang"] = "ru"
    config.save(cfg)
    config.cv_text_path().write_text(CV, encoding="utf-8")
    config.cv_meta_path().write_text(
        json.dumps({"filename": "Лаврентьев CV.pdf", "chars": len(CV)}, ensure_ascii=False),
        encoding="utf-8")

    run = db.start_run()
    for job in ВАКАНСИИ:
        db.save_job(job, run)
    db.finish_run(run, found=214, fresh=37, matched=len(ВАКАНСИИ), status="ok", log="")
    appstate.mark_setup_done(cfg["llm"])
    return slug


def поднять(data: Path, port: int):
    среда = dict(os.environ, AIJS_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=среда)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1).read()
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("приложение не поднялось")


def снять(chrome: str, url: str, out: Path, height: int, профиль: Path) -> None:
    subprocess.run([
        chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--user-data-dir={профиль}",
        f"--window-size={WIDTH},{height}",
        # Страница дорисовывает себя скриптами; без паузы снимок ловит её
        # наполовину собранной — с непроявленными карточками и пустой шапкой.
        "--virtual-time-budget=4000",
        f"--screenshot={out}", url,
    ], check=True, capture_output=True)


def разбор(есть: bool) -> None:
    """Включает и убирает глубокий разбор у верхней вакансии.

    Сервер открывает базу заново на каждый запрос, так что менять её отсюда
    можно и на ходу.
    """
    from jobsearch import db
    with db.conn() as c:
        c.execute("UPDATE jobs SET advice=? WHERE key='a1'",
                  (РАЗБОР if есть else "",))


def тема(какая: str) -> None:
    """Тему держит сервер, а не браузер: безголовый Chrome каждый раз приходит с
    чистым профилем, и переключатель в окне ему не помог бы. Пишется в файл
    состояния, который сервер перечитывает на каждой отрисовке."""
    from jobsearch import appstate
    appstate.set_theme(какая)


def main() -> None:
    chrome = браузер()
    data = Path(tempfile.mkdtemp(prefix="aijs-shots-"))
    профиль_браузера = Path(tempfile.mkdtemp(prefix="aijs-chrome-"))
    print("данные:", data)
    заполнить(data)
    # Без этого файла программа зовёт себя «dev», и это видно в шапке на каждом
    # снимке. На странице продукта такая подпись выглядит недоделкой.
    версия = ROOT / "VERSION.txt"
    было = версия.read_text(encoding="utf-8") if версия.exists() else None
    версия.write_text(номер_версии(), encoding="utf-8")
    port = свободный_порт()
    proc = поднять(data, port)
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        for какая, суффикс in (("light", ""), ("dark", "-dark")):
            тема(какая)
            for имя, путь, height, с_разбором in СТРАНИЦЫ:
                разбор(с_разбором)
                url = f"http://127.0.0.1:{port}{путь}"
                out = OUT / f"{имя}{суффикс}.png"
                снять(chrome, url, out, height, профиль_браузера)
                print(f"  {out.name}: {out.stat().st_size // 1024} КБ")
    finally:
        proc.terminate()
        версия.write_text(было, encoding="utf-8") if было else версия.unlink(missing_ok=True)
        shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(профиль_браузера, ignore_errors=True)


if __name__ == "__main__":
    main()
