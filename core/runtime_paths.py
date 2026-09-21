"""Keep installed resources separate from writable, per-user data."""

import os
import shutil
import sys
from pathlib import Path


APP_DIR = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parents[1])


def data_directory(app_dir=APP_DIR):
    override = os.environ.get("CAPSWRITER_DATA_DIR")
    if override:
        return Path(override).resolve()
    if (app_dir / "installed.flag").is_file():
        return Path(os.environ["LOCALAPPDATA"]) / "CapsWriterOffline"
    return app_dir


DATA_DIR = data_directory()


def initialize_user_data(app_dir=APP_DIR, data_dir=DATA_DIR):
    data_dir.mkdir(parents=True, exist_ok=True)
    if app_dir.resolve() == data_dir.resolve():
        return
    files = [app_dir / name for name in (
        "config_gui.json", "hot.txt", "hot-rule.txt", "hot-server.txt")]
    files.extend(path for path in (app_dir / "LLM").rglob("*")
                 if path.is_file() and "__pycache__" not in path.parts
                 and path.suffix != ".pyc")
    for source in files:
        target = data_dir / source.relative_to(app_dir)
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents child processes from resetting user data.
            try:
                with target.open("xb") as output, source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output)
            except FileExistsError:
                pass
