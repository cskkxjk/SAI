"""Desktop editor for the existing client hotword files."""

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from core.desktop_widgets import (BORDER, CARD_BG, DIVIDER, PRIMARY, Card,
                                  PillButton, TEXT, ui_font)
from core.desktop_voice_phrases import VoicePhrasePanel, recording_shortcut
from core.client.voice_phrase.matcher import (
    DEFAULT_THRESHOLD,
    best_candidates,
    match_phrases,
)
from core.hotword_rules import parse_rules, literal_rule, encode_literal
from core.shortcut_keys import shortcut_label


class HotwordEditor(ttk.Frame):
    """Embeddable page that edits hot.txt and hot-rule.txt in place."""

    def __init__(self, parent, root, voice_store=None, client=None):
        super().__init__(parent, style="Page.TFrame")
        self.root = Path(root)
        self.client = client
        self.originals = {}
        self.editors = {}
        self.names = ("hot.txt", "hot-rule.txt")
        self.status = tk.StringVar()
        self._corrector = None
        self._corrector_source = None

        card = Card(self, title="热词与替换规则")
        card.pack(fill="x")
        body = card.body
        self.tabs = ttk.Notebook(body)
        self._build_simple()
        for name, title in zip(self.names, ("热词与别名（高级）", "正则规则（高级）")):
            content = self._read(name)
            self.originals[name] = content
            frame = ttk.Frame(self.tabs, padding=(10, 10, 10, 6), style="Card.TFrame")
            self.tabs.add(frame, text=title)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            editor = tk.Text(frame, wrap="none", undo=True, width=45, height=10,
                             relief="flat", highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=PRIMARY,
                             borderwidth=0, background=CARD_BG, foreground=TEXT,
                             padx=8, pady=8, insertbackground=TEXT, font=ui_font(9))
            vertical = ttk.Scrollbar(frame, orient="vertical",
                                     style="Card.Slim.Vertical.TScrollbar",
                                     command=editor.yview)
            horizontal = ttk.Scrollbar(frame, orient="horizontal",
                                       style="Card.Slim.Horizontal.TScrollbar",
                                       command=editor.xview)
            editor.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            editor.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            editor.insert("1.0", content or "")
            editor.edit_reset()
            self.editors[name] = editor
        self.voice_panel = VoicePhrasePanel(
            self.tabs, store=voice_store,
            ready=getattr(client, "recognition_ready", None),
            loading=getattr(client, "recognition_loading", None),
            on_recording=getattr(client, "set_phrase_recording", None),
            on_mode_change=self._sync_voice_test_ui)
        self.tabs.add(self.voice_panel, text="语音短语")
        self._refresh_table()
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.tabs.pack(fill="both", expand=True)

        actions = ttk.Frame(body, style="Card.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        ttk.Label(actions, textvariable=self.status,
                  style="Hint.TLabel").pack(side="left")
        PillButton(actions, "重新加载", command=self._reload,
                   background=CARD_BG).pack(side="right")
        PillButton(actions, "保存当前页", command=self._save_current, kind="primary",
                   background=CARD_BG).pack(side="right", padx=(0, 8))

        tk.Frame(body, background=DIVIDER, height=1).pack(fill="x", pady=(14, 12))
        preview = ttk.Frame(body, style="Card.TFrame")
        preview.pack(fill="x")
        preview.columnconfigure(1, weight=1)
        ttk.Label(preview, text="测试原文", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 7))
        self.sample = tk.StringVar()
        ttk.Entry(preview, textvariable=self.sample).grid(
            row=0, column=1, sticky="ew", pady=(0, 7))
        PillButton(preview, "测试替换", command=self._preview,
                   background=CARD_BG).grid(row=0, column=2, padx=(10, 0))
        self.voice_test_button = PillButton(preview, "语音测试",
                                            command=self._toggle_voice_test,
                                            background=CARD_BG)
        self.voice_test_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(preview, text="替换结果", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12))
        self.result = tk.StringVar()
        ttk.Entry(preview, textvariable=self.result, state="readonly").grid(
            row=1, column=1, columnspan=3, sticky="ew")
        self.voice_test_status = tk.StringVar()
        ttk.Label(preview, textvariable=self.voice_test_status, style="Hint.TLabel",
                  justify="left").grid(row=2, column=0, columnspan=4, sticky="w",
                                       pady=(7, 0))

    def _build_simple(self):
        frame = ttk.Frame(self.tabs, padding=10, style="Card.TFrame")
        self.tabs.add(frame, text="文字替换")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(frame, columns=("source", "target"), show="headings",
                                  selectmode="browse", height=6)
        self.table.heading("source", text="识别成了什么")
        self.table.heading("target", text="替换成什么")
        for column in ("source", "target"):
            self.table.column(column, width=220, minwidth=100)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical",
                                  style="Card.Slim.Vertical.TScrollbar",
                                  command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<<TreeviewSelect>>", self._select_rule)
        form = ttk.Frame(frame, style="Card.TFrame")
        form.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        form.columnconfigure(1, weight=1)
        self.source = tk.StringVar()
        self.target = tk.StringVar()
        for row, (label, variable) in enumerate((
                ("识别成了什么", self.source), ("替换成什么", self.target))):
            ttk.Label(form, text=label, style="Field.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Entry(form, textvariable=variable).grid(row=row, column=1,
                                                        sticky="ew", pady=4)
        buttons = ttk.Frame(form, style="Card.TFrame")
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        PillButton(buttons, "添加", command=self._add_rule,
                   background=CARD_BG).pack(side="left")
        self.change_button = PillButton(buttons, "修改选中项", command=self._change_rule,
                                        background=CARD_BG)
        self.change_button.pack(side="left", padx=8)
        self.delete_button = PillButton(buttons, "删除选中项", command=self._delete_rule,
                                        background=CARD_BG)
        self.delete_button.pack(side="left")
        self.change_button.configure(state="disabled")
        self.delete_button.configure(state="disabled")

    def _refresh_table(self):
        if "hot-rule.txt" not in self.editors:
            return
        self.table.delete(*self.table.get_children())
        self.change_button.configure(state="disabled")
        self.delete_button.configure(state="disabled")
        for index, line in enumerate(self._content("hot-rule.txt").splitlines()):
            try:
                pair = literal_rule(line)
            except (ValueError, KeyError, TypeError):
                continue
            if pair is not None:
                self.table.insert("", "end", iid=str(index), values=pair)

    def _tab_changed(self, _=None):
        self._refresh_table()
        try:
            self.voice_panel.set_active(
                self.tabs.index(self.tabs.select()) == self.tabs.index(self.voice_panel))
        except Exception:
            pass
        try:
            self.voice_panel.refresh()
        except Exception:
            pass

    def notify_client_ready(self):
        """识别模型就绪：继续语音短语页等待中的录制模式。"""
        try:
            self.voice_panel.notify_client_ready()
        except Exception:
            pass

    def notify_client_stopped(self):
        """识别客户端停止：退出语音短语录制模式。"""
        try:
            self.voice_panel.notify_client_stopped()
        except Exception:
            pass

    def set_visible(self, visible):
        """主界面切走本页时退出语音短语录制模式与语音测试。"""
        if visible:
            return
        try:
            self._cancel_voice_test(silent=True)
        except Exception:
            pass
        try:
            self.voice_panel.cancel_capture(silent=True)
        except Exception:
            pass

    def _select_rule(self, _=None):
        selection = self.table.selection()
        if not selection:
            return
        source, target = literal_rule(self._content("hot-rule.txt").splitlines()[int(selection[0])])
        self.source.set(source)
        self.target.set(target)
        self.change_button.configure(state="normal")
        self.delete_button.configure(state="normal")

    def _write_lines(self, lines):
        editor = self.editors["hot-rule.txt"]
        editor.delete("1.0", "end")
        editor.insert("1.0", "\n".join(lines) + "\n")
        self._refresh_table()
        self.status.set("有未保存的修改")

    def _add_rule(self):
        try:
            line = encode_literal(self.source.get(), self.target.get())
        except ValueError as exc:
            messagebox.showerror("无法添加", str(exc), parent=self)
            return
        lines = self._content("hot-rule.txt").splitlines()
        for existing in lines:
            try:
                pair = literal_rule(existing)
            except (ValueError, KeyError, TypeError):
                continue
            if pair and pair[0] == self.source.get():
                messagebox.showerror("原词已存在", "请选中已有条目后修改。", parent=self)
                return
        self._write_lines(lines + [line])
        self.source.set("")
        self.target.set("")

    def _change_rule(self):
        selection = self.table.selection()
        if not selection:
            return
        try:
            line = encode_literal(self.source.get(), self.target.get())
        except ValueError as exc:
            messagebox.showerror("无法修改", str(exc), parent=self)
            return
        lines = self._content("hot-rule.txt").splitlines()
        for index, existing in enumerate(lines):
            if index == int(selection[0]):
                continue
            try:
                pair = literal_rule(existing)
            except (ValueError, KeyError, TypeError):
                continue
            if pair and pair[0] == self.source.get():
                messagebox.showerror("原词已存在", "请使用不同的原词。", parent=self)
                return
        lines[int(selection[0])] = line
        self._write_lines(lines)

    def _delete_rule(self):
        selection = self.table.selection()
        if not selection:
            return
        if not messagebox.askyesno("删除替换", "删除选中的替换条目？", parent=self):
            return
        lines = self._content("hot-rule.txt").splitlines()
        del lines[int(selection[0])]
        self._write_lines(lines)

    def _read(self, name):
        path = self.root / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    def _content(self, name):
        return self.editors[name].get("1.0", "end-1c")

    def _current(self):
        index = self.tabs.index(self.tabs.select())
        if index >= len(self.names) + 1:
            return None
        return ("hot-rule.txt", *self.names)[index]

    def _dirty(self, name):
        return self._content(name) != (self.originals[name] or "")

    def dirty_names(self):
        return [name for name in self.names if self._dirty(name)]

    def save_dirty(self):
        dirty = self.dirty_names()
        for name in dirty:
            if name != "hot-rule.txt":
                continue
            try:
                parse_rules(self._content(name))
            except ValueError as exc:
                messagebox.showerror("保存失败", str(exc), parent=self)
                return False
        for name in dirty:
            if not self._save(name):
                return False
        return True

    def _save_current(self):
        name = self._current()
        if name is None:
            return True
        return self._save(name)

    def _save(self, name):
        content = self._content(name)
        try:
            if name == "hot-rule.txt":
                parse_rules(content)
            if self._read(name) != self.originals[name]:
                messagebox.showerror("文件已变更", "文件已被其他程序修改，请重新加载后编辑。",
                                     parent=self)
                return False
            (self.root / name).write_text(content, encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return False
        self.originals[name] = content
        self.status.set(f"已保存 {name}")
        return True

    def _reload(self):
        name = self._current()
        if name is None:
            return
        if self._dirty(name) and not messagebox.askyesno(
                "放弃修改", "放弃当前页未保存的修改？", parent=self):
            return
        try:
            content = self._read(name)
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)
            return
        editor = self.editors[name]
        editor.delete("1.0", "end")
        editor.insert("1.0", content or "")
        editor.edit_reset()
        self.originals[name] = content
        self._refresh_table()
        self.status.set(f"已加载 {name}")

    def _hotword_corrector(self):
        content = self._content("hot.txt")
        if self._corrector_source != content:
            from core.client.hotword.hot_phoneme import PhonemeCorrector
            corrector = PhonemeCorrector()
            corrector.update_hotwords(content)
            self._corrector = corrector
            self._corrector_source = content
        return self._corrector

    def _preview(self, cancel_test=True):
        if cancel_test and self._voice_test_active():
            self._cancel_voice_test(silent=True)
        try:
            text = self._hotword_corrector().correct(self.sample.get()).text
        except Exception as exc:
            self.result.set("")
            messagebox.showerror("热词匹配失败", str(exc), parent=self)
            return
        try:
            for pattern, replacement in parse_rules(self._content("hot-rule.txt")):
                text = pattern.sub(replacement, text)
        except ValueError as exc:
            self.result.set("")
            messagebox.showerror("规则无效", str(exc), parent=self)
            return
        self.result.set(text)

    # ------------------------------------------------------------- 语音测试
    def _voice_test_active(self):
        return (self.voice_panel.mode_active
                and self.voice_panel.capture_owner == "test")

    def _sync_voice_test_ui(self):
        """录制模式变化时同步「语音测试」按钮（与「开始录制」共用同一模式）。"""
        if "voice_test_button" not in self.__dict__:
            return
        self.voice_test_button.configure(
            text="取消测试" if self._voice_test_active() else "语音测试")

    def _toggle_voice_test(self):
        if self._voice_test_active():
            self._cancel_voice_test()
        else:
            self._begin_voice_test()

    @staticmethod
    def _sample_summary(templates):
        """测试状态里显示短语数与样本数（同文字多份样本会合并成一个词条）。"""
        phrases = len({tpl.text for tpl in templates})
        return f"{phrases} 个短语 / {len(templates)} 份样本"

    def _begin_voice_test(self):
        """按录音键录一段话，与已录入的语音短语匹配后走替换流程。

        与「开始录制」走同一条路：确认模型就绪、暂停听写、录音键采集，
        只是录完后直接匹配对比，而不是等待标注保存。
        """
        try:
            templates = self.voice_panel.store.templates()
        except Exception as exc:
            self.voice_test_status.set(f"读取语音短语失败：{exc}")
            return
        if not templates:
            messagebox.showinfo("还没有语音短语",
                                "请先在「语音短语」页录制短语，再回来测试。", parent=self)
            return
        shortcut = recording_shortcut()
        label = shortcut_label(shortcut.get("key", ""))
        self.voice_test_status.set("正在准备录音设备…")
        self.voice_panel.begin_capture(
            owner="test", status=self.voice_test_status.set,
            captured=self._finish_voice_test,
            ready_text=f"按住 {label} 说短语，松开后匹配（{self._sample_summary(templates)}）")
        self._sync_voice_test_ui()

    def _cancel_voice_test(self, silent=False):
        self.voice_panel.cancel_capture(silent=True)
        self._sync_voice_test_ui()
        if not silent:
            self.voice_test_status.set("")

    def _finish_voice_test(self, audio):
        try:
            from config_client import ClientConfig as Config

            templates = self.voice_panel.store.templates()
            threshold = float(getattr(Config, "voice_phrase_threshold", DEFAULT_THRESHOLD)
                              or DEFAULT_THRESHOLD)
            matches = match_phrases(audio, templates, threshold=threshold)
            closest = None if matches else best_candidates(audio, templates)
        except Exception as exc:
            self.voice_test_status.set(f"匹配失败：{exc}")
            return
        if not matches:
            if closest:
                top = closest[0]
                self.voice_test_status.set(
                    f"未命中：最接近「{top.text}」{top.score:.2f}，阈值 {threshold:.2f}"
                    f"（{self._sample_summary(templates)}，可再次按住录音键重试）")
            else:
                self.voice_test_status.set(
                    f"未命中（{self._sample_summary(templates)}，阈值 {threshold:.2f}）")
            self.result.set("未命中语音短语")
            return
        matches.sort(key=lambda item: item.score)
        self.sample.set(matches[0].text)
        summary = "、".join(f"「{item.text}」（{item.score:.2f}）" for item in matches)
        self.voice_test_status.set(f"命中 {summary}，阈值 {threshold:.2f}，已填入测试原文")
        self._preview(cancel_test=False)
