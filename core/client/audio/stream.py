# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import time
import threading
import sys
import ctypes
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd
from config_client import ClientConfig as Config
from core.audio_devices import HOST_PRIORITY, input_devices, resolve_capture_device

from core.client.state import console
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import SaiClient



class AudioStreamManager:
    """
    音频流管理器

    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭

    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.02s）
    """

    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.02  # 20ms
    WARMUP_TIMEOUT = 3.0

    def __init__(self, app: SaiClient):
        """
        初始化音频流管理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._channels = 1
        self._input_sample_rate = self.SAMPLE_RATE
        self._running = False  # 标志是否应该运行
        self.session_lock = threading.Lock()
        self._lock = threading.RLock()
        self._shutdown = False
        self._signal_ready = threading.Event()
        self.keep_open = bool(Config.keep_microphone_open)
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="microphone")
        self._com = None

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数

        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # Warm-up samples are discarded; only track whether the driver is ready.
        if not self._signal_ready.is_set() and np.any(indata):
            self._signal_ready.set()
        if not self.state.recording:
            return

        if self._input_sample_rate != self.SAMPLE_RATE:
            target_frames = max(
                1, round(len(indata) * self.SAMPLE_RATE / self._input_sample_rate))
            source_x = np.linspace(0.0, 1.0, len(indata), endpoint=False)
            target_x = np.linspace(0.0, 1.0, target_frames, endpoint=False)
            indata = np.column_stack([
                np.interp(target_x, source_x, indata[:, channel])
                for channel in range(indata.shape[1])
            ]).astype(np.float32, copy=False)

        import asyncio

        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.app.loop
            )

    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return

        logger.info("音频流意外结束，正在尝试重启...")
        # PortAudio must not be reopened from its own callback thread.
        if self.app.loop and not self.app.loop.is_closed():
            self.app.loop.call_soon_threadsafe(self.reopen)

    def start(self) -> Optional[sd.InputStream]:
        with self._lock:
            if self._shutdown:
                return None
            return self._worker.submit(self._start).result()

    def prepare(self) -> Optional[sd.InputStream]:
        """Warm the explicitly enabled always-on mode; discard idle audio."""
        with self._lock:
            if self._shutdown or not self.keep_open:
                return None
            return self._worker.submit(self._prepare).result()

    def _prepare(self) -> Optional[sd.InputStream]:
        if self.state.stream is not None:
            return self.state.stream
        if self._start() is None:
            return None
        self._signal_ready.wait(self.WARMUP_TIMEOUT)
        logger.info("快速响应模式：麦克风保持开启，空闲音频丢弃、不保存、不发送")
        return self.state.stream

    def _initialize_com(self) -> None:
        # WASAPI needs COM on the thread that owns the stream, until close().
        if sys.platform != "win32" or self._com is not None:
            return
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoUninitialize.argtypes = []
        ole32.CoUninitialize.restype = None
        result = ole32.CoInitializeEx(None, 0)
        if result not in (0, 1):
            raise OSError(f"Microphone COM initialization failed: 0x{result & 0xffffffff:08X}")
        self._com = ole32

    def _release_com(self) -> None:
        if self._com is not None:
            self._com.CoUninitialize()
            self._com = None

    def _start(self) -> Optional[sd.InputStream]:
        """
        启动音频流

        Returns:
            创建的音频输入流，如果失败返回 None
        """
        if self._running:
            logger.debug("音频流已在运行，跳过启动")
            return self.state.stream
        self._initialize_com()
        try:
            return self._open_stream()
        finally:
            if not self._running:
                self._release_com()

    @staticmethod
    def _unique(values) -> list:
        ordered = []
        for value in values:
            if value is not None and value not in ordered:
                ordered.append(value)
        return ordered

    def _candidate_devices(self, selected: Optional[int]) -> list:
        """Ordered device indexes to try, preferring WASAPI endpoints.

        Some drivers expose a WASAPI endpoint that rejects every format, while
        the DirectSound/MME endpoint of the same microphone still works. All
        host-api variants of the selected (or default) microphone are therefore
        tried, and the PortAudio default device is the last resort.
        """
        try:
            devices = input_devices()
            if isinstance(devices, dict):
                devices = [devices]
        except Exception as exc:
            logger.debug("枚举录音设备失败，仅尝试所选设备: %s", exc)
            devices = []
        chosen = next((item for item in devices if item.get("index") == selected), None)
        names = []
        if chosen is not None:
            names.append(chosen.get("name"))
        try:
            default = sd.query_devices(kind="input")
        except sd.PortAudioError:
            default = None
        if default is not None and default.get("name") not in names:
            names.append(default["name"])
        candidates = [selected]
        for name in names:
            same_name = [
                item for item in devices
                if item.get("name") == name
                and item.get("hostapi") != "Windows WDM-KS"]
            same_name.sort(key=lambda item: HOST_PRIORITY.get(item.get("hostapi"), 99))
            candidates.extend(item.get("index") for item in same_name)
        ordered = self._unique(candidates)
        ordered.append(None)  # 最后兜底：PortAudio 默认录音设备
        return ordered

    def _open_stream(self) -> Optional[sd.InputStream]:
        # 检测音频设备
        try:
            selected = resolve_capture_device(Config.audio_device)
            device = sd.query_devices(selected, kind='input')
            self._channels = min(2, device['max_input_channels']) or 1
            device_name = device.get('name', '未知设备')
            console.print(
                f'使用音频设备：[italic]{device_name}，声道数：{self._channels}',
                end='\n\n'
            )
            logger.info(f"找到音频设备: {device_name}, 声道数: {self._channels}, 选择值: {selected if selected is not None else '默认'}")
        except (ValueError, sd.PortAudioError, UnicodeDecodeError) as exc:
            logger.error("无法打开所选麦克风: %s", exc)
            raise RuntimeError(f"无法打开所选录音设备，请重新选择麦克风：{exc}") from exc

        # 创建音频流：按候选设备、采样率、声道数逐个尝试
        stream = None
        last_error = None
        try:
            self._signal_ready.clear()
            for index in self._candidate_devices(selected):
                try:
                    current = sd.query_devices(index, kind='input')
                except sd.PortAudioError as exc:
                    last_error = exc
                    continue
                name = current.get('name', '未知设备')
                max_channels = min(2, current['max_input_channels']) or 1
                device_rate = int(round(float(current.get("default_samplerate", 0) or 0)))
                rates = [rate for rate in self._unique(
                    [self.SAMPLE_RATE, device_rate, 44100, 32000, 16000])
                    if isinstance(rate, (int, float)) and rate > 0]
                channel_options = self._unique([max_channels, 1])
                for rate in rates:
                    for channels in channel_options:
                        for latency in ("low", None):
                            try:
                                stream = sd.InputStream(
                                    samplerate=rate,
                                    blocksize=int(self.BLOCK_DURATION * rate),
                                    device=index,
                                    dtype="float32",
                                    channels=channels,
                                    latency=latency,
                                    callback=self._audio_callback,
                                    finished_callback=self._on_stream_finished,
                                )
                                stream.start()
                            except sd.PortAudioError as exc:
                                last_error = exc
                                logger.debug(
                                    "音频流尝试失败: 设备=%s(%s), 采样率=%s, "
                                    "声道数=%s, 延迟=%s: %s",
                                    name, index, rate, channels,
                                    latency or "默认", exc)
                                if stream is not None:
                                    try:
                                        stream.close()
                                    except Exception:
                                        pass
                                    stream = None
                                continue
                            self._channels = channels
                            self._input_sample_rate = rate
                            if index != selected:
                                logger.warning(
                                    "所选录音设备无法打开，已改用: %s, 选择值: %s",
                                    name, index)
                            break
                        if stream is not None:
                            break
                    if stream is not None:
                        break
                if stream is not None:
                    break

            if stream is None:
                raise last_error or RuntimeError("无法创建音频流")
            self.state.stream = stream
            self._running = True
            logger.debug(
                f"音频流已启动: 输入采样率={self._input_sample_rate}, "
                f"输出采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self._input_sample_rate)}, "
                f"声道数={self._channels}"
            )
            return stream

        except sd.PortAudioError as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            if '-9999' in str(e):
                console.print("""
[bold red]麦克风驱动初始化失败（错误码 -9999，具体原因见日志）[/bold red]
请尝试以下解决方案：

  1. 设置 > 隐私和安全性 > 麦克风，将「允许桌面应用访问麦克风」打开
  2. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「允许应用程序独占控制该设备」
  3. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「增强效果」
""")
            return None
        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            return None
        finally:
            if stream is not None and not self._running:
                stream.close()

    def stop(self) -> None:
        with self._lock:
            if not self._shutdown:
                self._worker.submit(self._stop).result()

    def _stop(self) -> None:
        if self.keep_open and not self._shutdown:
            return
        self._close()

    def _close(self) -> None:
        self._running = False
        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug("音频流已停止")
            except Exception as e:
                logger.debug(f"停止音频流时发生错误: {e}")
            finally:
                self.state.stream = None
        self._release_com()

    def shutdown(self) -> None:
        """Prevent a pending open from acquiring the device during app exit."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            try:
                self._worker.submit(self._close).result()
            finally:
                self._worker.shutdown(wait=True)

    def reopen(self) -> Optional[sd.InputStream]:
        with self._lock:
            if self._shutdown:
                return None
            return self._worker.submit(self._reopen).result()

    def _reopen(self) -> Optional[sd.InputStream]:
        self._close()
        if not (self.keep_open or self.state.recording):
            return None
        return self._start()
