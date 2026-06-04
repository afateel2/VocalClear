"""
VocalClear — Main application window (PySide6).

Shown on launch; closing minimizes to tray (does not quit).
Oscilloscope aesthetic: near-black green-tinted background, neon green accents.
"""

from __future__ import annotations

import math
import ctypes
from collections import deque
from pathlib import Path
from typing import Optional, Callable, TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QPointF, QSize
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontMetrics,
    QLinearGradient, QPalette, QIcon, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QSizePolicy, QApplication,
)

if TYPE_CHECKING:
    from audio_engine import AudioEngine
    from noise_filter import NoiseFilter
    from config import Config
    from soundboard import SoundBoard
    from window_snapper import SnapManager

# ── Palette ───────────────────────────────────────────────────────────────────
C_BG_ROOT  = QColor("#030603")
C_BG       = QColor("#060d06")
C_BG_CARD  = QColor("#0b160b")
C_GRID     = QColor("#0d1f0d")
C_GRID_DOT = QColor("#0f2a0f")

C_GREEN     = QColor("#00e676")
C_GREEN_DIM = QColor("#007a40")
C_GREEN_LO  = QColor("#004d28")
C_GREEN_XLO = QColor("#001f10")
C_AMBER     = QColor("#ffb300")
C_AMBER_DIM = QColor("#4a3200")
C_RED       = QColor("#ff1744")
C_RED_DIM   = QColor("#3a0010")

C_FG        = QColor("#c8ffd4")
C_FG_DIM    = QColor("#3a6642")
C_FG_MID    = QColor("#6aaa7a")

FONT_MONO   = QFont("Consolas", 9)
FONT_MONO_H = QFont("Consolas", 13); FONT_MONO_H.setBold(True)
FONT_MONO_L = QFont("Consolas", 8)
FONT_MONO_S = QFont("Consolas", 7)

W, H           = 480, 590
VU_BARS        = 22
VU_TICK_MS     = 50
PEAK_HOLD_TICKS = 32  # ~1.6 s at 50 ms/tick


# ── Stylesheet ────────────────────────────────────────────────────────────────
APP_QSS = """
QMainWindow, QWidget {
    background-color: #030603;
    color: #c8ffd4;
}
QToolTip {
    background-color: #0b160b;
    color: #00e676;
    border: 1px solid #007a40;
    font-family: Consolas;
    font-size: 8pt;
}
"""


# ── Win32 helpers ─────────────────────────────────────────────────────────────

def _apply_dark_titlebar(hwnd: int) -> None:
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


def _rms_to_db(rms: float) -> float:
    return -60.0 if rms < 1e-9 else max(-60.0, 20 * math.log10(rms))


# ── Geometric icon painter ────────────────────────────────────────────────────

def _paint_icon(p: QPainter, kind: str, rect: QRect, color: QColor) -> None:
    """Draw a simple geometric icon centred in *rect*. No emoji, no glyphs."""
    cx = rect.center().x()
    cy = rect.center().y()

    if kind == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawRect(QRect(cx - 6, cy - 5, 4, 10))
        p.drawRect(QRect(cx + 2, cy - 5, 4, 10))

    elif kind == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        pts = [QPoint(cx - 4, cy - 6), QPoint(cx + 7, cy), QPoint(cx - 4, cy + 6)]
        p.drawPolygon(pts)

    elif kind == "gear":
        p.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - 4, cy - 4, 8, 8)
        p.setPen(QPen(color, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(6):
            a = math.radians(i * 60)
            x1, y1 = cx + 4 * math.cos(a), cy + 4 * math.sin(a)
            x2, y2 = cx + 7 * math.cos(a), cy + 7 * math.sin(a)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    elif kind == "grid":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        for row in range(2):
            for col in range(3):
                x = cx - 7 + col * 5
                y = cy - 3 + row * 6
                p.drawRect(QRect(int(x), int(y), 3, 4))

    elif kind == "cross":
        p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
        p.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)


# ── Icon button ───────────────────────────────────────────────────────────────

class _IconButton(QWidget):
    """Bordered button with a QPainter-drawn icon — no emoji."""

    def __init__(self, icon: str, text: str, callback: Callable,
                 base_color: QColor = C_GREEN_DIM,
                 hot_color:  QColor = C_GREEN,
                 text_idle:  QColor | None = None,
                 parent=None):
        super().__init__(parent)
        self._icon      = icon
        self._text      = text
        self._callback  = callback
        self._base      = base_color
        self._hot       = hot_color
        self._idle_text = text_idle or base_color
        self._hovered   = False
        self._pressed   = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(FONT_MONO)
        tw = fm.horizontalAdvance(self._text)
        return QSize(tw + 42, 30)

    def paintEvent(self, _):
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r   = self.rect()
        col = self._hot if self._hovered else self._base

        if self._hovered:
            p.fillRect(r, col)
            p.setPen(QPen(col.lighter(160), 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            icon_col = C_BG_ROOT
            text_col = C_BG_ROOT
        else:
            p.fillRect(r, QColor(8, 18, 8))
            p.setPen(QPen(col, 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            icon_col = self._idle_text
            text_col = self._idle_text

        icon_rect = QRect(r.left() + 9, r.top() + (r.height() - 12) // 2, 12, 12)
        _paint_icon(p, self._icon, icon_rect, icon_col)

        text_r = QRect(r.left() + 26, r.top(), r.width() - 32, r.height())
        p.setFont(FONT_MONO)
        p.setPen(text_col)
        p.drawText(text_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._text)

    def enterEvent(self, _):  self._hovered = True;  self.update()
    def leaveEvent(self, _):  self._hovered = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._callback()


# ── VU meter ──────────────────────────────────────────────────────────────────

class _VUMeter(QWidget):
    """Dual-channel vertical-segment VU meter with peak hold."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.in_rms        = 0.0
        self.out_rms       = 0.0
        self._peak_in      = 0.0
        self._peak_out     = 0.0
        self._peak_in_hold = 0
        self._peak_out_hold= 0
        self._paused       = False
        self.setMinimumHeight(105)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_paused(self, paused: bool) -> None:
        if paused != self._paused:
            self._paused = paused
            self.update()

    def update_levels(self, in_rms: float, out_rms: float) -> None:
        self.in_rms  = in_rms
        self.out_rms = out_rms
        for attr, rms in (("_peak_in", in_rms), ("_peak_out", out_rms)):
            hold_attr = attr + "_hold"
            if rms >= getattr(self, attr):
                setattr(self, attr, rms)
                setattr(self, hold_attr, PEAK_HOLD_TICKS)
            elif getattr(self, hold_attr) > 0:
                setattr(self, hold_attr, getattr(self, hold_attr) - 1)
            else:
                setattr(self, attr, max(getattr(self, attr) * 0.88, rms))
        self.update()

    def paintEvent(self, _):
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        cw = self.width()
        ch = self.height()

        p.fillRect(self.rect(), C_BG_CARD)

        # Dot matrix background
        p.setPen(QPen(C_GRID_DOT, 1))
        for yy in range(4, ch - 2, 8):
            for xx in range(4, cw - 2, 8):
                p.drawPoint(xx, yy)

        LBL_H = 16
        meter_h = ch - LBL_H - 4
        half    = (cw - 6) // 2

        self._draw_channel(p, x0=2,        w=half, h=meter_h, y0=LBL_H,
                           rms=self.in_rms,  peak=self._peak_in,  label="IN")
        self._draw_channel(p, x0=half + 4,  w=half, h=meter_h, y0=LBL_H,
                           rms=self.out_rms, peak=self._peak_out, label="OUT")

        # Centre divider
        p.setPen(QPen(C_GREEN_LO, 1))
        mid = half + 3
        p.drawLine(mid, LBL_H, mid, ch - 4)

    def _draw_channel(self, p: QPainter, x0, w, h, y0, rms, peak, label):
        n       = VU_BARS
        seg_h   = max(2, (h - n) // n)
        step    = seg_h + 1
        filled  = min(int(rms * n * 2.8), n)
        peak_i  = min(int(peak * n * 2.8), n - 1)

        # Channel label
        p.setFont(FONT_MONO_S)
        p.setPen(C_FG_DIM)
        fm = QFontMetrics(FONT_MONO_S)
        lw = fm.horizontalAdvance(label)
        p.drawText(x0 + (w - lw) // 2, y0 - 3, label)

        for i in range(n):
            by  = y0 + h - (i + 1) * step
            row = QRect(x0, by, w, seg_h)

            if i < filled and not self._paused:
                if i < int(n * 0.63):
                    col = C_GREEN
                elif i < int(n * 0.82):
                    col = C_AMBER
                else:
                    col = C_RED
                if i == filled - 1:
                    col = col.lighter(140)
                p.fillRect(row, col)
            else:
                if i < int(n * 0.63):
                    col = C_GREEN_XLO
                elif i < int(n * 0.82):
                    col = C_AMBER_DIM
                else:
                    col = C_RED_DIM
                p.fillRect(row, col)

            # Peak hold suppressed when paused
            if i == peak_i and peak > 0.008 and not self._paused:
                if peak_i < int(n * 0.63):
                    pc = C_GREEN.lighter(170)
                elif peak_i < int(n * 0.82):
                    pc = C_AMBER.lighter(150)
                else:
                    pc = C_RED.lighter(150)
                p.fillRect(QRect(x0, by, w, 1), pc)


# ── History graph ─────────────────────────────────────────────────────────────

class _HistoryGraph(QWidget):
    """Scrolling RMS history with area fill under the input trace."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._in_hist:  deque = deque(maxlen=240)
        self._out_hist: deque = deque(maxlen=240)
        self.setMinimumHeight(95)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push(self, in_rms: float, out_rms: float) -> None:
        self._in_hist.append(in_rms)
        self._out_hist.append(out_rms)
        self.update()

    def paintEvent(self, _):
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        cw = self.width()
        ch = self.height()

        p.fillRect(self.rect(), C_BG_CARD)

        # Grid lines
        p.setPen(QPen(C_GRID, 1))
        for y in range(0, ch, 20):
            p.drawLine(0, y, cw, y)
        for x in range(0, cw, 30):
            p.drawLine(x, 0, x, ch)

        # Subtle dB reference labels
        p.setFont(FONT_MONO_S)
        p.setPen(QColor("#1a3f1a"))
        for db, frac in (("-12", 0.22), ("-24", 0.50), ("-48", 0.88)):
            y = int(frac * (ch - 4)) + 2
            p.drawText(2, y + 8, db)
            p.setPen(QPen(QColor("#112211"), 1, Qt.PenStyle.DashLine))
            p.drawLine(18, y, cw, y)
            p.setPen(QColor("#1a3f1a"))

        n = len(self._in_hist)
        if n < 2:
            return

        peak = max(max(self._in_hist), max(self._out_hist, default=0), 0.012)

        def _points(hist):
            m = len(hist)
            return [QPointF(i * (cw - 1) / (m - 1),
                            max(2.0, min(ch - 2.0,
                                ch - 2 - (v / peak) * (ch - 6))))
                    for i, v in enumerate(hist)]

        in_pts  = _points(self._in_hist)
        out_pts = _points(self._out_hist)

        # Area fill under input trace
        poly = QPolygonF(in_pts)
        poly.append(QPointF(in_pts[-1].x(), ch - 2))
        poly.append(QPointF(in_pts[0].x(),  ch - 2))
        grad = QLinearGradient(0, 0, 0, ch)
        grad.setColorAt(0.0, QColor(0, 230, 118, 60))
        grad.setColorAt(1.0, QColor(0, 230, 118, 0))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(poly)

        # Output trace
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(C_GREEN_DIM, 1))
        for i in range(len(out_pts) - 1):
            p.drawLine(out_pts[i], out_pts[i + 1])

        # Input trace (on top, brighter)
        p.setPen(QPen(C_GREEN, 1))
        for i in range(len(in_pts) - 1):
            p.drawLine(in_pts[i], in_pts[i + 1])

        # Legend
        p.setFont(FONT_MONO_S)
        p.setPen(C_GREEN)
        p.drawText(22, 11, "IN")
        p.setPen(C_GREEN_DIM)
        p.drawText(22, 21, "OUT")


# ── Engine info card ──────────────────────────────────────────────────────────

_ENGINE_META = {
    "deepfilter": ("DEEPFILTER 3",  "#00e676", "#003319", "AI · best quality"),
    "rnnoise":    ("RNNOISE",        "#00e676", "#003319", "AI · lightweight"),
    "wiener":     ("WIENER",         "#ffb300", "#2a1c00", "fallback — install pyrnnoise"),
    "none":       ("NONE",           "#ff1744", "#2a000a", "audio engine error"),
}


class _Badge(QWidget):
    """Coloured pill badge for engine name."""

    def __init__(self, text: str, fg: str, bg: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._fg   = QColor(fg)
        self._bg   = QColor(bg)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(FONT_MONO_L)
        return QSize(fm.horizontalAdvance(self._text) + 14, fm.height() + 6)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 1, -1, -1)
        p.setBrush(QBrush(self._bg))
        p.setPen(QPen(self._fg.darker(120), 1))
        p.drawRoundedRect(r, 3, 3)
        p.setFont(FONT_MONO_L)
        p.setPen(self._fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)


class _InfoCard(QWidget):
    def __init__(self, noise_filter, engine, parent=None):
        super().__init__(parent)
        self._nf     = noise_filter
        self._engine = engine

        lo = QVBoxLayout(self)
        lo.setContentsMargins(14, 8, 14, 8)
        lo.setSpacing(5)

        # Engine row
        r1 = QHBoxLayout(); r1.setSpacing(6)
        lbl_key = QLabel("ENGINE")
        lbl_key.setFont(FONT_MONO_S)
        lbl_key.setStyleSheet("color: #3a6642; letter-spacing: 1px;")
        b_name, b_fg, b_bg, b_hint = _ENGINE_META.get(
            self._nf.backend, ("UNKNOWN", "#ff1744", "#2a000a", ""))
        badge = _Badge(b_name, b_fg, b_bg)
        lbl_hint = QLabel(b_hint)
        lbl_hint.setFont(FONT_MONO_S)
        lbl_hint.setStyleSheet("color: #3a6642;")
        for w in (lbl_key, badge, lbl_hint): r1.addWidget(w)
        r1.addStretch()
        lo.addLayout(r1)

        # Output row
        r2 = QHBoxLayout(); r2.setSpacing(6)
        lbl_out_key = QLabel("OUTPUT")
        lbl_out_key.setFont(FONT_MONO_S)
        lbl_out_key.setStyleSheet("color: #3a6642; letter-spacing: 1px;")
        self._out_lbl = QLabel(self._engine.output_device_name or "detecting…")
        self._out_lbl.setFont(FONT_MONO_L)
        self._out_lbl.setStyleSheet("color: #6aaa7a;")
        for w in (lbl_out_key, self._out_lbl): r2.addWidget(w)
        r2.addStretch()
        lo.addLayout(r2)

        # Error row
        self._err_lbl = QLabel("")
        self._err_lbl.setFont(FONT_MONO_S)
        self._err_lbl.setStyleSheet("color: #ff1744;")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.hide()
        lo.addWidget(self._err_lbl)

        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, C_BG_CARD)
        self.setPalette(pal)

    def refresh_output(self) -> None:
        self._out_lbl.setText(self._engine.output_device_name or "detecting…")

    def refresh_error(self) -> None:
        err = self._engine.last_error
        if err:
            self._err_lbl.setText(f"ERR  {err}")
            self._err_lbl.show()
        else:
            self._err_lbl.hide()


# ── Divider ───────────────────────────────────────────────────────────────────

def _hdivider(color: QColor = C_GREEN_LO) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet(f"border: none; background: {color.name()}; max-height: 1px;")
    return line


# ── Status chip ───────────────────────────────────────────────────────────────

class _StatusChip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = True
        self._blink  = 0
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(FONT_MONO_S)
        return QSize(fm.horizontalAdvance("PAUSED") + 22, fm.height() + 8)

    def paintEvent(self, _):
        p    = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r    = self.rect().adjusted(0, 1, -1, -1)
        text = "ACTIVE" if self._active else "PAUSED"
        fg   = QColor("#00e676") if self._active else QColor("#3a6642")
        bg   = QColor("#001f10") if self._active else QColor("#0d0d0d")
        bord = QColor("#007a40") if self._active else QColor("#222222")

        p.setBrush(QBrush(bg))
        p.setPen(QPen(bord, 1))
        p.drawRoundedRect(r, 3, 3)

        # Dot indicator
        dot_col = fg if self._active else QColor("#333333")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dot_col))
        p.drawEllipse(r.left() + 6, r.center().y() - 3, 6, 6)

        p.setFont(FONT_MONO_S)
        p.setPen(fg)
        text_r = r.adjusted(17, 0, -2, 0)
        p.drawText(text_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(
        self,
        config:         "Config",
        noise_filter:   "NoiseFilter",
        engine:         "AudioEngine",
        soundboard:     "SoundBoard",
        on_toggle:      Callable,
        on_settings:    Callable,
        on_soundboard:  Callable,
        on_quit:        Callable,
        snapper=None,
    ):
        super().__init__()
        self.config         = config
        self.noise_filter   = noise_filter
        self.engine         = engine
        self.soundboard     = soundboard
        self._on_toggle     = on_toggle
        self._on_settings   = on_settings
        self._on_soundboard = on_soundboard
        self._on_quit       = on_quit
        self._snapper       = snapper

        self._vu_meter:   Optional[_VUMeter]      = None
        self._history:    Optional[_HistoryGraph]  = None
        self._info_card:  Optional[_InfoCard]      = None
        self._status_chip:Optional[_StatusChip]    = None
        self._toggle_btn: Optional[_IconButton]    = None
        self._db_lbl:     Optional[QLabel]         = None
        self._last_xrun:  int                      = 0
        self._xrun_flash: int                      = 0   # ticks remaining for amber flash

        self._build_ui()
        _apply_dark_titlebar(int(self.winId()))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(VU_TICK_MS)

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> None:
        if self._snapper:
            self._snapper.set_anchor("main")
            QTimer.singleShot(50, lambda: self._snapper.register("main", self))
        self.show()
        QApplication.instance().exec()

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        """Called by tray Quit — runs on the Qt main thread."""
        self._confirm_and_quit()

    def refresh_status(self) -> None:
        QTimer.singleShot(0, self._update_status)

    def refresh_output_device(self) -> None:
        QTimer.singleShot(0, self._do_refresh_output)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setWindowTitle("VocalClear")
        self.setFixedSize(W, H)
        self.setStyleSheet(APP_QSS)
        self._try_set_icon()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top accent bar ────────────────────────────────────────────────────
        accent = QWidget(); accent.setFixedHeight(2)
        accent.setStyleSheet("background: #00e676;")
        root.addWidget(accent)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setAutoFillBackground(True)
        pal = hdr.palette(); pal.setColor(QPalette.ColorRole.Window, QColor("#040804"))
        hdr.setPalette(pal)
        hdr_lo = QHBoxLayout(hdr)
        hdr_lo.setContentsMargins(16, 10, 14, 10)

        lbl_title = QLabel("VOCAL")
        lbl_title.setFont(FONT_MONO_H)
        lbl_title.setStyleSheet("color: #00e676;")

        lbl_clear = QLabel("CLEAR")
        lbl_clear.setFont(FONT_MONO_H)
        lbl_clear.setStyleSheet("color: #c8ffd4;")

        lbl_sub = QLabel("  AI NOISE SUPPRESSION")
        lbl_sub.setFont(FONT_MONO_S)
        lbl_sub.setStyleSheet("color: #2a4a2e; letter-spacing: 1px;")

        self._status_chip = _StatusChip()
        self._status_chip.set_active(self.noise_filter.enabled)

        hdr_lo.addWidget(lbl_title)
        hdr_lo.addWidget(lbl_clear)
        hdr_lo.addWidget(lbl_sub)
        hdr_lo.addStretch()
        hdr_lo.addWidget(self._status_chip)
        root.addWidget(hdr)
        root.addWidget(_hdivider(C_GREEN_LO))

        # ── VU meter section ──────────────────────────────────────────────────
        vu_outer = QWidget()
        vu_outer.setAutoFillBackground(True)
        pal = vu_outer.palette(); pal.setColor(QPalette.ColorRole.Window, C_BG_ROOT)
        vu_outer.setPalette(pal)
        vu_lo = QVBoxLayout(vu_outer)
        vu_lo.setContentsMargins(14, 10, 14, 4)
        vu_lo.setSpacing(5)

        self._vu_meter = _VUMeter()
        self._vu_meter.setStyleSheet("border: 1px solid #003d1f; border-radius: 1px;")
        vu_lo.addWidget(self._vu_meter)

        db_row = QHBoxLayout()
        db_row.setContentsMargins(2, 0, 2, 0)
        self._db_lbl = QLabel("")
        self._db_lbl.setFont(FONT_MONO_S)
        self._db_lbl.setStyleSheet("color: #3a6642;")
        db_row.addStretch()
        db_row.addWidget(self._db_lbl)
        db_row.addStretch()
        vu_lo.addLayout(db_row)
        root.addWidget(vu_outer)

        # ── Info card ─────────────────────────────────────────────────────────
        root.addWidget(_hdivider(C_GRID))
        self._info_card = _InfoCard(self.noise_filter, self.engine)
        root.addWidget(self._info_card)

        # ── History graph ─────────────────────────────────────────────────────
        root.addWidget(_hdivider(C_GRID))
        hist_outer = QWidget()
        hist_outer.setAutoFillBackground(True)
        pal = hist_outer.palette()
        pal.setColor(QPalette.ColorRole.Window, C_BG_ROOT)
        hist_outer.setPalette(pal)
        hist_lo = QVBoxLayout(hist_outer)
        hist_lo.setContentsMargins(14, 6, 14, 6)
        hist_lo.setSpacing(4)

        hist_hdr = QHBoxLayout()
        hist_lbl = QLabel("SIGNAL HISTORY")
        hist_lbl.setFont(FONT_MONO_S)
        hist_lbl.setStyleSheet("color: #1a3f1a; letter-spacing: 1px;")
        hist_hdr.addWidget(hist_lbl)
        hist_hdr.addStretch()
        hist_lo.addLayout(hist_hdr)

        hist_border = QWidget()
        hist_border.setStyleSheet("background: #003319; border: 1px solid #004d28;")
        hist_border.setFixedHeight(120)
        hist_inner_lo = QVBoxLayout(hist_border)
        hist_inner_lo.setContentsMargins(1, 1, 1, 1)
        self._history = _HistoryGraph()
        hist_inner_lo.addWidget(self._history)
        hist_lo.addWidget(hist_border)
        root.addWidget(hist_outer)

        # ── Action buttons ────────────────────────────────────────────────────
        root.addWidget(_hdivider(C_GRID))
        btn_bar = QWidget()
        btn_bar.setAutoFillBackground(True)
        pal = btn_bar.palette(); pal.setColor(QPalette.ColorRole.Window, C_BG_ROOT)
        btn_bar.setPalette(pal)
        btn_lo = QHBoxLayout(btn_bar)
        btn_lo.setContentsMargins(14, 10, 14, 10)
        btn_lo.setSpacing(8)

        active = self.noise_filter.enabled
        icon   = "pause" if active else "play"
        text   = "PAUSE" if active else "RESUME"
        self._toggle_btn = _IconButton(icon, text, self._on_toggle)
        btn_lo.addWidget(self._toggle_btn)

        settings_btn = _IconButton("gear", "SETTINGS", self._on_settings)
        btn_lo.addWidget(settings_btn)

        sb_btn = _IconButton("grid", "SOUNDBOARD", self._on_soundboard)
        btn_lo.addWidget(sb_btn)
        btn_lo.addStretch()
        root.addWidget(btn_bar)

        # ── Bottom bar ────────────────────────────────────────────────────────
        root.addStretch()
        root.addWidget(_hdivider(QColor("#0d2a0d")))
        bot = QWidget()
        bot.setAutoFillBackground(True)
        pal = bot.palette(); pal.setColor(QPalette.ColorRole.Window, QColor("#020502"))
        bot.setPalette(pal)
        bot_lo = QHBoxLayout(bot)
        bot_lo.setContentsMargins(14, 6, 14, 8)

        hint = QLabel("X  closes to tray")
        hint.setFont(FONT_MONO_S)
        hint.setStyleSheet("color: #1a3320;")
        bot_lo.addWidget(hint)
        bot_lo.addStretch()

        quit_btn = _IconButton(
            "cross", "QUIT", self._confirm_and_quit,
            base_color=QColor("#5a0010"),
            hot_color =QColor("#cc0030"),
            text_idle =QColor("#8b1a2a"),
        )
        bot_lo.addWidget(quit_btn)
        root.addWidget(bot)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _try_set_icon(self) -> None:
        ico = Path(__file__).parent / "vocalclear.ico"
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))

    def _update_status(self) -> None:
        active = self.noise_filter.enabled
        if self._status_chip:
            self._status_chip.set_active(active)
        if self._vu_meter:
            self._vu_meter.set_paused(not active)
        if self._toggle_btn:
            if active:
                self._toggle_btn.set_icon("pause")
                self._toggle_btn.set_text("PAUSE")
            else:
                self._toggle_btn.set_icon("play")
                self._toggle_btn.set_text("RESUME")

    def _do_refresh_output(self) -> None:
        if self._info_card:
            self._info_card.refresh_output()

    def _sounds_playing(self) -> bool:
        sb = getattr(self, "soundboard", None)
        if sb is None:
            return False
        with sb._play_lock:
            return len(sb._playing) > 0

    def _confirm_and_quit(self) -> None:
        if self._sounds_playing():
            from PySide6.QtWidgets import QMessageBox
            mb = QMessageBox(self)
            mb.setWindowTitle("VocalClear")
            mb.setText("A sound is currently playing.\nQuit anyway?")
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            mb.setStyleSheet(
                "QMessageBox { background: #030603; color: #c8ffd4; }"
                "QPushButton { background: #007a40; color: #030603; padding: 4px 14px; "
                "              border: none; font-family: Consolas; }"
                "QPushButton:hover { background: #00e676; }"
            )
            if mb.exec() != QMessageBox.StandardButton.Yes:
                return
        self._do_destroy()

    def _do_destroy(self) -> None:
        self._timer.stop()
        if self._snapper:
            try:
                self._snapper.unregister("main")
            except Exception:
                pass
        self.hide()
        app = QApplication.instance()
        if app:
            app.quit()

    def closeEvent(self, event) -> None:
        """Title bar X → minimize to tray, do not quit."""
        event.ignore()
        self.hide()

    # ── Animation tick ────────────────────────────────────────────────────────

    def _tick(self) -> None:
        active  = self.noise_filter.enabled
        in_rms  = getattr(self.engine, "input_rms",  0.0)
        out_rms = getattr(self.engine, "output_rms", 0.0)
        if self._vu_meter:
            self._vu_meter.set_paused(not active)
            self._vu_meter.update_levels(in_rms, out_rms)
        if self._history:
            self._history.push(in_rms, out_rms)
        if self._db_lbl:
            cur_xrun = getattr(self.engine, "xrun_count", 0)
            if cur_xrun > self._last_xrun:
                self._last_xrun  = cur_xrun
                self._xrun_flash = 40   # 40 × 50 ms = 2 s
            if self._xrun_flash > 0:
                self._xrun_flash -= 1
                self._db_lbl.setStyleSheet("color: #ffb300;")
                self._db_lbl.setText(
                    f"IN  {_rms_to_db(in_rms):+.1f} dB       OUT  {_rms_to_db(out_rms):+.1f} dB"
                    f"   ⚠ xrun")
            else:
                self._db_lbl.setStyleSheet("color: #3a6642;")
                self._db_lbl.setText(
                    f"IN  {_rms_to_db(in_rms):+.1f} dB       OUT  {_rms_to_db(out_rms):+.1f} dB")
        if self._info_card:
            self._info_card.refresh_error()
