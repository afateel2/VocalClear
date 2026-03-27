"""
SoundBoard window — oscilloscope aesthetic, fixed 480×308 px.

Snaps to VocalClear main window (Winamp-style) when dragged close.

Layout:
  ┌─ 2px GREEN ──────────────────────────────────────────────────────┐
  │  SOUNDBOARD  ·  VOCALCLEAR                  [+ ADD]  [■ STOP ALL]│
  ├─ 1px dim ────────────────────────────────────────────────────────┤
  │  ○ OVERLAP   ● MONITOR   ──────────── ■■■□  80%   SFX LEVEL     │
  ├─ 1px dim ────────────────────────────────────────────────────────┤
  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐   ← 4-col grid    │
  │  │ kick   │ │airhorn │ │  bruh  │ │  clap  │      scrollable    │
  │  │  F1    │ │        │ │        │ │        │                    │
  │  └────────┘ └────────┘ └────────┘ └────────┘                    │
  ├─ 1px dim ────────────────────────────────────────────────────────┤
  │  4 sounds loaded  ·  2 playing                                   │
  └──────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import Optional, TYPE_CHECKING


if TYPE_CHECKING:
    from soundboard import SoundBoard
    from window_snapper import SnapManager

# ── Palette ───────────────────────────────────────────────────────────────────
BG_ROOT  = "#030603"
BG       = "#060d06"
BG_CARD  = "#0b160b"
BG_INPUT = "#0f1f0f"
GRID_COL = "#0d1f0d"

GREEN     = "#00e676"
GREEN_DIM = "#007a40"
GREEN_LO  = "#004d28"
GREEN_ACT = "#00ff87"
AMBER     = "#ffb300"
RED       = "#ff1744"
SILVER    = "#8899aa"

FG        = "#c8ffd4"
FG_DIM    = "#3a6642"
FG_MID    = "#6aaa7a"

FONT_MONO   = ("Consolas", 9)
FONT_MONO_M = ("Consolas", 10)
FONT_MONO_H = ("Consolas", 11, "bold")
FONT_MONO_L = ("Consolas", 8)
FONT_MONO_XL = ("Consolas", 13, "bold")

# Window dimensions — fixed to match main window width for snapping
W        = 480
BTN_COLS = 4
BTN_W    = 104   # (480 - 32 padding - 3×6 gaps) / 4 ≈ 104
BTN_H    = 80
GRID_H   = 296   # canvas height for scrollable button area
H        = 590   # total window height — matches main + settings windows
TICK_MS  = 100

_SB_W    = 8     # custom scrollbar width in pixels


class _CustomScrollbar(tk.Canvas):
    """
    Thin vertical scrollbar matching the app aesthetic.
    Drop-in replacement for ttk.Scrollbar — exposes .set() and calls
    the scroll command exactly like the standard scrollbar widget.
    """

    def __init__(self, parent, command, **kw):
        super().__init__(
            parent,
            width=_SB_W,
            bg=BG_INPUT,
            highlightthickness=0,
            cursor="arrow",
            **kw,
        )
        self._command     = command
        self._thumb_start = 0.0
        self._thumb_end   = 1.0
        self._dragging    = False
        self._drag_y      = 0
        self._drag_start  = 0.0
        self._hover       = False

        self.bind("<Configure>",       self._redraw)
        self.bind("<Button-1>",        self._on_click)
        self.bind("<B1-Motion>",       self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>",           lambda e: self._set_hover(True))
        self.bind("<Leave>",           lambda e: self._set_hover(False))

    # Called by the canvas yscrollcommand
    def set(self, first: str, last: str) -> None:
        self._thumb_start = float(first)
        self._thumb_end   = float(last)
        self._redraw()

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self._redraw()

    def _redraw(self, _=None) -> None:
        self.delete("all")
        h = self.winfo_height() or 100
        w = self.winfo_width()  or _SB_W
        ty0 = int(h * self._thumb_start)
        ty1 = int(h * self._thumb_end)
        ty1 = max(ty1, ty0 + 14)          # minimum thumb height
        col = GREEN if self._hover else GREEN_DIM
        self.create_rectangle(1, ty0, w - 1, ty1, fill=col, outline="")

    def _on_click(self, event) -> None:
        h = self.winfo_height()
        if not h:
            return
        frac = event.y / h
        if frac < self._thumb_start:
            self._command("scroll", -1, "pages")
        elif frac > self._thumb_end:
            self._command("scroll", 1, "pages")
        else:
            self._dragging   = True
            self._drag_y     = event.y
            self._drag_start = self._thumb_start

    def _on_drag(self, event) -> None:
        if not self._dragging:
            return
        h = self.winfo_height()
        if not h:
            return
        delta   = (event.y - self._drag_y) / h
        span    = self._thumb_end - self._thumb_start
        new_pos = max(0.0, min(1.0 - span, self._drag_start + delta))
        self._command("moveto", new_pos)

    def _on_release(self, _) -> None:
        self._dragging = False


def _apply_dark_titlebar(root: tk.Tk) -> None:
    """Apply Windows dark mode to the native title bar (Windows 10 19041+)."""
    try:
        import ctypes
        from window_snapper import _frame_hwnd
        hwnd = _frame_hwnd(root)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20,
            ctypes.byref(ctypes.c_int(1)),
            ctypes.sizeof(ctypes.c_int),
        )
    except Exception:
        pass


class SoundBoardWindow:
    def __init__(self, soundboard: "SoundBoard", snapper: "SnapManager" = None):
        self.sb       = soundboard
        self._snapper = snapper
        self._root:   Optional[tk.Tk] = None
        self._btn_frames: dict[str, tk.Frame] = {}
        self._refresh_pending = threading.Event()  # thread-safe refresh flag
        self._running = False

        self._snap_bar_r = None
        self._header_status_lbl = None

        self.sb._on_sounds_changed = self._schedule_refresh
        self.sb._on_play_changed   = self._schedule_refresh

    # ──────────────────────────────────────────────────────────────────────────
    # Entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._root = tk.Tk()
        self._root.withdraw()           # hide until dark mode applied + positioned
        self._root.title("SoundBoard  —  VocalClear")
        self._root.geometry(f"{W}x{H}")
        self._root.resizable(False, False)
        self._root.configure(bg=BG_ROOT)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Log Tkinter callback exceptions (stderr is hidden under pythonw.exe)
        import traceback as _tb, datetime as _dt
        _log_path = Path.home() / ".vocalclear" / "vocalclear.log"
        def _report_tk_exc(exc, val, tb):
            try:
                with open(_log_path, "a", encoding="utf-8") as _f:
                    _f.write(f"[{_dt.datetime.now():%H:%M:%S}] SB Tk error:\n")
                    _f.write("".join(_tb.format_exception(exc, val, tb)))
            except Exception:
                pass
        self._root.report_callback_exception = _report_tk_exc

        # Icon — same as main app
        ico = Path(__file__).parent / "vocalclear.ico"
        try:
            if ico.exists():
                self._root.iconbitmap(str(ico))
        except Exception:
            try:
                self._root.iconbitmap(default="")
            except Exception:
                pass

        self._build_ui()
        self._refresh_buttons()   # starts background hotkey thread if needed
        self._running = True

        self._root.update_idletasks()
        _apply_dark_titlebar(self._root)  # synchronous — no flicker

        # Position to the left of main window on first open (horizontal snap)
        if self._snapper:
            self._snapper.position_left_of("main", self._root)

        self._root.deiconify()

        if self._snapper:
            self._root.after(50, lambda: self._snapper.register("soundboard", self._root, snap_side="left-only"))
            self._root.after(300, self._tick_snap_bar)

        self._root.after(50, self._poll_refresh)
        self._root.mainloop()

    # ──────────────────────────────────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = self._root

        # ── Top accent line ───────────────────────────────────────────────────
        tk.Frame(root, bg=GREEN, height=2).pack(fill="x")

        # ── Header: title row ─────────────────────────────────────────────────
        hdr_title = tk.Frame(root, bg=BG_ROOT, padx=16)
        hdr_title.pack(fill="x", pady=(8, 2))

        tk.Label(hdr_title, text="SOUNDBOARD", bg=BG_ROOT, fg=GREEN,
                 font=FONT_MONO_XL).pack(side="left")
        tk.Label(hdr_title, text="  ·  VOCALCLEAR", bg=BG_ROOT, fg=FG_DIM,
                 font=FONT_MONO).pack(side="left")
        self._header_status_lbl = tk.Label(hdr_title, text="", bg=BG_ROOT,
                                           fg=GREEN, font=FONT_MONO_L)
        self._header_status_lbl.pack(side="left", padx=(12, 0))

        # ── Header: action buttons row ────────────────────────────────────────
        hdr_btns = tk.Frame(root, bg=BG_ROOT, padx=16)
        hdr_btns.pack(fill="x", pady=(0, 8))

        self._btn_widget(hdr_btns, "⬇ IMPORT",   self._do_import, side="left")
        tk.Frame(hdr_btns, bg=BG_ROOT, width=5).pack(side="left")
        self._btn_widget(hdr_btns, "⬆ EXPORT",   self._do_export, side="left")
        tk.Frame(hdr_btns, bg=BG_ROOT, width=5).pack(side="left")
        self._btn_widget(hdr_btns, "+ ADD",       self._add_sound, side="left")
        self._btn_widget(hdr_btns, "■ STOP ALL",  self._stop_all,  side="right", color=RED)

        tk.Frame(root, bg=GREEN_LO, height=1).pack(fill="x")

        # ── Controls row ──────────────────────────────────────────────────────
        ctrl = tk.Frame(root, bg=BG_CARD, padx=16, pady=7)
        ctrl.pack(fill="x")

        # Overlap toggle
        self._overlap_state = self.sb.overlap
        self._overlap_btn = tk.Label(
            ctrl, text=self._toggle_label("OVERLAP", self._overlap_state),
            bg=GREEN if self._overlap_state else BG_INPUT,
            fg=BG_ROOT if self._overlap_state else FG_DIM,
            font=FONT_MONO_L, cursor="hand2", padx=8, pady=4,
        )
        self._overlap_btn.pack(side="left", padx=(0, 6))
        self._overlap_btn.bind("<Button-1>", lambda e: self._on_overlap_toggle())

        # Monitor toggle
        self._monitor_state = self.sb.monitor_enabled
        self._monitor_btn = tk.Label(
            ctrl, text=self._toggle_label("🔈 MONITOR", self._monitor_state),
            bg=GREEN if self._monitor_state else BG_INPUT,
            fg=BG_ROOT if self._monitor_state else FG_DIM,
            font=FONT_MONO_L, cursor="hand2", padx=8, pady=4,
        )
        self._monitor_btn.pack(side="left", padx=(0, 10))
        self._monitor_btn.bind("<Button-1>", lambda e: self._on_monitor_toggle())

        # SFX level slider
        self._sfx_canvas = tk.Canvas(
            ctrl, height=16, bg=BG_INPUT, highlightthickness=0, cursor="hand2"
        )
        self._sfx_canvas.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._sfx_canvas.bind("<Button-1>",  self._on_sfx_click)
        self._sfx_canvas.bind("<B1-Motion>", self._on_sfx_click)
        self._draw_sfx_bar()

        self._sfx_pct_var = tk.StringVar(value=f"{int(self.sb.master_volume*100):3d}%")
        tk.Label(ctrl, textvariable=self._sfx_pct_var,
                 bg=BG_CARD, fg=GREEN, font=FONT_MONO, width=4).pack(side="left")
        tk.Label(ctrl, text="SFX", bg=BG_CARD, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")

        tk.Frame(root, bg=GRID_COL, height=1).pack(fill="x")

        # ── Scrollable button grid ─────────────────────────────────────────────
        grid_outer = tk.Frame(root, bg=BG)
        grid_outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(grid_outer, bg=BG, highlightthickness=0, height=GRID_H)

        vsb = _CustomScrollbar(grid_outer, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._grid_frame = tk.Frame(canvas, bg=BG)
        self._grid_win   = canvas.create_window((0, 0), window=self._grid_frame,
                                                 anchor="nw")

        def _on_canvas_configure(e):
            canvas.itemconfig(self._grid_win, width=canvas.winfo_width())

        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        self._grid_canvas = canvas

        # ── Status bar ────────────────────────────────────────────────────────
        tk.Frame(root, bg=GREEN_LO, height=1).pack(fill="x", side="bottom")
        status_bar = tk.Frame(root, bg=BG_ROOT, pady=5, padx=16)
        status_bar.pack(fill="x", side="bottom")
        self._status_var = tk.StringVar(value="Ready")
        tk.Label(status_bar, textvariable=self._status_var,
                 bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO_L).pack(side="left")

        # ── Right snap bar (overlay) — lights up when snapped to main's left ──
        from snap_bar import SnapBar, BAR_WIDTH
        self._snap_bar_r = SnapBar(root)
        self._snap_bar_r.place(relx=1.0, x=-BAR_WIDTH, y=0,
                               width=BAR_WIDTH, relheight=1.0)

    # ──────────────────────────────────────────────────────────────────────────
    # Button grid
    # ──────────────────────────────────────────────────────────────────────────

    def _refresh_buttons(self) -> None:
        if self._root is None:
            return
        sounds   = self.sb.sounds
        playing  = self.sb.playing_names
        existing = set(self._btn_frames.keys())
        current  = set(sounds.keys())

        for name in existing - current:
            w = self._btn_frames.pop(name, None)
            if w:
                w.destroy()

        for name in current - existing:
            self._create_button(name, sounds[name])

        for name, frame in self._btn_frames.items():
            is_playing = name in playing
            col = GREEN_ACT if is_playing else GREEN_DIM
            try:
                frame.config(highlightbackground=col,
                             highlightthickness=2 if is_playing else 1)
            except Exception:
                pass

        self._reflow_grid()

        n = len(current)
        self._status_var.set(
            f"{n} sound{'s' if n != 1 else ''} loaded"
            + (f"  ·  {len(playing)} playing" if playing else "")
        )

    def _create_button(self, name: str, sound) -> None:
        frame = tk.Frame(
            self._grid_frame, bg=BG_CARD, width=BTN_W, height=BTN_H,
            highlightbackground=GREEN_DIM, highlightthickness=1,
            cursor="hand2",
        )
        frame.pack_propagate(False)
        self._btn_frames[name] = frame

        # Dynamic font: shrink for longer names so everything fits in the tile
        n = len(name)
        if n <= 10:
            font = FONT_MONO       # Consolas 9
        elif n <= 16:
            font = FONT_MONO_L     # Consolas 8
        else:
            font = ("Consolas", 7) # extra small for very long names
        name_lbl = tk.Label(frame, text=name, bg=BG_CARD, fg=FG,
                            font=font, wraplength=BTN_W - 10, justify="center")
        name_lbl.pack(expand=True)

        vol_canvas = tk.Canvas(frame, height=4, bg=BG_INPUT, highlightthickness=0)
        vol_canvas.pack(fill="x", side="bottom", padx=4, pady=(0, 4))
        self._draw_vol_bar(vol_canvas, sound.volume)

        def _play(e, n=name):
            self.sb.play(n)
            self._refresh_buttons()

        def _enter(e, f=frame):
            f.config(bg=BG_INPUT)
            for c in f.winfo_children():
                try: c.config(bg=BG_INPUT)
                except Exception: pass

        def _leave(e, f=frame):
            f.config(bg=BG_CARD)
            for c in f.winfo_children():
                try: c.config(bg=BG_CARD)
                except Exception: pass

        def _right_click(e, n=name, vc=vol_canvas):
            self._show_context_menu(e, n, vc)

        for w in [frame, name_lbl, vol_canvas]:
            w.bind("<Button-1>", _play)
            w.bind("<Enter>",    _enter)
            w.bind("<Leave>",    _leave)
            w.bind("<Button-3>", _right_click)

    def _reflow_grid(self) -> None:
        PAD = 6
        n   = len(self._btn_frames)
        for i, (name, frame) in enumerate(self._btn_frames.items()):
            row, col = divmod(i, BTN_COLS)
            x = PAD + col * (BTN_W + PAD)
            y = PAD + row * (BTN_H + PAD)
            frame.place(x=x, y=y, width=BTN_W, height=BTN_H)

        n_rows   = max(1, (n + BTN_COLS - 1) // BTN_COLS)
        total_h  = PAD + n_rows * (BTN_H + PAD)
        total_w  = PAD + BTN_COLS * (BTN_W + PAD)
        self._grid_frame.config(width=total_w, height=total_h)
        self._grid_canvas.configure(scrollregion=(0, 0, total_w, total_h))

    def _draw_vol_bar(self, canvas: tk.Canvas, volume: float) -> None:
        canvas.update_idletasks()
        w = canvas.winfo_width() or BTN_W - 8
        canvas.delete("all")
        filled = max(1, int(w * volume))
        canvas.create_rectangle(0, 0, filled, 4, fill=GREEN_DIM, outline="")

    # ──────────────────────────────────────────────────────────────────────────
    # Context menu
    # ──────────────────────────────────────────────────────────────────────────

    def _show_context_menu(self, event, name: str,
                           vol_canvas: tk.Canvas) -> None:
        menu = tk.Menu(self._root, tearoff=0,
                       bg=BG_CARD, fg=FG, activebackground=GREEN_DIM,
                       activeforeground=BG_ROOT, font=FONT_MONO_L, bd=0,
                       relief="flat")

        menu.add_command(label=f"  ▶  Play '{name}'",
                         command=lambda: self.sb.play(name))
        menu.add_command(label=f"  ■  Stop '{name}'",
                         command=lambda: self.sb.stop(name))
        menu.add_separator()

        def _set_volume():
            self._show_volume_popup(name, vol_canvas)

        def _rename():
            self._show_rename_popup(name)

        def _remove():
            if messagebox.askyesno("Remove Sound",
                                   f"Remove '{name}' from the soundboard?\n"
                                   f"(Removes from VocalClear's sounds folder.\n"
                                   f"Your original source file is unchanged.)",
                                   parent=self._root):
                self.sb.remove_sound(name)
                self._refresh_buttons()

        menu.add_command(label="  🔊  Set volume…",  command=_set_volume)
        menu.add_command(label="  ✏  Rename…",       command=_rename)
        menu.add_separator()
        menu.add_command(label="  ✕  Remove",        command=_remove)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_rename_popup(self, name: str) -> None:
        """Modal popup to rename a soundboard sound."""
        top, w = self._popup_shell("RENAME", name)

        body = tk.Frame(top, bg=BG, padx=16, pady=14)
        body.pack(fill="x")
        tk.Label(body, text="NEW NAME", bg=BG, fg=FG_DIM,
                 font=FONT_MONO_L).pack(anchor="w", pady=(0, 4))
        entry = tk.Entry(body, bg=BG_INPUT, fg=GREEN, font=FONT_MONO,
                         insertbackground=GREEN, relief="flat",
                         highlightthickness=1, highlightcolor=GREEN,
                         highlightbackground=GREEN_LO)
        entry.insert(0, name)
        entry.pack(fill="x", ipady=4)

        result: list = []

        def _cancel():
            top.destroy()

        def _save():
            new_name = entry.get().strip()
            if not new_name or new_name == name:
                top.destroy()
                return
            ok = self.sb.rename_sound(name, new_name)
            top.destroy()
            if not ok:
                messagebox.showerror(
                    "Rename Failed",
                    f"Could not rename '{name}' to '{new_name}'.\n"
                    "The name may already be in use.",
                    parent=self._root,
                )
            self._refresh_buttons()

        entry.bind("<Return>", lambda e: _save())
        self._popup_btn_bar(top, _cancel, _save)

        top.update_idletasks()
        ph = top.winfo_reqheight()
        rx = self._root.winfo_rootx() + (self._root.winfo_width()  - w)  // 2
        ry = self._root.winfo_rooty() + (self._root.winfo_height() - ph) // 2
        top.geometry(f"{w}x{ph}+{rx}+{ry}")
        top.deiconify()
        top.grab_set()
        entry.focus_set()
        entry.select_range(0, tk.END)
        # No wait_window() — popup stays open; _save()/_cancel() handle everything.

    # ──────────────────────────────────────────────────────────────────────────
    # SFX level bar
    # ──────────────────────────────────────────────────────────────────────────

    def _draw_sfx_bar(self) -> None:
        c = self._sfx_canvas
        c.update_idletasks()
        w = c.winfo_width() or 200
        h = 16
        pct = self.sb.master_volume
        filled = max(1, int(w * pct))
        c.delete("all")
        for x in range(0, w, 10):
            c.create_line(x, 0, x, h, fill=GRID_COL, width=1)
        c.create_rectangle(0, 2, filled, h - 2, fill=GREEN_DIM, outline="")
        if filled > 4:
            c.create_rectangle(filled - 3, 2, filled, h - 2, fill=GREEN, outline="")
        c.create_line(filled, 0, filled, h, fill=GREEN, width=1)

    def _on_sfx_click(self, event) -> None:
        w = self._sfx_canvas.winfo_width()
        if not w:
            return
        vol = max(0.0, min(1.0, event.x / w))
        self.sb.master_volume = vol
        self._sfx_pct_var.set(f"{int(vol*100):3d}%")
        self._draw_sfx_bar()
        self.sb._save_sounds_config()

    # ──────────────────────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────────────────────────
    # Toggle helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _toggle_label(self, text: str, on: bool) -> str:
        return f"● {text}" if on else f"○ {text}"

    def _on_overlap_toggle(self) -> None:
        self._overlap_state = not self._overlap_state
        self.sb.overlap = self._overlap_state
        self._overlap_btn.config(
            text=self._toggle_label("OVERLAP", self._overlap_state),
            bg=GREEN if self._overlap_state else BG_INPUT,
            fg=BG_ROOT if self._overlap_state else FG_DIM,
        )
        self.sb._save_sounds_config()

    def _on_monitor_toggle(self) -> None:
        self._monitor_state = not self._monitor_state
        self.sb.monitor_enabled = self._monitor_state
        self._monitor_btn.config(
            text=self._toggle_label("🔈 MONITOR", self._monitor_state),
            bg=GREEN if self._monitor_state else BG_INPUT,
            fg=BG_ROOT if self._monitor_state else FG_DIM,
        )
        self.sb._save_sounds_config()

    # ──────────────────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────────────────

    def _add_sound(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add Sound Files", parent=self._root,
            filetypes=[
                ("Audio files", "*.mp3 *.ogg *.m4a"),
                ("All files",   "*.*"),
            ],
        )
        for p in paths:
            threading.Thread(
                target=lambda f=Path(p): self.sb.load_file(f), daemon=True
            ).start()

    def _stop_all(self) -> None:
        self.sb.stop()
        self._refresh_buttons()

    def _do_export(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export Soundboard Profile",
            parent=self._root,
            defaultextension=".zip",
            filetypes=[("ZIP profile", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            return

        def _run():
            try:
                self.sb.export_profile(Path(path))
                if self._root:
                    self._root.after(0, lambda: self._set_header_status("EXPORTED ✔"))
            except Exception as e:
                if self._root:
                    self._root.after(0, lambda: messagebox.showerror(
                        "Export Failed", str(e), parent=self._root))

        threading.Thread(target=_run, daemon=True).start()

    def _do_import(self) -> None:
        path = filedialog.askopenfilename(
            title="Import Soundboard Profile",
            parent=self._root,
            filetypes=[("ZIP profile", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            return

        merge = messagebox.askyesno(
            "Import Profile",
            "Merge imported sounds with existing ones?\n\n"
            "Yes = keep existing sounds and add imported ones\n"
            "No  = replace all sounds with the imported profile",
            parent=self._root,
        )

        def _run():
            try:
                self.sb.import_profile(Path(path), merge=merge)
                if self._root:
                    self._root.after(0, lambda: self._set_header_status("IMPORTED ✔"))
            except Exception as e:
                if self._root:
                    self._root.after(0, lambda: messagebox.showerror(
                        "Import Failed", str(e), parent=self._root))

        threading.Thread(target=_run, daemon=True).start()

    def _set_header_status(self, msg: str) -> None:
        """Briefly show a status message in the header, then clear it."""
        if not self._root:
            return
        # Reuse the existing status label if present, otherwise skip
        if hasattr(self, "_header_status_lbl") and self._header_status_lbl:
            self._header_status_lbl.config(text=msg)
            self._root.after(3000, lambda: (
                self._header_status_lbl.config(text="") if self._header_status_lbl else None
            ))

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # Styled popups
    # ──────────────────────────────────────────────────────────────────────────

    def _popup_shell(self, title: str, subtitle: str,
                     w: int = 340) -> tuple[tk.Toplevel, int]:
        """
        Create a styled modal Toplevel.
        Returns (top, w) — caller must set final geometry after building content.
        """
        top = tk.Toplevel(self._root)
        top.withdraw()          # hide until positioned (prevents flicker)
        top.title(title)
        top.configure(bg=BG_ROOT)
        top.resizable(False, False)
        top.transient(self._root)

        # App icon
        ico = Path(__file__).parent / "vocalclear.ico"
        try:
            if ico.exists():
                top.iconbitmap(str(ico))
        except Exception:
            pass

        # Dark title bar
        try:
            import ctypes
            from window_snapper import _frame_hwnd
            hwnd = _frame_hwnd(top)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

        # Header
        tk.Frame(top, bg=GREEN, height=2).pack(fill="x")
        hdr = tk.Frame(top, bg=BG_ROOT, padx=14, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text=title.upper(), bg=BG_ROOT, fg=GREEN,
                 font=("Consolas", 10, "bold")).pack(side="left")
        tk.Label(hdr, text=f"  ·  {subtitle}", bg=BG_ROOT, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")
        tk.Frame(top, bg=GREEN_LO, height=1).pack(fill="x")

        return top, w

    def _popup_btn_bar(self, top: tk.Toplevel,
                       on_cancel, on_save,
                       save_label: str = "SAVE") -> None:
        """Attach the standard Cancel / Save button row to a popup."""
        tk.Frame(top, bg=GREEN_LO, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(top, bg=BG_ROOT, padx=14, pady=8)
        bar.pack(fill="x", side="bottom")

        def _mk(parent, text, cmd, col):
            f = tk.Frame(parent, bg=col, cursor="hand2")
            lbl = tk.Label(f, text=f"[ {text} ]",
                           bg=col, fg=BG_ROOT, font=FONT_MONO_L, padx=8, pady=4)
            lbl.pack()
            f.pack(side="right", padx=(4, 0))
            bright = GREEN if col == GREEN_DIM else "#cc1133"
            for w in (f, lbl):
                w.bind("<Button-1>", lambda e, c=cmd: c())
                w.bind("<Enter>", lambda e, fw=f, lw=lbl, b=bright:
                       (fw.config(bg=b), lw.config(bg=b)))
                w.bind("<Leave>", lambda e, fw=f, lw=lbl, o=col:
                       (fw.config(bg=o), lw.config(bg=o)))

        _mk(bar, save_label, on_save, GREEN_DIM)
        _mk(bar, "CANCEL",   on_cancel, "#3a1010")

    def _show_volume_popup(self, name: str, vol_canvas: tk.Canvas) -> None:
        """Modal volume slider popup."""
        snd = self.sb.sounds.get(name)
        init_vol = snd.volume if snd else 1.0

        top, w = self._popup_shell("VOLUME", name)
        body = tk.Frame(top, bg=BG_ROOT, padx=16, pady=14)
        body.pack(fill="x")

        # Percentage label
        pct_var = tk.StringVar(value=f"{int(init_vol*100):3d}%")
        row = tk.Frame(body, bg=BG_ROOT)
        row.pack(fill="x", pady=(0, 8))
        tk.Label(row, text="LEVEL", bg=BG_ROOT, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")
        tk.Label(row, textvariable=pct_var, bg=BG_ROOT, fg=GREEN,
                 font=FONT_MONO, width=5).pack(side="right")

        # Slider canvas
        slider = tk.Canvas(body, height=20, bg=BG_INPUT,
                           highlightthickness=0, cursor="hand2")
        slider.pack(fill="x")
        hint = tk.Label(body, text="← drag to set level",
                        bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO_L)
        hint.pack(anchor="w", pady=(4, 0))

        vol = [init_vol]   # mutable so closures can write

        def _draw():
            slider.update_idletasks()
            cw = slider.winfo_width() or 290
            ch = 20
            v  = vol[0]
            filled = max(1, int(cw * v))
            slider.delete("all")
            for x in range(0, cw, 12):
                slider.create_line(x, 0, x, ch, fill=GRID_COL, width=1)
            slider.create_rectangle(0, 3, filled, ch - 3, fill=GREEN_DIM, outline="")
            if filled > 4:
                slider.create_rectangle(filled - 4, 3, filled, ch - 3,
                                        fill=GREEN, outline="")
            slider.create_line(filled, 0, filled, ch, fill=GREEN, width=1)
            pct_var.set(f"{int(v*100):3d}%")

        def _click(event):
            cw = slider.winfo_width()
            if not cw:
                return
            vol[0] = max(0.0, min(1.0, event.x / cw))
            _draw()

        slider.bind("<Button-1>",  _click)
        slider.bind("<B1-Motion>", _click)
        slider.bind("<Configure>", lambda _e: _draw())

        def _save():
            v = vol[0]
            top.destroy()
            self.sb.set_volume(name, v)
            self._draw_vol_bar(vol_canvas, v)

        def _cancel():
            top.destroy()

        self._popup_btn_bar(top, _cancel, _save)
        top.update_idletasks()
        ph = top.winfo_reqheight()
        rx = self._root.winfo_rootx() + (self._root.winfo_width()  - w)  // 2
        ry = self._root.winfo_rooty() + (self._root.winfo_height() - ph) // 2
        top.geometry(f"{w}x{ph}+{rx}+{ry}")
        top.deiconify()
        top.grab_set()
        top.focus_force()
        # No wait_window() — popup stays open; _save()/_cancel() handle everything.

    def _btn_widget(self, parent, text: str, cmd,
                    side="left", color=GREEN_DIM) -> tk.Label:
        f = tk.Frame(parent, bg=color, cursor="hand2")
        lbl = tk.Label(f, text=text, bg=color,
                       fg=BG_ROOT if color != BG_CARD else FG,
                       font=FONT_MONO_L, padx=8, pady=4)
        lbl.pack()
        f.pack(side=side, padx=(0, 4))
        bright = GREEN if color == GREEN_DIM else "#cc0033"
        for w in (f, lbl):
            w.bind("<Button-1>", lambda e, c=cmd: c())
            w.bind("<Enter>", lambda e, fw=f, lw=lbl, b=bright:
                   (fw.config(bg=b), lw.config(bg=b)))
            w.bind("<Leave>", lambda e, fw=f, lw=lbl, orig=color:
                   (fw.config(bg=orig), lw.config(bg=orig)))
        return lbl

    def _tick_snap_bar(self) -> None:
        if not self._root or not self._snapper:
            return
        _, right = self._snapper.get_snap_sides("soundboard")
        if self._snap_bar_r:
            self._snap_bar_r.set_active(right)
        self._root.after(200, self._tick_snap_bar)

    def _schedule_refresh(self) -> None:
        # Called from ANY thread (folder watcher, hotkey callback, Tk thread).
        # Only sets a thread-safe flag — never touches Tkinter directly.
        self._refresh_pending.set()

    def _poll_refresh(self) -> None:
        # Runs only on the Tk thread (scheduled via after).
        # Drains the refresh flag and calls _refresh_buttons if needed.
        if not self._root:
            return
        if self._refresh_pending.is_set():
            self._refresh_pending.clear()
            self._refresh_buttons()
        self._root.after(50, self._poll_refresh)

    def _on_close(self) -> None:
        self._running = False
        if self._snapper:
            self._snapper.unregister("soundboard")
        self.sb._on_sounds_changed = None
        self.sb._on_play_changed   = None
        if self._root:
            self._root.destroy()
            self._root = None
