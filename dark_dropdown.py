"""
DarkDropdown — custom combobox matching the VocalClear oscilloscope palette.

Design
──────
Closed state: a Frame with the current value label left-aligned and a ▾ arrow
on the right — styled like the other BG_INPUT controls in the app.

Open state: a borderless Toplevel appears directly below the widget with a
green 1 px border (the Frame bg bleeds around the 1 px inner inset).  Items
are individual tk.Label rows so each one can have a hover highlight.  A
custom scrollbar matching the SnapBar style appears when the list is tall.

The widget exposes the same minimal interface used by settings_window.py:
  • textvariable  — tk.StringVar; kept in sync on selection
  • command       — called (no arguments) whenever the selection changes
  • set_values()  — replace the dropdown list at runtime
"""

import tkinter as tk

# ── Palette (must match settings_window.py) ───────────────────────────────────
_BG_ROOT  = "#030603"
_BG       = "#060d06"
_BG_CARD  = "#0b160b"
_BG_INPUT = "#0f1f0f"
_GREEN    = "#00e676"
_GREEN_DIM= "#007a40"
_GREEN_LO = "#004d28"
_FG       = "#c8ffd4"
_FG_DIM   = "#3a6642"
_FONT     = ("Consolas", 9)

_MAX_VISIBLE  = 8     # items visible before scroll
_ITEM_PADY    = 4     # vertical padding inside each item label
_SB_W         = 7     # scrollbar width in px


class DarkDropdown(tk.Frame):
    """
    Usage
    ─────
        var = tk.StringVar(value="System default")
        dd  = DarkDropdown(parent, values=names, textvariable=var,
                           command=on_changed)
        dd.pack(fill="x", pady=(4, 0))
    """

    def __init__(self, parent, values: list, textvariable: tk.StringVar,
                 command=None, font=_FONT, **kw):
        super().__init__(
            parent,
            bg=_BG_INPUT,
            highlightthickness=1,
            highlightbackground=_GREEN_LO,
            highlightcolor=_GREEN,
            cursor="hand2",
            **kw,
        )
        self._values  = list(values)
        self._var     = textvariable
        self._command = command
        self._font    = font
        self._popup   : tk.Toplevel | None = None

        # ── Display row ───────────────────────────────────────────────────────
        # Use explicit text= + trace instead of textvariable= to avoid
        # cross-interpreter StringVar binding failures (each window is its own Tk).
        self._val_lbl = tk.Label(
            self, text=textvariable.get(),
            bg=_BG_INPUT, fg=_FG, font=font,
            anchor="w", padx=10,
        )
        self._val_lbl.pack(side="left", fill="both", expand=True, ipady=5)
        self._var.trace_add("write",
                            lambda *_: self._val_lbl.config(text=self._var.get()))

        self._arr_lbl = tk.Label(
            self, text="▾", bg=_BG_INPUT, fg=_GREEN_DIM, font=font, padx=10,
        )
        self._arr_lbl.pack(side="right", fill="y")

        for w in (self, self._val_lbl, self._arr_lbl):
            w.bind("<Button-1>", lambda _e: self._toggle())
            w.bind("<Enter>",    lambda _e: self._hover(True))
            w.bind("<Leave>",    lambda _e: self._hover(False))

    # ── Public API ────────────────────────────────────────────────────────────

    def set_values(self, new_values: list) -> None:
        self._values = list(new_values)
        self._close_popup()

    # ── Hover ─────────────────────────────────────────────────────────────────

    def _hover(self, on: bool) -> None:
        bg  = _BG_CARD if on else _BG_INPUT
        hbc = _GREEN   if on else _GREEN_LO
        for w in (self, self._val_lbl, self._arr_lbl):
            try:
                w.config(bg=bg)
            except Exception:
                pass
        self.config(highlightbackground=hbc)

    # ── Popup lifecycle ───────────────────────────────────────────────────────

    def _toggle(self) -> None:
        if self._popup and self._popup.winfo_exists():
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self) -> None:
        self.update_idletasks()

        x  = self.winfo_rootx()
        y  = self.winfo_rooty() + self.winfo_height() + 2
        pw = self.winfo_width()

        n       = len(self._values)
        visible = min(n, _MAX_VISIBLE)

        self._popup = tk.Toplevel()
        self._popup.withdraw()
        self._popup.overrideredirect(True)
        self._popup.attributes("-topmost", True)
        # Green 1 px border — outermost bg shows as border
        self._popup.configure(bg=_GREEN_LO)

        # ── Content area (1 px inset from green border) ───────────────────────
        inner = tk.Frame(self._popup, bg=_BG_INPUT)
        inner.place(x=1, y=1, relwidth=1.0, relheight=1.0,
                    width=-2, height=-2)

        # ── Scrollable item list ───────────────────────────────────────────────
        canvas = tk.Canvas(inner, bg=_BG_INPUT, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)

        needs_scroll = n > _MAX_VISIBLE
        vsb = None
        if needs_scroll:
            vsb = _MiniScrollbar(inner, canvas.yview)
            vsb.pack(side="right", fill="y")
            canvas.configure(yscrollcommand=vsb.set)

        items_frame = tk.Frame(canvas, bg=_BG_INPUT)
        _win = canvas.create_window((0, 0), window=items_frame, anchor="nw")

        cur = self._var.get()

        for val in self._values:
            active = (val == cur)
            bg  = _BG_CARD  if active else _BG_INPUT
            fg  = _GREEN    if active else _FG
            bar_color = _GREEN if active else _BG_INPUT

            row = tk.Frame(items_frame, bg=bg)
            # Left accent bar — lit on current selection
            tk.Frame(row, bg=bar_color, width=3).pack(side="left", fill="y")
            lbl = tk.Label(row, text=val, bg=bg, fg=fg,
                           font=self._font, anchor="w",
                           padx=8, pady=_ITEM_PADY)
            lbl.pack(side="left", fill="both", expand=True)
            row.pack(fill="x")

            def _enter(e, r=row, l=lbl):
                r.config(bg=_BG_CARD)
                l.config(bg=_BG_CARD, fg=_GREEN)
                # also light the accent bar
                for child in r.winfo_children():
                    if isinstance(child, tk.Frame):
                        child.config(bg=_GREEN_DIM)

            def _leave(e, r=row, l=lbl, a=active, bclr=bar_color,
                       orig_bg=bg, orig_fg=fg):
                r.config(bg=orig_bg)
                l.config(bg=orig_bg, fg=orig_fg)
                for child in r.winfo_children():
                    if isinstance(child, tk.Frame):
                        child.config(bg=bclr)

            def _click(e, v=val):
                self._var.set(v)
                if self._command:
                    self._command()
                self._close_popup()

            for w in (row, lbl):
                w.bind("<Enter>",    _enter)
                w.bind("<Leave>",    _leave)
                w.bind("<Button-1>", _click)

        # ── Fit and show ──────────────────────────────────────────────────────
        items_frame.update_idletasks()
        item_h    = items_frame.winfo_reqheight()
        row_h     = item_h // n if n else 24
        popup_h   = visible * row_h + 2      # +2 for border

        canvas.configure(scrollregion=(0, 0, pw, item_h))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(_win, width=e.width))

        if needs_scroll:
            # Scroll to show the selected item
            try:
                idx  = self._values.index(cur)
                frac = idx / n
                canvas.yview_moveto(max(0.0, frac - (visible / n) / 2))
            except ValueError:
                pass

        self._popup.geometry(f"{pw}x{popup_h}+{x}+{y}")
        self._popup.deiconify()
        self._popup.lift()

        self._arr_lbl.config(text="▴", fg=_GREEN)

        # Close when user clicks outside
        self._popup.bind("<FocusOut>", lambda _e: self.after(120, self._check_close))
        # MouseWheel scrolling
        if needs_scroll:
            self._popup.bind_all(
                "<MouseWheel>",
                lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"),
            )

    def _check_close(self) -> None:
        """Close popup if focus has left it."""
        if not (self._popup and self._popup.winfo_exists()):
            return
        try:
            f = str(self._popup.focus_get() or "")
        except Exception:
            f = ""
        if not f or not f.startswith(str(self._popup)):
            self._close_popup()

    def _close_popup(self) -> None:
        if self._popup and self._popup.winfo_exists():
            try:
                self._popup.unbind_all("<MouseWheel>")
            except Exception:
                pass
            self._popup.destroy()
        self._popup = None
        try:
            self._arr_lbl.config(text="▾", fg=_GREEN_DIM)
            self._hover(False)
        except Exception:
            pass


# ── Minimal custom scrollbar for the dropdown list ────────────────────────────

class _MiniScrollbar(tk.Canvas):
    def __init__(self, parent, scroll_cmd):
        super().__init__(parent, width=_SB_W, bg=_BG_INPUT,
                        highlightthickness=0, cursor="arrow")
        self._cmd  = scroll_cmd
        self._t0   = 0.0
        self._t1   = 1.0
        self._drag = False
        self._dy   = 0
        self._ds   = 0.0
        self.bind("<Configure>",       lambda _e: self._draw())
        self.bind("<Button-1>",        self._click)
        self.bind("<B1-Motion>",       self._drag_move)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", False))

    def set(self, first, last) -> None:
        self._t0 = float(first)
        self._t1 = float(last)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        h  = self.winfo_height() or 100
        y0 = int(h * self._t0)
        y1 = max(int(h * self._t1), y0 + 10)
        self.create_rectangle(1, y0, _SB_W - 1, y1,
                              fill=_GREEN_DIM, outline="")

    def _click(self, e) -> None:
        h = self.winfo_height()
        if not h:
            return
        frac = e.y / h
        if frac < self._t0:
            self._cmd("scroll", -1, "pages")
        elif frac > self._t1:
            self._cmd("scroll",  1, "pages")
        else:
            self._drag = True
            self._dy   = e.y
            self._ds   = self._t0

    def _drag_move(self, e) -> None:
        if not self._drag:
            return
        h = self.winfo_height()
        if not h:
            return
        delta = (e.y - self._dy) / h
        span  = self._t1 - self._t0
        pos   = max(0.0, min(1.0 - span, self._ds + delta))
        self._cmd("moveto", pos)
