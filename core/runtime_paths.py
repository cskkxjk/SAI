"""Keep installed resources separate from writable, per-user data."""

import os
import shutil
import sys
from pathlib import Path


APP_DIR = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parents[1])

DATA_DIR_NAME = "SAI"
LEGACY_DATA_DIR_NAMES = ("CapsWriterOffline",)
POINTER_NAME = "data-dir.txt"


def _user_data_root() -> Path:
    """Per-user data root for the current platform."""
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME",
                               str(Path.home() / ".local" / "share")))


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


def read_pointer(folder):
    """Resolve the data directory recorded next to the program or user data.

    Returns None when the file is missing, unreadable or points to a place
    whose parent folder does not exist (an unplugged drive, for example).
    """
    path = Path(folder) / POINTER_NAME
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for encoding in ("utf-8", "mbcs"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidate = Path(os.path.expandvars(line)).expanduser()
            except (OSError, ValueError):
                return None
            try:
                candidate = Path(os.path.abspath(candidate))
            except (OSError, ValueError):
                return None
            if not candidate.parent.is_dir():
                return None
            return candidate
        return None
    return None


def write_pointer(target, folders):
    """Record the data directory so every process finds it after a restart."""
    target = Path(target).resolve()
    written = []
    for folder in folders:
        path = Path(folder) / POINTER_NAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(target), encoding="utf-8-sig")
        except OSError:
            continue
        written.append(path)
    if not written:
        raise OSError(f"无法在 {Path(folders[0])} 写入数据目录记录文件")
    return written


def default_data_directory(app_dir=APP_DIR):
    """The directory used when nothing points somewhere else."""
    if not (app_dir / "installed.flag").is_file():
        return Path(app_dir)
    return _user_data_root() / DATA_DIR_NAME


def data_directory(app_dir=APP_DIR):
    override = os.environ.get("SAI_DATA_DIR")
    if override:
        return Path(override).resolve()
    default = default_data_directory(app_dir)
    seen = []
    for folder in (Path(app_dir), default):
        if folder in seen:
            continue
        seen.append(folder)
        pointer = read_pointer(folder)
        if pointer is not None:
            return pointer
    if default == Path(app_dir):
        return Path(app_dir)
    target = default
    if target.exists():
        return target
    for name in LEGACY_DATA_DIR_NAMES:
        legacy = target.parent / name
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
