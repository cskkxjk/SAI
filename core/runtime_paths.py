"""Keep installed resources separate from writable, per-user data."""

import os
import shutil
import sys
from pathlib import Path


APP_DIR = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parents[1])

DATA_DIR_NAME = "SAI"
LEGACY_DATA_DIR_NAMES = ("CapsWriterOffline",)


def _user_data_root() -> Path:
    """Return the per-user data root for the current platform."""
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def _adopt_data_dir(legacy, target):
    """Move the pre-rename data directory, falling back to a plain copy."""
    try:
        legacy.rename(target)
        return True
    except OSError:
        pass
    try:
        shutil.copytree(legacy, target, dirs_exist_ok=True)
        return True
    except OSError:
        return False


def data_directory(app_dir=APP_DIR):
    override = os.environ.get("SAI_DATA_DIR")
    if override:
        return Path(override).resolve()
    if not (app_dir / "installed.flag").is_file():
        return app_dir
    local_app_data = _user_data_root()
    target = local_app_data / DATA_DIR_NAME
    if target.exists():
        return target
    for name in LEGACY_DATA_DIR_NAMES:
        legacy = local_app_data / name
        if not legacy.is_dir():
            continue
        if _adopt_data_dir(legacy, target):
            return target
        # Keep using the old directory when it cannot be moved or copied.
        return legacy
    return target


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
