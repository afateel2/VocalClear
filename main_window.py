"""
VocalClear — Main application window.

Shown on launch; closing minimizes to tray (does not quit).
Oscilloscope aesthetic matching settings_window.py and soundboard_window.py.

Layout:
  ┌─ VOCALCLEAR  ·  AI NOISE SUPPRESSION ────────── ● ACTIVE ─┐
  │  [VU meter — live input + output bars]                     │
  │   IN -12.4 dB                              OUT -18.2 dB    │
  ├────────────────────────────────────────────────────────────┤
  │  ENGINE  RNNOISE  AI · lightweight                         │
  │  OUTPUT  CABLE Input (VB-Audio Virtual Cable)              │
  ├────────────────────────────────────────────────────────────┤
  │  [ ⏸ PAUSE ]   [ ⚙ SETTINGS ]   [ 🎛 SOUNDBOARD ]        │
  ├────────────────────────────────────────────────────────────┤
  │  close to minimize to tray               [ ✖  QUIT ]      │
  └────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from collections import deque
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from audio_engine import AudioEngine
    from noise_filter import NoiseFilter
    from config import Config
    from soundboard import SoundBoard


# ── Colour palette (matches settings_window.py) ───────────────────────────────
BG_ROOT  = "#030603"
BG       = "#060d06"
BG_CARD  = "#0b160b"
BG_INPUT = "#0f1f0f"
GRID_COL = "#0d1f0d"

GREEN    = "#00e676"
GREEN_DIM= "#007a40"
GREEN_LO = "#004d28"
AMBER    = "#ffb300"
RED      = "#ff1744"

FG       = "#c8ffd4"
FG_DIM   = "#3a6642"
FG_MID   = "#6aaa7a"

FONT_MONO   = ("Consolas", 9)
FONT_MONO_H = ("Consolas", 13, "bold")
FONT_MONO_L = ("Consolas", 8)

W, H       = 480, 590
VU_BARS    = 24
VU_TICK_MS = 50


class MainWindow:
    def __init__(
        self,
        config:       "Config",
        noise_filter: "NoiseFilter",
        engine:       "AudioEngine",
        soundboard:   "SoundBoard",
        on_toggle:    Callable,
        on_settings:  Callable,
        on_soundboard: Callable,
        on_quit:      Callable,
        snapper       = None,
    ):
        self.config        = config
        self.noise_filter  = noise_filter
        self.engine        = engine
        self.soundboard    = soundboard
        self._on_toggle    = on_toggle
        self._on_settings  = on_settings
        self._on_soundboard = on_soundboard
        self._on_quit      = on_quit
        self._snapper      = snapper

        self._root:           Optional[tk.Tk]       = None
        self._status_var:     Optional[tk.StringVar] = None
        self._status_lbl:     Optional[tk.Label]    = None
        self._toggle_lbl:     Optional[tk.Label]    = None
        self._toggle_frame:   Optional[tk.Frame]    = None
        self._vu_canvas:      Optional[tk.Canvas]   = None
        self._vu_db_lbl:      Optional[tk.Label]    = None
        self._out_name_var:   Optional[tk.StringVar] = None
        self._err_var:        Optional[tk.StringVar] = None
        self._err_lbl:        Optional[tk.Label]    = None
        self._snap_bar_l      = None
        self._snap_bar_r      = None
        self._vu_running      = False
        self._hist_canvas:    Optional[tk.Canvas] = None
        self._in_history:     deque = deque(maxlen=220)
        self._out_history:    deque = deque(maxlen=220)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Build and display the window; runs tkinter mainloop (blocks)."""
        self._root = tk.Tk()
        self._root.withdraw()              # hide until fully built + dark mode applied
        self._root.title("VocalClear")
        self._root.geometry(f"{W}x{H}")
        self._root.resizable(False, False)
        self._root.configure(bg=BG_ROOT)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._try_set_icon()
        self._build_ui()
        self._root.update_idletasks()
        self._apply_dark_titlebar()        # synchronous — before first paint
        self._root.deiconify()             # show once, already dark
        self._vu_running = True
        self._tick_vu()
        if self._snapper:
            self._snapper.set_anchor("main")
            self._root.after(50, lambda: self._snapper.register("main", self._root))
            self._root.after(300, self._tick_snap_bars)
        self._root.mainloop()

    def show(self) -> None:
        """Restore window from tray — safe to call from any thread."""
        if self._root:
            self._root.after(0, self._do_show)

    def quit_from_tray(self) -> None:
        """Called by the tray Quit action — schedules a confirmed quit on the main thread."""
        if self._root:
            self._root.after(0, self._do_confirm_quit_from_tray)

    def _do_confirm_quit_from_tray(self) -> None:
        if self._sounds_playing():
            if not messagebox.askyesno(
                "Sounds Playing",
                "A sound is currently playing.\nQuit anyway?",
                icon="warning",
                parent=self._root,
            ):
                return
        self._do_destroy()

    def refresh_status(self) -> None:
        """Refresh toggle button label and status pill — safe from any thread."""
        if self._root:
            self._root.after(0, self._update_status)

    def refresh_output_device(self) -> None:
        """Update the output device name label — safe from any thread."""
        if self._root:
            self._root.after(0, self._update_output_device)

    def _update_output_device(self) -> None:
        if self._out_name_var:
            name = self.engine.output_device_name or "detecting…"
            self._out_name_var.set(name)

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _do_show(self) -> None:
        if self._root:
            self._root.deiconify()
            self._root.lift()
            self._root.focus_force()

    def _do_destroy(self) -> None:
        self._vu_running = False
        if self._root:
            self._root.destroy()
            self._root = None

    def _on_close(self) -> None:
        """Window ✕ → minimize to tray, do not quit."""
        if self._root:
            self._root.withdraw()

    def _sounds_playing(self) -> bool:
        sb = getattr(self, "soundboard", None)
        if sb is None:
            return False
        with sb._play_lock:
            return len(sb._playing) > 0

    def _handle_quit(self) -> None:
        """QUIT button handler."""
        if self._sounds_playing():
            if not messagebox.askyesno(
                "Sounds Playing",
                "A sound is currently playing.\nQuit anyway?",
                icon="warning",
                parent=self._root,
            ):
                return
        self._vu_running = False
        if self._snapper:
            self._snapper.unregister("main")
        self._on_quit()          # notify TrayApp
        if self._root:
            self._root.destroy()
            self._root = None

    def _try_set_icon(self) -> None:
        ico = Path(__file__).parent / "vocalclear.ico"
        if ico.exists():
            try:
                self._root.iconbitmap(str(ico))
                return
            except Exception:
                pass
        try:
            self._root.iconbitmap(default="")
        except Exception:
            pass

    def _apply_dark_titlebar(self) -> None:
        """Apply Windows dark mode to the native title bar (Windows 10 19041+)."""
        try:
            import ctypes
            from window_snapper import _frame_hwnd
            hwnd = _frame_hwnd(self._root)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = self._root

        # ── Header ────────────────────────────────────────────────────────────
        tk.Frame(root, bg=GREEN, height=2).pack(fill="x")

        hdr = tk.Frame(root, bg=BG_ROOT, padx=16, pady=8)
        hdr.pack(fill="x")

        tk.Label(hdr, text="VOCALCLEAR",
                 bg=BG_ROOT, fg=GREEN, font=FONT_MONO_H).pack(side="left")
        tk.Label(hdr, text="  ·  AI NOISE SUPPRESSION",
                 bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO).pack(side="left")

        self._status_var = tk.StringVar()
        self._status_lbl = tk.Label(
            hdr, textvariable=self._status_var,
            bg=BG_ROOT, fg=GREEN, font=FONT_MONO,
        )
        self._status_lbl.pack(side="right")

        tk.Frame(root, bg=GREEN_LO, height=1).pack(fill="x")

        # ── VU meter ──────────────────────────────────────────────────────────
        vu_outer = tk.Frame(root, bg=BG_ROOT, padx=16, pady=10)
        vu_outer.pack(fill="x")

        canvas_frame = tk.Frame(vu_outer, bg=GREEN_LO, bd=1, relief="flat")
        canvas_frame.pack(fill="x")

        self._vu_canvas = tk.Canvas(
            canvas_frame, height=140, bg=BG_CARD,
            highlightthickness=0, bd=0,
        )
        self._vu_canvas.pack(fill="x")

        vu_lbl = tk.Frame(vu_outer, bg=BG_ROOT)
        vu_lbl.pack(fill="x", pady=(3, 0))
        tk.Label(vu_lbl, text="INPUT",  bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO_L).pack(side="left")
        tk.Label(vu_lbl, text="OUTPUT", bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO_L).pack(side="right")
        self._vu_db_lbl = tk.Label(vu_lbl, text="", bg=BG_ROOT, fg=FG_MID, font=FONT_MONO_L)
        self._vu_db_lbl.pack(side="left", padx=(12, 0))

        # ── Engine / output info ──────────────────────────────────────────────
        tk.Frame(root, bg=GRID_COL, height=1).pack(fill="x", padx=16)

        info = tk.Frame(root, bg=BG_CARD, padx=16, pady=8)
        info.pack(fill="x", padx=16, pady=(6, 0))

        backend_map = {
            "deepfilter": ("DEEPFILTERNET 3", GREEN,  "AI · best quality"),
            "rnnoise":    ("RNNOISE",          GREEN,  "AI · lightweight"),
            "wiener":     ("WIENER FILTER",    AMBER,  "install pyrnnoise for AI quality"),
            "none":       ("NO BACKEND",       RED,    "error"),
        }
        b_name, b_col, b_hint = backend_map.get(
            self.noise_filter.backend, ("UNKNOWN", RED, ""))

        r1 = tk.Frame(info, bg=BG_CARD)
        r1.pack(fill="x")
        tk.Label(r1, text="ENGINE ", bg=BG_CARD, fg=FG_DIM, font=FONT_MONO_L).pack(side="left")
        tk.Label(r1, text=b_name,    bg=BG_CARD, fg=b_col,  font=FONT_MONO).pack(side="left")
        tk.Label(r1, text=f"  {b_hint}", bg=BG_CARD, fg=FG_DIM, font=FONT_MONO_L).pack(side="left")

        r2 = tk.Frame(info, bg=BG_CARD)
        r2.pack(fill="x", pady=(4, 0))
        tk.Label(r2, text="OUTPUT ", bg=BG_CARD, fg=FG_DIM, font=FONT_MONO_L).pack(side="left")
        self._out_name_var = tk.StringVar(value=self.engine.output_device_name or "detecting…")
        tk.Label(r2, textvariable=self._out_name_var,
                 bg=BG_CARD, fg=FG_MID, font=FONT_MONO_L).pack(side="left")

        # Stream error row (hidden unless there is an error)
        r3 = tk.Frame(info, bg=BG_CARD)
        r3.pack(fill="x", pady=(4, 0))
        self._err_var = tk.StringVar(value="")
        self._err_lbl = tk.Label(r3, textvariable=self._err_var,
                                 bg=BG_CARD, fg=RED, font=FONT_MONO_L,
                                 wraplength=W - 60, justify="left")
        self._err_lbl.pack(anchor="w")

        # ── Audio history ─────────────────────────────────────────────────────
        tk.Frame(root, bg=GRID_COL, height=1).pack(fill="x", padx=16, pady=(6, 0))

        hist_outer = tk.Frame(root, bg=BG_ROOT, padx=16, pady=5)
        hist_outer.pack(fill="x")

        tk.Label(hist_outer, text="AUDIO HISTORY",
                 bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO_L).pack(anchor="w", pady=(0, 3))

        hist_frame = tk.Frame(hist_outer, bg=GREEN_LO, bd=1, relief="flat")
        hist_frame.pack(fill="x")

        self._hist_canvas = tk.Canvas(
            hist_frame, height=126, bg=BG_CARD,
            highlightthickness=0, bd=0,
        )
        self._hist_canvas.pack(fill="x")

        # ── Action buttons ────────────────────────────────────────────────────
        tk.Frame(root, bg=GRID_COL, height=1).pack(fill="x", padx=16, pady=(6, 0))

        btn_bar = tk.Frame(root, bg=BG_ROOT, padx=16, pady=10)
        btn_bar.pack(fill="x")

        self._toggle_frame, self._toggle_lbl = self._make_btn(
            btn_bar, "", self._on_toggle, side="left",
        )
        self._make_btn(btn_bar, "⚙  SETTINGS",   self._on_settings,    side="left", gap=8)
        self._make_btn(btn_bar, "🎛  SOUNDBOARD", self._on_soundboard,  side="left", gap=8)

        self._update_status()   # sets toggle label + status pill

        # ── Bottom bar ────────────────────────────────────────────────────────
        tk.Frame(root, bg=GREEN_LO, height=1).pack(fill="x", side="bottom")
        bot = tk.Frame(root, bg=BG_ROOT, padx=16, pady=6)
        bot.pack(fill="x", side="bottom")

        tk.Label(bot, text="close ✕ to minimize to tray",
                 bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO_L).pack(side="left")
        self._make_btn(bot, "✖  QUIT", self._handle_quit, side="right", color=RED)

        # ── Snap bars (overlay) ───────────────────────────────────────────────
        # PCB-trace connector bars; light up when a companion window snaps here
        from snap_bar import SnapBar, BAR_WIDTH
        self._snap_bar_l = SnapBar(root)
        self._snap_bar_r = SnapBar(root)
        self._snap_bar_l.place(x=0, y=0, width=BAR_WIDTH, relheight=1.0)
        self._snap_bar_r.place(relx=1.0, x=-BAR_WIDTH, y=0,
                               width=BAR_WIDTH, relheight=1.0)

    # ──────────────────────────────────────────────────────────────────────────
    # Status / toggle refresh
    # ──────────────────────────────────────────────────────────────────────────

    def _update_status(self) -> None:
        if not self._root:
            return
        active = self.noise_filter.enabled
        if self._status_var:
            self._status_var.set("● ACTIVE" if active else "⏸ PAUSED")
        if self._status_lbl:
            self._status_lbl.config(fg=GREEN if active else FG_DIM)
        if self._toggle_lbl:
            self._toggle_lbl.config(text="⏸  PAUSE" if active else "▶  RESUME")

    # ──────────────────────────────────────────────────────────────────────────
    # VU animation
    # ──────────────────────────────────────────────────────────────────────────

    def _tick_snap_bars(self) -> None:
        if not self._root or not self._snapper:
            return
        left, right = self._snapper.get_snap_sides("main")
        if self._snap_bar_l:
            self._snap_bar_l.set_active(left)
        if self._snap_bar_r:
            self._snap_bar_r.set_active(right)
        self._root.after(200, self._tick_snap_bars)

    def _tick_vu(self) -> None:
        if not self._vu_running or self._root is None:
            return
        self._draw_vu()
        self._draw_history()
        self._poll_stream_error()
        self._root.after(VU_TICK_MS, self._tick_vu)

    def _poll_stream_error(self) -> None:
        err = self.engine.last_error
        if self._err_var is None:
            return
        if err:
            self._err_var.set(f"⚠  {err}")
        else:
            self._err_var.set("")

    def _draw_vu(self) -> None:
        c = self._vu_canvas
        c.update_idletasks()
        cw = c.winfo_width() or 448
        ch = c.winfo_height() or 140
        c.delete("all")

        for y in range(0, ch, 8):
            c.create_line(0, y, cw, y, fill=GRID_COL, width=1)
        for x in range(0, cw, 20):
            c.create_line(x, 0, x, ch, fill=GRID_COL, width=1)

        in_rms  = getattr(self.engine, "input_rms",  0.0)
        out_rms = getattr(self.engine, "output_rms", 0.0)

        half = cw // 2 - 4
        self._draw_vu_bars(c, x0=4,      y0=6, w=half, h=ch-12, rms=in_rms,  flip=False)
        self._draw_vu_bars(c, x0=half+8, y0=6, w=half, h=ch-12, rms=out_rms, flip=True)

        mid = cw // 2
        c.create_line(mid, 4, mid, ch - 4, fill=GREEN_LO, width=1)

        def _db(rms: float) -> float:
            return -60.0 if rms < 1e-9 else max(-60.0, 20 * math.log10(rms))

        self._vu_db_lbl.config(
            text=f"IN {_db(in_rms):+.1f} dB    OUT {_db(out_rms):+.1f} dB"
        )

    def _draw_vu_bars(self, c: tk.Canvas, x0, y0, w, h, rms, flip) -> None:
        n      = VU_BARS
        bar_w  = max(2, (w - n + 1) // n)
        filled = min(int(rms * n * 2.5), n)
        for i in range(n):
            xi = i if not flip else (n - 1 - i)
            bx = x0 + xi * (bar_w + 1)
            if i < filled:
                col = GREEN if i < n * 0.65 else (AMBER if i < n * 0.85 else RED)
            else:
                col = GREEN_LO
            c.create_rectangle(bx, y0, bx + bar_w, y0 + h, fill=col, outline="")

    def _draw_history(self) -> None:
        """Scrolling RMS level history — input (green) and output (dim green)."""
        c = self._hist_canvas
        if c is None:
            return

        in_rms  = getattr(self.engine, "input_rms",  0.0)
        out_rms = getattr(self.engine, "output_rms", 0.0)
        self._in_history.append(in_rms)
        self._out_history.append(out_rms)

        c.update_idletasks()
        cw = c.winfo_width() or 448
        ch = c.winfo_height() or 126
        c.delete("all")

        # Grid
        for y in range(0, ch, 8):
            c.create_line(0, y, cw, y, fill=GRID_COL, width=1)
        for x in range(0, cw, 20):
            c.create_line(x, 0, x, ch, fill=GRID_COL, width=1)

        n = len(self._in_history)
        if n < 2:
            return

        # Auto-scale peak with a soft floor so empty signal still shows baseline
        peak = max(max(self._in_history), max(self._out_history), 0.015)

        def _trace(hist, color):
            pts = []
            m = len(hist)
            for i, v in enumerate(hist):
                x = int(i * (cw - 1) / (m - 1))
                y = int(ch - 2 - (v / peak) * (ch - 4))
                y = max(2, min(ch - 2, y))
                pts.extend([x, y])
            if len(pts) >= 4:
                c.create_line(*pts, fill=color, width=1, smooth=False)

        _trace(self._out_history, GREEN_DIM)   # draw output first (behind)
        _trace(self._in_history,  GREEN)        # input on top

        # Corner legend
        c.create_text(5, 4,  text="IN",  anchor="nw", fill=GREEN,     font=FONT_MONO_L)
        c.create_text(5, 16, text="OUT", anchor="nw", fill=GREEN_DIM, font=FONT_MONO_L)

    # ──────────────────────────────────────────────────────────────────────────
    # Button factory
    # ──────────────────────────────────────────────────────────────────────────

    def _make_btn(self, parent, text: str, cmd: Callable,
                  side="left", color=GREEN_DIM, gap=0) -> tuple[tk.Frame, tk.Label]:
        if gap:
            tk.Frame(parent, bg=BG_ROOT, width=gap).pack(side=side)
        f = tk.Frame(parent, bg=color, cursor="hand2")
        lbl = tk.Label(f, text=text, bg=color, fg=BG_ROOT,
                       font=FONT_MONO, padx=10, pady=5)
        lbl.pack()
        f.pack(side=side)
        hover = GREEN if color == GREEN_DIM else ("#cc0033" if color == RED else GREEN)
        for w in (f, lbl):
            w.bind("<Button-1>", lambda e, c=cmd: c())
            w.bind("<Enter>",  lambda e, fw=f, lw=lbl, h=hover:
                               (fw.config(bg=h),    lw.config(bg=h)))
            w.bind("<Leave>",  lambda e, fw=f, lw=lbl, orig=color:
                               (fw.config(bg=orig), lw.config(bg=orig)))
        return f, lbl
