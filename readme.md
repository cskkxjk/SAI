# CapsWriter-Offline

![demo](assets/demo.png)

> **按住 CapsLock 说话，松开就上屏。就这么简单。**

**CapsWriter-Offline** 是一个专为 Windows 打造的**完全离线**语音输入工具。

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
-   **录音保存**：所有语音均保存为本地音频文件，隐私安全，永不丢失。

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

### Windows fork 从零构建统一 EXE

下面是从用户 fork 构建 Windows 统一版 EXE 的完整流程。构建完成后，日常使用只需要双击一个
`CapsWriter.exe`，不需要分别启动服务端和客户端。

#### 1. 安装构建环境

在 Windows 10/11 64 位系统中准备：

- Git
- Python 3.10 或更高版本
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

#### 3. 创建 Python 虚拟环境并安装依赖

```powershell
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-client.txt -r requirements-server.txt -r requirements-build.txt
```

如果 PowerShell 不允许执行激活脚本，也可以不激活环境，后续统一使用：
`.\.venv\Scripts\python.exe`。

#### 4. 准备模型

模型文件不放入 Git 仓库，也不能只下载到 `dist` 目录。必须先放在源码仓库的 `models`
目录中，再执行 PyInstaller 构建。

Qwen3-ASR、SenseVoice、Paraformer 和标点模型可以使用本仓库的 ModelScope 下载脚本：

```powershell
.\.venv\Scripts\python.exe download_models.py all
```

该命令会下载并校验以下组件：

| 组件 | 用途 |
| --- | --- |
| Qwen3-ASR | 准确率最高，适合有独显或接受较高延迟的电脑 |
| SenseVoice-Small | 速度快、占用低，适合普通电脑和短句输入 |
| Paraformer | CPU 专用，速度快，但准确率和语言支持较弱 |
| Punct-CT-Transformer | 为 Paraformer 和部分文本流程提供标点 |

Fun-ASR-Nano 使用本 fork 当前自定义引擎所需的 GGUF/ONNX 文件，不能用任意同名模型替代。
请从上游项目的模型发布页下载 `Fun-ASR-Nano-GGUF.zip`：

<https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models>

解压后，将以下四个文件放入：

```text
models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model/
├── Fun-ASR-Nano-Encoder-Adaptor.fp32.onnx
├── Fun-ASR-Nano-CTC.int8.onnx
├── Fun-ASR-Nano-Decoder.q8_0.gguf
└── tokens.txt
```

构建前可检查四套模型的目录是否存在：

```powershell
Test-Path models\Qwen3-ASR\Qwen3-ASR-1.7B\qwen3_asr_encoder_frontend.onnx
Test-Path models\SenseVoice-Small\Sherpa-ONNX\model.int8.onnx
Test-Path models\Paraformer\speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx\model.onnx
Test-Path models\Fun-ASR-Nano\Fun-ASR-Nano-GGUF\model\Fun-ASR-Nano-Decoder.q8_0.gguf
```

输出 `True` 才表示对应文件已准备好。GUI 打开后也会在模型下拉框下方显示缺少文件数量。

#### 5. 准备 llama.cpp 运行库

Qwen3-ASR 和 Fun-ASR-Nano 的 GGUF 解码器需要 llama.cpp 的 Windows Vulkan DLL。
从下面的官方发行包下载并解压：

<https://github.com/ggml-org/llama.cpp/releases/download/b7798/llama-b7798-bin-win-vulkan-x64.zip>

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
libomp140.x86_64.dll
```

不要把这些 DLL 放入 `models`，也不要只复制 `llama.dll`。`.gitignore` 默认忽略 DLL，
所以其他设备从 Git clone 后仍需要完成这一步。

#### 6. 构建统一 EXE

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm build-desktop.spec
```

成功后，统一入口位于：

```text
dist/CapsWriter-Offline/CapsWriter.exe
```

请整体保留 `dist/CapsWriter-Offline` 文件夹，不能只复制单个 EXE。构建程序会把源码中的
模型文件、`core`、`assets`、配置文件和 LLM 角色目录一起复制到发布目录。

#### 7. 第一次启动和配置

1. 双击 `dist\CapsWriter-Offline\CapsWriter.exe`。
2. 在“识别模型”下拉框中选择模型。
3. 查看模型下方的“建议”和“状态”文字：
   - Qwen3-ASR：准确率最高，推荐有独显的电脑。
   - Fun-ASR-Nano：准确率和速度均衡，适合日常中英文输入。
   - SenseVoice-Small：CPU 占用低，适合短句和普通电脑。
   - Paraformer：CPU 专用、速度快，不使用 GPU。
4. 在“录音设备”下拉框中选择实际使用的麦克风。
5. 如果设备列表不完整，点击“刷新设备”。
6. 点击“保存设置”只保存当前配置；点击“保存并启动”会保存配置并启动识别服务。
7. 启动成功后配置窗口自动隐藏到 Windows 右下角托盘。
8. 双击托盘图标可以重新打开配置页；右键托盘图标可以停止服务或退出程序。

Windows 可能会通过 MME、DirectSound、WASAPI 和 WDM-KS 为同一个物理麦克风创建多个
音频端点。配置页会按设备名称进行物理设备去重，并优先保留 `Windows WASAPI` 入口，因此
列表中的数量通常会少于 Windows 音频设置中看到的端点数量。

#### 8. 使用语音输入

- 按住 `CapsLock` 说话，松开后自动识别并输入到当前获得焦点的输入框。
- 按一下 `F8` 开始录音，再按一下 `F8` 停止录音并输入文字。
- 也支持鼠标侧键 `X2`。
- 目标程序如果以管理员身份运行，CapsWriter 也需要以管理员身份运行，才能向目标窗口模拟输入。

识别服务、录音客户端和配置界面都由同一个 `CapsWriter.exe` 管理。不要再单独运行旧的
`start_server.py`、`start_client.py` 或旧版 `dist-v*` 目录。


#### 9. 本 fork 的主要改动

相对上游原版，本 fork 主要增加和调整了以下内容：

| 改动 | 说明 |
| --- | --- |
| Windows 统一入口 | `gui_launcher.py` 提供配置窗口、模型检查、服务启动和托盘管理，最终由 `CapsWriter.exe` 统一运行 |
| 录音设备选择 | 配置页读取 Windows 录音设备，保存实际设备名称和接口信息，启动前检查设备是否可用 |
| 物理麦克风去重 | 合并同一麦克风的 MME、DirectSound、WASAPI、WDM-KS 端点，并优先选择 WASAPI |
| 快捷键输入 | 支持 CapsLock 长按录音、松开输入，以及 F8 开始/停止录音 |
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
