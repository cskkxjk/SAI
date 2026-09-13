# Windows 统一 EXE 从零配置

本文适用于 Windows 10/11 64 位。最终运行时不需要 Python，但构建 EXE 需要 Python。

## 1. 安装准备

1. 安装 Git。
2. 安装 Python 3.10 或更高版本，安装时勾选 **Add Python to PATH**。
3. 安装 Microsoft Visual C++ Redistributable 2015-2022 x64。
4. AMD、NVIDIA 和 Intel 显卡都可以使用。GGUF 模型会自动尝试 Vulkan；失败时回退 CPU。

## 2. 克隆 fork

在 PowerShell 中执行：

```powershell
git clone git@github.com:cskkxjk/CapsWriter-Offline.git
cd CapsWriter-Offline
```

如果 SSH 未配置，可使用：

```powershell
git clone https://github.com/cskkxjk/CapsWriter-Offline.git
```

## 3. 一键准备和构建

在 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\prepare_windows.ps1
```

脚本会创建 `.venv`、安装运行依赖和 ModelScope 下载工具、下载模型，并构建统一 EXE。
如果只想手动执行，也可以使用下面的分步命令。

## 4. 下载模型

先运行：

```powershell
.\.venv\Scripts\python.exe download_models.py all
```

脚本会从 ModelScope 下载 Qwen3-ASR、SenseVoice、Paraformer 和中文标点模型，并校验文件大小和 SHA-256。下载完成后模型必须位于仓库的 `models` 目录中，不能移动到 `dist` 或 `dist-v*` 目录。

Fun-ASR-Nano 当前使用的 GGUF/ONNX 模型文件需要放在：

```text
models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model/
```

文件名必须是：

```text
Fun-ASR-Nano-Encoder-Adaptor.fp32.onnx
Fun-ASR-Nano-CTC.int8.onnx
Fun-ASR-Nano-Decoder.q8_0.gguf
tokens.txt
```

如果要使用 Fun-ASR-Nano，请从项目模型发布页或已验证的 ModelScope 模型源下载这四个文件。

## 5. 准备 llama.cpp 运行库

GGUF 模型需要 `llama.dll`、`ggml.dll`、`ggml-vulkan.dll` 和对应的 `ggml-cpu-*.dll`。本 fork 已随源码提供 Windows Vulkan 运行 DLL，clone 后不需要另行下载。它们位于：

```text
core/server/engines/llama/bin/
```

至少应包含：

```text
llama.dll
ggml.dll
ggml-base.dll
ggml-vulkan.dll
ggml-cpu-x64.dll
libomp140.x86_64.dll
```

如果使用其他来源的源码，才需要手动补齐这组 DLL；不要把 DLL 放进 `models` 目录。

## 6. 构建统一 EXE

确认模型和 DLL 都准备好后执行：

```powershell
python -m PyInstaller --noconfirm build-desktop.spec
```

成功后只需要使用：

```text
dist/CapsWriter-Offline/CapsWriter.exe
```

请整体保留 `dist/CapsWriter-Offline` 文件夹，不能只复制 EXE。该文件夹中包含运行库、模型、配置和托盘图标。

## 7. 启动和选择麦克风

1. 双击 `dist/CapsWriter-Offline/CapsWriter.exe`。
2. 在“录音设备”下拉框中选择麦克风。
3. 点击“刷新设备”可重新读取 Windows 当前设备。
4. 优先选择名称后标有 `Windows WASAPI` 的入口。
5. 点击“保存设置”确认配置，或点击“保存并启动”直接启动。
6. 启动成功后窗口会隐藏到右下角托盘。
7. 双击托盘图标可重新打开配置页。

同一个物理麦克风可能被 Windows 通过 MME、DirectSound、WASAPI、WDM-KS 暴露为多个端点。配置页会按设备名称合并这些端点，并优先保留 WASAPI 入口，因此通常每个物理设备只显示一次。

## 8. 语音输入

- 按住 `CapsLock` 说话，松开后识别并输入到当前焦点输入框。
- 按 `F8` 开始录音，再按一次 `F8` 停止。
- 也支持鼠标侧键 `X2`。

如果目标程序以管理员权限运行，CapsWriter 也需要以管理员权限运行，Windows 才允许向该程序模拟输入。

## 9. 排错

日志位于：

```text
dist/CapsWriter-Offline/logs/server_latest.log
dist/CapsWriter-Offline/logs/client_latest.log
```

看到 `Vulkan0` 和显卡名称表示 GGUF 正在使用 Vulkan。看到 DirectML 动态形状错误时，程序会自动回退 CPU；Qwen INT4 编码器在 Windows 上会自动使用 CPU，GGUF 解码仍可使用 Vulkan。

可以运行回归测试：

```powershell
python -m unittest discover -s tests -p regressions.py -v
```
