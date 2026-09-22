"""Exercise the frozen server and restore the user's configuration afterwards."""

import argparse
import base64
import json
import math
import os
import re
from pathlib import Path
import socket
import subprocess
import time
import uuid
import wave

import numpy as np
from scipy.signal import resample_poly
from websockets.sync.client import connect


def recognize(audio_path):
    with wave.open(str(audio_path), "rb") as audio:
        rate = audio.getframerate()
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2, audio.getparams()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), "<i2")
    samples = samples.astype("<f4") / 32768
    divisor = math.gcd(rate, 16000)
    samples = resample_poly(samples, 16000 // divisor, rate // divisor)
    data = base64.b64encode(samples.astype("<f4").tobytes()).decode()
    with connect("ws://127.0.0.1:6016", proxy=None,
                 subprotocols=["binary"], max_size=None) as ws:
        ws.send(json.dumps({
            "task_id": str(uuid.uuid4()), "source": "mic", "data": data,
            "is_final": True, "time_start": time.time(),
            "language": "english",
        }))
        result = json.loads(ws.recv(timeout=90))
        assert result["is_final"] and result["text"].strip(), result
        return result["text"]


def run(root, audio_path, models, provider="AUTO", cpu_llm=False):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 6016)) == 0:
            raise RuntimeError("Stop the running ASR server before smoke testing.")
    config_path = root / "config_gui.json"
    original = config_path.read_bytes()
    config = json.loads(original)
    reports = []
    try:
        for model in models:
            config.update(model_type=model, onnx_provider=provider,
                          llm_use_gpu=not cpu_llm, child_tray=False)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            ready = root / "logs" / f".smoke-{uuid.uuid4().hex}"
            env = dict(os.environ, SAI_GUI="1",
                       SAI_READY_FILE=str(ready))
            process = subprocess.Popen(
                [str(root / "SAI.exe"), "--server"], cwd=root, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            started = time.monotonic()
            try:
                while not ready.exists():
                    if process.poll() is not None:
                        raise RuntimeError(f"Server exited: {process.returncode}")
                    if time.monotonic() - started > 180:
                        raise TimeoutError("Model startup timeout")
                    time.sleep(.25)
                text = recognize(audio_path)
                words = re.findall(r"[a-z]+", text.lower())
                expected = "hello this is a speech recognition test".split()
                assert words == expected, f"Unexpected transcript: {text!r}"
                reports.append({"model": model, "text": text,
                                "seconds": round(time.monotonic() - started, 2)})
                print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
            except Exception as exc:
                reports.append({"model": model, "error": repr(exc)})
                print(json.dumps(reports[-1]), flush=True)
            finally:
                if process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW, timeout=15,
                    )
                process.wait(timeout=10)
                ready.unlink(missing_ok=True)
                for name in ("server_latest.log", "server_bootstrap.log"):
                    log = root / "logs" / name
                    if log.exists():
                        (root / "logs" / f"smoke-{model}-{name}").write_bytes(log.read_bytes())
    finally:
        config_path.write_bytes(original)
    (root / "logs" / "desktop-smoke.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    return all("error" not in item for item in reports)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] /
                        "dist/SAI")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--provider", default="AUTO")
    parser.add_argument("--cpu-llm", action="store_true")
    parser.add_argument("models", nargs="*", default=[
        "fun_asr_nano", "qwen_asr", "sensevoice", "paraformer"])
    args = parser.parse_args()
    raise SystemExit(0 if run(args.root.resolve(), args.audio.resolve(), args.models,
                             args.provider, args.cpu_llm) else 1)
