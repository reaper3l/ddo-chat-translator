# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 模式）。

用法：pyinstaller --noconfirm build.spec

注意：一定要用 onedir（不要 onefile）。旧版试过 onefile，
打包 rapidocr_onnxruntime 的动态库时子进程会崩（exit code 3221225477）。
"""
from PyInstaller.utils.hooks import collect_all

APP_NAME = "DDO翻译助手_v3.0.4"      # 改版本时改这里（EXE/COLLECT/瘦身都用它）

datas = []
binaries = []
hiddenimports = []

for package in ("rapidocr_onnxruntime", "onnxruntime", "PIL", "numpy"):
    collected = collect_all(package)
    datas += collected[0]
    binaries += collected[1]
    hiddenimports += collected[2]

# 术语表和提示词数据要一起打进去
datas += [("assets", "assets")]
datas += [("app_icon.ico", ".")]        # 窗口图标（运行时读取）

a = Analysis(
    ["main.py"],
    pathex=[],
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
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="version_info.py",        # 可执行文件属性里显示作者/版本
    icon="app_icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

# --------------------------------------------------------------------------
# 瘦身：删掉运行时用不到的文件。
# 我们只做图像识别（不走视频），模型也只用 PP-OCRv5，所以下面这些可以安全移除；
# 这么做能把发布包压到 Gitee 发行版 100MB 的附件限制以内。
# --------------------------------------------------------------------------
import glob
import os
import shutil

_internal = os.path.join(DISTPATH, APP_NAME, "_internal")
_patterns = [
    "cv2/*ffmpeg*.dll",                    # OpenCV 的视频解码库（约 29MB）
    "rapidocr_onnxruntime/models/ch_PP-OCRv4_*.onnx",   # 只用 v5，v4 用不到（约 15MB）
    "PIL/_avif*.pyd",                      # AVIF 图片支持（用不到，约 7MB）
]
for _pattern in _patterns:
    for _path in glob.glob(os.path.join(_internal, _pattern)):
        try:
            os.remove(_path)
            print("removed:", os.path.relpath(_path, _internal))
        except Exception as _exc:
            print("skip:", _path, _exc)

for _name in ("lxml",):                    # rapidocr 不会 import lxml
    _path = os.path.join(_internal, _name)
    if os.path.isdir(_path):
        try:
            shutil.rmtree(_path)
            print("removed dir:", _name)
        except Exception as _exc:
            print("skip dir:", _name, _exc)
