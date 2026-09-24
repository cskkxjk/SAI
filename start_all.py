# coding: utf-8
"""同时启动 SAI 服务端与客户端。

- 若 6016 端口已有服务端在运行，则跳过启动服务端，直接启动客户端，
  避免端口冲突把两个进程一起带崩。
- Ctrl+C 会一并停止本次启动的子进程。
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
children: list[subprocess.Popen] = []


def stop_all(*_args):
    for child in reversed(children):
        if child.poll() is None:
            try:
                child.terminate()
            except Exception:
                pass
    for child in reversed(children):
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except Exception:
                pass


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main() -> int:
    os.chdir(ROOT)
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    # 读取端口配置（默认 6016）
    try:
        sys.path.insert(0, str(ROOT))
        from config_server import ServerConfig
        host, port = "127.0.0.1", int(ServerConfig.port)
    except Exception:
        host, port = "127.0.0.1", 6016

    server_started = False
    if _port_in_use(host, port):
        print(f"检测到服务端已在运行（{host}:{port}），跳过服务端，仅启动客户端。")
    else:
        print("正在启动服务端...")
        children.append(subprocess.Popen(
            [sys.executable, str(ROOT / "start_server.py")], cwd=ROOT, **kwargs))
        server_started = True
        # 等待服务端就绪
        for _ in range(60):
            if _port_in_use(host, port):
                break
            time.sleep(1)

    print("正在启动客户端...")
    children.append(subprocess.Popen(
        [sys.executable, str(ROOT / "start_client.py")], cwd=ROOT, **kwargs))

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    try:
        while all(child.poll() is None for child in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
