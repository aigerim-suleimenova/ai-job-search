"""Checking the packaged app: is the executable there, and the data with it.

A window cannot be opened in CI (there is no display), so we check what the build
contains — enough to catch the usual PyInstaller misses: absent templates,
forgotten hidden imports, an empty binary.
"""
import sys
from pathlib import Path

import system_libraries

# The Windows console is not UTF-8 by default, and non-Latin text in the output
# brings the script down with an encoding error — after the check itself has
# already passed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def find_bundle() -> Path:
    for candidate in (DIST / "AI Job Search.app" / "Contents" / "Resources",
                      DIST / "AI Job Search" / "_internal",
                      DIST / "AI Job Search"):
        if candidate.is_dir():
            return candidate
    raise SystemExit(f"No build found in {DIST}")


def main() -> int:
    bundle = find_bundle()
    problems = []

    exe_names = ["AI Job Search", "AI Job Search.exe"]
    exes = [p for name in exe_names
            for p in (DIST.rglob(name)) if p.is_file() and p.stat().st_size > 1_000_000]
    if not exes:
        problems.append("the executable is missing, or suspiciously small")

    for needed in ("templates/index.html", "templates/simple.html", "static/style.css"):
        if not (bundle / needed).exists():
            problems.append(f"{needed} is missing — the interface will not render")

    # On Windows the window is drawn by .NET, and this library is the bridge to it.
    # Without it the program builds as though nothing were wrong and dies later, in
    # someone's hands, on opening the window: checking the contents is cheaper than
    # finding out from a screenshot.
    if sys.platform == "win32":
        if not (bundle / "pythonnet" / "runtime" / "Python.Runtime.dll").exists():
            problems.append("pythonnet/runtime/Python.Runtime.dll is missing — the window will not open")

    # On Linux the reverse: these libraries must NOT be inside. The build's copies
    # shadow the machine's own, and the window — drawn by the system's WebKit —
    # stops opening; the program falls back to the browser and nobody knows why.
    # The spec throws them out, and here we make sure it really did: PyInstaller
    # collects them by itself, so a version of it that names them differently
    # would quietly bring the trouble back.
    if sys.platform.startswith("linux"):
        strays = sorted({p.name for p in bundle.rglob("*.so*")
                         if system_libraries.is_system_library(p.name)})
        if strays:
            problems.append("system libraries got into the build — the window will not open: "
                            + ", ".join(strays[:6]))

    if problems:
        print("The build check did not pass:")
        for p in problems:
            print(" -", p)
        return 1
    print(f"The build is sound: {bundle}")
    print(f"  executable: {exes[0].name}, {exes[0].stat().st_size // 1024 // 1024} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
