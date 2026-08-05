# PyInstaller: building "AI Job Search" into a single application.
# Built on the OS the result is wanted for: macOS → .app, Windows → .exe,
# Linux → a directory with the binary (the AppImage is a separate CI step).
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

# Signing: without a certificate the build is ad-hoc (it works locally, but on
# download the system will warn). Your own certificate is given through an
# environment variable, so that nothing personal is kept in the repository.
# The version comes from the build tag (v0.8.3 → 0.8.3). It used to be written into
# the spec by hand and drifted away from the tag: the app's Properties said 1.0.0
# whatever was released. Without a tag (a local build) a fallback value remains.
_ref = os.environ.get("GITHUB_REF_NAME", "")
VERSION = _ref[1:] if _ref.startswith("v") and _ref[1:2].isdigit() else "0.0.0"

SIGN_IDENTITY = os.environ.get("AIJS_CODESIGN_IDENTITY") or None
ENTITLEMENTS = str(ROOT / "packaging" / "entitlements.plist") if SIGN_IDENTITY else None

# The licences of the libraries travelling inside: MIT, BSD and Apache allow their
# code to be distributed on condition that their text travels with it. The file is
# assembled right here, so it always matches what the build really contains.
sys.path.insert(0, str(ROOT / "packaging"))
import collect_licenses                                  # noqa: E402
collect_licenses.main()

# The version number goes inside the build: without it the program cannot name
# itself, and someone whose program misbehaves cannot say what they actually ran.
(ROOT / "VERSION.txt").write_text(VERSION + "\n", encoding="utf-8")

# Templates and styles live next to the code and are read at run time — they go inside the build.
datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "static"), "static"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD-PARTY-LICENSES.txt"), "."),
    (str(ROOT / "VERSION.txt"), "."),
]

# APScheduler and uvicorn load parts of themselves dynamically — PyInstaller does
# not find those on its own. Nor the translations: the language is chosen at run
# time, so the locales modules have to be listed explicitly — otherwise in a
# packaged app every language but the first four would quietly fall back to English.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("apscheduler")
    + collect_submodules("jobsearch.locales")
    + ["jobsearch", "app"]
)

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)

# On Linux the window is drawn by the system's GTK and WebKit, so everything
# underneath them has to be the system's as well: the build's own copies shadow
# the machine's, and the two halves stop fitting together. That is not a guess —
# it is how the window stopped opening. The reasoning is in system_libraries.py.
if sys.platform.startswith("linux"):
    import system_libraries                                # noqa: E402

    a.binaries = [b for b in a.binaries if not system_libraries.is_system_library(b[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AI Job Search",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # без окна терминала — это и есть «обычная программа»
    argv_emulation=sys.platform == "darwin",
    codesign_identity=SIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="AI Job Search",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AI Job Search.app",
        bundle_identifier="com.aijobsearch.app",
        info_plist={
            "CFBundleName": "AI Job Search",
            "CFBundleDisplayName": "AI Job Search",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
