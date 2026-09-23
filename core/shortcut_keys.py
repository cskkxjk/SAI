"""Canonical keys shared by capture and the recording listener."""

MODIFIERS = ("ctrl", "alt", "shift", "win")
SIDED_MODIFIERS = ("ctrl_l", "ctrl_r", "alt_l", "alt_r",
                   "shift_l", "shift_r", "win_l", "win_r")
# 组合键展示/提交顺序：先修饰键（左在前），其余按键按字典序
CHORD_ORDER = SIDED_MODIFIERS + MODIFIERS

# 监听/捕获侧：pynput 报出的名字 -> 规范名
# Windows 上左右修饰键会分别报 ctrl_l/ctrl_r、shift(左)/shift_r、alt_l/alt_gr、cmd(左)/cmd_r
PRESSED_ALIASES = {
    "control": "ctrl_l", "control_l": "ctrl_l", "lctrl": "ctrl_l", "ctrl": "ctrl_l",
    "control_r": "ctrl_r", "rctrl": "ctrl_r",
    "alt": "alt_l", "lalt": "alt_l",
    "alt_gr": "alt_r", "ralt": "alt_r",
    "shift": "shift_l",
    "win": "win_l", "cmd": "win_l", "cmd_l": "win_l",
    "super": "win_l", "super_l": "win_l",
    "cmd_r": "win_r", "super_r": "win_r",
}

# 配置侧：写进 config_gui.json 的名字 -> 规范名
# 泛名（ctrl/alt/shift/win）保留“任意一侧”语义
CONFIG_ALIASES = {
    "control": "ctrl", "control_l": "ctrl_l", "lctrl": "ctrl_l", "left_ctrl": "ctrl_l",
    "control_r": "ctrl_r", "rctrl": "ctrl_r", "right_ctrl": "ctrl_r",
    "lalt": "alt_l", "left_alt": "alt_l",
    "alt_gr": "alt_r", "ralt": "alt_r", "right_alt": "alt_r",
    "left_shift": "shift_l", "right_shift": "shift_r",
    "cmd": "win", "super": "win",
    "cmd_l": "win_l", "cmd_r": "win_r", "super_l": "win_l", "super_r": "win_r",
    "lwin": "win_l", "rwin": "win_r", "left_win": "win_l", "right_win": "win_r",
}

LABELS = {
    "caps_lock": "CapsLock",
    "ctrl": "Ctrl", "ctrl_l": "左 Ctrl", "ctrl_r": "右 Ctrl",
    "alt": "Alt", "alt_l": "左 Alt", "alt_r": "右 Alt",
    "shift": "Shift", "shift_l": "左 Shift", "shift_r": "右 Shift",
    "win": "Win", "win_l": "左 Win", "win_r": "右 Win",
    "x1": "鼠标侧键 1", "x2": "鼠标侧键 2", "middle": "鼠标中键", "space": "Space",
}


def canonical_key(name):
    """监听/捕获到的按键名 -> 规范名（区分左右）"""
    name = (name or "").lower()
    return PRESSED_ALIASES.get(name, name)


def normalize_part(part):
    """配置里的单个按键 -> 规范名（泛名保留“任意一侧”语义）"""
    part = (part or "").lower()
    return CONFIG_ALIASES.get(part, part)


def matching_names(part):
    """配置项 part 能匹配的、已规范化的按键名集合"""
    if part in MODIFIERS:
        return {part, part + "_l", part + "_r"}
    return {part}


def combo_active(parts, pressed):
    """parts 里的每个按键都被按下（pressed 为已规范化的按键名集合）"""
    return all(pressed.intersection(matching_names(part)) for part in parts)


def join_chord(names):
    """把一组按键名拼成规范顺序的组合键字符串"""
    unique = {canonical_key(name) for name in names}
    ordered = [key for key in CHORD_ORDER if key in unique]
    ordered += sorted(unique.difference(CHORD_ORDER))
    return "+".join(ordered)


def shortcut_label(key):
    if not key:
        return "未设置"
    return " + ".join(LABELS.get(part, part.upper()) for part in key.split("+"))


class KeyCapture:
    """Commit a chord on release, including modifier-only chords."""
    def __init__(self):
        self.down = set()
        self.chord = set()

    def press(self, name):
        self.down.add(canonical_key(name))
        self.chord = set(self.down)

    def release(self, name):
        name = canonical_key(name)
        if name not in self.down:
            return None
        self.down.discard(name)
        return join_chord(self.chord)
