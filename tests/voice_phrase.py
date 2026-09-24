"""语音短语替换：特征、DTW、匹配与存储（合成音频，临时目录）。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from config_client import ClientConfig as Config
from core.client.voice_phrase.features import FEATURE_VERSION, N_CEPS, SAMPLE_RATE, mfcc
from core.client.voice_phrase.manager import VoicePhraseManager
from core.client.voice_phrase.matcher import (
    DEFAULT_THRESHOLD,
    PhraseMatch,
    PhraseTemplate,
    best_candidates,
    dtw_distance,
    match_phrases,
    segment_by_energy,
)
from core.client.voice_phrase.replace import apply_replacements
from core.client.voice_phrase.storage import MAX_SAMPLES, VoicePhraseStore


def _chirp(f0, f1, duration, sr=SAMPLE_RATE):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    freq = np.linspace(f0, f1, t.size)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    return (0.6 * np.sin(phase)).astype(np.float32)


def _tone(freq, duration, sr=SAMPLE_RATE):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (0.6 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(duration, sr=SAMPLE_RATE):
    return np.zeros(int(sr * duration), dtype=np.float32)


def _template(text, audio, phrase_id="p1", sample_index=1):
    return PhraseTemplate(phrase_id=phrase_id, text=text, feature=mfcc(audio), sample_index=sample_index)


class FeatureTests(unittest.TestCase):
    def test_mfcc_shape_and_short_audio(self):
        feature = mfcc(_chirp(300, 900, 1.0))
        self.assertEqual(feature.shape[1], N_CEPS - 1)
        self.assertGreater(feature.shape[0], 50)
        self.assertTrue(np.isfinite(feature).all())
        self.assertEqual(mfcc(_chirp(300, 900, 0.005)).shape[0], 0)

    def test_mfcc_applies_cmn(self):
        """逐句均值归一化：整体增益/信道偏移被抵消，各维时间均值为 0。"""
        feature = mfcc(_chirp(300, 900, 1.0))
        self.assertLess(float(np.abs(feature.mean(axis=0)).max()), 1e-4)
        quiet = mfcc(_chirp(300, 900, 1.0) * 0.2)
        self.assertLess(float(np.abs(feature - quiet).max()), 1e-3)


class DtwTests(unittest.TestCase):
    def test_self_distance_is_small_and_other_is_large(self):
        a = mfcc(_chirp(300, 900, 1.0))
        b = mfcc(_chirp(300, 900, 1.0) * 0.5)
        c = mfcc(_tone(440, 1.0))
        same = dtw_distance(a, b)
        other = dtw_distance(a, c)
        self.assertLess(same, 0.3)
        self.assertGreater(other, same * 2)


class SegmentationTests(unittest.TestCase):
    def test_energy_segmentation_finds_two_bursts(self):
        audio = np.concatenate(
            [_silence(0.5), _chirp(300, 900, 0.8), _silence(0.4), _tone(440, 0.6), _silence(0.3)]
        )
        spans = segment_by_energy(audio)
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0][0], 0.5, delta=0.15)
        self.assertAlmostEqual(spans[1][0], 1.7, delta=0.15)


class MatchTests(unittest.TestCase):
    def test_match_found_with_energy_segmentation(self):
        phrase = _chirp(300, 900, 1.0)
        audio = np.concatenate([_silence(0.6), phrase, _silence(0.4)])
        matches = match_phrases(audio, [_template("打开帮助文档", phrase)])
        self.assertEqual(len(matches), 1)
        self.assertAlmostEqual(matches[0].start, 0.6, delta=0.2)
        self.assertLess(matches[0].score, DEFAULT_THRESHOLD / 2)
        self.assertEqual(matches[0].text, "打开帮助文档")

    def test_match_found_with_explicit_spans(self):
        phrase = _chirp(400, 1200, 0.8)
        audio = np.concatenate([_silence(0.4), phrase, _silence(0.5)])
        matches = match_phrases(audio, [_template("你好世界", phrase)], spans=[(0.35, 1.3)])
        self.assertEqual(len(matches), 1)
        self.assertAlmostEqual(matches[0].start, 0.4, delta=0.2)

    def test_unrelated_audio_is_rejected(self):
        phrase = _chirp(300, 900, 1.0)
        audio = np.concatenate([_silence(0.3), _tone(440, 1.5), _silence(0.3)])
        matches = match_phrases(audio, [_template("打开帮助文档", phrase)])
        self.assertEqual(matches, [])

    def test_identical_templates_are_ambiguous_and_dropped(self):
        phrase = _chirp(300, 900, 1.0)
        audio = np.concatenate([_silence(0.5), phrase, _silence(0.3)])
        templates = [
            _template("方案一", phrase, phrase_id="p1"),
            _template("方案二", phrase, phrase_id="p2"),
        ]
        self.assertEqual(match_phrases(audio, templates), [])

    def test_identical_templates_with_the_same_text_keep_one(self):
        phrase = _chirp(300, 900, 1.0)
        audio = np.concatenate([_silence(0.5), phrase, _silence(0.3)])
        templates = [
            _template("打开帮助文档", phrase, phrase_id="p1", sample_index=1),
            _template("打开帮助文档", phrase, phrase_id="p2", sample_index=1),
        ]
        matches = match_phrases(audio, templates)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].text, "打开帮助文档")
        self.assertEqual(len(best_candidates(audio, templates)), 1)

    def test_best_candidates_reports_the_closest_above_threshold(self):
        phrase = _chirp(300, 900, 1.0)
        audio = np.concatenate([_silence(0.5), _chirp(1500, 3000, 1.0), _silence(0.3)])
        templates = [_template("打开帮助文档", phrase)]
        self.assertEqual(match_phrases(audio, templates, threshold=0.05), [])
        best = best_candidates(audio, templates)
        self.assertEqual([item.text for item in best], ["打开帮助文档"])
        self.assertGreater(best[0].score, 0.05)
        self.assertTrue(np.isfinite(best[0].score))


class StoreTests(unittest.TestCase):
    def test_store_roundtrip_and_feature_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            phrase = _chirp(300, 900, 0.9)
            item = store.add("打开帮助文档", [phrase, phrase])
            self.assertTrue(store.index_path.exists())
            self.assertEqual(len(item["samples"]), 2)
            self.assertEqual(len(store.load()), 1)

            templates = store.templates()
            self.assertEqual(len(templates), 2)
            self.assertTrue(all(t.text == "打开帮助文档" for t in templates))
            self.assertTrue(all(t.feature.shape[0] > 10 for t in templates))
            for name in item["samples"]:
                self.assertTrue(store._feature_path(name).exists())

            self.assertTrue(store.remove(item["id"]))
            self.assertEqual(store.load(), [])
            for name in item["samples"]:
                self.assertFalse((store.base_dir / name).exists())
                self.assertFalse(store._feature_path(name).exists())

    def test_stale_feature_cache_is_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            phrase = _chirp(300, 900, 0.9)
            item = store.add("测试短语", [phrase])
            name = item["samples"][0]
            np.savez_compressed(
                store._feature_path(name),
                version=FEATURE_VERSION + 99,
                feature=np.zeros((2, N_CEPS - 1), dtype=np.float32),
            )
            templates = store.templates()
            self.assertEqual(len(templates), 1)
            self.assertGreater(templates[0].feature.shape[0], 10)

    def test_add_rejects_empty_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            with self.assertRaises(ValueError):
                store.add("", [np.zeros(16000, dtype=np.float32)])
            with self.assertRaises(ValueError):
                store.add("文字", [])

    def test_add_merges_samples_for_the_same_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            phrase = _chirp(300, 900, 0.9)
            first = store.add("打开帮助文档", [phrase])
            second = store.add("  打开帮助文档 ", [phrase])
            self.assertEqual(second["id"], first["id"])
            self.assertEqual(len(store.load()), 1)
            self.assertEqual([Path(name).name for name in second["samples"]],
                             [f"{first['id']}.1.wav", f"{first['id']}.2.wav"])
            self.assertEqual(len(store.templates()), 2)

    def test_add_stops_merging_at_max_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            phrase = _chirp(300, 900, 0.9)
            for _ in range(MAX_SAMPLES + 1):
                store.add("打开帮助文档", [phrase])
            items = store.load()
            self.assertEqual(len(items), 2)
            self.assertEqual(len(items[0]["samples"]), MAX_SAMPLES)
            self.assertEqual(len(items[1]["samples"]), 1)
            self.assertNotEqual(items[0]["id"], items[1]["id"])

    def test_merge_duplicates_consolidates_existing_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            phrase = _chirp(300, 900, 0.9)
            first = store.add("打开帮助文档", [phrase], merge=False)
            second = store.add("打开帮助文档", [phrase], merge=False)
            store.add("另一个短语", [phrase], merge=False)
            self.assertEqual(len(store.load()), 3)
            self.assertEqual(store.merge_duplicates(), 1)
            items = store.load()
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["id"], first["id"])
            self.assertEqual(items[0]["samples"],
                             [first["samples"][0], second["samples"][0]])
            self.assertTrue(store.templates())
            self.assertEqual(store.merge_duplicates(), 0)

    def test_merge_duplicates_keeps_groups_that_are_too_large(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            phrase = _chirp(300, 900, 0.9)
            for _ in range(MAX_SAMPLES + 1):
                store.add("打开帮助文档", [phrase], merge=False)
            self.assertEqual(len(store.load()), MAX_SAMPLES + 1)
            self.assertEqual(store.merge_duplicates(), 0)
            self.assertEqual(len(store.load()), MAX_SAMPLES + 1)


class ReplaceTests(unittest.TestCase):
    def _match(self, text, start, end, score=0.2, phrase_id="p1"):
        return PhraseMatch(phrase_id=phrase_id, text=text, start=start, end=end, score=score)

    def test_replacement_uses_token_timestamps(self):
        tokens = list("帮我打开设置面板")
        timestamps = [i * 0.3 for i in range(len(tokens))]
        # “打开设置”是第 2~5 个 token（0.6s ~ 1.8s）
        new_text, applied = apply_replacements(
            "帮我打开设置面板", tokens, timestamps, [self._match("打开控制", 0.6, 1.75)]
        )
        self.assertEqual(new_text, "帮我打开控制面板")
        self.assertEqual([m.text for m in applied], ["打开控制"])

    def test_no_overlapping_tokens_skips(self):
        tokens = list("你好世界")
        timestamps = [i * 0.3 for i in range(len(tokens))]
        new_text, applied = apply_replacements(
            "你好世界", tokens, timestamps, [self._match("问候", 5.0, 6.0)]
        )
        self.assertEqual(new_text, "你好世界")
        self.assertEqual(applied, [])

    def test_missing_timestamps_skips(self):
        new_text, applied = apply_replacements(
            "你好世界", ["你好世界"], [], [self._match("问候", 0.0, 1.0)]
        )
        self.assertEqual((new_text, applied), ("你好世界", []))

    def test_multiple_replacements_apply_in_order(self):
        tokens = list("请打开设置和帮助")
        timestamps = [i * 0.3 for i in range(len(tokens))]
        matches = [
            self._match("设置页面", 0.9, 1.35, phrase_id="p1"),
            self._match("帮助文档", 1.8, 2.35, phrase_id="p2"),
        ]
        new_text, applied = apply_replacements("请打开设置和帮助", tokens, timestamps, matches)
        self.assertEqual(new_text, "请打开设置页面和帮助文档")
        self.assertEqual([m.text for m in applied], ["设置页面", "帮助文档"])

    def test_overlapping_replacements_keep_best_score(self):
        tokens = list("打开帮助")
        timestamps = [i * 0.3 for i in range(len(tokens))]
        matches = [
            self._match("差命中", 0.0, 0.65, score=0.4, phrase_id="p1"),
            self._match("好命中", 0.0, 0.35, score=0.15, phrase_id="p2"),
        ]
        new_text, applied = apply_replacements("打开帮助", tokens, timestamps, matches)
        self.assertEqual(new_text, "好命中帮助")
        self.assertEqual([m.text for m in applied], ["好命中"])


class ManagerTests(unittest.TestCase):
    def test_manager_reloads_and_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            phrase = _chirp(300, 900, 1.0)
            store.add("打开帮助文档", [phrase])
            manager = VoicePhraseManager(store=store)
            audio = np.concatenate([_silence(0.6), phrase, _silence(0.4)])
            with mock.patch.object(Config, "voice_phrase", True), mock.patch.object(
                Config, "voice_phrase_threshold", DEFAULT_THRESHOLD
            ):
                matches = manager.match(audio)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].text, "打开帮助文档")
                # 索引变化后自动重载
                store.add("第二个短语", [_chirp(500, 1500, 0.8)])
                self.assertEqual(len(manager.reload_if_changed()), 2)

    def test_manager_respects_disable_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VoicePhraseStore(Path(tmp) / "voice-phrases")
            manager = VoicePhraseManager(store=store)
            with mock.patch.object(Config, "voice_phrase", False):
                self.assertEqual(manager.match(np.zeros(16000, dtype=np.float32)), [])


if __name__ == "__main__":
    unittest.main()
