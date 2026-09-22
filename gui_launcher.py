# coding: utf-8
"""Windows graphical launcher and configuration editor."""
from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import sys
import tkinter as tk
import threading
import time
import uuid
from pathlib import Path
from tkinter import messagebox, ttk
import sounddevice as sd
from core.audio_devices import physical_input_devices, resolve_input_device
from core.runtime_paths import DATA_DIR, initialize_user_data
from core.model_download import download_model, DownloadCancelled, missing_files, model_files
from core.desktop_widgets import (
    BORDER, CARD_BG, DIVIDER, ERROR, FONT_FAMILY, HOVER_BG, PAGE_BG, PRIMARY,
    SELECTED_BG, SIDEBAR_BG, SUCCESS, TEXT, TEXT_SECONDARY, WARNING,
    Card, NavButton, PillButton, ScrollPage, ShortcutCapture, Switch,
    ui_font,
)

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG = DATA_DIR / "config_gui.json"
DEFAULT_MIC = "系统默认录音设备"
DEFAULT_SHORTCUTS = [{"key": "caps_lock", "type": "keyboard", "enabled": True}]
PUNC_MODEL = (
    "models/Punct-CT-Transformer/"
    "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12/model.onnx"
)


APP_TITLE = "SAI 离线语音输入"


def activate_existing():
    if os.name != "nt":
        return False
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    activate_existing.mutex = kernel.CreateMutexW(None, False, "Local\\SAIDesktop")
    if ctypes.get_last_error() != 183:
        return False
    user = ctypes.WinDLL("user32")
    user.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user.FindWindowW.restype = ctypes.c_void_p
    user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    window = user.FindWindowW(None, APP_TITLE)
    if window:
        user.ShowWindow(window, 9)
        user.SetForegroundWindow(window)
    return True

MODEL_INFO = {
    "openai_api": {
        "label": "OpenAI 兼容 API",
        "advice": "适合远程识别；录音发送至配置的服务，可能产生费用。不占用本地 GPU。",
        "files": (),
    },
    "qwen_asr": {
        "label": "Qwen3-ASR",
        "advice": "适合多语言和复杂口述；独显解码优先，当前 INT4 编码器自动使用 CPU。",
        "files": model_files("qwen_asr"),
    },
    "fun_asr_nano": {
        "label": "Fun-ASR-Nano",
        "advice": "适合日常中英文输入；GGUF 可使用独显，ONNX 不兼容时自动回退 CPU。",
        "files": model_files("fun_asr_nano"),
    },
    "sensevoice": {
        "label": "SenseVoice-Small",
        "advice": "适合短句、中英日韩粤语；当前安装的是 CPU INT8 版，适合低占用输入。",
        "files": model_files("sensevoice"),
    },
    "paraformer": {
        "label": "Paraformer",
        "advice": "CPU 专用、速度快、占用低；不使用 GPU。",
        "files": model_files("paraformer"),
    },
}
MODEL_CHOICES = {
    "openai_api": "OpenAI 兼容 API（远程转写，无需本地模型）",
    "qwen_asr": "Qwen3-ASR（多语言与复杂口述，推荐独显）",
    "fun_asr_nano": "Fun-ASR-Nano（准确率和速度均衡）",
    "sensevoice": "SenseVoice-Small（CPU 低占用，短句输入）",
    "paraformer": "Paraformer（CPU 专用、速度快）",
}
CLOSE_CHOICES = {
    "ask": "每次询问",
    "tray": "最小化到托盘，继续运行",
    "exit": "直接退出程序",
}
CLOSE_LABELS = {label: key for key, label in CLOSE_CHOICES.items()}


class Launcher(tk.Tk):
    def __init__(self):
        initialize_user_data()
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("940x800")
        self.minsize(860, 640)
        self.resizable(True, True)
        try:
            self.iconbitmap(default=str(ROOT / "assets" / "icon.ico"))
        except tk.TclError:
            pass
        self._configure_styles()
        self.processes = []
        self.ready_files = {}
        self.recording_files = {}
        self.tray_images = {}
        self.tray_recording = False
        self.starting = False
        self.running = False
        self.monitor = None
        self.download_thread = None
        self.download_cancel = threading.Event()
        self.download_events = queue.Queue()
        self.api_test_events = queue.Queue()
        self.api_test_thread = None
        self.hardware_events = queue.Queue()
        self.hardware_busy = False
        self.hardware_info = None
        self.hardware_after = None
        self.status = tk.StringVar(value="未启动")
        self.vars = {
            "model_type": tk.StringVar(value=MODEL_CHOICES["qwen_asr"]),
            "qwen_quantization": tk.StringVar(value="q5_k"),
            "onnx_provider": tk.StringVar(value="AUTO"),
            "llm_use_gpu": tk.BooleanVar(value=True),
            "language": tk.StringVar(value="auto"),
            "threshold": tk.StringVar(value="0.3"),
            "context": tk.StringVar(),
            "paste": tk.BooleanVar(value=False),
            "audio_device": tk.StringVar(value=DEFAULT_MIC),
            "keep_microphone_open": tk.BooleanVar(value=False),
            "close_behavior": tk.StringVar(value=CLOSE_CHOICES["ask"]),
            "asr_api_base_url": tk.StringVar(value="https://api.openai.com/v1"),
            "asr_api_model": tk.StringVar(value="whisper-1"),
            "asr_api_key": tk.StringVar(),
            "asr_api_timeout": tk.StringVar(value="60"),
            "asr_api_allow_http": tk.BooleanVar(value=False),
        }
        self.audio_devices = {DEFAULT_MIC: None}
        self.saved_audio_device = None
        self.saved_shortcuts = list(DEFAULT_SHORTCUTS)
        self.shortcut_capture = None
        self._load()
        self._build()
        self._start_tray()
        self.vars["model_type"].trace_add("write", lambda *_: self._update_model_info())
        self.vars["qwen_quantization"].trace_add("write", lambda *_: self._update_model_info())
        self._update_model_info()
        self._refresh_audio_devices()
        self.protocol("WM_DELETE_WINDOW", self._hide_or_close)
        self.hardware_after = self.after(100, self._detect_hardware)

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=ui_font(9), background=PAGE_BG, foreground=TEXT)
        style.configure("TFrame", background=PAGE_BG)
        style.configure("TLabel", background=PAGE_BG, foreground=TEXT)
        style.configure("Page.TFrame", background=PAGE_BG)
        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("CardTitle.TLabel", background=CARD_BG, foreground=TEXT,
                        font=ui_font(11, "bold"))
        style.configure("Field.TLabel", background=CARD_BG, foreground=TEXT)
        style.configure("Hint.TLabel", background=CARD_BG,
                        foreground=TEXT_SECONDARY, font=ui_font(8))
        style.configure("Warning.TLabel", background=CARD_BG, foreground=WARNING,
                        font=ui_font(8))
        style.configure("Success.TLabel", background=CARD_BG, foreground=SUCCESS,
                        font=ui_font(9, "bold"))
        style.configure("Danger.TLabel", background=CARD_BG, foreground=ERROR,
                        font=ui_font(9, "bold"))
        style.configure("Status.TLabel", background=PAGE_BG,
                        foreground=TEXT_SECONDARY)
        style.configure("Header.TLabel", background=PAGE_BG, foreground=TEXT,
                        font=ui_font(15, "bold"))
        style.configure("HeaderSub.TLabel", background=PAGE_BG,
                        foreground=TEXT_SECONDARY, font=ui_font(9))
        style.configure("SidebarBrand.TLabel", background=SIDEBAR_BG,
                        foreground=TEXT, font=ui_font(13, "bold"))
        style.configure("SidebarHint.TLabel", background=SIDEBAR_BG,
                        foreground=TEXT_SECONDARY, font=ui_font(8))
        style.configure("TButton", padding=(14, 7), font=ui_font(9),
                        background=CARD_BG, foreground=TEXT, bordercolor=BORDER,
                        focuscolor=PRIMARY, relief="flat", borderwidth=1)
        style.map("TButton",
                  background=[("pressed", "#EFEFF1"), ("active", "#F6F6F7"),
                              ("disabled", "#F5F5F6")],
                  foreground=[("disabled", "#B4B4BB")],
                  bordercolor=[("focus", PRIMARY)])
        style.configure("Accent.TButton", padding=(16, 8), font=ui_font(9, "bold"),
                        background=PRIMARY, foreground="#FFFFFF",
                        bordercolor=PRIMARY, focuscolor=PRIMARY, relief="flat",
                        borderwidth=1)
        style.map("Accent.TButton",
                  background=[("pressed", "#0064CB"), ("active", "#0A6FDB"),
                              ("disabled", "#A9CFFF")],
                  bordercolor=[("pressed", "#0064CB"), ("active", "#0A6FDB"),
                               ("disabled", "#A9CFFF")],
                  foreground=[("disabled", "#FFFFFF")])
        style.configure("TEntry", padding=(8, 6), fieldbackground=CARD_BG,
                        background=CARD_BG, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, insertcolor=TEXT, foreground=TEXT)
        style.map("TEntry",
                  bordercolor=[("focus", PRIMARY)],
                  lightcolor=[("focus", PRIMARY)],
                  darkcolor=[("focus", PRIMARY)],
                  foreground=[("disabled", "#B4B4BB")])
        style.configure("TCombobox", padding=(8, 5), fieldbackground=CARD_BG,
                        background=CARD_BG, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, arrowcolor=TEXT_SECONDARY,
                        foreground=TEXT, selectbackground=CARD_BG,
                        selectforeground=TEXT, borderwidth=1)
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD_BG), ("disabled", "#F2F2F3")],
                  foreground=[("disabled", "#B4B4BB")],
                  bordercolor=[("focus", PRIMARY)],
                  lightcolor=[("focus", PRIMARY)],
                  darkcolor=[("focus", PRIMARY)])
        self.option_add("*TCombobox*Listbox.background", CARD_BG)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", SELECTED_BG)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.option_add("*TCombobox*Listbox.font",
                        "{%s} 9" % FONT_FAMILY)
        self.option_add("*Text.selectBackground", SELECTED_BG)
        self.option_add("*Text.selectForeground", TEXT)
        style.configure("Slim.Vertical.TScrollbar", background="#C1C1C6",
                        troughcolor=PAGE_BG, bordercolor=PAGE_BG,
                        lightcolor="#C1C1C6", darkcolor="#C1C1C6",
                        arrowcolor=PAGE_BG, arrowsize=10, width=10,
                        relief="flat", borderwidth=0)
        style.map("Slim.Vertical.TScrollbar",
                  background=[("active", "#A8A8AD")],
                  lightcolor=[("active", "#A8A8AD")],
                  darkcolor=[("active", "#A8A8AD")])
        style.configure("Slim.Horizontal.TScrollbar", background="#C1C1C6",
                        troughcolor=PAGE_BG, bordercolor=PAGE_BG,
                        lightcolor="#C1C1C6", darkcolor="#C1C1C6",
                        arrowcolor=PAGE_BG, arrowsize=10, width=10,
                        relief="flat", borderwidth=0)
        style.map("Slim.Horizontal.TScrollbar",
                  background=[("active", "#A8A8AD")],
                  lightcolor=[("active", "#A8A8AD")],
                  darkcolor=[("active", "#A8A8AD")])
        style.configure("Card.Slim.Vertical.TScrollbar", background="#C1C1C6",
                        troughcolor=CARD_BG, bordercolor=CARD_BG,
                        lightcolor="#C1C1C6", darkcolor="#C1C1C6",
                        arrowcolor=CARD_BG, arrowsize=10, width=10,
                        relief="flat", borderwidth=0)
        style.map("Card.Slim.Vertical.TScrollbar",
                  background=[("active", "#A8A8AD")],
                  lightcolor=[("active", "#A8A8AD")],
                  darkcolor=[("active", "#A8A8AD")])
        style.configure("Card.Slim.Horizontal.TScrollbar", background="#C1C1C6",
                        troughcolor=CARD_BG, bordercolor=CARD_BG,
                        lightcolor="#C1C1C6", darkcolor="#C1C1C6",
                        arrowcolor=CARD_BG, arrowsize=10, width=10,
                        relief="flat", borderwidth=0)
        style.map("Card.Slim.Horizontal.TScrollbar",
                  background=[("active", "#A8A8AD")],
                  lightcolor=[("active", "#A8A8AD")],
                  darkcolor=[("active", "#A8A8AD")])
        style.configure("Horizontal.TProgressbar", thickness=6, background=PRIMARY,
                        troughcolor="#E4E4E6", bordercolor="#E4E4E6",
                        lightcolor=PRIMARY, darkcolor=PRIMARY)
        style.configure("TRadiobutton", background=PAGE_BG, foreground=TEXT,
                        focuscolor=PAGE_BG, font=ui_font(9))
        style.map("TRadiobutton", background=[("active", PAGE_BG)])
        style.configure("TCheckbutton", background=PAGE_BG, foreground=TEXT,
                        focuscolor=PAGE_BG, font=ui_font(9))
        style.map("TCheckbutton", background=[("active", PAGE_BG)])
        style.configure("TNotebook", background=CARD_BG, borderwidth=0,
                        bordercolor=CARD_BG, lightcolor=CARD_BG, darkcolor=CARD_BG,
                        tabmargins=(0, 2, 0, 0))
        style.configure("TNotebook.Tab", padding=(16, 7), font=ui_font(9),
                        background=CARD_BG, foreground=TEXT_SECONDARY, borderwidth=0,
                        bordercolor=CARD_BG, lightcolor=CARD_BG, darkcolor=CARD_BG,
                        focuscolor=CARD_BG)
        style.map("TNotebook.Tab",
                  background=[("selected", SELECTED_BG), ("active", HOVER_BG)],
                  foreground=[("selected", TEXT), ("active", TEXT)],
                  font=[("selected", ui_font(9, "bold"))])
        style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG,
                        foreground=TEXT, rowheight=26, borderwidth=0,
                        bordercolor=CARD_BG, lightcolor=CARD_BG, darkcolor=CARD_BG,
                        font=ui_font(9))
        style.map("Treeview", background=[("selected", SELECTED_BG)],
                  foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background="#F5F5F5",
                        foreground=TEXT_SECONDARY, relief="flat",
                        font=ui_font(9, "bold"), padding=(8, 6))
        style.map("Treeview.Heading", background=[("active", "#ECECEC")])

    def _load(self):
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
            self.saved_audio_device = data.get("audio_device")
            if isinstance(data.get("shortcuts"), list):
                self.saved_shortcuts = data["shortcuts"]
            if (data.get("close_action") in ("tray", "exit")
                    and data.get("close_action_remembered")):
                self.vars["close_behavior"].set(CLOSE_CHOICES[data["close_action"]])
            for key, value in data.items():
                if key in self.vars:
                    if key == "audio_device":
                        continue
                    if key == "model_type":
                        value = MODEL_CHOICES.get(value, value)
                    elif key == "onnx_provider" and value not in ("AUTO", "CPU"):
                        value = "AUTO"
                    elif key == "qwen_quantization" and value not in ("q5_k", "q4_k"):
                        value = "q5_k"
                    elif key == "close_behavior" and value not in CLOSE_LABELS:
                        value = CLOSE_CHOICES.get(value, CLOSE_CHOICES["ask"])
                    self.vars[key].set(value)
        except (OSError, ValueError):
            pass

    def _build(self):
        shell = tk.Frame(self, background=SIDEBAR_BG)
        shell.pack(fill="both", expand=True)
        shell.rowconfigure(0, weight=1)
        shell.columnconfigure(2, weight=1)
        sidebar = tk.Frame(shell, background=SIDEBAR_BG, width=210)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.pack_propagate(False)
        tk.Frame(shell, background=DIVIDER, width=1).grid(row=0, column=1, sticky="ns")
        content = tk.Frame(shell, background=PAGE_BG)
        content.grid(row=0, column=2, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)

        self._build_pages(content)
        self._build_sidebar(sidebar)
        self._build_header(content)
        self._build_action_bar(content)
        self._show_page(self.page_specs[0][0], self.nav_buttons[0])

    def _build_pages(self, content):
        container = tk.Frame(content, background=PAGE_BG)
        container.grid(row=2, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        home = ScrollPage(container)
        api = ScrollPage(container)
        hardware = ScrollPage(container)
        hotwords = ScrollPage(container)
        self.page_specs = (
            (home, "常规设置", "识别模型、录音与输出选项"),
            (api, "语音 API", "远程识别服务连接"),
            (hardware, "设备检测", "检测本机硬件并应用推荐配置"),
            (hotwords, "热词与替换", "热词、别名与正则替换规则"),
        )
        for page, _title, _subtitle in self.page_specs:
            page.grid(row=0, column=0, sticky="nsew")
        self._build_home_page(home.body)
        self._build_api_page(api.body)
        self._build_hardware_page(hardware.body)
        self._build_hotword_page(hotwords.body)

    def _build_sidebar(self, sidebar):
        brand = tk.Frame(sidebar, background=SIDEBAR_BG)
        brand.pack(fill="x", padx=22, pady=(26, 18))
        ttk.Label(brand, text="SAI",
                  style="SidebarBrand.TLabel").pack(anchor="w")
        ttk.Label(brand, text="离线语音输入 · AI 智能体控制",
                  style="SidebarHint.TLabel").pack(anchor="w", pady=(3, 0))
        self.nav_buttons = []
        for page, title, _subtitle in self.page_specs:
            button = NavButton(sidebar, title, background=SIDEBAR_BG)
            button.command = lambda target=page, item=button: self._show_page(target, item)
            button.pack(fill="x", padx=12, pady=2)
            self.nav_buttons.append(button)

    def _build_header(self, content):
        header = tk.Frame(content, background=PAGE_BG, height=68)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)
        self.page_title = ttk.Label(header, text="", style="Header.TLabel")
        self.page_title.grid(row=0, column=0, sticky="sw", padx=26, pady=(16, 0))
        self.page_subtitle = ttk.Label(header, text="", style="HeaderSub.TLabel")
        self.page_subtitle.grid(row=1, column=0, sticky="nw", padx=26, pady=(3, 0))
        tk.Frame(content, background=DIVIDER, height=1).grid(row=1, column=0,
                                                             sticky="ew")

    def _build_action_bar(self, content):
        tk.Frame(content, background=DIVIDER, height=1).grid(row=3, column=0,
                                                             sticky="ew")
        bar = tk.Frame(content, background=PAGE_BG, height=64)
        bar.grid(row=4, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.columnconfigure(1, weight=1)
        self.status_dot = tk.Canvas(bar, width=10, height=10, background=PAGE_BG,
                                    highlightthickness=0)
        self.status_dot.grid(row=0, column=0, sticky="w", padx=(26, 8))
        ttk.Label(bar, textvariable=self.status,
                  style="Status.TLabel").grid(row=0, column=1, sticky="w")
        buttons = tk.Frame(bar, background=PAGE_BG)
        buttons.grid(row=0, column=2, sticky="e", padx=(12, 24))
        self.stop_button = PillButton(buttons, "停止", command=self._stop_processes,
                                      kind="danger", width=88, background=PAGE_BG)
        self.stop_button.pack(side="left", padx=(0, 10))
        self.save_button = PillButton(buttons, "保存设置", command=self._save,
                                      kind="secondary", width=100, background=PAGE_BG)
        self.save_button.pack(side="left", padx=(0, 10))
        self.start_button = PillButton(buttons, "保存并启动", command=self._start,
                                       kind="primary", width=116, background=PAGE_BG)
        self.start_button.pack(side="left")
        self.status.trace_add("write", lambda *_: self._refresh_status())
        self._refresh_status()

    def _show_page(self, page, selected=None):
        page.tkraise()
        for button in self.nav_buttons:
            button.set_selected(button is selected)
        for target, title, subtitle in self.page_specs:
            if target is page:
                self.page_title.configure(text=title)
                self.page_subtitle.configure(text=subtitle)
                break

    def _refresh_status(self):
        if not hasattr(self, "status_dot"):
            return
        text = self.status.get()
        if any(word in text for word in ("失败", "错误", "不可用", "无法", "缺少", "无效")):
            color = ERROR
        elif any(word in text for word in ("运行中", "完成", "已保存", "就绪")):
            color = SUCCESS
        elif text.startswith("未启动") or text == "已停止":
            color = "#C7C7CC"
        elif any(word in text for word in ("正在", "加载", "下载", "启动", "取消")):
            color = WARNING
        else:
            color = "#C7C7CC"
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    def _card(self, parent, title):
        card = Card(parent, title=title)
        card.pack(fill="x", pady=(0, 14))
        return card.body

    @staticmethod
    def _autowrap(container, label, offset=8):
        def resize(event):
            width = max(180, event.width - offset)
            if getattr(label, "_wrap_width", None) != width:
                label._wrap_width = width
                label.configure(wraplength=width)
        container.bind("<Configure>", resize, add="+")

    def _row(self, parent, row, label, widget, hint=None):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, style="Field.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=(7, 7))
        widget.grid(row=row, column=1, sticky="e", pady=(7, 7))
        next_row = row + 1
        if hint:
            hint_label = ttk.Label(parent, text=hint, style="Hint.TLabel",
                                   justify="left")
            hint_label.grid(row=next_row, column=0, columnspan=2, sticky="w",
                            pady=(0, 6))
            self._autowrap(parent, hint_label)
            next_row += 1
        return next_row

    def _switch_row(self, parent, row, variable, title, hint, divider=True):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=title, style="Field.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=(8, 0))
        Switch(parent, variable, background=CARD_BG).grid(
            row=row, column=1, sticky="e", pady=(8, 0))
        hint_label = ttk.Label(parent, text=hint, style="Hint.TLabel",
                               justify="left")
        hint_label.grid(row=row + 1, column=0, columnspan=2, sticky="w",
                        pady=(2, 6))
        self._autowrap(parent, hint_label)
        if not divider:
            return row + 2
        tk.Frame(parent, background=DIVIDER, height=1).grid(
            row=row + 2, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        return row + 3

    def _build_home_page(self, body):
        self._build_recognition_card(body)
        self._build_audio_card(body)
        self._build_shortcut_card(body)
        self._build_options_card(body)

    def _build_recognition_card(self, body):
        form = self._card(body, "识别设置")
        row = self._row(form, 0, "识别模型", ttk.Combobox(
            form, textvariable=self.vars["model_type"], state="readonly",
            values=tuple(MODEL_CHOICES.values()), width=42, font=ui_font()))
        self.quantization_box = ttk.Combobox(
            form, textvariable=self.vars["qwen_quantization"], state="readonly",
            values=("q5_k", "q4_k"), width=14, font=ui_font())
        row = self._row(form, row, "Qwen 量化", self.quantization_box,
                        hint="Q5_K 适合独立显卡，Q4_K 适合集成显卡或低显存；仅 Qwen3-ASR 使用。")
        self.model_status = ttk.Label(form, text="", style="Field.TLabel")
        row = self._row(form, row, "模型状态", self.model_status)
        self.model_advice = ttk.Label(form, text="", style="Hint.TLabel",
                                      justify="left")
        self.model_advice.grid(row=row, column=0, columnspan=2, sticky="w",
                               pady=(0, 10))
        self._autowrap(form, self.model_advice, offset=6)
        row += 1
        actions = ttk.Frame(form, style="Card.TFrame")
        actions.grid(row=row, column=0, columnspan=2, sticky="w")
        self.download_button = PillButton(
            actions, "下载模型", command=self._download_model, kind="primary",
            width=112, background=CARD_BG)
        self.download_button.pack(side="left")
        self.cancel_download_button = PillButton(
            actions, "取消下载", command=self.download_cancel.set, kind="secondary",
            width=96, background=CARD_BG)
        self.cancel_download_button.pack(side="left", padx=(10, 0))
        self.cancel_download_button.configure(state="disabled")
        PillButton(actions, "刷新状态", command=self._update_model_info,
                   kind="secondary", width=96,
                   background=CARD_BG).pack(side="left", padx=(10, 0))
        self.download_progress = ttk.Progressbar(form, maximum=100)
        self.download_progress.grid(row=row + 1, column=0, columnspan=2,
                                    sticky="ew", pady=(12, 0))
        self.download_progress.grid_remove()
        row += 2
        tk.Frame(form, background=DIVIDER, height=1).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        row += 1
        row = self._row(form, row, "推理后端", ttk.Combobox(
            form, textvariable=self.vars["onnx_provider"], state="readonly",
            values=("AUTO", "CPU"), width=14, font=ui_font()),
            hint="自动选择可用的显卡执行 ONNX 推理，遇到兼容问题时再切换为 CPU。")
        row = self._row(form, row, "识别语言", ttk.Combobox(
            form, textvariable=self.vars["language"], state="readonly",
            values=("auto", "chinese", "english", "japanese"), width=14,
            font=ui_font()))
        row = self._row(form, row, "快捷键阈值（秒）", ttk.Entry(
            form, textvariable=self.vars["threshold"], width=14, font=ui_font()),
            hint="按住快捷键的时长超过该值才会触发识别，默认 0.3 秒。")
        self._row(form, row, "上下文提示", ttk.Entry(
            form, textvariable=self.vars["context"], width=42, font=ui_font()),
            hint="随录音一起发送的识别提示文字，可留空。")

    def _build_audio_card(self, body):
        form = self._card(body, "录音")
        control = ttk.Frame(form, style="Card.TFrame")
        self.audio_box = ttk.Combobox(
            control, textvariable=self.vars["audio_device"], state="readonly",
            values=(DEFAULT_MIC,), width=36, font=ui_font())
        self.audio_box.pack(side="left", fill="x", expand=True)
        PillButton(control, "刷新", command=lambda: self._refresh_audio_devices(True),
                   kind="secondary", width=64, height=32, background=CARD_BG,
                   padx=10).pack(side="left", padx=(8, 0))
        self._row(form, 0, "录音设备", control,
                  hint="选择用于识别的麦克风；设备热插拔后点击刷新重新读取。")

    def _build_shortcut_card(self, body):
        form = self._card(body, "快捷键")
        shortcut = self.saved_shortcuts[0] if self.saved_shortcuts else DEFAULT_SHORTCUTS[0]
        self.shortcut_capture = ShortcutCapture(form, shortcut, width=170)
        self._row(form, 0, "录音快捷键", self.shortcut_capture,
                  hint="点击按键标签后，直接按键盘组合键或鼠标侧键完成设置，Esc 取消。")

    def _build_options_card(self, body):
        form = self._card(body, "选项")
        row = self._switch_row(
            form, 0, self.vars["llm_use_gpu"], "启用 GGUF GPU 加速",
            "为 Fun-ASR / Qwen 的 GGUF 解码启用显卡加速，关闭后仅使用 CPU。")
        row = self._switch_row(
            form, row, self.vars["paste"], "使用剪贴板粘贴输出",
            "用剪贴板粘贴代替逐字键入，适合长文本或输入法不兼容的程序。")
        row = self._switch_row(
            form, row, self.vars["keep_microphone_open"], "快速响应",
            "空闲时保持麦克风占用，缩短按下快捷键后的启动延迟。")
        self._row(form, row, "关闭窗口时", ttk.Combobox(
            form, textvariable=self.vars["close_behavior"], state="readonly",
            values=tuple(CLOSE_CHOICES.values()), width=20, font=ui_font()),
            hint="“每次询问”在关闭时弹出选择；“最小化到托盘”保持后台运行。")

    def _build_api_page(self, body):
        form = self._card(body, "服务连接")
        hints = {
            "asr_api_base_url": "局域网服务示例：http://192.168.1.10:8000/v1，地址需要带上端口号。",
            "asr_api_model": "填写服务端支持的模型名，例如 fun-asr-nano、sensevoice。",
        }
        row = 0
        for key, label in (
                ("asr_api_base_url", "服务地址"),
                ("asr_api_model", "模型名称"),
                ("asr_api_key", "API Key"),
                ("asr_api_timeout", "请求超时（秒）")):
            row = self._row(form, row, label, ttk.Entry(
                form, textvariable=self.vars[key], width=42,
                show="*" if key == "asr_api_key" else "", font=ui_font()),
                hints.get(key))
        row = self._switch_row(
            form, row, self.vars["asr_api_allow_http"], "允许明文 HTTP",
            "仅用于可信局域网服务；开启后该地址直连，不经过系统代理。",
            divider=False)
        self.api_test_result = tk.StringVar()
        test_row = ttk.Frame(form, style="Card.TFrame")
        test_row.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.api_test_button = PillButton(
            test_row, "测试连接", command=self._test_api, kind="secondary",
            width=96, background=CARD_BG)
        self.api_test_button.pack(side="left")
        self.api_test_label = ttk.Label(
            test_row, textvariable=self.api_test_result, style="Hint.TLabel",
            justify="left")
        self.api_test_label.pack(side="left", padx=(12, 0))
        self._autowrap(form, self.api_test_label, offset=140)
        row += 1
        warning = ttk.Label(
            form, text="录音将上传至所配置的服务，可能产生费用；密钥以明文保存在本机配置文件中。",
            style="Warning.TLabel", justify="left")
        warning.grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 4))
        self._autowrap(form, warning)

    def _build_hardware_page(self, body):
        card = Card(body, title="本机硬件")
        card.pack(fill="x", pady=(0, 14))
        form = card.body
        form.columnconfigure(0, weight=1)
        self.hardware_text = tk.Text(
            form, height=15, wrap="word", state="disabled", font=ui_font(9),
            background=CARD_BG, foreground=TEXT, relief="flat",
            highlightthickness=0, borderwidth=0, padx=2, pady=2,
            spacing1=2, spacing3=4)
        self.hardware_text.grid(row=0, column=0, sticky="nsew")
        hardware_buttons = ttk.Frame(form, style="Card.TFrame")
        hardware_buttons.grid(row=1, column=0, columnspan=2, sticky="w",
                              pady=(14, 0))
        self.detect_button = PillButton(
            hardware_buttons, "重新检测", command=self._detect_hardware,
            kind="secondary", width=96, background=CARD_BG)
        self.detect_button.pack(side="left")
        self.apply_hardware_button = PillButton(
            hardware_buttons, "应用建议到配置", command=self._apply_hardware,
            kind="primary", width=132, background=CARD_BG)
        self.apply_hardware_button.pack(side="left", padx=(10, 0))
        self.apply_hardware_button.configure(state="disabled")

    def _shortcut_data(self):
        if self.shortcut_capture is None:
            return []
        value = dict(self.shortcut_capture.value)
        value["suppress"] = True
        value["hold_mode"] = True
        return [value] if value.get("key") else []

    def _set_hardware_text(self, text):
        lines = max(8, min(26, text.count("\n") + 2))
        self.hardware_text.configure(state="normal", height=lines)
        self.hardware_text.delete("1.0", "end")
        self.hardware_text.insert("1.0", text)
        self.hardware_text.configure(state="disabled")

    def _detect_hardware(self):
        if self.hardware_busy:
            return
        self.hardware_busy = True
        self.detect_button.configure(state="disabled")
        self.apply_hardware_button.configure(state="disabled")
        self._set_hardware_text("正在检测 CPU、内存和显卡...")

        def worker():
            try:
                from core.hardware_info import detect_hardware
                self.hardware_events.put((detect_hardware(), None))
            except Exception as exc:
                self.hardware_events.put((None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        self.hardware_after = self.after(100, self._poll_hardware)

    def _poll_hardware(self):
        self.hardware_after = None
        try:
            info, error = self.hardware_events.get_nowait()
        except queue.Empty:
            self.hardware_after = self.after(100, self._poll_hardware)
            return
        self.hardware_busy = False
        self.hardware_info = info
        self.detect_button.configure(state="normal")
        if error:
            self._set_hardware_text(f"设备检测失败：{error}\n可以继续手动选择模型。")
        else:
            from core.hardware_info import format_hardware
            self._set_hardware_text(format_hardware(info))
            self.apply_hardware_button.configure(state="normal")

    def _apply_hardware(self):
        if not self.hardware_info or self.hardware_busy:
            return
        if self.running or self.starting or self.download_thread is not None:
            messagebox.showinfo("暂时无法应用", "请先停止识别或等待模型下载结束。", parent=self)
            return
        recommendation = self.hardware_info["recommendation"]
        if not messagebox.askokcancel(
                "应用硬件建议", recommendation["reason"] +
                "\n\n将更改模型、量化和 GPU 设置，麦克风、API 密钥和热词保持不变。\n"
                "尚不会保存或启动，是否应用？", parent=self):
            return
        for key, value in recommendation["settings"].items():
            if key not in self.vars:
                continue
            self.vars[key].set(MODEL_CHOICES[value] if key == "model_type" else value)
        self.status.set("建议已填入识别设置；确认模型文件就绪后保存并启动")

    def _build_hotword_page(self, body):
        from core.desktop_hotwords import HotwordEditor
        try:
            self.hotword_page = HotwordEditor(body, CONFIG.parent)
        except (OSError, UnicodeError) as exc:
            self.hotword_page = None
            label = ttk.Label(body, text=f"无法打开热词文件：{exc}",
                              style="Danger.TLabel", justify="left")
            label.pack(anchor="w")
            self._autowrap(body, label)
            return
        self.hotword_page.pack(fill="x")

    def _test_api(self):
        if self.api_test_thread is not None:
            return
        if self.running or self.starting:
            messagebox.showinfo("请先停止识别", "停止语音识别后再测试连接。", parent=self)
            return
        from core.api_transcription_config import validate_api_settings
        try:
            base_url, model, timeout = validate_api_settings(
                self.vars["asr_api_base_url"].get(),
                self.vars["asr_api_model"].get(),
                self.vars["asr_api_timeout"].get(),
                self.vars["asr_api_allow_http"].get())
        except ValueError as exc:
            self.api_test_result.set(str(exc))
            self.api_test_label.configure(style="Danger.TLabel")
            return
        key = self.vars["asr_api_key"].get()
        allow_http = self.vars["asr_api_allow_http"].get()
        self.api_test_button.configure(state="disabled")
        self.api_test_result.set("正在连接并发送 1 秒测试音频...")
        self.api_test_label.configure(style="Hint.TLabel")

        def worker():
            try:
                import numpy as np
                from core.server.engines.openai_asr import APIConfig, OpenAIASREngine
                engine = OpenAIASREngine(APIConfig(
                    base_url=base_url, model=model, api_key=key, timeout=timeout,
                    allow_http=allow_http))
                stream = engine.create_stream()
                stream.accept_waveform(16000, np.zeros(16000, dtype="float32"))
                engine.decode_stream(stream)
                text = (stream.result.text or "").strip()
                self.api_test_events.put(
                    ("ok", f"连接成功，服务返回了 {len(text)} 个字符。"))
            except Exception as exc:
                self.api_test_events.put(("error", str(exc)))

        self.api_test_thread = threading.Thread(target=worker, daemon=True)
        self.api_test_thread.start()
        self.after(120, self._poll_api_test)

    def _poll_api_test(self):
        try:
            event, message = self.api_test_events.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_api_test)
            return
        self.api_test_thread.join()
        self.api_test_thread = None
        self.api_test_button.configure(state="normal")
        self.api_test_result.set(message)
        self.api_test_label.configure(
            style="Success.TLabel" if event == "ok" else "Danger.TLabel")

    def _update_model_info(self):
        info = MODEL_INFO.get(self._model_key())
        if not info or not hasattr(self, "model_status"):
            return
        quantization = self.vars["qwen_quantization"].get()
        missing = missing_files(ROOT, self._model_key(), quantization)
        status = (f"文件齐全（{len(info['files'])} 个，可校验）" if not missing
                  else f"模型未就绪，缺少或为空的文件：{len(missing)} / {len(info['files'])}")
        if self._model_key() == "openai_api":
            status = "无需本地模型；服务可用性以实际转写结果为准"
        advice = info["advice"]
        if self._model_key() == "qwen_asr":
            advice = ("Q5_K：独显优先，解码器约 1.47 GB。" if quantization == "q5_k"
                      else "Q4_K：集显／低显存可尝试，解码器约 1.28 GB。")
            advice += "共用 INT4 CPU 编码器。"
        self.quantization_box.configure(
            state="readonly" if self._model_key() == "qwen_asr" else "disabled")
        self.model_status.configure(
            text=status, style="Success.TLabel" if not missing else "Danger.TLabel")
        self.model_advice.configure(text=f"建议：{advice}")
        busy = self.download_thread is not None
        self.download_button.configure(
            text="下载模型" if missing else "校验／修复模型",
            state="disabled" if busy or self._model_key() == "openai_api" else "normal")

    def _download_model(self):
        if self.download_thread is not None:
            return
        if self.running or self.starting:
            messagebox.showinfo("请先停止识别", "停止语音识别后再下载或校验模型。", parent=self)
            return
        name = self._model_key()
        quantization = self.vars["qwen_quantization"].get()
        if name == "openai_api":
            return
        if not messagebox.askokcancel(
                "从 ModelScope 下载",
                f"模型：{MODEL_INFO[name]['label']} {quantization if name == 'qwen_asr' else ''}\n保存位置：{ROOT / 'models'}\n\n"
                "将联网校验并下载缺少或损坏的文件，可能需要数 GB 空间。\n是否继续？",
                parent=self):
            return
        self.download_cancel.clear()
        self.download_progress.configure(value=0)
        self.download_progress.grid()
        self.status.set("正在连接 ModelScope...")
        self.start_button.configure(state="disabled")
        self.cancel_download_button.configure(state="normal")

        def worker():
            last_update = 0
            def progress(label, done, total):
                nonlocal last_update
                now = time.monotonic()
                if now - last_update >= 0.15 or done == total:
                    self.download_events.put(("progress", (label, done, total)))
                    last_update = now
            try:
                download_model(name, ROOT, progress, self.download_cancel, quantization)
                self.download_events.put(("done", "模型下载并校验完成，可以启动"))
            except DownloadCancelled:
                self.download_events.put(("done", "下载已取消，已完成的模型文件保留"))
            except PermissionError:
                self.download_events.put(("error", "安装目录不可写，请安装到当前用户有写入权限的目录。"))
            except Exception as exc:
                self.download_events.put(("error", f"模型下载失败：{exc}"))

        self.download_thread = threading.Thread(target=worker, daemon=True)
        self.download_thread.start()
        self._update_model_info()
        self.after(150, self._poll_download)

    def _poll_download(self):
        while True:
            try:
                event, value = self.download_events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                label, done, total = value
                self.download_progress.configure(value=100 * done / total if total else 0)
                self.status.set(f"{label} | {done / 1048576:.1f} / {total / 1048576:.1f} MiB")
            else:
                self.download_thread.join()
                self.download_thread = None
                self.cancel_download_button.configure(state="disabled")
                self.start_button.configure(state="normal")
                self.download_progress.configure(value=0)
                self.download_progress.grid_remove()
                self.status.set(value)
                self._update_model_info()
                if event == "error":
                    messagebox.showerror("模型下载失败", value, parent=self)
                return
        self.after(150, self._poll_download)

    def _refresh_audio_devices(self, refresh=False):
        if not hasattr(self, "audio_box"):
            return
        try:
            selection = (self.audio_devices.get(self.vars["audio_device"].get())
                         if refresh else self.saved_audio_device)
            if refresh:
                # The GUI owns no audio streams; the recording client is separate.
                sd._terminate()
                sd._initialize()
            devices = physical_input_devices()
            mapping = {DEFAULT_MIC: None}
            for device in devices:
                name = " ".join(device["name"].split())
                label = name
                if label in mapping:
                    label = f"{name} ({device['hostapi']})"
                mapping[label] = device
            current = DEFAULT_MIC
            if selection is not None:
                try:
                    index = resolve_input_device(selection, devices)
                    current = next(label for label, device in mapping.items()
                                   if device and device["index"] == index)
                except ValueError:
                    name = selection.get("name", str(selection)) if isinstance(selection, dict) else str(selection)
                    current = f"设备不可用：{name}"
                    mapping[current] = selection
            self.audio_devices = mapping
            self.audio_box.configure(values=tuple(mapping))
            self.vars["audio_device"].set(current)
            if not self.running and not self.starting:
                self.status.set(f"未启动 | 已发现 {len(devices)} 个录音设备")
        except Exception as exc:
            self.status.set(f"读取录音设备失败：{exc}")

    def _data(self):
        try:
            threshold = float(self.vars["threshold"].get())
            if not math.isfinite(threshold) or threshold < 0:
                raise ValueError
        except ValueError:
            raise ValueError("阈值必须是非负数字")
        data = {key: var.get() for key, var in self.vars.items()}
        data["model_type"] = self._model_key()
        if data["model_type"] == "openai_api":
            from core.api_transcription_config import validate_api_settings
            (data["asr_api_base_url"], data["asr_api_model"],
             data["asr_api_timeout"]) = validate_api_settings(
                data["asr_api_base_url"], data["asr_api_model"],
                data["asr_api_timeout"], data["asr_api_allow_http"])
        selected_device = self.vars["audio_device"].get()
        data["audio_device"] = self.audio_devices[selected_device]
        data["threshold"] = threshold
        data["child_tray"] = False
        data["shortcuts"] = self._shortcut_data()
        data["close_action"] = self._close_key()
        data["close_action_remembered"] = data["close_action"] in ("tray", "exit")
        return data

    def _close_key(self):
        return CLOSE_LABELS.get(self.vars["close_behavior"].get(), "ask")

    def _model_key(self):
        selected = self.vars["model_type"].get()
        for key, label in MODEL_CHOICES.items():
            if selected == label:
                return key
        return selected

    def _save(self, quiet=False):
        page = getattr(self, "hotword_page", None)
        if page is not None and page.dirty_names() and not page.save_dirty():
            return False
        try:
            CONFIG.write_text(json.dumps(self._data(), ensure_ascii=False, indent=2),
                              encoding="utf-8")
        except (ValueError, OSError) as exc:
            messagebox.showerror("配置无效", str(exc))
            return False
        if not quiet:
            messagebox.showinfo("已保存", "设置已保存到 config_gui.json")
        return True

    def _start(self):
        if self.download_thread is not None:
            messagebox.showinfo("模型下载中", "请等待下载完成或取消下载后再启动。", parent=self)
            return
        if self._model_key() == "openai_api" and not messagebox.askokcancel(
                "启用远程语音识别",
                "录音和上下文提示将发送至配置的 API 服务，可能产生费用。\n是否继续？",
                parent=self):
            return
        try:
            selection = self.audio_devices[self.vars["audio_device"].get()]
            device_id = resolve_input_device(selection)
            device = sd.query_devices(device_id, kind="input")
            channels = min(2, device["max_input_channels"])
            device_rate = int(round(float(device.get("default_samplerate", 0) or 0)))
            rates = []
            for rate in (48000, device_rate, 44100, 32000, 16000):
                if rate > 0 and rate not in rates:
                    rates.append(rate)
            last_error = None
            for rate in rates:
                for candidate_channels in ((channels, 1) if channels > 1 else (1,)):
                    try:
                        sd.check_input_settings(
                            device=device_id, channels=candidate_channels,
                            dtype="float32", samplerate=rate)
                        last_error = None
                        break
                    except sd.PortAudioError as exc:
                        last_error = exc
                if last_error is None:
                    break
            if last_error is not None:
                raise last_error
        except Exception as exc:
            messagebox.showerror("录音设备不可用", f"请刷新列表并选择可用的麦克风。\n\n{exc}")
            return
        if not self._save(quiet=True):
            return
        self._stop_processes()
        (CONFIG.parent / "logs" / "asr-error.json").unlink(missing_ok=True)
        info = MODEL_INFO[self._model_key()]
        missing = missing_files(ROOT, self._model_key(), self.vars["qwen_quantization"].get())
        if self._model_key() in ("qwen_asr", "fun_asr_nano"):
            llama_dir = ROOT / "core" / "server" / "engines" / "llama" / "bin"
            dll_name = "llama.dll" if os.name == "nt" else "libllama.so"
            if not (llama_dir / dll_name).exists():
                missing.append(
                    f"core/server/engines/llama/bin/{dll_name}（GGUF 推理运行库）")
        if missing:
            messagebox.showerror(
                "模型未安装",
                f"当前选择：{info['label']}\n\n缺少以下文件：\n" +
                "\n".join(missing) +
                "\n\n请点击识别设置页的“下载模型”，完成后再启动。",
            )
            self._update_model_info()
            return
        self.starting = True
        self.deadline = time.monotonic() + 180
        self.status.set("正在加载模型...")
        self.start_button.configure(state="disabled")
        try:
            self._spawn("server")
        except OSError as exc:
            self._fail(str(exc))
            return
        self.monitor = self.after(500, self._check_children)

    def _spawn(self, role):
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(ROOT / "sai.py"))
        command.append(f"--{role}")
        log_dir = CONFIG.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        ready = log_dir / f".ready-{role}-{uuid.uuid4().hex}"
        self.ready_files[role] = ready
        recording = log_dir / f".recording-{role}-{uuid.uuid4().hex}"
        self.recording_files[role] = recording
        env = os.environ.copy()
        env.update(SAI_GUI="1", SAI_READY_FILE=str(ready),
                   SAI_RECORDING_FLAG=str(recording),
                   PYTHONIOENCODING="utf-8")
        with (log_dir / f"{role}_bootstrap.log").open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command, cwd=CONFIG.parent, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        process.sai_role = role
        self.processes.append(process)

    def _check_children(self):
        self.monitor = None
        error_path = CONFIG.parent / "logs" / "asr-error.json"
        if error_path.exists():
            try:
                error = json.loads(error_path.read_text(encoding="utf-8"))["error"]
                error_path.unlink()
                self.status.set(error)
                if self.tray_icon:
                    self.tray_icon.notify(error, "语音识别失败")
            except (OSError, ValueError, KeyError, NotImplementedError):
                pass
        failed = [p for p in self.processes if p.poll() is not None]
        if failed:
            self._fail("组件退出：" + ", ".join(
                f"{getattr(p, 'sai_role', 'unknown')} 退出码 {p.returncode}"
                for p in failed))
            return
        if self.starting:
            if time.monotonic() > self.deadline:
                self._fail("启动超时，请查看日志。")
                return
            if self.ready_files["server"].exists() and "client" not in self.ready_files:
                self.status.set("模型已就绪，正在打开麦克风...")
                try:
                    self._spawn("client")
                except OSError as exc:
                    self._fail(str(exc))
                    return
            client_ready = self.ready_files.get("client")
            if client_ready and client_ready.exists():
                self.starting = False
                self.running = True
                self.status.set("运行中 | CapsLock 按住录音，松开输入 | F8 开始/停止")
                self.start_button.configure(state="normal", text="保存并重启")
                if self.tray_icon and self.tray_icon.visible:
                    self.withdraw()
        self._refresh_recording_indicator()
        if self.processes:
            self.monitor = self.after(500, self._check_children)

    def _refresh_recording_indicator(self):
        """按下快捷键录音时，托盘图标换成红色，松开恢复蓝色"""
        flag = self.recording_files.get("client")
        if self.tray_icon is None or not self.tray_images:
            return
        recording = bool(flag and flag.exists())
        if recording == self.tray_recording:
            return
        self.tray_recording = recording
        try:
            self.tray_icon.icon = self.tray_images[recording]
            self.tray_icon.title = f"{APP_TITLE} · 正在录音" if recording else APP_TITLE
        except Exception:
            pass

    def _fail(self, reason):
        self._stop_processes()
        self._show()
        self.status.set("启动失败")
        details = [reason]
        for name in ("server_bootstrap.log", "server_latest.log",
                     "client_bootstrap.log", "client_latest.log"):
            log_file = CONFIG.parent / "logs" / name
            if log_file.exists():
                details.append(log_file.name + ":\n" +
                               log_file.read_text(encoding="utf-8", errors="replace")[-1800:])
        self._show_error_details("\n\n".join(details))

    def _show_error_details(self, details):
        """Show long startup errors in a bounded, scrollable window."""
        dialog = tk.Toplevel(self)
        dialog.title("组件运行失败")
        dialog.transient(self)
        dialog.minsize(520, 260)

        screen_width = dialog.winfo_screenwidth()
        screen_height = dialog.winfo_screenheight()
        width = min(900, max(520, screen_width - 120))
        height = min(620, max(260, screen_height - 120))
        dialog.geometry(f"{width}x{height}")
        dialog.maxsize(screen_width - 40, screen_height - 80)

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        text = tk.Text(frame, wrap="word", state="normal", font=ui_font(9),
                       background=CARD_BG, foreground=TEXT, relief="flat",
                       highlightthickness=0, borderwidth=0, padx=10, pady=8)
        text.insert("1.0", details)
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(frame, orient="vertical",
                                  style="Slim.Vertical.TScrollbar",
                                  command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)

        ttk.Button(frame, text="关闭", command=dialog.destroy).grid(
            row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        text.focus_set()

    def _stop_processes(self):
        if self.monitor is not None:
            self.after_cancel(self.monitor)
            self.monitor = None
        for process in self.processes:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=10,
                    )
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.processes.clear()
        for ready in self.ready_files.values():
            ready.unlink(missing_ok=True)
        self.ready_files.clear()
        for recording in self.recording_files.values():
            recording.unlink(missing_ok=True)
        self.recording_files.clear()
        self.starting = self.running = False
        self._refresh_recording_indicator()
        self.status.set("已停止")
        if hasattr(self, "start_button"):
            self.start_button.configure(state="normal", text="保存并启动")

    def _hide_or_close(self):
        if not self.processes:
            self._close()
            return
        action = self._close_key()
        if action in ("tray", "exit"):
            self._apply_close_action(action)
            return
        self._show_close_dialog()

    def _show_close_dialog(self):
        tray_available = bool(self.tray_icon and self.tray_icon.visible)
        dialog = tk.Toplevel(self)
        dialog.title("关闭 SAI")
        dialog.transient(self)
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="关闭窗口时：").pack(anchor="w")

        default_action = "tray" if tray_available else "exit"
        action = tk.StringVar(value=default_action)
        ttk.Radiobutton(
            frame, text="最小化到托盘，继续运行",
            variable=action, value="tray",
            state="normal" if tray_available else "disabled",
        ).pack(anchor="w", pady=(10, 4))
        ttk.Radiobutton(
            frame, text="直接关闭程序",
            variable=action, value="exit",
        ).pack(anchor="w")

        remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="记住我的选择",
            variable=remember,
        ).pack(anchor="w", pady=(12, 4))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))

        def confirm():
            selected = action.get()
            if remember.get():
                self.vars["close_behavior"].set(CLOSE_CHOICES[selected])
                self._save_close_preference(selected)
            dialog.destroy()
            self._apply_close_action(selected)

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(
            side="right", padx=(8, 0))
        ttk.Button(buttons, text="确定", command=confirm).pack(side="right")
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.focus_set()

        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _save_close_preference(self, action):
        try:
            data = {}
            if CONFIG.exists():
                data = json.loads(CONFIG.read_text(encoding="utf-8"))
            data["close_behavior"] = CLOSE_CHOICES[action]
            data["close_action"] = action
            data["close_action_remembered"] = True
            CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _apply_close_action(self, action):
        if action == "tray" and self.tray_icon and self.tray_icon.visible:
            self.withdraw()
        else:
            self._close()

    def _close(self):
        if self.download_thread is not None:
            self.download_cancel.set()
            self.status.set("正在取消下载，请稍候；网络请求最多等待 30 秒")
            self.after(200, self._close)
            return
        if self.hardware_after is not None:
            self.after_cancel(self.hardware_after)
            self.hardware_after = None
        page = getattr(self, "hotword_page", None)
        if page is not None and page.dirty_names():
            answer = messagebox.askyesnocancel(
                "未保存的热词", "保存热词与替换的修改后退出？", parent=self)
            if answer is None:
                return
            if answer and not page.save_dirty():
                return
        self._stop_processes()
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()

    def _start_tray(self):
        self.tray_icon = None
        self.tray_images = {}
        self.tray_recording = False
        try:
            import pystray
            from PIL import Image
            image = Image.open(ROOT / "assets" / "icon.ico")
            try:
                recording_image = Image.open(ROOT / "assets" / "icon-recording.ico")
            except (OSError, ValueError):
                recording_image = image
            self.tray_images = {False: image, True: recording_image}
            menu = pystray.Menu(
                pystray.MenuItem(
                    "打开数据目录", lambda icon, item: self.after(
                        0, lambda: os.startfile(str(CONFIG.parent)))),
                pystray.MenuItem(
                    "打开配置", lambda icon, item: self.after(0, self._show),
                    default=True),
                pystray.MenuItem(
                    "退出", lambda icon, item: self.after(0, self._close)),
            )
            self.tray_icon = pystray.Icon(
                "SAI", image, APP_TITLE, menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as exc:
            self.status.set(f"托盘不可用：{exc}")

    def _show(self):
        self.deiconify()
        self.lift()
        self.focus_force()


if __name__ == "__main__":
    os.chdir(ROOT)
    if not activate_existing():
        Launcher().mainloop()
