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
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
import sounddevice as sd
from core import update_checker
from core.audio_devices import physical_input_devices, resolve_input_device
from core.runtime_paths import APP_DIR, DATA_DIR, initialize_user_data
from core.tools.llama_runtime import verify_llama_runtime
from core.model_download import (download_llama_runtime, download_model,
                                 DownloadCancelled, missing_files, model_files)
from core.desktop_widgets import (
    BORDER, CARD_BG, DIVIDER, ERROR, FONT_FAMILY, HOVER_BG, PAGE_BG, PRIMARY,
    SELECTED_BG, SIDEBAR_BG, SUCCESS, TEXT, TEXT_SECONDARY, WARNING,
    Card, NavButton, PillButton, ScrollPage, ShortcutCapture, Switch,
    ui_font,
)
from core.shortcut_keys import shortcut_label
from config_client import __version__ as APP_VERSION

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG = DATA_DIR / "config_gui.json"
DEFAULT_MIC = "系统默认录音设备"
DEFAULT_SHORTCUTS = [{"key": "caps_lock", "type": "keyboard", "enabled": True}]
PUNC_MODEL = (
    "models/Punct-CT-Transformer/"
    "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12/model.onnx"
)
GGUF_MODELS = ("qwen_asr", "fun_asr_nano")


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
        self.download_error_title = "下载失败"
        self.start_after_repair = False
        self.api_test_events = queue.Queue()
        self.api_test_thread = None
        self.hardware_events = queue.Queue()
        self.hardware_busy = False
        self.hardware_info = None
        self.hardware_after = None
        self.update_check_events = queue.Queue()
        self.update_check_thread = None
        self.update_download_events = queue.Queue()
        self.update_download_thread = None
        self.update_cancel = threading.Event()
        self.update_info = None
        self.update_dialog = None
        self.update_badge_visible = False
        self.update_state = update_checker.read_state()
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
            "auto_check_update": tk.BooleanVar(value=True),
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
        self.paste_capture = None
        self._load()
        self._build()
        self._start_tray()
        self.vars["model_type"].trace_add("write", lambda *_: self._update_model_info())
        self.vars["qwen_quantization"].trace_add("write", lambda *_: self._update_model_info())
        self._update_model_info()
        self._refresh_audio_devices()
        self.protocol("WM_DELETE_WINDOW", self._hide_or_close)
        self.hardware_after = self.after(100, self._detect_hardware)
        # 启动几秒后再检查更新，避免和模型/设备检测抢启动时间
        self.after(5000, self._auto_check_update)

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
                    if key in ("audio_device", "paste"):
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
        brand_row = tk.Frame(brand, background=SIDEBAR_BG)
        brand_row.pack(anchor="w", fill="x")
        ttk.Label(brand_row, text="SAI",
                  style="SidebarBrand.TLabel").pack(side="left")
        # 有新版时显示的“new”徽标，默认隐藏
        self.update_badge = PillButton(
            brand_row, "new", command=self._open_update_dialog, kind="badge",
            width=42, height=20, radius=10, background=SIDEBAR_BG,
            font=ui_font(8, "bold"), padx=6)
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
        self.runtime_button = PillButton(
            actions, "修复运行库", command=self._repair_runtime, kind="secondary",
            width=112, background=CARD_BG)
        self.runtime_button.pack(side="left", padx=(10, 0))
        self.download_progress = ttk.Progressbar(form, maximum=100)
        self.download_progress.grid(row=row + 1, column=0, columnspan=2,
                                    sticky="ew", pady=(12, 0))
        self.download_progress.grid_remove()
        row += 2
        self.runtime_status = ttk.Label(form, text="", style="Field.TLabel")
        row = self._row(form, row, "推理运行库", self.runtime_status,
                        hint="GGUF 模型（Qwen3-ASR、Fun-ASR）依赖该运行库。"
                             "被杀毒软件破坏、误删或版本不符时会在“模型状态”旁提示，"
                             "点“修复运行库”可联网下载并校验后自动覆盖。")
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
            hint="写专业术语、人名、项目名等，作为解码提示随录音发给服务端。"
                 "仅 Fun-ASR、Qwen3-ASR 等带 LLM 解码器的模型和远端 API 会用到，"
                 "Paraformer、SenseVoice 会忽略；可留空。")

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
        shortcut = self._main_shortcut()
        self.shortcut_capture = ShortcutCapture(form, shortcut, width=170)
        row = self._row(form, 0, "录音快捷键", self.shortcut_capture,
                        hint="点击按键标签后，直接按键盘组合键或鼠标侧键完成设置，Esc 取消。")
        paste_shortcut = self._paste_shortcut()
        self.paste_capture = ShortcutCapture(form, paste_shortcut, width=170)
        self._row(form, row, "粘贴快捷键", self.paste_capture,
                  hint="用这个键录音，松开后一律用剪贴板 Ctrl+V 粘贴上屏，适合远程桌面、"
                       "虚拟机等逐字输入不好使的场景。建议用鼠标侧键或单独的字母键，"
                       "不要用 Alt+CapsLock——它是系统的输入法切换组合，会被抑制导致输入法异常。")

    def _main_shortcut(self):
        for item in self.saved_shortcuts:
            if not item.get("paste"):
                return item
        return DEFAULT_SHORTCUTS[0]

    def _paste_shortcut(self):
        for item in self.saved_shortcuts:
            if item.get("paste"):
                return item
        return {"key": "", "type": "keyboard"}

    def _build_options_card(self, body):
        form = self._card(body, "选项")
        row = self._switch_row(
            form, 0, self.vars["llm_use_gpu"], "启用 GGUF GPU 加速",
            "对 Fun-ASR / Qwen 的 GGUF 解码启用显卡加速，关闭后仅使用 CPU。")
        row = self._switch_row(
            form, row, self.vars["keep_microphone_open"], "快速响应",
            "空闲时保持麦克风占用，缩短按下快捷键后的启动延迟。")
        row = self._row(form, row, "关闭窗口时", ttk.Combobox(
            form, textvariable=self.vars["close_behavior"], state="readonly",
            values=tuple(CLOSE_CHOICES.values()), width=20, font=ui_font()),
            hint="“每次询问”在关闭时弹出选择；“最小化到托盘”保持后台运行。")
        row = self._switch_row(
            form, row, self.vars["auto_check_update"], "自动检查更新",
            "启动后查询 GitHub 发布页；有新版本时在左上角 SAI 旁显示 new 徽标。")
        version_row = ttk.Frame(form, style="Card.TFrame")
        version_row.grid(row=row, column=0, columnspan=2, sticky="ew",
                         pady=(4, 0))
        ttk.Label(version_row, text=f"当前版本 v{APP_VERSION}",
                  style="Hint.TLabel").pack(side="left")
        PillButton(version_row, "检查更新",
                   command=lambda: self._check_update(manual=True),
                   kind="secondary", width=96, height=32, background=CARD_BG,
                   padx=10).pack(side="right")

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

    def _shortcut_entry(self, capture, paste=False):
        if capture is None:
            return None
        value = dict(capture.value)
        if not value.get("key"):
            return None
        value["suppress"] = True
        value["hold_mode"] = True
        if paste:
            value["paste"] = True
        return value

    def _shortcut_data(self):
        main = self._shortcut_entry(getattr(self, "shortcut_capture", None))
        paste = self._shortcut_entry(getattr(self, "paste_capture", None), paste=True)
        if paste and main and paste.get("key") == main.get("key"):
            paste = None
        return [entry for entry in (main, paste) if entry]

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
        self._update_runtime_status(busy)

    @staticmethod
    def _runtime_base_dir():
        return ROOT / "core" / "server" / "engines" / "llama"

    def _runtime_report(self):
        return verify_llama_runtime(self._runtime_base_dir())

    def _update_runtime_status(self, busy=False):
        """刷新“推理运行库”一行：仅 GGUF 模型需要该运行库"""
        if not hasattr(self, "runtime_status"):
            return
        if self._model_key() not in GGUF_MODELS:
            self.runtime_status.configure(text="当前模型无需该运行库",
                                          style="Hint.TLabel")
            self.runtime_button.configure(state="disabled")
            return
        report = self._runtime_report()
        if report.fatal:
            style = "Danger.TLabel"
        elif report.needs_repair or report.warnings:
            style = "Field.TLabel"
        else:
            style = "Success.TLabel"
        self.runtime_status.configure(text=report.summary, style=style)
        self.runtime_button.configure(state="disabled" if busy else "normal")

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

        def work(progress, cancel):
            download_model(name, ROOT, progress, cancel, quantization)
            return "模型下载并校验完成，可以启动"

        self._run_download(work, "正在连接 ModelScope...",
                           fail_prefix="模型下载失败", error_title="模型下载失败")

    def _repair_runtime(self, confirm=True):
        """检查 GGUF 运行库，缺失或损坏时联网下载并覆盖安装"""
        if self.download_thread is not None:
            return
        if self.running or self.starting:
            messagebox.showinfo("请先停止识别", "停止语音识别后再检测或修复运行库。", parent=self)
            return
        report = self._runtime_report()
        if not report.needs_repair:
            if confirm:
                messagebox.showinfo(
                    "运行库正常",
                    f"llama.cpp {report.tag} 运行库完整（{len(report.files)} 个文件）。"
                    + ("\n\n" + "\n".join(report.warnings) if report.warnings else ""),
                    parent=self)
            return
        if confirm and not messagebox.askokcancel(
                "修复推理运行库",
                "检测到以下问题：\n" + "\n".join(report.problems) +
                "\n\n将从官网下载 llama.cpp 运行库（约 33 MB）并校验后覆盖安装。\n"
                "下载会走系统代理设置；如无法访问 GitHub，也可自行下载后解压到：\n"
                f"{self._runtime_base_dir() / 'bin'}\n\n是否继续？",
                parent=self):
            return

        def work(progress, cancel):
            return download_llama_runtime(ROOT, progress, cancel)

        self._run_download(work, "正在连接 GitHub...",
                           fail_prefix="运行库修复失败", error_title="运行库修复失败")

    def _run_download(self, work, start_status, fail_prefix="下载失败", error_title="下载失败"):
        """在后台线程执行 work(progress, cancel)，完成后把消息投递到 download_events"""
        self.download_cancel.clear()
        self.download_error_title = error_title
        self.download_progress.configure(value=0)
        self.download_progress.grid()
        self.status.set(start_status)
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
                message = work(progress, self.download_cancel)
                self.download_events.put(("done", message))
            except DownloadCancelled:
                self.download_events.put(("done", "下载已取消，已完成的文件保留"))
            except PermissionError:
                self.download_events.put(("error", "安装目录不可写，请安装到当前用户有写入权限的目录。"))
            except Exception as exc:
                self.download_events.put(("error", f"{fail_prefix}：{exc}"))

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
                repair_pending = self.start_after_repair
                self.start_after_repair = False
                if event == "error":
                    messagebox.showerror(getattr(self, "download_error_title", "下载失败"),
                                         value, parent=self)
                    return
                if repair_pending:
                    self._start()
                return
        self.after(150, self._poll_download)

    # ------------------------------------------------------------------
    # 更新提醒：检查 GitHub Release、下载安装包、静默升级
    # ------------------------------------------------------------------

    def _show_update_badge(self, visible):
        """左上角“new”徽标的显隐"""
        if visible == self.update_badge_visible:
            return
        self.update_badge_visible = visible
        if visible:
            self.update_badge.pack(side="left", padx=(8, 0))
        else:
            self.update_badge.pack_forget()

    def _auto_check_update(self):
        """启动后的自动检查；受“自动检查更新”开关和 24 小时间隔限制"""
        if not self.vars["auto_check_update"].get():
            return
        if not update_checker.should_check(self.update_state):
            return
        self._check_update()

    def _check_update(self, manual=False):
        if self.update_check_thread is not None:
            return
        if manual:
            # 检查通常不到一秒；结束后恢复原来的状态文本
            self._status_before_check = self.status.get()
            self.status.set("正在检查更新...")

        def worker():
            try:
                info = update_checker.fetch_latest_release(APP_VERSION)
                self.update_check_events.put(("checked", info))
            except Exception as exc:
                self.update_check_events.put(("error", str(exc)))

        self.update_check_thread = threading.Thread(target=worker, daemon=True)
        self.update_check_thread.start()
        self.after(120, lambda: self._poll_update_check(manual))

    def _poll_update_check(self, manual):
        try:
            event, value = self.update_check_events.get_nowait()
        except queue.Empty:
            self.after(120, lambda: self._poll_update_check(manual))
            return
        thread = self.update_check_thread
        if thread is not None:
            thread.join()
        self.update_check_thread = None
        # 成功或失败都记录时间，24 小时内不重复请求
        update_checker.mark_checked(self.update_state)
        update_checker.write_state(self.update_state)
        if manual and self.status.get() == "正在检查更新...":
            self.status.set(getattr(self, "_status_before_check", "未启动"))
        if event == "error":
            if manual:
                messagebox.showerror("检查更新失败",
                                     f"无法访问 GitHub：{value}", parent=self)
            return
        info = value
        if info is not None and update_checker.is_skipped(self.update_state,
                                                          info.version):
            info = None
        self.update_info = info
        self._show_update_badge(info is not None)
        if manual:
            if info is None:
                messagebox.showinfo("检查更新",
                                    f"当前已是最新版本 v{APP_VERSION}。",
                                    parent=self)
            else:
                self._open_update_dialog()

    def _open_update_dialog(self):
        info = self.update_info
        if info is None:
            self._check_update(manual=True)
            return
        if self.update_dialog is not None and self.update_dialog.winfo_exists():
            self.update_dialog.lift()
            self.update_dialog.focus_force()
            return

        dialog = tk.Toplevel(self)
        self.update_dialog = dialog
        dialog.title(f"发现新版本 v{info.version}")
        dialog.transient(self)
        dialog.minsize(520, 420)
        dialog.geometry("620x560")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header,
                  text=f"发现新版本 v{info.version}（当前 v{APP_VERSION}）",
                  style="Header.TLabel").pack(anchor="w")
        published = info.published_at[:10]
        ttk.Label(header,
                  text=f"发布日期：{published}" if published else "GitHub 发布页有新版本",
                  style="HeaderSub.TLabel").pack(anchor="w", pady=(2, 0))

        notes_frame = ttk.Frame(frame)
        notes_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 10))
        notes_frame.rowconfigure(0, weight=1)
        notes_frame.columnconfigure(0, weight=1)
        notes = self._release_notes_widget(notes_frame, info.notes)
        notes.grid(row=0, column=0, sticky="nsew")

        status_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=status_var, style="Hint.TLabel",
                  justify="left").grid(row=2, column=0, sticky="w")
        progress = ttk.Progressbar(frame, maximum=100)
        progress.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        progress.grid_remove()

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="e", pady=(14, 0))
        # 便携版/源码运行时只下载安装包，不自动安装
        installable = (info.can_install and getattr(sys, "frozen", False)
                       and (APP_DIR / "installed.flag").is_file())
        cancel_button = PillButton(
            buttons, "取消下载", command=self.update_cancel.set, kind="danger",
            width=96, background=PAGE_BG)
        PillButton(buttons, "稍后", command=dialog.destroy, kind="secondary",
                   width=72, background=PAGE_BG).pack(side="right")
        PillButton(buttons, "跳过此版本",
                   command=lambda: self._skip_update(dialog), kind="ghost",
                   width=104, background=PAGE_BG).pack(side="right", padx=(0, 10))
        PillButton(buttons, "打开发布页",
                   command=lambda: webbrowser.open(info.page_url),
                   kind="secondary", width=104,
                   background=PAGE_BG).pack(side="right", padx=(0, 10))
        action_button = PillButton(
            buttons, "下载并安装" if installable else "下载安装包",
            command=lambda: self._download_update(
                dialog, progress, status_var, action_button, cancel_button),
            kind="primary", width=112, background=PAGE_BG)
        action_button.pack(side="right", padx=(0, 10))
        cancel_button.pack(side="right", padx=(0, 10))
        cancel_button.configure(state="disabled")

        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()

    @staticmethod
    def _release_notes_widget(parent, notes):
        """优先用 tkhtmlview 渲染 Markdown，失败时退回纯文本"""
        text = notes or "（该版本没有填写更新说明）"
        try:
            import markdown as markdown_lib
            from tkhtmlview import HTMLScrolledText
            widget = HTMLScrolledText(
                parent, html=markdown_lib.markdown(text), background=CARD_BG,
                padx=10, pady=8, relief="flat", highlightthickness=1,
                highlightbackground=BORDER, font=ui_font(9))
        except Exception:
            widget = tk.Text(
                parent, wrap="word", background=CARD_BG, foreground=TEXT,
                relief="flat", highlightthickness=1, highlightbackground=BORDER,
                padx=10, pady=8, font=ui_font(9))
            widget.insert("1.0", text)
            widget.configure(state="disabled")
        return widget

    def _skip_update(self, dialog):
        info = self.update_info
        if info is not None:
            self.update_state["skipped_version"] = info.version
            update_checker.write_state(self.update_state)
        self._show_update_badge(False)
        dialog.destroy()
        if not (self.running or self.starting):
            self.status.set(f"已跳过 v{info.version}" if info else "已跳过该版本")

    def _download_update(self, dialog, progress, status_var, action_button,
                         cancel_button):
        info = self.update_info
        if info is None or self.update_download_thread is not None:
            return
        self.update_cancel.clear()
        while not self.update_download_events.empty():
            self.update_download_events.get_nowait()
        progress.configure(value=0)
        progress.grid()
        status_var.set(f"准备下载 {info.installer_name or '安装包'} ...")
        action_button.configure(state="disabled")
        cancel_button.configure(state="normal")

        def worker():
            def report(label, done, total):
                self.update_download_events.put(("progress", (label, done, total)))
            try:
                path = update_checker.download_installer(
                    info, report, self.update_cancel)
                self.update_download_events.put(("downloaded", path))
            except DownloadCancelled:
                self.update_download_events.put(("cancelled", None))
            except Exception as exc:
                self.update_download_events.put(("error", str(exc)))

        self.update_download_thread = threading.Thread(target=worker, daemon=True)
        self.update_download_thread.start()
        self._poll_update_download(dialog, progress, status_var, action_button,
                                   cancel_button)

    def _poll_update_download(self, dialog, progress, status_var, action_button,
                              cancel_button):
        if not dialog.winfo_exists():
            # 窗口被关掉后停止下载，并尽快释放线程
            self.update_cancel.set()
            thread = self.update_download_thread
            if thread is not None:
                thread.join(timeout=1)
                if not thread.is_alive():
                    self.update_download_thread = None
            return
        try:
            event, value = self.update_download_events.get_nowait()
        except queue.Empty:
            self.after(150, lambda: self._poll_update_download(
                dialog, progress, status_var, action_button, cancel_button))
            return
        if event == "progress":
            label, done, total = value
            progress.configure(value=100 * done / total if total else 0)
            status_var.set(f"{label} | {done / 1048576:.1f} / "
                           f"{total / 1048576:.1f} MiB")
            self.after(150, lambda: self._poll_update_download(
                dialog, progress, status_var, action_button, cancel_button))
            return
        self.update_download_thread.join()
        self.update_download_thread = None
        progress.grid_remove()
        cancel_button.configure(state="disabled")
        action_button.configure(state="normal")
        if event == "downloaded":
            status_var.set(f"安装包已下载并校验：{Path(value).name}")
            self._confirm_install_update(dialog, value)
        elif event == "cancelled":
            status_var.set("下载已取消")
        else:
            status_var.set(f"下载失败：{value}")
            messagebox.showerror("更新下载失败", value, parent=dialog)

    def _confirm_install_update(self, dialog, installer):
        info = self.update_info
        if info is None:
            return
        installed = (getattr(sys, "frozen", False)
                     and (APP_DIR / "installed.flag").is_file())
        if not installed:
            messagebox.showinfo(
                "安装包已下载",
                f"安装包已保存到：\n{installer}\n\n"
                "当前是便携版或源码运行，不会自动安装；"
                "可手动运行安装包完成升级。", parent=dialog)
            os.startfile(str(Path(installer).parent))
            return
        if not messagebox.askokcancel(
                "安装更新",
                f"将关闭 SAI 并静默安装 v{info.version}，"
                "安装完成后自动重新启动。\n\n是否继续？", parent=dialog):
            return
        self._install_update(installer)

    def _install_update(self, installer):
        """退出 SAI，交给批处理等待安装完成后重启新版本"""
        log_file = CONFIG.parent / "logs" / "update-install.log"
        log_file.parent.mkdir(exist_ok=True)
        try:
            script = update_checker.create_install_script(
                installer, sys.executable, log_file)
        except OSError as exc:
            messagebox.showerror("无法安装更新", str(exc), parent=self)
            return
        self.update_cancel.set()
        if self.download_thread is not None:
            self.download_cancel.set()
        self._save(quiet=True)
        self._stop_processes()
        if self.tray_icon:
            self.tray_icon.stop()
        subprocess.Popen(
            ["cmd", "/c", str(script)], cwd=str(script.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.destroy()

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
            messagebox.showinfo("已保存",
                                "设置已保存到 config_gui.json\n\n"
                                "快捷键改动会在客户端运行中几秒内自动生效，"
                                "不需要重启。")
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
        log_dir = CONFIG.parent / "logs"
        (log_dir / "asr-error.json").unlink(missing_ok=True)
        # 上次进程被强杀时留下的就绪/录音标记
        for stale in list(log_dir.glob(".ready-*")) + list(log_dir.glob(".recording-*")):
            stale.unlink(missing_ok=True)
        info = MODEL_INFO[self._model_key()]
        missing = missing_files(ROOT, self._model_key(), self.vars["qwen_quantization"].get())
        if missing:
            messagebox.showerror(
                "模型未安装",
                f"当前选择：{info['label']}\n\n缺少以下文件：\n" +
                "\n".join(missing) +
                "\n\n请点击识别设置页的“下载模型”，完成后再启动。",
            )
            self._update_model_info()
            return
        if self._model_key() in GGUF_MODELS:
            report = self._runtime_report()
            if report.fatal:
                answer = messagebox.askokcancel(
                    "推理运行库异常",
                    "检测到 llama.cpp 运行库异常：\n" + "\n".join(report.problems) +
                    "\n\n不修复则识别服务无法启动。是否现在下载修复？",
                    parent=self)
                self._update_model_info()
                if not answer:
                    return
                self.start_after_repair = True
                self._repair_runtime(confirm=False)
                return
            if report.needs_repair or report.warnings:
                self.status.set(report.summary)
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
        specific_error = None
        if error_path.exists():
            try:
                specific_error = json.loads(error_path.read_text(encoding="utf-8"))["error"]
                error_path.unlink()
                self.status.set(specific_error)
                if self.tray_icon:
                    self.tray_icon.notify(specific_error, "语音识别失败")
            except (OSError, ValueError, KeyError, NotImplementedError):
                specific_error = None
        failed = [p for p in self.processes if p.poll() is not None]
        if failed:
            detail = ", ".join(
                f"{getattr(p, 'sai_role', 'unknown')} 退出码 {p.returncode}"
                for p in failed)
            if specific_error:
                self._fail(f"{specific_error}（{detail}）")
            else:
                self._fail("组件退出：" + detail)
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
                self.status.set(self._running_status_text())
                self.start_button.configure(state="normal", text="保存并重启")
                # 启动完成后就不再需要标记文件
                for ready in self.ready_files.values():
                    ready.unlink(missing_ok=True)
                if self.tray_icon and self.tray_icon.visible:
                    self.withdraw()
        self._refresh_recording_indicator()
        if self.processes:
            self.monitor = self.after(500, self._check_children)

    def _running_status_text(self):
        capture = getattr(self, "shortcut_capture", None)
        key = capture.value.get("key", "") if capture is not None else ""
        label = shortcut_label(key) if key else ""
        if not label:
            return "运行中 | 快捷键已就绪"
        text = f"运行中 | {label} 按住录音，松开输入"
        paste_capture = getattr(self, "paste_capture", None)
        paste_key = paste_capture.value.get("key", "") if paste_capture is not None else ""
        paste_label = shortcut_label(paste_key) if paste_key else ""
        if paste_label and paste_label != label:
            text += f" | {paste_label} 粘贴输出"
        return text

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
