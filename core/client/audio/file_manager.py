# coding: utf-8
"""
音频文件管理模块

提供 AudioFileManager 类用于管理音频文件的创建、写入、完成和重命名。
支持 MP3（需要 FFmpeg）和 WAV 两种格式。
"""

from __future__ import annotations

import re
import shutil
import os
import subprocess
import tempfile
import time
import wave
from os import makedirs
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen
from typing import Optional, Tuple, Union

import numpy as np

from config_client import ClientConfig as Config
from . import logger


# 音频文件句柄类型
AudioWriter = Union[Popen, wave.Wave_write]

# 录音归档目录下会被清理的文件后缀
AUDIO_SUFFIXES = ('.mp3', '.wav')


def audio_folder(root: Union[Path, str]) -> Path:
    """
    返回录音文件夹（最近一个 年/月/assets 目录，没有则返回数据目录）

    Args:
        root: 数据目录

    Returns:
        录音文件夹路径
    """
    base = Path(root)
    folders = sorted(folder for folder in base.glob('????/??/assets')
                     if folder.is_dir())
    return folders[-1] if folders else base


def cleanup_old_recordings(root: Union[Path, str], keep_days: Union[int, float],
                           now: Optional[float] = None) -> list:
    """
    清理超过保留期的录音文件（只删音频，日记等其它文件不动）

    Args:
        root: 数据目录
        keep_days: 保留最近多少天，0 或负数表示永久保留
        now: 当前时间戳，默认取系统时间

    Returns:
        被删除的文件路径列表
    """
    try:
        keep_days = float(keep_days)
    except (TypeError, ValueError):
        return []
    if keep_days <= 0:
        return []

    base = Path(root)
    deadline = (time.time() if now is None else float(now)) - keep_days * 86400
    removed = []
    for folder in sorted(folder for folder in base.glob('????/??/assets')
                         if folder.is_dir()):
        for file in sorted(folder.iterdir()):
            if not file.is_file() or file.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            try:
                if file.stat().st_mtime < deadline:
                    file.unlink()
                    removed.append(file)
            except OSError as exc:
                logger.warning(f"清理录音失败: {file} ({exc})")
    return removed


class AudioFileManager:
    """
    音频文件管理器
    
    负责音频文件的完整生命周期管理：
    - 创建：根据是否安装 FFmpeg 选择 MP3 或 WAV 格式
    - 写入：将音频数据写入文件
    - 完成：关闭文件句柄
    - 重命名：根据识别文本重命名文件
    """
    
    SAMPLE_RATE = 48000
    
    def __init__(self):
        """初始化音频文件管理器"""
        self.file_path: Optional[Path] = None
        self.file_handle: Optional[AudioWriter] = None
        self.channels: int = 1
        self._has_ffmpeg = shutil.which('ffmpeg') is not None
        
        if self._has_ffmpeg:
            logger.debug("检测到 FFmpeg，将使用 MP3 格式保存录音")
        else:
            logger.debug("未检测到 FFmpeg，将使用 WAV 格式保存录音")
    
    def create(self, channels: int, time_start: float) -> Tuple[Path, AudioWriter]:
        """
        创建音频文件
        
        Args:
            channels: 音频声道数
            time_start: 录音开始时间戳
            
        Returns:
            (文件路径, 文件写入句柄) 元组
        """
        self.channels = channels
        
        # 构建目录和文件名
        local_time = time.localtime(time_start)
        time_year = time.strftime('%Y', local_time)
        time_month = time.strftime('%m', local_time)
        time_ymdhms = time.strftime("%Y%m%d-%H%M%S", local_time)
        
        folder_path = Path() / time_year / time_month / 'assets'
        makedirs(folder_path, exist_ok=True)
        
        # 创建临时文件名
        file_path = tempfile.mktemp(prefix=f'({time_ymdhms})', dir=folder_path)
        file_path = Path(file_path)
        
        if self._has_ffmpeg:
            # 使用 FFmpeg 输出 MP3
            file_path = file_path.with_suffix('.mp3')
            ffmpeg_command = [
                'ffmpeg', '-y',
                '-f', 'f32le',
                '-ar', str(self.SAMPLE_RATE),
                '-ac', str(channels),
                '-i', '-',
                '-b:a', '192k',
                str(file_path),
            ]
            creationflags = (
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            file_handle = Popen(
                ffmpeg_command, stdin=PIPE, stdout=DEVNULL, stderr=DEVNULL,
                creationflags=creationflags,
            )
            logger.debug(f"创建 MP3 文件: {file_path}")
        else:
            # 使用 wave 模块输出 WAV
            file_path = file_path.with_suffix('.wav')
            file_handle = wave.open(str(file_path), 'w')
            file_handle.setnchannels(channels)
            file_handle.setsampwidth(2)  # 16-bit
            file_handle.setframerate(self.SAMPLE_RATE)
            logger.debug(f"创建 WAV 文件: {file_path}")
        
        self.file_path = file_path
        self.file_handle = file_handle
        
        return file_path, file_handle
    
    def write(self, data: np.ndarray) -> None:
        """
        写入音频数据
        
        Args:
            data: 音频数据数组（float32 格式）
        """
        if self.file_handle is None:
            logger.warning("尝试写入数据但文件未打开")
            return
        
        if isinstance(self.file_handle, Popen):
            # FFmpeg 进程
            self.file_handle.stdin.write(data.tobytes())
            self.file_handle.stdin.flush()
        elif isinstance(self.file_handle, wave.Wave_write):
            # WAV 文件：转换 float32 -> int16
            int_data = (data * (2**15 - 1)).astype(np.int16).tobytes()
            self.file_handle.writeframes(int_data)
    
    def finish(self) -> Optional[Path]:
        """
        完成音频文件写入
        
        Returns:
            音频文件路径
        """
        if self.file_handle is None:
            return self.file_path
        
        try:
            if isinstance(self.file_handle, Popen):
                self.file_handle.stdin.close()
                logger.debug("FFmpeg 进程已关闭")
            elif isinstance(self.file_handle, wave.Wave_write):
                self.file_handle.close()
                logger.debug("WAV 文件已关闭")
        except Exception as e:
            logger.error(f"关闭音频文件时发生错误: {e}")
        finally:
            self.file_handle = None
        
        return self.file_path
    
    def rename(self, text: str, time_start: float) -> Optional[Path]:
        """
        根据识别文本重命名音频文件
        
        Args:
            text: 识别出的文本
            time_start: 录音开始时间戳
            
        Returns:
            重命名后的文件路径，如果失败返回 None
        """
        if self.file_path is None or not self.file_path.exists():
            logger.warning(f"文件不存在，无法重命名: {self.file_path}")
            return None
        
        # 构建新文件名
        time_ymdhms = time.strftime("%Y%m%d-%H%M%S", time.localtime(time_start))
        
        # 截取文本并清理非法字符
        text_clean = text[:Config.audio_name_len]
        text_clean = re.sub(r'[\\/:\"*?<>|]', ' ', text_clean)
        
        file_stem = f'({time_ymdhms}){text_clean}'
        new_path = self.file_path.with_name(file_stem + self.file_path.suffix)
        
        try:
            self.file_path.rename(new_path)
            logger.debug(f"音频文件已重命名: {self.file_path.name} -> {new_path.name}")
            self.file_path = new_path
            return new_path
        except Exception as e:
            logger.error(f"重命名音频文件失败: {e}")
            return self.file_path
