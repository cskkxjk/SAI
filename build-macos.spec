# -*- mode: python ; coding: utf-8 -*-
"""macOS 打包配置。

生成 onedir 文件夹 dist/SAI 以及 dist/SAI.app。
模块源码（core/、config_*.py）与用户可编辑文件（LLM/、hot*.txt、assets/）
在构建后由 make_macos_app.sh 复制到可执行文件旁（.app 内即 Contents/MacOS），
与 Windows 版布局一致；模型不打包，运行时按需下载或另行放置。
"""
from pathlib import Path
import importlib.util
import re

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.building.osx import BUNDLE
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

root = Path(SPECPATH)

_match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]",
                   (root / "config_server.py").read_text(encoding="utf-8"), re.M)
VERSION = _match.group(1) if _match else "0.0.0"

required = ("srt", "gguf", "onnxruntime", "sherpa_onnx", "sounddevice",
            "soundfile", "pynput", "PIL", "AppKit", "Foundation", "objc", "Quartz")
for module in required:
    if importlib.util.find_spec(module) is None:
        raise RuntimeError(f"Missing build dependency: {module}")


def rich_unicode_data_modules():
    """rich 通过 importlib 加载带连字符的 unicode 表文件。"""
    spec = importlib.util.find_spec("rich._unicode_data")
    locations = list(getattr(spec, "submodule_search_locations", None) or [])
    if not locations:
        return []
    return sorted(f"rich._unicode_data.{path.stem}"
                  for path in Path(locations[0]).glob("unicode*.py"))


datas = (
    collect_data_files("sherpa_onnx")
    + collect_data_files("_sounddevice_data")
    + collect_data_files("soundfile")
)
binaries = (
    collect_dynamic_libs("onnxruntime")
    + collect_dynamic_libs("sherpa_onnx")
    + collect_dynamic_libs("soundfile")
)

hiddenimports = list(required) + [
    "sentencepiece",
    "pynput.keyboard._darwin",
    "pynput.mouse._darwin",
    "numba",
    "llvmlite",
] + rich_unicode_data_modules()

a = Analysis(
    ["sai.py"],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    runtime_hooks=["build_hook.py"],
    excludes=["IPython", "PySide6", "PySide2", "PyQt5", "matplotlib",
              "wx", "torch", "funasr", "transformers", "datasets",
              "sklearn", "pandas", "pyarrow",
              "soynlp", "scipy", "Cython", "cython", "lxml",
              "pydoc_data", "pystray"],
    noarchive=True,
)

private = ("core", "config_client", "config_server", "LLM")
a.pure = [(name, src, kind) for name, src, kind in a.pure
          if not any(name == m or name.startswith(m + ".") for m in private)]
a.datas = [(name, src, kind) for name, src, kind in a.datas
           if not any(name.replace("\\", "/").startswith(m + "/") or
                      name in (m + ".py", m + ".pyc") for m in private)]

exe = EXE(
    PYZ(a.pure), a.scripts, [], exclude_binaries=True,
    name="SAI", console=False, icon=str(root / "assets/icon.icns"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SAI", upx=False)

info_plist = {
    "CFBundleName": "SAI",
    "CFBundleDisplayName": "SAI 离线语音输入",
    "CFBundleIdentifier": "com.cskkxjk.sai",
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    "CFBundleIconFile": "icon.icns",
    "NSMicrophoneUsageDescription": "SAI 需要使用麦克风进行离线语音识别。",
    "NSAppleEventsUsageDescription": "SAI 需要发送通知或打开文件夹。",
    "NSHighResolutionCapable": True,
    "LSMinimumSystemVersion": "12.0",
}

app = BUNDLE(
    coll,
    name="SAI.app",
    icon=str(root / "assets/icon.icns"),
    bundle_identifier="com.cskkxjk.sai",
    info_plist=info_plist,
)
