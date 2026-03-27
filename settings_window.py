"""
VocalClear Settings — oscilloscope aesthetic.

Changes from original:
  • VU meter removed (it lives in the main window)
  • Checkbutton toggles replaced with explicit Label-based toggles (no BooleanVar)
  • Apply button highlights when a restart-requiring setting changes
  • Snaps to main window / soundboard via SnapManager
  • vocalclear.ico applied; dark title bar set before first paint (no flicker)
"""

import threading
import tkinter as tk
from pathlib import Path
from typing import Optional

import sounddevice as sd

from audio_engine import AudioEngine, find_vbcable_device, list_input_devices
from config import Config
from noise_filter import NoiseFilter


# ── Colour palette ─────────────────────────────────────────────────────────────
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
SILVER   = "#8899aa"

FG       = "#c8ffd4"
FG_DIM   = "#3a6642"
FG_MID   = "#6aaa7a"

FONT_MONO   = ("Consolas", 9)
FONT_MONO_M = ("Consolas", 10)
FONT_MONO_H = ("Consolas", 11, "bold")
FONT_MONO_L = ("Consolas", 8)

W, H = 540, 590


class SettingsWindow:
    def __init__(self, config: Config, noise_filter: NoiseFilter,
                 engine: AudioEngine, snapper=None):
        self.config       = config
        self.noise_filter = noise_filter
        self.engine       = engine
        self._snapper     = snapper

        # Baseline values — dirty tracking compares against these
        self._original_input_dev  = config["input_device"]
        self._original_excl_mode  = config.get("wasapi_exclusive", False)
        self._original_startup    = config.is_startup_enabled()
        self._original_strength   = config["strength"]
        self._original_gain       = config.get("output_gain", 1.0)

        self._root: Optional[tk.Tk] = None

        # Widgets / vars
        self._strength_var  : Optional[tk.DoubleVar]  = None
        self._pct_var       : Optional[tk.StringVar]  = None
        self._input_var     : Optional[tk.StringVar]  = None
        self._calib_btn     : Optional[tk.Label]      = None
        self._calib_status  : Optional[tk.StringVar]  = None
        self._apply_frame   : Optional[tk.Frame]      = None
        self._apply_lbl     : Optional[tk.Label]      = None
        self._apply_status  : Optional[tk.StringVar]  = None
        self._status_var    : Optional[tk.StringVar]  = None
        self._status_lbl    : Optional[tk.Label]      = None
        self._startup_btn   : Optional[tk.Label]      = None
        self._excl_btn      : Optional[tk.Label]      = None
        self._snap_bar_l    = None

        # Toggle states (explicit booleans — no BooleanVar)
        self._startup_state : bool = config.is_startup_enabled()
        self._excl_state    : bool = config.get("wasapi_exclusive", False)
        self._ptt_state     : bool = config.get("ptt_enabled", False)
        self._ptt_key_str   : str  = config.get("ptt_key", "")

        # Gain / PTT widgets
        self._gain_var      : Optional[tk.DoubleVar] = None
        self._gain_pct_var  : Optional[tk.StringVar] = None
        self._gain_canvas                            = None
        self._ptt_btn       : Optional[tk.Label]     = None
        self._ptt_key_lbl   : Optional[tk.Label]     = None

    # ──────────────────────────────────────────────────────────────────────────
    # Entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._root = tk.Tk()
        self._root.withdraw()           # hide until dark mode applied
        self._root.title("VocalClear — Settings")
        self._root.geometry(f"{W}x{H}")
        self._root.resizable(False, False)
        self._root.configure(bg=BG_ROOT)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Icon
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
        self._refresh_status()

        # Apply dark titlebar before first paint
        self._root.update_idletasks()
        self._apply_dark_titlebar()

        # Position to the right of main on first open
        if self._snapper:
            self._snapper.position_right_of("main", self._root)

        self._root.deiconify()

        if self._snapper:
            self._root.after(50, lambda: self._snapper.register("settings", self._root, snap_side="right-only"))
            self._root.after(300, self._tick_snap_bar)

        self._root.mainloop()

    # ──────────────────────────────────────────────────────────────────────────
    # Dark titlebar
    # ──────────────────────────────────────────────────────────────────────────

    def _apply_dark_titlebar(self) -> None:
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

        tk.Label(hdr, text="SETTINGS", bg=BG_ROOT, fg=GREEN,
                 font=("Consolas", 13, "bold")).pack(side="left")
        tk.Label(hdr, text="  ·  VOCALCLEAR", bg=BG_ROOT, fg=FG_DIM,
                 font=FONT_MONO).pack(side="left")

        self._status_var = tk.StringVar(value="● ACTIVE")
        self._status_lbl = tk.Label(
            hdr, textvariable=self._status_var,
            bg=BG_ROOT, fg=GREEN, font=FONT_MONO,
        )
        self._status_lbl.pack(side="right")

        tk.Frame(root, bg=GREEN_LO, height=1).pack(fill="x")

        # ── Engine / strength ─────────────────────────────────────────────────
        self._sep(root)
        engine_card = tk.Frame(root, bg=BG_CARD, padx=16, pady=10)
        engine_card.pack(fill="x", padx=16, pady=(6, 0))

        backend_map = {
            "deepfilter": ("DEEPFILTERNET 3",  GREEN,  "AI · best quality"),
            "rnnoise":    ("RNNOISE",           GREEN,  "AI · lightweight"),
            "wiener":     ("WIENER FILTER",     AMBER,  "install pyrnnoise for AI quality"),
            "none":       ("NO BACKEND",        RED,    "error"),
        }
        b_name, b_col, b_hint = backend_map.get(
            self.noise_filter.backend, ("UNKNOWN", RED, ""))

        row1 = tk.Frame(engine_card, bg=BG_CARD)
        row1.pack(fill="x")
        tk.Label(row1, text="ENGINE ", bg=BG_CARD, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")
        tk.Label(row1, text=b_name, bg=BG_CARD, fg=b_col,
                 font=FONT_MONO).pack(side="left")
        tk.Label(row1, text=f"  {b_hint}", bg=BG_CARD, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")
        tk.Label(row1, text=f"LATENCY  ~{self._latency_estimate()} ms",
                 bg=BG_CARD, fg=FG_MID, font=FONT_MONO_L).pack(side="right")

        # Strength bar
        row2 = tk.Frame(engine_card, bg=BG_CARD)
        row2.pack(fill="x", pady=(8, 0))
        tk.Label(row2, text="STRENGTH", bg=BG_CARD, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")

        self._pct_var = tk.StringVar(value=f"  {int(self.config['strength']*100):3d}%")
        tk.Label(row2, textvariable=self._pct_var, bg=BG_CARD, fg=GREEN,
                 font=FONT_MONO, width=5).pack(side="right")

        self._strength_var = tk.DoubleVar(value=self.config["strength"] * 100)
        self._strength_canvas = tk.Canvas(
            engine_card, height=20, bg=BG_INPUT, highlightthickness=0, cursor="hand2")
        self._strength_canvas.pack(fill="x", pady=(4, 0))
        self._strength_canvas.bind("<Button-1>",  self._on_strength_click)
        self._strength_canvas.bind("<B1-Motion>", self._on_strength_click)
        self._strength_canvas.bind("<Configure>",
                                   lambda e: self._draw_strength_bar())

        if self.noise_filter.backend == "wiener":
            calib_row = tk.Frame(engine_card, bg=BG_CARD)
            calib_row.pack(fill="x", pady=(8, 0))
            self._calib_btn = self._btn(
                calib_row, "[ CALIBRATE — stay quiet 3 s ]",
                self._do_calibrate, side="left",
            )
            self._calib_status = tk.StringVar(
                value="CALIBRATED" if self.noise_filter.is_calibrated else "NOT CALIBRATED"
            )
            tk.Label(calib_row, textvariable=self._calib_status,
                     bg=BG_CARD, fg=FG_MID, font=FONT_MONO_L,
                     ).pack(side="left", padx=(12, 0))

        # ── Input device ──────────────────────────────────────────────────────
        self._sep(root)
        dev_card = tk.Frame(root, bg=BG_CARD, padx=16, pady=10)
        dev_card.pack(fill="x", padx=16, pady=(6, 0))

        tk.Label(dev_card, text="INPUT DEVICE", bg=BG_CARD, fg=FG_DIM,
                 font=FONT_MONO_L).pack(anchor="w")

        inputs   = list_input_devices()
        in_names = ["System default"] + [d["name"] for d in inputs]
        self._input_map = {d["name"]: d["index"] for d in inputs}

        self._input_var = tk.StringVar()
        cur = self.config["input_device"]
        if cur is None:
            self._input_var.set("System default")
        else:
            try:
                self._input_var.set(sd.query_devices(cur)["name"])
            except Exception:
                self._input_var.set("System default")

        from dark_dropdown import DarkDropdown
        in_cb = DarkDropdown(
            dev_card, values=in_names,
            textvariable=self._input_var,
            command=self._on_device_changed,
            font=FONT_MONO,
        )
        in_cb.pack(fill="x", pady=(4, 0))

        vbc = find_vbcable_device()
        vbc_frame = tk.Frame(dev_card, bg=BG_CARD)
        vbc_frame.pack(fill="x", pady=(6, 0))
        if vbc is None:
            row = tk.Frame(vbc_frame, bg=BG_CARD)
            row.pack(anchor="w")
            tk.Label(row, text="⚠  VB-CABLE not found — install from ",
                     bg=BG_CARD, fg=AMBER, font=FONT_MONO_L).pack(side="left")
            link = tk.Label(row, text="vb-audio.com",
                            bg=BG_CARD, fg=GREEN, font=FONT_MONO_L,
                            cursor="hand2")
            link.pack(side="left")
            link.bind("<Button-1>", lambda e: __import__("webbrowser").open(
                "https://vb-audio.com/Cable/"))
            link.bind("<Enter>", lambda e: link.config(fg="#00ff87"))
            link.bind("<Leave>", lambda e: link.config(fg=GREEN))
            tk.Label(row, text=" then restart",
                     bg=BG_CARD, fg=AMBER, font=FONT_MONO_L).pack(side="left")
        else:
            dev_name = sd.query_devices(vbc)["name"]
            tk.Label(vbc_frame, text=f"●  Virtual mic:  {dev_name}",
                     bg=BG_CARD, fg=GREEN_DIM, font=FONT_MONO_L).pack(anchor="w")

        # ── System toggles ────────────────────────────────────────────────────
        self._sep(root)
        sys_card = tk.Frame(root, bg=BG_CARD, padx=16, pady=10)
        sys_card.pack(fill="x", padx=16, pady=(6, 0))

        # START WITH WINDOWS
        tog_row1 = tk.Frame(sys_card, bg=BG_CARD)
        tog_row1.pack(anchor="w")
        self._startup_btn = tk.Label(
            tog_row1,
            text=self._toggle_label("START WITH WINDOWS", self._startup_state),
            bg=GREEN if self._startup_state else BG_INPUT,
            fg=BG_ROOT if self._startup_state else FG_DIM,
            font=FONT_MONO_L, cursor="hand2", padx=8, pady=4,
        )
        self._startup_btn.pack(side="left")
        self._startup_btn.bind("<Button-1>", lambda e: self._on_startup_toggle())

        # WASAPI EXCLUSIVE MODE
        tog_row2 = tk.Frame(sys_card, bg=BG_CARD)
        tog_row2.pack(anchor="w", pady=(8, 0))
        self._excl_btn = tk.Label(
            tog_row2,
            text=self._toggle_label("WASAPI EXCLUSIVE MODE", self._excl_state),
            bg=GREEN if self._excl_state else BG_INPUT,
            fg=BG_ROOT if self._excl_state else FG_DIM,
            font=FONT_MONO_L, cursor="hand2", padx=8, pady=4,
        )
        self._excl_btn.pack(side="left")
        self._excl_btn.bind("<Button-1>", lambda e: self._on_excl_toggle())
        tk.Label(
            tog_row2,
            text="  ~3 ms · other apps cannot use mic while active · requires restart",
            bg=BG_CARD, fg=FG_DIM, font=FONT_MONO_L,
        ).pack(side="left")

        # ── Output gain ───────────────────────────────────────────────────────
        self._sep(root)
        gain_card = tk.Frame(root, bg=BG_CARD, padx=16, pady=10)
        gain_card.pack(fill="x", padx=16, pady=(6, 0))

        gain_row = tk.Frame(gain_card, bg=BG_CARD)
        gain_row.pack(fill="x")
        tk.Label(gain_row, text="OUTPUT GAIN", bg=BG_CARD, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")

        init_gain = self.config.get("output_gain", 1.0)
        # Map gain 0.5–3.0 → slider 0–100
        init_pct = (init_gain - 0.5) / 2.5 * 100
        self._gain_pct_var = tk.StringVar(value=f"  {init_gain:.2f}×")
        tk.Label(gain_row, textvariable=self._gain_pct_var, bg=BG_CARD, fg=GREEN,
                 font=FONT_MONO, width=7).pack(side="right")

        self._gain_var = tk.DoubleVar(value=init_pct)
        self._gain_canvas = tk.Canvas(
            gain_card, height=20, bg=BG_INPUT, highlightthickness=0, cursor="hand2")
        self._gain_canvas.pack(fill="x", pady=(4, 0))
        self._gain_canvas.bind("<Button-1>",  self._on_gain_click)
        self._gain_canvas.bind("<B1-Motion>", self._on_gain_click)
        self._gain_canvas.bind("<Configure>", lambda e: self._draw_gain_bar())

        # ── Push-to-talk ──────────────────────────────────────────────────────
        self._sep(root)
        ptt_card = tk.Frame(root, bg=BG_CARD, padx=16, pady=10)
        ptt_card.pack(fill="x", padx=16, pady=(6, 0))

        ptt_row1 = tk.Frame(ptt_card, bg=BG_CARD)
        ptt_row1.pack(anchor="w")
        self._ptt_btn = tk.Label(
            ptt_row1,
            text=self._toggle_label("PUSH-TO-TALK", self._ptt_state),
            bg=GREEN if self._ptt_state else BG_INPUT,
            fg=BG_ROOT if self._ptt_state else FG_DIM,
            font=FONT_MONO_L, cursor="hand2", padx=8, pady=4,
        )
        self._ptt_btn.pack(side="left")
        self._ptt_btn.bind("<Button-1>", lambda e: self._on_ptt_toggle())
        tk.Label(ptt_row1, text="  mic only passes while key is held",
                 bg=BG_CARD, fg=FG_DIM, font=FONT_MONO_L).pack(side="left")

        ptt_row2 = tk.Frame(ptt_card, bg=BG_CARD)
        ptt_row2.pack(anchor="w", pady=(8, 0))
        self._btn(ptt_row2, "[ SET PTT KEY ]", self._on_ptt_set_key, side="left")
        key_text = self._ptt_key_str if self._ptt_key_str else "— none —"
        self._ptt_key_lbl = tk.Label(
            ptt_row2, text=key_text, bg=BG_CARD, fg=GREEN, font=FONT_MONO,
            padx=10)
        self._ptt_key_lbl.pack(side="left")

        # ── Bottom action bar ─────────────────────────────────────────────────
        tk.Frame(root, bg=GREEN_LO, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(root, bg=BG_ROOT, pady=8, padx=16)
        bar.pack(fill="x", side="bottom")

        # Apply button — dim until a restart-requiring setting changes
        self._apply_frame = tk.Frame(bar, bg=GREEN_DIM, cursor="hand2")
        self._apply_lbl   = tk.Label(
            self._apply_frame,
            text="[ APPLY & RESTART AUDIO ]",
            bg=GREEN_DIM, fg=BG_ROOT, font=FONT_MONO, padx=10, pady=5,
        )
        self._apply_lbl.pack()
        self._apply_frame.pack(side="right")
        for w in (self._apply_frame, self._apply_lbl):
            w.bind("<Button-1>", lambda e: self._apply_and_restart())
            w.bind("<Enter>",  lambda e: self._apply_hover(True))
            w.bind("<Leave>",  lambda e: self._apply_hover(False))

        self._apply_status = tk.StringVar(value="")
        tk.Label(bar, textvariable=self._apply_status,
                 bg=BG_ROOT, fg=FG_MID, font=FONT_MONO_L).pack(side="right", padx=8)

        self._check_dirty()   # set initial Apply button state

        # ── Left snap bar (overlay) — lights up when snapped to main's right ──
        from snap_bar import SnapBar, BAR_WIDTH
        self._snap_bar_l = SnapBar(root)
        self._snap_bar_l.place(x=0, y=0, width=BAR_WIDTH, relheight=1.0)

    # ──────────────────────────────────────────────────────────────────────────
    # Strength bar
    # ──────────────────────────────────────────────────────────────────────────

    def _draw_strength_bar(self) -> None:
        c = self._strength_canvas
        c.update_idletasks()
        cw = c.winfo_width() or 500
        ch = 20
        pct = self._strength_var.get() / 100.0
        filled = int(cw * pct)
        c.delete("all")
        for x in range(0, cw, 12):
            c.create_line(x, 0, x, ch, fill=GRID_COL, width=1)
        c.create_rectangle(0, 3, filled, ch - 3, fill=GREEN_DIM, outline="")
        if filled > 4:
            c.create_rectangle(filled - 4, 3, filled, ch - 3, fill=GREEN, outline="")
        c.create_line(filled, 0, filled, ch, fill=GREEN, width=1)

    def _on_strength_click(self, event) -> None:
        cw = self._strength_canvas.winfo_width()
        if not cw:
            return
        pct = max(0.0, min(1.0, event.x / cw))
        self._strength_var.set(pct * 100)
        self._pct_var.set(f"  {int(pct*100):3d}%")
        self._draw_strength_bar()
        self.noise_filter.strength = pct
        self.config["strength"]    = pct
        self._check_dirty()

    # ──────────────────────────────────────────────────────────────────────────
    # Output gain bar
    # ──────────────────────────────────────────────────────────────────────────

    def _draw_gain_bar(self) -> None:
        c = self._gain_canvas
        if c is None:
            return
        c.update_idletasks()
        cw = c.winfo_width() or 500
        ch = 20
        pct = self._gain_var.get() / 100.0
        filled = int(cw * pct)
        # Draw a tick mark at the "1.0×" default position (pct = 20%)
        unity_x = int(cw * 0.20)
        c.delete("all")
        for x in range(0, cw, 12):
            c.create_line(x, 0, x, ch, fill=GRID_COL, width=1)
        c.create_rectangle(0, 3, filled, ch - 3, fill=GREEN_DIM, outline="")
        if filled > 4:
            c.create_rectangle(filled - 4, 3, filled, ch - 3, fill=GREEN, outline="")
        c.create_line(filled, 0, filled, ch, fill=GREEN, width=1)
        # Unity marker
        c.create_line(unity_x, 0, unity_x, ch, fill=AMBER, width=1, dash=(2, 2))

    def _on_gain_click(self, event) -> None:
        cw = self._gain_canvas.winfo_width()
        if not cw:
            return
        pct  = max(0.0, min(1.0, event.x / cw))
        gain = 0.5 + pct * 2.5        # map 0–1 → 0.5×–3.0×
        self._gain_var.set(pct * 100)
        self._gain_pct_var.set(f"  {gain:.2f}×")
        self._draw_gain_bar()
        self.config["output_gain"] = gain
        self._check_dirty()

    # ──────────────────────────────────────────────────────────────────────────
    # Dirty tracking
    # ──────────────────────────────────────────────────────────────────────────

    def _check_dirty(self) -> None:
        """Light up Apply button if any saved setting differs from the baseline."""
        new_input    = self._input_map.get(self._input_var.get(), None) if self._input_var else None
        new_excl     = self._excl_state
        new_startup  = self._startup_state
        new_strength = self._strength_var.get() / 100.0 if self._strength_var else self._original_strength
        new_gain     = 0.5 + (self._gain_var.get() / 100.0) * 2.5 if self._gain_var else self._original_gain
        dirty = (
            new_input    != self._original_input_dev
            or new_excl     != self._original_excl_mode
            or new_startup  != self._original_startup
            or abs(new_strength - self._original_strength) > 0.005
            or abs(new_gain - self._original_gain) > 0.005
        )
        if self._apply_frame:
            bg = GREEN if dirty else GREEN_DIM
            self._apply_frame.config(bg=bg)
            self._apply_lbl.config(bg=bg)

    def _apply_hover(self, entering: bool) -> None:
        """Hover effect only when button is lit (dirty)."""
        new_input    = self._input_map.get(self._input_var.get(), None) if self._input_var else None
        new_strength = self._strength_var.get() / 100.0 if self._strength_var else self._original_strength
        new_gain     = 0.5 + (self._gain_var.get() / 100.0) * 2.5 if self._gain_var else self._original_gain
        dirty = (
            new_input              != self._original_input_dev
            or self._excl_state    != self._original_excl_mode
            or self._startup_state != self._original_startup
            or abs(new_strength - self._original_strength) > 0.005
            or abs(new_gain - self._original_gain) > 0.005
        )
        if not dirty:
            return
        bg = "#00ff87" if entering else GREEN
        self._apply_frame.config(bg=bg)
        self._apply_lbl.config(bg=bg)

    # ──────────────────────────────────────────────────────────────────────────
    # Toggle helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _toggle_label(text: str, on: bool) -> str:
        return f"● {text}" if on else f"○ {text}"

    def _on_startup_toggle(self) -> None:
        self._startup_state = not self._startup_state
        self.config.set_startup(self._startup_state)
        self._startup_btn.config(
            text=self._toggle_label("START WITH WINDOWS", self._startup_state),
            bg=GREEN if self._startup_state else BG_INPUT,
            fg=BG_ROOT if self._startup_state else FG_DIM,
        )
        self._check_dirty()

    def _on_excl_toggle(self) -> None:
        self._excl_state = not self._excl_state
        self.config["wasapi_exclusive"] = self._excl_state
        self._excl_btn.config(
            text=self._toggle_label("WASAPI EXCLUSIVE MODE", self._excl_state),
            bg=GREEN if self._excl_state else BG_INPUT,
            fg=BG_ROOT if self._excl_state else FG_DIM,
        )
        self._check_dirty()

    def _on_ptt_toggle(self) -> None:
        self._ptt_state = not self._ptt_state
        self.config["ptt_enabled"] = self._ptt_state
        self._ptt_btn.config(
            text=self._toggle_label("PUSH-TO-TALK", self._ptt_state),
            bg=GREEN if self._ptt_state else BG_INPUT,
            fg=BG_ROOT if self._ptt_state else FG_DIM,
        )

    def _on_ptt_set_key(self) -> None:
        """Open a key-capture popup using GetAsyncKeyState polling."""
        if not self._root:
            return
        import ctypes as _ct
        import threading
        import time as _time
        import queue as _queue

        result: list = []
        stop_event = threading.Event()
        _q: _queue.Queue = _queue.Queue()

        W_POPUP = 340

        top = tk.Toplevel(self._root)
        top.withdraw()
        top.title("Set PTT Key")
        top.configure(bg=BG_ROOT)
        top.resizable(False, False)
        top.transient(self._root)

        ico = Path(__file__).parent / "vocalclear.ico"
        try:
            if ico.exists():
                top.iconbitmap(str(ico))
        except Exception:
            pass

        try:
            from window_snapper import _frame_hwnd
            hwnd = _frame_hwnd(top)
            _ct.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, _ct.byref(_ct.c_int(1)), _ct.sizeof(_ct.c_int))
        except Exception:
            pass

        # ── Header (same style as soundboard popup) ───────────────────────────
        tk.Frame(top, bg=GREEN, height=2).pack(fill="x")
        hdr = tk.Frame(top, bg=BG_ROOT, padx=14, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="PTT KEY", bg=BG_ROOT, fg=GREEN,
                 font=("Consolas", 10, "bold")).pack(side="left")
        tk.Label(hdr, text="  ·  VocalClear", bg=BG_ROOT, fg=FG_DIM,
                 font=FONT_MONO_L).pack(side="left")
        tk.Frame(top, bg=GREEN_LO, height=1).pack(fill="x")

        # ── Body ──────────────────────────────────────────────────────────────
        body = tk.Frame(top, bg=BG_ROOT, padx=16, pady=14)
        body.pack(fill="x")

        # Live key-capture area
        cap_frame = tk.Frame(body, bg=BG_INPUT,
                             highlightthickness=1, highlightbackground=GREEN_LO)
        cap_frame.pack(fill="x")
        cap_lbl = tk.Label(cap_frame, text="PRESS ANY KEY COMBINATION...",
                           bg=BG_INPUT, fg=FG_DIM, font=FONT_MONO_L, pady=10)
        cap_lbl.pack()

        tk.Label(body, text="Any key or modifier combination",
                 bg=BG_ROOT, fg=FG_DIM, font=FONT_MONO_L).pack(anchor="w", pady=(6, 0))

        # VK → display name table
        _VK_NAMES: dict = {
            **{0x41 + i: chr(0x41 + i) for i in range(26)},   # A-Z
            **{0x30 + i: str(i) for i in range(10)},           # 0-9
            0x70:"F1",  0x71:"F2",  0x72:"F3",  0x73:"F4",
            0x74:"F5",  0x75:"F6",  0x76:"F7",  0x77:"F8",
            0x78:"F9",  0x79:"F10", 0x7A:"F11", 0x7B:"F12",
            0x25:"LEFT", 0x27:"RIGHT", 0x26:"UP", 0x28:"DOWN",
            0x21:"PGUP", 0x22:"PGDN", 0x23:"END", 0x24:"HOME",
            0x2D:"INS",  0x2E:"DEL",  0x13:"PAUSE",
            0x20:"SPACE", 0x09:"TAB", 0x0D:"ENTER", 0x08:"BKSP",
            0xBC:"COMMA",   0xBE:"PERIOD",    0xBA:"SEMICOLON",
            0xBF:"SLASH",   0xDC:"BACKSLASH", 0xDB:"[",  0xDD:"]",
            0xDE:"QUOTE",   0xC0:"GRAVE",     0xBD:"-",  0xBB:"=",
            0x60:"NUM0", 0x61:"NUM1", 0x62:"NUM2", 0x63:"NUM3",
            0x64:"NUM4", 0x65:"NUM5", 0x66:"NUM6", 0x67:"NUM7",
            0x68:"NUM8", 0x69:"NUM9", 0x6A:"NUM*", 0x6B:"NUM+",
            0x6D:"NUM-", 0x6E:"NUM.", 0x6F:"NUM/",
        }
        # VK codes that are pure modifiers — skip these in the scan loop
        _MOD_VKS = frozenset({
            0x10, 0x11, 0x12,       # generic SHIFT / CTRL / ALT
            0xA0, 0xA1,             # L/R SHIFT
            0xA2, 0xA3,             # L/R CTRL
            0xA4, 0xA5,             # L/R ALT
            0x5B, 0x5C,             # L/R WIN
            0x14, 0x90, 0x91,       # CAPS / NUM / SCROLL LOCK
        })

        _captured: list = [None, None]   # [key_label, vk_code]

        def _update_display(label: str, vk: int) -> None:
            _captured[0] = label
            _captured[1] = vk
            cap_lbl.config(text=label, fg=GREEN)
            cap_frame.config(highlightbackground=GREEN)
            top.update_idletasks()

        def _cancel() -> None:
            stop_event.set()
            top.grab_release()
            top.destroy()

        def _save() -> None:
            if _captured[0]:
                result.append((_captured[0], _captured[1]))
            stop_event.set()
            top.grab_release()
            top.destroy()

        def _poll() -> None:
            GAS = _ct.windll.user32.GetAsyncKeyState
            _time.sleep(0.20)   # wait for the click that opened the popup to clear
            while not stop_event.is_set():
                shift = bool(GAS(0x10) & 0x8000)
                ctrl  = bool(GAS(0x11) & 0x8000)
                alt   = bool(GAS(0x12) & 0x8000)
                for vk in range(0x08, 0xFF):
                    if vk in _MOD_VKS:
                        continue
                    if GAS(vk) & 0x0001:    # transition bit: newly pressed since last call
                        if vk == 0x1B:      # Escape → cancel
                            _q.put(("cancel",))
                            return
                        name = _VK_NAMES.get(vk, f"VK{vk:02X}")
                        parts: list = []
                        if ctrl:  parts.append("CTRL")
                        if alt:   parts.append("ALT")
                        if shift: parts.append("SHIFT")
                        parts.append(name)
                        _q.put(("update", "+".join(parts), vk))
                _time.sleep(0.01)

        def _drain_queue() -> None:
            try:
                while True:
                    msg = _q.get_nowait()
                    if msg[0] == "update":
                        _update_display(msg[1], msg[2])
                    elif msg[0] == "cancel":
                        _cancel()
                        return
            except _queue.Empty:
                pass
            if not stop_event.is_set():
                top.after(30, _drain_queue)

        threading.Thread(target=_poll, daemon=True).start()
        top.after(30, _drain_queue)

        # ── Button bar (same style as soundboard popup) ───────────────────────
        tk.Frame(top, bg=GREEN_LO, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(top, bg=BG_ROOT, padx=14, pady=8)
        bar.pack(fill="x", side="bottom")

        def _mk_btn(parent, text, cmd, col):
            f   = tk.Frame(parent, bg=col, cursor="hand2")
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

        _mk_btn(bar, "SAVE",   _save,   GREEN_DIM)
        _mk_btn(bar, "CANCEL", _cancel, "#3a1010")

        top.protocol("WM_DELETE_WINDOW", _cancel)

        top.update_idletasks()
        ph = top.winfo_reqheight()
        rx = self._root.winfo_rootx() + (self._root.winfo_width()  - W_POPUP)  // 2
        ry = self._root.winfo_rooty() + (self._root.winfo_height() - ph) // 2
        top.geometry(f"{W_POPUP}x{ph}+{rx}+{ry}")
        top.deiconify()
        top.grab_set()
        top.focus_force()
        top.wait_window()
        stop_event.set()   # ensure thread stops if wait_window returned without _cancel/_save

        if result:
            label, vk = result[0]
            self._ptt_key_str = label
            self.config["ptt_key"] = label
            self.config["ptt_vk"]  = vk
            if self._ptt_key_lbl:
                self._ptt_key_lbl.config(text=label)

    def _on_device_changed(self) -> None:
        in_name = self._input_var.get()
        self.config["input_device"] = self._input_map.get(in_name, None)
        self._check_dirty()

    # ──────────────────────────────────────────────────────────────────────────
    # Calibrate / Apply
    # ──────────────────────────────────────────────────────────────────────────

    def _do_calibrate(self) -> None:
        if self._calib_btn:
            self._calib_btn.config(text="[ RECORDING 3 s — STAY QUIET ]")
        if self._calib_status:
            self._calib_status.set("CALIBRATING…")

        def on_done():
            if self._calib_btn:
                self._calib_btn.config(text="[ CALIBRATE — stay quiet 3 s ]")
            if self._calib_status:
                self._calib_status.set("CALIBRATED")

        self.engine.start_calibration(
            duration_s=3.0,
            done_cb=lambda: self._root.after(0, on_done) if self._root else None,
        )

    def _apply_and_restart(self) -> None:
        new_input   = self._input_map.get(self._input_var.get(), None)
        new_excl    = self._excl_state
        self.config["input_device"]     = new_input
        self.config["output_device"]    = None
        self.config["wasapi_exclusive"] = new_excl

        # Baseline resets so button dims after apply
        self._original_startup  = self._startup_state
        self._original_strength = self._strength_var.get() / 100.0 if self._strength_var else self._original_strength
        self._original_gain     = self.config.get("output_gain", 1.0)
        needs_restart = (
            new_input != self._original_input_dev
            or new_excl != self._original_excl_mode
        )
        if not needs_restart:
            self._original_input_dev = new_input
            self._original_excl_mode = new_excl
            self._apply_status.set("SAVED ✔")
            self._check_dirty()
            return

        self._original_input_dev = new_input
        self._original_excl_mode = new_excl

        if self._apply_lbl:
            self._apply_lbl.config(text="RESTARTING…")
        self._apply_status.set("")

        def _restart():
            try:
                self.engine.restart()
                status = f"ACTIVE  →  {self.engine.output_device_name}"
            except Exception:
                status = "ERR — quit and relaunch VocalClear"
            if self._root:
                self._root.after(0, lambda: (
                    self._apply_lbl.config(text="[ APPLY & RESTART AUDIO ]"),
                    self._apply_status.set(status),
                    self._refresh_status(),
                    self._check_dirty(),
                ) if self._apply_lbl else None)

        threading.Thread(target=_restart, daemon=True).start()

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _latency_estimate(self) -> str:
        import math as _math
        bs  = self.config["block_size"]
        sr  = 48000
        ms  = round((bs / sr) * 1000 * (2 if self.noise_filter.backend == "deepfilter" else 1))
        if self.config.get("wasapi_exclusive", False):
            ms = max(3, ms - 8)
        return str(ms)

    def _sep(self, parent) -> None:
        tk.Frame(parent, bg=GRID_COL, height=1).pack(fill="x", padx=16, pady=(8, 0))

    def _btn(self, parent, text: str, cmd, side="left") -> tk.Label:
        f   = tk.Frame(parent, bg=GREEN_DIM, cursor="hand2")
        lbl = tk.Label(f, text=text, bg=GREEN_DIM, fg=BG_ROOT,
                       font=FONT_MONO, padx=10, pady=5)
        lbl.pack()
        f.pack(side=side)
        for w in (f, lbl):
            w.bind("<Button-1>", lambda e, c=cmd: c())
            w.bind("<Enter>",  lambda e, fw=f, lw=lbl: (fw.config(bg=GREEN),     lw.config(bg=GREEN)))
            w.bind("<Leave>",  lambda e, fw=f, lw=lbl: (fw.config(bg=GREEN_DIM), lw.config(bg=GREEN_DIM)))
        return lbl

    def _refresh_status(self) -> None:
        if not self._root:
            return
        if self.noise_filter.enabled:
            self._status_var.set("● ACTIVE")
            self._status_lbl.config(fg=GREEN)
        else:
            self._status_var.set("⏸ PAUSED")
            self._status_lbl.config(fg=FG_DIM)

    def _tick_snap_bar(self) -> None:
        if not self._root or not self._snapper:
            return
        left, _ = self._snapper.get_snap_sides("settings")
        if self._snap_bar_l:
            self._snap_bar_l.set_active(left)
        self._root.after(200, self._tick_snap_bar)

    def _on_close(self) -> None:
        if self._snapper:
            self._snapper.unregister("settings")
        if self._root:
            self._root.destroy()
            self._root = None
