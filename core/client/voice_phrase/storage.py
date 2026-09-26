# -*- coding: utf-8 -*-
"""语音短语样本存储。

每条短语保存 1~3 份 16k 单声道 wav 录音样本（原始语音），MFCC 特征缓存为
同名 npz；特征版本不匹配时自动重算，因此更换特征/embedding 后端只需重算，
不必重新录音。索引文件为 voice-phrases.json。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf

from core import get_logger
from core.client.voice_phrase.features import (
    FEATURE_VERSION,
    SAMPLE_RATE,
    feature_matches_backend,
    mfcc,
    trim_silence,
)
from core.client.voice_phrase.matcher import PhraseTemplate

logger = get_logger('client')

INDEX_NAME = "voice-phrases.json"
INDEX_VERSION = 1
MAX_SAMPLES = 3


class VoicePhraseStore:
    """管理 DATA_DIR/voice-phrases 下的短语样本与索引。"""

    def __init__(self, base_dir=None):
        if base_dir is None:
            from core.runtime_paths import DATA_DIR

            base_dir = Path(DATA_DIR) / "voice-phrases"
        self.base_dir = Path(base_dir)
        self.index_path = self.base_dir / INDEX_NAME

    # ------------------------------------------------------------------ 索引
    def load(self) -> List[Dict]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"语音短语索引读取失败，按空索引处理: {exc}")
            return []
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict) and item.get("text")]

    def _write_index(self, items: List[Dict]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": INDEX_VERSION, "items": items}
        # 原子替换：图形界面与客户端（语音测试）可能并发读写，避免读到半截文件
        temp = self.index_path.with_name(INDEX_NAME + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        temp.replace(self.index_path)

    # ------------------------------------------------------------------ 增删
    def add(self, text: str, samples, created_at: Optional[str] = None,
            merge: bool = True) -> Dict:
        """保存录音样本；同文字的词条未满 MAX_SAMPLES 时追加，否则新建词条。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("短语文字不能为空")
        prepared = []
        for sample in samples or []:
            array = np.asarray(sample, dtype=np.float32)
            if array.ndim > 1:
                array = array.mean(axis=1)
            if array.size:
                prepared.append(array)
        if not prepared:
            raise ValueError("至少需要一份录音样本")
        items = self.load()
        target = self._find_by_text(items, text) if merge else None
        if target is not None:
            room = MAX_SAMPLES - len(target.get("samples") or [])
            prepared = prepared[:room]
        phrase_id = target["id"] if target is not None else uuid.uuid4().hex[:12]
        self.base_dir.mkdir(parents=True, exist_ok=True)
        files = list(target.get("samples") or []) if target is not None else []
        index = self._next_index(files)
        for offset, sample in enumerate(prepared):
            name = f"{phrase_id}.{index + offset}.wav"
            sf.write(str(self.base_dir / name), sample, SAMPLE_RATE, subtype="PCM_16")
            files.append(name)
        if target is not None:
            target["samples"] = files
            self._write_index(items)
            return target
        item = {
            "id": phrase_id,
            "text": text,
            "created_at": created_at or time.strftime("%Y-%m-%d %H:%M:%S"),
            "samples": files,
        }
        items.append(item)
        self._write_index(items)
        return item

    def find_by_text(self, text: str) -> Optional[Dict]:
        """返回文字相同且样本未满、可继续追加样本的词条。"""
        return self._find_by_text(self.load(), text)

    def merge_duplicates(self) -> int:
        """合并文字相同的词条（样本总数不超过 MAX_SAMPLES 时），返回减少的词条数。"""
        items = self.load()
        groups: Dict[str, List[Dict]] = {}
        order: List[str] = []
        for item in items:
            key = (item.get("text") or "").strip().casefold()
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(item)
        merged: List[Dict] = []
        removed = 0
        for key in order:
            group = groups[key]
            total = sum(len(item.get("samples") or []) for item in group)
            if len(group) == 1 or total > MAX_SAMPLES:
                merged.extend(group)
                continue
            keeper = dict(group[0])
            keeper["samples"] = [name for item in group for name in (item.get("samples") or [])]
            created = sorted(item.get("created_at") or "" for item in group)
            if created and created[0]:
                keeper["created_at"] = created[0]
            merged.append(keeper)
            removed += len(group) - 1
        if removed:
            self._write_index(merged)
        return removed

    def remove(self, phrase_id: str) -> bool:
        items = self.load()
        target = next((item for item in items if item.get("id") == phrase_id), None)
        if target is None:
            return False
        for name in target.get("samples", []):
            for path in (self.base_dir / name, self._feature_path(name)):
                try:
                    path.unlink()
                except OSError:
                    pass
        self._write_index([item for item in items if item.get("id") != phrase_id])
        return True

    def clear(self) -> None:
        for item in self.load():
            self.remove(item.get("id"))

    # ------------------------------------------------------------------ 模板
    def templates(self) -> List[PhraseTemplate]:
        result: List[PhraseTemplate] = []
        for item in self.load():
            for index, name in enumerate(item.get("samples", []), start=1):
                feature = self._sample_feature(name)
                if feature is None or feature.shape[0] < 8:
                    continue
                result.append(
                    PhraseTemplate(
                        phrase_id=item["id"],
                        text=item["text"],
                        feature=feature,
                        sample_index=index,
                    )
                )
        return result

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _find_by_text(items: List[Dict], text: str) -> Optional[Dict]:
        key = text.strip().casefold()
        for item in items:
            if (item.get("text") or "").strip().casefold() != key:
                continue
            if len(item.get("samples") or []) < MAX_SAMPLES:
                return item
        return None

    @staticmethod
    def _next_index(samples) -> int:
        index = 0
        for name in samples or []:
            try:
                index = max(index, int(Path(name).stem.rsplit(".", 1)[-1]))
            except (TypeError, ValueError):
                continue
        return index + 1

    def _feature_path(self, sample_name: str) -> Path:
        return self.base_dir / (Path(sample_name).stem + ".feat.npz")

    def _sample_feature(self, sample_name: str) -> Optional[np.ndarray]:
        path = self.base_dir / sample_name
        if not path.exists():
            return None
        cache = self._feature_path(sample_name)
        if cache.exists():
            try:
                with np.load(cache) as data:
                    if feature_matches_backend(data["version"]):
                        return data["feature"].astype(np.float32)
            except (OSError, ValueError, KeyError):
                pass
        try:
            audio, _ = sf.read(str(path), dtype="float32", always_2d=False)
        except (OSError, RuntimeError):
            return None
        feature = mfcc(trim_silence(audio))
        if feature.shape[0]:
            try:
                np.savez_compressed(cache, version=FEATURE_VERSION, feature=feature)
            except OSError:
                pass
        return feature
