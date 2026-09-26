# coding: utf-8
"""
识别结果处理模块

提供 ResultProcessor 类用于处理服务端返回的识别结果。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Optional

from config_client import ClientConfig as Config
from core.client.state import console
from core.protocol import RecognitionMessage

from core.client.output.text_output import TextOutput
from core.client.clipboard import needs_paste_for_remote
from core.tools.window_detector import get_active_window_info
from core.tools import key_send
from . import logger

from core.client.udp.udp_broadcaster import broadcast_output_udp
from core.tools.zhconv import convert as zhconv_convert
from core.client.audio.file_manager import AudioFileManager
from core.client.llm.llm_write_md import write_llm_md
from core.client.voice_phrase.replace import apply_replacements

if TYPE_CHECKING:
    from core.client.connection.websocket_manager import WebSocketManager
    from core.client.state import ClientState
    from core.client.app import SaiClient
    from core.client.hotword.manager import HotwordManager
    from core.client.diary.diary_writer import DiaryWriter



def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


async def _auto_enter(delay: float) -> None:
    """延迟发送回车键"""
    await asyncio.sleep(delay)
    key_send.press_and_release('enter')
    logger.debug(f"自动回车已发送 (延迟 {delay}s)")


class ResultProcessor:
    """
    识别结果处理器
    
    负责处理服务端返回的识别结果：
    - 接收 WebSocket 消息
    - 执行热词替换
    - 可选地调用 LLM 进行润色
    - 输出最终文本
    - 保存录音和日记
    """
    
    def __init__(self, app: SaiClient):
        """
        初始化结果处理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._exit_event = asyncio.Event()
        self._loop = asyncio.get_running_loop()  # 保存事件循环引用

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @property
    def ws(self) -> WebSocketManager:
        """快捷访问连接管理器"""
        return self.app.ws

    @property
    def hotword(self) -> HotwordManager:
        """快捷访问热词管理器"""
        return self.app.hotword

    @property
    def output(self) -> TextOutput:
        """快捷访问文本输出器"""
        return self.app.output

    @property
    def diary(self) -> DiaryWriter:
        """快捷访问日记写入器"""
        return self.app.diary

    def request_exit(self):
        """请求退出处理循环（线程安全）"""
        logger.info("收到退出请求，设置退出事件")

        # 线程安全地设置事件
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._exit_event.set)
            logger.debug("已通过 call_soon_threadsafe 设置退出事件")
        else:
            self._exit_event.set()
            logger.debug("已直接设置退出事件")
    
    def _format_llm_result(self, llm_result) -> str:
        """格式化 LLM 结果输出"""
        polished_text = llm_result.result
        role_name = llm_result.role_name
        processed = llm_result.processed
        token_count = llm_result.token_count
        generation_time = llm_result.generation_time  # 使用生成时间（从第一个 token 开始）

        polished_text = polished_text.replace('\n', ' ').replace('\r', ' ')
        max_display_length = 50
        if len(polished_text) > max_display_length:
            polished_text = polished_text[:max_display_length] + '...'

        role_label = f'[{role_name}]' if role_name else ''
        result_text = f'[green]{polished_text}[/green]' if processed else polished_text

        if token_count == 0 and polished_text:
            token_count = _estimate_tokens(polished_text)

        # 使用生成时间计算速度（更准确）
        if processed and generation_time > 0:
            speed = token_count / generation_time if token_count > 0 else 0
            speed_label = f'    {speed:.1f} tokens/s' if speed > 0 else ''
        else:
            speed_label = ''

        return f'    模型结果{role_label}：{result_text}{speed_label}'
    
    def _log_modifier_key_state(self) -> None:
        """
        检测并记录当前按下的所有键
        
        用于调试按键卡住问题。
        """
        try:
            # 获取所有当前按下的键
            pressed_keys = key_send.pressed_keys()
            logger.debug(f"当前按下的键: {pressed_keys}")
                
        except Exception as e:
            logger.debug(f"检测按键状态失败: {e}")
    
    async def start(self) -> None:
        """开启工作循环（含自动重联）"""
        while not self._exit_event.is_set():
            # 1. 尝试连接，失败则重试
            if not await self.ws.connect():
                await asyncio.sleep(2)
                continue

            # 2. 消息接收循环
            while not self._exit_event.is_set():
                try:
                    message = await self.ws.receive()
                    if message is None: break
                    await self._handle_message(message)
                except Exception as e:
                    logger.debug(f"连接异常中断: {e}")
                    break

            console.print('[bold red]已断开服务端连接[/bold red]\n')
            self._cleanup()
            

    async def _handle_message(self, message: Optional[RecognitionMessage]) -> None:
        """处理接收到的消息"""
        if message is None:
            return
        # 语音短语匹配由本次任务的终态消息统一消费（含错误/空白结果），避免残留
        voice_matches = (
            self.state.pop_voice_matches(message.task_id)
            if message.is_final else []
        )
        message_error = getattr(message, "error", "")
        if message_error:
            logger.error(message_error)
            if message.is_final:
                self.state.pop_audio_file(message.task_id)
            from core.runtime_paths import DATA_DIR
            import json
            try:
                error_path = DATA_DIR / "logs" / "asr-error.json"
                error_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = error_path.with_suffix(".tmp")
                temp_path.write_text(json.dumps({"error": message_error}), encoding="utf-8")
                temp_path.replace(error_path)
            except OSError:
                pass
            return

        # 使用 text 字段（简单拼接结果，用于语音输入）
        text = message.text
        original_text = text  # 保存原始识别结果
        delay = message.time_complete - message.time_submit

        if message.is_final:
            logger.info(f"收到最终识别结果: {text}, 时延: {delay:.2f}s")
        else:
            logger.debug(
                f"接收到识别结果，文本: {text[:50]}{'...' if len(text) > 50 else ''}, "
                f"时延: {delay:.2f}s"
            )

        # 如果非最终结果，继续等待
        if not message.is_final:
            return

        if not text.strip() or text.strip().casefold() == "/sil":
            logger.info("忽略空白或 /sil 识别结果，不执行输入或 LLM 操作")
            self.state.pop_audio_file(message.task_id)
            return

        # 0. 语音短语替换（音频层命中，优先级最高）
        #    必须放在繁体转换之前：tokens 来自服务端 text_accu（简体），
        #    文本先转成繁体后 token 在文中的定位会失败。
        if voice_matches:
            try:
                text, voice_applied = apply_replacements(
                    text, message.tokens, message.timestamps, voice_matches,
                    accu_text=getattr(message, "text_accu", "") or "",
                )
            except Exception as e:
                voice_applied = []
                logger.warning(f"语音短语替换失败: {e}")
            if voice_applied:
                summary = "、".join(match.text for match in voice_applied)
                console.print(f'    语音短语：[magenta]{summary}[/]')
                logger.info(f"语音短语替换: {summary}")
                if Config.voice_phrase_hint:
                    try:
                        from core.client.ui import toast
                        toast(f"语音短语替换：{summary}", duration=2000)
                    except Exception as e:
                        logger.debug(f"语音短语提示失败: {e}")
            else:
                logger.debug("语音短语命中但未能定位文本区间，已跳过替换")

        # 繁体转换（放在语音短语替换之后，见上）
        if Config.traditional_convert:
            try:
                text = zhconv_convert(text, Config.traditional_locale)
            except Exception as e:
                logger.warning(f"繁体转换失败: {e}")

        # 1. 音素检索，热词替换
        hotword_start = time.monotonic()
        correction_result = self.hotword.get_phoneme_corrector().correct(text, k=10)
        if Config.hot:
            text = correction_result.text

        # 2. 去掉末尾符号
        text = TextOutput.strip_punc(text)

        # 3. 正则替换
        text = self.hotword.get_rule_corrector().substitute(text)
        hotword_elapsed = time.monotonic() - hotword_start

        # 保存最近一次识别结果
        self.state.last_recognition_text = text

        # 控制台输出：时延 + 热词时延合并到一行
        hotword_label = f'  热词时延: {hotword_elapsed:.2f}s' if Config.hot else ''
        console.print(f'    转录时延：{delay:.2f}s{hotword_label}')

        # 先显示原始识别结果
        original_text_stripped = TextOutput.strip_punc(original_text)
        console.print(f'    识别结果：[green]{original_text_stripped}')

        # 如果发生了热词替换，显示替换后的结果
        if original_text_stripped != text:
            console.print(f'    热词替换：[cyan]{text}')
            logger.debug(f"热词替换后: {text[:50]}{'...' if len(text) > 50 else ''}")

        # 热词匹配情况
        matched_hotwords = correction_result.matches
        potential_hotwords = correction_result.similars

        # 1. 显示完全匹配/已替换的热词
        if matched_hotwords and Config.hot:
            # 提取热词文本 (现为 (原词, 热词, 分数))
            replaced_info = [f"{origin}->[green4]{hw}[/]" for origin, hw, score in matched_hotwords]
            console.print(f'    完全匹配：{", ".join(replaced_info)}')

        # 2. 潜在热词记录到 log
        if potential_hotwords and Config.hot:
            replaced_set = {hw for origin, hw, score in matched_hotwords}
            potential_matches = [(origin, hw, score) for origin, hw, score in potential_hotwords if hw not in replaced_set]
            if potential_matches:
                log_str = "; ".join([f"{origin}->{hw}({score:.2f})" for origin, hw, score in potential_matches])
                logger.debug(f"潜在热词: {log_str}")

        # 窗口兼容性检测（粘贴快捷键的设定优先于配置）
        override = self.state.consume_paste_override()
        paste = Config.paste if override is None else override
        if override is not None:
            logger.debug("本次录音来自粘贴快捷键，使用剪贴板粘贴输出")
        process_name = get_active_window_info().get('process_name', '')
        logger.debug(f"当前活动窗口: {process_name}")
        if any(app.lower() == process_name.lower() for app in Config.paste_apps):
            paste = True
            logger.debug(f"检测到兼容性应用: {process_name}，使用粘贴模式")
        if not paste and needs_paste_for_remote(text):
            paste = True
            logger.debug(f"当前是远程桌面/虚拟桌面 {process_name}，中文只能粘贴输出")

        # 自动回车检测
        for app, delay in Config.enter_apps:
            if app.lower() == process_name:
                asyncio.create_task(_auto_enter(delay))

        # LLM 处理和输出
        llm_result = None
        if Config.llm_enabled:
            llm_result = await self.app.llm.process_and_output(
                text,
                paste=paste,
                matched_hotwords=potential_hotwords  # 传递上下文热词给 LLM
            )
        else:
            await self.output.output(text, paste=paste)
            self.state.set_output_text(text)
            broadcast_output_udp(text)

        # 保存录音与写入 md 文件
        file_audio = None
        if Config.save_audio:
            # 重命名音频文件
            file_path = self.state.pop_audio_file(message.task_id)
            if file_path:
                file_manager = AudioFileManager()
                file_manager.file_path = file_path
                file_audio = file_manager.rename(text, message.time_start)

            # 写入日记
            self.diary.write(text, message.time_start, file_audio)

        # LLM 结果显示和保存
        if Config.llm_enabled and llm_result and llm_result.processed:
            console.print(self._format_llm_result(llm_result))
            write_llm_md(
                llm_result.input_text,
                llm_result.result,
                llm_result.role_name,
                message.time_start,
                file_audio
            )

        # 检测修饰键状态（调试用）
        self._log_modifier_key_state()

        console.line()
    
    def _cleanup(self) -> None:
        """清理资源"""
        if self.state.websocket is not None:
            try:
                if self.state.websocket.closed:
                    self.state.websocket = None
                    logger.debug("WebSocket 连接已清理")
            except Exception:
                self.state.websocket = None
