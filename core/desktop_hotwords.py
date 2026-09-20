"""Desktop editor for the existing client hotword files."""

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from core.hotword_rules import parse_rules, literal_rule, encode_literal


class HotwordEditor(tk.Toplevel):
    def __init__(self, parent, root):
        super().__init__(parent)
        self.withdraw()
        self.title("热词与替换")
        self.geometry("760x600")
        self.minsize(560, 460)
        self.root = Path(root)
        self.originals = {}
        self.editors = {}
        self.names = ("hot.txt", "hot-rule.txt")
        self.status = tk.StringVar()
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self._build_simple()
        for name, title in zip(self.names, ("热词与别名（高级）", "正则规则（高级）")):
            content = self._read(name)
            self.originals[name] = content
            frame = ttk.Frame(self.tabs, padding=8)
            self.tabs.add(frame, text=title)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            editor = tk.Text(frame, wrap="none", undo=True, width=45, height=12)
            vertical = ttk.Scrollbar(frame, orient="vertical", command=editor.yview)
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=editor.xview)
            editor.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            editor.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            editor.insert("1.0", content or "")
            editor.edit_reset()
            self.editors[name] = editor
        self._refresh_table()
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self._refresh_table())
        preview = ttk.Frame(outer)
        preview.pack(fill="x", pady=12)
        preview.columnconfigure(1, weight=1)
        ttk.Label(preview, text="测试原文").grid(row=0, column=0, padx=(0, 8))
        self.sample = tk.StringVar()
        ttk.Entry(preview, textvariable=self.sample).grid(row=0, column=1, sticky="ew")
        ttk.Button(preview, text="测试替换", command=self._preview).grid(row=0, column=2, padx=(8, 0))
        self.result = tk.StringVar()
        ttk.Label(preview, text="替换结果").grid(row=1, column=0, padx=(0, 8), pady=8)
        ttk.Entry(preview, textvariable=self.result, state="readonly").grid(
            row=1, column=1, columnspan=2, sticky="ew")
        ttk.Label(outer, textvariable=self.status, wraplength=520).pack(anchor="w")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="重新加载", command=self._reload).pack(side="left")
        ttk.Button(buttons, text="关闭", command=self._close).pack(side="right")
        ttk.Button(buttons, text="保存当前页", command=self._save_current).pack(
            side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.deiconify()

    def _build_simple(self):
        frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(frame, text="文字替换")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(frame, columns=("source", "target"), show="headings",
                                  selectmode="browse", height=8)
        self.table.heading("source", text="识别成了什么")
        self.table.heading("target", text="替换成什么")
        for column in ("source", "target"):
            self.table.column(column, width=220, minwidth=100)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<<TreeviewSelect>>", self._select_rule)
        form = ttk.Frame(frame)
        form.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        form.columnconfigure(1, weight=1)
        self.source = tk.StringVar()
        self.target = tk.StringVar()
        for row, (label, variable) in enumerate((
                ("识别成了什么", self.source), ("替换成什么", self.target))):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Entry(form, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)
        buttons = ttk.Frame(form)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="添加", command=self._add_rule).pack(side="left")
        self.change_button = ttk.Button(buttons, text="修改选中项", command=self._change_rule,
                                       state="disabled")
        self.change_button.pack(side="left", padx=8)
        self.delete_button = ttk.Button(buttons, text="删除选中项", command=self._delete_rule,
                                       state="disabled")
        self.delete_button.pack(side="left")

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

    def _preview(self):
        try:
            text = self.sample.get()
            for pattern, replacement in parse_rules(self._content("hot-rule.txt")):
                text = pattern.sub(replacement, text)
        except ValueError as exc:
            self.result.set("")
            messagebox.showerror("规则无效", str(exc), parent=self)
            return
        self.result.set(text)

    def _close(self):
        dirty = [name for name in self.names if self._dirty(name)]
        if dirty:
            save = messagebox.askyesnocancel("未保存的修改", "保存修改后关闭？", parent=self)
            if save is None:
                return
            if save:
                for name in dirty:
                    if not self._save(name):
                        return
        self.destroy()
