# -*- coding: utf-8 -*-
"""语音短语特征提取。

把 16k 单声道音频转成 MFCC 特征序列（去掉 c0 的 12 维倒谱 + 一阶差分，共 24 维）。
去掉 c0 后对音量/增益不敏感，且特征逐帧独立、不受前后文影响——这正是滑窗匹配
所依赖的稳定性。特征带版本号，版本变化时调用方应重算缓存的模板特征，这也是将来
更换特征/embedding 后端的迁移点。
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 25
HOP_MS = 10
N_FFT = 512
N_MELS = 40
N_CEPS = 13
FEATURE_VERSION = 1

_FRAME_LEN = int(SAMPLE_RATE * FRAME_MS / 1000)
_HOP_LEN = int(SAMPLE_RATE * HOP_MS / 1000)
_WINDOW = np.hamming(_FRAME_LEN).astype(np.float32)


def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank() -> np.ndarray:
    edges = _mel_to_hz(np.linspace(_hz_to_mel(20.0), _hz_to_mel(SAMPLE_RATE / 2.0), N_MELS + 2))
    bins = np.clip(np.floor((N_FFT + 1) * edges / SAMPLE_RATE).astype(int), 0, N_FFT // 2)
    bank = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for i in range(N_MELS):
        left, center, right = int(bins[i]), int(bins[i + 1]), int(bins[i + 2])
        center = max(center, left + 1)
        right = max(right, center + 1)
        for j in range(left, min(center, bank.shape[1])):
            bank[i, j] = (j - left) / (center - left)
        for j in range(center, min(right, bank.shape[1])):
            bank[i, j] = (right - j) / (right - center)
    return bank


def _dct_matrix() -> np.ndarray:
    n = np.arange(N_MELS)
    k = np.arange(N_CEPS)[:, None]
    matrix = (np.cos(np.pi * k * (2 * n + 1) / (2 * N_MELS)) * np.sqrt(2.0 / N_MELS)).astype(np.float32)
    matrix[0] *= np.sqrt(0.5)
    return matrix


_MEL_BANK = _mel_filterbank()
_DCT = _dct_matrix()


def trim_silence(audio: np.ndarray, relative: float = 0.08, margin_ms: int = 30) -> np.ndarray:
    """裁掉首尾静音（保留一点余量），避免静音污染 CMN 与 DTW。"""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = _FRAME_LEN
    hop = _HOP_LEN
    if audio.size < frame:
        return audio
    n = 1 + (audio.size - frame) // hop
    indices = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt((audio[indices] ** 2).mean(axis=1) + 1e-12)
    threshold = max(float(rms.max()) * relative, 0.005)
    active = rms > threshold
    if not active.any():
        return audio
    first = int(np.argmax(active))
    last = n - 1 - int(np.argmax(active[::-1]))
    margin = int(SAMPLE_RATE * margin_ms / 1000)
    start = max(0, first * hop - margin)
    end = min(audio.size, last * hop + frame + margin)
    return audio[start:end]


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    """float32 单声道 + 峰值归一化；静音原样返回。"""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if not audio.size:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-4:
        audio = audio * (0.7 / peak)
    return audio


def mfcc(audio: np.ndarray) -> np.ndarray:
    """返回 (T, 2*(N_CEPS-1)) 的特征序列；音频过短时返回空数组。"""
    audio = normalize_audio(audio)
    if audio.size < _FRAME_LEN // 2:
        return np.zeros((0, N_CEPS * 2), dtype=np.float32)
    if audio.size < _FRAME_LEN:
        audio = np.pad(audio, (0, _FRAME_LEN - audio.size))
    audio = np.concatenate([[audio[0]], audio[1:] - 0.97 * audio[:-1]])
    n_frames = 1 + (audio.size - _FRAME_LEN) // _HOP_LEN
    indices = np.arange(_FRAME_LEN)[None, :] + _HOP_LEN * np.arange(n_frames)[:, None]
    frames = audio[indices] * _WINDOW[None, :]
    power = np.abs(np.fft.rfft(frames, n=N_FFT)) ** 2
    log_mel = np.log(power @ _MEL_BANK.T + 1e-10)
    ceps = (log_mel @ _DCT.T)[:, 1:]
    delta = np.diff(ceps, axis=0, prepend=ceps[:1, :])
    return np.concatenate([ceps, delta], axis=1).astype(np.float32)


def feature_matches_backend(version) -> bool:
    """特征缓存是否与当前特征后端兼容。"""
    try:
        return int(version) == FEATURE_VERSION
    except (TypeError, ValueError):
        return False
