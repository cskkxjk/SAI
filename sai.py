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


def main():
    from core.runtime_paths import DATA_DIR, initialize_user_data
    initialize_user_data()
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    os.chdir(DATA_DIR)
    role = next((r for r in ("server", "client") if f"--{r}" in sys.argv), None)
    # Spawned workers enter freeze_support before the normal role dispatch.
    # Give them real streams so exceptions don't open invisible error dialogs.
    prepare_streams(DATA_DIR, role or os.environ.get("SAI_ROLE", "desktop"))
    multiprocessing.freeze_support()
    if "--self-test" in sys.argv:
        from core.desktop_selftest import run
        return run()
    if role:
        os.environ["SAI_ROLE"] = role
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
                return SaiClient().start()
        except Exception:
            traceback.print_exc()
            return 1
    else:
        from gui_launcher import Launcher, activate_existing
        if not activate_existing():
            Launcher().mainloop()


if __name__ == "__main__":
    sys.exit(main())
