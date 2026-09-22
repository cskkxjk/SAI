"""Audio transcription adapter; does not retry potentially billable uploads."""
import io
import os
import wave
from dataclasses import dataclass, field

import numpy as np
import requests

from core.api_transcription_config import bypass_proxy, validate_api_settings
from .base import BaseASREngine, EngineCapabilities, RecognitionStream


@dataclass
class APIConfig:
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout: float = 60
    allow_http: bool = False


class APIStream(RecognitionStream):
    def __init__(self):
        super().__init__()
        self.chunks = []

    def accept_waveform(self, sample_rate, audio):
        if self.chunks and sample_rate != self.sample_rate:
            raise ValueError("Audio sample rate changed within a request")
        self.sample_rate = sample_rate
        self.chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1).copy())


class OpenAIASREngine(BaseASREngine):
    def __init__(self, config):
        super().__init__(config)
        self.base_url, self.model, self.timeout = validate_api_settings(
            config.base_url, config.model, config.timeout, config.allow_http)
        self.direct = bypass_proxy(self.base_url, config.allow_http)
        self.key = config.api_key.strip() or os.environ.get("SAI_ASR_API_KEY", "")

    @property
    def capabilities(self):
        return [EngineCapabilities.ASR, EngineCapabilities.PUNC]

    def create_stream(self, hotwords=None):
        return APIStream()

    def decode_stream(self, stream, context=None, **kwargs):
        if not stream.chunks:
            return
        samples = np.concatenate(stream.chunks)
        if not samples.size:
            return
        pcm = (np.clip(np.nan_to_num(samples), -1, 1) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(stream.sample_rate)
            wav.writeframes(pcm.tobytes())
        data = {"model": self.model, "response_format": "json"}
        language = kwargs.get("language")
        languages = {"chinese": "zh", "english": "en", "japanese": "ja",
                     "korean": "ko", "cantonese": "zh"}
        if language and language != "auto":
            data["language"] = languages.get(language.lower(), language)
        if context:
            data["prompt"] = context
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        endpoint = self.base_url
        if not endpoint.endswith("/audio/transcriptions"):
            endpoint += "/audio/transcriptions"
        try:
            response = requests.post(
                endpoint, headers=headers, data=data,
                files={"file": ("recording.wav", buf.getvalue(), "audio/wav")},
                timeout=(min(10, self.timeout), self.timeout), allow_redirects=False,
                proxies={"http": None, "https": None} if self.direct else None)
        except requests.Timeout as exc:
            raise RuntimeError(
                f"语音 API 请求超时（{endpoint}）：{exc}；请检查服务或增加超时时间") from None
        except requests.RequestException as exc:
            raise RuntimeError(
                f"语音 API 连接失败（{endpoint}）：{exc.__class__.__name__}: {exc}"
                "；请检查地址端口、网络和证书") from None
        try:
            if response.status_code != 200:
                hints = {401: "密钥无效", 403: "无访问权限", 404: "地址或模型不存在",
                         413: "录音过长", 429: "额度不足或请求过于频繁"}
                hint = hints.get(response.status_code, "服务请求失败")
                raise RuntimeError(f"语音 API HTTP {response.status_code}（{endpoint}）：{hint}")
            try:
                body = response.json()
            except ValueError:
                raise RuntimeError("语音 API 返回的不是 JSON") from None
            if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                raise RuntimeError("语音 API 响应缺少 text 字符串")
            stream.result.text = body["text"].strip()
            stream.result.duration = samples.size / stream.sample_rate
        finally:
            response.close()

    def cleanup(self):
        pass
