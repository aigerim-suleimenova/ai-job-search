"""Собирает лицензии всех библиотек, которые уезжают внутри приложения.

MIT, BSD, Apache и почти все остальные разрешают распространять код при одном
условии: сохранить их текст и копирайт. В сборку попадает под сорок пакетов,
и без такого файла это условие не выполняется — нарушение мелкое, но настоящее,
и чинится один раз навсегда.

Файл собирается на этапе сборки (см. packaging/aijobsearch.spec), поэтому
всегда соответствует тому, что реально лежит внутри.
"""
import sys
from pathlib import Path

try:
    import importlib.metadata as md
except ImportError:                       # Python < 3.8, до нас не дойдёт
    import importlib_metadata as md       # type: ignore

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD-PARTY-LICENSES.txt"

HEADER = """AI Job Search — сторонние компоненты
=====================================

Приложение распространяется по лицензии MIT (файл LICENSE). Внутрь собранной
программы попадают перечисленные ниже библиотеки; каждая остаётся под своей
лицензией, и её условия действуют независимо от лицензии самого приложения.

Полные тексты лицензий приведены после списка — там, где пакет их публикует.

"""

# Имена файлов с текстом лицензии, как их кладут в дистрибутивы
LICENSE_FILES = ("LICENSE", "LICENCE", "COPYING", "NOTICE")


def _license_name(dist) -> str:
    meta = dist.metadata
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    if classifiers:
        return classifiers[0].split("::")[-1].strip()
    value = (meta.get("License") or "").strip()
    if value and "\n" not in value and len(value) < 60:
        return value
    expr = (meta.get("License-Expression") or "").strip()
    return expr or "не указана"


def _license_text(dist) -> str:
    """Текст лицензии из дистрибутива, если он там есть.

    Читаем с диска через locate(): read_text() ищет файл рядом с METADATA, а
    современные колёса кладут лицензии в подкаталог dist-info/licenses/.
    """
    for file in dist.files or []:
        name = Path(str(file)).name.upper()
        if not any(name.startswith(x) for x in LICENSE_FILES) or name.endswith(".PY"):
            continue
        try:
            path = Path(file.locate())
            if not path.is_file() or path.stat().st_size > 200_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if text.strip():
            return text.strip()
    return ""


def collect() -> str:
    seen = {}
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in seen:
            continue
        seen[name.lower()] = (name, dist.version or "", _license_name(dist), _license_text(dist))

    rows = sorted(seen.values(), key=lambda r: r[0].lower())
    width = max((len(r[0]) for r in rows), default=20)
    out = [HEADER]
    for name, version, lic, _ in rows:
        out.append(f"  {name:<{width}}  {version:<12}  {lic}")

    out.append("\n\n" + "=" * 70 + "\nПОЛНЫЕ ТЕКСТЫ ЛИЦЕНЗИЙ\n" + "=" * 70 + "\n")
    for name, version, lic, text in rows:
        if not text:
            continue
        out.append(f"\n{'-' * 70}\n{name} {version} — {lic}\n{'-' * 70}\n\n{text}\n")
    return "\n".join(out)


def main() -> int:
    text = collect()
    OUT.write_text(text, encoding="utf-8")
    packages = text.count("\n  ")
    print(f"{OUT.name}: {len(text) // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
