# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).resolve().parents[1]

hiddenimports = (
    collect_submodules("collada")
    + collect_submodules("numpy")
    + collect_submodules("PIL")
)

a = Analysis(
    [str(ROOT / "start.py")],
    pathex=[
        str(ROOT),
        str(ROOT / "SluggiesTools"),
        str(ROOT / "SluggiesTools" / "Icons"),
        str(ROOT / "SluggiesTools" / "Hammerspace"),
        str(ROOT / "SluggiesTools" / "InplacePatcher"),
    ],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sluggies-dat-tools",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="sluggies-dat-tools",
)
