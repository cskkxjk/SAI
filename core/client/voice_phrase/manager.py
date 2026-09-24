# -*- coding: utf-8 -*-
"""语音短语模板管理

惰性加载录入的语音短语模板，索引变化时自动重载；
对上层提供 match() 统一入口，开关与阈值取自客户端配置。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from config_client import ClientConfig as Config
from core import get_logger
from core.client.voice_phrase.matcher import (
    DEFAULT_THRESHOLD,
    PhraseMatch,
    PhraseTemplate,
    match_phrases,
)
from core.client.voice_phrase.storage import VoicePhraseStore

logger = get_logger('client')


class VoicePhraseManager:
    """管理语音短语模板与匹配。"""

    def __init__(self, store: Optional[VoicePhraseStore] = None):
        self._store = store
        self._templates: List[PhraseTemplate] = []
        self._signature = None

    @property
    def store(self) -> VoicePhraseStore:
        if self._store is None:
            self._store = VoicePhraseStore()
        return self._store

    def _index_signature(self):
        try:
            stat = self.store.index_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def reload_if_changed(self) -> List[PhraseTemplate]:
        """索引文件变化时重新加载模板，返回当前模板列表。"""
        signature = self._index_signature()
        if signature != self._signature:
            self._signature = signature
            try:
                self._templates = self.store.templates()
            except Exception as exc:
                logger.warning(f"加载语音短语模板失败: {exc}")
                self._templates = []
            logger.debug(f"语音短语模板已加载: {len(self._templates)} 条")
        return self._templates

    def match(
        self,
        audio: np.ndarray,
        spans: Optional[Sequence[Tuple[float, float]]] = None,
    ) -> List[PhraseMatch]:
        """在整段录音中匹配已录入的语音短语。"""
        if not getattr(Config, "voice_phrase", False):
            return []
        templates = self.reload_if_changed()
        if not templates:
            return []
        threshold = float(getattr(Config, "voice_phrase_threshold", DEFAULT_THRESHOLD)
                          or DEFAULT_THRESHOLD)
        return match_phrases(
            np.asarray(audio, dtype=np.float32),
            templates,
            spans=spans,
            threshold=threshold,
        )


_manager: Optional[VoicePhraseManager] = None


def get_voice_phrase_manager() -> VoicePhraseManager:
    """获取全局语音短语管理器（惰性创建）。"""
    global _manager
    if _manager is None:
        _manager = VoicePhraseManager()
    return _manager
