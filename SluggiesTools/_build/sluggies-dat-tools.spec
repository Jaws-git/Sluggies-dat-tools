# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH).resolve().parents[1]

# DearPyGui ships no PyInstaller hook: collect its extension module
# (dearpygui._dearpygui) and its bundled vcruntime140_1.dll explicitly,
# plus all submodules of the pure-Python API layer.
dearpygui_datas, dearpygui_binaries, dearpygui_hiddenimports = collect_all("dearpygui")

hiddenimports = (
    collect_submodules("numpy")
    + collect_submodules("PIL")
    + dearpygui_hiddenimports
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
    binaries=dearpygui_binaries,
    datas=dearpygui_datas,
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
