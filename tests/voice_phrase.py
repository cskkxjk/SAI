"""语音短语替换：特征、DTW、匹配与存储（合成音频，临时目录）。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.client.voice_phrase.features import FEATURE_VERSION, N_CEPS, SAMPLE_RATE, mfcc
from core.client.voice_phrase.matcher import (
    PhraseTemplate,
    dtw_distance,
    match_phrases,
    segment_by_energy,
)
from core.client.voice_phrase.storage import VoicePhraseStore


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
        self.assertEqual(feature.shape[1], 2 * (N_CEPS - 1))
        self.assertGreater(feature.shape[0], 50)
        self.assertTrue(np.isfinite(feature).all())
        self.assertEqual(mfcc(_chirp(300, 900, 0.005)).shape[0], 0)


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
        self.assertLess(matches[0].score, 0.45)
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
                feature=np.zeros((2, 2 * (N_CEPS - 1)), dtype=np.float32),
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


if __name__ == "__main__":
    unittest.main()
