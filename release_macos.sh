#!/bin/zsh
# 一键打出 macOS 瘦身发布包：
#   dist/SAI.app 以及 release/SAI-<版本>-macos-arm64.zip
# 用法： ./release_macos.sh            # 版本号取自 config_server.__version__
#        ./release_macos.sh 1.0.4      # 或手动指定版本
set -euo pipefail
cd "$(dirname "$0")"

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
VER="${1:-$(.venv/bin/python -c 'from config_server import __version__; print(__version__)')}"

echo "== 同步打包依赖 =="
"$UV" sync --group build

echo "== PyInstaller 打包（版本 ${VER}）=="
.venv/bin/python -m PyInstaller --noconfirm --distpath dist build-macos.spec

echo "== 整理 app（源码/资源、installed.flag、去模型、ad-hoc 重签名）=="
./make_macos_app.sh

echo "== 压缩发布包 =="
mkdir -p release
ZIP="release/SAI-${VER}-macos-arm64.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent dist/SAI.app "$ZIP"

echo
echo "发布包：$PWD/$ZIP"
echo "大小：$(du -h "$ZIP" | cut -f1)"
echo "sha256：$(shasum -a 256 "$ZIP" | awk '{print $1}')"
