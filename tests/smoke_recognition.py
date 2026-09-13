"""Send a local WAV to an already running server and require a transcript."""

import argparse
import base64
import json
import math
import time
import uuid

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly
from websockets.sync.client import connect


def transcribe(path):
    rate, data = wavfile.read(path)
    if data.dtype == np.int16:
        audio = data.astype(np.float32) / 32768
    else:
        audio = data.astype(np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    gcd = math.gcd(rate, 16000)
    audio = resample_poly(audio, 16000 // gcd, rate // gcd).astype(np.float32)
    with connect("ws://127.0.0.1:6016", subprotocols=["binary"], proxy=None) as ws:
        ws.send(json.dumps({
            "task_id": str(uuid.uuid4()), "source": "mic",
            "data": base64.b64encode(audio.tobytes()).decode(),
            "is_final": True, "time_start": time.time(),
            "language": "auto",
        }))
        result = json.loads(ws.recv(timeout=60))
    print(json.dumps(result, ensure_ascii=False))
    assert result["is_final"] and result["text"].strip(), result
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    transcribe(parser.parse_args().wav)
