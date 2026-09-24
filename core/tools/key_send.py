# coding: utf-8
"""跨平台按键发送工具。

Windows / Linux 使用 ``keyboard`` 库（支持快速键入与组合键），
macOS 使用 ``pynput`` 的 Controller（``keyboard`` 库不支持 macOS）。

对外暴露：
    write(text)               逐字键入文本
    press_and_release(combo)  发送组合键，如 'enter'、'cmd+c'
    pressed_keys()            尽力返回当前按下的键名列表（仅用于日志）
"""
from __future__ import annotations

import sys

IS_DARWIN = sys.platform == "darwin"

if IS_DARWIN:
    from pynput.keyboard import Controller as _Controller, Key as _Key

    _controller = _Controller()

    # 组合键里出现的修饰键名 -> pynput Key
    _NAMED = {
        "ctrl": _Key.ctrl, "control": _Key.ctrl, "ctrl_l": _Key.ctrl_l, "ctrl_r": _Key.ctrl_r,
        "alt": _Key.alt, "option": _Key.alt, "alt_l": _Key.alt_l, "alt_r": _Key.alt_r,
        "shift": _Key.shift, "shift_l": _Key.shift_l, "shift_r": _Key.shift_r,
        "cmd": _Key.cmd, "win": _Key.cmd, "super": _Key.cmd,
        "cmd_l": _Key.cmd_l, "cmd_r": _Key.cmd_r,
        "enter": _Key.enter, "return": _Key.enter, "tab": _Key.tab,
        "esc": _Key.esc, "space": _Key.space,
    }
else:
    import keyboard  # noqa: F401  (Windows / Linux)


def write(text: str) -> None:
    """逐字键入文本。"""
    if not text:
        return
    if IS_DARWIN:
        _controller.type(text)
    else:
        keyboard.write(text)


def press_and_release(combo: str) -> None:
    """发送组合键。

    combo 形如 'enter'、'cmd+c'、'ctrl+shift+a'。大小写不敏感。
    """
    if not combo:
        return
    combo = combo.strip().lower()
    if not IS_DARWIN:
        keyboard.press_and_release(combo)
        return

    parts = [p for p in combo.split("+") if p]
    keys = []
    for part in parts:
        if part in _NAMED:
            keys.append(_NAMED[part])
        elif len(part) == 1:
            keys.append(part)
        else:
            # 未知名称，尽量按原样交给 pynput（可能抛错，由调用方兜底）
            keys.append(part)

    for key in keys:
        _controller.press(key)
    for key in reversed(keys):
        _controller.release(key)


def pressed_keys() -> list:
    """返回当前按下的键名列表；macOS 上无法可靠获取，返回空列表。"""
    if IS_DARWIN:
        return []
    try:
        return list(keyboard._pressed_events.keys())
    except Exception:
        return []
