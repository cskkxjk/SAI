"""Desktop editor for recorded voice phrases (语音短语).

页面上没有录音按钮：停留在「语音短语」页且窗口有焦点时，录音键直接录制
新短语（按住说话、松开完成，0.5~4 秒，超时自动停止），音频留在页面里
试听/保存/放弃。录制期间写入占用标记，正在运行的识别客户端会忽略录音键，
窗口失焦或离开本页时立即让出录音键，不影响正常听写。
"""

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import numpy as np

from core.desktop_widgets import CARD_BG, PillButton
from core.runtime_paths import DATA_DIR
from core.shortcut_keys import (canonical_key, combo_active,
                                darwin_suppress_intercept, matching_names,
                                normalize_part, shortcut_label)
from core.client.voice_phrase.storage import MAX_SAMPLES, VoicePhraseStore

SAMPLE_RATE = 16000
MIN_SECONDS = 0.5
MAX_SECONDS = 4.0
MAX_PHRASES = 50
SILENCE_PEAK = 0.001
LOW_VOLUME_PEAK = 0.05
POLL_MS = 25
FLAG_REFRESH_MS = 30000  # 占用标记 ts 刷新间隔（客户端超过 120s 视为残留）
# 面板兜底录音键：macOS 的 CapsLock 只发瞬时事件、无法按住说话，退回右 Option
DEFAULT_SHORTCUT = {"key": "alt_r" if sys.platform == "darwin" else "caps_lock",
                    "type": "keyboard"}
CAPTURE_FLAG = DATA_DIR / "logs" / "voice-capture.flag"


def recording_shortcut(config_path=None):
    """主录音快捷键：config_gui.json 里第一个非粘贴快捷键。"""
    from core.client.shortcut.shortcut_config import load_shortcuts

    path = config_path or (DATA_DIR / "config_gui.json")
    try:
        shortcuts = load_shortcuts(path)
    except Exception:
        shortcuts = []
    for shortcut in shortcuts:
        if shortcut.enabled and not getattr(shortcut, "paste", False):
            return {"key": shortcut.key, "type": shortcut.type}
    return dict(DEFAULT_SHORTCUT)


def _read_audio(path):
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, rate


def play_audio(audio, rate=SAMPLE_RATE):
    """用系统默认输出设备播放一段音频。

    不能复用录音设备：输入端点通常没有输出通道，会报
    “Invalid number of channels”（PaErrorCode -9998）；
    单声道在部分设备上也不被接受，按默认设备能力复制成立体声。
    """
    import sounddevice as sd

    data = np.asarray(audio, dtype=np.float32).reshape(-1, 1)
    if not data.size:
        return
    try:
        info = sd.query_devices(kind="output")
        channels = int(info.get("max_output_channels") or 1)
    except Exception:
        channels = 1
    if channels >= 2:
        data = np.repeat(data, 2, axis=1)
    sd.play(data, rate)


def _resample(audio, source_rate, target_rate):
    """线性插值重采样（蓝牙耳机常只支持 48k，需要转回 16k 模板）。"""
    if source_rate == target_rate or not audio.size:
        return audio
    frames = max(1, round(audio.size * target_rate / source_rate))
    source_x = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    target_x = np.linspace(0.0, 1.0, frames, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def _candidate_devices(selected):
    """录音候选设备：所选设备 -> 同名其它主机接口 -> 系统默认设备。"""
    import sounddevice as sd

    from core.audio_devices import HOST_PRIORITY, input_devices

    try:
        devices = input_devices()
    except Exception:
        devices = []
    chosen = next((item for item in devices if item.get("index") == selected), None)
    names = [chosen.get("name")] if chosen else []
    try:
        default = sd.query_devices(kind="input")
    except Exception:
        default = None
    if default is not None and default.get("name") not in names:
        names.append(default["name"])
    candidates = [selected]
    for name in names:
        same = sorted(
            (item for item in devices
             if item.get("name") == name and item.get("hostapi") != "Windows WDM-KS"),
            key=lambda item: HOST_PRIORITY.get(item.get("hostapi"), 99))
        candidates.extend(item.get("index") for item in same)
    ordered = []
    for value in candidates + [None]:
        if value not in ordered:
            ordered.append(value)
    return ordered


class _HotkeyListener:
    """只匹配一个录音快捷键的全局监听。

    Win32 下用 win32_event_filter 屏蔽匹配到的按键，避免录音键触发系统
    行为（例如 CapsLock 切换）；macOS 下用 darwin_intercept 达到同样效果，
    鼠标快捷键会额外跟踪键盘修饰键。
    """

    def __init__(self, shortcut, on_press, on_release):
        self.key = str((shortcut or {}).get("key") or "")
        self.kind = (shortcut or {}).get("type", "keyboard")
        parts = [normalize_part(part) for part in self.key.split("+") if part]
        self.parts = parts
        self.trigger = parts[-1] if parts else ""
        self.modifiers = parts[:-1]
        self.on_press = on_press
        self.on_release = on_release
        self.pressed = set()
        self.holding = False
        self.darwin_suppress = False
        self.listeners = []
        self.keyboard_listener = None
        self.mouse_listener = None

    # ------------------------------------------------------------- 生命周期
    def start(self):
        from pynput import keyboard, mouse

        win32 = os.name == "nt"
        options = ({"darwin_intercept": darwin_suppress_intercept(self)}
                   if sys.platform == "darwin" else {})
        if self.kind == "mouse":
            self.mouse_listener = (
                mouse.Listener(win32_event_filter=self._mouse_filter) if win32
                else mouse.Listener(on_click=self._mouse_click, **options))
            self.listeners.append(self.mouse_listener)
            if self.modifiers:
                self.keyboard_listener = (
                    keyboard.Listener(win32_event_filter=self._track_filter) if win32
                    else keyboard.Listener(on_press=self._track_press,
                                           on_release=self._track_release,
                                           **options))
                self.listeners.append(self.keyboard_listener)
        else:
            self.keyboard_listener = (
                keyboard.Listener(win32_event_filter=self._keyboard_filter) if win32
                else keyboard.Listener(on_press=self._keyboard_press,
                                       on_release=self._keyboard_release,
                                       **options))
            self.listeners.append(self.keyboard_listener)
        for listener in self.listeners:
            listener.start()

    def stop(self):
        for listener in self.listeners:
            try:
                listener.stop()
            except Exception:
                pass
        self.listeners = []
        self.keyboard_listener = None
        self.mouse_listener = None
        self.pressed.clear()
        self.holding = False
        self.darwin_suppress = False

    # ------------------------------------------------------------- 内部工具
    @staticmethod
    def _key_name(key):
        from pynput import keyboard

        if isinstance(key, keyboard.Key):
            return key.name
        return getattr(key, "char", None) or str(key).replace("'", "").lower()

    @staticmethod
    def _mouse_name(msg, data):
        from core.client.shortcut.key_mapper import (
            WM_MBUTTONDOWN, WM_MBUTTONUP, XBUTTON1)

        if msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            return "middle"
        xbutton = (data.mouseData >> 16) & 0xFFFF
        return "x1" if xbutton == XBUTTON1 else "x2"

    def _suppress(self, listener):
        if listener is None:
            return
        try:
            listener.suppress_event()
        except Exception:
            pass

    def _trigger_press(self):
        if self.holding:
            return
        self.holding = True
        self.on_press()

    def _trigger_release(self):
        if not self.holding:
            return
        self.holding = False
        self.on_release()

    # ------------------------------------------------------------- Win32 键盘
    def _keyboard_filter(self, msg, data):
        from core.client.shortcut.key_mapper import (
            KEYBOARD_MESSAGES, KEY_DOWN_MESSAGES, KEY_UP_MESSAGES, KeyMapper)

        if msg not in KEYBOARD_MESSAGES:
            return True
        name = canonical_key(KeyMapper.vk_to_name(data.vkCode))
        if msg in KEY_DOWN_MESSAGES:
            self.pressed.add(name)
            if name in matching_names(self.trigger) and combo_active(self.parts, self.pressed):
                self._trigger_press()
                self._suppress(self.keyboard_listener)
        elif msg in KEY_UP_MESSAGES:
            self.pressed.discard(name)
            if any(name in matching_names(part) for part in self.parts):
                self._trigger_release()
                self._suppress(self.keyboard_listener)
        return True

    def _track_filter(self, msg, data):
        from core.client.shortcut.key_mapper import (
            KEYBOARD_MESSAGES, KEY_DOWN_MESSAGES, KEY_UP_MESSAGES, KeyMapper)

        if msg not in KEYBOARD_MESSAGES:
            return True
        name = canonical_key(KeyMapper.vk_to_name(data.vkCode))
        if msg in KEY_DOWN_MESSAGES:
            self.pressed.add(name)
        elif msg in KEY_UP_MESSAGES:
            self.pressed.discard(name)
        return True

    # ------------------------------------------------------------- 其他平台键盘
    def _keyboard_press(self, key):
        name = canonical_key(self._key_name(key))
        self.pressed.add(name)
        if name in matching_names(self.trigger) and combo_active(self.parts, self.pressed):
            self._trigger_press()
            self.darwin_suppress = True

    def _keyboard_release(self, key):
        name = canonical_key(self._key_name(key))
        self.pressed.discard(name)
        if any(name in matching_names(part) for part in self.parts):
            self._trigger_release()
            self.darwin_suppress = True

    def _track_press(self, key):
        self.pressed.add(canonical_key(self._key_name(key)))

    def _track_release(self, key):
        self.pressed.discard(canonical_key(self._key_name(key)))

    # ------------------------------------------------------------- 鼠标
    def _mouse_filter(self, msg, data):
        from core.client.shortcut.key_mapper import (
            MOUSE_MESSAGES, WM_MBUTTONDOWN, WM_MBUTTONUP, WM_XBUTTONDOWN,
            WM_XBUTTONUP)

        if msg not in MOUSE_MESSAGES:
            return True
        name = self._mouse_name(msg, data)
        if name != self.trigger:
            return True
        if msg in (WM_XBUTTONDOWN, WM_MBUTTONDOWN):
            if combo_active(self.modifiers, self.pressed):
                self._trigger_press()
                self._suppress(self.mouse_listener)
        elif msg in (WM_XBUTTONUP, WM_MBUTTONUP):
            self._trigger_release()
            self._suppress(self.mouse_listener)
        return True

    def _mouse_click(self, _x, _y, button, pressed):
        name = getattr(button, "name", "")
        if name != self.trigger:
            return
        if pressed:
            if combo_active(self.modifiers, self.pressed):
                self._trigger_press()
                self.darwin_suppress = True
        else:
            self._trigger_release()
            self.darwin_suppress = True


class VoiceCapture:
    """录音键驱动的单段录音采集（短语录入与语音测试共用）。

    - arm() 时后台打开麦克风并等首个数据（蓝牙耳机要 0.6s 以上才出声），
      空闲期间的数据直接丢弃，按键后立刻开始采集，不会漏掉开头；
    - pynput 事件经队列转投，所有回调都在 Tk 主线程执行；
    - 监听期间写入占用标记文件，识别客户端会忽略录音键并让出麦克风；
    - disarm() 只停止监听并保留音频，cancel() 连音频一起丢弃。
    """

    def __init__(self, widget, device=None, flag_path=None, on_recording=None):
        self.widget = widget
        self.device = device
        self.flag_path = Path(flag_path) if flag_path else CAPTURE_FLAG
        self.on_recording = on_recording
        self.audio = np.zeros(0, dtype=np.float32)
        self.owner = None
        self._listener = None
        self._events = queue.Queue()
        self._poll = None
        self._stream = None
        self._stream_rate = SAMPLE_RATE
        self._open_queue = queue.Queue()
        self._open_job = None
        self._open_generation = 0
        self._opening = False
        self._open_error = None
        self._chunks = []
        self._data_seen = False
        self._started = 0.0
        self._timer = None
        self._armed = False
        self._flag_job = None
        self._recording = False
        self._key_down = False
        self._pending_start = False
        self._label = "录音键"
        self._ready_text = None
        self._status = lambda _text: None
        self._captured = lambda _audio: None
        self._started_cb = None

    @property
    def armed(self):
        return self._armed

    @property
    def recording(self):
        return self._recording

    @property
    def duration(self):
        return self.audio.size / SAMPLE_RATE

    def _idle_text(self):
        return f"按住 {self._label} 说话即可录制（{MIN_SECONDS:.1f}~{MAX_SECONDS:.0f} 秒）"

    # ------------------------------------------------------------- 生命周期
    def arm(self, shortcut, owner=None, status=None, captured=None, started=None,
            ready_text=None):
        """开始监听录音键并后台预热麦克风；返回是否成功。"""
        self.disarm()
        self._status = status or (lambda _text: None)
        self._captured = captured or (lambda _audio: None)
        self._started_cb = started
        self._ready_text = ready_text
        label = shortcut_label((shortcut or {}).get("key", ""))
        self._label = "录音键" if label in ("", "未设置") else label
        self.owner = owner
        # macOS 上 CapsLock 是系统切换键，pynput 只报一次瞬时事件，
        # 无法实现按住说话；提前提示用户换键（客户端默认录音键是右 Option）
        if sys.platform == "darwin" and (shortcut or {}).get("key") == "caps_lock":
            self._ready_text = f"macOS 上 {self._label} 无法按住说话，请在「设置」里更换录音键"
        self._write_flag(True)
        try:
            self._listener = _HotkeyListener(shortcut, self._queue_press,
                                             self._queue_release)
            self._listener.start()
        except Exception as exc:
            self._listener = None
            self.owner = None
            self._write_flag(False)
            self._status(f"无法监听录音键：{exc}")
            return False
        self._armed = True
        self._data_seen = False
        self._schedule_flag_refresh()
        self._poll = self.widget.after(POLL_MS, self._drain)
        self._start_opening()
        return True

    def set_owner(self, owner, status=None, captured=None, ready_text=None):
        """切换录制用途（不重新打开设备、不打断已开始的录音）。"""
        self.owner = owner
        if status is not None:
            self._status = status
        if captured is not None:
            self._captured = captured
        self._ready_text = ready_text
        if self._armed and not self._recording and not self._opening:
            self._status(self._ready_text or self._idle_text())

    def disarm(self):
        """停止监听、关闭麦克风并删除占用标记，保留已录到的音频。"""
        if self._poll is not None:
            try:
                self.widget.after_cancel(self._poll)
            except Exception:
                pass
            self._poll = None
        if self._open_job is not None:
            try:
                self.widget.after_cancel(self._open_job)
            except Exception:
                pass
            self._open_job = None
        self._open_generation += 1  # 使仍在后台打开的流作废
        if self._flag_job is not None:
            try:
                self.widget.after_cancel(self._flag_job)
            except Exception:
                pass
            self._flag_job = None
        if self._timer is not None:
            try:
                self.widget.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        self._close_stream()
        while True:
            try:
                event, _generation, value = self._open_queue.get_nowait()
            except queue.Empty:
                break
            if event == "ok":
                try:
                    value[0].stop()
                    value[0].close()
                except Exception:
                    pass
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        if self._armed:
            self._write_flag(False)
        self._armed = False
        self._recording = False
        self._opening = False
        self._pending_start = False
        self._key_down = False
        self.owner = None
        self._chunks = []
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break

    def cancel(self):
        """停止监听并丢弃音频。"""
        self.disarm()
        self.audio = np.zeros(0, dtype=np.float32)

    def discard(self):
        self.audio = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------- 事件循环
    def _queue_press(self):
        self._events.put("press")

    def _queue_release(self):
        self._events.put("release")

    def _drain(self):
        self._poll = None
        while self._armed and not self._events.empty():
            event = self._events.get_nowait()
            if event == "press":
                self._key_down = True
                self._begin()
            elif event == "release":
                self._key_down = False
                self._pending_start = False
                self._finish()
        if self._armed:
            self._poll = self.widget.after(POLL_MS, self._drain)

    # ------------------------------------------------------------- 设备预热
    def _start_opening(self):
        """后台打开麦克风并等首个数据，避免按键时才开始预热。"""
        self._opening = True
        self._open_error = None
        self._open_generation += 1
        generation = self._open_generation
        self._status("正在准备录音设备…")

        def work():
            try:
                stream, rate = self._open_stream()
            except Exception as exc:
                self._open_queue.put(("error", generation, str(exc)))
                return
            self._open_queue.put(("ok", generation, (stream, rate)))

        threading.Thread(target=work, name="voice-capture-open", daemon=True).start()
        self._open_job = self.widget.after(POLL_MS, self._poll_open)

    def _poll_open(self):
        self._open_job = None
        while True:
            try:
                event, generation, value = self._open_queue.get_nowait()
            except queue.Empty:
                self._open_job = self.widget.after(POLL_MS, self._poll_open)
                return
            if generation == self._open_generation:
                break
            # disarm/重新 arm 后作废的流：线程可能刚交付，这里负责收尾
            if event == "ok":
                stream, _rate = value
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self._opening = False
        if event == "error":
            self._open_error = value
            self._status(f"无法打开录音设备：{value}")
            return
        stream, rate = value
        if not self._armed:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            return
        self._stream = stream
        self._stream_rate = rate
        self._status(self._ready_text or self._idle_text())
        if self._pending_start and self._key_down:
            self._pending_start = False
            self._begin()

    # ------------------------------------------------------------- 录音
    def _begin(self):
        if self._recording:
            return
        if self._stream is None:
            if self._opening:
                self._pending_start = True
                self._status("录音设备正在准备中，请稍候…")
            else:
                self._status(f"录音设备不可用：{self._open_error or '未打开'}")
            return
        self._chunks = []
        self._started = time.monotonic()
        self._recording = True
        self._notify_recording(True)
        if self._started_cb is not None:
            self._started_cb()
        self._tick()

    def _open_stream(self):
        """按候选设备、采样率、延迟逐个尝试，兼容蓝牙耳机等挑剔设备。

        打开成功后等待首个音频数据（蓝牙耳机切到通话模式要 0.6s 以上），
        确认能出声才接受，避免“能打开但一直没数据”的设备。
        """
        import sounddevice as sd

        callback = self._make_callback()
        last_error = None
        budget = [6.0]  # 秒：等待首个音频数据（蓝牙耳机切通话模式可能要 1 秒以上）
        for index in _candidate_devices(self.device):
            try:
                info = sd.query_devices(index, kind="input")
            except Exception as exc:
                last_error = exc
                continue
            default_rate = int(round(float(info.get("default_samplerate", 0) or 0)))
            rates = []
            for rate in (SAMPLE_RATE, default_rate, 48000, 44100, 32000):
                if rate and rate > 0 and rate not in rates:
                    rates.append(rate)
            for rate in rates:
                for latency in ("low", None):
                    stream = None
                    try:
                        stream = sd.InputStream(
                            samplerate=rate, channels=1, dtype="float32",
                            device=index, latency=latency, callback=callback)
                        stream.start()
                    except Exception as exc:
                        last_error = exc
                        if stream is not None:
                            try:
                                stream.close()
                            except Exception:
                                pass
                        continue
                    if not self._wait_for_data(budget):
                        last_error = RuntimeError(
                            f"设备 {info.get('name', index)} 没有返回音频数据")
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            pass
                        continue
                    return stream, rate
        raise last_error or RuntimeError("无法创建音频流")

    def _make_callback(self):
        def callback(indata, _frames, _time_info, _status):
            self._data_seen = True
            if self._recording:
                self._chunks.append(indata.copy())
        return callback

    def _wait_for_data(self, budget):
        """等待首个音频回调，确认流真的能收到数据。"""
        wait = min(1.5, budget[0])
        if wait <= 0:
            return False
        budget[0] -= wait
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self._data_seen:
                return True
            time.sleep(0.02)
        return bool(self._data_seen)

    def _tick(self):
        if not self._recording:
            return
        elapsed = time.monotonic() - self._started
        self._status(f"正在录音… {elapsed:.1f} 秒")
        if elapsed >= MAX_SECONDS:
            self._finish()
            return
        self._timer = self.widget.after(100, self._tick)

    def _close_stream(self):
        stream, self._stream = self._stream, None
        was_recording = self._recording
        self._recording = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if was_recording:
            self._notify_recording(False)

    def _notify_recording(self, active):
        if self.on_recording is None:
            return
        try:
            self.on_recording(bool(active))
        except Exception:
            pass

    def _finish(self):
        if not self._recording:
            return
        self._recording = False
        if self._timer is not None:
            try:
                self.widget.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        if self._chunks:
            audio = np.concatenate(self._chunks)[:, 0]
        else:
            audio = np.zeros(0, dtype=np.float32)
        self._chunks = []
        self._notify_recording(False)
        if self._stream_rate != SAMPLE_RATE:
            audio = _resample(audio, self._stream_rate, SAMPLE_RATE)
        duration = audio.size / SAMPLE_RATE
        if duration < MIN_SECONDS:
            self.audio = np.zeros(0, dtype=np.float32)
            if duration < 0.05:
                self._status(
                    "没有采集到音频：请确认录音设备未被其它程序占用"
                    f"（蓝牙耳机需处于通话模式），再重新按住{self._label}说话")
            else:
                self._status(f"录音太短（{duration:.1f} 秒），请重新按住{self._label}说话")
            return
        self.audio = audio
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < SILENCE_PEAK:
            self.audio = np.zeros(0, dtype=np.float32)
            self._status(
                f"已录制 {duration:.1f} 秒，但没有采集到声音（音量 {peak:.3f}）："
                "请确认麦克风未被静音、蓝牙耳机处于通话模式，再重新按住"
                f"{self._label}说话")
            return
        self._captured(self.audio)

    # ------------------------------------------------------------- 占用标记
    def _write_flag(self, active):
        path = self.flag_path
        try:
            if active:
                path.parent.mkdir(parents=True, exist_ok=True)
                # 原子替换：客户端随时可能读取，避免看到写了一半的 JSON
                temp = path.with_name(path.name + ".tmp")
                temp.write_text(
                    json.dumps({"pid": os.getpid(), "ts": time.time()}),
                    encoding="utf-8")
                os.replace(temp, path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def _schedule_flag_refresh(self):
        """占用标记带 ts，客户端超时按残留清理，因此录制模式期间定期刷新。"""
        if not self._armed:
            return
        self._flag_job = self.widget.after(FLAG_REFRESH_MS, self._refresh_flag)

    def _refresh_flag(self):
        self._flag_job = None
        if not self._armed:
            return
        self._write_flag(True)
        self._schedule_flag_refresh()


class VoicePhrasePanel(ttk.Frame):
    """「语音短语」页：录音键录入、试听、删除语音模板。

    没有单独的录音按钮：「开始录制」把录音键切换为短语录制模式，
    再点一次（取消录制）恢复为语音识别；模型未就绪时等待载入完成。
    录制过程中托盘图标会像听写一样变色。
    """

    def __init__(self, parent, capture=None, store=None, ready=None,
                 loading=None, on_recording=None, on_mode_change=None):
        super().__init__(parent, padding=(10, 10, 10, 6), style="Card.TFrame")
        self.store = store if store is not None else VoicePhraseStore()
        self.device, self._device_warning = self._resolve_device()
        self._ready = ready or (lambda: True)
        self._loading = loading or (lambda: False)
        self.on_mode_change = on_mode_change
        self.capture = capture if capture is not None else VoiceCapture(
            self, device=self.device, on_recording=on_recording)
        self._pending = np.zeros(0, dtype=np.float32)
        self._pending_arm = False
        self._poll_job = None
        self._capture_owner = None
        self._capture_status_cb = None
        self._capture_done_cb = None
        self._capture_ready_text = None
        self.status = tk.StringVar()
        self.capture_status = tk.StringVar()
        self.capture_text = tk.StringVar()
        self.device_text = tk.StringVar()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(self, columns=("text", "samples", "duration", "created"),
                                  show="headings", selectmode="browse", height=8)
        for column, title, width in (
                ("text", "短语文字", 220), ("samples", "样本数", 70),
                ("duration", "总时长", 80), ("created", "录入时间", 150)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=50, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical",
                                  style="Card.Slim.Vertical.TScrollbar",
                                  command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(self, style="Card.TFrame")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.record_button = PillButton(actions, "开始录制", command=self.toggle_capture,
                                        kind="primary", background=CARD_BG)
        self.record_button.pack(side="left")
        self.play_button = PillButton(actions, "播放选中", command=self._play,
                                      background=CARD_BG)
        self.play_button.configure(state="disabled")
        self.play_button.pack(side="left", padx=8)
        self.delete_button = PillButton(actions, "删除选中", command=self._delete,
                                        background=CARD_BG)
        self.delete_button.configure(state="disabled")
        self.delete_button.pack(side="left")
        ttk.Label(actions, textvariable=self.status,
                  style="Hint.TLabel").pack(side="right")

        self.capture_frame = ttk.Frame(self, style="Card.TFrame")
        self.capture_frame.grid(row=2, column=0, columnspan=2, sticky="ew",
                                pady=(10, 0))
        self.capture_frame.columnconfigure(1, weight=1)
        ttk.Label(self.capture_frame, text="短语文字", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.capture_entry = ttk.Entry(self.capture_frame, textvariable=self.capture_text)
        self.capture_entry.grid(row=0, column=1, sticky="ew")
        self.capture_entry.bind("<Return>", lambda _event: self._save_pending())
        capture_actions = ttk.Frame(self.capture_frame, style="Card.TFrame")
        capture_actions.grid(row=0, column=2, sticky="e", padx=(10, 0))
        self.play_pending_button = PillButton(capture_actions, "试听", command=self._play_pending,
                                              background=CARD_BG)
        self.play_pending_button.configure(state="disabled")
        self.play_pending_button.pack(side="left")
        self.save_button = PillButton(capture_actions, "保存", command=self._save_pending,
                                      kind="primary", background=CARD_BG)
        self.save_button.configure(state="disabled")
        self.save_button.pack(side="left", padx=8)
        self.discard_button = PillButton(capture_actions, "放弃", command=self.discard_pending,
                                         background=CARD_BG)
        self.discard_button.configure(state="disabled")
        self.discard_button.pack(side="left")
        ttk.Label(self.capture_frame, textvariable=self.capture_status,
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=3,
                                            sticky="w", pady=(6, 0))

        hint = ("说出的短语会在识别完成后整体替换为标注文字（音频层匹配，优先级高于热词与规则）；"
                f"最多 {MAX_PHRASES} 条，每条 {MIN_SECONDS:.1f}~{MAX_SECONDS:.0f} 秒。")
        ttk.Label(self, text=hint, style="Hint.TLabel", wraplength=560).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(self, textvariable=self.device_text, style="Hint.TLabel",
                  wraplength=560).grid(row=4, column=0, columnspan=2,
                                       sticky="w", pady=(4, 0))

        self.table.bind("<<TreeviewSelect>>", self._select)
        self.bind("<Destroy>", self._on_destroy, add="+")
        toplevel = self.winfo_toplevel()
        self._focus_bindings = [
            (sequence, toplevel.bind(sequence, self._focus_changed, add="+"))
            for sequence in ("<Map>", "<Unmap>", "<FocusOut>", "<FocusIn>")
        ]
        merged = self.store.merge_duplicates()
        self.refresh()
        self._refresh_device_text()
        self._set_mode_ui()
        if merged:
            self.status.set(f"已合并 {merged} 个重复词条；"
                            f"{len(self.store.load())} / {MAX_PHRASES} 条")

    @staticmethod
    def _resolve_device():
        """返回 (设备索引, 提示文本)。

        与客户端保持一致：用 resolve_capture_device（默认麦优先 WASAPI），
        避免 GUI 走 sounddevice 默认（MME）而客户端走 WASAPI 造成特征不一致。
        所选设备不可用时回退系统默认并给出提示，不再静默换麦。
        """
        try:
            from config_client import ClientConfig as Config
            from core.audio_devices import resolve_capture_device

            selection = getattr(Config, "audio_device", None)
            try:
                return resolve_capture_device(selection), ""
            except Exception as exc:
                return None, f"所选麦克风不可用（{exc}），已回退系统默认，请在「设置」里重新选择"
        except Exception:
            return None, ""

    @staticmethod
    def _device_name(index):
        try:
            import sounddevice as sd

            if index is None:
                index = sd.default.device[0]
            if index is None or index < 0:
                return ""
            return str(sd.query_devices(index)["name"]).strip()
        except Exception:
            return ""

    def _refresh_device_text(self):
        name = self._device_name(self.capture.device) or "系统默认"
        text = f"当前麦克风：{name}"
        if self._device_warning:
            text = f"{text}（{self._device_warning}）"
        self.device_text.set(text)

    def _sync_device(self):
        """按最新配置重新解析麦克风（未武装时才调用）。"""
        device, warning = self._resolve_device()
        self._device_warning = warning
        if device != self.capture.device:
            self.capture.device = device
        self._refresh_device_text()

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        self._cancel_ready_poll()
        try:
            self.capture.cancel()
        except Exception:
            pass
        toplevel = self.winfo_toplevel()
        for sequence, funcid in self._focus_bindings:
            try:
                toplevel.unbind(sequence, funcid)
            except Exception:
                pass

    # ------------------------------------------------------------- 列表
    def refresh(self):
        for item in self.table.get_children():
            self.table.delete(item)
        self.play_button.configure(state="disabled")
        self.delete_button.configure(state="disabled")
        items = self.store.load()
        for item in items:
            samples = item.get("samples") or []
            duration = 0.0
            for name in samples:
                try:
                    audio, rate = _read_audio(self.store.base_dir / name)
                    duration += audio.size / rate
                except Exception:
                    continue
            self.table.insert("", "end", iid=item.get("id", ""),
                              values=(item.get("text", ""),
                                      f"{len(samples)} / {MAX_SAMPLES}",
                                      f"{duration:.1f}s", item.get("created_at", "")))
        self.status.set(f"{len(items)} / {MAX_PHRASES} 条")

    def _select(self, _=None):
        selection = self.table.selection()
        state = "normal" if selection else "disabled"
        self.play_button.configure(state=state)
        self.delete_button.configure(state=state)
        if selection:
            values = self.table.item(selection[0], "values")
            if values and values[0]:
                # 选中词条时把文字填进录入框，方便再录一份样本而不用重打
                self.capture_text.set(values[0])

    def _selected_id(self):
        selection = self.table.selection()
        return selection[0] if selection else None

    # ------------------------------------------------------------- 录制模式
    def set_active(self, active):
        """切换页面时调用：离开本页退出录制模式，录音键交还语音识别。"""
        if not active and self.mode_active:
            self.cancel_capture(silent=True)

    @property
    def mode_active(self):
        """录制模式是否生效（含等待模型载入）。"""
        return self.capture.armed or self._pending_arm

    @property
    def capture_owner(self):
        """当前录制模式的用途：'panel'（录入）或 'test'（语音测试）。"""
        return self._capture_owner

    def _set_mode_ui(self):
        recording = self.mode_active and self._capture_owner != "test"
        self.record_button.configure(text="取消录制" if recording else "开始录制")
        self._refresh_capture_status()
        if self.on_mode_change is not None:
            try:
                self.on_mode_change()
            except Exception:
                pass

    def _refresh_capture_status(self):
        if self._pending_arm:
            self.capture_status.set("识别模型正在载入，载入完成后按住录音键即可录制…")
        elif self._pending.size:
            peak = float(np.max(np.abs(self._pending)))
            warning = "，音量偏低，建议靠近麦克风或用耳机麦克风" if peak < LOW_VOLUME_PEAK else ""
            self.capture_status.set(
                f"已录制 {self._pending.size / SAMPLE_RATE:.1f} 秒"
                f"（音量 {peak:.2f}{warning}），填写文字后保存")
        elif self.capture.armed:
            self.capture_status.set(self._capture_ready_text or self._idle_hint())
        else:
            self.capture_status.set("点「开始录制」后，录音键用于录制语音短语")

    def toggle_capture(self):
        if self.mode_active and self._capture_owner != "test":
            self.cancel_capture()
        else:
            self.begin_capture(owner="panel")

    def begin_capture(self, owner="panel", status=None, captured=None,
                      ready_text=None):
        """启动按键：模型就绪后把录音键切换为短语录制。

        「开始录制」与「语音测试」共用这里：都先确认模型就绪、暂停听写，
        再由录音键采集；区别只在录制完成后的处理（录入 vs 匹配对比）。
        两者互不联动：一个模式生效时点另一个按钮只切换用途，不重新打开设备。
        """
        if owner == "panel":
            try:
                items = self.store.load()
                room = any(len(item.get("samples") or []) < MAX_SAMPLES for item in items)
                if len(items) >= MAX_PHRASES and not room:
                    messagebox.showerror("数量已满", f"最多录入 {MAX_PHRASES} 条短语。",
                                         parent=self)
                    return
            except Exception:
                pass
        self._capture_owner = owner
        self._capture_status_cb = status
        self._capture_done_cb = captured
        self._capture_ready_text = ready_text
        if self.mode_active:
            if self.capture.armed:
                self.capture.set_owner(
                    owner,
                    status=self._capture_status_cb or self._on_capture_status,
                    captured=self._dispatch_captured,
                    ready_text=ready_text)
            self._set_mode_ui()
            return
        self._sync_device()
        if not self._ready():
            if self._loading():
                self._pending_arm = True
                self._set_mode_ui()
                self._schedule_ready_poll()
            else:
                messagebox.showinfo(
                    "识别未启动",
                    "请先在主界面点击「保存并启动」；模型就绪后再开始录制语音短语。",
                    parent=self)
                self._reset_capture_mode()
            return
        self._begin_listening()

    def _begin_listening(self):
        shortcut = recording_shortcut()
        self.capture.arm(shortcut, owner=self._capture_owner or "panel",
                         status=self._capture_status_cb or self._on_capture_status,
                         captured=self._dispatch_captured,
                         ready_text=self._capture_ready_text)
        self._set_mode_ui()

    def _dispatch_captured(self, audio):
        """录制完成：按用途分派（测试模式不进入待保存状态）。"""
        if self._capture_owner == "test":
            if self._capture_done_cb is not None:
                self._capture_done_cb(audio)
            self._set_mode_ui()
            return
        self._on_captured(audio)

    def _schedule_ready_poll(self):
        if self._poll_job is None:
            self._poll_job = self.after(500, self._poll_ready)

    def _cancel_ready_poll(self):
        if self._poll_job is None:
            return
        try:
            self.after_cancel(self._poll_job)
        except Exception:
            pass
        self._poll_job = None

    def _poll_ready(self):
        self._poll_job = None
        if not self._pending_arm:
            return
        if self._ready():
            self._pending_arm = False
            self._begin_listening()
        else:
            self._schedule_ready_poll()

    def notify_client_ready(self):
        """识别客户端（模型）就绪：继续等待中的录制模式。"""
        if self._pending_arm:
            self._pending_arm = False
            self._cancel_ready_poll()
            self._begin_listening()

    def notify_client_stopped(self):
        """识别客户端停止：退出录制模式，录音键交还语音识别。"""
        if self.mode_active:
            self.cancel_capture(silent=True)
        else:
            self._reset_capture_mode()

    def cancel_capture(self, silent=False):
        """退出录制模式（保留未保存的录音）。"""
        self._pending_arm = False
        self._cancel_ready_poll()
        self.capture.disarm()
        self._reset_capture_mode()
        self._set_mode_ui()
        if not silent:
            self.refresh()

    def _reset_capture_mode(self):
        self._capture_owner = None
        self._capture_status_cb = None
        self._capture_done_cb = None
        self._capture_ready_text = None

    def discard_pending(self):
        """放弃未保存的录音。"""
        self._pending = np.zeros(0, dtype=np.float32)
        self.capture.discard()
        self.capture_text.set("")
        self._set_pending_buttons(False)
        self._refresh_capture_status()

    def _focus_changed(self, _event=None):
        self.after_idle(self._on_visibility_changed)

    def _on_visibility_changed(self):
        try:
            if not self.winfo_exists():
                return
            visible = bool(self.winfo_viewable()) and self._window_focused()
        except tk.TclError:
            return
        # 最小化到托盘、窗口被隐藏或切到其它程序时退出录制模式，
        # 避免录音键一直被占用（FocusIn/FocusOut 只对焦点变化做轻量判断）
        if not visible and self.mode_active:
            self.cancel_capture(silent=True)

    def _window_focused(self):
        """应用是否仍持有焦点；查询失败时按仍有焦点处理，避免误退出。"""
        try:
            toplevel = self.winfo_toplevel()
            return (toplevel.focus_displayof() is not None
                    or toplevel.focus_get() is not None)
        except (tk.TclError, KeyError):
            return True

    def _idle_hint(self):
        label = shortcut_label(recording_shortcut().get("key", ""))
        return f"按住 {label} 说话即可录入新短语（{MIN_SECONDS:.1f}~{MAX_SECONDS:.0f} 秒）"

    def _on_capture_status(self, text):
        self.capture_status.set(text)

    def _on_captured(self, audio):
        self._pending = np.asarray(audio, dtype=np.float32)
        self._set_pending_buttons(True)
        self._refresh_capture_status()

    def _set_pending_buttons(self, pending):
        state = "normal" if pending else "disabled"
        self.play_pending_button.configure(state=state)
        self.save_button.configure(state=state)
        self.discard_button.configure(state=state)

    def _save_pending(self):
        text = self.capture_text.get().strip()
        if not text:
            messagebox.showerror("缺少文字", "请填写这段语音对应的文字。", parent=self)
            self.capture_entry.focus_set()
            return
        duration = self._pending.size / SAMPLE_RATE
        if not (MIN_SECONDS <= duration <= MAX_SECONDS + 0.2):
            messagebox.showerror(
                "时长不合适",
                f"语音需在 {MIN_SECONDS:.1f}~{MAX_SECONDS:.0f} 秒之间（当前 {duration:.1f} 秒）。",
                parent=self)
            return
        try:
            if self.store.find_by_text(text) is None and len(self.store.load()) >= MAX_PHRASES:
                messagebox.showerror("数量已满", f"最多录入 {MAX_PHRASES} 条短语。",
                                     parent=self)
                return
            item = self.store.add(text, [self._pending])
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self._pending = np.zeros(0, dtype=np.float32)
        self.capture.discard()
        self.capture_text.set("")
        self._set_pending_buttons(False)
        self._refresh_capture_status()
        self.refresh()
        count = len(item.get("samples") or []) if isinstance(item, dict) else 1
        if count > 1:
            self.status.set(f"已保存「{text}」（第 {count} 份样本，最多 {MAX_SAMPLES} 份）")
        else:
            self.status.set(f"已保存「{text}」")

    def _play_pending(self):
        if not self._pending.size:
            return
        try:
            play_audio(self._pending)
        except Exception as exc:
            messagebox.showerror("无法播放", str(exc), parent=self)

    # ------------------------------------------------------------- 列表操作
    def _play(self):
        phrase_id = self._selected_id()
        if not phrase_id:
            return
        try:
            item = next((row for row in self.store.load() if row.get("id") == phrase_id), None)
            if not item:
                return
            chunks = []
            for name in item.get("samples") or []:
                audio, rate = _read_audio(self.store.base_dir / name)
                chunks.append(audio)
                chunks.append(np.zeros(int(rate * 0.25), dtype=np.float32))
            if chunks:
                play_audio(np.concatenate(chunks))
        except Exception as exc:
            messagebox.showerror("无法播放", str(exc), parent=self)

    def _delete(self):
        phrase_id = self._selected_id()
        if not phrase_id:
            return
        if not messagebox.askyesno("删除短语", "删除选中的语音短语及其录音？", parent=self):
            return
        try:
            self.store.remove(phrase_id)
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.refresh()

    def destroy(self):
        self._cancel_ready_poll()
        try:
            self.capture.cancel()
        except Exception:
            pass
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass
        super().destroy()
