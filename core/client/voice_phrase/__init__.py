# -*- coding: utf-8 -*-
"""语音短语替换：音频层模板匹配，命中后用标注文字替换识别结果。"""

from core.client.voice_phrase.features import FEATURE_VERSION, mfcc
from core.client.voice_phrase.matcher import (
    DEFAULT_MARGIN,
    DEFAULT_THRESHOLD,
    PhraseMatch,
    PhraseTemplate,
    dtw_distance,
    match_phrases,
    segment_by_energy,
)
from core.client.voice_phrase.storage import VoicePhraseStore

__all__ = [
    "DEFAULT_MARGIN",
    "DEFAULT_THRESHOLD",
    "FEATURE_VERSION",
    "PhraseMatch",
    "PhraseTemplate",
    "VoicePhraseStore",
    "dtw_distance",
    "match_phrases",
    "mfcc",
    "segment_by_energy",
]
