# coding: utf-8
"""
快捷键管理器（重构版）

统一管理多个快捷键，处理键盘和鼠标事件，支持：
1. 多快捷键并发处理
2. 防止不同按键互相干扰
3. restore 功能的防自捕获逻辑
4. hold_mode 和 click_mode 支持
"""
from __future__ import annotations
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from pynput import keyboard, mouse

from . import logger
from core.shortcut_keys import (canonical_key, combo_active,
                                darwin_suppress_intercept, matching_names,
                                normalize_part)
from core.client.shortcut.key_mapper import *
from core.client.shortcut.key_mapper import KeyMapper
from core.client.shortcut.emulator import ShortcutEmulator
from core.client.shortcut.event_handler import ShortcutEventHandler
from core.client.shortcut.shortcut_config import load_shortcuts
from core.client.shortcut.task import ShortcutTask

if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.app import SaiClient


# 图形界面在「语音短语」页录音时写入的占用标记
CAPTURE_FLAG_NAME = "voice-capture.flag"



class ShortcutManager:
    """
    快捷键管理器

    统一管理多个快捷键，使用 pynput 监听键盘和鼠标事件。
    所有事件处理都在 win32_event_filter 中完成，确保高性能和低延迟。
    """

    def __init__(self, app: SaiClient, shortcuts: List[Shortcut]):
        """
        初始化快捷键管理器

        Args:
            app: 客户端 App 实例
            shortcuts: 快捷键配置列表
        """
        self.app = app
        self.shortcuts = shortcuts

        # 监听器
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None

        # 快捷键任务映射（key -> ShortcutTask）
        self.tasks: Dict[str, ShortcutTask] = {}
        self._pressed_keys = set()

        # 线程池
        self._pool = ThreadPoolExecutor(max_workers=4)

        # 按键模拟器
        self._emulator = ShortcutEmulator()

        # 按键恢复状态追踪
        self._restoring_keys = set()

        # 事件处理器
        self._event_handler = ShortcutEventHandler(self.tasks, self._pool, self._emulator)

        # 配置热重载（config_gui.json 变化时自动重建快捷键任务）
        self._config_thread: Optional[threading.Thread] = None
        self._config_stop: Optional[threading.Event] = None
        self._config_signature = self._signature(shortcuts)

        # 图形界面录制语音短语时暂停触发（标记文件由启动器写入）
        self.capture_hold = False
        self._capture_flag = self._capture_flag_path()
        self._capture_mic_lock = threading.Lock()
        self._capture_mic_state = None

        # macOS：darwin_intercept 的屏蔽标记（回调先打标记，拦截器再消费）
        self.darwin_suppress = False

        # 初始化快捷键任务
        self._init_tasks()

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _init_tasks(self) -> None:
        """初始化所有快捷键任务"""
        from config_client import ClientConfig as Config

        for shortcut in self.shortcuts:
            if not shortcut.enabled:
                continue

            task = ShortcutTask(self.app, shortcut)
            task._manager_ref = lambda: self  # 弱引用，用于回调
            task.pool = self._pool
            task.threshold = shortcut.get_threshold(Config.threshold)
            self.tasks[shortcut.key] = task

    # ========== 热重载 ==========

    CONFIG_POLL_INTERVAL = 0.5

    @staticmethod
    def _signature(shortcuts: List[Shortcut]) -> tuple:
        """快捷键配置指纹，用于判断配置是否真的发生变化"""
        return tuple(
            (shortcut.key, shortcut.type, bool(shortcut.enabled),
             bool(shortcut.suppress), bool(shortcut.hold_mode),
             bool(getattr(shortcut, 'paste', False)))
            for shortcut in shortcuts
        )

    def reload(self, shortcuts: List[Shortcut]) -> None:
        """
        热重载快捷键配置

        监听器读取的是 self.tasks 这个活字典，所以只需就地重建任务即可生效：
        不用重启进程，也不会重新加载模型。
        """
        for task in self.tasks.values():
            if task.is_recording:
                task.cancel()

        self.shortcuts = shortcuts
        self.tasks.clear()
        self._pressed_keys.clear()
        self._init_tasks()
        self._config_signature = self._signature(shortcuts)
        self.start()

        enabled = ", ".join(s.key for s in shortcuts if s.enabled) or "无"
        logger.info(f"快捷键配置已热重载，当前启用: {enabled}")

    def start_config_watcher(self) -> None:
        """启动配置监视线程：config_gui.json 变化时自动热重载快捷键"""
        if self._config_thread and self._config_thread.is_alive():
            return

        self._config_stop = threading.Event()
        self._config_thread = threading.Thread(
            target=self._watch_config, name="shortcut-config-watcher", daemon=True)
        self._config_thread.start()
        logger.debug("快捷键配置监视已启动")

    def stop_config_watcher(self) -> None:
        """停止配置监视线程"""
        if self._config_stop is not None:
            self._config_stop.set()

        thread = self._config_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)

        self._config_thread = None
        self._config_stop = None

    def _watch_config(self) -> None:
        while self._config_stop is not None and not self._config_stop.wait(self.CONFIG_POLL_INTERVAL):
            try:
                self._poll_config()
            except Exception as exc:
                logger.debug(f"检查快捷键配置变化失败: {exc}")
            try:
                self._poll_capture_hold()
            except Exception as exc:
                logger.debug(f"检查语音短语录制标记失败: {exc}")

    def _poll_config(self) -> bool:
        """对比配置文件与当前快捷键，必要时热重载；返回是否发生重载"""
        shortcuts = load_shortcuts()
        if not shortcuts:
            return False
        if self._signature(shortcuts) == self._config_signature:
            return False

        logger.info("检测到 config_gui.json 变化，正在热重载快捷键")
        self.reload(shortcuts)
        return True

    @staticmethod
    def _capture_flag_path():
        try:
            from core.runtime_paths import DATA_DIR
        except Exception:
            return None
        return Path(DATA_DIR) / "logs" / CAPTURE_FLAG_NAME

    def _poll_capture_hold(self) -> bool:
        """图形界面正在录制语音短语时暂停快捷键触发。

        标记文件由启动器写入，录制结束即删除；启动器被强杀留下的
        残留标记会在检测到进程不存在时清理。
        """
        active = self._read_capture_hold()
        if active != self.capture_hold:
            self.capture_hold = active
            self._apply_capture_hold(active)
        return active

    def _read_capture_hold(self) -> bool:
        path = self._capture_flag
        if path is None or not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        pid = payload.get("pid") if isinstance(payload, dict) else None
        if pid and not self._process_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            return False
        return True

    def _apply_capture_hold(self, active: bool):
        """录制语音短语时让客户端让出麦克风，避免两条输入流互相干扰。"""
        stream = getattr(self.app, "stream", None)
        if stream is None:
            return None

        def work():
            # 串行执行，避免快速切换时挂起/恢复乱序
            with self._capture_mic_lock:
                if self._capture_mic_state == active:
                    return
                self._capture_mic_state = active
                try:
                    if active:
                        stream.suspend_for_capture()
                    else:
                        stream.resume_after_capture()
                except Exception as exc:
                    logger.debug(f"语音短语录制时调整麦克风失败: {exc}")

        thread = threading.Thread(target=work, name="voice-capture-mic", daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _process_alive(pid) -> bool:
        try:
            import psutil
            return psutil.pid_exists(int(pid))
        except Exception:
            return True

    def _capture_hold_active(self) -> bool:
        """录制标记是否生效：直接查文件，消除轮询间隙。"""
        if self._capture_flag is None:
            return self.capture_hold
        return self._poll_capture_hold()

    def _combo_active(self, parts) -> bool:
        return combo_active(parts, self._pressed_keys)

    # ========== 监听器创建 ==========

    def create_keyboard_filter(self):
        """创建键盘事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 KEYDOWN 和 KEYUP 消息
            if msg not in KEYBOARD_MESSAGES:
                return True

            key_name = canonical_key(KeyMapper.vk_to_name(data.vkCode))

            # 防自捕获检查
            if self._check_emulating(key_name, msg):
                return True
            if self._check_restoring(key_name, msg):
                return True

            if msg in KEY_DOWN_MESSAGES:
                self._pressed_keys.add(key_name)
                matched = []
                for task in self.tasks.values():
                    if task.shortcut.type != "keyboard":
                        continue
                    parts = [normalize_part(part) for part in task.shortcut.key.split("+")]
                    if not self._combo_active(parts):
                        continue
                    # 语音短语录制中：不触发新的录音（已开始的录音仍能正常收尾）
                    if self._capture_hold_active():
                        continue
                    self._event_handler.handle_keydown(task.shortcut.key, task)
                    matched.append(task)
                suppress = any(task.shortcut.suppress for task in matched)
            elif msg in KEY_UP_MESSAGES:
                matched = []
                for task in self.tasks.values():
                    if task.shortcut.type != "keyboard":
                        continue
                    parts = [normalize_part(part) for part in task.shortcut.key.split("+")]
                    key_matches = any(key_name in matching_names(part) for part in parts)
                    if key_matches and task.pressed:
                        self._event_handler.handle_keyup(task.shortcut.key, task)
                        matched.append(task)
                self._pressed_keys.discard(key_name)
                suppress = any(task.shortcut.suppress for task in matched)
            else:
                suppress = False

            # 阻塞事件
            if suppress and self.keyboard_listener:
                self.keyboard_listener.suppress_event()

            return True

        return win32_event_filter

    def _keyboard_press(self, key):
        key_name = canonical_key(self._key_to_name(key))
        self._pressed_keys.add(key_name)
        suppress = False
        for shortcut_key, task in tuple(self.tasks.items()):
            if task.shortcut.type != "keyboard":
                continue
            parts = [normalize_part(part) for part in shortcut_key.split("+")]
            if self._combo_active(parts) and not self._capture_hold_active():
                self._event_handler.handle_keydown(shortcut_key, task)
                suppress = suppress or task.shortcut.suppress
        self.darwin_suppress = suppress

    def _keyboard_release(self, key):
        key_name = canonical_key(self._key_to_name(key))
        suppress = False
        for shortcut_key, task in tuple(self.tasks.items()):
            if task.shortcut.type != "keyboard":
                continue
            parts = [normalize_part(part) for part in shortcut_key.split("+")]
            if any(key_name in matching_names(part) for part in parts) and task.pressed:
                self._event_handler.handle_keyup(shortcut_key, task)
                suppress = suppress or task.shortcut.suppress
        self._pressed_keys.discard(key_name)
        self.darwin_suppress = suppress

    @staticmethod
    def _key_to_name(key) -> str:
        if isinstance(key, keyboard.Key):
            return key.name
        return getattr(key, 'char', None) or str(key).replace("'", "").lower()

    def create_mouse_filter(self):
        """创建鼠标事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理侧键与中键消息
            if msg not in MOUSE_MESSAGES:
                return True

            # 获取按键标识
            if msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                button_name = 'middle'
            else:
                xbutton = (data.mouseData >> 16) & 0xFFFF
                button_name = 'x1' if xbutton == XBUTTON1 else 'x2'

            # 防自捕获检查
            if self._check_emulating(button_name, msg, is_mouse=True):
                return True

            # 查找匹配的快捷键
            if button_name not in self.tasks:
                return True

            task = self.tasks[button_name]

            # 处理鼠标事件
            if msg in (WM_XBUTTONDOWN, WM_MBUTTONDOWN):
                if not self._capture_hold_active():
                    self._event_handler.handle_keydown(button_name, task)
            elif msg in (WM_XBUTTONUP, WM_MBUTTONUP):
                self._handle_mouse_keyup(button_name, task)

            # 阻塞事件；语音短语录制中不阻塞，让启动器监听录音键
            if task.shortcut.suppress and self.mouse_listener and not self.capture_hold:
                self.mouse_listener.suppress_event()

            return True

        return win32_event_filter

    def _handle_mouse_keyup(self, button_name: str, task) -> None:
        """处理鼠标按键释放事件"""
        # 单击模式
        if not task.shortcut.hold_mode:
            if task.pressed:
                task.pressed = False
                task.released = True
                task.event.set()
            return

        # 长按模式
        task.pressed = False
        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{button_name}] 松开按键，持续时间: {duration:.3f}s")

        if duration < task.threshold:
            task.cancel()
            if task.shortcut.suppress:
                logger.debug(f"[{button_name}] 安排异步补发鼠标按键")
                self._pool.submit(self._emulator.emulate_mouse_click, button_name)
        else:
            task.finish()

    # ========== 按键恢复管理 ==========

    def schedule_restore(self, key: str) -> None:
        """
        安排按键恢复（延迟执行，避免在事件处理中阻塞）

        Args:
            key: 要恢复的按键

        注意：标志清除只在按键释放事件中处理（_check_restoring），
        避免在线程中提前清除导致主线程收到重复消息。
        """
        from pynput import keyboard

        self._restoring_keys.add(key)

        def do_restore():
            import time
            time.sleep(0.05)  # 延迟 50ms
            key_obj = KeyMapper.name_to_key(key)
            if key_obj is None:
                return
            controller = keyboard.Controller()
            controller.press(key_obj)
            controller.release(key_obj)

        self._pool.submit(do_restore)

    def is_restoring(self, key: str) -> bool:
        """检查是否正在恢复指定按键"""
        return key in self._restoring_keys

    def clear_restoring_flag(self, key: str) -> None:
        """清除恢复标志"""
        self._restoring_keys.discard(key)

    # ========== 防自捕获检查 ==========

    def _check_emulating(self, key_name: str, msg: int, is_mouse: bool = False) -> bool:
        """检查是否正在模拟按键"""
        if not self._emulator.is_emulating(key_name):
            return False

        # 松开时清除标志
        if is_mouse:
            if msg in (WM_XBUTTONUP, WM_MBUTTONUP):
                self._emulator.clear_emulating_flag(key_name)
        else:
            if msg in (WM_KEYUP, WM_SYSKEYUP):
                self._emulator.clear_emulating_flag(key_name)

        return True  # 放行

    def _check_restoring(self, key_name: str, msg: int) -> bool:
        """检查是否正在恢复按键"""
        if not self.is_restoring(key_name):
            return False

        if msg in (WM_KEYUP, WM_SYSKEYUP):
            self.clear_restoring_flag(key_name)

        return True  # 放行

    # ========== 公共接口 ==========

    def start(self) -> None:
        """启动所有监听器"""
        has_keyboard = any(s.type == 'keyboard' for s in self.shortcuts if s.enabled)
        has_mouse = any(s.type == 'mouse' for s in self.shortcuts if s.enabled)

        if has_keyboard:
            if self.keyboard_listener and self.keyboard_listener.is_alive():
                logger.debug("键盘监听器已在运行，跳过启动")
            else:
                if __import__('sys').platform == 'win32':
                    self.keyboard_listener = keyboard.Listener(
                        win32_event_filter=self.create_keyboard_filter()
                    )
                else:
                    options = ({'darwin_intercept': darwin_suppress_intercept(self)}
                               if __import__('sys').platform == 'darwin' else {})
                    self.keyboard_listener = keyboard.Listener(
                        on_press=self._keyboard_press,
                        on_release=self._keyboard_release,
                        **options
                    )
                self.keyboard_listener.start()
                logger.info("键盘监听器已启动")

        if has_mouse:
            if self.mouse_listener and self.mouse_listener.is_alive():
                logger.debug("鼠标监听器已在运行，跳过启动")
            else:
                if __import__('sys').platform == 'win32':
                    self.mouse_listener = mouse.Listener(
                        win32_event_filter=self.create_mouse_filter()
                    )
                else:
                    options = ({'darwin_intercept': darwin_suppress_intercept(self)}
                               if __import__('sys').platform == 'darwin' else {})
                    self.mouse_listener = mouse.Listener(
                        on_click=self._mouse_click,
                        **options
                    )
                self.mouse_listener.start()
                logger.info("鼠标监听器已启动")

        # 打印所有启用的快捷键
        for shortcut in self.shortcuts:
            if shortcut.enabled:
                mode = "长按" if shortcut.hold_mode else "单击"
                toggle = "可恢复" if shortcut.is_toggle_key() else "普通键"
                logger.info(f"  [{shortcut.key}] {mode}模式, 阻塞:{shortcut.suppress}, {toggle}")

    def _mouse_click(self, x, y, button, pressed):
        button_name = getattr(button, 'name', '')
        if button_name not in self.tasks:
            return
        task = self.tasks[button_name]
        if pressed:
            if self._capture_hold_active():
                self.darwin_suppress = False
                return
            self._event_handler.handle_keydown(button_name, task)
        else:
            self._handle_mouse_keyup(button_name, task)
        self.darwin_suppress = task.shortcut.suppress and not self.capture_hold

    def stop(self) -> None:
        """停止所有监听器和清理资源"""
        self.stop_config_watcher()

        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
                logger.debug("键盘监听器已停止")
            except Exception:
                pass
            finally:
                self.keyboard_listener = None
                
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
                logger.debug("鼠标监听器已停止")
            except Exception:
                pass
            finally:
                self.mouse_listener = None

        # 取消所有任务
        for task in self.tasks.values():
            if task.is_recording:
                task.cancel()

        # 关闭线程池
        self._pool.shutdown(wait=False)
        logger.debug("快捷键管理器线程池已关闭")
