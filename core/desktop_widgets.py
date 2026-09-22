"""Shared desktop surfaces for the launcher.

The visual language follows clash-verge-rev: a light neutral canvas, white
rounded cards, one blue accent color, and compact switches instead of
check boxes.
"""

import queue
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from core.shortcut_keys import KeyCapture, shortcut_label

try:  # Pillow ships with the tray stack; keep a vector fallback just in case.
    from PIL import Image, ImageDraw, ImageFilter, ImageTk

    HAS_PIL = True
except ImportError:  # pragma: no cover - only hit without Pillow installed
    HAS_PIL = False

FONT_FAMILY = "Microsoft YaHei UI"

PAGE_BG = "#ECECEC"
SIDEBAR_BG = "#F5F5F5"
CARD_BG = "#FFFFFF"
DIVIDER = "#E4E4E6"
BORDER = "#DBDBDE"
PRIMARY = "#007AFF"
PRIMARY_HOVER = "#0A6FDB"
PRIMARY_ACTIVE = "#0064CB"
PRIMARY_DISABLED = "#A9CFFF"
SELECTED_BG = "#D9EBFF"
SELECTED_ACTIVE = "#CDE2FA"
HOVER_BG = "#EBEBED"
TEXT = "#1F1F1F"
TEXT_SECONDARY = "#6E6E73"
SUCCESS = "#06943D"
ERROR = "#FF3B30"
WARNING = "#FF9500"
SWITCH_ON = PRIMARY
SWITCH_OFF = "#D1D1D6"
SWITCH_OFF_HOVER = "#C3C3C9"

# Legacy names kept for older imports.
BACKGROUND = PAGE_BG
SIDEBAR = SIDEBAR_BG


def ui_font(size=9, weight="normal"):
    return (FONT_FAMILY, size, weight)


SHADOW_PAD = 3
SHADOW_ALPHA = 46
SHADOW_BLUR = 3
SURFACE_CACHE_LIMIT = 96

_SURFACE_CACHE = {}


def _vector_rounded_rect(canvas, x1, y1, x2, y2, radius, fill, outline=None,
                         tags=()):
    """Vector fallback used when Pillow is unavailable."""
    x1, y1, x2, y2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
    if outline:
        _vector_rounded_rect(canvas, x1, y1, x2, y2, radius, outline, tags=tags)
        _vector_rounded_rect(canvas, x1 + 1, y1 + 1, x2 - 1, y2 - 1,
                             max(1, radius - 1), fill, tags=tags)
        return
    radius = max(1, min(int(radius), int((x2 - x1) / 2), int((y2 - y1) / 2)))
    canvas.create_arc(x1, y1, x1 + 2 * radius, y1 + 2 * radius, start=90,
                      extent=90, style="pieslice", fill=fill, outline="", tags=tags)
    canvas.create_arc(x2 - 2 * radius, y1, x2, y1 + 2 * radius, start=0,
                      extent=90, style="pieslice", fill=fill, outline="", tags=tags)
    canvas.create_arc(x2 - 2 * radius, y2 - 2 * radius, x2, y2, start=270,
                      extent=90, style="pieslice", fill=fill, outline="", tags=tags)
    canvas.create_arc(x1, y2 - 2 * radius, x1 + 2 * radius, y2, start=180,
                      extent=90, style="pieslice", fill=fill, outline="", tags=tags)
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill,
                            outline="", tags=tags)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill,
                            outline="", tags=tags)


def _rounded_surface(width, height, radius, fill, border=None, shadow=False,
                     base=PAGE_BG, knob=None, scale=4):
    """Render a rounded rectangle (optionally with shadow and knob) off screen.

    Everything is drawn at ``scale``x and downsampled, which is what gives the
    smooth corners Tk canvas primitives cannot produce on their own.
    """
    width, height = max(1, int(width)), max(1, int(height))
    pad = SHADOW_PAD if shadow else 0
    image = Image.new("RGB", (width * scale, height * scale), base)
    box = (pad * scale, pad * scale,
           (width - pad) * scale - 1, (height - pad) * scale - 1)
    if shadow:
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            (box[0], box[1] + scale, box[2], box[3] + scale),
            radius=radius * scale, fill=(15, 23, 42, SHADOW_ALPHA))
        layer = layer.filter(ImageFilter.GaussianBlur(SHADOW_BLUR * scale))
        image.paste(layer, (0, 0), layer)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle(box, radius=min(radius, height / 2) * scale,
                           fill=fill, outline=border,
                           width=scale if border else 0)
    if knob is not None:
        radius_px = (height / 2 - 3) * scale
        center_x = (height / 2 if knob < 0.5 else width - height / 2) * scale
        center_y = height / 2 * scale
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(
            (center_x - radius_px, center_y - radius_px + scale * 0.4,
             center_x + radius_px, center_y + radius_px + scale * 0.4),
            fill=(15, 23, 42, 30))
        layer = layer.filter(ImageFilter.GaussianBlur(1.2 * scale))
        image.paste(layer, (0, 0), layer)
        ImageDraw.Draw(image).ellipse(
            (center_x - radius_px, center_y - radius_px,
             center_x + radius_px, center_y + radius_px), fill=CARD_BG)
    if scale > 1:
        image = image.resize((width, height), Image.LANCZOS)
    return image


def _surface_photo(canvas, width, height, radius, fill, border=None,
                   shadow=False, base=PAGE_BG, knob=None):
    scale = 4 if width * height <= 160000 else 2
    key = (id(canvas.winfo_toplevel()), width, height, radius, fill, border,
           shadow, base, knob, scale)
    photo = _SURFACE_CACHE.get(key)
    if photo is not None:
        try:
            photo.width()
            return photo
        except tk.TclError:
            _SURFACE_CACHE.pop(key, None)
    image = _rounded_surface(width, height, radius, fill, border, shadow, base,
                             knob, scale)
    photo = ImageTk.PhotoImage(image, master=canvas)
    if len(_SURFACE_CACHE) >= SURFACE_CACHE_LIMIT:
        _SURFACE_CACHE.pop(next(iter(_SURFACE_CACHE)))
    _SURFACE_CACHE[key] = photo
    return photo


def draw_surface(canvas, width, height, radius, fill, border=None,
                 shadow=False, base=PAGE_BG, knob=None, tags=()):
    """Paint an antialiased rounded surface on ``canvas``."""
    if HAS_PIL:
        try:
            photo = _surface_photo(canvas, width, height, radius, fill, border,
                                   shadow, base, knob)
            return canvas.create_image(0, 0, image=photo, anchor="nw",
                                       tags=tags if tags else None)
        except (tk.TclError, OSError, ValueError):
            pass
    _vector_rounded_rect(canvas, 0.5, 0.5, width - 0.5, height - 0.5, radius,
                         fill, outline=border, tags=tags)


class ScrollPage(ttk.Frame):
    """One navigation page: a scrollable column of cards."""

    def __init__(self, parent):
        super().__init__(parent, style="Page.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background=PAGE_BG, highlightthickness=0,
                                width=1, height=1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       style="Slim.Vertical.TScrollbar",
                                       command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.body = ttk.Frame(self.canvas, style="Page.TFrame",
                              padding=(20, 16, 20, 24))
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.canvas.bind("<Configure>", self._resize)
        self.body.bind("<Configure>", self._region)
        # Bind on this toplevel, but only scroll events originating in this page.
        self._wheel_id = self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Destroy>", self._destroy, add="+")

    def _resize(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        self._region()

    def _region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_scroll(self, first, last):
        self.scrollbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scrollbar.grid_remove()
        else:
            self.scrollbar.grid()

    def _wheel(self, event):
        widget = event.widget
        while widget is not None and widget is not self:
            widget = getattr(widget, "master", None)
        if widget is self and self.canvas.yview() != (0.0, 1.0):
            if isinstance(event.widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
                return
            self.canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"

    def _destroy(self, event):
        if event.widget is self:
            self.winfo_toplevel().unbind("<MouseWheel>", self._wheel_id)


class Card(tk.Frame):
    """White, rounded setting group that grows with its content.

    The rounded surface is painted on a background canvas; the content frame
    is packed normally so geometry propagation keeps working.
    """

    INSET = 6

    def __init__(self, parent, title=None, padding=(14, 12, 14, 14), radius=10,
                 background=PAGE_BG):
        super().__init__(parent, background=background, highlightthickness=0,
                         bd=0)
        self._radius = radius
        self._background = background
        self._redraw_job = None
        self.canvas = tk.Canvas(self, background=background,
                                highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.content = ttk.Frame(self, style="Card.TFrame", padding=padding)
        self.content.pack(fill="both", expand=True,
                          padx=self.INSET, pady=self.INSET)
        # The canvas is created first, so it stays behind the packed content.
        if title:
            ttk.Label(self.content, text=title,
                      style="CardTitle.TLabel").pack(anchor="w")
        self.body = ttk.Frame(self.content, style="Card.TFrame")
        self.body.pack(fill="both", expand=True,
                       pady=(10, 0) if title else (0, 0))
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event):
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
        self._redraw_job = self.after(
            30, lambda: self._paint(event.width, event.height))

    def _paint(self, width, height):
        self._redraw_job = None
        try:
            self.canvas.delete("surface")
            draw_surface(self.canvas, width, height, self._radius, CARD_BG,
                         shadow=True, base=self._background, tags="surface")
        except tk.TclError:
            pass


class Switch(tk.Canvas):
    """Compact iOS-style switch bound to a BooleanVar."""

    def __init__(self, parent, variable, command=None, background=CARD_BG,
                 width=40, height=22):
        super().__init__(parent, width=width, height=height,
                         background=background, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=True)
        self.variable = variable
        self.command = command
        self._hover = False
        self._focused = False
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<FocusIn>", lambda _e: self._focus(True))
        self.bind("<FocusOut>", lambda _e: self._focus(False))
        self.bind("<Destroy>", self._destroy, add="+")
        self._draw()

    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        if self.command is not None:
            self.command()
        return "break"

    def _enter(self, _event):
        self._hover = True
        self._draw()

    def _leave(self, _event):
        self._hover = False
        self._draw()

    def _focus(self, active):
        self._focused = active
        self._draw()

    def _draw(self):
        try:
            width = max(1, self.winfo_width())
            height = max(1, self.winfo_height())
        except tk.TclError:
            return
        self.delete("all")
        on = bool(self.variable.get())
        if on:
            track = PRIMARY_HOVER if self._hover else SWITCH_ON
        else:
            track = SWITCH_OFF_HOVER if self._hover else SWITCH_OFF
        draw_surface(self, width, height, height / 2, track,
                     border=PRIMARY if self._focused else None,
                     base=self.cget("background"), knob=1.0 if on else 0.0)

    def _destroy(self, event):
        if event.widget is self and self._trace:
            try:
                self.variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass
            self._trace = None


class NavButton(tk.Canvas):
    """Sidebar navigation entry with a rounded selection pill."""

    def __init__(self, parent, text, command=None, height=40, radius=10,
                 background=SIDEBAR_BG, font=None):
        super().__init__(parent, width=1, height=height, background=background,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        self.text = text
        self.command = command
        self._radius = radius
        self._font = font or ui_font(9, "bold")
        self._selected = False
        self._hover = False
        self._pressed = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())
        self._draw()

    def set_selected(self, selected):
        selected = bool(selected)
        if self._selected != selected:
            self._selected = selected
            self._draw()

    def _enter(self, _event):
        self._hover = True
        self._draw()

    def _leave(self, _event):
        self._hover = False
        self._pressed = False
        self._draw()

    def _press(self, _event):
        self._pressed = True
        self.focus_set()
        self._draw()

    def _release(self, event):
        invoke = self._pressed and 0 <= event.x <= self.winfo_width() and \
            0 <= event.y <= self.winfo_height()
        self._pressed = False
        self._draw()
        if invoke:
            self._invoke()

    def _invoke(self, _event=None):
        if self.command is not None:
            self.command()
        return "break"

    def _draw(self):
        try:
            width = max(1, self.winfo_width())
            height = max(1, self.winfo_height())
        except tk.TclError:
            return
        self.delete("all")
        if self._selected and self._pressed:
            fill = SELECTED_ACTIVE
        elif self._selected:
            fill = SELECTED_BG
        elif self._hover and self._pressed:
            fill = "#E3E3E6"
        elif self._hover:
            fill = HOVER_BG
        else:
            fill = None
        if fill:
            draw_surface(self, width, height, self._radius, fill,
                         base=self.cget("background"))
        self.create_text(16, height / 2, text=self.text, anchor="w",
                         fill=TEXT, font=self._font)


class PillButton(tk.Canvas):
    """Rounded flat button in the four clash-verge button styles."""

    KINDS = {
        "primary": dict(fill=PRIMARY, text="#FFFFFF", border="",
                        hover=PRIMARY_HOVER, active=PRIMARY_ACTIVE,
                        disabled_fill=PRIMARY_DISABLED, disabled_text="#FFFFFF",
                        disabled_border=""),
        "secondary": dict(fill=CARD_BG, text=TEXT, border=BORDER,
                          hover="#F6F6F7", active="#EFEFF1",
                          disabled_fill="#F5F5F6", disabled_text="#B4B4BB",
                          disabled_border="#E8E8EA"),
        "danger": dict(fill=CARD_BG, text=ERROR, border="#F3C9C6",
                       hover="#FFF2F1", active="#FFE7E5",
                       disabled_fill="#F5F5F6", disabled_text="#D8B0AE",
                       disabled_border="#EDDFDE"),
        "ghost": dict(fill="", text=PRIMARY, border="",
                      hover="#EAF3FF", active="#D9EBFF",
                      disabled_fill="", disabled_text="#A8C7E8",
                      disabled_border=""),
    }

    def __init__(self, parent, text="", textvariable=None, command=None,
                 kind="secondary", width=None, height=34, radius=8,
                 background=CARD_BG, font=None, padx=18):
        self._font = font or ui_font(9, "bold")
        self._font_object = tkfont.Font(font=self._font)
        self._kind = kind if kind in self.KINDS else "secondary"
        self._command = command
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._focused = False
        self._radius = radius
        self._padx = padx
        self._auto_width = width is None
        self._variable = textvariable
        self._text = textvariable.get() if textvariable is not None else text
        if self._auto_width:
            width = self._measure() + 2 * padx
        super().__init__(parent, width=width, height=height,
                         background=background, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=True)
        self._trace = None
        if textvariable is not None:
            self._trace = textvariable.trace_add("write", self._text_changed)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", self._key_invoke)
        self.bind("<space>", self._key_invoke)
        self.bind("<FocusIn>", lambda _e: self._focus(True))
        self.bind("<FocusOut>", lambda _e: self._focus(False))
        self.bind("<Destroy>", self._destroy, add="+")
        self._draw()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        redraw = False
        if "state" in kw:
            self._state = ("disabled" if str(kw.pop("state")) == "disabled"
                           else "normal")
            redraw = True
        if "command" in kw:
            self._command = kw.pop("command")
        if "text" in kw:
            new_text = kw.pop("text")
            if self._variable is not None:
                self._variable.set(new_text)
            else:
                self._text = new_text
            redraw = True
        if "kind" in kw:
            kind = kw.pop("kind")
            if kind in self.KINDS:
                self._kind = kind
                redraw = True
        if kw:
            super().configure(**kw)
        if redraw:
            self._resize_to_text()
            self._draw()

    config = configure

    def set_kind(self, kind):
        self.configure(kind=kind)

    def invoke(self):
        if self._state == "normal" and self._command is not None:
            self._command()

    def _measure(self):
        return self._font_object.measure(self._text or " ")

    def _resize_to_text(self):
        if self._auto_width:
            super().configure(width=self._measure() + 2 * self._padx)

    def _text_changed(self, *_args):
        if self._variable is not None:
            self._text = self._variable.get()
        self._resize_to_text()
        self._draw()

    def _enter(self, _event):
        self._hover = True
        self._draw()

    def _leave(self, _event):
        self._hover = False
        self._pressed = False
        self._draw()

    def _press(self, _event):
        if self._state != "normal":
            return
        self._pressed = True
        self.focus_set()
        self._draw()

    def _release(self, event):
        invoke = self._pressed and self._state == "normal" and \
            0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._pressed = False
        self._draw()
        if invoke:
            self.invoke()

    def _key_invoke(self, _event=None):
        self.invoke()
        return "break"

    def _focus(self, active):
        self._focused = active
        self._draw()

    def _draw(self):
        try:
            width = max(1, self.winfo_width())
            height = max(1, self.winfo_height())
        except tk.TclError:
            return
        if width <= 1:
            width = int(self.cget("width"))
            height = int(self.cget("height"))
        self.delete("all")
        colors = self.KINDS[self._kind]
        if self._state == "disabled":
            fill = colors["disabled_fill"] or self.cget("background")
            text = colors["disabled_text"]
            border = colors["disabled_border"] or None
        else:
            if self._pressed:
                fill = colors["active"]
            elif self._hover:
                fill = colors["hover"]
            else:
                fill = colors["fill"]
            fill = fill or self.cget("background")
            text = colors["text"]
            border = colors["border"] or None
            if self._focused and border:
                border = PRIMARY
        draw_surface(self, width, height, self._radius, fill, border=border,
                     base=self.cget("background"))
        self.create_text(width / 2, height / 2, text=self._text, fill=text,
                         font=self._font)

    def _destroy(self, event):
        if event.widget is self and self._trace:
            try:
                self._variable.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass
            self._trace = None


class ShortcutCapture(ttk.Frame):
    """Click once, then press a chord or a mouse button; no type selector."""

    def __init__(self, parent, shortcut, can_capture=lambda: True):
        super().__init__(parent, style="Card.TFrame")
        self.value = dict(shortcut)
        self.can_capture = can_capture
        self.active = False
        self.listeners = []
        self.poll = None
        self.events = queue.Queue()
        self.label = tk.StringVar(value=shortcut_label(self.value.get("key", "")))
        self.button = PillButton(self, textvariable=self.label, command=self.begin,
                                 kind="secondary", width=280, height=34,
                                 background=CARD_BG, font=ui_font(9),
                                 padx=10)
        self.button.pack(side="left", fill="x", expand=True)
        self.clear_button = PillButton(self, text="×", command=self.clear,
                                       kind="ghost", width=34, height=34,
                                       background=CARD_BG, font=ui_font(11),
                                       padx=4)
        self.clear_button.pack(side="left", padx=(8, 0))
        self.bind("<Destroy>", self._destroy, add="+")
        self.top = self.winfo_toplevel()
        self.focus_id = self.top.bind("<FocusOut>", self._focus_out, add="+")

    def begin(self):
        if self.active or not self.can_capture():
            return
        from pynput import keyboard, mouse
        self.active = True
        self.capture = KeyCapture()
        self.events = queue.Queue()
        self.label.set("请按键或鼠标侧键…")
        self.button.set_kind("primary")
        self.button.focus_set()

        def name(key):
            # VK names preserve letters for Ctrl+A (not the control character).
            if hasattr(key, "name"):
                return key.name
            vk = getattr(key, "vk", None)
            if vk is not None and 65 <= vk <= 90:
                return chr(vk).lower()
            return getattr(key, "char", None)

        def press(key):
            if name(key):
                self.events.put(("down", name(key)))

        def release(key):
            if name(key):
                self.events.put(("up", name(key)))

        def click(_x, _y, button, pressed):
            if not pressed and button.name in ("x1", "x2", "middle"):
                self.events.put(("mouse", button.name))

        try:
            self.listeners = [keyboard.Listener(on_press=press, on_release=release, suppress=True),
                              mouse.Listener(on_click=click)]
            for listener in self.listeners:
                listener.start()
            self.poll = self.after(25, self._drain)
        except Exception:
            self.cancel()
            raise

    def _drain(self):
        self.poll = None
        while self.active and not self.events.empty():
            kind, name = self.events.get_nowait()
            if name == "esc":
                self.cancel()
            elif kind == "mouse":
                self.commit(name, "mouse")
            elif kind == "down":
                self.capture.press(name)
                self.label.set(shortcut_label("+".join(sorted(self.capture.chord))))
            elif kind == "up":
                key = self.capture.release(name)
                if key:
                    self.commit(key, "keyboard")
        if self.active:
            self.poll = self.after(25, self._drain)

    def commit(self, key, kind):
        self.value.update(key=key, type=kind, enabled=True)
        self.cancel()

    def clear(self):
        self.value.update(key="", enabled=False)
        self.cancel()

    def cancel(self):
        self.active = False
        if self.poll is not None:
            self.after_cancel(self.poll)
            self.poll = None
        for listener in self.listeners:
            listener.stop()
        self.listeners = []
        self.label.set(shortcut_label(self.value.get("key", "")))
        if self.button.winfo_exists():
            self.button.set_kind("secondary")

    def _focus_out(self, _event):
        self.after_idle(self._check_focus)

    def _check_focus(self):
        if self.active and self.focus_displayof() is None:
            self.cancel()

    def _destroy(self, event):
        if event.widget is self:
            self.cancel()
            self.top.unbind("<FocusOut>", self.focus_id)
