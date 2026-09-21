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

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG = DATA_DIR / "config_gui.json"
DEFAULT_MIC = "系统默认录音设备"
PUNC_MODEL = (
    "models/Punct-CT-Transformer/"
    "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12/model.onnx"
)


def activate_existing():
    if os.name != "nt":
        return False
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    activate_existing.mutex = kernel.CreateMutexW(None, False, "Local\\CapsWriterOfflineDesktop")
    if ctypes.get_last_error() != 183:
        return False
    user = ctypes.WinDLL("user32")
    user.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user.FindWindowW.restype = ctypes.c_void_p
    user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    window = user.FindWindowW(None, "CapsWriter Offline")
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


class Launcher(tk.Tk):
    def __init__(self):
        initialize_user_data()
        super().__init__()
        self.title("CapsWriter Offline")
        self.geometry("800x800")
        self.minsize(700, 780)
        self.resizable(True, True)
        self.processes = []
        self.ready_files = {}
        self.starting = False
        self.running = False
        self.monitor = None
        self.download_thread = None
        self.download_cancel = threading.Event()
        self.download_events = queue.Queue()
        self.status = tk.StringVar(value="未启动")
        self.vars = {
            "model_type": tk.StringVar(value=MODEL_CHOICES["qwen_asr"]),
            "qwen_quantization": tk.StringVar(value="q5_k"),
            "onnx_provider": tk.StringVar(value="AUTO"),
            "llm_use_gpu": tk.BooleanVar(value=True),
            "gpu_boost_enabled": tk.BooleanVar(value=False),
            "language": tk.StringVar(value="auto"),
            "threshold": tk.StringVar(value="0.3"),
            "context": tk.StringVar(),
            "paste": tk.BooleanVar(value=False),
            "audio_device": tk.StringVar(value=DEFAULT_MIC),
            "keep_microphone_open": tk.BooleanVar(value=False),
            "asr_api_base_url": tk.StringVar(value="https://api.openai.com/v1"),
            "asr_api_model": tk.StringVar(value="whisper-1"),
            "asr_api_key": tk.StringVar(),
            "asr_api_timeout": tk.StringVar(value="60"),
        }
        self.audio_devices = {DEFAULT_MIC: None}
        self.saved_audio_device = None
        self._load()
        self._build()
        self._start_tray()
        self.vars["model_type"].trace_add("write", lambda *_: self._update_model_info())
        self.vars["qwen_quantization"].trace_add("write", lambda *_: self._update_model_info())
        self._update_model_info()
        self._refresh_audio_devices()
        self.protocol("WM_DELETE_WINDOW", self._hide_or_close)

    def _load(self):
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
            self.saved_audio_device = data.get("audio_device")
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
                    self.vars[key].set(value)
        except (OSError, ValueError):
            pass

    def _build(self):
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="CapsWriter Offline",
                  font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="x")
        form = ttk.Frame(tabs, padding=14)
        tabs.add(form, text="识别设置")
        api_form = ttk.Frame(tabs, padding=14)
        tabs.add(api_form, text="语音 API")
        for row, (key, label) in enumerate((
                ("asr_api_base_url", "服务地址"),
                ("asr_api_model", "模型名称"),
                ("asr_api_key", "API Key"),
                ("asr_api_timeout", "请求超时（秒）"))):
            self._row(api_form, row, label, ttk.Entry(
                api_form, textvariable=self.vars[key],
                show="*" if key == "asr_api_key" else ""))
        ttk.Label(api_form, text="录音将上传至所配置服务，可能产生费用。\n"
                  "密钥保存在本机配置文件中（明文）。",
                  foreground="#a33", wraplength=520).grid(
                      row=4, column=0, columnspan=2, sticky="w", pady=12)
        model_box = ttk.Combobox(
            form, textvariable=self.vars["model_type"], state="readonly",
            values=tuple(MODEL_CHOICES.values()), width=62)
        self._row(form, 0, "识别模型", model_box)
        self._row(form, 1, "推理后端", ttk.Combobox(
            form, textvariable=self.vars["onnx_provider"], state="readonly",
            values=("AUTO", "CPU"), width=28))
        self._row(form, 2, "识别语言", ttk.Combobox(
            form, textvariable=self.vars["language"], state="readonly",
            values=("auto", "chinese", "english", "japanese"), width=28))
        self._row(form, 3, "快捷键阈值（秒）",
                  ttk.Entry(form, textvariable=self.vars["threshold"], width=31))
        self._row(form, 4, "上下文提示",
                  ttk.Entry(form, textvariable=self.vars["context"], width=31))
        self.audio_box = ttk.Combobox(
            form, textvariable=self.vars["audio_device"], state="readonly",
            values=(DEFAULT_MIC,), width=32)
        self._row(form, 5, "录音设备", self.audio_box)
        ttk.Button(form, text="刷新设备", command=lambda: self._refresh_audio_devices(True)).grid(
            row=6, column=1, sticky="e", pady=5)
        self.model_info = ttk.Label(
            form, text="", foreground="#555", wraplength=390, justify="left")
        self.model_info.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        downloads = ttk.Frame(form)
        downloads.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.download_button = ttk.Button(
            downloads, text="下载模型", command=self._download_model)
        self.download_button.pack(side="left")
        self.cancel_download_button = ttk.Button(
            downloads, text="取消下载", command=self.download_cancel.set, state="disabled")
        self.cancel_download_button.pack(side="left", padx=6)
        ttk.Button(downloads, text="刷新状态", command=self._update_model_info).pack(side="left")
        self.download_progress = ttk.Progressbar(downloads, maximum=100)
        self.download_progress.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.quantization_box = ttk.Combobox(
            form, textvariable=self.vars["qwen_quantization"], state="readonly",
            values=("q5_k", "q4_k"), width=12)
        self._row(form, 9, "Qwen 量化", self.quantization_box)
        options = ttk.LabelFrame(outer, text="选项", padding=14)
        options.pack(fill="x", pady=14)
        ttk.Checkbutton(options, text="启用 GGUF GPU 加速",
                        variable=self.vars["llm_use_gpu"]).pack(anchor="w")
        ttk.Checkbutton(options, text="启用 NVIDIA GPU 预加速（需管理员权限）",
                        variable=self.vars["gpu_boost_enabled"]).pack(anchor="w", pady=5)
        ttk.Checkbutton(options, text="使用剪贴板粘贴输出",
                        variable=self.vars["paste"]).pack(anchor="w")
        ttk.Checkbutton(options, text="快速响应（空闲时持续占用麦克风）",
                        variable=self.vars["keep_microphone_open"]).pack(anchor="w", pady=(5, 0))
        ttk.Label(outer, textvariable=self.status, foreground="#187a3d",
                  wraplength=640).pack(anchor="w")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="保存设置", command=self._save).pack(side="left")
        ttk.Button(buttons, text="热词与替换", command=self._open_hotwords).pack(
            side="left", padx=8)
        self.start_button = ttk.Button(buttons, text="保存并启动", command=self._start)
        self.start_button.pack(side="right")
        ttk.Button(buttons, text="停止", command=self._stop_processes).pack(side="right", padx=8)

    def _open_hotwords(self):
        editor = getattr(self, "_hotword_editor", None)
        if editor is not None and editor.winfo_exists():
            editor.deiconify()
            editor.lift()
            return
        from core.desktop_hotwords import HotwordEditor
        try:
            # Read first so a permissions/encoding error cannot leave a partial window.
            for name in ("hot.txt", "hot-rule.txt"):
                path = CONFIG.parent / name
                if path.exists():
                    path.read_text(encoding="utf-8")
            self._hotword_editor = HotwordEditor(self, CONFIG.parent)
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("无法打开热词文件", str(exc), parent=self)

    def _update_model_info(self):
        info = MODEL_INFO.get(self._model_key())
        if not info or not hasattr(self, "model_info"):
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
        self.model_info.configure(
            text=f"建议：{advice}\n状态：{status}",
            foreground="#187a3d" if not missing else "#b3261e")
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
            for device in sorted(devices, key=lambda d: d["hostapi"] != "Windows WASAPI"):
                name = " ".join(device["name"].split())
                label = f"{name} [{device['hostapi']}] #{device['index']}"
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

    @staticmethod
    def _row(parent, row, label, widget):
        ttk.Label(parent, text=label, width=16).grid(row=row, column=0, sticky="w", pady=5)
        widget.grid(row=row, column=1, sticky="w", pady=5)
        parent.columnconfigure(1, weight=1)
        widget.grid_configure(sticky="ew")

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
                data["asr_api_base_url"], data["asr_api_model"], data["asr_api_timeout"])
        selected_device = self.vars["audio_device"].get()
        data["audio_device"] = self.audio_devices[selected_device]
        data["threshold"] = threshold
        data["child_tray"] = False
        return data

    def _model_key(self):
        selected = self.vars["model_type"].get()
        for key, label in MODEL_CHOICES.items():
            if selected == label:
                return key
        return selected

    def _save(self, quiet=False):
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
            sd.check_input_settings(device=device_id, channels=min(2, device["max_input_channels"]),
                                    dtype="float32", samplerate=48000)
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
            command.append(str(ROOT / "capswriter.py"))
        command.append(f"--{role}")
        log_dir = CONFIG.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        ready = log_dir / f".ready-{role}-{uuid.uuid4().hex}"
        self.ready_files[role] = ready
        env = os.environ.copy()
        env.update(CAPSWRITER_GUI="1", CAPSWRITER_READY_FILE=str(ready),
                   PYTHONIOENCODING="utf-8")
        with (log_dir / f"{role}_bootstrap.log").open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command, cwd=CONFIG.parent, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
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
            self._fail("组件退出，退出码：" + ", ".join(str(p.returncode) for p in failed))
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
        if self.processes:
            self.monitor = self.after(500, self._check_children)

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
        messagebox.showerror("组件运行失败", "\n\n".join(details))

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
        self.starting = self.running = False
        self.status.set("已停止")
        if hasattr(self, "start_button"):
            self.start_button.configure(state="normal", text="保存并启动")

    def _hide_or_close(self):
        if self.processes and self.tray_icon and self.tray_icon.visible:
            self.withdraw()
        else:
            self._close()

    def _close(self):
        if self.download_thread is not None:
            self.download_cancel.set()
            self.status.set("正在取消下载，请稍候；网络请求最多等待 30 秒")
            self.after(200, self._close)
            return
        self._stop_processes()
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()

    def _start_tray(self):
        self.tray_icon = None
        try:
            import pystray
            from PIL import Image
            image = Image.open(ROOT / "assets" / "icon.ico")
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
                "CapsWriter", image, "CapsWriter Offline", menu)
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
