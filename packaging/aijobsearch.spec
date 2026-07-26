# PyInstaller: сборка «AI Job Search» в одно приложение.
# Собирается на той ОС, под которую нужен результат: macOS → .app, Windows → .exe,
# Linux → каталог с бинарником (AppImage собирается отдельным шагом в CI).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

# Шаблоны и стили лежат рядом с кодом и читаются во время работы — кладём внутрь сборки.
datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "static"), "static"),
]

# APScheduler и uvicorn грузят части динамически — PyInstaller их сам не находит.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("apscheduler")
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
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
