"""ModelScope runtime downloads, isolated from GUI and proxy environment."""
import hashlib
import re
import shutil
import threading
from pathlib import Path

import requests


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
