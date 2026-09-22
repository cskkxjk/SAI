"""ONNX sessions with GPU initialization and execution fallback."""

import logging
from pathlib import Path

import onnxruntime as ort

logger = logging.getLogger("server")


class OnnxSession:
    def __init__(self, model_path, provider="CPU"):
        self.model_path = str(model_path)
        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        preferred = {
            "CUDA": "CUDAExecutionProvider",
            "DML": "DmlExecutionProvider",
            "TRT": "TensorrtExecutionProvider",
            "TENSORRT": "TensorrtExecutionProvider",
        }.get(provider.upper())
        if preferred in available:
            if preferred == "TensorrtExecutionProvider":
                providers.insert(0, (preferred, {
                    "trt_fp16_enable": True,
                    "trt_engine_cache_enable": True,
                    "trt_engine_cache_path": str(Path(model_path).parent / "trt_cache"),
                }))
            else:
                providers.insert(0, preferred)
        self._gpu_active = len(providers) > 1
        self._session = None
        try:
            self._session = self._create(providers)
        except Exception as exc:
            if not self._gpu_active:
                raise
            self._fallback(exc)
        logger.info("[ONNX] %s: %s", Path(model_path).name, self.get_providers())

    def _create(self, providers):
        options = ort.SessionOptions()
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if "DmlExecutionProvider" in providers:
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        return ort.InferenceSession(
            self.model_path, sess_options=options, providers=providers,
        )

    @staticmethod
    def _describe(exc):
        """显卡驱动返回的报错文本是本机语言，pybind 按 UTF-8 解码会失败。"""
        message = str(exc).strip()
        if isinstance(exc, UnicodeDecodeError) or "codec can't decode" in message:
            return ("显卡驱动返回的报错文本无法解码（非 UTF-8），"
                    "通常是驱动或 ONNX Runtime 不支持该模型的算子")
        return message

    def _fallback(self, exc):
        logger.warning("[ONNX] %s GPU 不可用，已回退 CPU 运行: %s",
                       Path(self.model_path).name, self._describe(exc))
        logger.debug("[ONNX] GPU 执行失败详情", exc_info=True)
        self._session = None
        self._gpu_active = False
        self._session = self._create(["CPUExecutionProvider"])

    def run(self, output_names, feeds):
        try:
            return self._session.run(output_names, feeds)
        except Exception as exc:
            if not self._gpu_active:
                raise
            self._fallback(exc)
            return self._session.run(output_names, feeds)

    def get_inputs(self):
        return self._session.get_inputs()

    def get_outputs(self):
        return self._session.get_outputs()

    def get_providers(self):
        return self._session.get_providers()
