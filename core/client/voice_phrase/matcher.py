# -*- coding: utf-8 -*-
"""语音短语匹配。

流程：语音区间 -> 分段均值余弦粗筛（滑窗取前 K 个候选）-> 带宽 DTW 精筛 ->
阈值 + 次优裕度判定 -> 输出命中的音频时间段。numba 缺失时自动退化为纯 Python。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from core.client.voice_phrase.features import (
    SAMPLE_RATE,
    feature_span_samples,
    mfcc,
    resample_feature,
    speech_bounds,
)

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

DEFAULT_THRESHOLD = 8.0
DEFAULT_MARGIN = 1.08
SEGMENT_COUNT = 4
COARSE_TOP_K = 5
COARSE_HOP_SEC = 0.05
SPAN_PAD_SEC = 0.3
RATE_PAD_FRAMES = 35


@dataclass
class PhraseTemplate:
    """一条短语的一份录音样本。"""

    phrase_id: str
    text: str
    feature: np.ndarray
    sample_index: int = 1


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
def _dtw_subsequence_end(template: np.ndarray, window: np.ndarray):
    """模板在窗口内的子序列 DTW（起点终点自由），返回 (归一化距离, 结束帧号)。"""
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
    best_j = 1
    for j in range(2, m + 1):
        if prev[j] < best_end:
            best_end = prev[j]
            best_j = j
    return float(best_end) / n, best_j


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

    if not spans and float(rms.max()) > 0.02:
        # 整段都是连续语音（没有静音间隔）时，按整段切分，避免漏掉匹配
        duration = audio.size / SAMPLE_RATE
        pieces = max(1, int(np.ceil(duration / max_speech)))
        step = duration / pieces
        spans = [(k * step, (k + 1) * step) for k in range(pieces)]
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
    candidates = score_candidates(audio, templates, spans=spans, top_k=top_k)
    return _resolve([item for item in candidates if item.score < threshold], margin)


def best_candidates(
    audio: np.ndarray,
    templates: Sequence[PhraseTemplate],
    spans: Optional[Sequence[Tuple[float, float]]] = None,
    top_k: int = COARSE_TOP_K,
) -> List[PhraseMatch]:
    """每种文字的最优候选（不套阈值），供测试页展示阈值对比。"""
    best: dict = {}
    for item in score_candidates(audio, templates, spans=spans, top_k=top_k):
        key = item.text.strip().casefold()
        current = best.get(key)
        if current is None or item.score < current.score:
            best[key] = item
    return sorted(best.values(), key=lambda item: item.score)


def score_candidates(
    audio: np.ndarray,
    templates: Sequence[PhraseTemplate],
    spans: Optional[Sequence[Tuple[float, float]]] = None,
    top_k: int = COARSE_TOP_K,
) -> List[PhraseMatch]:
    """对每个语音区间与模板打分，返回全部候选（未按阈值过滤）。"""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    templates = [t for t in templates if t.feature is not None and t.feature.shape[0] >= 8]
    if not audio.size or not templates:
        return []
    if spans is None:
        spans = segment_by_energy(audio)

    candidates: List[PhraseMatch] = []
    pad = int(SPAN_PAD_SEC * SAMPLE_RATE)
    for span_start, span_end in spans:
        # 能量门限会削掉起止的低能量音节，向两侧放宽，避免模板比窗口还长
        lo = max(0, int(span_start * SAMPLE_RATE) - pad)
        hi = min(audio.size, int(span_end * SAMPLE_RATE) + pad)
        if hi - lo < int(0.2 * SAMPLE_RATE):
            continue
        base = audio[lo:hi]
        # 特征从裁剪后的语音算起，帧号要加上裁剪偏移才能映射回采样点
        trim_start, trim_end = speech_bounds(base)
        feature = mfcc(base[trim_start:trim_end])
        if feature.shape[0] < 8:
            continue
        for tpl in templates:
            n = tpl.feature.shape[0]
            if n > feature.shape[0]:
                # 说得比样本快：把模板压缩到等长再整段对齐
                tpl_feature = resample_feature(tpl.feature, feature.shape[0])
                score, end = _dtw_subsequence_end(tpl_feature, feature)
                candidates.append(_make_match(
                    tpl, score, lo + trim_start, feature, end,
                    tpl_feature.shape[0]))
                continue
            for start in _coarse_starts(feature, tpl.feature, top_k):
                win_lo = max(0, start - RATE_PAD_FRAMES)
                win_hi = min(feature.shape[0], start + n + RATE_PAD_FRAMES)
                # 起点范围（start + n <= total）与前后 pad 保证窗口不短于模板，
                # 无需再做压缩模板的等长对齐
                window = feature[win_lo:win_hi]
                score, end = _dtw_subsequence_end(tpl.feature, window)
                candidates.append(_make_match(
                    tpl, score, lo + trim_start, window, end,
                    n, frame_offset=win_lo))
    return candidates


def _make_match(tpl: PhraseTemplate, score: float, base_sample: int,
                feature: np.ndarray, end_frame: int, frames: int,
                frame_offset: int = 0) -> PhraseMatch:
    """把 DTW 结果换算成音频时间段（秒）。"""
    end_sample = base_sample + feature_span_samples(frame_offset + end_frame)
    begin_sample = max(base_sample,
                       end_sample - feature_span_samples(frames))
    begin = begin_sample / SAMPLE_RATE
    return PhraseMatch(
        phrase_id=tpl.phrase_id,
        text=tpl.text,
        start=begin,
        end=end_sample / SAMPLE_RATE,
        score=float(score),
        sample_index=tpl.sample_index,
    )


def _resolve(candidates: List[PhraseMatch], margin: float) -> List[PhraseMatch]:
    """同一文字取最优样本；重叠命中保留更优者；歧义（次优太接近）丢弃。"""
    best: dict = {}
    for item in candidates:
        key = item.text.strip().casefold()
        current = best.get(key)
        if current is None or item.score < current.score:
            best[key] = item
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
