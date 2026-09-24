# coding: utf-8
import asyncio
import os
import sys
from pathlib import Path
from . import logger
from ..ui import TipsDisplay
from ..state import console
from config_client import ClientConfig as Config, __version__


class MicRunner:
    """
    麦克风模式运行器：负责麦克风模式下的资源初始化、识别处理器循环及生命周期监控。
    """
    def __init__(self, app):
        self.app = app
        self.processor = None

    @property
    def state(self):
        return self.app.state

    @property
    def ws_manager(self):
        return self.app.ws

    @property
    def tray_manager(self):
        return self.app.tray

    def start_resources(self):
        """初始化麦克风模式特有资源 (音频硬件、快捷键、UI 托盘)"""
        # 0. 上次进程被强杀可能残留按住的修饰键，先收尾
        from core.tools.stuck_keys import release_stuck_modifiers
        released = release_stuck_modifiers()
        if released:
            logger.warning(f"检测到残留的修饰键，已释放: {', '.join(released)}")

        # 1. 托盘
        self.tray_manager.start()

        # 2. UI 提示
        TipsDisplay.show_mic_tips()

        # 2.5 macOS 权限自检（缺失时快捷键/上屏不会生效）
        if sys.platform == 'darwin':
            from core.tools import macos_permissions
            missing = macos_permissions.missing()
            if missing:
                logger.warning("缺少 macOS 权限，全局快捷键/模拟按键可能无效:\n%s",
                               macos_permissions.describe())
                console.print(
                    "\n[bold yellow]检测到缺少 macOS 权限，请到「系统设置 > 隐私与安全性」授予：[/bold yellow]")
                console.print(macos_permissions.describe())
                console.print("[yellow]授权后请重启本程序。[/yellow]\n")

        # Only listen for shortcuts here; each recording owns its audio stream.
        self.app.shortcut.start()
        # 快捷键配置改变后热重载，不必重启客户端（模型无需重新加载）
        self.app.shortcut.start_config_watcher()
        
        # 4. 开启 UDP 控制 (如果启用)
        if Config.udp_control:
            self.app.udp.start()

        # 5. 开启后台服务 (热词、LLM)
        self.app.hotword.start()
        self.app.llm.start()

    async def run(self):
        """麦克风模式主入口"""
        
        logger.info("=" * 50)
        logger.info(f"SAI Client {__version__} (麦克风模式)")
        logger.info(f"日志级别: {Config.log_level}")
        
        # Warm up before enabling shortcuts. No samples are sent or saved.
        if Config.keep_microphone_open:
            if await asyncio.to_thread(self.app.stream.prepare) is None:
                raise RuntimeError("Cannot initialize microphone; see client_latest.log")

        # 1. 资源启动
        self.start_resources()
        if os.environ.get("SAI_READY_FILE"):
            for _ in range(30):
                if await self.ws_manager.connect():
                    Path(os.environ["SAI_READY_FILE"]).touch()
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError("无法连接识别服务，请查看 client_latest.log")
        
        # 2. 启动核心处理器 (内部处理连接与循环)
        
        from ..output import ResultProcessor
        self.processor = ResultProcessor(self.app)
        await self.processor.start()
            
