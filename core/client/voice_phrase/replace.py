# -*- coding: utf-8 -*-
"""语音短语替换：把音频命中区间映射到识别文本并替换。

匹配得到的是音频时间区间，需要借助服务端返回的 token 时间戳
定位到识别文本中的字符区间；token 与文本对不上时（缺 token、
文本被改写等）跳过该条替换。

token/timestamps 由服务端对齐在 text_accu 上，而客户端输出用的是
text（简单拼接）。两者不一致时（重叠分段、模糊合并等）先在
text_accu 上定位 token，再用 difflib 映射回 text，避免定位到错误的
重复词位置。
"""

from __future__ import annotations

import difflib
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


def _map_index(index: int, opcodes, target_len: int) -> int:
    """把 source 文本中的字符下标映射到 target 文本（difflib opcodes）。"""
    for tag, i1, i2, j1, j2 in opcodes:
        if i1 <= index < i2:
            if tag == "delete":
                return j1
            if tag == "equal" or (i2 - i1) == (j2 - j1):
                return j1 + (index - i1)
            ratio = (index - i1) / (i2 - i1)
            return min(j2, j1 + int(round(ratio * (j2 - j1))))
    return target_len


def _map_spans(
    spans: Sequence[Optional[Tuple[int, int]]],
    opcodes,
    target_len: int,
) -> List[Optional[Tuple[int, int]]]:
    """把 source 上的字符区间映射到 target；映射后为空的区间返回 None。"""
    mapped: List[Optional[Tuple[int, int]]] = []
    for span in spans:
        if span is None:
            mapped.append(None)
            continue
        start = _map_index(span[0], opcodes, target_len)
        end = _map_index(span[1], opcodes, target_len)
        mapped.append((start, end) if end > start else None)
    return mapped


def _locate_in_source(
    tokens: Sequence[str],
    source: str,
    target: str,
    hint: Optional[int],
) -> List[Optional[Tuple[int, int]]]:
    """在 token 所属文本 source 上定位；source != target 时映射回 target。"""
    positions = _locate_tokens(tokens, source, hint)
    if source == target:
        return positions
    opcodes = difflib.SequenceMatcher(None, source, target, autojunk=False).get_opcodes()
    return _map_spans(positions, opcodes, len(target))


def _bounds(
    positions: Sequence[Optional[Tuple[int, int]]],
    indices: Sequence[int],
) -> Optional[Tuple[int, int]]:
    """取命中 token 在文本中的整体区间（首 token 起、末 token 止）。"""
    first = next((positions[index] for index in indices if positions[index]), None)
    last = next((positions[index] for index in reversed(indices)
                 if positions[index]), None)
    if not first or not last or first[0] >= last[1]:
        return None
    return first[0], last[1]


def apply_replacements(
    text: str,
    tokens: Sequence[str],
    timestamps: Sequence[float],
    matches: Sequence[PhraseMatch],
    tolerance: float = DEFAULT_TOLERANCE,
    accu_text: str = "",
) -> Tuple[str, List[PhraseMatch]]:
    """按音频命中把 text 中对应区间替换为短语标注文字。

    Args:
        text: 实际输出/替换用的文本（服务端简单拼接结果）
        tokens/timestamps: 服务端 token 及其起始时间（对齐在 accu_text 上）
        matches: 音频层命中的短语区间
        tolerance: token 区间匹配的容差（秒）
        accu_text: token 所属的精确文本（text_accu），空则退回 text

    Returns:
        (替换后的文本, 实际生效的命中列表)
    """
    if not text or not matches:
        return text, []
    if not tokens or not timestamps or len(tokens) != len(timestamps):
        return text, []

    base_text = accu_text or text
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
            hint = max(0, int(len(base_text) * ratio) - 8)
        positions = _locate_in_source(tokens, base_text, text, hint)
        bounds = _bounds(positions, indices)
        if bounds is None and base_text != text:
            # accu 对齐映射失败时退回直接在 text 上定位（与旧行为一致）
            positions = _locate_tokens(tokens, text, hint)
            bounds = _bounds(positions, indices)
        if bounds is None:
            continue
        candidates.append((bounds[0], bounds[1], match))

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
