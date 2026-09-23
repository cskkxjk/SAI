# 更新日志

## v1.0.2（SAI）

- **粘贴快捷键**：新增独立的「粘贴快捷键」——用该键录音时，结果一律用剪贴板 `Ctrl+V` 上屏（支持本机与远程桌面）；原「使用剪贴板粘贴输出」开关移除，改由该快捷键与远程桌面自动判断接管。
- **远程桌面 / 虚拟桌面适配**：自动识别 `mstsc`、深信服（`RVLSession`）等远程窗口；中文结果自动改用剪贴板，ASCII 内容改用 pynput 逐字键入（`keyboard.write` 的扫描码事件会被转发层搅乱）；剪贴板同步与恢复的等待时间自动加长到 1s / 3s。
- **快捷键热重载**：在界面里改快捷键后，客户端几秒内自动重建按键任务，不需要重启、不重新加载模型。
- **粘贴时序加固**：写入剪贴板后先校验再发 `Ctrl+V`，失败自动重试；恢复原剪贴板前确认内容仍是本次识别结果，避免覆盖用户新复制的内容。
- **GGUF 引擎启动崩溃修复**：Fun-ASR、Qwen3-ASR 与对齐器此前各自去加载自己目录下的 `bin` 运行库（该目录并不存在），缺少运行库时服务端启动即退出（退出码 1）；现统一使用 `core/server/engines/llama` 下的运行库，缺库或 DLL 损坏时界面直接显示可读原因与修复建议。
- **运行库检测与一键修复**：「识别设置」页新增「推理运行库」状态与「修复运行库」按钮（仅 GGUF 模型需要）；缺失、被杀毒软件破坏或版本不符时可自动下载官方压缩包、校验 SHA256 后覆盖安装，启动前也会先体检并引导修复。支持 `SAI_LLAMA_RUNTIME_URL` / `SAI_LLAMA_RUNTIME_SHA256` 环境变量或 `runtime-version.json` 的 `url` 字段指定镜像。
- **残留修饰键清理**：客户端启动时检查并释放上次异常退出可能残留的 Shift / Ctrl / Alt / Win 按下状态。
- **界面细节**：去掉「选项」卡片里的粘贴开关与冗余分割线；「粘贴快捷键」提示中标注 Alt+CapsLock 与系统输入法切换冲突，建议改用鼠标侧键；设备检测页去掉过时的 NVIDIA 预加速说明，同型号显卡自动去重；「上下文提示」补充说明它被哪些模型使用。
- **安装包瘦身**：排除只会被韩语分词（`soynlp`）和 `pydantic` 可选探测拉进来的 `scipy`、`Cython`、`lxml` 等未使用依赖（韩语对齐自动退回按字切分，其它功能不变），安装包改用固实 `lzma2/ultra64` 压缩，体积从约 133 MB 降到约 67 MB。

## v1.0.1（SAI）

- **设置页调整**：「模型文件」卡片并入「识别」卡片，模型状态、Qwen 量化与下载按钮和识别模型放在一起；「快捷键」卡片改为行内布局，更紧凑。
- **关闭行为可选**：新增「关闭窗口时」选项，可选每次询问、最小化到托盘继续运行、直接退出程序，并随配置保存。
- **构建元数据同步**：`uv.lock` 项目名由 `capswriter-offline` 更正为 `sai`，与更名后的项目一致。

## v1.0.0（SAI）

SAI 从本版起独立编版本号。相对上游 CapsWriter-Offline（v2.6）的主要变化：

- **更名与图形界面**：项目更名为 **SAI**，桌面界面与安装流程整体重做（配色、卡片与侧边导航参考 Clash Verge Rev）。
- **统一设置窗口**：识别模型、推理后端、录音设备、快捷键、模型文件、热词与替换、语音 API、设备检测都在主窗口里完成。
- **快捷键可改**：支持任意键盘组合键或鼠标侧键，按住说话与单击开始/停止两种模式；录音时托盘图标变红。
- **热词与替换编辑器**：表格化编辑热词、别名与正则规则，内置测试替换，保存时自动校验规则。
- **拖拽转录**：把音视频文件拖到 `SAI.exe` 上，直接生成 `.srt` / `.txt` / `.json`。
- **远端 OpenAI 兼容 API**：新增「语音 API」页与「测试连接」，支持允许明文 HTTP 并绕过系统代理。
- **安装与数据**：一键安装包（可选模型、自动下载与 SHA256 校验），安装目录与用户数据分离，旧数据目录自动迁移。
- **稳定性修复**：麦克风端点打不开时自动回退到同名设备的其它 host API / 采样率；修复缺少 rich Unicode 数据导致服务端退出码 1；所有子进程不再弹出控制台窗口。


## v2.6

- **GPU 预加速**：服务端新配置（默认关闭），适用于 Nvidia 独显，录音开始时主动锁定显存高频率，大幅降低模型转录延迟至 0.1s
- **标点处理优化**：仅对 8 词以内的结果去除末尾标点，长句不再误伤；可通过配置 config_client.py 指定某些程序强制去除标点
- **带延迟的附加回车**：用于同花顺等场景，输入股票名后等待 0.5s 自动回车切换，可在 config_client.py 配置
- **角色功能简化**：角色功能简化为单一开关；默认角色改用 DeepSeek API，方便新用户填入密钥后直接体验
- **ITN 增强**：连续多数字转换更鲁棒
- **活动窗口日志**：每次识别后通过 debug 日志输出当前活动窗口名，方便用户配置 paste_apps
- **标点模型容错**：取消强制检查，加载失败时静默回退，避免启动受阻
- **热词使用体验**：右键菜单点击「热词」直接打开 hot.txt 文件，不再弹窗
- **重启菜单**：托盘右键新增「重启」选项，便于修改配置后快速重启生效
- **集成显卡兼容**：加入调试性配置项，集显出问题时可通过禁用 feature 解决
- **Windows Terminal 适配**：支持隐藏 Windows Terminal 控制台窗口

## v2.5

- **引入 [Qwen3-ASR-1.7B](https://github.com/HaujetZhao/Qwen3-ASR-GGUF)**：140ms 极速推理，准确率夯爆。Decoder Vulkan 加速默认打开，需占 1.6GB 显存。显卡空闲时，会降低显存频率，冷启动转录延迟升至 300ms。若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- **集成 Force Aligner**：辅助 Qwen3-ASR 支持时间戳，按需加载、超时释放，仅文件转录时占用资源
- **热词别名**：热词支持用 `|` 分隔定义多个别名，
- **角色别名**：`name` 用 `|` 分隔定义多个别名，解决 ASR 对角色名识别不准的问题
- **移除纠错历史**：热词别名已能覆盖纠错历史的需求，移除 `hot-rectify.txt` 及相关逻辑
- **文件转录支持热词**：文件转录现在也可以使用热词功能
- **语言配置**：`config_client.py` 新增 `language` 选项，支持指定识别目标语言
- **架构重构**：进行了大量重构，方便后续维护
- **日志优化**：只保留一份日志文件

## v2.4

- **改进 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型，使 Encoder 支持通过 DML 用显卡（独显、集显均可）加速推理，Encoder 和 CTC 默认改为 FP16 精度，以便更好利用显卡算力**，短音频延迟最低可降至 200ms 以内。
  - 若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- 服务端 Fun-ASR-Nano 使用单独的热词文件 hot-server.txt ，只具备建议替换性，而客户端的热词具有强制替换性，二者不再混用
- 可以在句子的开头或结尾说「逗号、句号、回车」，自动转换为对应标点符号，支持说连续多个回车。
- Fun-ASR-Nano 加入采样温度，避免极端情况下的因贪婪采样导致的无限复读
- 服务端字母拼写合并处理

## v2.3

- **引入 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型支持，推理更轻快**
- 重构了大文件转录逻辑，采用异步流式处理
- 优化中英混排空格
- 增强了服务端对异常断连的清理逻辑

## v2.2

- **改进热词检索**：将每个热词的前两个音素作为索引进行匹配，而非只用首音素索引。
- **UDP广播和控制**：支持将结果 UDP 广播，也可以通过 UDP 控制客户端，便于做扩展。
- **Toast窗口编辑**：支持对角色输出的 Toast 窗口内容进行编辑。
- **多快捷键**：支持设置多个听写键，以及鼠标快捷键，通过 pynput 实现。
- **繁体转换**：支持输出繁体中文，通过 zhconv 实现。

## v2.1

- **更强的模型**：内置多种模型可选，速度与准确率大幅提升。
- **更准的 ITN**：重新编写了数字 ITN 逻辑，日期、分数、大写转换更智能。
- **RAG 检索增强**：热词识别不再死板，支持音素级的 fuzzy 匹配，就算发音稍有偏差也能认出。
- **LLM 角色系统**：集成大模型，支持润色、翻译、写作等多种自定义角色。
- **纠错检索**：可记录纠错历史，辅助LLM润色。
- **托盘化运行**：新增托盘图标，可以完全隐藏前台窗口。
- **完善的日志**：全链路日志记录，排查问题不再抓瞎。


## v1.0 

- 通过分段识别和去重，实现了支持无限时长语音的转写
- 客户端支持转写音视频文件为 srt 字幕，只需将音视频文件拖动到客户端 exe 上打开即可

## v0.6 


- 新增日记功能，将每日的录音结果保存在一个 Markdown 文件中
- 新增关键词日记功能，每日的以关键词开头的录音结果会保存在特别的 Markdown 文件中
- 新建录音文件夹的时候，会复制一个 Python 辅助脚本，用于清理没有被 Markdown 文件引用的附件，这样一来，通过编辑 Markdown 日记就可以清理不需要保存的录音
- 新增定义录音文件保存目录
- 默认保存48000采样率高品质录音录音，如果用户安装了 FFmpeg 则保存为 mp3 格式，否则保存为 wav 格式
- 输入方式改为模拟 Ctrl + V 粘贴，粘贴完后恢复剪贴板内容
- 使用 rich 库输出彩色文字，尽量在各种终端达到一致的显示效果

## v0.5 

- 修改热词文件后，不用重启客户端，就可以动态更新热词了。

## v0.4

- 为客户端加入了三种热词功能：中文、英文、自定义
- 改进了对中文数字的搜索，当数字的左侧或者右侧有英文时，就一定会被选中。
- 改进了中英空格排版，能够正常输出 iPhone 4s 这样的词语。


## v0.3 

- 客户端当音频设备名不可 utf-8 解码时，不再闪退
- 客户端添加配置可以编辑修改，要消除识别结果末尾哪些标点
- 客户端添加配置可以修改快捷键
- 客户端添加配置可以修改快捷键触发的时间阈值
- 客户端连接中断会自动进行重试
- 客户端提示当前所用的快捷键
- 服务端对识别结果，中英文混排进行空格校正
- 当地址无法被绑定时提示问题，而不是直接闪退


## 起源

**2020年10月**，因手机上的语音输入法很好用，但电脑上却没有足够好用的语音输入法，我手写了一个工具 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)**，通过长按大写锁定键录音，松开后，调用阿里云的一句话识别 API，识别后上屏。12月的时候，还加入图形化界面。但当时大学宿舍的校园网经常抽风，没有稳定的网络环境，转录延迟飘忽不定。有时候说完话了，等了好几秒，才发现 WiFi 没有连接，毁心态。但是当时并没有识别率能满足要求的离线中文 ASR 模型。

后来 Whisper 发布，中文识别率确实不错，但是模型延迟很高，无法满足本地语音输入。

到了**2023年5月**，在B站看到了 [极客湾](https://space.bilibili.com/25876945) 的视频 [我们做了个能对话的AI派蒙，免费给大家玩！](https://www.bilibili.com/video/BV1bm4y117ba/) ，他们实现的效果非常好，其中提到 ASR 模型用了阿里 [FunASR](https://github.com/modelscope/FunASR) 团队发布的开源 [Paraformer](https://www.modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1) 模型，于是就拿来测试了下，果然识别准确率很棒、延迟超低，于是弃坑 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)** ，立马新开了 **[CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)** ，用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 调用 Paraformer 进行转录，效果非常好。

**2025年12月09日**，我看到阿里 [FunASR](https://github.com/modelscope/FunASR) 团队开源了 [Fun-ASR-Nano](https://www.modelscope.cn/models/FunAudioLLM/Fun-ASR-Nano-2512) 模型，拿来一测，果然准确率又上一个台阶，在 sherpa_onnx 支持后，我赶紧更新了 CapsWriter-Offline v2.1，一次性加入了 SenseVoice-Small 和 Fun-ASR-Nano 模型的支持。但问题是当时 sherpa_onnx 是用 ONNX 实现的 Fun-ASR-Nano，速度有些慢。

我研究了 Fun-ASR-Nano 的架构，发现它的 Decoder 是 LLM 架构，而推理 LLM 最快的是 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 。虽然我编程能力差，但刚好此时，Google 推出了 Antigravity AI 编程 IDE，里面有 Gemini 和 Claude 模型，在他们的帮助下，我成功写出了 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF)，用 onnx 和 gguf 格式混合运行 Fun-ASR-Nano，用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分，在我的笔记本上实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.025 |
| CPU U9-285H  | 0.1 |

**2026年01月21日**，[Qwen3-TTS](https://www.modelscope.cn/collections/Qwen/Qwen3-TTS) 开源发布了，生成效果极其优异，让我特别想要，但官方版本推理速度很慢，于是基于 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 的加速推理经验，在 Antigravity 的帮助下，我又实现了 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) ，也是用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分。

**2026年01月28日**，间隔没几天，[Qwen3-ASR](https://www.modelscope.cn/collections/Qwen/Qwen3-ASR) 开源发布了，本来没抱太大预期的，但下载 1.7B 一测后，又给我震惊了，准确率竟比 Fun-ASR-Nano 还上一个台阶，能吊打闭源模型！于是在 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) 经验的帮助下，我马不停蹄地实现了 [Qwen3-ASR-GGUF](https://github.com/HaujetZhao/Qwen3-ASR-GGUF) 加速推理，实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.05 |
| CPU U9-285H  | 0.2 |

这就是 CapsWriter-Offline 大致的来路。总体就是这几个组件：

- 模型推理
- 热词替换
- 按键监听与录音

当前只有 Windows 的打包，是因为我只有 Windows 电脑，没有 Linux 与 MacOS 的需求。在 Vibe Coding 时代，我相信需求的小伙伴在 Claude Code 的帮助下，也能在其系统上做出类似功能的实现。
