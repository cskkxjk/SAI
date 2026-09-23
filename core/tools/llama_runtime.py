# coding: utf-8
"""
llama.cpp 运行库定位与体检工具

纯函数模块：不加载 DLL、不修改 PATH 或工作目录，供 GGUF 引擎与 GUI 复用。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

RUNTIME_VERSION = "b10621"
ENV_BIN = "SAI_LLAMA_BIN"
ENV_URL = "SAI_LLAMA_RUNTIME_URL"
ENV_SHA256 = "SAI_LLAMA_RUNTIME_SHA256"

RUNTIME_ARCHIVE = "llama-b10621-bin-win-vulkan-x64.zip"
RUNTIME_SHA256 = "2672d85bf87c8280d94dee01eb6a86280046878f70a07d786a93637fa9081163"
RELEASE_URL = ("https://github.com/ggml-org/llama.cpp/releases/download/"
               "{tag}/{archive}")

MANIFEST_NAME = "runtime-version.json"
CORE_LIB_COUNT = 3


class LlamaRuntimeError(RuntimeError):
    """llama.cpp 运行库缺失或无法加载"""


def lib_names() -> tuple:
    """按 (ggml, ggml-base, llama) 顺序返回当前平台的库文件名"""
    if sys.platform == "win32":
        return ("ggml.dll", "ggml-base.dll", "llama.dll")
    if sys.platform == "darwin":
        return ("libggml.dylib", "libggml-base.dylib", "libllama.dylib")
    return ("libggml.so", "libggml-base.so", "libllama.so")


def main_lib_name() -> str:
    """用于判定目录是否可用的主库文件名"""
    return lib_names()[2]


def runtime_platform_supported() -> bool:
    """官方二进制包目前只覆盖 Windows x64"""
    return sys.platform == "win32"


def candidate_dirs(base_dir) -> list:
    """按优先级返回候选运行库目录（环境变量可覆盖默认位置）"""
    directories = []
    override = os.environ.get(ENV_BIN)
    if override:
        directories.append(Path(override))
    directories.append(Path(base_dir) / "bin")
    return directories


def missing_runtime_message(base_dir) -> str:
    """运行库缺失时的可读说明"""
    checked = "、".join(str(d) for d in candidate_dirs(base_dir))
    return (
        f"llama.cpp 运行库缺失：未找到 {main_lib_name()}（已查找 {checked}）。"
        "安装包应自带该目录；若被杀毒软件隔离或删除，请把安装目录加入白名单后重新安装 SAI。"
        f"也可从 llama.cpp {RUNTIME_VERSION} 发布包解压 DLL 到 core/server/engines/llama/bin，"
        f"或用环境变量 {ENV_BIN} 指定目录。"
    )


def load_failure_hint() -> str:
    """DLL 加载失败时的补充说明（常见于杀毒软件破坏文件）"""
    return (
        "若提示“损坏的映像”或“没有被指定在 Windows 上运行”，"
        "通常是杀毒软件隔离或文件不完整：请把安装目录加入白名单后重新安装 SAI。"
    )


def resolve_llama_bin(base_dir) -> Path:
    """返回包含运行库的目录，找不到时抛出带说明的 LlamaRuntimeError"""
    for directory in candidate_dirs(base_dir):
        if (directory / main_lib_name()).is_file():
            return directory
    raise LlamaRuntimeError(missing_runtime_message(base_dir))


def check_llama_bin(base_dir) -> Optional[Path]:
    """可用时返回目录，否则返回 None（不抛异常）"""
    try:
        return resolve_llama_bin(base_dir)
    except LlamaRuntimeError:
        return None


def read_manifest(base_dir) -> dict:
    """读取 bin/runtime-version.json，缺失或损坏时返回空字典"""
    for directory in candidate_dirs(base_dir):
        path = directory / MANIFEST_NAME
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def runtime_tag(base_dir) -> str:
    return str(read_manifest(base_dir).get("tag") or RUNTIME_VERSION)


def runtime_archive(base_dir) -> str:
    return str(read_manifest(base_dir).get("archive") or RUNTIME_ARCHIVE)


def runtime_digest(base_dir) -> str:
    return str(read_manifest(base_dir).get("sha256") or RUNTIME_SHA256).lower()


def runtime_download_url(base_dir) -> str:
    """下载地址：环境变量 > 清单 url 字段 > llama.cpp 官方 release"""
    override = os.environ.get(ENV_URL)
    if override:
        return override
    manifest = read_manifest(base_dir)
    if manifest.get("url"):
        return str(manifest["url"])
    return RELEASE_URL.format(tag=runtime_tag(base_dir), archive=runtime_archive(base_dir))


def runtime_download_digest(base_dir) -> str:
    """期望的压缩包 SHA256；环境变量可覆盖（自建镜像时使用）"""
    override = os.environ.get(ENV_SHA256)
    if override:
        return override.lower()
    return runtime_digest(base_dir)


def expected_files(base_dir) -> Tuple[str, ...]:
    """期望存在的运行库文件；清单未登记时退化为核心库"""
    manifest = read_manifest(base_dir)
    files = manifest.get("files")
    if isinstance(files, list) and files:
        return tuple(str(name) for name in files)
    if sys.platform == "win32":
        return ("ggml-vulkan.dll", *lib_names())
    return lib_names()


def _is_corrupt(path: Path) -> bool:
    """体积为 0 或缺少 PE 头（MZ）视为损坏，能识别杀毒软件破坏的 DLL"""
    try:
        if path.stat().st_size < 1024:
            return True
        with path.open("rb") as handle:
            return handle.read(2) != b"MZ"
    except OSError:
        return True


@dataclass(frozen=True)
class RuntimeReport:
    """运行库体检结果"""

    directory: Optional[Path] = None
    tag: str = ""
    expected: int = 0
    missing: Tuple[str, ...] = ()
    corrupt: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    core_missing: Tuple[str, ...] = ()
    files: Tuple[str, ...] = field(default=(), compare=False)

    @property
    def fatal(self) -> bool:
        """服务端必然启动失败的情形"""
        return (self.directory is None or bool(self.core_missing)
                or bool(self.corrupt))

    @property
    def needs_repair(self) -> bool:
        return self.fatal or bool(self.missing)

    @property
    def summary(self) -> str:
        if self.directory is None:
            return f"未安装（缺少 {main_lib_name()}）"
        if self.fatal:
            parts = []
            if self.core_missing:
                parts.append("缺少 " + "、".join(self.core_missing[:3]))
            if self.corrupt:
                parts.append("损坏 " + "、".join(self.corrupt[:3]))
            return "运行库异常：" + "；".join(parts)
        if self.missing:
            return f"运行库不完整：缺少 {len(self.missing)} 个文件"
        return f"正常（llama.cpp {self.tag}，{len(self.files)} 个文件）"

    @property
    def problems(self) -> Tuple[str, ...]:
        items = []
        if self.directory is None:
            items.append(f"未找到运行库目录（缺少 {main_lib_name()}）")
        if self.core_missing:
            items.append("缺少核心库：" + "、".join(self.core_missing))
        if self.corrupt:
            items.append("DLL 已损坏（多被杀毒软件破坏）：" + "、".join(self.corrupt))
        if self.missing:
            items.append("清单内文件缺失：" + "、".join(self.missing[:6])
                         + ("…" if len(self.missing) > 6 else ""))
        items.extend(self.warnings)
        return tuple(items)


def verify_llama_runtime(base_dir) -> RuntimeReport:
    """检查运行库目录：核心库缺失与 DLL 损坏为致命问题，其余缺失为可修复项"""
    directory = check_llama_bin(base_dir)
    tag = runtime_tag(base_dir)
    expected = expected_files(base_dir)
    warnings = []
    if directory is None:
        return RuntimeReport(directory=None, tag=tag, expected=len(expected))
    present = {path.name for path in directory.glob("*") if path.is_file()}
    missing = tuple(name for name in expected if name not in present)
    core_missing = tuple(name for name in lib_names() if name not in present)
    corrupt = tuple(sorted(path.name for path in directory.glob("*.dll")
                           if _is_corrupt(path)))
    if tag != RUNTIME_VERSION:
        warnings.append(f"运行库版本为 {tag}，程序按 {RUNTIME_VERSION} 构建；"
                        "如无异常可忽略，异常时可点“修复运行库”还原。")
    return RuntimeReport(directory=directory, tag=tag, expected=len(expected),
                         missing=missing, corrupt=corrupt, warnings=tuple(warnings),
                         core_missing=core_missing,
                         files=tuple(sorted(name for name in present
                                            if name.lower().endswith(".dll"))))
