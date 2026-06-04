"""
VocalClear Settings — PySide6, oscilloscope aesthetic.

Runs on the Qt main thread as a QMainWindow (shown/hidden, never recreated).
All sliders are custom-painted QWidgets matching the app palette.
"""

from __future__ import annotations

import datetime
import threading
from pathlib import Path
from typing import Optional, Callable, TYPE_CHECKING

import sounddevice as sd

from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QSize
from PySide6.QtGui import (
    QColor, QPainter, QPen, QFont, QFontMetrics, QPalette, QIcon,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QComboBox, QSizePolicy, QApplication,
)

from audio_engine import AudioEngine, find_vbcable_device, list_input_devices
from config import Config
from noise_filter import NoiseFilter

_LOG = Path.home() / ".vocalclear" / "vocalclear.log"


def _log(msg: str) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass

if TYPE_CHECKING:
    from window_snapper import SnapManager

# ── Palette ───────────────────────────────────────────────────────────────────
C_BG_ROOT  = QColor("#030603")
C_BG_CARD  = QColor("#0b160b")
C_BG_INPUT = QColor("#0f1f0f")
C_GRID     = QColor("#0d1f0d")
C_GREEN    = QColor("#00e676")
C_GREEN_DIM= QColor("#007a40")
C_GREEN_LO = QColor("#004d28")
C_AMBER    = QColor("#ffb300")
C_RED      = QColor("#ff1744")
C_FG       = QColor("#c8ffd4")
C_FG_DIM   = QColor("#3a6642")
C_FG_MID   = QColor("#6aaa7a")

FONT_MONO   = QFont("Consolas", 9)
FONT_MONO_H = QFont("Consolas", 13); FONT_MONO_H.setBold(True)
FONT_MONO_L = QFont("Consolas", 8)

W, H = 540, 590


def _apply_dark_titlebar(hwnd: int) -> None:
    import ctypes
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


def _hdivider(color: QColor = C_GREEN_LO, margin: int = 0) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet(f"border: none; background: {color.name()}; max-height: 1px;")
    if margin:
        line.setContentsMargins(margin, 0, margin, 0)
    return line


# ── Custom painted bar slider ─────────────────────────────────────────────────

class _BarSlider(QWidget):
    """
    Horizontal click/drag slider rendered as a filled bar with a bright tip —
    matches the strength/gain bars from the tkinter version exactly.
    """

    def __init__(self, value: float = 0.5, parent=None):
        super().__init__(parent)
        self._value   = max(0.0, min(1.0, value))
        self._on_change: Optional[Callable[[float], None]] = None
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, v: float) -> None:
        self._value = max(0.0, min(1.0, v))
        self.update()

    def set_on_change(self, cb: Callable[[float], None]) -> None:
        self._on_change = cb

    def paintEvent(self, _):
        p   = QPainter(self)
        cw  = self.width()
        ch  = self.height()
        pct = self._value

        p.fillRect(self.rect(), C_BG_INPUT)

        # Grid
        p.setPen(QPen(C_GRID, 1))
        for x in range(0, cw, 12):
            p.drawLine(x, 0, x, ch)

        filled = max(1, int(cw * pct))
        p.fillRect(QRect(0, 3, filled, ch - 6), C_GREEN_DIM)
        if filled > 4:
            p.fillRect(QRect(filled - 4, 3, 4, ch - 6), C_GREEN)

        p.setPen(QPen(C_GREEN, 1))
        p.drawLine(filled, 0, filled, ch)

    def _set_from_x(self, x: int) -> None:
        cw = self.width()
        if not cw:
            return
        v = max(0.0, min(1.0, x / cw))
        self._value = v
        self.update()
        if self._on_change:
            self._on_change(v)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._set_from_x(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_x(e.position().x())


class _GainSlider(_BarSlider):
    """Bar slider that also draws a unity (1.0×) tick mark."""

    def paintEvent(self, _):
        super().paintEvent(_)
        p      = QPainter(self)
        cw     = self.width()
        ch     = self.height()
        unity  = int(cw * 0.20)   # gain 0.5 at 0%, unity (1.0×) at 20%
        p.setPen(QPen(C_AMBER, 1, Qt.PenStyle.DashLine))
        p.drawLine(unity, 0, unity, ch)


# ── Toggle button ─────────────────────────────────────────────────────────────

class _ToggleBtn(QLabel):
    """Label-style toggle that lights up when on."""

    def __init__(self, text: str, state: bool, parent=None):
        super().__init__(parent)
        self._text     = text
        self._state    = state
        self._callback: Optional[Callable] = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(FONT_MONO_L)
        self._refresh()

    def set_callback(self, cb: Callable) -> None:
        self._callback = cb

    @property
    def state(self) -> bool:
        return self._state

    @state.setter
    def state(self, v: bool) -> None:
        self._state = v
        self._refresh()

    def _refresh(self) -> None:
        dot = "●" if self._state else "○"
        self.setText(f" {dot} {self._text} ")
        if self._state:
            self.setStyleSheet(
                "background: #00e676; color: #030603; padding: 4px 8px;")
        else:
            self.setStyleSheet(
                "background: #0f1f0f; color: #3a6642; padding: 4px 8px;")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._callback:
            self._callback()


# ── Glow button (re-used from main_window style) ──────────────────────────────

class _GlowButton(QWidget):
    def __init__(self, text: str, callback: Callable,
                 color: QColor = C_GREEN_DIM, parent=None):
        super().__init__(parent)
        self._text        = text
        self._callback    = callback
        self._base_color  = color
        self._hovered     = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_text(self, t: str) -> None:
        self._text = t; self.update()

    def set_color(self, c: QColor) -> None:
        self._base_color = c; self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(FONT_MONO)
        return QSize(fm.horizontalAdvance(self._text) + 22, fm.height() + 12)

    def paintEvent(self, _):
        p   = QPainter(self)
        col = self._base_color.lighter(130) if self._hovered else self._base_color
        p.fillRect(self.rect(), col)
        p.setFont(FONT_MONO)
        p.setPen(C_BG_ROOT)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)

    def enterEvent(self, _): self._hovered = True;  self.update()
    def leaveEvent(self, _): self._hovered = False; self.update()
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self._callback()


# ── Dropdown (QComboBox styled) ───────────────────────────────────────────────

_COMBO_QSS = """
QComboBox {
    background: #0f1f0f;
    color: #c8ffd4;
    border: 1px solid #004d28;
    padding: 5px 10px;
    font-family: Consolas;
    font-size: 9pt;
}
QComboBox:hover { border-color: #00e676; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #007a40;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background: #0b160b;
    color: #c8ffd4;
    border: 1px solid #007a40;
    selection-background-color: #007a40;
    selection-color: #030603;
    font-family: Consolas;
    font-size: 9pt;
    outline: none;
}
QComboBox QAbstractItemView::item { padding: 4px 10px; }
"""


# ── PTT key capture popup ─────────────────────────────────────────────────────

class _PTTCaptureDialog(QMainWindow):
    """Non-modal popup to capture a PTT key combination."""

    _VK_NAMES: dict = {
        **{0x41 + i: chr(0x41 + i) for i in range(26)},
        **{0x30 + i: str(i) for i in range(10)},
        0x70:"F1",  0x71:"F2",  0x72:"F3",  0x73:"F4",
        0x74:"F5",  0x75:"F6",  0x76:"F7",  0x77:"F8",
        0x78:"F9",  0x79:"F10", 0x7A:"F11", 0x7B:"F12",
        0x25:"LEFT",0x27:"RIGHT",0x26:"UP", 0x28:"DOWN",
        0x20:"SPACE",0x0D:"ENTER",0x09:"TAB",0x2E:"DEL",
        0x2D:"INS", 0x23:"END", 0x24:"HOME",
    }
    _MOD_VKS = frozenset({
        0x10,0x11,0x12,0xA0,0xA1,0xA2,0xA3,0xA4,0xA5,0x5B,0x5C,0x14,0x90,0x91,
    })

    def __init__(self, parent, on_captured: Callable, on_cancel: Callable):
        super().__init__(parent)
        self._on_captured = on_captured
        self._on_cancel   = on_cancel
        self._stop        = threading.Event()
        self._captured: Optional[tuple[str, int]] = None

        self.setWindowTitle("PTT Key")
        self.setFixedSize(340, 160)
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #030603; }"
        )
        _apply_dark_titlebar(int(self.winId()))

        ico = Path(__file__).parent / "vocalclear.ico"
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))

        central = QWidget()
        self.setCentralWidget(central)
        lo = QVBoxLayout(central)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        # Accent
        acc = QWidget(); acc.setFixedHeight(2)
        acc.setStyleSheet("background: #00e676;"); lo.addWidget(acc)

        # Header
        hdr = QWidget()
        hdr_lo = QHBoxLayout(hdr); hdr_lo.setContentsMargins(14, 8, 14, 8)
        t = QLabel("PTT KEY"); t.setFont(FONT_MONO_H); t.setStyleSheet("color: #00e676;")
        s = QLabel("  ·  VocalClear"); s.setFont(FONT_MONO_L); s.setStyleSheet("color: #3a6642;")
        hdr_lo.addWidget(t); hdr_lo.addWidget(s); hdr_lo.addStretch()
        lo.addWidget(hdr)
        lo.addWidget(_hdivider(C_GREEN_LO))

        # Body
        body = QWidget()
        body_lo = QVBoxLayout(body); body_lo.setContentsMargins(16, 12, 16, 12)
        self._cap_lbl = QLabel("PRESS ANY KEY COMBINATION…")
        self._cap_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cap_lbl.setFont(FONT_MONO_L)
        self._cap_lbl.setStyleSheet(
            "color: #3a6642; background: #0f1f0f; padding: 8px; border: 1px solid #004d28;")
        body_lo.addWidget(self._cap_lbl)
        hint = QLabel("Any key or modifier combination")
        hint.setFont(FONT_MONO_L); hint.setStyleSheet("color: #3a6642;")
        body_lo.addWidget(hint)
        lo.addWidget(body)

        # Bottom bar
        lo.addWidget(_hdivider(C_GREEN_LO))
        bar = QWidget()
        bar_lo = QHBoxLayout(bar); bar_lo.setContentsMargins(14, 8, 14, 8)
        bar_lo.addStretch()
        cancel_btn = _GlowButton("[ CANCEL ]", self._do_cancel, QColor("#3a1010"))
        save_btn   = _GlowButton("[ SAVE ]",   self._do_save,   C_GREEN_DIM)
        bar_lo.addWidget(cancel_btn); bar_lo.addWidget(save_btn)
        lo.addWidget(bar)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._drain)
        self._q: list = []
        self._lock = threading.Lock()

        # Start capture thread after a short delay (clear the click that opened us)
        QTimer.singleShot(200, self._start_capture)

    def _start_capture(self) -> None:
        threading.Thread(target=self._capture_loop, daemon=True).start()
        self._poll_timer.start(30)

    def _capture_loop(self) -> None:
        import ctypes as _ct, time as _t
        GAS = _ct.windll.user32.GetAsyncKeyState
        _t.sleep(0.10)
        while not self._stop.is_set():
            shift = bool(GAS(0x10) & 0x8000)
            ctrl  = bool(GAS(0x11) & 0x8000)
            alt   = bool(GAS(0x12) & 0x8000)
            for vk in range(0x08, 0xFF):
                if vk in self._MOD_VKS:
                    continue
                if GAS(vk) & 0x0001:
                    if vk == 0x1B:
                        with self._lock: self._q.append(("cancel",))
                        return
                    name  = self._VK_NAMES.get(vk, f"VK{vk:02X}")
                    parts = []
                    if ctrl:  parts.append("CTRL")
                    if alt:   parts.append("ALT")
                    if shift: parts.append("SHIFT")
                    parts.append(name)
                    with self._lock: self._q.append(("key", "+".join(parts), vk))
            _t.sleep(0.01)

    def _drain(self) -> None:
        with self._lock:
            msgs = list(self._q); self._q.clear()
        for msg in msgs:
            if msg[0] == "cancel":
                self._do_cancel(); return
            elif msg[0] == "key":
                self._captured = (msg[1], msg[2])
                self._cap_lbl.setText(msg[1])
                self._cap_lbl.setStyleSheet(
                    "color: #00e676; background: #0f1f0f; "
                    "padding: 8px; border: 1px solid #00e676;")

    def _do_save(self) -> None:
        self._stop.set(); self._poll_timer.stop()
        if self._captured:
            self._on_captured(*self._captured)
        self.close()

    def _do_cancel(self) -> None:
        self._stop.set(); self._poll_timer.stop()
        self._on_cancel()
        self.close()

    def closeEvent(self, e):
        self._stop.set(); self._poll_timer.stop()
        e.accept()


# ── Settings window ───────────────────────────────────────────────────────────

class SettingsWindow(QMainWindow):
    def __init__(self, config: Config, noise_filter: NoiseFilter,
                 engine: AudioEngine, snapper: "SnapManager" = None,
                 on_close: Optional[Callable] = None):
        super().__init__()
        self.config       = config
        self.noise_filter = noise_filter
        self.engine       = engine
        self._snapper     = snapper
        self._on_close_cb = on_close

        # Baseline for dirty tracking
        self._orig_input_dev = config["input_device"]
        self._orig_excl_mode = config.get("wasapi_exclusive", False)
        self._orig_startup   = config.is_startup_enabled()
        self._orig_strength  = config["strength"]
        self._orig_gain      = config.get("output_gain", 1.0)

        # Widget refs
        self._status_lbl:    Optional[QLabel]       = None
        self._strength_bar:  Optional[_BarSlider]   = None
        self._strength_val:  Optional[QLabel]       = None
        self._gain_bar:      Optional[_GainSlider]  = None
        self._gain_val:      Optional[QLabel]       = None
        self._input_combo:   Optional[QComboBox]    = None
        self._startup_btn:   Optional[_ToggleBtn]   = None
        self._excl_btn:      Optional[_ToggleBtn]   = None
        self._ptt_btn:       Optional[_ToggleBtn]   = None
        self._ptt_key_lbl:   Optional[QLabel]       = None
        self._apply_btn:     Optional[_GlowButton]  = None
        self._apply_status:  Optional[QLabel]       = None
        self._calib_btn:     Optional[_GlowButton]  = None
        self._calib_status:  Optional[QLabel]       = None

        self._input_map: dict[str, Optional[int]] = {}
        self._ptt_key_str = config.get("ptt_key", "")

        self.setWindowTitle("VocalClear — Settings")
        self.setFixedSize(W, H)

        ico = Path(__file__).parent / "vocalclear.ico"
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))

        self.setStyleSheet(
            "QMainWindow, QWidget { background: #030603; color: #c8ffd4; }")

        self._build_ui()
        _apply_dark_titlebar(int(self.winId()))

        if self._snapper:
            QTimer.singleShot(50, lambda: self._snapper.register(
                "settings", self, snap_side="right-only"))

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Accent
        acc = QWidget(); acc.setFixedHeight(2)
        acc.setStyleSheet("background: #00e676;")
        root.addWidget(acc)

        # Header
        hdr = QWidget()
        hdr_lo = QHBoxLayout(hdr); hdr_lo.setContentsMargins(16, 8, 16, 8)
        t = QLabel("SETTINGS"); t.setFont(FONT_MONO_H); t.setStyleSheet("color: #00e676;")
        s = QLabel("  ·  VOCALCLEAR"); s.setFont(FONT_MONO); s.setStyleSheet("color: #3a6642;")
        self._status_lbl = QLabel("● ACTIVE")
        self._status_lbl.setFont(FONT_MONO)
        self._status_lbl.setStyleSheet("color: #00e676;")
        hdr_lo.addWidget(t); hdr_lo.addWidget(s); hdr_lo.addStretch()
        hdr_lo.addWidget(self._status_lbl)
        root.addWidget(hdr)
        root.addWidget(_hdivider(C_GREEN_LO))

        scroll_area = QWidget()
        scroll_lo = QVBoxLayout(scroll_area)
        scroll_lo.setContentsMargins(0, 0, 0, 0)
        scroll_lo.setSpacing(0)

        # ── Engine card ───────────────────────────────────────────────────────
        scroll_lo.addWidget(_hdivider(C_GRID, margin=16))
        eng = self._card(scroll_lo)

        backend_map = {
            "deepfilter": ("DEEPFILTERNET 3", "#00e676", "AI · best quality"),
            "rnnoise":    ("RNNOISE",          "#00e676", "AI · lightweight"),
            "wiener":     ("WIENER FILTER",    "#ffb300", "install pyrnnoise for AI quality"),
            "none":       ("NO BACKEND",       "#ff1744", "error"),
        }
        b_name, b_col, b_hint = backend_map.get(
            self.noise_filter.backend, ("UNKNOWN", "#ff1744", ""))

        r1 = QHBoxLayout(); r1.setSpacing(0)
        _lbl(r1, "ENGINE ", FONT_MONO_L, "#3a6642")
        _lbl(r1, b_name,   FONT_MONO,   b_col)
        _lbl(r1, f"  {b_hint}", FONT_MONO_L, "#3a6642")
        r1.addStretch()
        lat = QLabel(f"LATENCY  ~{self._latency_estimate()} ms")
        lat.setFont(FONT_MONO_L); lat.setStyleSheet("color: #6aaa7a;")
        r1.addWidget(lat)
        eng.addLayout(r1)

        # Strength
        r2 = QHBoxLayout(); r2.setSpacing(0)
        _lbl(r2, "STRENGTH", FONT_MONO_L, "#3a6642")
        r2.addStretch()
        self._strength_val = QLabel(f"  {int(self.config['strength']*100):3d}%")
        self._strength_val.setFont(FONT_MONO); self._strength_val.setStyleSheet("color: #00e676;")
        r2.addWidget(self._strength_val)
        eng.addLayout(r2)

        self._strength_bar = _BarSlider(value=self.config["strength"])
        self._strength_bar.set_on_change(self._on_strength)
        eng.addWidget(self._strength_bar)

        if self.noise_filter.backend == "wiener":
            cr = QHBoxLayout()
            self._calib_btn = _GlowButton(
                "[ CALIBRATE — stay quiet 3 s ]", self._do_calibrate)
            self._calib_status = QLabel(
                "CALIBRATED" if self.noise_filter.is_calibrated else "NOT CALIBRATED")
            self._calib_status.setFont(FONT_MONO_L)
            self._calib_status.setStyleSheet("color: #6aaa7a;")
            cr.addWidget(self._calib_btn); cr.addWidget(self._calib_status)
            cr.addStretch(); eng.addLayout(cr)

        # ── Input device ──────────────────────────────────────────────────────
        scroll_lo.addWidget(_hdivider(C_GRID, margin=16))
        dev = self._card(scroll_lo)
        _lbl_direct(dev, "INPUT DEVICE", FONT_MONO_L, "#3a6642")

        inputs   = list_input_devices()
        in_names = ["System default"] + [d["name"] for d in inputs]
        self._input_map = {"System default": None}
        self._input_map.update({d["name"]: d["index"] for d in inputs})

        self._input_combo = QComboBox()
        self._input_combo.setStyleSheet(_COMBO_QSS)
        self._input_combo.addItems(in_names)
        cur = self.config["input_device"]
        if cur is None:
            self._input_combo.setCurrentText("System default")
        else:
            try:
                self._input_combo.setCurrentText(sd.query_devices(cur)["name"])
            except Exception:
                self._input_combo.setCurrentIndex(0)
        self._input_combo.currentTextChanged.connect(self._on_device_changed)
        dev.addWidget(self._input_combo)

        vbc = find_vbcable_device()
        if vbc is None:
            vbc_row = QHBoxLayout(); vbc_row.setSpacing(0)
            _lbl(vbc_row, "⚠  VB-CABLE not found — install from ", FONT_MONO_L, "#ffb300")
            link = QLabel('<a href="https://vb-audio.com/Cable/" '
                          'style="color:#00e676;">vb-audio.com</a>')
            link.setFont(FONT_MONO_L); link.setOpenExternalLinks(True)
            vbc_row.addWidget(link)
            _lbl(vbc_row, " then restart", FONT_MONO_L, "#ffb300")
            vbc_row.addStretch(); dev.addLayout(vbc_row)
        else:
            dev_name = sd.query_devices(vbc)["name"]
            _lbl_direct(dev, f"●  Virtual mic:  {dev_name}", FONT_MONO_L, "#007a40")

        # ── System toggles ────────────────────────────────────────────────────
        scroll_lo.addWidget(_hdivider(C_GRID, margin=16))
        sys_c = self._card(scroll_lo)

        self._startup_btn = _ToggleBtn("START WITH WINDOWS",
                                       self.config.is_startup_enabled())
        self._startup_btn.set_callback(self._on_startup_toggle)
        sys_c.addWidget(self._startup_btn)

        excl_row = QHBoxLayout(); excl_row.setSpacing(8)
        self._excl_btn = _ToggleBtn("WASAPI EXCLUSIVE MODE",
                                    self.config.get("wasapi_exclusive", False))
        self._excl_btn.set_callback(self._on_excl_toggle)
        excl_row.addWidget(self._excl_btn)
        hint = QLabel("~3 ms · other apps cannot use mic · requires restart")
        hint.setFont(FONT_MONO_L); hint.setStyleSheet("color: #3a6642;")
        excl_row.addWidget(hint); excl_row.addStretch()
        sys_c.addLayout(excl_row)

        # ── Output gain ───────────────────────────────────────────────────────
        scroll_lo.addWidget(_hdivider(C_GRID, margin=16))
        gain_c = self._card(scroll_lo)
        g_row = QHBoxLayout(); g_row.setSpacing(0)
        _lbl(g_row, "OUTPUT GAIN", FONT_MONO_L, "#3a6642"); g_row.addStretch()
        self._gain_val = QLabel(f"  {self.config.get('output_gain', 1.0):.2f}×")
        self._gain_val.setFont(FONT_MONO); self._gain_val.setStyleSheet("color: #00e676;")
        g_row.addWidget(self._gain_val); gain_c.addLayout(g_row)

        init_gain = self.config.get("output_gain", 1.0)
        init_pct  = (init_gain - 0.5) / 2.5
        self._gain_bar = _GainSlider(value=init_pct)
        self._gain_bar.set_on_change(self._on_gain)
        gain_c.addWidget(self._gain_bar)

        # ── Push-to-talk ──────────────────────────────────────────────────────
        scroll_lo.addWidget(_hdivider(C_GRID, margin=16))
        ptt_c = self._card(scroll_lo)

        ptt_r1 = QHBoxLayout(); ptt_r1.setSpacing(8)
        self._ptt_btn = _ToggleBtn("PUSH-TO-TALK",
                                   self.config.get("ptt_enabled", False))
        self._ptt_btn.set_callback(self._on_ptt_toggle)
        ptt_r1.addWidget(self._ptt_btn)
        _lbl(ptt_r1, "mic only passes while key is held", FONT_MONO_L, "#3a6642")
        ptt_r1.addStretch(); ptt_c.addLayout(ptt_r1)

        ptt_r2 = QHBoxLayout(); ptt_r2.setSpacing(8)
        set_key_btn = _GlowButton("[ SET PTT KEY ]", self._on_ptt_set_key)
        ptt_r2.addWidget(set_key_btn)
        key_text = self._ptt_key_str if self._ptt_key_str else "— none —"
        self._ptt_key_lbl = QLabel(key_text)
        self._ptt_key_lbl.setFont(FONT_MONO); self._ptt_key_lbl.setStyleSheet("color: #00e676;")
        ptt_r2.addWidget(self._ptt_key_lbl); ptt_r2.addStretch()
        ptt_c.addLayout(ptt_r2)

        scroll_lo.addStretch()
        root.addWidget(scroll_area, stretch=1)

        # ── Bottom action bar ─────────────────────────────────────────────────
        root.addWidget(_hdivider(C_GREEN_LO))
        bar = QWidget()
        bar_lo = QHBoxLayout(bar); bar_lo.setContentsMargins(16, 8, 16, 8)
        self._apply_status = QLabel("")
        self._apply_status.setFont(FONT_MONO_L)
        self._apply_status.setStyleSheet("color: #6aaa7a;")
        bar_lo.addWidget(self._apply_status)
        bar_lo.addStretch()
        self._apply_btn = _GlowButton("[ APPLY & RESTART AUDIO ]",
                                      self._apply_and_restart, C_GREEN_DIM)
        bar_lo.addWidget(self._apply_btn)
        root.addWidget(bar)

        self._refresh_status()
        self._check_dirty()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _card(self, parent_layout: QVBoxLayout) -> QVBoxLayout:
        card = QWidget()
        card.setAutoFillBackground(True)
        pal = card.palette(); pal.setColor(QPalette.ColorRole.Window, C_BG_CARD)
        card.setPalette(pal)
        lo = QVBoxLayout(card)
        lo.setContentsMargins(16, 10, 16, 10)
        lo.setSpacing(6)
        wrap = QWidget()
        wrap_lo = QVBoxLayout(wrap)
        wrap_lo.setContentsMargins(16, 6, 16, 0); wrap_lo.setSpacing(0)
        wrap_lo.addWidget(card)
        parent_layout.addWidget(wrap)
        return lo

    def _latency_estimate(self) -> str:
        bs  = self.config["block_size"]
        sr  = 48000
        ms  = round((bs / sr) * 1000 * (2 if self.noise_filter.backend == "deepfilter" else 1))
        if self.config.get("wasapi_exclusive", False):
            ms = max(3, ms - 8)
        return str(ms)

    def _refresh_status(self) -> None:
        if not self._status_lbl:
            return
        if self.noise_filter.enabled:
            self._status_lbl.setText("● ACTIVE"); self._status_lbl.setStyleSheet("color: #00e676;")
        else:
            self._status_lbl.setText("⏸ PAUSED"); self._status_lbl.setStyleSheet("color: #3a6642;")

    def _check_dirty(self) -> None:
        new_input    = self._input_map.get(self._input_combo.currentText() if self._input_combo else "System default", None)
        new_excl     = self._excl_btn.state if self._excl_btn else False
        new_startup  = self._startup_btn.state if self._startup_btn else False
        new_strength = self._strength_bar.value if self._strength_bar else self._orig_strength
        new_gain     = 0.5 + (self._gain_bar.value * 2.5) if self._gain_bar else self._orig_gain
        dirty = (
            new_input   != self._orig_input_dev
            or new_excl != self._orig_excl_mode
            or new_startup != self._orig_startup
            or abs(new_strength - self._orig_strength) > 0.005
            or abs(new_gain - self._orig_gain) > 0.005
        )
        if self._apply_btn:
            self._apply_btn.set_color(C_GREEN if dirty else C_GREEN_DIM)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_strength(self, v: float) -> None:
        self.noise_filter.strength = v
        self.config["strength"] = v
        if self._strength_val:
            self._strength_val.setText(f"  {int(v*100):3d}%")
        self._check_dirty()

    def _on_gain(self, v: float) -> None:
        gain = 0.5 + v * 2.5
        self.config["output_gain"] = gain
        if self._gain_val:
            self._gain_val.setText(f"  {gain:.2f}×")
        self._check_dirty()

    def _on_device_changed(self) -> None:
        name = self._input_combo.currentText() if self._input_combo else "System default"
        self.config["input_device"] = self._input_map.get(name, None)
        self._check_dirty()

    def _on_startup_toggle(self) -> None:
        if self._startup_btn:
            new_state = not self._startup_btn.state
            self.config.set_startup(new_state)
            self._startup_btn.state = new_state
            self._check_dirty()

    def _on_excl_toggle(self) -> None:
        if self._excl_btn:
            new_state = not self._excl_btn.state
            self.config["wasapi_exclusive"] = new_state
            self._excl_btn.state = new_state
            self._check_dirty()

    def _on_ptt_toggle(self) -> None:
        if self._ptt_btn:
            new_state = not self._ptt_btn.state
            self.config["ptt_enabled"] = new_state
            self._ptt_btn.state = new_state

    def _on_ptt_set_key(self) -> None:
        def _captured(label: str, vk: int) -> None:
            self._ptt_key_str = label
            self.config["ptt_key"] = label
            self.config["ptt_vk"]  = vk
            if self._ptt_key_lbl:
                self._ptt_key_lbl.setText(label)

        dlg = _PTTCaptureDialog(self, on_captured=_captured, on_cancel=lambda: None)
        dlg.show()

    def _do_calibrate(self) -> None:
        if self._calib_btn:
            self._calib_btn.set_text("[ RECORDING 3 s — STAY QUIET ]")
        if self._calib_status:
            self._calib_status.setText("CALIBRATING…")

        def _done():
            if self._calib_btn:
                QTimer.singleShot(0, lambda: self._calib_btn.set_text(
                    "[ CALIBRATE — stay quiet 3 s ]"))
            if self._calib_status:
                QTimer.singleShot(0, lambda: self._calib_status.setText("CALIBRATED"))

        self.engine.start_calibration(duration_s=3.0, done_cb=_done)

    def _apply_and_restart(self) -> None:
        new_input = self._input_map.get(
            self._input_combo.currentText() if self._input_combo else "System default", None)
        new_excl  = self._excl_btn.state if self._excl_btn else False

        self.config["input_device"]     = new_input
        self.config["output_device"]    = None
        self.config["wasapi_exclusive"] = new_excl

        # Reset baselines
        self._orig_startup  = self._startup_btn.state if self._startup_btn else False
        self._orig_strength = self._strength_bar.value if self._strength_bar else self._orig_strength
        self._orig_gain     = self.config.get("output_gain", 1.0)

        needs_restart = (new_input != self._orig_input_dev or new_excl != self._orig_excl_mode)
        if not needs_restart:
            self._orig_input_dev = new_input
            self._orig_excl_mode = new_excl
            if self._apply_status: self._apply_status.setText("SAVED ✔")
            self._check_dirty(); return

        self._orig_input_dev = new_input
        self._orig_excl_mode = new_excl

        if self._apply_btn: self._apply_btn.set_text("RESTARTING…")

        def _restart():
            try:
                self.engine.restart()
                status = f"ACTIVE  →  {self.engine.output_device_name}"
                _log(f"Engine restarted  output={self.engine.output_device_name!r}")
            except Exception as exc:
                status = "ERR — quit and relaunch VocalClear"
                _log(f"Engine restart failed: {exc}")
            QTimer.singleShot(0, lambda: (
                self._apply_btn.set_text("[ APPLY & RESTART AUDIO ]") if self._apply_btn else None,
                self._apply_status.setText(status) if self._apply_status else None,
                self._refresh_status(),
                self._check_dirty(),
            ))

        threading.Thread(target=_restart, daemon=True).start()

    # ── Window events ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._snapper:
            self._snapper.unregister("settings")
        if self._on_close_cb:
            self._on_close_cb()
        event.accept()

    def showEvent(self, event) -> None:
        self._refresh_status()
        self._check_dirty()
        if self._snapper:
            self._snapper.position_right_of("main", self)
        event.accept()


# ── Label helpers ─────────────────────────────────────────────────────────────

def _lbl(layout: QHBoxLayout, text: str, font: QFont, color: str) -> QLabel:
    w = QLabel(text); w.setFont(font); w.setStyleSheet(f"color: {color};")
    layout.addWidget(w); return w


def _lbl_direct(layout: QVBoxLayout, text: str, font: QFont, color: str) -> QLabel:
    w = QLabel(text); w.setFont(font); w.setStyleSheet(f"color: {color};")
    layout.addWidget(w); return w
