"""Shared validation for OpenAI-compatible audio endpoints."""
import ipaddress
import math
from urllib.parse import urlsplit

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain"}


def _normalize(host):
    return str(host or "").strip().strip("[]").lower()


def is_loopback_host(host):
    """True for loopback host names and addresses such as 127.0.0.1."""
    host = _normalize(host)
    if host in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback


def is_local_host(host):
    """True for loopback, private and link-local hosts."""
    host = _normalize(host)
    if host in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def bypass_proxy(base_url, allow_http=False):
    """LAN services and trusted plain-HTTP endpoints must connect directly.

    A system-wide proxy would otherwise capture traffic that never leaves
    the local network, which usually fails or leaks the audio upload.
    """
    parsed = urlsplit(str(base_url).strip())
    if is_local_host(parsed.hostname):
        return True
    return parsed.scheme == "http" and bool(allow_http)


def validate_api_settings(base_url, model, timeout, allow_http=False):
    url = str(base_url).strip().rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("API 地址必须是 http(s) 地址，不能包含密码、查询参数或片段")
    if (parsed.scheme == "http" and not allow_http
            and not is_loopback_host(parsed.hostname)):
        raise ValueError("远程 API 必须使用 HTTPS；如需连接可信局域网 HTTP 服务，"
                         "请开启“允许明文 HTTP”")
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
