"""The program's version — one for everyone, and a person has to be able to see it.

There used to be nowhere to look it up: not on any page, not in the error log.
When someone wrote "it will not start", the first question was "which version do
you have?" — and they could not answer it.

The number is stamped in at build time (see packaging/aijobsearch.spec) into a
file next to the program. Running from source has no version and says so
honestly: "dev".
"""
import sys
from functools import lru_cache
from pathlib import Path

FALLBACK = "dev"


def _candidates():
    """Where VERSION.txt lives: in a packaged app, next to the build's data; when
    running from source, in the root of the repository."""
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
