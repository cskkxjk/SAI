# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from shutil import copy2, copytree, ignore_patterns
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
required = ("srt", "gguf", "onnxruntime", "sherpa_onnx", "sounddevice",
            "soundfile", "pynput", "pystray")
import importlib.util
for module in required:
    if importlib.util.find_spec(module) is None:
        raise RuntimeError(f"Missing build dependency: {module}")

a = Analysis(
    ["capswriter.py"],
    pathex=[str(root)],
    binaries=[],
    datas=collect_data_files("sherpa_onnx"),
    hiddenimports=list(required) + ["rich._unicode_data.unicode17-0-0", "sentencepiece"],
    runtime_hooks=["build_hook.py"],
    excludes=["IPython", "PySide6", "PySide2", "PyQt5", "matplotlib",
              "wx", "torch", "funasr", "transformers", "datasets",
              "sklearn", "pandas", "pyarrow"],
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
    name="CapsWriter", console=False, icon=str(root / "assets/icon.ico"),
    contents_directory="internal", upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CapsWriter-Offline", upx=False)
destination = Path(coll.name)
for name in ("config_client.py", "config_server.py", "config_gui.json",
             "hot.txt", "hot-server.txt", "hot-rule.txt", "readme.md", "LICENSE"):
    copy2(root / name, destination / name)
for name in ("core", "assets", "LLM"):
    copytree(root / name, destination / name,
             ignore=ignore_patterns("__pycache__", "*.pyc", "*.bak", "export", "logs"))
# Real directories make the distribution independent of the source checkout.
from gui_launcher import MODEL_INFO
for info in MODEL_INFO.values():
    for relative in info["files"]:
        source = root / relative
        if source.is_file():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            copy2(source, target)
            metadata = source.parent / "model-source.json"
            if metadata.is_file():
                copy2(metadata, target.parent / metadata.name)
