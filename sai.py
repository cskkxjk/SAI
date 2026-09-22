"""Unified desktop entry point; child roles reuse the same executable."""

import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def prepare_streams(root, role):
    if sys.stdin is None:
        sys.stdin = open(os.devnull, encoding="utf-8")
    if sys.stdout is None:
        log_dir = root / "logs"
        log_dir.mkdir(exist_ok=True)
        sys.stdout = open(log_dir / f"{role}_bootstrap.log", "a",
                          encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout


def dropped_files(arguments):
    """Files dragged onto the executable, used for batch transcription."""
    return [arg for arg in arguments
            if not arg.startswith("-") and Path(arg).is_file()]


def transcription_request(arguments):
    """Dropped media files transcribe instead of opening the launcher."""
    if "--self-test" in arguments or any(a in ("--server", "--client") for a in arguments):
        return []
    return dropped_files(arguments)


def show_transcription_error():
    """Explain a failed drag-and-drop transcription; there is no console."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(
            "SAI 文件转录",
            "无法连接识别服务，文件没有转录。\n\n"
            "请先启动 SAI（双击托盘图标，确认状态为运行中），"
            "再把文件拖到 SAI.exe 上；详细日志见数据目录的 logs。",
        )
        root.destroy()
    except Exception:
        pass


def main():
    from core.runtime_paths import DATA_DIR, initialize_user_data
    initialize_user_data()
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    os.chdir(DATA_DIR)
    role = next((r for r in ("server", "client") if f"--{r}" in sys.argv), None)
    transcription = False
    if role is None and transcription_request(sys.argv[1:]):
        # Dragging files onto the executable transcribes them through the client.
        role = "client"
        transcription = True
    # Spawned workers enter freeze_support before the normal role dispatch.
    # Give them real streams so exceptions don't open invisible error dialogs.
    prepare_streams(DATA_DIR, role or os.environ.get("SAI_ROLE", "desktop"))
    multiprocessing.freeze_support()
    if "--self-test" in sys.argv:
        from core.desktop_selftest import run
        return run()
    if role:
        os.environ["SAI_ROLE"] = role
        if f"--{role}" in sys.argv:
            sys.argv.remove(f"--{role}")
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        try:
            if role == "server":
                from core.server.app import SaiServer
                return SaiServer().start()
            else:
                from core.client.app import SaiClient
                code = SaiClient().start()
        except Exception:
            traceback.print_exc()
            code = 1
        if transcription and code:
            show_transcription_error()
        return code
    else:
        from gui_launcher import Launcher, activate_existing
        if not activate_existing():
            Launcher().mainloop()


if __name__ == "__main__":
    sys.exit(main())
