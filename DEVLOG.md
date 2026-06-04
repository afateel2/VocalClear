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

- [ ] **Soundboard close→reopen breaks updates**  
  `soundboard_window.py:closeEvent` stops `_refresh_timer` and nulls `sb._on_sounds_changed/played`.  
  `tray_app._sb_window` is never cleared → reopening just calls `.show()` on the broken object.  
  Fix: in `showEvent` restart the timer and re-register callbacks. In `closeEvent` keep timer stop
  and null callbacks but don't rely on window never being shown again.

- [ ] **`monitor_enabled` not persisted across restarts**  
  `soundboard.py:_save_sounds_config()` saves `master_volume`, `monitor_volume`, `overlap` but
  NOT `_monitor_enabled`. The MONITOR toggle in soundboard window always resets to `True` on launch.  
  Fix: add `"monitor_enabled": self._monitor_enabled` to the `_global` section, restore in `_load_sounds_config`.

- [ ] **Commit the PySide6 migration**  
  All 5 modified files + 3 deletions need to be staged and committed.

### P2 — Papercuts (important, non-critical)

- [ ] **Soundboard window: close button should hide, not destroy**  
  Currently `closeEvent` accepts → Qt hides the window. But the timer is stopped and callbacks nulled,
  meaning reopening gives a broken window. Simplest fix: override `closeEvent` to call `hide()` instead,
  which keeps timer running, callbacks registered, and state intact.  
  (Linked to the P1 bug above — fixing one fixes both.)

- [ ] **Settings `_apply_and_restart` baseline reset bug**  
  Lines 784–786 unconditionally reset `_orig_startup/_orig_strength/_orig_gain` baselines even on a
  no-op save. This means the dirty indicator (APPLY button color) clears for unchanged settings when
  something else triggers an apply. Cosmetic but confusing.

- [ ] **xrun count indicator**  
  `AudioEngine.xrun_count` is tracked but never shown to the user. A brief amber flash in the status bar
  or main window bottom when new xruns are detected would help diagnose performance issues.

- [ ] **Main window: paused state should dim VU meter**  
  When noise suppression is paused (`_status_chip` shows "PAUSED"), the VU meter still animates.
  Dimming the colors to grey/dark-green when paused would make the state more visible at a glance.

### P3 — Features (nice-to-have)

- [ ] **Window position persistence**  
  Remember main window position in config and restore on next launch. Settings/soundboard snap
  positions could also be restored.

- [ ] **Auto-reconnect on device error**  
  If `audio_engine.last_error` is set AND the error looks like a device disconnect, attempt `restart()`
  after a 5-second delay. Cap retry attempts to avoid loops. Show tray notification on each attempt.

- [ ] **VU meter dB tick marks**  
  Add subtle dB labels (−6, −12, −24) on the VU meter widget similar to the history graph.

- [ ] **Keyboard shortcut: global pause/resume toggle**  
  A global hotkey (e.g. Ctrl+Shift+V configurable) to toggle `noise_filter.enabled` without opening
  the window. Different from PTT which requires holding.

- [ ] **CPU/latency live readout**  
  Show current callback processing time in the bottom bar (derived from `audio_engine` timing).

- [ ] **Soundboard: volume normalizer**  
  Button to normalize all loaded sounds to the same RMS target so they play at consistent volumes.

- [ ] **"Test mic" button in settings**  
  Record 2 seconds through the selected input device and play back through monitor so the user can
  verify the mic is working without leaving the app.

- [ ] **Log rotation**  
  `vocalclear.log` grows forever. At startup, if the file exceeds 500 KB trim it to the last 200 lines.
  This keeps the file readable without manual cleanup and prevents disk bloat on long-running installs.

- [ ] **Crash detection on next launch**  
  If the previous session has no "exited cleanly" line (i.e., the last line is "VocalClear starting"),
  show a one-time tray notification: "Last session ended unexpectedly — see log for details."
  Implemented in `main.py` by reading the tail of the log before writing the new start entry.

- [ ] **Startup health-check dialog**  
  On first launch (or when VB-CABLE is missing), show a guided one-time setup notice:
  "VB-CABLE not found — noise suppression is inactive. Install from vb-audio.com then restart."
  Currently the warning only appears buried in Settings. A modal on first bad start is much clearer.

- [ ] **Audio clipping warning in VU meter**  
  If `output_rms` stays above 0.92 for 3+ consecutive ticks, flash the OUT channel red and show
  "CLIP" in the dB readout. Clipping before VB-CABLE means Discord hears distortion.
  Clear automatically after 2 seconds of non-clipping output.

- [ ] **Show actual stream latency in main window**  
  `sounddevice.Stream.latency` returns the measured `(input_latency, output_latency)` tuple after
  the stream opens. Expose this on `AudioEngine` and display it in the main window info card
  (e.g., "LATENCY  4.2 ms in / 8.1 ms out") replacing the estimated value shown in settings.

- [ ] **Mid-session device-loss watchdog**  
  Currently if VB-CABLE resets or the mic is unplugged while the stream is running, the audio
  callback throws and sets `last_error` silently with no recovery. Add a `QTimer`-driven watchdog
  (every 5 s) on the main thread that checks `engine.last_error`; if newly set, attempt
  `engine.restart()` once and notify the tray. Log the attempt and outcome.

- [ ] **PTT live indicator in main window**  
  When PTT mode is enabled, add a small "PTT" chip next to the status chip that glows green while
  the key is held and dims when released. Currently there is zero visual feedback that PTT is active
  or that the key is registering. Check `engine._ptt_active` in the existing `_tick` timer.

- [ ] **Soundboard: per-sound loop toggle**  
  Add "Loop" to the right-click context menu. A looping sound repeats from the start when it ends
  (`_PlayingInstance` needs a `loop: bool` flag; `get_mix_frame` restarts `pos` instead of marking done).
  Show a loop indicator (↺) on the button tile when active.

- [ ] **Soundboard: search / filter bar**  
  Add a small text input above the grid. As the user types, hide buttons whose names don't match.
  No new data model needed — just iterate `self._btns` and call `btn.setVisible(match)`.
  Clear filter on Escape. Useful once the soundboard grows past ~12 sounds.

- [ ] **Centralize dialog stylesheets**  
  The same `QMessageBox` dark-theme CSS block is copy-pasted in `main_window.py`,
  `soundboard_window.py`, and `tray_app.py`. Extract to a single `_style_dialog(mb)` helper
  in a shared `ui_utils.py` module. Reduces drift if the palette ever changes.

- [ ] **Config schema version**  
  Add `"schema": 1` to the saved JSON. When `config.py:load()` reads an older config without
  this field, it's schema 0 — run any needed migrations before updating. Currently migrations
  are ad-hoc (`block_size > 480 → reset`). A version field makes future migrations systematic.

- [ ] **Tray tooltip: show live input dB**  
  Update the tray tooltip every 5 s (via the keepalive timer that `QSystemTrayIcon` needs anyway)
  to include the current input level: "VocalClear – Active → CABLE Input  |  −18 dB".
  Gives at-a-glance confirmation the mic is active without opening the main window.

---

## Session Log

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
