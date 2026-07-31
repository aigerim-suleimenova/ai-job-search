"""Версия программы — одна на всех, и её должно быть видно человеку.

Раньше её негде было посмотреть: ни на одной странице, ни в журнале ошибок.
Когда человек писал «не запускается», первым вопросом было «а какая у вас
версия?» — и ответить на него он не мог.

Номер проставляется при сборке (см. packaging/aijobsearch.spec) в файл рядом
с программой. Запуск из исходников версии не имеет и честно говорит «dev».
"""
import sys
from functools import lru_cache
from pathlib import Path

FALLBACK = "dev"


def _candidates():
    """Где лежит VERSION.txt: в собранном приложении — рядом с данными сборки,
    при запуске из исходников — в корне репозитория."""
    here = Path(__file__).resolve().parent.parent
    yield here / "VERSION.txt"
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        yield Path(bundle) / "VERSION.txt"


@lru_cache(maxsize=1)
def current() -> str:
    for path in _candidates():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return FALLBACK
