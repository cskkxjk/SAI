# coding: utf-8
"""GitHub Release 更新检查与安装包下载。

参考 clash-verge-rev 的更新实现：
- 只在远端版本更高时提示，相同或更旧一律忽略；
- 检查结果按时间间隔缓存，避免每次启动都请求 GitHub；
- 安装包下载后按发布资产的 SHA256 摘要校验；
- 支持「跳过此版本」，只跳过被标记的那一个版本；
- 安装由 GUI 调用 create_install_script() 生成批处理：
  等待 SAI 退出 -> 静默覆盖安装 -> 重新启动新版本。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from core.model_download import DownloadCancelled
from core.runtime_paths import DATA_DIR

REPO = "cskkxjk/SAI"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
USER_AGENT = "SAI-Updater"
CHECK_INTERVAL = 24 * 60 * 60  # 秒
REQUEST_TIMEOUT = (10, 30)
DOWNLOAD_TIMEOUT = (15, 120)
CHUNK_SIZE = 1024 * 1024

UPDATES_DIR = DATA_DIR / "updates"
STATE_FILE = UPDATES_DIR / "state.json"

_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+){0,3})(.*)$")


def parse_version(text):
    """把 v1.0.2 / 1.0.2+build / 1.0.2-beta 解析成数字元组，无法解析返回 None"""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _padded(parts, length):
    return parts + (0,) * (length - len(parts))


def compare_versions(remote, current):
    """远端相对本地：1 更新，0 相同，-1 更旧；无法比较返回 None"""
    remote_parts = parse_version(remote)
    current_parts = parse_version(current)
    if remote_parts is None or current_parts is None:
        return None
    length = max(len(remote_parts), len(current_parts))
    remote_parts = _padded(remote_parts, length)
    current_parts = _padded(current_parts, length)
    return (remote_parts > current_parts) - (remote_parts < current_parts)


def is_build_to_stable(current, remote):
    """本地是 1.0.2+构建号 时，远端回到 1.0.2 正式版也算更新（对齐 clash-verge）"""
    if not isinstance(current, str) or not isinstance(remote, str):
        return False
    base, separator, build = current.strip().lstrip("vV").partition("+")
    if not separator or not build or "-" in base:
        return False
    return base == remote.strip().lstrip("vV")


def is_newer(remote, current):
    comparison = compare_versions(remote, current)
    if comparison is None:
        return False
    return comparison > 0 or (comparison == 0 and is_build_to_stable(current, remote))


def normalize_version(text):
    """去掉 v 前缀，保留其余后缀"""
    if not isinstance(text, str):
        return ""
    return text.strip().lstrip("vV")


@dataclass
class ReleaseInfo:
    """一条可用的新版本信息；没有安装包时 installer_* 为空"""

    version: str
    tag: str
    name: str
    notes: str
    page_url: str
    installer_name: str = ""
    installer_url: str = ""
    installer_size: int = 0
    installer_sha256: str = ""
    published_at: str = ""

    @property
    def can_install(self):
        return bool(self.installer_name and self.installer_url)


def pick_installer(assets):
    """挑选安装包资产，优先 SAI-*-Setup.exe"""
    assets = [asset for asset in assets if isinstance(asset, dict)]
    for asset in assets:
        if str(asset.get("name", "")).lower().endswith("-setup.exe"):
            return asset
    for asset in assets:
        if str(asset.get("name", "")).lower().endswith(".exe"):
            return asset
    return {}


def parse_release(data, current_version):
    """把 GitHub Release JSON 转成 ReleaseInfo；无更新或不是正式版返回 None"""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    version = normalize_version(tag or data.get("name") or "")
    if not version or not is_newer(version, current_version):
        return None
    installer = pick_installer(data.get("assets") or [])
    digest = str(installer.get("digest") or "").lower()
    if digest.startswith("sha256:"):
        digest = digest[len("sha256:"):]
    return ReleaseInfo(
        version=version,
        tag=tag,
        name=str(data.get("name") or tag),
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        installer_name=str(installer.get("name") or ""),
        installer_url=str(installer.get("browser_download_url") or ""),
        installer_size=int(installer.get("size") or 0),
        installer_sha256=digest,
        published_at=str(data.get("published_at") or ""),
    )


def fetch_latest_release(current_version, session=None, api_url=None,
                         timeout=REQUEST_TIMEOUT):
    """查询最新正式版；没有更新返回 None。SAI_UPDATE_API 可覆盖接口地址。"""
    url = api_url or os.environ.get("SAI_UPDATE_API") or API_LATEST
    session = session or requests.Session()
    # GitHub 在部分网络环境下需要走系统代理
    session.trust_env = True
    response = session.get(url, timeout=timeout, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    })
    response.raise_for_status()
    return parse_release(response.json(), current_version)


def read_state(path=None):
    """读取更新状态；文件损坏或不存在时返回空字典"""
    path = Path(path) if path is not None else STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state, path=None):
    """写入更新状态；失败时静默忽略，不影响界面"""
    path = Path(path) if path is not None else STATE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass


def should_check(state, now=None, interval=CHECK_INTERVAL):
    """距离上次检查是否已超过间隔"""
    last = state.get("last_check")
    if not isinstance(last, (int, float)):
        return True
    return (now if now is not None else time.time()) - last >= interval


def mark_checked(state, version="", now=None):
    """记录本次检查时间与看到的版本"""
    state["last_check"] = now if now is not None else time.time()
    if version:
        state["last_seen_version"] = version
    return state


def is_skipped(state, version):
    """该版本是否被用户跳过"""
    return bool(version) and state.get("skipped_version") == version


def installer_path(release, directory=None):
    directory = Path(directory) if directory is not None else UPDATES_DIR
    name = release.installer_name or f"SAI-{release.version}-Setup.exe"
    return directory / name


def sha256_file(path):
    loader = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(CHUNK_SIZE), b""):
            loader.update(data)
    return loader.hexdigest()


def download_installer(release, progress=lambda *args: None, cancel=None,
                       session=None, directory=None):
    """下载安装包到 updates 目录并按 SHA256 校验。

    已存在且校验通过的文件直接复用；取消或校验失败不会留下半成品。
    """
    if not release.can_install:
        raise RuntimeError("该版本没有提供安装包")
    cancel = cancel or threading.Event()
    target = installer_path(release, directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (not release.installer_sha256
                             or sha256_file(target) == release.installer_sha256):
        progress("安装包已下载", target.stat().st_size, target.stat().st_size)
        return target
    session = session or requests.Session()
    session.trust_env = True
    temporary = target.with_name(target.name + ".part")
    try:
        response = session.get(release.installer_url, stream=True,
                               timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
        with response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length")
                        or release.installer_size or 0)
            loader = hashlib.sha256()
            received = 0
            with temporary.open("wb") as output:
                for data in response.iter_content(CHUNK_SIZE):
                    if cancel.is_set():
                        raise DownloadCancelled()
                    received += len(data)
                    output.write(data)
                    loader.update(data)
                    progress(f"正在下载 SAI {release.version}", received, total)
        if cancel.is_set():
            raise DownloadCancelled()
        if release.installer_sha256 and loader.hexdigest() != release.installer_sha256:
            raise RuntimeError("安装包校验失败（SHA256 不匹配），请重试")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def create_install_script(installer, app_exe, log_file=None, directory=None,
                          delay=3):
    """生成静默升级批处理，返回脚本路径。

    脚本会等待 SAI 退出（ping 延迟），用 /SILENT 覆盖安装到上次的目录
    （Inno Setup 默认 UsePreviousAppDir），完成后重新启动新版本。
    """
    installer = Path(installer).resolve()
    app_exe = Path(app_exe).resolve()
    directory = Path(directory) if directory is not None else UPDATES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "install-update.cmd"
    command = (f'start "" /wait "{installer}" /SILENT /SUPPRESSMSGBOXES '
               f"/NORESTART")
    if log_file is not None:
        command += f' /LOG="{Path(log_file).resolve()}"'
    lines = [
        "@echo off",
        f"ping -n {max(1, int(delay))} 127.0.0.1 >nul",
        command,
        f'start "" "{app_exe}"',
        'del "%~f0" >nul 2>nul',
    ]
    # 批处理用系统 ANSI 编码写盘，cmd 才能正确解析中文路径
    encoding = "mbcs" if os.name == "nt" else "utf-8"
    script.write_text("\r\n".join(lines) + "\r\n", encoding=encoding,
                      errors="replace")
    return script
