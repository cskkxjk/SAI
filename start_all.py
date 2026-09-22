# coding: utf-8
"""Start the SAI server and client together."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
children: list[subprocess.Popen] = []


def stop_all(*_args):
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for child in reversed(children):
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()


def main() -> int:
    os.chdir(ROOT)
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    for entry in ("start_server.py", "start_client.py"):
        children.append(subprocess.Popen([sys.executable, str(ROOT / entry)], cwd=ROOT, **kwargs))
        time.sleep(1)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    try:
        while all(child.poll() is None for child in children):
            time.sleep(1)
    finally:
        stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
