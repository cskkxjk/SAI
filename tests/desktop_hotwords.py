"""Desktop hotword editing tests, using temporary files only."""

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from core.desktop_hotwords import HotwordEditor, parse_rules
from core.hotword_rules import encode_literal
from core.client.hotword.hot_rule import RuleCorrector
from core.client.hotword.manager import HotwordManager


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
        self.editor = HotwordEditor(self.parent, self.root)

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
