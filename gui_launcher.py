# coding: utf-8
"""Windows graphical launcher and configuration editor."""
from __future__ import annotations

import json
import math
import os
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

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG = ROOT / "config_gui.json"
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
    "qwen_asr": {
        "label": "Qwen3-ASR",
        "advice": "适合多语言和复杂口述；独显解码优先，当前 INT4 编码器自动使用 CPU。",
        "files": (
            "models/Qwen3-ASR/Qwen3-ASR-1.7B/qwen3_asr_encoder_frontend.onnx",
            "models/Qwen3-ASR/Qwen3-ASR-1.7B/qwen3_asr_encoder_backend.onnx",
            "models/Qwen3-ASR/Qwen3-ASR-1.7B/qwen3_asr_llm.gguf",
        ),
    },
    "fun_asr_nano": {
        "label": "Fun-ASR-Nano",
        "advice": "适合日常中英文输入；GGUF 可使用独显，ONNX 不兼容时自动回退 CPU。",
        "files": (
            "models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model/Fun-ASR-Nano-Encoder-Adaptor.fp32.onnx",
            "models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model/Fun-ASR-Nano-CTC.int8.onnx",
            "models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model/Fun-ASR-Nano-Decoder.q8_0.gguf",
            "models/Fun-ASR-Nano/Fun-ASR-Nano-GGUF/model/tokens.txt",
        ),
    },
    "sensevoice": {
        "label": "SenseVoice-Small",
        "advice": "适合短句、中英日韩粤语；当前安装的是 CPU INT8 版，适合低占用输入。",
        "files": (
            "models/SenseVoice-Small/Sherpa-ONNX/model.int8.onnx",
            "models/SenseVoice-Small/Sherpa-ONNX/tokens.txt",
            PUNC_MODEL,
        ),
    },
    "paraformer": {
        "label": "Paraformer",
        "advice": "CPU 专用、速度快、占用低；不使用 GPU。",
        "files": (
            "models/Paraformer/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/model.onnx",
            "models/Paraformer/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/tokens.txt",
            PUNC_MODEL,
        ),
    },
}
MODEL_CHOICES = {
    "qwen_asr": "Qwen3-ASR（多语言与复杂口述，推荐独显）",
    "fun_asr_nano": "Fun-ASR-Nano（准确率和速度均衡）",
    "sensevoice": "SenseVoice-Small（CPU 低占用，短句输入）",
    "paraformer": "Paraformer（CPU 专用、速度快）",
}


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CapsWriter Offline")
        self.geometry("780x710")
        self.minsize(700, 690)
        self.resizable(True, True)
        self.processes = []
        self.ready_files = {}
        self.starting = False
        self.running = False
        self.monitor = None
        self.status = tk.StringVar(value="未启动")
        self.vars = {
            "model_type": tk.StringVar(value=MODEL_CHOICES["qwen_asr"]),
            "onnx_provider": tk.StringVar(value="AUTO"),
            "llm_use_gpu": tk.BooleanVar(value=True),
            "gpu_boost_enabled": tk.BooleanVar(value=False),
            "language": tk.StringVar(value="auto"),
            "threshold": tk.StringVar(value="0.3"),
            "context": tk.StringVar(),
            "paste": tk.BooleanVar(value=False),
            "audio_device": tk.StringVar(value=DEFAULT_MIC),
        }
        self.audio_devices = {DEFAULT_MIC: None}
        self.saved_audio_device = None
        self._load()
        self._build()
        self._start_tray()
        self.vars["model_type"].trace_add("write", lambda *_: self._update_model_info())
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
                    self.vars[key].set(value)
        except (OSError, ValueError):
            pass

    def _build(self):
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="CapsWriter Offline",
                  font=("Segoe UI", 18, "bold")).pack(anchor="w")
        form = ttk.LabelFrame(outer, text="识别设置", padding=14)
        form.pack(fill="x")
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
        options = ttk.LabelFrame(outer, text="选项", padding=14)
        options.pack(fill="x", pady=14)
        ttk.Checkbutton(options, text="启用 GGUF GPU 加速",
                        variable=self.vars["llm_use_gpu"]).pack(anchor="w")
        ttk.Checkbutton(options, text="启用 NVIDIA GPU 预加速（需管理员权限）",
                        variable=self.vars["gpu_boost_enabled"]).pack(anchor="w", pady=5)
        ttk.Checkbutton(options, text="使用剪贴板粘贴输出",
                        variable=self.vars["paste"]).pack(anchor="w")
        ttk.Label(outer, textvariable=self.status, foreground="#187a3d",
                  wraplength=640).pack(anchor="w")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="保存设置", command=self._save).pack(side="left")
        self.start_button = ttk.Button(buttons, text="保存并启动", command=self._start)
        self.start_button.pack(side="right")
        ttk.Button(buttons, text="停止", command=self._stop_processes).pack(side="right", padx=8)

    def _update_model_info(self):
        info = MODEL_INFO.get(self._model_key())
        if not info or not hasattr(self, "model_info"):
            return
        missing = [path for path in info["files"] if not (ROOT / path).exists()]
        status = "模型文件已找到" if not missing else f"未安装模型，缺少 {len(missing)} 个文件"
        self.model_info.configure(
            text=f"建议：{info['advice']}\n状态：{status}",
            foreground="#187a3d" if not missing else "#b3261e")

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
        info = MODEL_INFO[self._model_key()]
        missing = [path for path in info["files"] if not (ROOT / path).exists()]
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
                "\n\n请下载模型并按目录结构放入 models 文件夹。",
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
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        ready = log_dir / f".ready-{role}-{uuid.uuid4().hex}"
        self.ready_files[role] = ready
        env = os.environ.copy()
        env.update(CAPSWRITER_GUI="1", CAPSWRITER_READY_FILE=str(ready),
                   PYTHONIOENCODING="utf-8")
        with (log_dir / f"{role}_bootstrap.log").open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.processes.append(process)

    def _check_children(self):
        self.monitor = None
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
            log_file = ROOT / "logs" / name
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
