"""ModelScope runtime downloads, isolated from GUI and proxy environment."""
import hashlib
import re
import shutil
import sys
import tarfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

import requests


RUNTIME_DOWNLOAD_TIMEOUT = (15, 60)


DOWNLOADS = {
    "qwen_asr": (
        "wen14778591/Qwen3-ASR-1.7B-GGUF-Hybrid",
        "models/Qwen3-ASR/Qwen3-ASR-1.7B",
        {
            "qwen3_asr_encoder_frontend.int4.onnx": "qwen3_asr_encoder_frontend.onnx",
            "qwen3_asr_encoder_backend.int4.onnx": "qwen3_asr_encoder_backend.onnx",
            "qwen3_asr_llm.q5_k.gguf": "qwen3_asr_llm.q5_k.gguf",
        },
    ),
    "fun_asr_nano": (
        "HaujetZhao/Fun-ASR-Nano-2512-GGUF",
        "models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model",
        {f"model/{name}": name for name in (
            "Fun-ASR-Nano-Encoder-Adaptor.fp32.onnx", "Fun-ASR-Nano-CTC.int8.onnx",
            "Fun-ASR-Nano-Decoder.q8_0.gguf", "tokens.txt")},
    ),
    "sensevoice": (
        "pengzhendong/sherpa-onnx-sense-voice-zh-en-ja-ko-yue",
        "models/SenseVoice-Small/Sherpa-ONNX",
        {"model.int8.onnx": "model.int8.onnx", "tokens.txt": "tokens.txt"},
    ),
    "paraformer": (
        "pengzhendong/sherpa-onnx-paraformer-zh",
        "models/Paraformer/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx",
        {"model.int8.onnx": "model.onnx", "tokens.txt": "tokens.txt"},
    ),
    "punc": (
        "csukuangfj/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12",
        "models/Punct-CT-Transformer/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12",
        {"model.onnx": "model.onnx"},
    ),
}


class DownloadCancelled(Exception):
    pass


QWEN_QUANTIZATIONS = ("q5_k", "q4_k")


def model_spec(name, quantization="q5_k"):
    if quantization not in QWEN_QUANTIZATIONS:
        raise ValueError("Unsupported Qwen quantization")
    repo, directory, files = DOWNLOADS[name]
    if name == "qwen_asr":
        files = dict(files)
        del files["qwen3_asr_llm.q5_k.gguf"]
        filename = f"qwen3_asr_llm.{quantization}.gguf"
        files[filename] = filename
    return repo, directory, files


def components(name):
    return (name, "punc") if name in ("sensevoice", "paraformer") else (name,)


def model_files(name, quantization="q5_k"):
    if name == "openai_api":
        return ()
    return tuple(f"{model_spec(key, quantization)[1]}/{target}"
                 for key in components(name) for target in model_spec(key, quantization)[2].values())


def missing_files(root, name, quantization="q5_k"):
    return [relative for relative in model_files(name, quantization)
            if not (Path(root) / relative).is_file()
            or (Path(root) / relative).stat().st_size == 0]


def _check_cancel(cancel):
    if cancel.is_set():
        raise DownloadCancelled()


def _digest(path, cancel):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while data := source.read(1024 * 1024):
            _check_cancel(cancel)
            digest.update(data)
    return digest.hexdigest()


def fetch_manifest(session, name, quantization="q5_k"):
    """Resolve file sizes, checksums and immutable revisions before writing."""
    result = []
    for key in components(name):
        repo, directory, files = model_spec(key, quantization)
        with session.get(f"https://modelscope.cn/api/v1/models/{repo}/repo/files",
                         params={"Revision": "master", "Recursive": "true"},
                         timeout=(10, 30)) as response:
            response.raise_for_status()
            body = response.json()
        if not body.get("Success"):
            raise RuntimeError(f"无法读取 ModelScope 模型清单：{repo}")
        entries = {entry["Path"]: entry for entry in body["Data"]["Files"]}
        for source, target in files.items():
            entry = entries.get(source, {})
            digest = entry.get("Sha256", "")
            size = entry.get("Size", 0)
            revision = entry.get("Revision", "")
            if (not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
                    or not isinstance(size, int) or size <= 0 or not revision):
                raise RuntimeError(f"ModelScope 文件或校验信息缺失：{repo}/{source}")
            result.append(dict(repo=repo, source=source, relative=f"{directory}/{target}",
                               size=size, digest=digest.lower(), revision=revision))
    return result


def download_model(name, root, progress=lambda *args: None, cancel=None, quantization="q5_k"):
    """progress(label, completed_bytes, total_bytes); existing files are verified."""
    cancel = cancel or threading.Event()
    root = Path(root).resolve()
    with requests.Session() as session:
        session.trust_env = False
        progress("正在读取 ModelScope 文件清单", 0, 0)
        _check_cancel(cancel)
        manifest = fetch_manifest(session, name, quantization)
        total = sum(item["size"] for item in manifest)
        completed = 0
        for item in manifest:
            _check_cancel(cancel)
            target = (root / item["relative"]).resolve()
            if not target.is_relative_to(root / "models"):
                raise RuntimeError("模型目录指向安装目录外，拒绝写入")
            target.parent.mkdir(parents=True, exist_ok=True)
            progress(f"正在校验 {target.name}", completed, total)
            # Old releases removed the quantization suffix; reuse only after hash verification.
            legacy = target.with_name("qwen3_asr_llm.gguf")
            if (name == "qwen_asr" and target.suffix == ".gguf" and not target.exists()
                    and legacy.is_file() and legacy.stat().st_size == item["size"]
                    and _digest(legacy, cancel) == item["digest"]):
                legacy.replace(target)
            if (target.is_file() and target.stat().st_size == item["size"]
                    and _digest(target, cancel) == item["digest"]):
                completed += item["size"]
                progress(f"已存在 {target.name}", completed, total)
                continue
            if shutil.disk_usage(target.parent).free < item["size"] + 16 * 1024 * 1024:
                raise OSError("安装目录所在磁盘空间不足")
            temporary = target.with_name(target.name + ".part")
            try:
                with session.get(
                    f"https://modelscope.cn/api/v1/models/{item['repo']}/repo",
                    params={"Revision": item["revision"], "FilePath": item["source"]},
                    stream=True, timeout=(10, 30),
                ) as response:
                    response.raise_for_status()
                    digest = hashlib.sha256()
                    received = 0
                    with temporary.open("wb") as output:
                        for data in response.iter_content(1024 * 1024):
                            _check_cancel(cancel)
                            received += len(data)
                            if received > item["size"]:
                                raise RuntimeError(f"模型大小异常：{target.name}")
                            output.write(data)
                            digest.update(data)
                            progress(target.name, completed + received, total)
                _check_cancel(cancel)
                if received != item["size"] or digest.hexdigest() != item["digest"]:
                    raise RuntimeError(f"模型校验失败，请重试：{target.name}")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            completed += item["size"]
        progress("下载并校验完成", total, total)


def install_llama_runtime(archive, bin_dir, cancel=None, suffix=None):
    """把运行库压缩包里的动态库解压到 bin_dir（扁平覆盖），返回写入的文件数。

    Windows 官方包是 zip（只取 DLL）；macOS 官方包是 tar.gz（只取 dylib，
    保留符号链接，跳过命令行工具与文档）。
    """
    cancel = cancel or threading.Event()
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    if suffix is None:
        suffix = ".dylib" if sys.platform == "darwin" else ".dll"
    archive = Path(archive)
    count = 0
    if archive.name.lower().endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as bundle:
            members = []
            for entry in bundle.getmembers():
                name = PurePosixPath(entry.name.replace("\\", "/")).name
                if entry.isdir() or not name.lower().endswith(suffix):
                    continue
                entry.name = name
                members.append(entry)
            for entry in members:
                _check_cancel(cancel)
                try:
                    bundle.extract(entry, path=bin_dir, filter="data",
                                   set_attrs=False)
                except (tarfile.TarError, OSError) as error:
                    raise RuntimeError(f"运行库解压失败：{error}") from error
                count += 1
    else:
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                name = PurePosixPath(entry.filename.replace("\\", "/")).name
                if entry.is_dir() or not name.lower().endswith(suffix):
                    continue
                _check_cancel(cancel)
                with bundle.open(entry) as source, (bin_dir / name).open("wb") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                count += 1
    if not count:
        raise RuntimeError("运行库压缩包里没有动态库文件")
    return count


def _manual_runtime_hint(url, bin_dir, error):
    from core.tools import llama_runtime

    return (f"运行库下载失败：{error}\n"
            f"可手动下载 {url} 并解压其中的动态库到 {bin_dir}，"
            f"或设置环境变量 {llama_runtime.ENV_URL} 指向镜像地址。")


def download_llama_runtime(root, progress=lambda *args: None, cancel=None,
                           base_dir=None):
    """下载并安装 llama.cpp 运行库，返回可展示的完成消息。

    base_dir 为 None 时用标准引擎目录（<root>/core/server/engines/llama）；
    macOS 装机版由调用方传入可写的用户数据目录，避免写入 .app 破坏签名。
    """
    from core.tools import llama_runtime

    cancel = cancel or threading.Event()
    root = Path(root).resolve()
    if base_dir is None:
        base_dir = root / "core" / "server" / "engines" / "llama"
    base_dir = Path(base_dir)
    bin_dir = base_dir / "bin"
    if not llama_runtime.runtime_platform_supported():
        raise RuntimeError("当前系统暂不支持自动下载运行库，请手动安装 llama.cpp")
    url = llama_runtime.runtime_download_url(base_dir)
    digest = llama_runtime.runtime_download_digest(base_dir)
    tag = llama_runtime.runtime_tag(base_dir)
    free_space = shutil.disk_usage(base_dir if base_dir.exists() else root).free
    if free_space < 256 * 1024 * 1024:
        raise OSError("安装目录所在磁盘空间不足")
    base_dir.mkdir(parents=True, exist_ok=True)
    progress("正在下载 llama.cpp 运行库", 0, 0)
    _check_cancel(cancel)
    temporary = base_dir / ".runtime-download.part"
    try:
        with requests.Session() as session:
            # GitHub 在部分网络环境下需要走系统代理
            session.trust_env = True
            try:
                response = session.get(url, stream=True,
                                       timeout=RUNTIME_DOWNLOAD_TIMEOUT,
                                       allow_redirects=True)
            except requests.RequestException as error:
                raise RuntimeError(_manual_runtime_hint(url, bin_dir, error)) from error
            with response:
                try:
                    response.raise_for_status()
                except requests.RequestException as error:
                    raise RuntimeError(_manual_runtime_hint(url, bin_dir, error)) from error
                total = int(response.headers.get("Content-Length") or 0)
                loader = hashlib.sha256()
                received = 0
                with temporary.open("wb") as output:
                    for data in response.iter_content(1024 * 1024):
                        _check_cancel(cancel)
                        received += len(data)
                        output.write(data)
                        loader.update(data)
                        progress("正在下载 llama.cpp 运行库", received, total)
        _check_cancel(cancel)
        if digest and loader.hexdigest() != digest:
            raise RuntimeError(f"运行库校验失败（SHA256 不匹配），请重试：{url}")
        count = install_llama_runtime(temporary, bin_dir, cancel)
    finally:
        temporary.unlink(missing_ok=True)
    report = llama_runtime.verify_llama_runtime(base_dir)
    if report.fatal:
        raise RuntimeError("运行库修复后仍异常：" + "；".join(report.problems))
    return f"运行库已修复（llama.cpp {tag}，{count} 个文件）"
