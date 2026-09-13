"""Download only runtime model files from ModelScope, without a proxy."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from shutil import copy2

ROOT = Path(__file__).resolve().parent
DOWNLOADS = {
    "qwen_asr": (
        "wen14778591/Qwen3-ASR-1.7B-GGUF-Hybrid",
        "models/Qwen3-ASR/Qwen3-ASR-1.7B",
        {
            "qwen3_asr_encoder_frontend.int4.onnx": "qwen3_asr_encoder_frontend.onnx",
            "qwen3_asr_encoder_backend.int4.onnx": "qwen3_asr_encoder_backend.onnx",
            "qwen3_asr_llm.q5_k.gguf": "qwen3_asr_llm.gguf",
        },
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
def download(name):
    os.environ["NO_PROXY"] = "*"
    from modelscope.hub.api import HubApi
    from modelscope.hub.file_download import model_file_download
    repo, destination, files = DOWNLOADS[name]
    metadata = {f["Path"]: f for f in HubApi().get_model_files(repo)}
    record = {"repository": repo, "files": []}
    for source, target_name in files.items():
        target = ROOT / destination / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        info = metadata[source]
        expected = info.get("Sha256")
        valid = target.exists() and target.stat().st_size == info["Size"]
        if valid and expected:
            with target.open("rb") as stream:
                valid = hashlib.file_digest(stream, "sha256").hexdigest() == expected
        if not valid:
            cached = model_file_download(repo, source, cache_dir=str(ROOT / "downloads"))
            copy2(cached, target)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if target.stat().st_size != info["Size"] or expected and digest != expected:
            raise RuntimeError(f"Model checksum mismatch: {target}")
        record["files"].append({"source": source, "target": target_name,
                                "bytes": target.stat().st_size, "sha256": digest})
        print(f"Verified {name}: {target_name} ({target.stat().st_size} bytes)", flush=True)
    (ROOT / destination / "model-source.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=[*DOWNLOADS, "all"])
    selected = list(DOWNLOADS) if parser.parse_args().model == "all" else [parser.parse_args().model]
    for name in selected:
        download(name)
