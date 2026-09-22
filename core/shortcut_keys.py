"""Canonical keys shared by capture and the recording listener."""

MODIFIERS = ("ctrl", "alt", "shift", "win")
ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "control_l": "ctrl", "control_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win",
    "super_l": "win", "super_r": "win", "win_l": "win", "win_r": "win",
}


def canonical_key(name):
    return ALIASES.get(name.lower(), name.lower())


def shortcut_label(key):
    names = {"caps_lock": "CapsLock", "ctrl": "Ctrl", "alt": "Alt",
             "shift": "Shift", "win": "Win", "x1": "鼠标侧键 1",
             "x2": "鼠标侧键 2", "middle": "鼠标中键", "space": "Space"}
    return " + ".join(names.get(part, part.upper()) for part in key.split("+")) if key else "未设置"


class KeyCapture:
    """Commit a chord on release, including modifier-only chords."""
    def __init__(self):
        self.down = set()
        self.chord = set()

    def press(self, name):
        self.down.add(name)
        self.chord = {canonical_key(key) for key in self.down}

    def release(self, name):
        if name not in self.down:
            return None
        self.down.discard(name)
        parts = [key for key in MODIFIERS if key in self.chord]
        parts += sorted(self.chord.difference(MODIFIERS))
        return "+".join(parts)
