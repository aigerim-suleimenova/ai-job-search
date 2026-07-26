"""Проверка собранного приложения: на месте ли исполняемый файл и данные.

Запустить окно в CI нельзя (нет дисплея), поэтому проверяем состав сборки —
этого достаточно, чтобы поймать типичные промахи PyInstaller: пропущенные
шаблоны, забытые скрытые импорты, пустой бинарник.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def find_bundle() -> Path:
    for candidate in (DIST / "AI Job Search.app" / "Contents" / "Resources",
                      DIST / "AI Job Search" / "_internal",
                      DIST / "AI Job Search"):
        if candidate.is_dir():
            return candidate
    raise SystemExit(f"Сборка не найдена в {DIST}")


def main() -> int:
    bundle = find_bundle()
    problems = []

    exe_names = ["AI Job Search", "AI Job Search.exe"]
    exes = [p for name in exe_names
            for p in (DIST.rglob(name)) if p.is_file() and p.stat().st_size > 1_000_000]
    if not exes:
        problems.append("исполняемый файл не найден или подозрительно мал")

    for needed in ("templates/index.html", "templates/simple.html", "static/style.css"):
        if not (bundle / needed).exists():
            problems.append(f"нет {needed} — интерфейс не отрисуется")

    if problems:
        print("Проверка сборки не пройдена:")
        for p in problems:
            print(" -", p)
        return 1
    print(f"Сборка в порядке: {bundle}")
    print(f"  исполняемый файл: {exes[0].name}, {exes[0].stat().st_size // 1024 // 1024} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
