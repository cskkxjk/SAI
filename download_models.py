"""Download only runtime model files from ModelScope, without a proxy."""

import argparse
from pathlib import Path
from core.model_download import DOWNLOADS, download_model

ROOT = Path(__file__).resolve().parent
def download(name, quantization="q5_k"):
    download_model(name, ROOT, lambda label, done, total: print(
        f"{label}: {done / 1048576:.1f}/{total / 1048576:.1f} MiB", flush=True),
        quantization=quantization)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=[*DOWNLOADS, "all"])
    parser.add_argument("--quantization", choices=("q5_k", "q4_k"), default="q5_k")
    args = parser.parse_args()
    selected = ("qwen_asr", "fun_asr_nano", "sensevoice", "paraformer") if args.model == "all" else [args.model]
    for name in selected:
        download(name, args.quantization)
