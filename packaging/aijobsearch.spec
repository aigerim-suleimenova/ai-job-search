# PyInstaller: сборка «AI Job Search» в одно приложение.
# Собирается на той ОС, под которую нужен результат: macOS → .app, Windows → .exe,
# Linux → каталог с бинарником (AppImage собирается отдельным шагом в CI).
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

# Подпись: без сертификата собирается ad-hoc (работает локально, но при скачивании
# система предупредит). Свой сертификат задаётся переменной окружения, чтобы в
# репозитории не хранить ничего личного.
# Версия — из тега сборки (v0.8.3 → 0.8.3). Раньше она была вписана в спеку
# руками и разъезжалась с тегом: в «Свойствах» приложения стояло 1.0.0, что бы
# ни выпускали. Без тега (локальная сборка) остаётся запасное значение.
_ref = os.environ.get("GITHUB_REF_NAME", "")
VERSION = _ref[1:] if _ref.startswith("v") and _ref[1:2].isdigit() else "0.0.0"

SIGN_IDENTITY = os.environ.get("AIJS_CODESIGN_IDENTITY") or None
ENTITLEMENTS = str(ROOT / "packaging" / "entitlements.plist") if SIGN_IDENTITY else None

# Лицензии библиотек, которые уезжают внутрь: MIT, BSD и Apache разрешают
# распространять свой код при условии, что их текст едет вместе с ним. Файл
# собирается прямо здесь, чтобы он всегда соответствовал реальному составу сборки.
sys.path.insert(0, str(ROOT / "packaging"))
import collect_licenses                                  # noqa: E402
collect_licenses.main()

# Шаблоны и стили лежат рядом с кодом и читаются во время работы — кладём внутрь сборки.
datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "static"), "static"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD-PARTY-LICENSES.txt"), "."),
]

# APScheduler и uvicorn грузят части динамически — PyInstaller их сам не находит.
# Переводы тоже: язык выбирается во время работы, поэтому модули locales
# приходится перечислить явно — иначе в собранном приложении все языки, кроме
# первых четырёх, молча откатывались бы на английский.
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
