# CapsWriter-Offline

![demo](assets/demo.png)

> **按住 CapsLock 说话，松开就上屏。就这么简单。**

**CapsWriter-Offline** 是一个专为 Windows 打造的语音输入工具，默认使用本地模型，
也支持可选的 OpenAI 兼容语音转写 API。

## ✨ 核心特性

-   **语音输入**：按住 `CapsLock键` 或 `鼠标侧键X2` 说话，松开即输入，超低延迟，默认去除末尾逗句号。支持对讲机模式和单击录音模式。
-   **文件转录**：音视频文件往客户端 exe 一丢，字幕 (`.srt`)、文本 (`.txt`)、时间戳 (`.json`) 统统都有。
-   **数字 ITN**：自动将「十五六个」转为「15~16个」，支持各种复杂数字格式。
-   **热词替换**：在 `hot.txt` 记下偏僻词，通过音素模糊匹配，相似度大于阈值则强制替换。
-   **正则替换**：在 `hot-rule.txt` 用正则或简单等号规则，精准强制替换。
-   **LLM 角色**：预置了润色、小助理等角色，当识别结果的开头匹配任一角色名字时，将交由该角色处理。
-   **托盘菜单**：右键托盘图标即可添加热词、复制结果、清除LLM记忆。
-   **C/S 架构**：服务端与客户端分离，虽然 Win7 老电脑跑不了服务端模型，但最少能用客户端输入。
-   **日记归档**：按日期保存你的每一句语音及其识别结果。
-   **录音保存**：录音可保存为本地音频文件；API 模式还会向配置的服务上传录音。

### OpenAI 兼容语音 API

除本地模型外，Windows 图形配置页还支持 `OpenAI 兼容 API`。选择该模式后不需要
准备 `models` 目录，也不会加载本地 ASR 模型；录音片段会发送到配置的
`/audio/transcriptions` 接口，返回的文字仍会经过本项目现有的热词、替换和上屏流程。

在“语音 API”页填写：

1. **服务地址**：例如 `https://api.openai.com/v1`，或兼容服务的 `/v1` 地址。
2. **模型名称**：例如 `whisper-1`，以服务端实际支持的模型名为准。
3. **API Key**：保存在当前用户数据目录的 `config_gui.json` 中；也可以留空并使用
   环境变量 `CAPSWRITER_ASR_API_KEY`。
4. **请求超时**：默认 60 秒。

远程地址要求使用 HTTPS；本机开发服务可使用 `http://127.0.0.1`、
`http://localhost` 或 `http://[::1]`。启用时程序会再次确认，因为录音和上下文提示
会离开本机，服务商可能按请求收费。该模式沿用现有分段策略，长录音可能在松键前就提交
片段，不是实时流式接口；网络延迟会影响最终文字出现时间。失败不自动重试，
以避免重复计费。API 没有提供时间戳时，字幕时间仅为估算，不适合精确对齐。

服务必须支持语音转写，不是只有 `/chat/completions` 就能使用。上下文作为可选 `prompt`
提交，语言自动检测时不发送 `language`；若服务不支持这些可选字段，可清空上下文、
选择自动语言。Key 为明文，请勿共享个人配置文件或将其提交 Git。

安装包不包含模型。首次打开后，在“识别设置”
将识别模型切换为“OpenAI 兼容 API”，填写参数后“保存并启动”。

**CapsWriter-Offline** 的精髓在于：**完全离线**（不受网络限制）、**响应极快**、**高准确率** 且 **高度自定义**。我追求的是一种「如臂使指」的流畅感，让它成为一个专属的一体化输入利器。无需安装，一个U盘就能带走，随插随用，保密电脑也能用。

以下为支持的模型：

| 引擎名 | 准确性 | 速度 | 格式 | 显卡加速 |
|------|-------|------|------|---------|
| Paraformer | ★★★☆☆ | ★★★★★ | ONNX | ❌ |
| SenseVoice-Small | ★★★☆☆ | ★★★★★ | ONNX | ✅ |
| Fun-ASR-Nano | ★★★★☆ | ★★★★☆ | ONNX + GGUF | ✅ |
| Qwen3-ASR | ★★★★★ | ★★★☆☆ | ONNX + GGUF | ✅ |


性能参考（20s 音频转录延迟）：

| 模型 | CPU U9-285H | GPU RTX5050 |
|------|------------|------------|
| Paraformer | 0.6s | - |
| SenseVoice-Small | 0.6s | 0.15s |
| Fun-ASR-Nano | 2.0s | 0.5s |
| Qwen3-ASR-1.7B | 4.0s | 1.0s |

详细功能说明请参考 [`docs/`](docs/) 目录：
- [环境依赖安装说明](docs/环境依赖安装说明.md) — VC++ 运行库、FFmpeg 安装
- [热词功能如何使用](docs/热词功能如何使用.md) — 热词替换、规则替换、自定义短语
- [角色功能如何使用](docs/角色功能如何使用.md) — LLM 角色配置、输出模式、创建新角色
- [识别语言如何配置](docs/识别语言如何配置.md) — 各引擎语言支持范围与配置方法
- [文件转录功能如何使用](docs/文件转录功能如何使用.md) — 拖拽转字幕、时间戳对齐
- [显卡加速的若干问题](docs/显卡加速的若干问题.md) — DirectML、Vulkan 加速配置
- [模型下载的若干问题](docs/模型下载的若干问题.md) — 引擎选择、模型下载、目录结构
- [常见问题](docs/常见问题.md) — FAQ
- [更新日志](docs/CHANGELOG.md) 


## 💻 平台支持

目前**仅能保证在 Windows 10/11 (64位) 下完美运行**。

- **Linux**：暂无环境进行测试和打包，无法保证兼容性。
- **MacOS**：由于底层的 `keyboard` 库已放弃支持 MacOS，且系统限制极多，暂时无法支持。

[LazyTyper](https://lazytyper.com/) 和 [闪电说](https://shandianshuo.cn/) 也是很优秀的作品，都有离线引擎，都支持 Windows Linux 与 MacOS，并都有漂亮的图形化页面，推荐使用。

CapsWriter 的特别之处在于追求：

- 无感输入
- 完全离线，不受网络约束
- 低延迟，尽量做到硬件极限的最快速度
- 高度自定义的热词系统


## 🎬 快速开始

### Windows 安装版与源码构建

#### 使用安装向导

安装包位于 `dist/installer`，运行 `CapsWriter-Offline-2.7.0-Setup.exe`。
这是不含模型的单文件安装包，只需复制 Setup.exe，不再需要旁边的 `.bin` 文件。
安装过程不下载模型；首次使用本地识别时，在程序配置页从 ModelScope 按需下载。

1. 选择安装语言，接受许可证。
2. 选择安装路径，默认是当前用户的 `%LOCALAPPDATA%\Programs\CapsWriter Offline`。
3. 安装程序和运行库，不需要预先选择或准备模型。
4. 选择开始菜单目录，可勾选创建桌面快捷方式，点击“安装”。
5. 启动后在“识别设置”选择模型，检查“状态”，缺少文件时点击“下载模型”。
6. 等待下载和校验完成，选择麦克风并点击“保存并启动”。默认模型是 SenseVoice。

模型下载到 **安装目录下的 `models` 文件夹**，不是用户配置目录。请安装到当前用户可写
且空间充足的目录。SenseVoice 和 Paraformer 会自动下载标点模型。
下载直连 ModelScope，不使用系统代理；进度显示已完成与总字节数，可取消。
下载以 `.part` 临时文件保存，大小和 SHA256 校验通过后才替换正式文件。
失败或取消会清除当前未完成文件，已完成文件保留；再次下载会先校验并跳过完整文件。
“刷新状态”检查文件是否存在及非空；“校验／修复模型”联网执行完整 SHA256 校验，
修复大小正确但内容损坏的文件。API 模式不需要下载任何模型。
下载时不可启动识别，识别运行时需先停止再下载，防止替换正在使用的文件。

**Qwen3-ASR 量化版本：** 选择 Qwen3-ASR 后，“Qwen 量化”下拉框可选择
`q5_k`（默认，独显优先，解码器约 1.47 GB）或 `q4_k`（集显／低显存可尝试，
解码器约 1.28 GB）。两个版本共用 INT4 ONNX 编码器，当前编码器走 CPU；
GGUF 解码器按 GPU 设置运行。具体延迟和效果以自己的录音测试为准。
各自保存为 `qwen3_asr_llm.q5_k.gguf` 和 `qwen3_asr_llm.q4_k.gguf`，可同时保留。
切换后检查状态、下载缺少文件，再“保存并启动／重启”才能生效。
旧版 `qwen3_asr_llm.gguf` 点击下载时会先校验，匹配所选版本才改名复用。
命令行可用 `python download_models.py qwen_asr --quantization q4_k`。

若旧安装版 Qwen 输出重复问号或“解码有误，强制熔断”，请更新修复版：
旧版按需下载缺少来源记录，可能使 INT4 编码器误用 DirectML。新版对默认编码器文件名
直接启用 CPU 兼容保护，不依赖来源记录；GGUF GPU 解码不受影响。
解码重试仍失败时只通知错误，不再将失败文本输入当前窗口。已有模型无需重新下载。

**设备检测：** 打开配置窗口后，“设备检测”页自动显示 CPU、核心／线程数、系统内存、
显卡型号与厂商、专用显存、共享内存上限，以及 Vulkan 驱动入口和 ONNX 运行库后端。
Windows 使用 DXGI 读取显存，支持 AMD、NVIDIA、Intel 和多显卡；软件适配器会被排除。
共享内存不是显卡当前可用显存，检测驱动入口也不等于模型 GPU 推理已经验证成功。

建议是保守的容量估计，不是性能测试：专用显存至少 4 GiB 时优先试 Qwen Q5_K，
2–4 GiB 时试 Q4_K，共享内存型设备先试 Fun-ASR-Nano；无已识别硬件显卡、未发现
Vulkan 或系统内存不足时先试 SenseVoice CPU。NVIDIA 预加速默认不建议开启。
界面不会自动覆盖配置；点击“应用建议到配置”并确认后，才填入模型、量化和 GPU 选项，
不更改麦克风、密钥或热词，也不会自动下载、保存或启动。之后按常规流程下载模型、
保存并启动。实际使用哪块 GPU 以启动日志为准，目前不检测 GPU 空闲率或剩余显存。

安装版已经包含 Python 和推理库，不需要安装 Python、Git 或 uv。
它是当前用户安装，不默认请求管理员权限；自选目录必须是当前用户可写的位置。
可以通过 Windows“已安装的应用”或开始菜单中的卸载入口卸载。
程序设置、热词、LLM 角色和录音保存在 `%LOCALAPPDATA%\CapsWriterOffline`，
托盘菜单“打开数据目录”可以打开它；更新和卸载不会删除这些个人数据。

旧便携版数据不会自动迁移。退出新旧两版并做好备份后，将旧版的 `config_gui.json`、
`hot.txt`、`hot-rule.txt`、`hot-server.txt`、`LLM` 和需要保留的年份目录复制到上述数据目录；
不要复制旧版 `core`、`internal` 或 DLL。安装版和便携版不要同时运行。
安装包未配置代码签名，发布者身份提示不能作为已签名发行版看待。

下面是从用户 fork 构建 Windows 统一版 EXE 的完整流程。构建完成后，日常使用只需要双击一个
`CapsWriter.exe`，不需要分别启动服务端和客户端。

这是目录式便携版，不是单文件安装包。只想在另一台电脑上使用时，可以复制完整的
`dist/CapsWriter-Offline` 文件夹，无需安装 Python 或 Git；仍需满足下面的 Windows
运行环境要求，并在新电脑上重新选择录音设备。

Git 仓库只提供源码、构建配置和说明，不包含 EXE、模型、llama.cpp DLL 或你的录音。
因此 `git clone` 后需要按第 1～6 步准备环境并构建，不能直接找到一个已编译的 EXE。
以下命令均在 **Windows PowerShell** 中执行；不要在 WSL 中运行这些命令来构建 Windows EXE。

#### 1. 安装构建环境

在 Windows 10/11 64 位系统中准备：

- Git
- Python 3.14 x64（普通版，不使用自由线程实验构建），保留 Tcl/Tk
- uv，用于按 `uv.lock` 安装构建环境
- Inno Setup 6.7.3 或兼容的 6.x 版本，用于编译安装向导；仅构建便携版时不需要
- Microsoft Visual C++ Redistributable 2015-2022 x64
- 可选：FFmpeg。只有使用文件转录功能时才需要，并且 `ffmpeg.exe` 必须在 PATH 中

AMD、NVIDIA 和 Intel 显卡都可以运行。GGUF 解码器使用随 llama.cpp 发布包提供的 Vulkan
运行库；如果 Vulkan 初始化失败，程序会自动回退到 CPU。

#### 2. 克隆 fork

```powershell
git clone git@github.com:cskkxjk/CapsWriter-Offline.git
cd CapsWriter-Offline
```

如果本机还没有配置 GitHub SSH Key，可以改用：

```powershell
git clone https://github.com/cskkxjk/CapsWriter-Offline.git
cd CapsWriter-Offline
```

#### 3. 创建 Python 3.14 环境并安装锁定依赖

```powershell
python -m pip install uv
uv python install 3.14
uv sync --locked --group build
.\.venv\Scripts\python.exe -c "import tkinter, sounddevice, soundfile, sherpa_onnx, sentencepiece, gguf, onnxruntime, PyInstaller; print('构建依赖导入成功')"
```

不需要激活虚拟环境或修改 PowerShell 执行策略，后续始终调用
`.\.venv\Scripts\python.exe`。上面的第一个 `python` 可以是已有 Python，用于安装 uv；
没有 Python 时可先安装 Python 3.14 x64，再执行命令。安装 uv 后如命令无法找到，
重新打开终端或把其 Scripts 目录加入 PATH。
`pyproject.toml` 与 `uv.lock` 是当前推荐的构建依赖来源，使用阿里云 PyPI 镜像；
旧的 requirements 文件仅保留给原有启动流程，不作为本次升级的锁定构建环境。
依赖导入检查失败时先处理安装错误，不要跳过并直接打包。

#### 4. 模型按需下载（构建前可跳过）

模型文件不放入 Git 仓库，也不再打包进 EXE 安装包。构建前无需下载。
构建或安装后打开程序，在“识别设置”选择模型并点击“下载模型”即可。

如需直接从源码运行，可选用同一下载逻辑的命令行入口（下载到源码目录的 `models`）：

```powershell
.\.venv\Scripts\python.exe download_models.py all
```

该命令会下载并校验以下组件：

| 组件 | 用途 |
| --- | --- |
| Qwen3-ASR | 面向多语言和复杂口述；当前 INT4 编码器走 CPU，GGUF 解码可尝试 GPU |
| Fun-ASR-Nano | 从 HaujetZhao/Fun-ASR-Nano-2512-GGUF 下载当前引擎兼容文件 |
| SenseVoice-Small | 速度快、占用低，适合普通电脑和短句输入 |
| Paraformer | 当前配置使用 CPU，适合低占用输入 |
| Punct-CT-Transformer | 为 Paraformer 和部分文本流程提供标点 |

Fun-ASR-Nano 使用本 fork 当前自定义引擎所需的 GGUF/ONNX 文件，不能用任意同名模型替代。
GUI 和命令行会自动将四个文件下载到以下目录，无需手动解压：

```text
models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model/
├── Fun-ASR-Nano-Encoder-Adaptor.fp32.onnx
├── Fun-ASR-Nano-CTC.int8.onnx
├── Fun-ASR-Nano-Decoder.q8_0.gguf
└── tokens.txt
```

如已手动放置模型，可检查目录是否存在（不是构建要求）：

```powershell
Test-Path models\Qwen3-ASR\Qwen3-ASR-1.7B\qwen3_asr_encoder_frontend.onnx
Test-Path models\SenseVoice-Small\Sherpa-ONNX\model.int8.onnx
Test-Path models\Paraformer\speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx\model.onnx
Test-Path models\Fun-ASR-Nano\Fun-ASR-Nano-GGUF\model\Fun-ASR-Nano-Decoder.q8_0.gguf
```

输出 `True` 才表示对应文件已准备好。GUI 打开后也会在模型下拉框下方显示缺少文件数量。
上述命令仅检查代表文件，不代表全部文件已经齐全。打包前可检查 GUI 使用的全部文件清单：

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; from gui_launcher import MODEL_INFO; missing = [p for info in MODEL_INFO.values() for p in info['files'] if not Path(p).is_file()]; print('\n'.join(missing) if missing else '四套模型文件均已找到'); raise SystemExit(bool(missing))"
```

空间有限时只需下载打算使用的模型。即使源码目录已有模型，打包也不会复制它们。
未准备的模型仍会显示在下拉菜单中，点击“下载模型”后即可准备使用。

#### 5. 准备 llama.cpp 运行库

Qwen3-ASR 和 Fun-ASR-Nano 的 GGUF 解码器需要 llama.cpp 的 Windows Vulkan DLL。
从下面的官方发行包下载并解压：

<https://github.com/ggml-org/llama.cpp/releases/download/b10621/llama-b10621-bin-win-vulkan-x64.zip>

将压缩包中的 DLL 文件全部复制到：

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
libomp.dll
```

不要把这些 DLL 放入 `models`，也不要只复制 `llama.dll`。`.gitignore` 默认忽略 DLL，
所以其他设备从 Git clone 后仍需要完成这一步。
压缩包 SHA256 为 `2672d85bf87c8280d94dee01eb6a86280046878f70a07d786a93637fa9081163`；
可用 `Get-FileHash <压缩包路径> -Algorithm SHA256` 核对。保留仓库提供的
`core/server/engines/llama/bin/runtime-version.json`，构建时会检查版本。
升级时先把旧 bin 目录备份到其他位置，再放入新版 DLL，不要混用 b7798 和 b10621。

#### 6. 构建统一 EXE

首次构建时，在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm build-desktop.spec
```

成功后，统一入口位于：

```text
dist/CapsWriter-Offline/CapsWriter.exe
```

请整体保留 `dist/CapsWriter-Offline` 文件夹，不能只复制单个 EXE。构建程序会把源码中的
`core`、`assets`、配置文件和 LLM 角色目录一起复制到发布目录，但不复制模型。

可以把该文件夹复制到另一台 Windows x64 电脑，或整体压缩传输，解压后直接打开 EXE。
不要仅发送 `CapsWriter.exe`；`internal` 是 Python 和第三方运行库，`core` 是程序代码，
`models` 是模型，`assets` 是界面资源，`LLM` 保存润色角色配置，均应随包保留。

**重新构建时保护旧数据：** `--noconfirm` 可能直接替换同名发布目录。
不要向正在使用、含个人配置和历史录音的目录直接打包。改用一个新的输出目录，例如：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist-next build-desktop.spec
```

先确认 `dist-next/CapsWriter-Offline/CapsWriter.exe` 能正常运行，再退出旧程序并迁移自己的
`config_gui.json`、热词文件和需要保留的按年录音目录。不要用旧 `core`、`internal`
覆盖新程序；手工改过的 `config_client.py`、`config_server.py` 应比较后迁移配置。
再次构建时也不要重复覆盖已经在使用的 `dist-next`，应另选空目录。

**关于两层 dist 目录：** 当前规范入口是 `dist/CapsWriter-Offline/CapsWriter.exe`。
如果外层另有旧的 `dist/CapsWriter.exe`、`dist/config_gui.json`，确认没有使用且无需保留后
可以删除。内层整个文件夹不是重复文件，而是当前完整程序。
需要平铺时，先退出程序、处理外层同名旧文件，再把内层所有内容一起移动到 `dist`；
EXE 和相邻资源必须保持相对位置。下次构建仍会生成内层目录，不会跟随手工搬移。

可选的源码回归检查（不需要启动识别服务，不使用真实麦克风；GUI 测试需 Windows 桌面会话）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p regressions.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p microphone_lifecycle.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p audio_input_regressions.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p desktop_hotwords.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p upstream_merger.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p installer_paths.py -v
```

#### 6.1 编译带安装向导的 EXE

在全新的 staging 目录构建，再用 Inno Setup 编译：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath build/installer-stage build-desktop.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" installer\CapsWriter.iss
```

Inno Setup 安装路径不同时，请把第二条命令改成实际 `ISCC.exe` 路径。
安装包不需要任何模型文件。输出在 `dist/installer`，只有一个 Setup.exe。
旧版遗留的 `CapsWriter-Offline-2.7.0-Setup-*.bin` 不再使用，新版生成成功后可删除。
不要使用含个人日志、录音、API 密钥的旧发布目录作为安装包源；
默认配置来自 `installer/config_gui.json`，首次安装不会继承本机的麦克风设备选择。

打包后可执行离线诊断，不会录音或模拟键盘输入：

```powershell
.\build\installer-stage\CapsWriter-Offline\CapsWriter.exe --self-test
```

结果保存在数据目录的 `logs/self-test.json`。可加 `--model sensevoice --audio C:\path\sample.wav`
验证指定音频文件的实际识别；换成 `fun_asr_nano` 或 `qwen_asr` 可验证新版 GGUF 运行库。

#### 7. 第一次启动和配置

1. 双击 `dist\CapsWriter-Offline\CapsWriter.exe`。
2. 在“识别模型”下拉框中选择模型。
3. 查看模型下方的“建议”和“状态”文字：
   - Qwen3-ASR：适合多语言和复杂口述，推荐有独显的电脑。
   - Fun-ASR-Nano：准确率和速度均衡，适合日常中英文输入。
   - SenseVoice-Small：CPU 占用低，适合短句和普通电脑。
   - Paraformer：CPU 专用、速度快，不使用 GPU。
4. 在“录音设备”下拉框中选择实际使用的麦克风。
5. 如果设备列表不完整，点击“刷新设备”。
6. 点击“保存设置”只保存当前配置；点击“保存并启动”会保存配置并启动识别服务。
7. 启动成功后配置窗口自动隐藏到 Windows 右下角托盘。
8. 双击托盘图标可以重新打开配置页，在配置页点击“停止”可停止服务；
   右键托盘菜单可以打开配置或退出程序。

Windows 可能会通过 MME、DirectSound、WASAPI 和 WDM-KS 为同一个物理麦克风创建多个
音频端点。配置页会按设备名称进行物理设备去重，并优先保留 `Windows WASAPI` 入口，因此
列表中的数量通常会少于 Windows 音频设置中看到的端点数量。

#### 8. 使用语音输入

在 EXE 配置页点击“热词与替换”，默认打开“文字替换”表格，不需要编写代码：

1. “识别成了什么”填 `欧拉玛`，“替换成什么”填 `Ollama`。
2. 点击“添加”，再点“保存当前页”。修改已有条目时先选中表格中的一行，
   修改输入框后点“修改选中项”；删除使用“删除选中项”。
3. 在“测试原文”输入一句话，点击“测试替换”查看结果。
   普通替换按文字原样匹配，标点不需要转义；替换内容留空表示删除原词。

已有简单规则会显示在表格中，复杂正则保留在高级页，保存不会删除它们。
表格创建的条目由程序保存为 `hot-rule.txt` 内的 `@literal` 数据，不需要手工编辑。
升级程序时请连同 `core` 目录一起更新；旧版本不认识这种条目。

- “热词与别名（高级）”：每行 `目标词 | 别名`，例如 `CapsWriter | 卡普斯赖特`，
  用于发音相近的纠错；这不是严格的逐字匹配。
- “正则规则（高级）”：每行 `查找内容 = 替换内容`，等号两侧保留空格，
  例如 `欧拉玛 = Ollama`。查找内容支持正则表达式；普通标点如 `.` 需写成 `\.`。
- 点击“测试替换”可预览规则替换结果，不会录音或向其他窗口输入文字；
  此测试只执行“规则替换”，不包含发音纠错或 LLM 润色。
- 点击“保存当前页”。运行中的客户端通常约 3 秒后自动重载，无需重启模型；
  未启动时会在下次启动加载。“文字替换”和“正则规则（高级）”共用一份文件；
  热词别名单独保存，关闭时会提醒未保存的修改。
- 便携版文件保存在 EXE 同目录的 `hot.txt` 和 `hot-rule.txt`；
  安装版保存在 `%LOCALAPPDATA%\CapsWriterOffline`，不是源码目录；
  保存前会检查规则格式、正则和文件是否被其他程序修改。

- 默认按需开启麦克风，松键或取消即关闭，空闲时不占用设备。
  部分 USB 麦克风重新开启后会有约 2 秒的无声冷启动期，立即说话可能丢失开头。
- 此类设备若需要即按即说，可勾选“快速响应（空闲时持续占用麦克风）”并保存重启：
  启动时短暂预热，之后保持采集，但空闲音频直接丢弃、不保存、不发送；
  只有按键录音期间的音频会进入识别。此模式下 Windows 显示麦克风正在使用是正常现象。
- 按住 `CapsLock` 说话，松开后自动识别并输入到当前获得焦点的输入框。
- 按一下 `F8` 开始录音，再按一下 `F8` 停止录音并输入文字。
- 也支持鼠标侧键 `X2`。
- 目标程序如果以管理员身份运行，CapsWriter 也需要以管理员身份运行，才能向目标窗口模拟输入。

识别服务、录音客户端和配置界面都由同一个 `CapsWriter.exe` 管理。不要再单独运行旧的
`start_server.py`、`start_client.py` 或旧版 `dist-v*` 目录。


#### 9. 本 fork 的主要改动

2026-09-21 已合并上游至 `84912d5`：

- `39c3318`：强制对齐上下文由 3072 扩大到 4096。
- `84912d5`：跨分片匹配切点落在 token 内部时按字符拆分，避免空格或其他字符丢失，
  并保留窗口之外的历史内容。

- `47df96a`、`29a0c8b`：llama.cpp b10621 结构体及采样函数签名适配，
  同时保留本 fork 的 GPU 加载失败回退 CPU。
- `1a332b4`：Python 3.14 / uv 环境管理；本 fork 另外补齐模型下载和安装版构建依赖。

新版代码与 b7798 DLL 不兼容，必须整套升级并重新构建 EXE。

相对上游原版，本 fork 主要增加和调整了以下内容：

| 改动 | 说明 |
| --- | --- |
| Windows 统一入口 | `gui_launcher.py` 提供配置窗口、模型检查、服务启动和托盘管理，最终由 `CapsWriter.exe` 统一运行 |
| 录音设备选择 | 配置页读取 Windows 录音设备，保存实际设备名称和接口信息，启动前检查设备是否可用 |
| 物理麦克风去重 | 合并同一麦克风的 MME、DirectSound、WASAPI、WDM-KS 端点，并优先选择 WASAPI |
| 快捷键输入 | 支持 CapsLock 长按录音、松开输入，以及 F8 开始/停止录音 |
| 麦克风生命周期 | 默认松键释放设备；可选快速响应模式，空闲音频不保存、不发送；专用 COM 线程修复 Windows 设备打开失败 |
| 音频完整性 | 修复缓存切换丢帧，跳过全零录音和 `/sil` 输出，防止按住快捷键时反复失败重试 |
| 可视化替换 | 表格添加、修改、删除文字替换；保留高级正则和热词别名，保存后自动重载 |
| GPU 容错 | 自动尝试可用的 DirectML/Vulkan 后端；GPU 初始化或推理失败时回退 CPU |
| GGUF/ONNX 兼容 | 增加 Qwen3-ASR、Fun-ASR-Nano 的模型接口和不同导出格式的兼容处理 |
| 发布构建 | `build-desktop.spec` 将代码、运行库、模型、配置和托盘资源整理为统一发布目录 |

## ⚙️ 个性化配置

首次使用和日常修改优先通过 `CapsWriter.exe` 配置页完成：模型、识别语言、推理后端、
录音设备、输出方式和 GPU 选项都可以在界面中保存。高级用户仍可以编辑发布目录中的
`config_server.py`、`config_client.py`、`hot.txt` 和 `hot-rule.txt`。


## 🛠️ 常见问题


**Q: 为什么按了没反应？**  
A: 确认已经双击统一入口 `dist\CapsWriter-Offline\CapsWriter.exe`，并在配置页点击
“保存并启动”。启动成功后主窗口会隐藏到托盘，右键托盘图标可以查看状态或退出。

**Q: 为什么识别结果没字？**  
A: 到 `年/月/assets` 文件夹中检查录音文件，看是不是没有录到音；听听录音效果，是不是麦克风太差，建议使用桌面 USB 麦克风；检查麦克风权限。

**Q: 想要隐藏黑窗口？**  
A: 统一 EXE 默认不显示黑色控制台窗口，启动成功后配置窗口会隐藏到右下角托盘。

**Q: 如何开机启动？**  
A: `Win+R` 输入 `shell:startup` 打开启动文件夹，将
`dist\CapsWriter-Offline\CapsWriter.exe` 的快捷方式放进去即可。

**Q: 如何修改快捷键？**
A: 默认使用 CapsLock 长按录音、松开输入，F8 单击开始/停止。高级用户可以编辑
`config_client.py` 中的 `ClientConfig.shortcuts`；`hold_mode=True` 是按住说话，
`hold_mode=False` 是单击开始、再次单击停止。

**Q: Windows 如何使用统一图形界面？**
A: 构建 `build-desktop.spec` 后，双击 `dist/CapsWriter-Offline/CapsWriter.exe`，在配置页
选择模型和录音设备，点击“保存并启动”。成功后窗口会隐藏到托盘。

更多问题请参阅 [docs/常见问题.md](docs/常见问题.md)。


## 🚀 我的其他优质项目推荐

| 项目名称 | 说明 | 体验地址 |
| :--- | :--- | :--- |
| [**IME_Indicator**](https://github.com/HaujetZhao/IME_Indicator) | Windows 输入法中英状态指示器 | [下载即用](https://github.com/HaujetZhao/IME_Indicator/releases/latest/download/IME-Indicator.exe) |
| [**Rust-Tray**](https://github.com/HaujetZhao/Rust-Tray) | 将控制台最小化到托盘图标的工具 | [下载即用](https://github.com/HaujetZhao/Rust-Tray/releases/latest/download/Tray.exe) |
| [**Gallery-Viewer**](https://github.com/HaujetZhao/Gallery-Viewer-HTML) | 网页端图库查看器，纯 HTML 实现 | [点击即用](https://haujetzhao.github.io/Gallery-Viewer-HTML/) |
| [**全景图片查看器**](https://github.com/HaujetZhao/Panorama-Viewer-HTML) | 单个网页实现全景照片、视频查看 | [点击即用](https://haujetzhao.github.io/Panorama-Viewer-HTML/) |
| [**图标生成器**](https://github.com/HaujetZhao/Font-Awesome-Icon-Generator-HTML) | 使用 Font-Awesome 生成网站 Icon | [点击即用](https://haujetzhao.github.io/Font-Awesome-Icon-Generator-HTML/) |
| [**五笔编码反查**](https://github.com/HaujetZhao/wubi86-revert-query) | 86 五笔编码在线反查 | [点击即用](https://haujetzhao.github.io/wubi86-revert-query/) |
| [**快捷键映射图**](https://github.com/HaujetZhao/ShortcutMapper_Chinese) | 可视化、交互式的快捷键映射图 (中文版) | [点击即用](https://haujetzhao.github.io/ShortcutMapper_Chinese/) |


## ❤️ 致谢

本项目基于以下优秀的开源项目：

-   [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx)
-   [FunASR](https://github.com/alibaba-damo-academy/FunASR)

感谢 Google Antigravity、Anthropic Claude、GLM、DeepSeek，如果不是这些编程助手，许多功能（例如基于音素的热词检索算法）我是无力实现的。

特别感谢那些慷慨解囊的捐助者，你们的捐助让我用在了购买这些优质的 AI 编程助手服务，并最终将这些成果反馈到了软件的更新里。


如果觉得好用，欢迎点个 Star 或者打赏支持：


![sponsor](assets/sponsor.jpg)	
