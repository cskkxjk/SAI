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
  # 仓库根目录 config_gui.json 是开发者的本机配置（Windows 可能与 macOS 不兼容），
  # 这里修正 app 内配置：GGUF 模型改 paraformer，缺省快捷键用右 Option（alt_r）。
  local cfg="$dest/config_gui.json"
  if [ -f "$cfg" ] && [ -x /usr/bin/python3 ]; then
    /usr/bin/python3 - "$cfg" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
if data.get("model_type") in ("fun_asr_nano", "qwen_asr"):
    data["model_type"] = "paraformer"
if not data.get("shortcuts"):
    data["shortcuts"] = [{"key": "alt_r", "type": "keyboard",
                          "suppress": True, "hold_mode": True, "enabled": True}]
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY
  fi
  # 关键：存在 canonical 语法标记 -> 运行期数据写到用户数据目录
  touch "$dest/installed.flag"
}

# ---- onedir（dist/SAI）----
if [ -d "$ROOT/dist/SAI" ]; then
  copy_payload "$ROOT/dist/SAI"
  echo "已整理 onedir：$ROOT/dist/SAI"
fi

# ---- .app ----
APP="$ROOT/dist/SAI.app"
if [ -d "$APP" ]; then
  MACOS="$APP/Contents/MacOS"
  copy_payload "$MACOS"
  # 模型不放进 app 包（macOS 上模型位于用户数据目录，首次运行在界面下载），
  # 这样分发的是瘦身包，且不会因写入 .app 破坏代码签名。
  codesign --force --deep --sign - "$APP"
  echo "已整理并签名：$APP"
fi

echo "完成。运行：open \"$APP\""
