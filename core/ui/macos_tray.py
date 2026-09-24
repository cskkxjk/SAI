# coding: utf-8
"""macOS 原生菜单栏状态项（NSStatusBar）封装。

pystray 在 macOS 上必须在主线程运行且与 Tk 主循环冲突，因此桌面端在 macOS
上改用原生 NSStatusBar。所有调用（创建、改图标、移除）都必须在主线程执行，
即放在 Tk 的回调里。

对外接口刻意与 pystray.Icon 对齐（visible / icon / title / notify / stop），
以便 gui_launcher 用同一套代码驱动两个平台。
"""
from __future__ import annotations

from io import BytesIO
from typing import Callable, Optional, Sequence, Tuple

import objc
from AppKit import (
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSData, NSObject


class _TrayTarget(NSObject):
    """菜单项事件目标；用 tag 区分是哪一个菜单项。"""

    def initWithCallbacks_(self, callbacks):
        self = objc.super(_TrayTarget, self).init()
        if self is None:
            return None
        self._callbacks = callbacks
        return self

    def handle_(self, sender):
        callback = self._callbacks.get(int(sender.tag()))
        if callback is not None:
            callback()


def _pil_to_nsimage(image, size: int = 18):
    """把 PIL.Image 转成 18pt 的 NSImage。"""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    data = NSData.dataWithBytes_length_(raw, len(raw))
    ns_image = NSImage.alloc().initWithData_(data)
    if ns_image is not None:
        ns_image.setSize_((size, size))
    return ns_image


class MacTray:
    def __init__(
        self,
        icon_path: str,
        title: str,
        actions: Sequence[Tuple[str, Callable[[], None]]],
    ):
        self.title = title
        self.visible = True
        self._icon_image = None

        self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength)

        icon = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
        if icon is not None:
            icon.setSize_((18, 18))
            self._status_item.button().setImage_(icon)
        else:
            self._status_item.button().setTitle_("SAI")

        self._callbacks = {}
        self._target = _TrayTarget.alloc().initWithCallbacks_(self._callbacks)
        menu = NSMenu.alloc().init()
        for index, (label, callback) in enumerate(actions):
            self._callbacks[index] = callback
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                label, "handle:", "")
            item.setTarget_(self._target)
            item.setTag_(index)
            menu.addItem_(item)
        self._status_item.setMenu_(menu)
        self._menu = menu

    @property
    def icon(self):
        return self._icon_image

    @icon.setter
    def icon(self, image):
        self._icon_image = image
        ns_image = _pil_to_nsimage(image)
        if ns_image is not None and self.visible:
            self._status_item.button().setImage_(ns_image)

    def notify(self, message: str, title: Optional[str] = None) -> None:
        """尽力发送系统通知（失败不影响主流程）。"""
        try:
            import subprocess
            text = str(message or "").replace('"', "'")
            heading = str(title or self.title).replace('"', "'")
            subprocess.Popen(
                ["osascript", "-e",
                 f'display notification "{text}" with title "{heading}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def stop(self) -> None:
        if not self.visible:
            return
        try:
            NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
        except Exception:
            pass
        self.visible = False
