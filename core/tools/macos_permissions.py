# coding: utf-8
"""macOS 权限自检。

macOS 需要用户在「系统设置 > 隐私与安全性」中手动授予以下权限，
程序无法自动开启：

- 输入监控 (Input Monitoring)：全局监听按键（pynput Listener）
- 辅助功能 (Accessibility)：模拟按键上屏/粘贴、读取前台窗口
- 麦克风 (Microphone)：采集录音

本模块仅做只读检测，用于启动时给出明确提示。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys


def check() -> dict:
    """返回 macOS 权限状态字典；非 macOS 返回空字典。"""
    if sys.platform != "darwin":
        return {}

    result: dict = {}

    # 输入监控（监听按键）
    try:
        import Quartz  # type: ignore
        result["input_monitoring"] = bool(Quartz.CGPreflightListenEventAccess())
    except Exception as exc:  # pragma: no cover
        result["input_monitoring"] = None
        result["quartz_error"] = str(exc)

    # 辅助功能（模拟按键、控制其他 App）
    try:
        path = ctypes.util.find_library("ApplicationServices")
        services = ctypes.CDLL(path)
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        result["accessibility"] = bool(services.AXIsProcessTrusted())
    except Exception as exc:  # pragma: no cover
        result["accessibility"] = None
        result["ax_error"] = str(exc)

    return result


def missing() -> list:
    """返回缺失的权限名称列表。"""
    status = check()
    return [name for name in ("input_monitoring", "accessibility")
            if status.get(name) is False]


def describe() -> str:
    """生成人类可读的权限状态说明（多行）。"""
    status = check()
    if not status:
        return ""
    labels = {
        "input_monitoring": "输入监控 (Input Monitoring)",
        "accessibility": "辅助功能 (Accessibility)",
    }
    lines = []
    for key, label in labels.items():
        value = status.get(key)
        mark = "✓" if value else ("?" if value is None else "✗")
        lines.append(f"  [{mark}] {label}")
    return "\n".join(lines)
