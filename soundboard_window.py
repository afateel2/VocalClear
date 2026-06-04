"""
VocalClear SoundBoard window — PySide6, oscilloscope aesthetic.

Fixed 480×590 px to match main/settings for horizontal snapping.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QRect, QSize, QPoint
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontMetrics,
    QPalette, QIcon, QLinearGradient,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QScrollArea, QSizePolicy, QApplication, QFileDialog,
    QMessageBox, QGridLayout, QInputDialog, QLineEdit,
)

if TYPE_CHECKING:
    from soundboard import SoundBoard, Sound
    from window_snapper import SnapManager

# ── Palette ───────────────────────────────────────────────────────────────────
C_BG_ROOT  = QColor("#030603")
C_BG       = QColor("#060d06")
C_BG_CARD  = QColor("#0b160b")
C_BG_INPUT = QColor("#0f1f0f")
C_GRID     = QColor("#0d1f0d")
C_GREEN    = QColor("#00e676")
C_GREEN_DIM= QColor("#007a40")
C_GREEN_LO = QColor("#004d28")
C_GREEN_XLO= QColor("#001f10")
C_AMBER    = QColor("#ffb300")
C_RED      = QColor("#ff1744")
C_FG       = QColor("#c8ffd4")
C_FG_DIM   = QColor("#3a6642")
C_FG_MID   = QColor("#6aaa7a")

FONT_MONO    = QFont("Consolas", 9)
FONT_MONO_XL = QFont("Consolas", 13); FONT_MONO_XL.setBold(True)
FONT_MONO_H  = QFont("Consolas", 11); FONT_MONO_H.setBold(True)
FONT_MONO_L  = QFont("Consolas", 8)
FONT_MONO_S  = QFont("Consolas", 7)

W        = 480
H        = 590
BTN_COLS = 4
BTN_W    = 104
BTN_H    = 90
TICK_MS  = 100


def _apply_dark_titlebar(hwnd: int) -> None:
    import ctypes
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


def _hdivider(color: QColor = C_GREEN_LO) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet(f"border: none; background: {color.name()}; max-height: 1px;")
    return line


# ── Toggle button ─────────────────────────────────────────────────────────────

class _ToggleBtn(QLabel):
    def __init__(self, text: str, state: bool, parent=None):
        super().__init__(parent)
        self._text  = text
        self._state = state
        self._cb    = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(FONT_MONO_L)
        self._refresh()

    def set_callback(self, cb) -> None: self._cb = cb

    @property
    def state(self) -> bool: return self._state

    @state.setter
    def state(self, v: bool) -> None:
        self._state = v; self._refresh()

    def _refresh(self) -> None:
        dot = "●" if self._state else "○"
        self.setText(f" {dot} {self._text} ")
        if self._state:
            self.setStyleSheet(
                "background: #00e676; color: #020502; padding: 3px 8px; "
                "font-family: Consolas; font-size: 8pt;")
        else:
            self.setStyleSheet(
                "background: #081208; color: #2a4a2e; padding: 3px 8px; "
                "border: 1px solid #003319; font-family: Consolas; font-size: 8pt;")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._cb:
            self._cb()


# ── Small header button ───────────────────────────────────────────────────────

class _HeaderBtn(QWidget):
    """Bordered action button for the soundboard header."""

    def __init__(self, text: str, callback,
                 color: QColor = C_GREEN_DIM,
                 hot: QColor = C_GREEN,
                 text_idle: QColor | None = None,
                 parent=None):
        super().__init__(parent)
        self._text      = text
        self._callback  = callback
        self._base      = color
        self._hot       = hot
        self._idle_text = text_idle or color
        self._hovered   = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_text(self, t: str) -> None: self._text = t; self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(FONT_MONO_L)
        return QSize(fm.horizontalAdvance(self._text) + 18, fm.height() + 10)

    def paintEvent(self, _):
        p   = QPainter(self)
        r   = self.rect()
        col = self._hot if self._hovered else self._base
        if self._hovered:
            p.fillRect(r, col)
            p.setPen(QPen(col.lighter(160), 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            tc = C_BG_ROOT
        else:
            p.fillRect(r, QColor(8, 18, 8))
            p.setPen(QPen(col, 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            tc = self._idle_text
        p.setFont(FONT_MONO_L)
        p.setPen(tc)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)

    def enterEvent(self, _): self._hovered = True;  self.update()
    def leaveEvent(self, _): self._hovered = False; self.update()
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self._callback()


# Alias for the volume popup (same widget, slightly larger text)
class _SmallBtn(_HeaderBtn):
    def sizeHint(self) -> QSize:
        fm = QFontMetrics(FONT_MONO)
        return QSize(fm.horizontalAdvance(self._text) + 22, fm.height() + 12)

    def paintEvent(self, _):
        p   = QPainter(self)
        r   = self.rect()
        col = self._hot if self._hovered else self._base
        if self._hovered:
            p.fillRect(r, col)
            p.setPen(QPen(col.lighter(160), 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            tc = C_BG_ROOT
        else:
            p.fillRect(r, QColor(8, 18, 8))
            p.setPen(QPen(col, 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            tc = self._idle_text
        p.setFont(FONT_MONO)
        p.setPen(tc)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)


# ── SFX level slider ──────────────────────────────────────────────────────────

class _SFXBar(QWidget):
    def __init__(self, value: float = 0.8, parent=None):
        super().__init__(parent)
        self._value     = value
        self._on_change = None
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def value(self) -> float: return self._value

    @value.setter
    def value(self, v: float) -> None: self._value = v; self.update()

    def set_on_change(self, cb) -> None: self._on_change = cb

    def paintEvent(self, _):
        p  = QPainter(self)
        cw = self.width()
        ch = self.height()

        # Background
        p.fillRect(self.rect(), QColor("#081208"))
        p.setPen(QPen(QColor("#0d1f0d"), 1))
        for x in range(0, cw, 12):
            p.drawLine(x, 0, x, ch)

        # Filled portion
        filled = max(1, int(cw * self._value))
        # Gradient fill
        grad = QLinearGradient(0, 0, filled, 0)
        grad.setColorAt(0.0, QColor("#003d1f"))
        grad.setColorAt(0.7, QColor("#006030"))
        grad.setColorAt(1.0, QColor("#00e676"))
        p.fillRect(QRect(0, 3, filled, ch - 6), QBrush(grad))

        # Bright tip
        if filled > 4:
            p.fillRect(QRect(filled - 3, 3, 3, ch - 6), QColor("#00ff9a"))

        # Cursor line
        p.setPen(QPen(C_GREEN, 1))
        p.drawLine(filled, 1, filled, ch - 2)

        # Border
        p.setPen(QPen(C_GREEN_LO, 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _set_from_x(self, x: int) -> None:
        cw = self.width()
        if not cw: return
        v = max(0.0, min(1.0, x / cw))
        self._value = v; self.update()
        if self._on_change: self._on_change(v)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self._set_from_x(int(e.position().x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton: self._set_from_x(int(e.position().x()))


# ── Sound button ──────────────────────────────────────────────────────────────

class _SoundButton(QWidget):
    """
    Single sound tile in the grid.

    Idle:    dark background · dot grid · dim border
    Hover:   lighter background · brighter border
    Playing: green tinted background · scan lines · bright border ·
             filled triangle indicator in top-right corner
    """

    def __init__(self, name: str, sound, on_play, on_context, parent=None):
        super().__init__(parent)
        self._name       = name
        self._sound      = sound
        self._on_play    = on_play
        self._on_context = on_context
        self._playing    = False
        self._hovered    = False
        self.setFixedSize(BTN_W, BTN_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_playing(self, v: bool) -> None:
        if v != self._playing:
            self._playing = v; self.update()

    def paintEvent(self, _):
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        BW = self.width()
        BH = self.height()

        # ── Background ────────────────────────────────────────────────────────
        if self._playing:
            bg = QColor("#0c2010")
        elif self._hovered:
            bg = QColor("#101f10")
        else:
            bg = C_BG_CARD
        p.fillRect(self.rect(), bg)

        # Dot grid texture
        dot_col = QColor("#162a16") if self._playing else QColor("#0e200e")
        p.setPen(QPen(dot_col, 1))
        for yy in range(5, BH - 2, 7):
            for xx in range(5, BW - 2, 7):
                p.drawPoint(xx, yy)

        # Scan lines when playing
        if self._playing:
            scan = QColor(0, 60, 20, 35)
            for yy in range(0, BH, 5):
                p.fillRect(QRect(0, yy, BW, 1), scan)

        # ── Border ───────────────────────────────────────────────────────────
        if self._playing:
            p.setPen(QPen(C_GREEN, 2))
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
            # Inner bright line on top edge
            p.setPen(QPen(QColor("#00ff9a"), 1))
            p.drawLine(2, 1, BW - 3, 1)
        elif self._hovered:
            p.setPen(QPen(C_GREEN_DIM, 1))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        else:
            p.setPen(QPen(C_GREEN_XLO, 1))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))

        # ── Playing triangle indicator (top-right corner) ─────────────────────
        if self._playing:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(C_GREEN))
            tri = [QPoint(BW - 3, 3), QPoint(BW - 3, 14), QPoint(BW - 14, 3)]
            p.drawPolygon(tri)

        # ── Name ─────────────────────────────────────────────────────────────
        n = len(self._name)
        if n <= 10:
            font = FONT_MONO
        elif n <= 18:
            font = FONT_MONO_L
        else:
            font = FONT_MONO_S
        text_col = QColor("#00ff9a") if self._playing else C_FG
        p.setFont(font)
        p.setPen(text_col)
        # Reserve top-right for indicator when playing
        right_inset = 14 if self._playing else 4
        text_r = QRect(4, 4, BW - 4 - right_inset, BH - 14)
        p.drawText(text_r,
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
                   | Qt.TextFlag.TextWordWrap,
                   self._name)

        # ── Volume bar (full-width, 3 px at absolute bottom) ─────────────────
        vol    = self._sound.volume
        bar_y  = BH - 5
        bar_h  = 3
        filled = max(1, int(BW * vol))
        p.fillRect(QRect(0, bar_y, BW, bar_h), QColor("#001a0e"))
        bar_col = QColor("#00c863") if self._playing else QColor("#005030")
        p.fillRect(QRect(0, bar_y, filled, bar_h), bar_col)
        if filled > 3:
            p.fillRect(QRect(filled - 3, bar_y, 3, bar_h), bar_col.lighter(170))

    def enterEvent(self, _): self._hovered = True;  self.update()
    def leaveEvent(self, _): self._hovered = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._on_play(self._name)
        elif e.button() == Qt.MouseButton.RightButton:
            self._on_context(self._name, e.globalPosition().toPoint())


# ── Volume popup ──────────────────────────────────────────────────────────────

class _VolumePopup(QMainWindow):
    def __init__(self, name: str, init_vol: float, on_save, parent=None):
        super().__init__(parent)
        self._on_save = on_save
        self._vol     = [init_vol]

        self.setWindowTitle("Volume")
        self.setFixedSize(300, 160)
        self.setStyleSheet("QMainWindow, QWidget { background: #030603; }")
        _apply_dark_titlebar(int(self.winId()))

        ico = Path(__file__).parent / "vocalclear.ico"
        if ico.exists(): self.setWindowIcon(QIcon(str(ico)))

        c = QWidget(); self.setCentralWidget(c)
        lo = QVBoxLayout(c); lo.setContentsMargins(0, 0, 0, 0); lo.setSpacing(0)

        acc = QWidget(); acc.setFixedHeight(2)
        acc.setStyleSheet("background: #00e676;"); lo.addWidget(acc)

        hdr = QWidget()
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(14, 8, 14, 8)
        t   = QLabel("VOLUME"); t.setFont(FONT_MONO_H); t.setStyleSheet("color: #00e676;")
        s   = QLabel(f"  {name}"); s.setFont(FONT_MONO_L); s.setStyleSheet("color: #3a6642;")
        hl.addWidget(t); hl.addWidget(s); hl.addStretch()
        lo.addWidget(hdr)
        lo.addWidget(_hdivider(C_GREEN_LO))

        body = QWidget()
        bl   = QVBoxLayout(body); bl.setContentsMargins(16, 12, 16, 12); bl.setSpacing(10)

        row  = QHBoxLayout()
        _l   = QLabel("LEVEL"); _l.setFont(FONT_MONO_L); _l.setStyleSheet("color: #3a6642;")
        row.addWidget(_l); row.addStretch()
        self._pct = QLabel(f"{int(init_vol * 100):3d}%")
        self._pct.setFont(FONT_MONO); self._pct.setStyleSheet("color: #00e676;")
        row.addWidget(self._pct)
        bl.addLayout(row)

        self._bar = _SFXBar(init_vol)

        def _changed(v):
            self._vol[0] = v
            self._pct.setText(f"{int(v * 100):3d}%")

        self._bar.set_on_change(_changed)
        bl.addWidget(self._bar)
        lo.addWidget(body)

        lo.addWidget(_hdivider(C_GREEN_LO))
        bbar = QWidget()
        bl2  = QHBoxLayout(bbar); bl2.setContentsMargins(14, 8, 14, 8); bl2.addStretch()
        cancel = _SmallBtn("CANCEL", self.close,
                           color=QColor("#3a1010"), hot=QColor("#cc0030"),
                           text_idle=QColor("#8b1a2a"))
        save   = _SmallBtn("SAVE",   self._save, color=C_GREEN_DIM, hot=C_GREEN)
        bl2.addWidget(cancel); bl2.addWidget(save)
        lo.addWidget(bbar)

    def _save(self) -> None:
        self._on_save(self._vol[0]); self.close()


# ── Soundboard window ─────────────────────────────────────────────────────────

class SoundBoardWindow(QMainWindow):
    def __init__(self, soundboard: "SoundBoard", snapper: "SnapManager" = None):
        super().__init__()
        self.sb        = soundboard
        self._snapper  = snapper
        self._btns:    dict[str, _SoundButton] = {}
        self._refresh_pending = threading.Event()
        self._status_lbl:     Optional[QLabel] = None
        self._header_status:  Optional[QLabel] = None
        self._placeholder:    Optional[QLabel] = None

        self.setWindowTitle("SoundBoard  —  VocalClear")
        self.setFixedSize(W, H)
        self.setStyleSheet("QMainWindow, QWidget { background: #030603; color: #c8ffd4; }")

        ico = Path(__file__).parent / "vocalclear.ico"
        if ico.exists(): self.setWindowIcon(QIcon(str(ico)))

        self._build_ui()
        _apply_dark_titlebar(int(self.winId()))

        self.sb._on_sounds_changed = self._schedule_refresh
        self.sb._on_play_changed   = self._schedule_refresh

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._poll_refresh)
        self._refresh_timer.start(50)

        if self._snapper:
            QTimer.singleShot(50, lambda: self._snapper.register(
                "soundboard", self, snap_side="left-only"))

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        c    = QWidget(); self.setCentralWidget(c)
        root = QVBoxLayout(c); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # Top accent bar
        acc = QWidget(); acc.setFixedHeight(2)
        acc.setStyleSheet("background: #00e676;"); root.addWidget(acc)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setAutoFillBackground(True)
        pal = hdr.palette(); pal.setColor(QPalette.ColorRole.Window, QColor("#040804"))
        hdr.setPalette(pal)
        hdr_lo = QVBoxLayout(hdr); hdr_lo.setContentsMargins(0, 0, 0, 0); hdr_lo.setSpacing(0)

        # Title row
        title_row = QHBoxLayout(); title_row.setContentsMargins(16, 10, 16, 4)
        t_sound = QLabel("SOUND"); t_sound.setFont(FONT_MONO_XL)
        t_sound.setStyleSheet("color: #00e676;")
        t_board = QLabel("BOARD"); t_board.setFont(FONT_MONO_XL)
        t_board.setStyleSheet("color: #c8ffd4;")
        t_sub = QLabel("  VOCALCLEAR"); t_sub.setFont(FONT_MONO_S)
        t_sub.setStyleSheet("color: #2a4a2e; letter-spacing: 1px;")
        self._header_status = QLabel("")
        self._header_status.setFont(FONT_MONO_L)
        self._header_status.setStyleSheet("color: #00e676;")
        title_row.addWidget(t_sound); title_row.addWidget(t_board)
        title_row.addWidget(t_sub);   title_row.addWidget(self._header_status)
        title_row.addStretch()
        hdr_lo.addLayout(title_row)

        # Action buttons row
        btn_row = QHBoxLayout(); btn_row.setContentsMargins(16, 0, 16, 10); btn_row.setSpacing(6)
        btn_row.addWidget(_HeaderBtn("▼ IMPORT", self._do_import))
        btn_row.addWidget(_HeaderBtn("▲ EXPORT", self._do_export))
        btn_row.addWidget(_HeaderBtn("+ ADD",    self._add_sound))
        btn_row.addStretch()
        self._stop_btn = _HeaderBtn(
            "▪ STOP ALL", self._stop_all,
            color=QColor("#5a0010"), hot=QColor("#cc0030"),
            text_idle=QColor("#8b1a2a"))
        btn_row.addWidget(self._stop_btn)
        hdr_lo.addLayout(btn_row)
        root.addWidget(hdr)
        root.addWidget(_hdivider(C_GREEN_LO))

        # ── Controls row ──────────────────────────────────────────────────────
        ctrl = QWidget()
        ctrl.setAutoFillBackground(True)
        pal = ctrl.palette(); pal.setColor(QPalette.ColorRole.Window, QColor("#050d05"))
        ctrl.setPalette(pal)
        ctrl_lo = QHBoxLayout(ctrl)
        ctrl_lo.setContentsMargins(14, 8, 14, 8); ctrl_lo.setSpacing(8)

        self._overlap_btn = _ToggleBtn("OVERLAP", self.sb.overlap)
        self._overlap_btn.set_callback(self._on_overlap_toggle)
        ctrl_lo.addWidget(self._overlap_btn)

        self._monitor_btn = _ToggleBtn("MONITOR", self.sb.monitor_enabled)
        self._monitor_btn.set_callback(self._on_monitor_toggle)
        ctrl_lo.addWidget(self._monitor_btn)

        # SFX bar + label grouped together
        sfx_group = QHBoxLayout(); sfx_group.setSpacing(6)
        self._sfx_bar = _SFXBar(self.sb.master_volume)
        self._sfx_bar.set_on_change(self._on_sfx)
        self._sfx_pct = QLabel(f"{int(self.sb.master_volume * 100)}%")
        self._sfx_pct.setFont(FONT_MONO_L)
        self._sfx_pct.setStyleSheet("color: #00e676; min-width: 32px;")
        sfx_lbl = QLabel("SFX")
        sfx_lbl.setFont(FONT_MONO_S)
        sfx_lbl.setStyleSheet("color: #2a4a2e; letter-spacing: 1px;")
        sfx_group.addWidget(self._sfx_bar, stretch=1)
        sfx_group.addWidget(self._sfx_pct)
        sfx_group.addWidget(sfx_lbl)
        ctrl_lo.addLayout(sfx_group, stretch=1)
        root.addWidget(ctrl)
        root.addWidget(_hdivider(QColor("#0d1f0d")))

        # ── Scrollable button grid ─────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: #060d06; }"
            "QScrollBar:vertical { background: #080f08; width: 8px; border: none; }"
            "QScrollBar::handle:vertical { background: #004d28; min-height: 24px; border-radius: 4px; }"
            "QScrollBar::handle:vertical:hover { background: #00e676; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }"
        )

        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background: #060d06;")
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setContentsMargins(8, 8, 8, 8)
        self._grid_layout.setSpacing(6)

        # Empty-state placeholder
        self._placeholder = QLabel(
            "NO SOUNDS LOADED\n\n"
            "▼ IMPORT a soundboard profile\n"
            "or  + ADD individual audio files")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setFont(FONT_MONO_L)
        self._placeholder.setStyleSheet("color: #1a3f1a; padding: 40px;")
        self._placeholder.setWordWrap(True)
        self._grid_layout.addWidget(self._placeholder, 0, 0, 1, BTN_COLS)

        self._scroll.setWidget(self._grid_widget)
        root.addWidget(self._scroll, stretch=1)

        # ── Status bar ────────────────────────────────────────────────────────
        root.addWidget(_hdivider(QColor("#0d1f0d")))
        sb = QWidget()
        sb.setAutoFillBackground(True)
        pal = sb.palette(); pal.setColor(QPalette.ColorRole.Window, QColor("#020502"))
        sb.setPalette(pal)
        sb_lo = QHBoxLayout(sb); sb_lo.setContentsMargins(14, 5, 14, 6)
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setFont(FONT_MONO_S)
        self._status_lbl.setStyleSheet("color: #1a3f1a;")
        self._playing_lbl = QLabel("")
        self._playing_lbl.setFont(FONT_MONO_S)
        self._playing_lbl.setStyleSheet("color: #00e676;")
        sb_lo.addWidget(self._status_lbl)
        sb_lo.addStretch()
        sb_lo.addWidget(self._playing_lbl)
        root.addWidget(sb)

    # ── Grid management ───────────────────────────────────────────────────────

    def _refresh_buttons(self) -> None:
        sounds  = self.sb.sounds
        playing = self.sb.playing_names

        # Remove stale buttons
        gone = set(self._btns) - set(sounds)
        for name in gone:
            btn = self._btns.pop(name)
            btn.setParent(None)
            btn.deleteLater()

        # Add new buttons
        for name, snd in sounds.items():
            if name not in self._btns:
                btn = _SoundButton(
                    name, snd,
                    on_play=self._play,
                    on_context=self._show_context_menu,
                )
                self._btns[name] = btn

        # Update playing state + sound ref
        for name, btn in self._btns.items():
            btn.set_playing(name in playing)
            if name in sounds:
                btn._sound = sounds[name]
                btn.update()

        # Show/hide placeholder
        if self._placeholder:
            self._placeholder.setVisible(not sounds)

        # Reflow grid (playing sounds sort to front)
        sorted_names = sorted(
            self._btns.keys(),
            key=lambda n: (0 if n in playing else 1, n.lower())
        )
        for i, name in enumerate(sorted_names):
            row, col = divmod(i, BTN_COLS)
            self._grid_layout.addWidget(self._btns[name], row, col)

        # Status bar
        n = len(sounds)
        self._status_lbl.setText(
            f"{n} sound{'s' if n != 1 else ''} loaded" if n else "No sounds loaded")
        np = len(playing)
        self._playing_lbl.setText(
            f"● {np} playing" if np else "")

    # ── Context menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, name: str, pos: QPoint) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #0b160b; color: #c8ffd4; border: 1px solid #007a40; }"
            "QMenu::item { padding: 5px 20px; font-family: Consolas; font-size: 8pt; }"
            "QMenu::item:selected { background: #007a40; color: #030603; }"
            "QMenu::separator { height: 1px; background: #004d28; margin: 2px 0; }"
        )
        menu.addAction(f"  ▶  Play",         lambda: self._play(name))
        menu.addAction(f"  ▪  Stop",         lambda: self._stop_one(name))
        menu.addSeparator()
        menu.addAction("  VOL  Set volume…", lambda: self._show_volume(name))
        menu.addAction("  REN  Rename…",     lambda: self._rename(name))
        menu.addSeparator()
        menu.addAction("  DEL  Remove",      lambda: self._remove(name))
        menu.exec(pos)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _play(self, name: str) -> None:
        self.sb.play(name); self._refresh_buttons()

    def _stop_one(self, name: str) -> None:
        self.sb.stop(name); self._refresh_buttons()

    def _stop_all(self) -> None:
        self.sb.stop(); self._refresh_buttons()

    def _add_sound(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Sound Files", "",
            "Audio files (*.mp3 *.ogg *.m4a *.wav *.flac);;All files (*.*)",
        )
        for p in paths:
            threading.Thread(
                target=lambda f=Path(p): self.sb.load_file(f), daemon=True).start()

    def _do_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Soundboard Profile", "",
            "ZIP profile (*.zip);;All files (*.*)",
        )
        if not path:
            return
        def _run():
            try:
                self.sb.export_profile(Path(path))
                QTimer.singleShot(0, lambda: self._set_header_status("EXPORTED"))
            except Exception as e:
                QTimer.singleShot(0, lambda: QMessageBox.critical(
                    self, "Export Failed", str(e)))
        threading.Thread(target=_run, daemon=True).start()

    def _do_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Soundboard Profile", "",
            "ZIP profile (*.zip);;All files (*.*)",
        )
        if not path:
            return
        mb = QMessageBox(self)
        mb.setWindowTitle("Import Profile")
        mb.setText("Merge imported sounds with existing ones?\n\n"
                   "Yes = keep existing + add imported\n"
                   "No  = replace all sounds")
        mb.setStyleSheet(
            "QMessageBox { background: #030603; color: #c8ffd4; }"
            "QPushButton { background: #007a40; color: #030603; padding: 4px 12px; "
            "              border: none; font-family: Consolas; }"
            "QPushButton:hover { background: #00e676; }")
        mb.setStandardButtons(QMessageBox.StandardButton.Yes |
                              QMessageBox.StandardButton.No  |
                              QMessageBox.StandardButton.Cancel)
        result = mb.exec()
        if result == QMessageBox.StandardButton.Cancel:
            return
        merge = (result == QMessageBox.StandardButton.Yes)
        def _run():
            try:
                self.sb.import_profile(Path(path), merge=merge)
                QTimer.singleShot(0, lambda: self._set_header_status("IMPORTED"))
            except Exception as e:
                QTimer.singleShot(0, lambda: QMessageBox.critical(
                    self, "Import Failed", str(e)))
        threading.Thread(target=_run, daemon=True).start()

    def _show_volume(self, name: str) -> None:
        snd = self.sb.sounds.get(name)
        if snd is None:
            return
        def _save(v: float) -> None:
            self.sb.set_volume(name, v)
            if name in self._btns:
                self._btns[name].update()
        popup = _VolumePopup(name, snd.volume, _save, parent=self)
        popup.show()

    def _rename(self, name: str) -> None:
        text, ok = QInputDialog.getText(
            self, "Rename Sound", "New name:", QLineEdit.EchoMode.Normal, name)
        text = text.strip() if ok else ""
        if not text or text == name:
            return
        if not self.sb.rename_sound(name, text):
            QMessageBox.warning(self, "Rename Failed",
                f"Could not rename '{name}' to '{text}'.\n"
                "The name may already be in use.")
        self._refresh_buttons()

    def _remove(self, name: str) -> None:
        mb = QMessageBox(self)
        mb.setWindowTitle("Remove Sound")
        mb.setText(
            f"Remove '{name}' from the soundboard?\n"
            "(Removes from VocalClear's sounds folder.\n"
            "Your original source file is unchanged.)")
        mb.setStyleSheet(
            "QMessageBox { background: #030603; color: #c8ffd4; }"
            "QPushButton { background: #007a40; color: #030603; padding: 4px 12px; "
            "              border: none; font-family: Consolas; }"
            "QPushButton:hover { background: #00e676; }")
        mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if mb.exec() == QMessageBox.StandardButton.Yes:
            self.sb.remove_sound(name)
            self._refresh_buttons()

    # ── Toggle callbacks ──────────────────────────────────────────────────────

    def _on_overlap_toggle(self) -> None:
        new = not self._overlap_btn.state
        self.sb.overlap = new
        self._overlap_btn.state = new
        self.sb._save_sounds_config()

    def _on_monitor_toggle(self) -> None:
        new = not self._monitor_btn.state
        self.sb.monitor_enabled = new
        self._monitor_btn.state = new
        self.sb._save_sounds_config()

    def _on_sfx(self, v: float) -> None:
        self.sb.master_volume = v
        self._sfx_pct.setText(f"{int(v * 100)}%")
        self.sb._save_sounds_config()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_header_status(self, msg: str) -> None:
        if self._header_status:
            self._header_status.setText(f"  {msg}")
            QTimer.singleShot(3000, lambda: (
                self._header_status.setText("") if self._header_status else None))

    def _schedule_refresh(self) -> None:
        self._refresh_pending.set()

    def _poll_refresh(self) -> None:
        if self._refresh_pending.is_set():
            self._refresh_pending.clear()
            self._refresh_buttons()

    # ── Window events ─────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        if self._snapper:
            QTimer.singleShot(50, lambda: self._snapper.register(
                "soundboard", self, snap_side="left-only"))
            self._snapper.position_left_of("main", self)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(TICK_MS)
        self._refresh_buttons()
        event.accept()

    def closeEvent(self, event) -> None:
        """X button hides the window; keeps timer + callbacks alive for reopen."""
        event.ignore()
        self.hide()
        if self._snapper:
            self._snapper.unregister("soundboard")
