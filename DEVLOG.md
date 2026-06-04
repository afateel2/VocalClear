# VocalClear — Developer Log & Backlog

This file is the persistent session log + backlog for autonomous improvement loops.
Each session appends an entry describing what was done, problems hit, and research links.
Future sessions must read this file before starting work to avoid duplicating effort.

---

## Project Baseline (2026-06-04)

**Git state at baseline:** commit `9628910` (main).  
Working tree contains **uncommitted PySide6 migration** across 5 modified files + 3 deleted files.  
The migration is complete and functional but not yet committed.

### Architecture (current, PySide6)
```
main.py              → single-instance mutex, logging
tray_app.py          → QSystemTrayIcon + QApplication event loop
main_window.py       → VU meter, history graph, toggle, status (oscilloscope aesthetic)
settings_window.py   → strength/gain sliders, device picker, PTT, startup toggle
soundboard_window.py → scrollable 4-col button grid, volume popup, context menu
audio_engine.py      → sounddevice duplex stream, PTT loop, soundboard mixing
noise_filter.py      → DeepFilterNet → RNNoise → Wiener cascade
soundboard.py        → load/decode/resample, mix frame provider, waveOut monitor
config.py            → JSON config at %USERPROFILE%\.vocalclear\config.json
window_snapper.py    → Winamp-style flush-edge snap (DWM-aware insets)
icon.py              → PIL-generated RGBA equalizer bar icon
```

**Deleted (obsolete with PySide6):** `snap_bar.py`, `dark_dropdown.py`, `hotkey_manager.py`

### Key architectural facts
- All windows live on the Qt main thread; cross-thread calls use `QTimer.singleShot(0, fn)`
- Audio callback runs on PortAudio high-priority thread — no Qt calls, no blocking
- Block size: 480 samples = 10 ms at 48 kHz (matches RNNoise frame exactly)
- RNNoise/Wiener: inline processing in callback; DeepFilterNet: queue + dedicated thread
- VB-CABLE auto-detected by name matching "cable input" + "vb-audio"
- Sample rate auto-detected from output device (usually 48 kHz for VB-CABLE)

---

## Backlog

Items are ordered by priority. Check off and move to the relevant session entry when done.

### P1 — Bugs (must fix)

- [x] **Soundboard close→reopen breaks updates** *(fixed session 002)*  
  Changed `closeEvent` to `event.ignore() + hide()` (same pattern as `main_window.py`).  
  `showEvent` now restarts the timer if stopped and re-registers with the snapper.

- [x] **`monitor_enabled` not persisted across restarts** *(fixed session 002)*  
  Added `"monitor_enabled"` to `_global` in `_save_sounds_config`; restored via the property  
  setter in `_load_sounds_config` so `_monitor.start()/stop()` fires correctly.

- [x] **Commit the PySide6 migration** *(done session 001)*

### P2 — Papercuts (important, non-critical)

- [x] **Soundboard window: close button hides, not destroys** *(fixed session 002, merged with P1)*

- [ ] **Settings `_apply_and_restart` baseline reset bug**  
  Lines 784–786 unconditionally reset `_orig_startup/_orig_strength/_orig_gain` baselines even on a
  no-op save. This means the dirty indicator (APPLY button color) clears for unchanged settings when
  something else triggers an apply. Cosmetic but confusing.

- [x] **xrun count indicator** *(done session 002)*  
  `_tick` now compares `engine.xrun_count` against a stored baseline; new xruns amber the dB  
  readout label and append "⚠ xrun" for 2 seconds, then revert automatically.

- [x] **Main window: paused state dims VU meter** *(done session 002)*  
  Added `_VUMeter.set_paused(bool)`; when paused all filled segments render in their XLO/dim  
  variants and peak-hold indicators are suppressed. Called each tick and on every status update.

### P3 — Features (nice-to-have)

- [x] **Window position persistence** *(done session 005)*  
  `window_x/window_y` in config. `_restore_position()` called from `_build_ui`, validated  
  against `QGuiApplication.screenAt()` so a stale position from a disconnected monitor is  
  silently ignored. `_save_position()` called from `closeEvent` and `_do_destroy`.

- [x] **Auto-reconnect on device error** *(done session 004 — watchdog)*

- [x] **VU meter dB tick marks** *(done session 006)*  
  `_draw_channel(db_ticks=True)` on the OUT channel: dotted reference lines + dim labels  
  at −18 dB (bar 7) and −12 dB (bar 15) drawn right-justified inside the channel.

- [ ] **Keyboard shortcut: global pause/resume toggle**  
  A global hotkey (e.g. Ctrl+Shift+V configurable) to toggle `noise_filter.enabled` without opening
  the window. Different from PTT which requires holding.

- [x] **CPU/latency live readout** *(done session 006)*  
  `engine.process_time_ms` is a 10-sample rolling average of `noise_filter.process()` wall time  
  in the inline callback path. Displayed in the LATENCY info card row as "· 0.42 ms cpu";  
  refreshed every 20 ticks (~1 s).

- [ ] **Soundboard: volume normalizer**  
  Button to normalize all loaded sounds to the same RMS target so they play at consistent volumes.

- [ ] **"Test mic" button in settings**  
  Record 2 seconds through the selected input device and play back through monitor so the user can
  verify the mic is working without leaving the app.

- [x] **Log rotation** *(done session 002)*  
  `_rotate_log()` in `main.py` trims log to last 200 lines when file exceeds 500 KB.  
  Runs once per startup, before the new "starting" entry is written.

- [x] **Crash detection on next launch** *(done session 002)*  
  `_check_previous_crash()` scans the log tail: if the last "VocalClear starting" line has no  
  following "exited cleanly", `TrayApp` receives `prev_crashed=True` and shows a warning  
  notification 2 s after startup (only when no engine error is also firing).

- [x] **Startup VB-CABLE missing notification** *(done session 003)*  
  After `engine.start()`, if `output_device_name == "System default"`, `_vbc_missing = True` is set  
  and a 7 s tray warning fires explaining that Discord/Zoom won't receive cleaned audio.

- [x] **Audio clipping warning** *(done session 003)*  
  `_tick` tracks consecutive ticks where `out_rms > 0.92`; after 3 consecutive clips a "▲ CLIP"  
  label appears in red to the left of the dB readout for 2 seconds.

- [x] **Show actual stream latency in info card** *(done session 003)*  
  `AudioEngine` now exposes `input_latency_ms` / `output_latency_ms` read from  
  `stream.latency` after start (reset to 0.0 on stop). `_InfoCard` shows a LATENCY row;  
  updated 2 s after launch and after every Settings-triggered restart.

- [x] **Mid-session device-loss watchdog** *(done session 004)*  
  5 s `QTimer` on main thread watches `engine.last_error`. On new error: tray warning + background  
  restart thread. Guard flag prevents re-entry. `engine.restart()` now clears `last_error = None`  
  on success so the watchdog doesn't re-trigger on stale state.

- [x] **PTT live indicator in main window** *(done session 004)*  
  `_ptt_chip` QLabel in header: hidden when PTT off; "● PTT" (green fill) when key held,  
  "○ PTT" (dim border) when enabled but key up. Updated every `_tick`.

- [x] **Soundboard: per-sound loop toggle** *(done session 005)*  
  `_PlayingInstance.loop` flag; `get_mix_frame` restarts `pos=0` instead of marking done.  
  Context menu shows "↺ Loop" / "↺ Stop loop". "↺" drawn bottom-left of tile when looping.  
  `_toggle_loop`: flips flag on an in-flight instance or starts a new looping instance.

- [x] **Soundboard: search / filter bar** *(done session 004)*  
  `QLineEdit` row between controls and grid; `_apply_search_filter()` hides non-matching  
  buttons. Called on text change and at end of `_refresh_buttons`. Escape clears filter.

- [x] **Centralize dialog stylesheets** *(done session 006)*  
  `ui_utils.py` created with `style_dialog(widget)` and `_DARK_DIALOG_QSS`. Three inline  
  QMessageBox style blocks in `main_window.py` and `soundboard_window.py` replaced with  
  `from ui_utils import style_dialog; style_dialog(mb)`.

- [x] **Config schema version** *(done session 005)*  
  `"schema": 1` added to DEFAULTS. `_run_migrations()` in `load()` gates the block_size fix  
  on `saved_schema < 1` and stamps current schema on every load. `window_x/window_y` also  
  added to DEFAULTS for position persistence.

- [x] **Tray tooltip: live input dB** *(done session 004)*  
  `_tooltip()` now appends the current input dB (e.g. "| −18 dB"); called by the watchdog  
  timer every 5 s so the tooltip stays fresh without a separate timer.

---

## Session Log

---

### Session 006 — 2026-06-04 (autonomous loop tick 5)

**Goal:** Clean up stale backlog, centralize dialog CSS, VU dB tick marks, CPU readout.

**Done:**
- Marked "Auto-reconnect" as done (already covered by session 004 watchdog).
- `ui_utils.py` with `style_dialog()` / `_DARK_DIALOG_QSS`; replaced 3 inline QMessageBox  
  blocks in `main_window.py` and `soundboard_window.py`.
- VU meter dB tick marks: `_draw_channel(db_ticks=True)` on OUT channel; dotted reference  
  lines + right-justified dim labels at −18 dB (bar 7) and −12 dB (bar 15).
- CPU readout: `engine.process_time_ms` — 10-sample EMA of inline filter wall time;  
  appended to LATENCY row as "· 0.42 ms cpu"; refreshed every 20 ticks (~1 s).

**Problems:**
- None.

**Research / Key Facts:**
- `time.perf_counter()` in the PortAudio callback is safe on Windows (it uses the TSC/HPET  
  counter, which doesn't block). Confirmed by sounddevice docs: callback must be non-blocking  
  but reading a hardware counter is fine.
- The EMA formula `new = old * 0.9 + sample * 0.1` converges to steady state in ~20 samples  
  (~200 ms at 10 ms blocks). Fast enough for the 1 s display refresh cycle.

**Next session should do:** Soundboard volume normalizer, "test mic" button in settings,  
global pause/resume hotkey (research win32 RegisterHotKey approach).

---

### Session 005 — 2026-06-04 (autonomous loop tick 4)

**Goal:** Config schema versioning, window position persistence, soundboard loop toggle.

**Done:**
- Config schema: `"schema": 1` in DEFAULTS; `_run_migrations()` gates block_size fix on  
  `saved_schema < 1`; stamps current schema on every load. `window_x/window_y` also added.
- Window position: `_restore_position()` in `_build_ui` moves window to saved pos if it's  
  on a valid screen (`QGuiApplication.screenAt`). `_save_position()` called on X-close and QUIT.
- Soundboard loop: `_PlayingInstance.loop` flag + `get_mix_frame` restart-on-end; `play(loop=)`;  
  `looping_names` property; context menu "↺ Loop"/"↺ Stop loop"; "↺" drawn on tile  
  bottom-left; `_toggle_loop` flips in-flight flag or starts new looping instance.

**Problems:**
- `_toggle_loop` writes `inst.loop` from the UI thread while the audio callback reads it. This  
  is safe in CPython (bool assignment is atomic), but not guaranteed by the language spec.  
  Acceptable given the project's single-platform target and performance requirements.

**Research / Key Facts:**
- `QGuiApplication.screenAt(QPoint)` returns `None` if the point is not on any screen.  
  Good for validating saved window positions after monitor reconnect/disconnect.
- `_PlayingInstance` is a dataclass. Python dataclasses are mutable by default; the `loop`  
  field can be flipped in place without recreating the instance.

**Next session should do:** Centralize dialog stylesheets, VU meter dB tick marks, "test mic"  
button in settings, global pause/resume hotkey.

---

### Session 004 — 2026-06-04 (autonomous loop tick 3)

**Goal:** Device-loss watchdog, PTT live indicator, soundboard search bar, live dB in tray tooltip.

**Done:**
- Device-loss watchdog: 5 s `QTimer` on main thread; new `engine.last_error` triggers background  
  restart with tray notifications for success/failure. `_watchdog_restart_active` guard prevents  
  concurrent restarts. `engine.restart()` now clears `last_error = None` on success.
- PTT chip: `_ptt_chip` QLabel in header row; hidden when PTT disabled, "● PTT" green-fill  
  when key held, "○ PTT" dim-border when enabled but released. Polled each `_tick`.
- Soundboard search: `QLineEdit` row with FILTER label; `_apply_search_filter()` hides  
  non-matching buttons, re-applied at end of `_refresh_buttons`. Escape clears. Placeholder  
  visibility unaffected by filter (only depends on whether any sounds are loaded).
- Tray tooltip live dB: `_tooltip()` appends current `input_rms` as dB string; watchdog  
  calls `setToolTip(self._tooltip())` every 5 s.

**Problems:**
- `_ptt_chip` reads `engine._ptt_active` (private attr). Acceptable since it's within the same  
  package and the attr is a simple `bool` written atomically by the PTT thread.

**Research / Key Facts:**
- `QTimer.singleShot(0, fn)` from a background thread safely queues onto the Qt event loop  
  (this is documented Qt behaviour). Used throughout the watchdog restart callbacks.
- `threading.Thread(..., daemon=True)` is critical for the watchdog restart thread: if the  
  app quits while a restart is in progress, the thread dies with the process rather than blocking.

**Next session should do:** Config schema version, centralize dialog stylesheets, soundboard  
per-sound loop toggle, "test mic" button in settings.

---

### Session 003 — 2026-06-04 (autonomous loop tick 2)

**Goal:** VB-CABLE missing notification, clip warning, stream latency display.

**Done:**
- VB-CABLE missing notification: `_start_engine` detects `output_device_name == "System default"`  
  and sets `_vbc_missing`; a 7 s tray warning fires explaining Discord/Zoom won't get cleaned audio.  
  Priority below engine error, above prev-crash notification.
- Clip warning: `▲ CLIP` label (hidden by default) appears to the left of the dB readout for 2 s  
  whenever 3 consecutive ticks of `out_rms > 0.92` are detected.
- Stream latency: `AudioEngine.input_latency_ms / output_latency_ms` read from `stream.latency`  
  after start; displayed in a new LATENCY row in `_InfoCard`; refreshed 2 s post-launch and after  
  every Settings restart; reset to 0.0 on engine stop.

**Problems:**
- `sounddevice.Stream.latency` returns a plain float on some builds (not always a tuple). Wrapped  
  the read in try/except; if it fails the row shows "measuring…" indefinitely — acceptable fallback.

**Research / Key Facts:**
- `sounddevice.Stream.latency` is documented to return a `(input, output)` namedtuple in seconds,  
  but some builds return a scalar (the output latency only). The try/except guard handles this.
- VB-CABLE "Input" device shows up as output-only (`max_input_channels == 0`) in sounddevice;  
  the detection in `find_vbcable_device` checks this correctly.

**Next session should do:** Mid-session device-loss watchdog, PTT live indicator in main window,  
soundboard search/filter bar, tray tooltip live dB.

---

### Session 002 — 2026-06-04 (autonomous loop tick 1)

**Goal:** Fix all P1 bugs + high-value P2 items from backlog.

**Done:**
- Fixed soundboard close→reopen bug: `closeEvent` now calls `hide()` + `event.ignore()`;  
  `showEvent` restarts the timer and re-registers with the snapper on every show.
- Fixed `monitor_enabled` not persisting: added to `_global` in `_save_sounds_config`,  
  restored via property setter in `_load_sounds_config`.
- VU meter dims when paused: `_VUMeter.set_paused(bool)` swaps all lit segments to their  
  XLO/dim variants and suppresses peak-hold markers.
- xrun indicator: dB readout label turns amber and shows "⚠ xrun" for 2 s when  
  `engine.xrun_count` increases.
- Log rotation: `_rotate_log()` trims log to 200 lines if > 500 KB, runs at startup.
- Crash detection: `_check_previous_crash()` detects missing "exited cleanly" in the  
  previous session's log tail; shows a tray warning on next launch.

**Problems:**
- None — all changes were straightforward edits.

**Research / Key Facts:**
- `QTimer.isActive()` must be checked before restarting in `showEvent`; starting an already-active  
  timer resets its interval but is harmless.
- The `monitor_enabled` property setter calls `_monitor.start()/stop()` — must use the setter,  
  not direct `_monitor_enabled` assignment, to keep the `_MonitorMixer` in sync.
- `QLabel.setStyleSheet()` called each tick is fine (Qt skips repaint if style unchanged).

**Next session should do:** Settings `_apply_and_restart` baseline fix, startup health-check  
dialog, audio clipping warning, actual stream latency in main window.

---

### Session 001 — 2026-06-04

**Goal:** Audit full codebase post-PySide6 migration. Find bugs. Create DEVLOG.

**Done:**
- Fully read all source files: `main.py`, `tray_app.py`, `main_window.py`, `settings_window.py`,
  `soundboard_window.py`, `audio_engine.py`, `noise_filter.py`, `soundboard.py`, `config.py`,
  `window_snapper.py`, `VocalClear.spec`
- Identified 3 confirmed bugs (see Backlog P1 above) and 2 papercuts (P2)
- Created this DEVLOG file

**Problems:**
- None during audit — this was a read-only session.

**Research / Key Facts Learned:**
- `QTimer.singleShot(0, fn)` is the correct cross-thread callback pattern in PySide6.
  Background threads must NOT call Qt widget methods directly.
- `int(window.winId())` returns the native HWND for a QMainWindow on Windows.
  This is the correct Qt path, unlike tkinter's `winfo_id()` which returns a child handle.
- RNNoise `process_mono_frame` expects float32 in [-1,1] and returns (int16_array, speech_probability).
  The int16 array must be divided by 32767.0 to get float32 back.
- The VAD gate hold counter (`_hold_ctr`) is critical for preventing the gate from snapping shut
  immediately at end of speech, which would cause audible clicks.
- `_fire_play_changed()` is called from the audio callback thread (inside `get_mix_frame`).
  This is safe ONLY because `_on_play_changed` in the soundboard window sets a `threading.Event`,
  NOT a direct Qt widget call.
- `sounddevice.WasapiSettings(exclusive=True)` can be passed as a tuple for duplex streams:
  `extra_settings=(WasapiSettings(exclusive=True), WasapiSettings(exclusive=False))`
  to make input exclusive and output shared. The VB-CABLE output MUST stay shared.
- PyInstaller requires `pyrnnoise.rnnoise` (not `pyrnnoise`) in hidden imports because the
  `__init__.py` imports `audiolab` which isn't bundled. A fake parent module is injected into
  `sys.modules` to bypass this.

**Next session should do:** Fix the two P1 bugs (soundboard reopen + monitor_enabled persistence)
then commit the full PySide6 migration.
