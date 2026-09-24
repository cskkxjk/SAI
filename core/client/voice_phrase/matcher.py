# -*- coding: utf-8 -*-
"""语音短语匹配。

流程：语音区间 -> 分段均值余弦粗筛（滑窗取前 K 个候选）-> 带宽 DTW 精筛 ->
阈值 + 次优裕度判定 -> 输出命中的音频时间段。numba 缺失时自动退化为纯 Python。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from core.client.voice_phrase.features import SAMPLE_RATE, mfcc, trim_silence

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]

        def decorate(func):
            return func

        return decorate

DEFAULT_THRESHOLD = 0.45
DEFAULT_MARGIN = 1.08
SEGMENT_COUNT = 4
COARSE_TOP_K = 5
COARSE_HOP_SEC = 0.05


@dataclass
class PhraseTemplate:
    """一条短语的一份录音样本。"""

    phrase_id: str
    text: str
    feature: np.ndarray
    duration: float = 0.0
    sample_index: int = 1

    def __post_init__(self):
        if not self.duration:
            self.duration = self.feature.shape[0] * 0.01


@dataclass
class PhraseMatch:
    """命中结果：start/end 为相对本次录音的秒数。"""

    phrase_id: str
    text: str
    start: float
    end: float
    score: float
    sample_index: int = 1


@njit(cache=True)
def _dtw_subsequence(template: np.ndarray, window: np.ndarray) -> float:
    """模板在窗口内的子序列 DTW（起点终点自由），按模板长度归一。"""
    n, dim = template.shape[0], template.shape[1]
    m = window.shape[0]
    inf = np.float32(1e9)
    prev = np.zeros(m + 1, dtype=np.float32)
    curr = np.empty(m + 1, dtype=np.float32)
    for i in range(1, n + 1):
        curr[0] = inf
        for j in range(1, m + 1):
            dist = np.float32(0.0)
            for k in range(dim):
                diff = template[i - 1, k] - window[j - 1, k]
                dist += diff * diff
            dist = dist ** np.float32(0.5)
            best = prev[j - 1]
            if prev[j] < best:
                best = prev[j]
            if curr[j - 1] < best:
                best = curr[j - 1]
            curr[j] = dist + best
        prev, curr = curr, prev
    best_end = prev[1]
    for j in range(2, m + 1):
        if prev[j] < best_end:
            best_end = prev[j]
    return float(best_end) / n


def dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """模板 a 在窗口 b 中的子序列 DTW 距离（越小越相似）。"""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.ndim != 2 or b.ndim != 2 or not a.shape[0] or not b.shape[0]:
        return float("inf")
    if b.shape[0] < a.shape[0]:
        return float("inf")
    return _dtw_subsequence(a, b)


def _segment_means(feature: np.ndarray, count: int = SEGMENT_COUNT) -> Optional[np.ndarray]:
    n = feature.shape[0]
    if n <= 0:
        return None
    bounds = np.linspace(0, n, count + 1).astype(int)
    means = np.empty((count, feature.shape[1]), dtype=np.float32)
    for i in range(count):
        lo = bounds[i]
        hi = max(lo + 1, bounds[i + 1])
        means[i] = feature[lo:hi].mean(axis=0)
    return means


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-8:
        return 0.0
    return float(np.dot(a, b) / denom)


def _coarse_starts(feature: np.ndarray, template: np.ndarray, top_k: int) -> List[int]:
    """分段均值余弦粗筛，返回候选起点帧号。"""
    n = template.shape[0]
    total = feature.shape[0]
    if n > total:
        return []
    tpl_means = _segment_means(template)
    if tpl_means is None:
        return []
    hop = max(1, int(round(COARSE_HOP_SEC / 0.01)))
    starts = list(range(0, total - n + 1, hop))
    if not starts:
        return []
    scored: List[Tuple[float, int]] = []
    for start in starts:
        means = _segment_means(feature[start:start + n])
        if means is None:
            continue
        scored.append((_cosine(means.ravel(), tpl_means.ravel()), start))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [start for _, start in scored[:top_k]]


def segment_by_energy(
    audio: np.ndarray,
    min_speech: float = 0.35,
    max_speech: float = 4.5,
    merge_gap: float = 0.15,
) -> List[Tuple[float, float]]:
    """无时间戳时的兜底：能量门限分窗，返回 [(start, end)] 秒。"""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = int(SAMPLE_RATE * 0.02)
    hop = int(SAMPLE_RATE * 0.01)
    if audio.size < frame:
        return []
    n = 1 + (audio.size - frame) // hop
    indices = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt((audio[indices] ** 2).mean(axis=1) + 1e-12)
    floor = float(np.percentile(rms, 20))
    threshold = max(floor * 3.0, float(rms.max()) * 0.08, 0.005)
    active = rms > threshold

    intervals: List[List[float]] = []
    i = 0
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j < n and active[j]:
            j += 1
        start = i * hop / SAMPLE_RATE
        end = (j * hop + frame) / SAMPLE_RATE
        if intervals and start - intervals[-1][1] < merge_gap:
            intervals[-1][1] = end
        else:
            intervals.append([start, end])
        i = j

    spans: List[Tuple[float, float]] = []
    for start, end in intervals:
        if end - start < min_speech:
            continue
        if end - start <= max_speech:
            spans.append((start, end))
            continue
        pieces = int(np.ceil((end - start) / max_speech))
        step = (end - start) / pieces
        for k in range(pieces):
            spans.append((start + k * step, start + (k + 1) * step))
    return spans


def match_phrases(
    audio: np.ndarray,
    templates: Sequence[PhraseTemplate],
    spans: Optional[Sequence[Tuple[float, float]]] = None,
    threshold: float = DEFAULT_THRESHOLD,
    margin: float = DEFAULT_MARGIN,
    top_k: int = COARSE_TOP_K,
) -> List[PhraseMatch]:
    """在 audio 中匹配短语模板，返回去重后的命中列表。"""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    templates = [t for t in templates if t.feature is not None and t.feature.shape[0] >= 8]
    if not audio.size or not templates:
        return []
    if spans is None:
        spans = segment_by_energy(audio)

    candidates: List[PhraseMatch] = []
    for span_start, span_end in spans:
        lo = max(0, int(span_start * SAMPLE_RATE))
        hi = min(audio.size, int(span_end * SAMPLE_RATE))
        if hi - lo < int(0.2 * SAMPLE_RATE):
            continue
        feature = mfcc(trim_silence(audio[lo:hi]))
        if feature.shape[0] < 8:
            continue
        for tpl in templates:
            n = tpl.feature.shape[0]
            if n > feature.shape[0]:
                continue
            for start in _coarse_starts(feature, tpl.feature, top_k):
                start_sample = lo + int(round(start * 0.01 * SAMPLE_RATE))
                end_sample = min(hi, start_sample + n * int(0.01 * SAMPLE_RATE))
                if end_sample - start_sample < int(0.2 * SAMPLE_RATE):
                    continue
                window = mfcc(trim_silence(audio[start_sample:end_sample]))
                if window.shape[0] < max(8, n // 2):
                    continue
                score = float(_dtw_subsequence(tpl.feature, window))
                if score >= threshold:
                    continue
                begin = start_sample / SAMPLE_RATE
                candidates.append(
                    PhraseMatch(
                        phrase_id=tpl.phrase_id,
                        text=tpl.text,
                        start=begin,
                        end=begin + n * 0.01,
                        score=score,
                        sample_index=tpl.sample_index,
                    )
                )

    return _resolve(candidates, margin)


def _resolve(candidates: List[PhraseMatch], margin: float) -> List[PhraseMatch]:
    """同一短语取最优样本；重叠命中保留更优者；歧义（次优太接近）丢弃。"""
    best: dict = {}
    for item in candidates:
        current = best.get(item.phrase_id)
        if current is None or item.score < current.score:
            best[item.phrase_id] = item
    ordered = sorted(best.values(), key=lambda item: item.score)
    resolved: List[PhraseMatch] = []
    for item in ordered:
        keep = True
        for other in ordered:
            if other is item:
                continue
            overlap = min(item.end, other.end) - max(item.start, other.start)
            if overlap <= 0:
                continue
            shorter = min(item.end - item.start, other.end - other.start)
            if shorter <= 0 or overlap / shorter < 0.5:
                continue
            if other.score <= item.score:
                keep = False
                break
            if other.score < item.score * margin:
                keep = False
                break
        if keep:
            resolved.append(item)
    return resolved
