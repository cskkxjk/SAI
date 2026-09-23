# coding: utf-8
"""释放可能残留的修饰键。

模拟按键注入（粘贴用的 Ctrl+V、逐字键入时的 Shift 等）在进程被强杀时，
可能只发出按下事件、来不及发出抬起事件，系统就会一直认为该修饰键被按住，
表现得像“键盘打字变成了快捷键”。客户端启动时收尾一次即可。
"""

from __future__ import annotations

import ctypes
import sys
from typing import List

KEYEVENTF_KEYUP = 0x0002

# (虚拟键码, 名称)
_MODIFIERS = (
    (0x10, "Shift"), (0x11, "Ctrl"), (0x12, "Alt"),
    (0x5B, "Win"), (0x5C, "Win(右)"),
    (0xA0, "左 Shift"), (0xA1, "右 Shift"),
    (0xA2, "左 Ctrl"), (0xA3, "右 Ctrl"),
    (0xA4, "左 Alt"), (0xA5, "右 Alt"),
)


def release_stuck_modifiers() -> List[str]:
    """检测并释放逻辑上仍被按住的修饰键，返回被释放的键名列表"""
    if sys.platform != "win32":
        return []

    try:
        user32 = ctypes.windll.user32
    except (OSError, AttributeError):
        return []

    released = []
    for vk, name in _MODIFIERS:
        try:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                released.append(name)
        except OSError:
            continue
    return released
