"""Desktop editor for the existing client hotword files."""

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from core.desktop_widgets import (BORDER, CARD_BG, DIVIDER, PRIMARY, Card,
                                  PillButton, TEXT, ui_font)
from core.hotword_rules import parse_rules, literal_rule, encode_literal


class HotwordEditor(ttk.Frame):
    """Embeddable page that edits hot.txt and hot-rule.txt in place."""

    def __init__(self, parent, root):
        super().__init__(parent, style="Page.TFrame")
        self.root = Path(root)
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
        self._refresh_table()
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self._refresh_table())
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
        ttk.Label(preview, text="替换结果", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12))
        self.result = tk.StringVar()
        ttk.Entry(preview, textvariable=self.result, state="readonly").grid(
            row=1, column=1, columnspan=2, sticky="ew")

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
        return ("hot-rule.txt", *self.names)[self.tabs.index(self.tabs.select())]

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
        return self._save(self._current())

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

    def _preview(self):
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
