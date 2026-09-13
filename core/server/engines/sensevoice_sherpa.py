"""Adapter for the ModelScope sherpa-onnx SenseVoice export."""

from dataclasses import dataclass

import sherpa_onnx

from .base import BaseASREngine, EngineCapabilities
from .paraformer_onnx.asr_engine import ParaformerStream


@dataclass
class SenseVoiceSherpaConfig:
    model: str
    tokens: str


class SenseVoiceSherpaEngine(BaseASREngine):
    def __init__(self, config):
        super().__init__(config)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=config.model, tokens=config.tokens,
            num_threads=4, use_itn=True, provider="cpu", language="auto",
        )

    @property
    def capabilities(self):
        return [EngineCapabilities.ASR, EngineCapabilities.PUNC,
                EngineCapabilities.TIMESTAMPS]

    def create_stream(self, hotwords=None):
        return ParaformerStream(self.recognizer)

    def decode_stream(self, stream, **kwargs):
        self.recognizer.decode_stream(stream.internal_stream)
        result = stream.internal_stream.result
        stream.result.text = result.text
        stream.result.tokens = list(result.tokens)
        stream.result.timestamps = list(result.timestamps)

    def update_hotwords(self, hotwords):
        pass

    def cleanup(self):
        self.recognizer = None
