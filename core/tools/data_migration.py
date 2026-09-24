"""Relocate the per-user data directory (settings, hotwords, logs, recordings)."""

import shutil
from pathlib import Path

SKIP_NAMES = ("data-dir.txt",)


def validate_target(source, target, app_dir=None):
    """Return a readable error message when target cannot hold user data."""
    source = Path(source).resolve()
    target = Path(target).resolve()
    if target == source:
        return "新目录与当前数据目录相同，无需更改。"
    if source in target.parents:
        return "新目录不能放在当前数据目录里面。"
    if target in source.parents:
        return "新目录不能是当前数据目录的上级目录。"
    if app_dir is not None and target == Path(app_dir).resolve():
        return "新目录不能是程序安装目录，请另选一个文件夹。"
    if target.exists() and not target.is_dir():
        return f"{target} 已经是一个文件，请另选一个文件夹。"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".sai-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"无法写入该目录：{exc}"
    return None


def move_data(source, target):
    """Move every entry from source into target, keeping existing files."""
    source = Path(source).resolve()
    target = Path(target).resolve()
    report = {"moved": [], "skipped": [], "failed": []}
    if not source.is_dir() or source == target:
        return report
    for entry in sorted(source.iterdir()):
        if entry.name in SKIP_NAMES:
            continue
        destination = target / entry.name
        try:
            if entry.is_dir():
                report["moved"].extend(_merge_tree(entry, destination, report))
            elif destination.exists():
                report["skipped"].append(entry.name)
            else:
                shutil.move(str(entry), str(destination))
                report["moved"].append(entry.name)
        except OSError as exc:
            report["failed"].append(f"{entry.name}（{exc}）")
    return report


def _merge_tree(source, target, report, prefix=""):
    moved = []
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        if entry.is_dir():
            moved.extend(_merge_tree(entry, destination, report, prefix + entry.name + "/"))
            try:
                entry.rmdir()
            except OSError:
                pass
            continue
        if destination.exists():
            report["skipped"].append(prefix + entry.name)
            continue
        shutil.move(str(entry), str(destination))
        moved.append(prefix + entry.name)
    try:
        source.rmdir()
    except OSError:
        pass
    return moved
