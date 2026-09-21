"""Shared validation for OpenAI-compatible audio endpoints."""
import math
from urllib.parse import urlsplit


def validate_api_settings(base_url, model, timeout):
    url = str(base_url).strip().rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("API 地址必须是 http(s) 地址，不能包含密码、查询参数或片段")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程 API 必须使用 HTTPS；HTTP 仅允许本机服务")
    model = str(model).strip()
    if not model:
        raise ValueError("请填写 API 语音识别模型名称")
    try:
        timeout = float(timeout)
    except (ValueError, TypeError):
        raise ValueError("API 超时必须是 1 至 600 秒") from None
    if not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise ValueError("API 超时必须是 1 至 600 秒")
    return url, model, timeout
