# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
SITE_PKGS = Path("D:/Codex/wisland-exe/build/site-packages")

datas = [
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.txt"), "."),
    (str(ROOT / "config.example.json"), "."),
    (str(ROOT / "wisland" / "icon.png"), "wisland"),
    (str(ROOT / "wisland" / "icon.ico"), "wisland"),
    (str(ROOT / "wisland" / "assets"), "wisland/assets"),
]
binaries = []
hiddenimports = []

datas += collect_data_files("comtypes")
datas += collect_data_files("winrt", includes=["**/*.pywinrt"])
hiddenimports += collect_submodules("winrt")
hiddenimports += collect_submodules("winrt.windows.ui.notifications")
hiddenimports += collect_submodules("winrt.windows.applicationmodel")
hiddenimports += collect_submodules("winrt.windows.devices.bluetooth.advertisement")
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
    excludes=[
        "PIL",
        "Pillow",
        "tkinter",
        "unittest",
        "pydoc",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
    ],
    noarchive=False,
    optimize=0,
)

# 瘦身过滤：移除未使用的软件 OpenGL、多余翻译、开发期 .lib/.h，以及重复打包的超大 DLL
def _keep_toc_item(item, is_binary: bool) -> bool:
    dest = str(item[0]).replace("\\", "/")
    low = dest.lower()
    if low.endswith("opengl32sw.dll"):
        return False
    if "pyside6/translations/" in low and not (
        low.endswith("_zh_cn.qm") or low.endswith("_en.qm")
    ):
        return False
    if low.startswith("sherpa_onnx/") and low.endswith((".lib", ".h", ".cmake", ".pdb")):
        return False
    # 去重：保留 numpy.libs / sherpa_onnx/lib 内的原位置，移除根目录重复拷贝
    if "/" not in dest and (
        low.startswith("libscipy_openblas64_")
        or low in ("onnxruntime.dll", "sherpa-onnx-c-api.dll")
    ):
        return False
    return True

a.binaries = [x for x in a.binaries if _keep_toc_item(x, True)]
a.datas = [x for x in a.datas if _keep_toc_item(x, False)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ledge",
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
    version=str(ROOT / "build" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Ledge",
)
