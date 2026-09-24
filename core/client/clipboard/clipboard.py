# coding: utf-8
"""
剪贴板工具模块

提供统一的剪贴板操作接口，包括：
1. 安全读取剪贴板（支持多种编码）
2. 安全写入剪贴板
3. 剪贴板保存/恢复上下文管理器
4. 粘贴文本（模拟 Ctrl+V）
"""
import asyncio
import platform
from contextlib import contextmanager
import pyclip
from pynput import keyboard
from config_client import ClientConfig as Config
from . import logger


# 支持的编码列表
CLIPBOARD_ENCODINGS = ['utf-8', 'gbk', 'utf-16', 'latin1']

# 远程桌面/虚拟机等目标的应用进程名：剪贴板数据要经网络送到对端，
# 且对端应用是按需向本机索要数据，所以要等得更久，否则会粘到旧内容
SLOW_TARGET_PROCESSES = (
    # 微软远程桌面 / Windows App / Hyper-V 控制台
    'mstsc.exe', 'rdpclip.exe', 'msrdc.exe', 'avd.exe', 'windowsapp.exe',
    'vmconnect.exe', 'vmware-vmx.exe',
    # 第三方虚拟桌面 / 远程控制客户端
    'rvlsession.exe', 'srapcsession.exe', 'srapcsession2.exe',
    'sfremoteappclient.exe', 'sfremoteappsession.exe', 'sangforgnavbar.exe',
    'parsec.exe', 'anydesk.exe', 'teamviewer.exe', 'sunloginclient.exe',
)
SLOW_TARGET_FACTOR = 3.0
# 远程目标粘贴后至少等这么久再恢复剪贴板：对端要经过一次网络往返才能取到数据
SLOW_TARGET_RESTORE_DELAY = 3.0
# 慢目标发送 Ctrl+V 前至少等待这么久，让剪贴板同步到对端（不然对端会粘到旧内容）
SLOW_TARGET_SETTLE_DELAY = 1.0
# 往远程目标逐字打字时的按键间隔：太快会被转发层丢弃或乱序
REMOTE_TYPE_INTERVAL = 0.02


def _target_delay_factor() -> float:
    """当前前台窗口是远程桌面之类慢目标时，返回加长的等待系数"""
    try:
        from core.tools.window_detector import get_active_window_info
        process_name = (get_active_window_info().get('process_name') or '').lower()
    except Exception:
        return 1.0
    return SLOW_TARGET_FACTOR if process_name in SLOW_TARGET_PROCESSES else 1.0


def is_remote_target() -> bool:
    """当前前台窗口是否是远程桌面 / 虚拟桌面这类慢目标"""
    return _target_delay_factor() > 1.0


def needs_paste_for_remote(text: str) -> bool:
    """
    远程桌面里是否必须改用剪贴板

    按键注入只能送出“按键”，中文要靠对端输入法组字；而远程桌面客户端
    对 Unicode 注入（keyboard.write 打中文的方式）支持普遍不好，实测会丢字
    或变乱码。所以内容含非 ASCII（中文等）时，远程目标只能走剪贴板。
    """
    return bool(text) and not text.isascii() and is_remote_target()


def _config_number(name: str, default: float) -> float:
    try:
        return float(getattr(Config, name, default))
    except (TypeError, ValueError):
        return default


def safe_paste() -> str:
    """
    安全地从剪贴板读取并解码文本

    尝试多种编码方式，确保能够正确读取

    Returns:
        解码后的文本字符串，失败返回空字符串
    """
    try:
        clipboard_data = pyclip.paste()

        if isinstance(clipboard_data, str):
            return clipboard_data

        # 尝试多种编码方式
        for encoding in CLIPBOARD_ENCODINGS:
            try:
                return clipboard_data.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                continue

        # 如果所有编码都失败，返回空字符串
        logger.debug(f"剪贴板解码失败，尝试了编码: {CLIPBOARD_ENCODINGS}")
        return ""

    except Exception as e:
        logger.warning(f"剪贴板读取失败: {e}")
        return ""


def safe_copy(content: str) -> bool:
    """
    安全地复制内容到剪贴板

    Args:
        content: 要复制的内容

    Returns:
        是否成功
    """
    if not content:
        return False

    try:
        pyclip.copy(content)
        logger.debug(f"剪贴板写入成功，长度: {len(content)}")
        return True
    except Exception as e:
        logger.warning(f"剪贴板写入失败: {e}")
        return False


def copy_to_clipboard(content: str):
    """
    复制内容到剪贴板（兼容旧 API）

    Args:
        content: 要复制的内容
    """
    safe_copy(content)


@contextmanager
def save_and_restore_clipboard():
    """
    剪贴板保存/恢复上下文管理器

    用法:
        with save_and_restore_clipboard():
            # 在这里操作剪贴板
            pyclip.copy("临时内容")
        # 退出后剪贴板恢复原内容
    """
    original = safe_paste()
    try:
        yield
    finally:
        if original:
            pyclip.copy(original)
            logger.debug("剪贴板已恢复")


async def paste_text(text: str, restore_clipboard: bool = True):
    """
    通过模拟 Ctrl+V 粘贴文本

    Args:
        text: 要粘贴的文本
        restore_clipboard: 粘贴后是否恢复原剪贴板内容
    """
    # 保存剪切板
    original = ''
    if restore_clipboard:
        try:
            original = safe_paste()
        except:
            pass

    # 复制要粘贴的文本；剪贴板可能被其它程序短暂占用，写入没生效时重试几次
    for attempt in range(3):
        pyclip.copy(text)
        if safe_paste() == text:
            break
        if attempt < 2:
            logger.debug("剪贴板写入未生效，稍后重试")
            await asyncio.sleep(0.08 * (attempt + 1))
    else:
        logger.warning("剪贴板写入未生效，粘贴结果可能不是本次识别内容")
    logger.debug(f"已复制文本到剪贴板，长度: {len(text)}")

    # 远程桌面等慢目标需要时间把剪贴板同步到对端，先等一会儿再发粘贴快捷键。
    # 本地目标（factor==1）在 macOS 上剪贴板是同步写入且已校验，几乎无需等待。
    factor = _target_delay_factor()
    settle = _config_number('paste_settle_delay', 0.12) * factor
    if factor > 1.0:
        settle = max(settle, SLOW_TARGET_SETTLE_DELAY)
    elif platform.system() == 'Darwin':
        settle = min(settle, 0.02)
    if settle > 0:
        await asyncio.sleep(settle)

    # 粘贴结果（使用 pynput 模拟粘贴快捷键）
    controller = keyboard.Controller()
    if platform.system() == 'Darwin':
        # macOS: Command+V
        with controller.pressed(keyboard.Key.cmd):
            controller.tap('v')
        paste_combo = 'Cmd+V'
    else:
        # Windows/Linux: Ctrl+V
        with controller.pressed(keyboard.Key.ctrl):
            controller.tap('v')
        paste_combo = 'Ctrl+V'

    logger.debug(f"已发送粘贴命令 ({paste_combo})")

    # 还原剪贴板：要等目标把数据读走之后再恢复，否则会粘到旧内容
    if restore_clipboard and original:
        delay = max(0.1, _config_number('restore_clip_delay', 0.5)) * factor
        if factor > 1.0:
            delay = max(delay, SLOW_TARGET_RESTORE_DELAY)
        await asyncio.sleep(delay)
        if safe_paste() == text:
            pyclip.copy(original)
            logger.debug(f"剪贴板已恢复（等待 {delay:.2f}s）")
        else:
            logger.debug("剪贴板已被其它程序改写，跳过恢复")
