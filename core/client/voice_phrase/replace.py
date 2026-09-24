# -*- coding: utf-8 -*-
"""语音短语替换：把音频命中区间映射到识别文本并替换。

匹配得到的是音频时间区间，需要借助服务端返回的 token 时间戳
定位到识别文本中的字符区间；token 与文本对不上时（缺 token、
文本被改写等）跳过该条替换。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from core.client.voice_phrase.matcher import PhraseMatch

DEFAULT_TOLERANCE = 0.15


def _token_spans(timestamps: Sequence[float]) -> List[Tuple[float, float]]:
    """由 token 起始时间构造区间（下一个 token 起点为终点）。"""
    spans: List[Tuple[float, float]] = []
    total = len(timestamps)
    for index, start in enumerate(timestamps):
        end = timestamps[index + 1] if index + 1 < total else float(start) + 0.1
        if end <= start:
            end = float(start) + 0.01
        spans.append((float(start), float(end)))
    return spans


def _select_indices(
    token_spans: Sequence[Tuple[float, float]],
    match: PhraseMatch,
    tolerance: float,
) -> List[int]:
    """选出与命中区间对应的时间 token 下标。"""
    strict = [
        index
        for index, (start, end) in enumerate(token_spans)
        if end > match.start and start < match.end
    ]
    if strict:
        return strict
    return [
        index
        for index, (start, end) in enumerate(token_spans)
        if end > match.start - tolerance and start < match.end + tolerance
    ]


def _locate_tokens(
    tokens: Sequence[str],
    text: str,
    hint: Optional[int] = None,
) -> List[Optional[Tuple[int, int]]]:
    """把每个 token 顺序定位到 text，返回字符区间（找不到为 None）。"""
    positions: List[Optional[Tuple[int, int]]] = []
    cursor = max(0, hint or 0)
    for token in tokens:
        if not token:
            positions.append(None)
            continue
        pos = text.find(token, cursor)
        if pos < 0 and cursor:
            pos = text.find(token)
        if pos < 0:
            positions.append(None)
            continue
        positions.append((pos, pos + len(token)))
        cursor = pos + len(token)
    return positions


def apply_replacements(
    text: str,
    tokens: Sequence[str],
    timestamps: Sequence[float],
    matches: Sequence[PhraseMatch],
    tolerance: float = DEFAULT_TOLERANCE,
) -> Tuple[str, List[PhraseMatch]]:
    """按音频命中把 text 中对应区间替换为短语标注文字。

    Returns:
        (替换后的文本, 实际生效的命中列表)
    """
    if not text or not matches:
        return text, []
    if not tokens or not timestamps or len(tokens) != len(timestamps):
        return text, []

    token_spans = _token_spans(timestamps)
    total = float(timestamps[-1])
    candidates: List[Tuple[int, int, PhraseMatch]] = []
    for match in matches:
        indices = _select_indices(token_spans, match, tolerance)
        if not indices:
            continue
        # 先按时间比例估计文本位置，再顺序查找 token
        hint = None
        if total > 0:
            ratio = min(1.0, max(0.0, match.start / total))
            hint = max(0, int(len(text) * ratio) - 8)
        positions = _locate_tokens(tokens, text, hint)
        first = next((positions[i] for i in indices if positions[i]), None)
        last = next((positions[i] for i in reversed(indices) if positions[i]), None)
        if not first or not last or first[0] >= last[1]:
            continue
        candidates.append((first[0], last[1], match))

    if not candidates:
        return text, []

    # 文本区间重叠时保留分数更优的命中
    candidates.sort(key=lambda item: item[2].score)
    chosen: List[Tuple[int, int, PhraseMatch]] = []
    for start, end, match in candidates:
        if any(not (end <= s or start >= e) for s, e, _ in chosen):
            continue
        chosen.append((start, end, match))

    chosen.sort(key=lambda item: item[0], reverse=True)
    result = text
    applied: List[PhraseMatch] = []
    for start, end, match in chosen:
        result = result[:start] + match.text + result[end:]
        applied.append(match)
    applied.reverse()
    return result, applied
