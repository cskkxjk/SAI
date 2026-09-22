import os
import json
from pathlib import Path
from core.runtime_paths import APP_DIR, DATA_DIR

# 版本信息
__version__ = '1.0.1'

# 项目根目录
BASE_DIR = str(DATA_DIR)
_GUI_CONFIG = Path(BASE_DIR) / 'config_gui.json'


def _gui_value(name, default):
    try:
        with _GUI_CONFIG.open('r', encoding='utf-8') as f:
            return json.load(f).get(name, default)
    except (OSError, ValueError, TypeError):
        return default


def _resolve_onnx_provider():
    """Prefer a usable GPU execution provider and fall back to CPU."""
    requested = str(_gui_value('onnx_provider', 'AUTO')).upper()
    if requested in ('CPU', 'DML', 'CUDA'):
        return requested
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        if 'CUDAExecutionProvider' in providers:
            return 'CUDA'
        if 'DmlExecutionProvider' in providers:
            return 'DML'
    except Exception:
        pass
    return 'CPU'


# 服务端配置
class ServerConfig:
    addr = '0.0.0.0'
    port = '6016'

    # 语音模型选择：'qwen_asr', 'fun_asr_nano', 'sensevoice', 'paraformer'
    model_type = _gui_value('model_type', 'qwen_asr')
    qwen_quantization = _gui_value('qwen_quantization', 'q5_k')
    if qwen_quantization not in ('q5_k', 'q4_k'):
        qwen_quantization = 'q5_k'
    asr_api_base_url = _gui_value('asr_api_base_url', 'https://api.openai.com/v1')
    asr_api_model = _gui_value('asr_api_model', 'whisper-1')
    asr_api_key = _gui_value('asr_api_key', '')
    asr_api_timeout = _gui_value('asr_api_timeout', 60)
    asr_api_allow_http = _gui_value('asr_api_allow_http', False)

    format_num = True       # 输出时是否将中文数字转为阿拉伯数字
    format_spell = True     # 输出时是否调整中英之间的空格

    enable_tray = _gui_value('child_tray', True)
    hotwords_path = DATA_DIR / 'hot-server.txt' # 全局热词配置文件路径

    # 日志配置
    log_level = 'DEBUG'        # 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    aligner_idle_timeout = 10  # 对齐引擎空闲多少秒后自动释放显存 (0 表示不释放)

    # GPU 预加速配置（有识别任务时，提前调高显存频率，降低延迟，需管理员权限运行）
    gpu_boost_enabled = False
    gpu_boost_cmd = 'nvidia-smi -lmc 9000'      # GPU 预加速命令，锁定显存频率到9000MHz（根据实际 GPU 调整）
    gpu_unboost_cmd = 'nvidia-smi -rmc'         # GPU 取消预加速命令，恢复显存到默认频率
    gpu_unboost_timeout = 1                     # 空闲多少秒后取消加速

    # 集成显卡兼容性补丁
    # os.environ["GGML_VK_DISABLE_COOPMAT"] = "1"   # AMD集显无法加载 GGUF 模型时尝试
    # os.environ["GGML_VK_DISABLE_F16"] = "1"       # 集成显卡解码有误，强制熔断时尝试




class ModelDownloadLinks:
    """模型下载链接配置"""
    # 统一导向 GitHub Release 模型页面
    models_page = "https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models"


class ModelPaths:
    """模型文件路径配置"""

    # 基础目录
    model_dir = APP_DIR / 'models'

    # Paraformer 模型路径
    paraformer_dir = model_dir / 'Paraformer' / "speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx"
    paraformer_model = paraformer_dir / 'model.onnx'
    paraformer_tokens = paraformer_dir / 'tokens.txt'

    # 标点模型路径
    punc_model_dir = model_dir / 'Punct-CT-Transformer' / 'sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12' / 'model.onnx'

    # SenseVoice 模型路径，自带标点
    sensevoice_dir = model_dir / 'SenseVoice-Small' / 'Sensevoice-Small-ONNX'
    sensevoice_encoder = sensevoice_dir / 'SenseVoice-Encoder.fp16.onnx'
    sensevoice_decoder = sensevoice_dir / 'SenseVoice-CTC.fp16.onnx'
    sensevoice_tokenizer = sensevoice_dir / 'tokenizer.bpe.model'
    sensevoice_sherpa_model = model_dir / 'SenseVoice-Small/Sherpa-ONNX/model.int8.onnx'
    sensevoice_sherpa_tokens = model_dir / 'SenseVoice-Small/Sherpa-ONNX/tokens.txt'


    # Fun-ASR-Nano 模型路径，自带标点
    fun_asr_nano_gguf_dir = model_dir / 'Fun-ASR-Nano' / 'Fun-ASR-Nano-GGUF'
    fun_asr_nano_gguf_encoder_adaptor = fun_asr_nano_gguf_dir / 'model/Fun-ASR-Nano-Encoder-Adaptor.fp32.onnx'
    fun_asr_nano_gguf_ctc = fun_asr_nano_gguf_dir / 'model/Fun-ASR-Nano-CTC.int8.onnx'
    fun_asr_nano_gguf_llm_decode = fun_asr_nano_gguf_dir / 'model/Fun-ASR-Nano-Decoder.q8_0.gguf'
    fun_asr_nano_gguf_token = fun_asr_nano_gguf_dir / 'model/tokens.txt'
    fun_asr_nano_gguf_hotwords = DATA_DIR / 'hot-server.txt'

    # Qwen3-ASR 模型路径，自带标点
    qwen3_asr_gguf_dir = model_dir / 'Qwen3-ASR' / 'Qwen3-ASR-1.7B'
    qwen3_asr_gguf_encoder_frontend = qwen3_asr_gguf_dir / 'qwen3_asr_encoder_frontend.onnx'
    qwen3_asr_gguf_encoder_backend = qwen3_asr_gguf_dir / 'qwen3_asr_encoder_backend.onnx'
    qwen3_asr_gguf_llm_decode = qwen3_asr_gguf_dir / f'qwen3_asr_llm.{ServerConfig.qwen_quantization}.gguf'

    # Force-Aligner 模型路径
    force_aligner_gguf_dir = model_dir / 'Qwen3-ForcedAligner' / 'Qwen3-ForcedAligner-0.6B'
    force_aligner_gguf_encoder_frontend = force_aligner_gguf_dir / 'qwen3_aligner_encoder_frontend.int4.onnx'
    force_aligner_gguf_encoder_backend = force_aligner_gguf_dir / 'qwen3_aligner_encoder_backend.int4.onnx'
    force_aligner_gguf_llm_decode = force_aligner_gguf_dir / 'qwen3_aligner_llm.q5_k.gguf'



class ParaformerArgs:
    """Paraformer 模型参数配置"""

    paraformer = ModelPaths.paraformer_model.as_posix()
    tokens = ModelPaths.paraformer_tokens.as_posix()
    num_threads = 4
    sample_rate = 16000
    feature_dim = 80
    decoding_method = 'greedy_search'
    provider = 'cpu'
    debug = False


class SenseVoiceArgs:
    """SenseVoice 模型参数配置"""

    encoder_path = ModelPaths.sensevoice_encoder.as_posix()
    decoder_path = ModelPaths.sensevoice_decoder.as_posix()
    tokenizer_path = ModelPaths.sensevoice_tokenizer.as_posix()
    itn = True                  # 原生输出阿拉伯数字
    onnx_provider = _resolve_onnx_provider()
    top_k = 8                   # 热词检索的 CTC 空间大小
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果


class FunASRNanoGGUFArgs:
    """Fun-ASR-Nano-GGUF 模型参数配置"""

    # 模型路径
    encoder_onnx_path = ModelPaths.fun_asr_nano_gguf_encoder_adaptor.as_posix()
    ctc_onnx_path = ModelPaths.fun_asr_nano_gguf_ctc.as_posix()
    decoder_gguf_path = ModelPaths.fun_asr_nano_gguf_llm_decode.as_posix()
    tokens_path = ModelPaths.fun_asr_nano_gguf_token.as_posix()

    # 显卡加速
    onnx_provider = _resolve_onnx_provider()
    llm_use_gpu = _gui_value('llm_use_gpu', True)
    vulkan_force_fp32 = False   # 是否强制 FP32 计算（如果 GPU 是 Intel 集显且出现精度溢出，可设为 True）
    
    # 模型细节
    enable_ctc = True           # 是否启用 CTC 热词检索
    n_predict = 512             # LLM 最大生成 token 数
    n_threads = None            # 线程数，None 表示自动
    similar_threshold = 0.6     # 热词相似度阈值，超过阈值的热词会被传入 llm decoder 的上下文
    max_hotwords = 20           # 传入上下文的热词数量上限
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果
    verbose = False

class Qwen3ASRGGUFArgs:
    """Qwen3-ASR-GGUF 模型参数配置"""

    # 模型路径
    model_dir = ModelPaths.qwen3_asr_gguf_dir.as_posix()
    encoder_frontend_fn = ModelPaths.qwen3_asr_gguf_encoder_frontend.name
    encoder_backend_fn = ModelPaths.qwen3_asr_gguf_encoder_backend.name
    llm_fn = ModelPaths.qwen3_asr_gguf_llm_decode.name

    # 显卡加速
    onnx_provider = _resolve_onnx_provider()
    llm_use_gpu = _gui_value('llm_use_gpu', True)
    
    # 模型细节
    n_ctx = 2048                # 上下文窗口大小
    chunk_size = 80.0           # 分段长度（秒）
    memory_num = 1              # 记忆段数
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果
    verbose = False


class ForceAlignerGGUFArgs:
    """Force-Aligner-GGUF 模型参数配置"""

    # 模型路径
    model_dir = ModelPaths.force_aligner_gguf_dir.as_posix()
    encoder_frontend_fn = ModelPaths.force_aligner_gguf_encoder_frontend.name
    encoder_backend_fn = ModelPaths.force_aligner_gguf_encoder_backend.name
    llm_fn = ModelPaths.force_aligner_gguf_llm_decode.name

    # 显卡加速
    onnx_provider = 'CPU'       # ONNX 推理后端 (CPU, DML)(推荐保持 DML)
    llm_use_gpu = False          # 是否启用 GPU 加速 GGUF 模型
    
    # 对齐细节
    n_ctx = 4096                # 上下文窗口大小
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果
