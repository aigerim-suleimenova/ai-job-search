"""Collects the licences of every library that travels inside the app.

MIT, BSD, Apache and nearly all the rest allow the code to be distributed on one
condition: keep their text and their copyright. Close to forty packages end up in
the build, and without a file like this that condition is not met — a small
violation, but a real one, and it is fixed once and for all.

The file is assembled at build time (see packaging/aijobsearch.spec), so it always
matches what is really inside.
"""
import sys
from pathlib import Path

# The Windows console is not UTF-8 by default, and non-Latin text in the output
# brings the script down with an encoding error — exactly as it did in
# verify_build.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import importlib.metadata as md
except ImportError:                       # Python < 3.8, which will never reach us
    import importlib_metadata as md       # type: ignore

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD-PARTY-LICENSES.txt"

HEADER = """AI Job Search — third-party components
=====================================

The application is distributed under the MIT licence (see the LICENSE file). The
libraries listed below are built into the packaged program; each one remains
under its own licence, and its terms apply independently of the application's.

Full licence texts follow the list — wherever the package publishes them.

"""

# The names of licence-text files, as distributions tend to place them
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
    return expr or "not stated"


def _license_text(dist) -> str:
    """The licence text from the distribution, if it is there at all.

    We read from disk through locate(): read_text() looks for the file next to
    METADATA, while modern wheels put licences into a dist-info/licenses/
    subdirectory.
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

    out.append("\n\n" + "=" * 70 + "\nFULL LICENCE TEXTS\n" + "=" * 70 + "\n")
    for name, version, lic, text in rows:
        if not text:
            continue
        out.append(f"\n{'-' * 70}\n{name} {version} — {lic}\n{'-' * 70}\n\n{text}\n")
    return "\n".join(out)


def main() -> int:
    text = collect()
    OUT.write_text(text, encoding="utf-8")
    packages = text.count("\n  ")
    print(f"{OUT.name}: {len(text) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
