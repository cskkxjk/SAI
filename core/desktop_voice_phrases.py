"""Desktop editor for recorded voice phrases (语音短语)."""

import time
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

from core.desktop_widgets import CARD_BG, PillButton
from core.client.voice_phrase.storage import VoicePhraseStore

SAMPLE_RATE = 16000
MIN_SECONDS = 0.5
MAX_SECONDS = 4.0
MAX_PHRASES = 50


def _read_audio(path):
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, rate


class RecordDialog(tk.Toplevel):
    """录制一条语音短语并标注文字。"""

    def __init__(self, master, store, device=None, on_saved=None):
        super().__init__(master)
        self.title("录制语音短语")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.store = store
        self.device = device
        self.on_saved = on_saved

        self.audio = np.zeros(0, dtype=np.float32)
        self._stream = None
        self._chunks = []
        self._started = 0.0
        self._timer = None

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="短语文字").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.text = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.text, width=28)
        entry.grid(row=0, column=1, sticky="ew")
        entry.focus_set()

        self.status = tk.StringVar(value=f"点「开始录音」，最多 {MAX_SECONDS:.0f} 秒")
        ttk.Label(body, textvariable=self.status).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.record_button = PillButton(buttons, "开始录音", command=self._toggle,
                                        kind="primary", background=CARD_BG)
        self.record_button.pack(side="left")
        self.play_button = PillButton(buttons, "播放", command=self._play,
                                      background=CARD_BG)
        self.play_button.configure(state="disabled")
        self.play_button.pack(side="left", padx=8)
        self.save_button = PillButton(buttons, "保存", command=self._save,
                                      background=CARD_BG)
        self.save_button.configure(state="disabled")
        self.save_button.pack(side="left")
        PillButton(buttons, "取消", command=self.destroy,
                   background=CARD_BG).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    # ------------------------------------------------------------- 录音
    def _toggle(self):
        if self._stream is None:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        try:
            import sounddevice as sd
        except Exception as exc:
            messagebox.showerror("无法录音", f"音频库不可用：{exc}", parent=self)
            return
        self._chunks = []
        self._started = time.monotonic()

        def callback(indata, frames, time_info, status):
            self._chunks.append(indata.copy())

        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    device=self.device, callback=callback)
            stream.start()
        except Exception as exc:
            messagebox.showerror("无法录音", str(exc), parent=self)
            return
        self._stream = stream
        self.audio = np.zeros(0, dtype=np.float32)
        self.record_button.configure(text="停止录音")
        self.play_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self._tick()

    def _tick(self):
        if self._stream is None:
            return
        elapsed = time.monotonic() - self._started
        self.status.set(f"正在录音… {elapsed:.1f}s")
        if elapsed >= MAX_SECONDS:
            self._stop_recording()
            return
        self._timer = self.after(100, self._tick)

    def _stop_recording(self):
        if self._timer is not None:
            try:
                self.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if self._chunks:
            self.audio = np.concatenate(self._chunks)[:, 0]
        else:
            self.audio = np.zeros(0, dtype=np.float32)
        self._chunks = []

        duration = self.audio.size / SAMPLE_RATE
        self.record_button.configure(text="重新录音")
        state = "normal" if duration >= MIN_SECONDS else "disabled"
        self.play_button.configure(state=state)
        self.save_button.configure(state=state)
        if duration < MIN_SECONDS:
            self.status.set(f"录音太短（{duration:.1f}s），至少 {MIN_SECONDS:.1f}s")
        else:
            self.status.set(f"已录制 {duration:.1f}s，点「保存」写入")

    # ------------------------------------------------------------- 播放/保存
    def _play(self):
        if not self.audio.size:
            return
        try:
            import sounddevice as sd
            sd.play(self.audio, SAMPLE_RATE, device=self.device)
        except Exception as exc:
            messagebox.showerror("无法播放", str(exc), parent=self)

    def _save(self):
        text = self.text.get().strip()
        if not text:
            messagebox.showerror("缺少文字", "请填写这段语音对应的文字。", parent=self)
            return
        duration = self.audio.size / SAMPLE_RATE
        if not (MIN_SECONDS <= duration <= MAX_SECONDS + 0.2):
            messagebox.showerror(
                "时长不合适",
                f"语音需在 {MIN_SECONDS:.1f}~{MAX_SECONDS:.0f}s 之间（当前 {duration:.1f}s）。",
                parent=self)
            return
        try:
            if len(self.store.load()) >= MAX_PHRASES:
                messagebox.showerror("数量已满", f"最多录入 {MAX_PHRASES} 条短语。", parent=self)
                return
            self.store.add(text, [self.audio])
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()

    def destroy(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._timer is not None:
            try:
                self.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        super().destroy()


class VoicePhrasePanel(ttk.Frame):
    """「语音短语」页：管理录入的语音模板。"""

    def __init__(self, parent, root=None):
        super().__init__(parent, padding=(10, 10, 10, 6), style="Card.TFrame")
        self.store = VoicePhraseStore()
        self.device = None
        try:
            from config_client import ClientConfig as Config
            self.device = getattr(Config, "audio_device", None)
        except Exception:
            pass
        self.status = tk.StringVar()

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
        PillButton(actions, "录制新短语", command=self._record,
                   kind="primary", background=CARD_BG).pack(side="left")
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

        hint = ("说出的短语会在识别完成后整体替换为标注文字（音频层匹配，优先级高于热词与规则）；"
                f"最多 {MAX_PHRASES} 条，每条 {MIN_SECONDS:.1f}~{MAX_SECONDS:.0f}s。")
        ttk.Label(self, text=hint, style="Hint.TLabel", wraplength=560).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.table.bind("<<TreeviewSelect>>", self._select)
        self.refresh()

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
                              values=(item.get("text", ""), len(samples),
                                      f"{duration:.1f}s", item.get("created_at", "")))
        self.status.set(f"{len(items)} / {MAX_PHRASES} 条")

    def _select(self, _=None):
        selection = self.table.selection()
        state = "normal" if selection else "disabled"
        self.play_button.configure(state=state)
        self.delete_button.configure(state=state)

    def _selected_id(self):
        selection = self.table.selection()
        return selection[0] if selection else None

    # ------------------------------------------------------------- 操作
    def _record(self):
        try:
            if len(self.store.load()) >= MAX_PHRASES:
                messagebox.showerror("数量已满", f"最多录入 {MAX_PHRASES} 条短语。", parent=self)
                return
        except Exception:
            pass
        RecordDialog(self, self.store, device=self.device, on_saved=self.refresh)

    def _play(self):
        phrase_id = self._selected_id()
        if not phrase_id:
            return
        try:
            import sounddevice as sd

            item = next((row for row in self.store.load() if row.get("id") == phrase_id), None)
            if not item:
                return
            chunks = []
            for name in item.get("samples") or []:
                audio, rate = _read_audio(self.store.base_dir / name)
                chunks.append(audio)
                chunks.append(np.zeros(int(rate * 0.25), dtype=np.float32))
            if chunks:
                sd.play(np.concatenate(chunks), SAMPLE_RATE, device=self.device)
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
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        super().destroy()
