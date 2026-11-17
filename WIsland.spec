# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
SITE_PKGS = Path("D:/Codex/wisland-exe/build/site-packages")

datas = [
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "config.example.json"), "."),
    (str(ROOT / "wisland" / "icon.png"), "wisland"),
    (str(ROOT / "wisland" / "icon.ico"), "wisland"),
]
binaries = []
hiddenimports = []

datas += collect_data_files("comtypes")
hiddenimports += collect_submodules("winrt")
tmp_ret = collect_all("sherpa_onnx")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

pathex = [str(ROOT)]
if SITE_PKGS.exists():
    pathex.append(str(SITE_PKGS))

a = Analysis(
    [str(ROOT / "run.pyw")],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
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
    name="WIsland",
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
    icon=[str(ROOT / "wisland" / "icon.ico")],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WIsland",
)
