# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

repo = Path(SPECPATH).resolve().parents[1]
tools = repo / "tools"

a = Analysis(
    [str(tools / "nlscope.py")],
    pathex=[str(tools)],
    binaries=[],
    datas=[],
    hiddenimports=["serial.tools.list_ports", "serial.tools.list_ports_windows"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinkScope",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(repo / "tools" / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LinkScope",
)
