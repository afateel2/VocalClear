"""
System-tray application shell for VocalClear.

The tray icon is green (active) or grey (paused).
Right-click menu: Show Window · Toggle · Settings · Soundboard · Quit

On launch the main window is shown; closing it minimizes to tray.

Tray architecture:
  - pystray runs in a dedicated daemon thread using blocking icon.run()
  - If the icon dies (e.g. Windows Explorer restarts), the thread automatically
    restarts it after a 2-second pause — the app never disappears unexpectedly
  - tkinter mainloop owns the main thread (required on Windows)
  - A threading.Event (_quit_event) coordinates clean shutdown
"""

import time
import threading
from pathlib import Path
from typing import Optional

import pystray

from config import Config
from noise_filter import NoiseFilter
from audio_engine import AudioEngine, find_vbcable_device
from icon import draw_icon
from soundboard import SoundBoard
from window_snapper import SnapManager


class TrayApp:
    def __init__(self):
        self.config = Config()
        self.noise_filter = NoiseFilter(sample_rate=self.config["sample_rate"])
        self.noise_filter.strength = self.config["strength"]
        self.noise_filter.enabled  = self.config["enabled"]

        self.engine = AudioEngine(self.config, self.noise_filter)

        self._icon:            Optional[pystray.Icon]   = None
        self._active:          bool                     = self.config["enabled"]
        self._error_msg:       Optional[str]            = None
        self._main_window                               = None
        self._quit_event       = threading.Event()      # set when app should exit
        self._settings_thread: Optional[threading.Thread] = None
        self._sb_thread:       Optional[threading.Thread] = None
        self._icon_lock        = threading.Lock()       # guards cross-thread icon updates

        # Check VB-CABLE on startup
        self._vbc_index = find_vbcable_device()

        # SoundBoard — auto-create watch folder
        sb_dir = Path.home() / ".vocalclear" / "sounds"
        sb_dir.mkdir(parents=True, exist_ok=True)
        self._soundboard = SoundBoard(sounds_dir=sb_dir)
        self._soundboard.set_sounds_dir(sb_dir)
        self.engine.attach_soundboard(self._soundboard)

        # Window snapper (Winamp-style sticky windows)
        self._snapper = SnapManager()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._start_engine()

        # Tray icon runs in a self-restarting daemon thread so the app
        # survives Explorer crashes without vanishing from the tray.
        tray_thread = threading.Thread(
            target=self._tray_loop,
            daemon=True,
            name="VocalClear-tray",
        )
        tray_thread.start()

        # Keepalive: refresh the tray icon tooltip every 30 s so Windows
        # doesn't auto-hide it to the overflow after a period of inactivity.
        keepalive_thread = threading.Thread(
            target=self._icon_keepalive,
            daemon=True,
            name="VocalClear-tray-keepalive",
        )
        keepalive_thread.start()

        # Main window — runs on the main thread (required by tkinter/Win32)
        from main_window import MainWindow
        self._main_window = MainWindow(
            config        = self.config,
            noise_filter  = self.noise_filter,
            engine        = self.engine,
            soundboard    = self._soundboard,
            on_toggle     = self._do_toggle,
            on_settings   = self._open_settings,
            on_soundboard = self._open_soundboard,
            on_quit       = self._do_quit_callback,
            snapper       = self._snapper,
        )
        self._main_window.run()   # blocks until window is destroyed

        # ── Cleanup after mainloop exits ──────────────────────────────
        self._quit_event.set()          # signal tray loop to stop restarting
        if self._icon:
            try:
                self._icon.stop()       # unblock icon.run() in tray thread
            except Exception:
                pass
        self._soundboard.stop_watcher()
        self.engine.stop()

    # ------------------------------------------------------------------
    # Tray loop — restarts automatically if the icon dies
    # ------------------------------------------------------------------

    def _tray_loop(self) -> None:
        """Run pystray; restart after any failure until _quit_event is set."""
        import traceback as _tb, datetime as _dt
        _log = Path.home() / ".vocalclear" / "vocalclear.log"
        def _log_err(msg: str) -> None:
            try:
                with open(_log, "a", encoding="utf-8") as f:
                    f.write(f"[{_dt.datetime.now():%H:%M:%S}] {msg}\n")
            except Exception:
                pass

        while not self._quit_event.is_set():
            try:
                self._icon = pystray.Icon(
                    "VocalClear",
                    draw_icon(64, active=self._active),
                    self._tooltip(),
                    menu=self._build_menu(),
                )
                # Show the icon and fire error notification if engine failed.
                # visible=True MUST be set in a custom setup; pystray only does
                # it automatically when no setup callback is provided.
                def _on_icon_ready(icon):
                    icon.visible = True
                    if self._error_msg:
                        try:
                            icon.notify(
                                f"Audio engine failed to start:\n{self._error_msg}\n\n"
                                "Open Settings → Apply & Restart Audio to retry.",
                                "VocalClear — Engine Error",
                            )
                        except Exception:
                            pass
                self._icon.run(setup=_on_icon_ready)
            except BaseException as exc:
                _log_err(f"[TrayApp] Icon error:\n{_tb.format_exc()}")

            if not self._quit_event.is_set():
                # Unplanned exit (Explorer crash, etc.) — wait then restart
                _log_err("[TrayApp] Tray icon lost — restarting in 2 s")
                time.sleep(2)

    def _icon_keepalive(self) -> None:
        """Refresh the tray icon every 30 s to prevent Windows auto-hiding it."""
        while not self._quit_event.wait(timeout=30):
            try:
                if self._icon and self._icon.visible:
                    # NIM_MODIFY with NIF_TIP — lightweight, thread-safe,
                    # no HICON involved. Keeps the notification-area slot alive.
                    self._icon.title = self._tooltip()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def _start_engine(self) -> None:
        try:
            self.engine.start()
            if not self.noise_filter.is_calibrated:
                self.engine.start_calibration(duration_s=2.0, done_cb=lambda: None)
        except Exception as e:
            self._error_msg = str(e)

    # ------------------------------------------------------------------
    # Toggle
    # ------------------------------------------------------------------

    def _do_toggle(self) -> None:
        """Toggle active/paused — no pystray args required."""
        self._active = not self._active
        self.noise_filter.enabled = self._active
        self.config["enabled"]    = self._active
        with self._icon_lock:
            try:
                if self._icon:
                    self._icon.icon  = draw_icon(64, active=self._active)
                    self._icon.title = self._tooltip()
                    self._icon.update_menu()
            except Exception:
                pass   # icon might be restarting
        if self._main_window:
            self._main_window.refresh_status()

    # ------------------------------------------------------------------
    # Quit
    # ------------------------------------------------------------------

    def _do_quit_callback(self) -> None:
        """Called by main window QUIT button (no-op — run() handles cleanup)."""
        pass

    # ------------------------------------------------------------------
    # Tray menu callbacks  (pystray passes icon, item)
    # ------------------------------------------------------------------

    def _toggle(self, icon: pystray.Icon, item) -> None:
        self._do_toggle()

    def _show_window(self, icon: pystray.Icon, item) -> None:
        if self._main_window:
            self._main_window.show()

    def _quit(self, icon: pystray.Icon, item) -> None:
        """Tray Quit: signal the quit event, then destroy the main window."""
        self._quit_event.set()     # prevent tray loop from restarting
        if self._main_window:
            # Delegates to main thread so the messagebox can show if sounds are playing
            self._main_window.quit_from_tray()
        else:
            # No main window — clean up directly
            self._soundboard.stop_watcher()
            self.engine.stop()
            icon.stop()

    def _open_settings(self, icon=None, item=None) -> None:
        if self._settings_thread and self._settings_thread.is_alive():
            return   # already open — ignore
        self._settings_thread = threading.Thread(
            target=self._show_settings_window, daemon=True)
        self._settings_thread.start()

    def _open_soundboard(self, icon=None, item=None) -> None:
        if self._sb_thread and self._sb_thread.is_alive():
            return   # already open — ignore
        self._sb_thread = threading.Thread(
            target=self._show_soundboard_window, daemon=True)
        self._sb_thread.start()

    def _show_soundboard_window(self) -> None:
        try:
            from soundboard_window import SoundBoardWindow
            win = SoundBoardWindow(self._soundboard, snapper=self._snapper)
            win.run()
        except BaseException:
            import traceback, datetime
            log = Path.home() / ".vocalclear" / "vocalclear.log"
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] "
                            f"Soundboard window error:\n{traceback.format_exc()}\n")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Settings window (its own thread)
    # ------------------------------------------------------------------

    def _show_settings_window(self) -> None:
        from settings_window import SettingsWindow
        win = SettingsWindow(self.config, self.noise_filter, self.engine,
                             snapper=self._snapper)
        win.run()
        # Sync state after settings close
        with self._icon_lock:
            try:
                if self._icon:
                    self._active = self.config["enabled"]
                    self.noise_filter.enabled  = self._active
                    self.noise_filter.strength = self.config["strength"]
                    self._icon.icon  = draw_icon(64, active=self._active)
                    self._icon.title = self._tooltip()
                    self._icon.update_menu()
            except Exception:
                pass
        if self._main_window:
            self._main_window.refresh_status()
            self._main_window.refresh_output_device()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tooltip(self) -> str:
        state = "Active" if self._active else "Paused"
        if self._error_msg:
            return f"VocalClear – ERROR: {self._error_msg}"
        vbc = f" → {self.engine.output_device_name}" if self.engine.output_device_name else ""
        return f"VocalClear – {state}{vbc}"

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("⊞  Show Window",     self._show_window, default=True),
            pystray.MenuItem(
                lambda item: "⏸  Pause" if self._active else "▶  Resume",
                self._toggle,
            ),
            pystray.MenuItem("⚙  Settings",        self._open_settings),
            pystray.MenuItem("🎛  Soundboard",      self._open_soundboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("✖  Quit VocalClear",  self._quit),
        )
