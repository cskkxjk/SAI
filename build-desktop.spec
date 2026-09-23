# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import json
from shutil import copy2, copytree, ignore_patterns
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
runtime_manifest = root / "core/server/engines/llama/bin/runtime-version.json"
if (not runtime_manifest.is_file()
        or json.loads(runtime_manifest.read_text(encoding="utf-8")).get("tag") != "b10621"):
    raise RuntimeError("Install llama.cpp b10621 DLLs and runtime-version.json; see README.")
required = ("srt", "gguf", "onnxruntime", "sherpa_onnx", "sounddevice",
            "soundfile", "pynput", "pystray")
import importlib.util
for module in required:
    if importlib.util.find_spec(module) is None:
        raise RuntimeError(f"Missing build dependency: {module}")


def rich_unicode_data_modules():
    """rich loads its unicode tables through importlib with dashed file names."""
    spec = importlib.util.find_spec("rich._unicode_data")
    locations = list(getattr(spec, "submodule_search_locations", None) or [])
    if not locations:
        return []
    return sorted(f"rich._unicode_data.{path.stem}"
                  for path in Path(locations[0]).glob("unicode*.py"))

a = Analysis(
    ["sai.py"],
    pathex=[str(root)],
    binaries=[],
    datas=collect_data_files("sherpa_onnx"),
    hiddenimports=list(required) + ["sentencepiece"] + rich_unicode_data_modules(),
    runtime_hooks=["build_hook.py"],
    excludes=["IPython", "PySide6", "PySide2", "PyQt5", "matplotlib",
              "wx", "torch", "funasr", "transformers", "datasets",
              "sklearn", "pandas", "pyarrow",
              "soynlp", "scipy", "Cython", "cython", "lxml", "pydoc_data"],
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
    name="SAI", console=False, icon=str(root / "assets/icon.ico"),
    version=str(root / "installer/file_version_info.txt"),
    contents_directory="internal", upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SAI", upx=False)
destination = Path(coll.name)
for name in ("config_client.py", "config_server.py", "config_gui.json",
             "hot.txt", "hot-server.txt", "hot-rule.txt", "readme.md", "LICENSE"):
    copy2(root / name, destination / name)
for name in ("core", "LLM"):
    copytree(root / name, destination / name,
             ignore=ignore_patterns("__pycache__", "*.pyc", "*.bak", "export", "logs"))
(destination / "assets").mkdir(exist_ok=True)
copy2(root / "assets/icon.ico", destination / "assets/icon.ico")
copy2(root / "assets/icon-recording.ico", destination / "assets/icon-recording.ico")
# Models are downloaded on demand beside the installed executable.
