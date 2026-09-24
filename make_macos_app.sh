#!/bin/zsh
# 将 SAI 打包产物整理为可运行的 macOS 应用。
#
# 前置：先运行
#   .venv/bin/python -m PyInstaller --noconfirm --distpath dist build-macos.spec
#
# 本脚本做三件事：
#   1. 把 core/、LLM/、config_*.py、hot*.txt、assets 复制到可执行文件旁
#   2. 放入 installed.flag，使运行期配置/日志/录音写到
#      ~/Library/Application Support/SAI，而不是修改 app 包（避免破坏签名）
#   3. 重新 ad-hoc 签名 .app（因为修改过 bundle 内容）
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"

copy_payload() {
  local dest="$1"
  mkdir -p "$dest"
  for f in config_client.py config_server.py config_gui.json \
           hot.txt hot-server.txt hot-rule.txt readme.md LICENSE; do
    [ -f "$ROOT/$f" ] && cp -f "$ROOT/$f" "$dest/$f"
  done
  for d in core LLM; do
    rsync -a --delete \
      --exclude '__pycache__' --exclude '*.pyc' --exclude '*.bak' \
      --exclude 'export' --exclude 'logs' \
      "$ROOT/$d/" "$dest/$d/"
  done
  mkdir -p "$dest/assets"
  for img in icon.png icon-recording.png icon.icns; do
    [ -f "$ROOT/assets/$img" ] && cp -f "$ROOT/assets/$img" "$dest/assets/$img"
  done
  # 关键：存在 canonical 语法标记 -> 运行期数据写到用户数据目录
  touch "$dest/installed.flag"
}

# ---- onedir（dist/SAI）----
if [ -d "$ROOT/dist/SAI" ]; then
  copy_payload "$ROOT/dist/SAI"
  if [ -d "$ROOT/models" ] && [ ! -e "$ROOT/dist/SAI/models" ]; then
    ln -s "$ROOT/models" "$ROOT/dist/SAI/models"
  fi
  echo "已整理 onedir：$ROOT/dist/SAI"
fi

# ---- .app ----
APP="$ROOT/dist/SAI.app"
if [ -d "$APP" ]; then
  MACOS="$APP/Contents/MacOS"
  copy_payload "$MACOS"
  if [ -d "$ROOT/models" ]; then
    mkdir -p "$MACOS/models"
    rsync -a --exclude '__pycache__' "$ROOT/models/" "$MACOS/models/"
  fi
  codesign --force --deep --sign - "$APP"
  echo "已整理并签名：$APP"
fi

echo "完成。运行：open \"$APP\""
