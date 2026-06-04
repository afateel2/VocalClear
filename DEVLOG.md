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
