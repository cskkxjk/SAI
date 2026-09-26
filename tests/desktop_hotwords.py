"""Desktop hotword editing tests, using temporary files only."""

import json
import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from core.desktop_hotwords import HotwordEditor, parse_rules
from core.desktop_voice_phrases import (VoiceCapture, VoicePhrasePanel,
                                        play_audio)
from core.client.voice_phrase.storage import MAX_SAMPLES, VoicePhraseStore
from core.hotword_rules import encode_literal
from core.client.hotword.hot_rule import RuleCorrector
from core.client.hotword.manager import HotwordManager


def _chirp(f0, f1, duration, sr=16000):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    freq = np.linspace(f0, f1, t.size)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    return (0.6 * np.sin(phase)).astype(np.float32)


def _silence(duration, sr=16000):
    return np.zeros(int(sr * duration), dtype=np.float32)


class RuleValidationTests(unittest.TestCase):
    def test_existing_rules_match_client_engine(self):
        text = (Path(__file__).resolve().parents[1] / "hot-rule.txt").read_text(encoding="utf-8")
        corrector = RuleCorrector()
        corrector.update_rules(text)
        for sample in ("欧拉玛", "5000毫安时", "50赫兹", "回车", "/sil"):
            result = sample
            for pattern, replacement in parse_rules(text):
                result = pattern.sub(replacement, result)
            self.assertEqual(result, corrector.substitute(sample))

    def test_invalid_rules_report_line_number(self):
        for rule in ("missing separator", "[ = word", "(a) = \\2", " = word"):
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, "第 2 行"):
                parse_rules("# comment\n" + rule)

    def test_empty_replacements_and_duplicate_patterns_match_runtime(self):
        text = "word = old\nword = new\n/sil =\n"
        corrector = RuleCorrector()
        self.assertEqual(corrector.update_rules(text), 2)
        self.assertEqual(corrector.substitute("word/sil"), "new")
        self.assertEqual(len(parse_rules(text)), 2)

    def test_literal_punctuation_backslashes_equals_and_spaces(self):
        corrector = RuleCorrector()
        for source, target in (("a.b", r"C:\stuff\1"), ("x = y", " x = z "),
                               ("[word]", ""), ("question?", r"\s"),
                               ("line", "first\nsecond")):
            with self.subTest(source=source):
                text = encode_literal(source, target)
                corrector.update_rules(text)
                self.assertEqual(corrector.substitute(source), target)
                compiled, replacement = parse_rules(text)[0]
                self.assertEqual(compiled.sub(replacement, source), target)
        corrector.update_rules(encode_literal("a.b", "new"))
        self.assertEqual(corrector.substitute("axb"), "axb")


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "hot.txt").write_text("SAI | 别名\n", encoding="utf-8")
        (self.root / "hot-rule.txt").write_text("# keep comment\n欧拉玛 = Ollama\n", encoding="utf-8")
        self.parent = tk.Tk()
        self.parent.withdraw()
        self.addCleanup(self.parent.destroy)
        self.store = VoicePhraseStore(Path(self.temp.name) / "voice-phrases")
        self.editor = HotwordEditor(self.parent, self.root, voice_store=self.store)

    def set_text(self, name, text):
        widget = self.editor.editors[name]
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def test_edit_save_and_preview_preserve_other_file(self):
        original_hot = (self.root / "hot.txt").read_bytes()
        self.set_text("hot-rule.txt", "# keep comment\n欧拉玛 = Ollama\n千问 = Qwen\n")
        self.editor.sample.set("欧拉玛和千问")
        self.editor._preview()
        self.assertEqual(self.editor.result.get(), "Ollama和Qwen")
        self.assertTrue(self.editor._save("hot-rule.txt"))
        self.assertEqual((self.root / "hot.txt").read_bytes(), original_hot)
        self.assertIn("# keep comment", (self.root / "hot-rule.txt").read_text(encoding="utf-8"))

    def test_preview_applies_hotwords_before_rules(self):
        self.set_text("hot.txt", "123-4567-8910 | 我的手机号\n")
        self.set_text("hot-rule.txt", "8910 = 0000\n")
        self.editor.sample.set("我的手机号")
        self.editor._preview()
        self.assertEqual(self.editor.result.get(), "123-4567-0000")

    def test_preview_rebuilds_hotword_corrector_after_edits(self):
        self.set_text("hot.txt", "AAA | 别名\n")
        self.editor.sample.set("别名")
        self.editor._preview()
        self.assertEqual(self.editor.result.get(), "AAA")
        self.set_text("hot.txt", "BBB | 别名\n")
        self.editor._preview()
        self.assertEqual(self.editor.result.get(), "BBB")

    def test_invalid_rules_do_not_overwrite_file(self):
        original = (self.root / "hot-rule.txt").read_bytes()
        self.set_text("hot-rule.txt", "[ = invalid")
        with patch("core.desktop_hotwords.messagebox.showerror") as error:
            self.assertFalse(self.editor._save("hot-rule.txt"))
            error.assert_called_once()
        self.assertEqual((self.root / "hot-rule.txt").read_bytes(), original)

    def test_external_edit_is_not_overwritten(self):
        self.set_text("hot.txt", "local edit")
        (self.root / "hot.txt").write_text("external edit", encoding="utf-8")
        with patch("core.desktop_hotwords.messagebox.showerror"):
            self.assertFalse(self.editor._save("hot.txt"))
        self.assertEqual((self.root / "hot.txt").read_text(encoding="utf-8"), "external edit")

    def test_save_dirty_writes_all_changed_files(self):
        self.assertEqual(self.editor.dirty_names(), [])
        self.set_text("hot.txt", "new term")
        self.set_text("hot-rule.txt", "wrong = right")
        self.assertEqual(self.editor.dirty_names(), ["hot.txt", "hot-rule.txt"])
        self.assertTrue(self.editor.save_dirty())
        self.assertEqual(self.editor.dirty_names(), [])
        self.assertEqual((self.root / "hot.txt").read_text(encoding="utf-8"), "new term")
        self.assertEqual((self.root / "hot-rule.txt").read_text(encoding="utf-8"), "wrong = right")

    def test_save_dirty_keeps_every_file_when_a_rule_is_invalid(self):
        original = (self.root / "hot-rule.txt").read_bytes()
        self.set_text("hot.txt", "new term")
        self.set_text("hot-rule.txt", "[ = invalid")
        with patch("core.desktop_hotwords.messagebox.showerror") as error:
            self.assertFalse(self.editor.save_dirty())
            error.assert_called_once()
        self.assertEqual((self.root / "hot-rule.txt").read_bytes(), original)
        self.assertEqual(self.editor.dirty_names(), ["hot.txt", "hot-rule.txt"])

    def test_empty_rules_can_be_saved(self):
        self.set_text("hot-rule.txt", "")
        self.assertTrue(self.editor._save("hot-rule.txt"))
        self.assertEqual((self.root / "hot-rule.txt").read_text(encoding="utf-8"), "")

    def test_simple_table_add_modify_delete_and_preserve_regex(self):
        self.set_text("hot-rule.txt", "# comment\n[0-9]+ = number\n欧拉玛 = Ollama\n")
        self.editor._refresh_table()
        self.assertEqual(len(self.editor.table.get_children()), 1)
        self.editor.source.set("a.b")
        self.editor.target.set(r"C:\folder")
        self.editor._add_rule()
        self.assertEqual(len(self.editor.table.get_children()), 2)
        self.editor.sample.set("a.b 欧拉玛 123")
        self.editor._preview()
        self.assertEqual(self.editor.result.get(), r"C:\folder Ollama number")
        self.editor.table.selection_set("3")
        self.editor._select_rule()
        self.editor.target.set("new")
        self.editor._change_rule()
        self.assertTrue(self.editor._save_current())
        self.assertIn("[0-9]+ = number", (self.root / "hot-rule.txt").read_text(encoding="utf-8"))
        self.editor.table.selection_set("3")
        with patch("core.desktop_hotwords.messagebox.askyesno", return_value=True):
            self.editor._delete_rule()
        self.assertNotIn("@literal", self.editor._content("hot-rule.txt"))
        self.assertIn("# comment", self.editor._content("hot-rule.txt"))

    def test_simple_table_rejects_empty_source_and_duplicates(self):
        with patch("core.desktop_hotwords.messagebox.showerror") as error:
            self.editor._add_rule()
            self.assertEqual(error.call_count, 1)
            self.editor.source.set("欧拉玛")
            self.editor.target.set("duplicate")
            self.editor._add_rule()
            self.assertEqual(error.call_count, 2)

    def test_simple_table_roundtrip(self):
        self.editor.source.set("[literal]")
        self.editor.target.set("")
        self.editor._add_rule()
        self.assertTrue(self.editor._save_current())
        other = HotwordEditor(self.parent, self.root)
        try:
            self.assertEqual(len(other.table.get_children()), 2)
            other.sample.set("[literal]")
            other._preview()
            self.assertEqual(other.result.get(), "")
        finally:
            other.destroy()

    def test_saved_rules_are_hot_reloaded(self):
        manager = HotwordManager(hotword_files={
            "hot": self.root / "hot.txt", "rule": self.root / "hot-rule.txt"})
        manager.start()
        try:
            self.assertEqual(manager.rule_corrector.substitute("千问"), "千问")
            self.set_text("hot.txt", "UniqueHotword")
            self.assertTrue(self.editor._save("hot.txt"))
            self.set_text("hot-rule.txt", "千问 = Qwen")
            self.assertTrue(self.editor._save("hot-rule.txt"))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if (manager.rule_corrector.substitute("千问") == "Qwen"
                        and "UniqueHotword" in manager.phoneme_corrector.hotwords):
                    break
                time.sleep(.05)
            self.assertEqual(manager.rule_corrector.substitute("千问"), "Qwen")
            self.assertIn("UniqueHotword", manager.phoneme_corrector.hotwords)
        finally:
            manager.stop()


class VoicePhrasePanelTests(unittest.TestCase):
    """语音短语页：录音键驱动、页面内保存，不再弹独立对话框。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = tk.Tk()
        self.parent.withdraw()
        self.addCleanup(self.parent.destroy)
        self.store = VoicePhraseStore(Path(self.temp.name) / "voice-phrases")
        self.panel = VoicePhrasePanel(self.parent, store=self.store)
        self.addCleanup(self.panel.destroy)

    def test_pending_audio_is_saved_inline(self):
        audio = _chirp(300, 900, 1.0)
        self.panel._on_captured(audio)
        self.assertEqual(self.panel._pending.size, audio.size)
        self.panel.capture_text.set("打开帮助文档")
        self.panel._save_pending()
        items = self.store.load()
        self.assertEqual([item["text"] for item in items], ["打开帮助文档"])
        self.assertFalse(self.panel.capture.armed)
        self.assertIn("已保存", self.panel.status.get())
        self.assertIn("打开帮助文档", self.panel.table.item(items[0]["id"], "values")[0])

    def test_saving_the_same_text_merges_samples(self):
        for _ in range(2):
            self.panel._on_captured(_chirp(300, 900, 1.0))
            self.panel.capture_text.set("打开帮助文档")
            self.panel._save_pending()
        items = self.store.load()
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]["samples"]), 2)
        self.assertIn("第 2 份样本", self.panel.status.get())
        self.assertEqual(self.panel.table.item(items[0]["id"], "values")[1], "2 / 3")

    def test_panel_merges_duplicate_entries_on_startup(self):
        phrase = _chirp(300, 900, 1.0)
        self.store.add("打开帮助文档", [phrase], merge=False)
        self.store.add("打开帮助文档", [phrase], merge=False)
        panel = VoicePhrasePanel(self.parent, store=self.store)
        self.addCleanup(panel.destroy)
        self.assertEqual(len(self.store.load()), 1)
        self.assertEqual(len(self.store.load()[0]["samples"]), 2)
        self.assertIn("已合并", panel.status.get())

    def test_selecting_a_row_fills_the_phrase_text(self):
        phrase = _chirp(300, 900, 1.0)
        item = self.store.add("打开帮助文档", [phrase])
        self.panel.refresh()
        self.panel.table.selection_set(item["id"])
        self.panel._select()
        self.assertEqual(self.panel.capture_text.get(), "打开帮助文档")

    def test_save_requires_text_and_pending_audio(self):
        self.panel._on_captured(_chirp(300, 900, 1.0))
        with patch("core.desktop_voice_phrases.messagebox.showerror") as error:
            self.panel._save_pending()
            error.assert_called_once()
        self.assertEqual(self.store.load(), [])
        self.panel.capture_text.set("只有文字")
        self.panel._pending = np.zeros(0, dtype=np.float32)
        with patch("core.desktop_voice_phrases.messagebox.showerror") as error:
            self.panel._save_pending()
            error.assert_called_once()
        self.assertEqual(self.store.load(), [])

    def test_capture_flag_marks_the_recording_key_as_busy(self):
        flag = Path(self.temp.name) / "voice-capture.flag"
        capture = VoiceCapture(self.parent, flag_path=flag)
        capture._write_flag(True)
        payload = json.loads(flag.read_text(encoding="utf-8"))
        self.assertEqual(payload["pid"], os.getpid())
        self.assertGreater(payload["ts"], 0)
        self.assertFalse(list(flag.parent.glob("*.tmp")))
        capture._write_flag(False)
        self.assertFalse(flag.exists())

    def test_leaving_the_tab_lets_go_of_the_recording_key(self):
        self.panel.capture.owner = "panel"
        self.panel.capture._armed = True
        with patch.object(VoiceCapture, "disarm", autospec=True) as disarm:
            self.panel.set_active(False)
            disarm.assert_called_once()

    @staticmethod
    def _fake_arm(capture_self, *args, **kwargs):
        capture_self._armed = True
        return True

    def test_begin_capture_arms_when_ready(self):
        with patch.object(VoiceCapture, "arm", autospec=True,
                          side_effect=self._fake_arm) as arm:
            self.panel.begin_capture()
        self.assertEqual(arm.call_args.kwargs["owner"], "panel")
        self.assertIn("按住", self.panel.capture_status.get())
        self.assertEqual(self.panel.record_button._text, "取消录制")

    def test_device_text_names_the_microphone(self):
        with patch.object(VoicePhrasePanel, "_resolve_device",
                          return_value=(3, "")), \
                patch.object(VoicePhrasePanel, "_device_name",
                             return_value="测试麦克风"):
            panel = VoicePhrasePanel(self.parent, store=self.store)
        self.addCleanup(panel.destroy)
        self.assertEqual(panel.device_text.get(), "当前麦克风：测试麦克风")

    def test_unavailable_selected_microphone_is_reported(self):
        with patch.object(VoicePhrasePanel, "_resolve_device",
                          return_value=(None, "所选麦克风不可用（X）")), \
                patch.object(VoicePhrasePanel, "_device_name",
                             return_value="默认麦"):
            panel = VoicePhrasePanel(self.parent, store=self.store)
        self.addCleanup(panel.destroy)
        self.assertIn("不可用", panel.device_text.get())

    def test_sync_device_follows_the_configuration(self):
        self.panel.capture.device = 7
        with patch.object(VoicePhrasePanel, "_resolve_device",
                          return_value=(15, "")):
            self.panel._sync_device()
        self.assertEqual(self.panel.capture.device, 15)

    def test_low_volume_recording_is_flagged(self):
        self.panel._on_captured(np.full(16000, 0.01, dtype=np.float32))
        self.assertIn("音量偏低", self.panel.capture_status.get())

    def test_begin_capture_waits_for_the_loading_model(self):
        state = {"ready": False}
        panel = VoicePhrasePanel(self.parent, store=self.store,
                                 ready=lambda: state["ready"], loading=lambda: True)
        self.addCleanup(panel.destroy)
        with patch.object(VoiceCapture, "arm", autospec=True,
                          side_effect=self._fake_arm) as arm:
            panel.begin_capture()
            arm.assert_not_called()
            self.assertIn("载入", panel.capture_status.get())
            self.assertEqual(panel.record_button._text, "取消录制")
            state["ready"] = True
            panel.notify_client_ready()
        self.assertEqual(arm.call_args.kwargs["owner"], "panel")

    def test_begin_capture_prompts_when_recognition_is_not_started(self):
        panel = VoicePhrasePanel(self.parent, store=self.store,
                                 ready=lambda: False, loading=lambda: False)
        self.addCleanup(panel.destroy)
        with patch("core.desktop_voice_phrases.messagebox.showinfo") as info, \
                patch.object(VoiceCapture, "arm", autospec=True,
                             side_effect=self._fake_arm) as arm:
            panel.begin_capture()
        info.assert_called_once()
        arm.assert_not_called()
        self.assertFalse(panel.mode_active)

    def test_client_stopped_leaves_the_recording_mode(self):
        self.panel._pending_arm = True
        self.panel.capture.owner = "panel"
        self.panel.capture._armed = True

        def fake_disarm(capture_self):
            capture_self._armed = False

        with patch.object(VoiceCapture, "disarm", autospec=True,
                          side_effect=fake_disarm) as disarm:
            self.panel.notify_client_stopped()
        disarm.assert_called_once()
        self.assertFalse(self.panel.mode_active)

    def test_full_list_does_not_arm_capture(self):
        for index in range(50):
            self.store.add(f"短语{index}",
                           [np.zeros(16000, dtype=np.float32)] * MAX_SAMPLES)
        with patch("core.desktop_voice_phrases.messagebox.showerror") as error, \
                patch.object(VoiceCapture, "arm", autospec=True,
                             side_effect=self._fake_arm) as arm:
            self.panel.begin_capture()
        error.assert_called_once()
        arm.assert_not_called()

    def test_full_list_with_sample_room_still_arms(self):
        """词条已满但还能追加样本时，允许开始录制。"""
        for index in range(50):
            self.store.add(f"短语{index}", [np.zeros(16000, dtype=np.float32)])
        with patch.object(VoiceCapture, "arm", autospec=True,
                          side_effect=self._fake_arm) as arm:
            self.panel.begin_capture()
        arm.assert_called_once()

    def test_begin_capture_keeps_the_test_mode_armed(self):
        """语音测试录完后不退出模式，可以再按录音键重试。"""
        seen = []
        with patch.object(VoiceCapture, "arm", autospec=True,
                          side_effect=self._fake_arm):
            self.panel.begin_capture(owner="test", captured=seen.append)
        self.assertEqual(self.panel.capture_owner, "test")
        self.panel._dispatch_captured(np.zeros(16000, dtype=np.float32))
        self.assertEqual(len(seen), 1)
        self.assertTrue(self.panel.mode_active)
        self.assertEqual(self.panel.capture_owner, "test")
        self.assertEqual(self.panel._pending.size, 0)

    def test_record_button_is_not_active_while_testing(self):
        """测试模式生效时「开始录制」按钮保持原样，两个按钮不联动。"""
        with patch.object(VoiceCapture, "arm", autospec=True,
                          side_effect=self._fake_arm):
            self.panel.begin_capture(owner="test")
        self.assertEqual(self.panel.record_button._text, "开始录制")
        self.assertTrue(self.panel.mode_active)

    def test_switching_between_modes_reuses_the_open_device(self):
        with patch.object(VoiceCapture, "arm", autospec=True,
                          side_effect=self._fake_arm) as arm:
            self.panel.begin_capture(owner="test")
            with patch.object(VoiceCapture, "set_owner", autospec=True) as switch:
                self.panel.begin_capture(owner="panel")
        arm.assert_called_once()
        switch.assert_called_once()
        self.assertEqual(switch.call_args.args[1], "panel")
        self.assertEqual(self.panel.capture_owner, "panel")
        self.assertEqual(self.panel.record_button._text, "取消录制")

    def test_press_while_the_device_is_still_opening_defers_the_start(self):
        capture = VoiceCapture(self.parent)
        capture._status = lambda _text: None
        capture._armed = True
        capture._opening = True
        capture._key_down = True
        capture._begin()
        self.assertTrue(capture._pending_start)
        self.assertFalse(capture.recording)
        stream = Mock()
        capture._open_queue.put(("ok", capture._open_generation, (stream, 16000)))
        capture._poll_open()
        self.assertIs(capture._stream, stream)
        self.assertFalse(capture._pending_start)
        self.assertTrue(capture.recording)
        capture._finish()

    def test_disarm_closes_the_preheated_stream(self):
        capture = VoiceCapture(self.parent)
        stream = Mock()
        capture._armed = True
        capture._stream = stream
        capture._stream_rate = 16000
        capture.disarm()
        stream.stop.assert_called_once()
        stream.close.assert_called_once()
        self.assertIsNone(capture._stream)
        self.assertFalse(capture.armed)

    def test_stale_microphone_open_from_the_previous_arm_is_discarded(self):
        capture = VoiceCapture(self.parent)
        stale = Mock()
        capture._armed = True
        capture._opening = True
        capture._open_queue.put(("ok", capture._open_generation - 1, (stale, 16000)))
        capture._open_queue.put(("ok", capture._open_generation, (Mock(), 16000)))
        capture._poll_open()
        stale.stop.assert_called_once()
        stale.close.assert_called_once()
        self.assertIsNotNone(capture._stream)
        self.assertFalse(capture._opening)

    def test_losing_window_focus_lets_go_of_the_recording_key(self):
        self.panel.capture.owner = "panel"
        self.panel.capture._armed = True
        with patch.object(type(self.panel), "winfo_viewable", return_value=True), \
                patch.object(type(self.panel), "_window_focused", return_value=False), \
                patch.object(VoiceCapture, "disarm", autospec=True) as disarm:
            self.panel._on_visibility_changed()
        disarm.assert_called_once()

    def test_wait_for_data_detects_silent_streams(self):
        capture = VoiceCapture(self.parent)
        capture._data_seen = False
        self.assertFalse(capture._wait_for_data([0.05]))
        capture._data_seen = True
        self.assertTrue(capture._wait_for_data([0.05]))
        self.assertFalse(capture._wait_for_data([0.0]))

    def test_callback_tracks_data_without_buffering_before_recording(self):
        capture = VoiceCapture(self.parent)
        callback = capture._make_callback()
        callback(np.zeros((10, 1), dtype=np.float32), 10, None, None)
        self.assertTrue(capture._data_seen)
        self.assertEqual(capture._chunks, [])
        capture._recording = True
        callback(np.zeros((10, 1), dtype=np.float32), 10, None, None)
        self.assertEqual(len(capture._chunks), 1)

    def test_finish_resamples_48k_devices_to_16k(self):
        capture = VoiceCapture(self.parent)
        capture._recording = True
        capture._stream_rate = 48000
        capture._chunks = [np.full((48000, 1), 0.1, dtype=np.float32)]
        capture._finish()
        self.assertEqual(capture.audio.size, 16000)

    def test_finish_reports_silent_recordings(self):
        captured = []
        status = []
        capture = VoiceCapture(self.parent)
        capture._captured = captured.append
        capture._status = status.append
        capture._recording = True
        capture._stream_rate = 16000
        capture._chunks = [np.zeros((16000, 1), dtype=np.float32)]
        capture._finish()
        self.assertEqual(captured, [])
        self.assertEqual(capture.audio.size, 0)
        self.assertTrue(any("没有采集到声音" in text for text in status))

    def test_recording_toggles_the_tray_indicator(self):
        events = []
        capture = VoiceCapture(self.parent, on_recording=events.append)
        capture._stream = Mock()
        capture._stream_rate = 16000
        capture._begin()
        capture._chunks.append(np.zeros((16000, 1), dtype=np.float32))
        self.assertEqual(events, [True])
        self.assertTrue(capture.recording)
        capture._finish()
        self.assertEqual(events, [True, False])
        self.assertFalse(capture.recording)

    def test_playback_uses_the_default_output_device_in_stereo(self):
        """试听不能复用录音设备：输入端点没有输出通道（PaErrorCode -9998）。"""
        played = {}

        def fake_play(data, rate=None, **kwargs):
            played["data"] = data
            played["rate"] = rate
            played["device"] = kwargs.get("device")

        with patch("sounddevice.play", fake_play), \
                patch("sounddevice.query_devices",
                      return_value={"max_output_channels": 2}):
            play_audio(np.zeros(16000, dtype=np.float32))
        self.assertEqual(played["data"].shape, (16000, 2))
        self.assertIsNone(played["device"])
        self.assertEqual(played["rate"], 16000)

    def test_playback_falls_back_to_mono(self):
        played = {}

        with patch("sounddevice.play", lambda data, rate=None, **kw: played.update(data=data)), \
                patch("sounddevice.query_devices",
                      return_value={"max_output_channels": 1}):
            play_audio(np.zeros(8000, dtype=np.float32))
        self.assertEqual(played["data"].shape, (8000, 1))

    def test_play_pending_uses_the_output_helper(self):
        self.panel._on_captured(np.zeros(16000, dtype=np.float32))
        with patch("core.desktop_voice_phrases.play_audio") as play:
            self.panel._play_pending()
        play.assert_called_once()


class RecordingHotkeyDarwinTests(unittest.TestCase):
    """macOS：语音短语录音键用 darwin_intercept 屏蔽（Windows 上直接调回调）。"""

    @staticmethod
    def _listener(shortcut):
        from core.desktop_voice_phrases import _HotkeyListener
        return _HotkeyListener(shortcut, lambda: None, lambda: None)

    def test_darwin_intercept_is_attached_on_mac_only(self):
        listener = self._listener({"key": "caps_lock", "type": "keyboard"})
        with patch("sys.platform", "darwin"), patch("os.name", "posix"), \
                patch("pynput.keyboard.Listener") as kb:
            listener.start()
        self.assertIn("darwin_intercept", kb.call_args.kwargs)
        self.assertNotIn("win32_event_filter", kb.call_args.kwargs)

        other = self._listener({"key": "caps_lock", "type": "keyboard"})
        with patch("sys.platform", "linux"), patch("os.name", "posix"), \
                patch("pynput.keyboard.Listener") as kb2:
            other.start()
        self.assertNotIn("darwin_intercept", kb2.call_args.kwargs)

    def test_recording_key_suppresses_the_system_event(self):
        from pynput import keyboard
        from core.shortcut_keys import darwin_suppress_intercept
        listener = self._listener({"key": "caps_lock", "type": "keyboard"})
        intercept = darwin_suppress_intercept(listener)
        event = object()
        self.assertIs(intercept("keydown", event), event)
        listener._keyboard_press(keyboard.Key.caps_lock)
        self.assertTrue(listener.darwin_suppress)
        self.assertIsNone(intercept("keydown", event))
        self.assertFalse(listener.darwin_suppress)
        listener._keyboard_release(keyboard.Key.caps_lock)
        self.assertTrue(listener.darwin_suppress)
        self.assertIsNone(intercept("keyup", event))

    def test_other_keys_are_passed_through(self):
        from pynput import keyboard
        from core.shortcut_keys import darwin_suppress_intercept
        listener = self._listener({"key": "caps_lock", "type": "keyboard"})
        intercept = darwin_suppress_intercept(listener)
        event = object()
        listener._keyboard_press(keyboard.Key.space)
        self.assertFalse(listener.darwin_suppress)
        self.assertIs(intercept("keydown", event), event)

    def test_mouse_recording_key_suppresses_its_click(self):
        from types import SimpleNamespace
        from core.shortcut_keys import darwin_suppress_intercept
        listener = self._listener({"key": "x2", "type": "mouse"})
        intercept = darwin_suppress_intercept(listener)
        listener._mouse_click(0, 0, SimpleNamespace(name="x2"), True)
        self.assertTrue(listener.darwin_suppress)
        self.assertIsNone(intercept("mouse", object()))
        listener._mouse_click(0, 0, SimpleNamespace(name="x2"), False)
        self.assertTrue(listener.darwin_suppress)
        self.assertIsNone(intercept("mouse", object()))


class VoiceTestTests(unittest.TestCase):
    """测试替换区的「语音测试」：录音→匹配→填入测试原文→替换。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "hot.txt").write_text("SAI | 别名\n", encoding="utf-8")
        (self.root / "hot-rule.txt").write_text("帮助文档 = 帮助中心\n", encoding="utf-8")
        self.parent = tk.Tk()
        self.parent.withdraw()
        self.addCleanup(self.parent.destroy)
        self.store = VoicePhraseStore(self.root / "voice-phrases")
        self.phrase = _chirp(300, 900, 1.0)
        self.store.add("打开帮助文档", [self.phrase])
        self.editor = HotwordEditor(self.parent, self.root, voice_store=self.store)
        self.addCleanup(self.editor.destroy)

    def test_voice_test_fills_sample_and_applies_replacement(self):
        audio = np.concatenate([_silence(0.6), self.phrase, _silence(0.4)])
        self.editor._finish_voice_test(audio)
        self.assertEqual(self.editor.sample.get(), "打开帮助文档")
        self.assertIn("命中", self.editor.voice_test_status.get())
        self.assertEqual(self.editor.result.get(), "打开帮助中心")

    def test_voice_test_reports_miss_without_changing_result(self):
        self.editor.result.set("原结果")
        self.editor._finish_voice_test(_chirp(1500, 3000, 1.2))
        status = self.editor.voice_test_status.get()
        self.assertIn("未命中", status)
        self.assertIn("阈值", status)
        self.assertIn("最接近", status)
        self.assertEqual(self.editor.result.get(), "未命中语音短语")

    def test_switching_away_from_the_page_exits_recording_mode(self):
        with patch.object(VoicePhrasePanel, "cancel_capture", autospec=True) as cancel, \
                patch.object(HotwordEditor, "_cancel_voice_test", autospec=True) as test_cancel:
            self.editor.set_visible(False)
        cancel.assert_called_once()
        test_cancel.assert_called_once()

    def test_voice_test_button_ignores_the_recording_mode(self):
        """「开始录制」生效时「语音测试」按钮不被激活，反之亦然。"""
        with patch.object(VoiceCapture, "arm", autospec=True,
                          side_effect=VoicePhrasePanelTests._fake_arm):
            self.editor.voice_panel.begin_capture(owner="panel")
        self.assertEqual(self.editor.voice_test_button._text, "语音测试")
        with patch.object(VoiceCapture, "set_owner", autospec=True):
            self.editor._begin_voice_test()
        self.assertEqual(self.editor.voice_test_button._text, "取消测试")
        self.assertEqual(self.editor.voice_panel.record_button._text, "开始录制")

    def test_editor_wires_client_readiness_and_indicator(self):
        events = []

        class Client:
            def recognition_ready(self):
                return False

            def recognition_loading(self):
                return True

            def set_phrase_recording(self, active):
                events.append(active)

        editor = HotwordEditor(self.parent, self.root, voice_store=self.store,
                               client=Client())
        self.addCleanup(editor.destroy)
        self.assertFalse(editor.voice_panel._ready())
        self.assertTrue(editor.voice_panel._loading())
        editor.voice_panel.capture._notify_recording(True)
        self.assertEqual(events, [True])
