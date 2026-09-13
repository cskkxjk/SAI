#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if grep -qi microsoft /proc/version 2>/dev/null; then
    echo "WSL detected: starting the Linux client/server in this WSL environment."
    echo "For a Windows-wide hotkey in Windows applications, run start_all.bat on Windows instead."
fi
exec python3 start_all.py
