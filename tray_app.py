"""
System-tray application shell for VocalClear (PySide6).

Architecture changes from tkinter version:
  - ONE QApplication owns the main thread and event loop
  - QSystemTrayIcon replaces pystray — no tray daemon thread, no keepalive thread
  - MainWindow, SettingsWindow, SoundBoardWindow are QMainWindow objects on the
    main thread; they are shown/hidden rather than created/destroyed repeatedly
  - Cross-thread callbacks (e.g. from the audio engine) use QTimer.singleShot(0, fn)
    which safely queues the call onto the Qt main thread
  - Quit: QApplication.quit() unblocks exec() in TrayApp.run()
"""

import ctypes
import datetime
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap, QImage, QAction, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QWidget, QLabel, QVBoxLayout)

from config import Config
from noise_filter import NoiseFilter
from audio_engine import AudioEngine, find_vbcable_device
from icon import draw_icon
from soundboard import SoundBoard
from window_snapper import SnapManager

_LOG = Path.home() / ".vocalclear" / "vocalclear.log"


def _log(msg: str) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _pil_to_qicon(pil_img) -> QIcon:
    """Convert a PIL RGBA image to QIcon without writing a temp file."""
    pil_img = pil_img.convert("RGBA")
    data    = pil_img.tobytes("raw", "RGBA")
    qimg    = QImage(data, pil_img.width, pil_img.height,
                     QImage.Format.Format_RGBA8888)
    return QIcon(QPixmap.fromImage(qimg))


class _ToastOverlay(QWidget):
    """Brief always-on-top state badge shown in the bottom-right corner when the
    global toggle hotkey fires. Disappears after 2 seconds without stealing focus."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._lbl = QLabel()
        self._lbl.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lo = QVBoxLayout(self)
        lo.setContentsMargins(18, 10, 18, 10)
        lo.addWidget(self._lbl)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def flash(self, active: bool) -> None:
        if active:
            self._lbl.setText("● MIC ACTIVE")
            self._lbl.setStyleSheet("color: #00e676;")
            self.setStyleSheet(
                "background: #0b160b; border: 1px solid #007a40; border-radius: 4px;")
        else:
            self._lbl.setText("⊘ MIC MUTED")
            self._lbl.setStyleSheet("color: #ff1744;")
            self.setStyleSheet(
                "background: #160505; border: 1px solid #7a0010; border-radius: 4px;")
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen:
            rect = screen.availableGeometry()
            self.move(rect.right()  - self.width()  - 24,
                      rect.bottom() - self.height() - 48)
        self.show()
        self._hide_timer.start(2000)


class TrayApp:
    def __init__(self, prev_crashed: bool = False):
        self.config       = Config()
        self.noise_filter = NoiseFilter(sample_rate=self.config["sample_rate"])
        self.noise_filter.strength = self.config["strength"]
        self.noise_filter.enabled  = self.config["enabled"]

        self.engine = AudioEngine(self.config, self.noise_filter)

        self._active:                  bool                   = self.config["enabled"]
        self._muted:                   bool                   = False
        self._error_msg:               Optional[str]          = None
        self._prev_crashed:            bool                   = prev_crashed
        self._vbc_missing:             bool                   = False
        self._watched_error:           Optional[str]          = None
        self._watchdog_restart_active: bool                   = False
        self._toggle_hotkey_was_down:  bool                   = False
        self._toast:                   Optional[_ToastOverlay] = None

        # Check VB-CABLE on startup
        self._vbc_index = find_vbcable_device()

        # SoundBoard
        sb_dir = Path.home() / ".vocalclear" / "sounds"
        sb_dir.mkdir(parents=True, exist_ok=True)
        self._soundboard = SoundBoard(sounds_dir=sb_dir)
        self._soundboard.set_sounds_dir(sb_dir)
        self.engine.attach_soundboard(self._soundboard)

        # Window snapper
        self._snapper = SnapManager()

        # These are created lazily in run() once QApplication exists
        self._tray:    Optional[QSystemTrayIcon] = None
        self._main_window                        = None
        self._settings_window                    = None
        self._sb_window                          = None

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName("VocalClear")

        # Give the process its own identity in Task Manager / taskbar
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "VocalClear.App")
        except Exception:
            pass
        ico_path = Path(__file__).parent / "vocalclear.ico"
        if ico_path.exists():
            app.setWindowIcon(QIcon(str(ico_path)))
        else:
            app.setWindowIcon(_pil_to_qicon(draw_icon(64, active=True)))

        self._start_engine()
        self._watched_error = self.engine.last_error  # baseline so watchdog ignores startup state
        self._build_tray(app)

        # Build all windows up-front (cheap — they start hidden)
        self._build_windows()

        # Screen-corner state overlay for the global toggle hotkey
        self._toast = _ToastOverlay()

        # Show main window on launch
        self._main_window.show()

        # Notify if engine failed, VB-CABLE missing, or previous session crashed
        if self._error_msg:
            QTimer.singleShot(1000, self._notify_engine_error)
        elif self._vbc_missing:
            QTimer.singleShot(1500, self._notify_no_vbcable)
        elif self._prev_crashed:
            QTimer.singleShot(2000, self._notify_prev_crash)

        watchdog = QTimer()
        watchdog.timeout.connect(self._watchdog_tick)
        watchdog.start(5000)

        # R-CTRL + \ global toggle hotkey — polled on the Qt main thread
        hotkey_timer = QTimer()
        hotkey_timer.timeout.connect(self._check_toggle_hotkey)
        hotkey_timer.start(30)

        app.exec()

        watchdog.stop()
        hotkey_timer.stop()
        # ── Cleanup after exec() returns ──────────────────────────────────────
        self._soundboard.stop_watcher()
        self.engine.stop()

    # ──────────────────────────────────────────────────────────────────────────
    # Engine lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    def _start_engine(self) -> None:
        try:
            self.engine.start()
            _log(f"Engine started  backend={self.noise_filter.backend}"
                 f"  output={self.engine.output_device_name!r}")
            if self.engine.output_device_name == "System default":
                self._vbc_missing = True
                _log("VB-CABLE not found — output falling back to system default")
            if not self.noise_filter.is_calibrated:
                self.engine.start_calibration(duration_s=2.0, done_cb=lambda: None)
        except Exception as e:
            self._error_msg = str(e)
            _log(f"ENGINE ERROR: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # System tray
    # ──────────────────────────────────────────────────────────────────────────

    def _build_tray(self, app: QApplication) -> None:
        self._tray = QSystemTrayIcon(app)
        self._tray.setIcon(_pil_to_qicon(draw_icon(64, active=self._active)))
        self._tray.setToolTip(self._tooltip())
        self._tray.setContextMenu(self._build_menu())
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background: #0b160b; color: #c8ffd4; border: 1px solid #007a40; }"
            "QMenu::item { padding: 5px 20px; font-family: Consolas; font-size: 9pt; }"
            "QMenu::item:selected { background: #007a40; color: #030603; }"
            "QMenu::separator { height: 1px; background: #007a40; margin: 2px 0; }"
        )
        show_act     = QAction("⊞  Show Window", menu)
        mute_act     = QAction(
            ("●  Unmute mic  (R-Ctrl+\\)" if self._muted else "⊘  Mute mic  (R-Ctrl+\\)"), menu)
        toggle_act   = QAction(
            ("⏸  Pause filter (passthrough)" if self._active else "▶  Resume filter"), menu)
        settings_act = QAction("⚙  Settings",       menu)
        sb_act       = QAction("🎛  Soundboard",     menu)
        quit_act     = QAction("✖  Quit VocalClear", menu)

        show_act.triggered.connect(self._show_main_window)
        mute_act.triggered.connect(self._do_mute_toggle)
        toggle_act.triggered.connect(self._do_toggle)
        settings_act.triggered.connect(self._open_settings)
        sb_act.triggered.connect(self._open_soundboard)
        quit_act.triggered.connect(self._do_quit)

        menu.addAction(show_act)
        menu.addSeparator()
        menu.addAction(mute_act)
        menu.addAction(toggle_act)
        menu.addAction(settings_act)
        menu.addAction(sb_act)
        menu.addSeparator()
        menu.addAction(quit_act)
        return menu

    def _refresh_tray(self) -> None:
        """Rebuild icon + tooltip + menu after a state change."""
        if not self._tray:
            return
        self._tray.setIcon(_pil_to_qicon(draw_icon(64, active=self._active)))
        self._tray.setToolTip(self._tooltip())
        self._tray.setContextMenu(self._build_menu())

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_main_window()

    def _notify_engine_error(self) -> None:
        if self._tray and self._error_msg:
            _log(f"Tray notification shown: ENGINE ERROR — {self._error_msg}")
            self._tray.showMessage(
                "VocalClear — Engine Error",
                f"Audio engine failed to start:\n{self._error_msg}\n\n"
                "Open Settings → Apply & Restart Audio to retry.",
                QSystemTrayIcon.MessageIcon.Critical,
                5000,
            )

    def _notify_no_vbcable(self) -> None:
        if self._tray:
            _log("Tray notification shown: VB-CABLE not found")
            self._tray.showMessage(
                "VocalClear — VB-CABLE Not Found",
                "Virtual audio cable not detected.\n"
                "Noise suppression is active but your cleaned audio is NOT\n"
                "reaching Discord or Zoom.\n\n"
                "Install VB-CABLE from vb-audio.com, then restart VocalClear.",
                QSystemTrayIcon.MessageIcon.Warning,
                7000,
            )

    def _notify_prev_crash(self) -> None:
        if self._tray:
            _log("Tray notification shown: previous session did not exit cleanly")
            self._tray.showMessage(
                "VocalClear",
                "Last session ended unexpectedly.\nSee the log for details.",
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Window construction
    # ──────────────────────────────────────────────────────────────────────────

    def _build_windows(self) -> None:
        from main_window import MainWindow
        self._main_window = MainWindow(
            config        = self.config,
            noise_filter  = self.noise_filter,
            engine        = self.engine,
            soundboard    = self._soundboard,
            on_toggle     = self._do_toggle,
            on_mute       = self._do_mute_toggle,
            on_settings   = self._open_settings,
            on_soundboard = self._open_soundboard,
            on_quit       = self._do_quit,
            snapper       = self._snapper,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Actions (all called on the Qt main thread)
    # ──────────────────────────────────────────────────────────────────────────

    def _show_main_window(self) -> None:
        if self._main_window:
            self._main_window.show()
            self._main_window.raise_()
            self._main_window.activateWindow()

    def _do_toggle(self) -> None:
        self._active               = not self._active
        self.noise_filter.enabled  = self._active
        self.config["enabled"]     = self._active
        self._refresh_tray()
        if self._main_window:
            self._main_window.refresh_status()

    def _do_mute_toggle(self) -> None:
        self._muted             = not self._muted
        self.engine.muted       = self._muted
        self._refresh_tray()
        if self._main_window:
            self._main_window.refresh_mute_state(self._muted)

    def _open_settings(self) -> None:
        if self._settings_window is None:
            from settings_window import SettingsWindow
            self._settings_window = SettingsWindow(
                self.config, self.noise_filter, self.engine,
                snapper=self._snapper,
                on_close=self._on_settings_closed,
            )
        self._settings_window.show()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    def _on_settings_closed(self) -> None:
        """Called when the settings window closes — sync tray and main window."""
        self._active              = self.config["enabled"]
        self.noise_filter.enabled  = self._active
        self.noise_filter.strength = self.config["strength"]
        self._refresh_tray()
        if self._main_window:
            self._main_window.refresh_status()
            self._main_window.refresh_output_device()

    def _open_soundboard(self) -> None:
        if self._sb_window is None:
            from soundboard_window import SoundBoardWindow
            self._sb_window = SoundBoardWindow(
                self._soundboard, snapper=self._snapper)
        self._sb_window.show()
        self._sb_window.raise_()
        self._sb_window.activateWindow()

    def _do_quit(self) -> None:
        """Tray / button quit — delegates to main window for the sounds-playing check."""
        if self._main_window:
            self._main_window.quit_from_tray()
        else:
            QApplication.instance().quit()

    # ──────────────────────────────────────────────────────────────────────────
    # Watchdog — runs on the Qt main thread every 5 s
    # ──────────────────────────────────────────────────────────────────────────

    def _watchdog_tick(self) -> None:
        # Update tray tooltip with live input dB
        if self._tray:
            self._tray.setToolTip(self._tooltip())

        if self._watchdog_restart_active:
            return
        cur_error = self.engine.last_error
        if cur_error and cur_error != self._watched_error:
            self._watched_error = cur_error
            _log(f"Watchdog detected runtime engine error: {cur_error}")
            self._watchdog_restart_active = True
            if self._tray:
                self._tray.showMessage(
                    "VocalClear",
                    "Audio stream error — attempting automatic restart…",
                    QSystemTrayIcon.MessageIcon.Warning,
                    3000,
                )
            threading.Thread(
                target=self._watchdog_restart,
                daemon=True,
                name="VocalClear-watchdog",
            ).start()

    def _watchdog_restart(self) -> None:
        try:
            self.engine.restart()
            _log(f"Watchdog restart succeeded → {self.engine.output_device_name}")
            QTimer.singleShot(0, self._on_watchdog_success)
        except Exception as exc:
            _log(f"Watchdog restart failed: {exc}")
            self._watched_error = str(exc)   # prevent immediate re-trigger
            QTimer.singleShot(0, lambda e=str(exc): self._on_watchdog_failure(e))
        finally:
            self._watchdog_restart_active = False

    def _on_watchdog_success(self) -> None:
        self._watched_error = None
        self._refresh_tray()
        if self._main_window:
            self._main_window.refresh_output_device()
        if self._tray:
            self._tray.showMessage(
                "VocalClear",
                f"Stream reconnected → {self.engine.output_device_name}",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _on_watchdog_failure(self, err: str) -> None:
        if self._tray:
            self._tray.showMessage(
                "VocalClear — Stream Error",
                f"Auto-restart failed.\nOpen Settings → Apply & Restart Audio.\n\n{err}",
                QSystemTrayIcon.MessageIcon.Critical,
                6000,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Global toggle hotkey — R-CTRL + \ (polled every 30 ms on the Qt main thread)
    # ──────────────────────────────────────────────────────────────────────────

    def _check_toggle_hotkey(self) -> None:
        r_ctrl    = bool(ctypes.windll.user32.GetAsyncKeyState(0xA3) & 0x8000)  # VK_RCONTROL
        backslash = bool(ctypes.windll.user32.GetAsyncKeyState(0xDC) & 0x8000)  # VK_OEM_5 (US \)
        down = r_ctrl and backslash
        if down and not self._toggle_hotkey_was_down:
            self._do_mute_toggle()
            self._show_toggle_feedback()
        self._toggle_hotkey_was_down = down

    def _show_toggle_feedback(self) -> None:
        # Feedback reflects mute state: unmuted = active, muted = muted
        if self._toast:
            self._toast.flash(not self._muted)
        self._play_toggle_sound(not self._muted)

    def _play_toggle_sound(self, active: bool) -> None:
        # winsound.Beep() uses the Windows audio session directly — no PortAudio
        # stream setup overhead — so the tone plays immediately on a single press.
        # sounddevice.play() opened a new OutputStream each call (~50-100 ms to
        # initialise) which consumed most of the 120 ms tone before it was audible.
        import winsound as _ws
        freq = 880 if active else 587   # unmute = high, mute = low
        threading.Thread(
            target=lambda: _ws.Beep(freq, 150),
            daemon=True,
            name="VocalClear-tone",
        ).start()

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _tooltip(self) -> str:
        import math as _math
        if self._error_msg:
            return f"VocalClear – ERROR: {self._error_msg}"
        if self._muted:
            state = "Muted"
        elif not self._active:
            state = "Passthrough"
        else:
            state = "Active"
        vbc = f" → {self.engine.output_device_name}" if self.engine.output_device_name else ""
        rms = self.engine.input_rms
        db_str = ""
        if rms > 1e-9:
            db = max(-60.0, 20 * _math.log10(rms))
            db_str = f"  |  {db:+.0f} dB"
        return f"VocalClear – {state}{vbc}{db_str}"
