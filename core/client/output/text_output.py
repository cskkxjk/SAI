# coding: utf-8
"""
文本输出模块

提供 TextOutput 类用于将识别结果输出到当前窗口。
"""

from __future__ import annotations

from typing import Optional
import re
import time

import keyboard
from pynput.keyboard import Controller as PynputController

from config_client import ClientConfig as Config
from core.tools.asyncio_to_thread import to_thread
from core.tools.window_detector import get_active_window_info
from . import logger


# 语义字符计数模式：匹配中文字、英文单词、数字
# 中文字（含日韩等）各算 1 个语义单元，英文连续字母算 1 个，连续数字算 1 个
_SEMANTIC_UNIT_RE = re.compile(
    r'[一-鿿㐀-䶿豈-﫿]'
    r'|[a-zA-Z]+'
    r'|\d+'
)


def count_semantic_units(text: str) -> int:
    """计算语义字符数：中文字=1，英文单词=1，数字=1"""
    return len(_SEMANTIC_UNIT_RE.findall(text))


def type_text(text: str) -> None:
    """
    逐字键入文本（LLM 流式输出与普通输出共用这一份实现）

    - 普通窗口：keyboard.write，速度快
    - 远程桌面 / 虚拟桌面（深信服等）：实测 keyboard.write 的扫描码事件会被
      这些客户端搅成乱码，必须改用 pynput 的虚拟键事件，并逐字留出间隔
    - 远程目标无法键入中文（对端没有输入法组字），这类内容应走剪贴板粘贴
    """
    if not text:
        return

    from core.client.clipboard import REMOTE_TYPE_INTERVAL, is_remote_target
    if not is_remote_target():
        keyboard.write(text)
        return

    if not text.isascii():
        logger.warning("远程桌面无法键入中文等非 ASCII 内容，请改用粘贴快捷键输出")
        return

    controller = PynputController()
    for char in text:
        controller.type(char)
        time.sleep(REMOTE_TYPE_INTERVAL)


class TextOutput:
    """
    文本输出器
    
    提供文本输出功能，支持模拟打字和粘贴两种方式。
    """
    
    @staticmethod
    def strip_punc(text: str) -> str:
        """
        消除末尾最后一个标点

        语义字符数不超过 trash_punc_thresh 时去除末尾标点，
        超过时保留标点（长句正常说话场景）。
        在 trash_punc_apps 指定的应用中，不受阈值限制，必定去除。

        Args:
            text: 原始文本

        Returns:
            处理后的文本
        """
        if not text or not Config.trash_punc:
            return text

        # 检查是否在强制去标点的应用中
        force_strip = False
        if Config.trash_punc_apps:
            process_name = get_active_window_info().get('process_name', '').lower()
            if any(app.lower() == process_name for app in Config.trash_punc_apps):
                force_strip = True

        if not force_strip and Config.trash_punc_thresh > 0 and count_semantic_units(text) > Config.trash_punc_thresh:
            return text

        clean_text = re.sub(f"(?<=.)[{Config.trash_punc}]$", "", text)
        return clean_text
    
    async def output(self, text: str, paste: Optional[bool] = None) -> None:
        """
        输出识别结果
        
        根据配置选择使用模拟打字或粘贴方式输出文本。
        
        Args:
            text: 要输出的文本
            paste: 是否使用粘贴方式（None 表示使用配置值）
        """
        if not text:
            return
        
        # 确定输出方式
        if paste is None:
            paste = Config.paste

        # 远程桌面 / 虚拟桌面里中文只能靠剪贴板，避免静默丢失
        if not paste:
            from core.client.clipboard import needs_paste_for_remote
            if needs_paste_for_remote(text):
                paste = True
                logger.debug("远程桌面 / 虚拟桌面无法键入中文，改用剪贴板粘贴输出")

        if paste:
            await self._paste_text(text)
        else:
            # 远程目标逐字键入会 sleep，放到线程里避免阻塞事件循环
            await to_thread(type_text, text)
    
    async def _paste_text(self, text: str) -> None:
        """
        通过粘贴方式输出文本

        统一走 core.client.clipboard 的实现：先写入剪贴板并校验，
        再模拟 Ctrl+V，最后延迟恢复原剪贴板（远程桌面等慢目标会等更久）。

        Args:
            text: 要粘贴的文本
        """
        from core.client.clipboard import paste_text
        logger.debug(f"使用粘贴方式输出文本，长度: {len(text)}")
        await paste_text(text, restore_clipboard=Config.restore_clip)
    
    def _type_text(self, text: str) -> None:
        """
        通过模拟打字方式输出文本

        具体实现见模块级 type_text：普通窗口用 keyboard.write 快速键入，
        远程桌面 / 虚拟桌面（深信服等）改用 pynput。

        Args:
            text: 要输出的文本
        """
        logger.debug(f"使用打字方式输出文本，长度: {len(text)}")
        type_text(text)
