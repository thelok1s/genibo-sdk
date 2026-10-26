# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Genibo Studio (one-folder build; macOS also gets a .app bundle).
# Driven by packaging/build.py; can also be run directly:
#   uv run --extra studio --with pyinstaller pyinstaller packaging/genibo-studio.spec
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
ROOT = HERE.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
ICONS = HERE / "icons"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# Compiled user programs import genibo.* at build time inside the frozen app, so
# every SDK module must be in the archive even if the studio never imports it.
hiddenimports = collect_submodules("genibo") + ["serial.tools.list_ports"]

a = Analysis(  # noqa: F821
    [str(HERE / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=collect_data_files("genibo"),  # includes genibo/studio/resources/genibo.png (window icon)
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "hypothesis", "_pytest", "IPython"],
    noarchive=False,
)
# The GTK3 platform-theme plugin would drag GTK/Pango into the package dependencies;
# the studio uses Qt's Fusion style, so ship without it.
a.binaries = [b for b in a.binaries if "platformthemes/libqgtk3" not in b[0].replace("\\", "/")]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="genibo-studio",
    console=not (IS_MAC or IS_WIN),  # GUI subsystem on Windows/macOS; Linux needs a console for the `genibo` CLI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Linux executables carry no icon; the .desktop entry and hicolor theme provide it.
    icon=str(ICONS / "genibo.ico") if IS_WIN else str(ICONS / "genibo.icns") if IS_MAC else None,
    upx=False,
    strip=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="genibo-studio",
)

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name="Genibo Studio.app",
        icon=str(ICONS / "genibo.icns"),
        bundle_identifier="org.genibo.studio",
        version=VERSION,
        info_plist={
            "CFBundleName": "Genibo Studio",
            "CFBundleDisplayName": "Genibo Studio",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "14.0",  # PySide6 6.12 wheels require macOS 14
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
