#!/bin/zsh
# 一键打出 macOS 发布包：
#   dist/SAI.app 以及 release/sai-desktop-macos-<架构>.zip / .dmg
# 用法： ./release_macos.sh            # 版本号取自 config_server.__version__
#        ./release_macos.sh 1.0.4      # 或手动指定版本
#
# 前置要求：
#   1. core/server/engines/llama/bin 内已放置 llama.cpp 运行库（libllama.dylib 等）
#   2. ./make_macos_app.sh 可用的构包环境（uv + PyInstaller）
set -euo pipefail
cd "$(dirname "$0")"

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
VER="${1:-}"

case "$(uname -m)" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64) ARCH="x64" ;;
  *) echo "不支持的 CPU 架构：$(uname -m)" >&2; exit 1 ;;
esac

if [ ! -f core/server/engines/llama/bin/libllama.dylib ]; then
  echo "缺少 llama.cpp 运行库：请先把 llama-b10621-bin-macos-${ARCH}.tar.gz" \
       "解压到 core/server/engines/llama/bin" >&2
  exit 1
fi

echo "== 同步打包依赖 =="
"$UV" sync --group build

if [ -z "$VER" ]; then
  VER="$(.venv/bin/python -c 'from config_server import __version__; print(__version__)')"
fi

echo "== PyInstaller 打包（版本 ${VER}，架构 ${ARCH}）=="
.venv/bin/python -m PyInstaller --noconfirm --distpath dist build-macos.spec

echo "== 整理 app（源码/资源、installed.flag、去模型、ad-hoc 重签名）=="
./make_macos_app.sh

echo "== 压缩发布包 =="
mkdir -p release
ZIP="release/sai-desktop-macos-${ARCH}.zip"
DMG="release/sai-desktop-macos-${ARCH}.dmg"
rm -f "$ZIP" "$DMG"
ditto -c -k --sequesterRsrc --keepParent dist/SAI.app "$ZIP"

echo "== 生成 DMG 安装镜像（拖拽到“应用程序”安装）=="
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto dist/SAI.app "$STAGE/SAI.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "SAI" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

echo
echo "发布包：$PWD/$ZIP"
echo "大小：$(du -h "$ZIP" | cut -f1)"
echo "sha256：$(shasum -a 256 "$ZIP" | awk '{print $1}')"
echo
echo "发布包：$PWD/$DMG"
echo "大小：$(du -h "$DMG" | cut -f1)"
echo "sha256：$(shasum -a 256 "$DMG" | awk '{print $1}')"
