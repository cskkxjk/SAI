# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

from __future__ import annotations
import asyncio
import time
from threading import Event
from typing import TYPE_CHECKING, Optional

from . import logger
from core.tools.my_status import Status
 
if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.audio.recorder import AudioRecorder
    from core.client.app import SaiClient



class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, app: SaiClient, shortcut: Shortcut, recorder_class=None):
        """
        初始化快捷键任务

        Args:
            app: 客户端 App 实例
            shortcut: 快捷键配置
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.app = app
        self.shortcut = shortcut
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        self._ended = None
        self._cancelled = False

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _get_recorder(self) -> AudioRecorder:
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from core.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.app)

    def launch(self) -> None:
        """启动录音任务"""
        # All shortcuts share one microphone and one queue consumer.
        if not self.app.stream.session_lock.acquire(blocking=False):
            return
        logger.info(f"[{self.shortcut.key}] 触发：开始录音")

        self.recording_start_time = time.time()
        self.is_recording = True
        self._cancelled = False
        self._ended = asyncio.Event()
        self.state.start_recording(self.recording_start_time)
        session = self._run_session()
        try:
            self.task = asyncio.run_coroutine_threadsafe(session, self.app.loop)
        except Exception:
            session.close()
            self.is_recording = False
            self.state.stop_recording()
            self.app.stream.session_lock.release()
            raise

    async def _run_session(self) -> None:
        recorder_task = None
        end_task = None
        opening = None
        try:
            if not self.is_recording:
                return
            # Clear any abandoned messages before installing the next consumer.
            while not self.state.queue_in.empty():
                self.state.queue_in.get_nowait()
                self.state.queue_in.task_done()
            self.state.queue_in.put_nowait({
                'type': 'begin', 'time': self.recording_start_time, 'data': None,
            })
            recorder_task = asyncio.create_task(self._get_recorder().record_and_send())
            # Device initialization must not block the low-level keyboard hook.
            opening = asyncio.create_task(asyncio.to_thread(self.app.stream.start))
            if await asyncio.shield(opening) is None:
                raise RuntimeError("Cannot open microphone; check the selected device and permissions.")
            if self.is_recording:
                self._status.start()
            end_task = asyncio.create_task(self._ended.wait())
            await asyncio.wait((recorder_task, end_task), return_when=asyncio.FIRST_COMPLETED)
            self.state.stop_recording()
            await asyncio.to_thread(self.app.stream.stop)
            if not self._cancelled and not recorder_task.done():
                self.state.queue_in.put_nowait({
                    'type': 'finish', 'time': time.time(), 'data': None,
                })
                await recorder_task
        except Exception:
            logger.exception("Microphone recording failed")
            from core.client.ui import toast
            toast("麦克风打开失败，请检查录音设备和权限后重试", duration=4000)
        finally:
            self.is_recording = False
            self.state.stop_recording()
            try:
                # A release/cancellation can arrive while the driver is opening.
                if opening is not None:
                    await asyncio.gather(opening, return_exceptions=True)
                await asyncio.to_thread(self.app.stream.stop)
            finally:
                self._status.stop()
                pending = [task for task in (recorder_task, end_task) if task is not None]
                for task in pending:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                self.app.stream.session_lock.release()

    def _end(self, cancelled: bool) -> bool:
        if not self.is_recording:
            return False
        self.is_recording = False
        self._cancelled = cancelled
        self.state.stop_recording()
        self.app.loop.call_soon_threadsafe(self._ended.set)
        return True

    def cancel(self) -> None:
        """取消录音任务（时间过短）"""
        logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）")

        self._end(cancelled=True)

    def finish(self) -> None:
        """完成录音任务"""
        logger.info(f"[{self.shortcut.key}] 释放：完成录音")

        if not self._end(cancelled=False):
            return

        # 执行 restore（可恢复按键 + 非阻塞模式）
        # 阻塞模式下按键不会发送到系统，状态不会改变，不需要恢复
        if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
            self._restore_key()

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
