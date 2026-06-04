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
