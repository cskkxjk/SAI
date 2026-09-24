# coding: utf-8
"""GitHub Release 更新检查与安装包下载。

参考 clash-verge-rev 的更新实现：
- 只在远端版本更高时提示，相同或更旧一律忽略；
- 检查结果按时间间隔缓存，避免每次启动都请求 GitHub；
- 优先 GitHub API，被限流或失败时回退 releases.atom（不占 API 配额）；
- 安装包下载后按发布资产的 SHA256 摘要校验；
- 支持「跳过此版本」，只跳过被标记的那一个版本；
- 安装由 GUI 调用 create_install_script() 生成批处理：
  等待 SAI 退出 -> 静默覆盖安装 -> 重新启动新版本。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import requests

from core.model_download import DownloadCancelled
from core.runtime_paths import DATA_DIR

REPO = "cskkxjk/SAI"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ATOM_LATEST = f"https://github.com/{REPO}/releases.atom"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
USER_AGENT = "SAI-Updater"
CHECK_INTERVAL = 24 * 60 * 60  # 秒
ATTEMPT_INTERVAL = 15 * 60  # 检查失败后多久可以再试
REQUEST_TIMEOUT = (10, 30)
DOWNLOAD_TIMEOUT = (15, 120)
CHUNK_SIZE = 1024 * 1024
ATOM_NS = "{http://www.w3.org/2005/Atom}"

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


def _machine_arch(machine=None):
    """把 machine 名称归一化为 arm64 / x64"""
    machine = str(machine or platform.machine()).lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x64"
    return machine


def pick_installer(assets, platform_name=None, machine=None):
    """挑选当前平台的安装包资产。

    Windows 优先 sai-desktop-win-x64.exe（兼容旧的 SAI-*-Setup.exe）；
    macOS 优先对应架构的 dmg，依次回退到对应架构 zip、任意 dmg、任意 zip。
    """
    platform_name = platform_name or sys.platform
    assets = [asset for asset in assets if isinstance(asset, dict)]
    if platform_name == "darwin":
        arch = _machine_arch(machine)
        mac_assets = [asset for asset in assets
                      if "macos" in str(asset.get("name", "")).lower()]
        for suffix in (f"-macos-{arch}.dmg", f"-macos-{arch}.zip"):
            for asset in mac_assets:
                if str(asset.get("name", "")).lower().endswith(suffix):
                    return asset
        for suffix in (".dmg", ".zip"):
            for asset in mac_assets:
                if str(asset.get("name", "")).lower().endswith(suffix):
                    return asset
        return {}
    for asset in assets:
        if str(asset.get("name", "")).lower().endswith("sai-desktop-win-x64.exe"):
            return asset
    for asset in assets:
        if str(asset.get("name", "")).lower().endswith("-setup.exe"):
            return asset
    for asset in assets:
        if str(asset.get("name", "")).lower().endswith(".exe"):
            return asset
    return {}


def parse_release(data, current_version, platform_name=None, machine=None):
    """把 GitHub Release JSON 转成 ReleaseInfo；无更新或不是正式版返回 None"""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    version = normalize_version(tag or data.get("name") or "")
    if not version or not is_newer(version, current_version):
        return None
    installer = pick_installer(data.get("assets") or [],
                               platform_name=platform_name, machine=machine)
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


def installer_name_for(version, platform_name=None, machine=None):
    """按发布约定推导安装包文件名（发行资产已统一命名，不含版本号）"""
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return f"sai-desktop-macos-{_machine_arch(machine)}.dmg"
    return "sai-desktop-win-x64.exe"


def installer_url_for(tag, name):
    """按发布约定推导安装包下载地址"""
    return (f"{RELEASES_PAGE}/download/{quote(str(tag), safe='')}/"
            f"{quote(str(name), safe='')}")


def html_to_text(source):
    """把 release 说明的 HTML 转成便于 Markdown 显示的纯文本"""
    text = str(source or "")
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?is)<h[1-6][^>]*>", "\n### ", text)
    text = re.sub(r"(?is)</(p|div|li|ul|ol|h[1-6]|tr|table|blockquote)>",
                  "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_atom(xml_text, current_version, platform_name=None, machine=None):
    """解析 releases.atom，取最新一条正式版；无更新返回 None。

    Atom 不提供资产大小与摘要，按 SAI-<版本>-<平台包名> 的发布约定推导下载地址，
    下载时跳过 SHA256 校验。
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except (ElementTree.ParseError, TypeError, ValueError):
        return None
    for entry in root.findall(f"{ATOM_NS}entry"):
        link = entry.find(f"{ATOM_NS}link")
        page_url = str(link.get("href") or "") if link is not None else ""
        tag = ""
        if "/releases/tag/" in page_url:
            tag = page_url.rsplit("/releases/tag/", 1)[-1]
        if not tag:
            identifier = str(entry.findtext(f"{ATOM_NS}id") or "")
            tag = identifier.rsplit("/", 1)[-1] if "/" in identifier else identifier
        tag = tag.strip()
        version = normalize_version(tag)
        # Atom 里可能混入预发布版本，带后缀的一律跳过
        if not version or "-" in version or not is_newer(version, current_version):
            continue
        name = str(entry.findtext(f"{ATOM_NS}title") or tag).strip()
        installer = installer_name_for(version, platform_name, machine)
        return ReleaseInfo(
            version=version,
            tag=tag,
            name=name,
            notes=html_to_text(entry.findtext(f"{ATOM_NS}content") or ""),
            page_url=page_url or RELEASES_PAGE,
            installer_name=installer,
            installer_url=installer_url_for(tag, installer),
            published_at=str(entry.findtext(f"{ATOM_NS}updated") or "").strip(),
        )
    return None


def fetch_latest_release(current_version, session=None, api_url=None,
                         timeout=REQUEST_TIMEOUT, atom_url=None,
                         platform_name=None, machine=None):
    """查询最新正式版；没有更新返回 None。

    优先 GitHub API（SAI_UPDATE_API 可覆盖），被限流（403）或网络异常时
    回退 releases.atom（SAI_UPDATE_ATOM 可覆盖），两者都失败才报错。
    platform_name / machine 决定挑选哪个平台的安装包（默认当前系统）。
    """
    session = session or requests.Session()
    # GitHub 在部分网络环境下需要走系统代理
    session.trust_env = True
    url = api_url or os.environ.get("SAI_UPDATE_API") or API_LATEST
    api_error = None
    try:
        response = session.get(url, timeout=timeout, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        })
        response.raise_for_status()
        return parse_release(response.json(), current_version,
                             platform_name=platform_name, machine=machine)
    except (requests.RequestException, ValueError, TypeError) as error:
        api_error = error
    feed = atom_url or os.environ.get("SAI_UPDATE_ATOM") or ATOM_LATEST
    try:
        response = session.get(feed, timeout=timeout, headers={
            "Accept": "application/atom+xml",
            "User-Agent": USER_AGENT,
        })
        response.raise_for_status()
        return parse_atom(response.text, current_version,
                          platform_name=platform_name, machine=machine)
    except (requests.RequestException, ValueError, TypeError) as error:
        raise RuntimeError(
            f"GitHub API 失败（{api_error}），releases.atom 也失败（{error}）"
        ) from error


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


def should_check(state, now=None, interval=CHECK_INTERVAL,
                 attempt_interval=ATTEMPT_INTERVAL):
    """距离上次检查是否已超过间隔；刚失败过的短时间内不再重试"""
    current = now if now is not None else time.time()
    attempt = state.get("last_attempt")
    if isinstance(attempt, (int, float)) and current - attempt < attempt_interval:
        return False
    last = state.get("last_check")
    if not isinstance(last, (int, float)):
        return True
    return current - last >= interval


def mark_checked(state, version="", now=None):
    """记录本次检查时间与看到的版本"""
    state["last_check"] = now if now is not None else time.time()
    state.pop("last_attempt", None)
    if version:
        state["last_seen_version"] = version
    return state


def mark_attempted(state, now=None):
    """记录一次失败的检查，短时间内不重复请求（成功后由 mark_checked 清除）"""
    state["last_attempt"] = now if now is not None else time.time()
    return state


def is_skipped(state, version):
    """该版本是否被用户跳过"""
    return bool(version) and state.get("skipped_version") == version


def installer_path(release, directory=None):
    directory = Path(directory) if directory is not None else UPDATES_DIR
    name = release.installer_name or installer_name_for(release.version)
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


def macos_app_bundle(executable=None):
    """从可执行文件路径推导 .app 包路径；不在 .app 内时返回 None"""
    executable = Path(executable or sys.executable).resolve()
    for parent in executable.parents:
        if parent.name.endswith(".app"):
            return parent
    return None


def prepare_macos_update(installer, directory=None):
    """解压升级包里的 SAI.app，返回解压后的应用路径。

    zip 包用 ditto 解压；dmg 包先挂载再拷贝，最后自动卸载。
    """
    installer = Path(installer).resolve()
    if not installer.is_file():
        raise RuntimeError(f"升级包不存在：{installer}")
    directory = Path(directory) if directory is not None else UPDATES_DIR
    target_dir = directory / f"extract-{installer.stem}"
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = installer.suffix.lower()
    if suffix == ".zip":
        subprocess.run(["ditto", "-x", "-k", str(installer), str(target_dir)],
                       check=True)
    elif suffix == ".dmg":
        mount = target_dir / "mount"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", str(installer), "-nobrowse",
                        "-mountpoint", str(mount)], check=True)
        try:
            bundles = sorted(mount.glob("*.app"))
            if not bundles:
                raise RuntimeError("安装包里没有找到 SAI.app")
            subprocess.run(
                ["ditto", str(bundles[0]), str(target_dir / bundles[0].name)],
                check=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-quiet"],
                           check=False)
    else:
        raise RuntimeError(f"不支持的升级包格式：{installer.name}")
    app = target_dir / "SAI.app"
    if not app.is_dir():
        raise RuntimeError("升级包里没有找到 SAI.app")
    return app


def create_macos_update_script(new_app, app_bundle, log_file=None,
                               directory=None, pid=None, delay=3):
    """生成 macOS 升级脚本，返回脚本路径。

    脚本等待 SAI 退出后用 ditto 原地替换 .app；替换失败自动回滚，
    完成后重新打开新版本。
    """
    new_app = Path(new_app).resolve()
    app_bundle = Path(app_bundle).resolve()
    if not new_app.is_dir():
        raise RuntimeError(f"新版本应用不存在：{new_app}")
    if not app_bundle.is_dir() or not app_bundle.name.endswith(".app"):
        raise RuntimeError(f"当前应用包路径无效：{app_bundle}")
    directory = Path(directory) if directory is not None else UPDATES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "install-update.sh"
    lines = ["#!/bin/sh"]
    if log_file is not None:
        lines.append(f'exec >>"{Path(log_file).resolve()}" 2>&1')
    lines.append('echo "== $(date) 开始更新 =="')
    wait_lines = []
    if pid:
        wait_lines = [
            "i=0",
            'while [ "$i" -lt 120 ]; do',
            f"  if ! kill -0 {int(pid)} 2>/dev/null; then break; fi",
            "  sleep 1",
            "  i=$((i + 1))",
            "done",
        ]
    wait_lines.append(f"sleep {max(1, int(delay))}")
    lines += wait_lines + [
        f'OLD="{app_bundle}"',
        f'NEW="{new_app}"',
        'BACKUP="${OLD}.old"',
        'rm -rf "$BACKUP"',
        'if ! mv "$OLD" "$BACKUP"; then',
        '  echo "无法替换旧版本，可能没有写入权限：$OLD"',
        '  open -R "$NEW"',
        "  exit 1",
        "fi",
        'if ! ditto "$NEW" "$OLD"; then',
        "  echo \"复制新版本失败，正在回滚\"",
        '  rm -rf "$OLD"',
        '  mv "$BACKUP" "$OLD"',
        '  open "$OLD"',
        "  exit 1",
        "fi",
        'rm -rf "$BACKUP"',
        'xattr -dr com.apple.quarantine "$OLD" >/dev/null 2>&1',
        'open "$OLD"',
        'rm -f "$0"',
    ]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script
