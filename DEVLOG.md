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

- [~] **Settings `_apply_and_restart` baseline reset bug** *(closed — cosmetic only)*  
  On failed restart the APPLY button dims even though the restart failed, but the error is clearly  
  shown in the status label. Not worth the added complexity to fix.

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

- [x] **Keyboard shortcut: global pause/resume toggle** *(done — shipped as mute/unmute toggle, not via RegisterHotKey)*  
  Actual implementation differs from the plan above: R-CTRL+`\` polled via `QTimer` calling  
  `GetAsyncKeyState` every 50 ms on the Qt main thread (`tray_app.py::_check_toggle_hotkey`),  
  not `RegisterHotKey`/`WM_HOTKEY`. Pairs with `_ToastOverlay` (screen-corner toast) and a  
  `winsound.Beep` audio cue. This polling pattern is proven safe and is the template to reuse  
  for any future hotkey (e.g. soundboard sound-trigger hotkeys, see backlog below) — simpler  
  than `RegisterHotKey` and avoids needing a native event filter.

- [x] **CPU/latency live readout** *(done session 006)*  
  `engine.process_time_ms` is a 10-sample rolling average of `noise_filter.process()` wall time  
  in the inline callback path. Displayed in the LATENCY info card row as "· 0.42 ms cpu";  
  refreshed every 20 ticks (~1 s).

- [x] **Soundboard: volume normalizer** *(done session 007)*  
  `soundboard.normalize_volumes(target_rms=0.05)` scales per-sound volumes so all play at equal  
  loudness; "≈ NORMALIZE" button in header action row.

- [x] **"Test mic" button in settings** *(done session 007)*  
  "[ TEST MIC — 2 s ]" button in the Input Device card. Records 2 s via `sounddevice.rec()` in  
  a background thread then plays back via `sounddevice.play()`. Shows "disable WASAPI exclusive  
  mode first" warning when exclusive mode is on (exclusive mode blocks concurrent readers).

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

- [ ] **Software AGC/leveler** *(researched session 008, not implemented — see FEATURE_IDEAS.md)*  
  RMS gain-follower applied post-noise-suppression, pre-output. Small, well-established DSP,  
  no new dependency. Awaiting user go-ahead.

- [ ] **True acoustic echo cancellation (AEC)** *(researched session 008, not implemented — see  
  FEATURE_IDEAS.md)* High-risk/high-effort: no maintained pip-installable Windows AEC3/Speex  
  binding without a C compiler, and no WASAPI loopback support in `sounddevice` to capture the  
  reference signal it needs. Do not start without explicit user sign-off.

- [ ] **Soundboard hotkeys v2** *(researched session 008, not implemented — see  
  FEATURE_IDEAS.md)* The original hotkey feature was removed for a Tk-specific deadlock  
  (`AppHangB1`) that no longer applies post-PySide6-migration. The existing  
  `_check_toggle_hotkey` `QTimer`-polling pattern in `tray_app.py` is already proven safe and  
  reusable for this.

- [ ] **Bleed/echo live indicator** *(researched session 008, not implemented — see  
  FEATURE_IDEAS.md)* Surface the VAD instrumentation added in session 008 (strict-mode  
  blocks, noise-floor margin) to the main window UI so the user gets visual feedback instead  
  of relying on friends to notice.

---

## Session Log

> **Note (2026-06-26):** Sessions 001–007 below predate a long stretch of regular (non-loop)  
> development covering many features and fixes not logged in this format — mute/unmute,  
> PTT, output gain, window snapping, soundboard export/import, the full PySide6 polish pass,  
> and more. See `git log` for the authoritative history of that period; this file resumes  
> loop-session logging below rather than backfilling it.

---

### Session 010 — 2026-07-11 (mic-dropout after silence: strict-gate lockout)

**Goal:** User confirms the echo is FIXED, but reports a new symptom: sometimes speech produces
no output at all, and waiting a few seconds then speaking again makes it work. Troubleshoot.

**Root cause (reproduced deterministically before fixing):** the amplitude-relative noise floor
added in session 008 was a plain EMA over all frames with `speech_prob < vad_thresh`. Breaths
and unvoiced fricatives (s/f/sh) are loud but spectrally noise-like → low speech_prob → they
fed the floor. Speaking into a closed strict-mode gate therefore inflated the floor toward
speech level, and the 3× RMS reopen check started rejecting the user's own voice — a
self-locking feedback loop. A few seconds of silence decayed the EMA back down, which is
exactly the user's "wait then retry" workaround. Boost/strength changes can't help: both sides
of the comparison scale together (ratio test). Simulation (stubbed `_rn_proc`, controlled
prob/RMS): 2 s quiet → 300 ms breath → 1 s confident speech = **entire utterance swallowed**;
after 3 s wait, retry opens in 40 ms. Log ruled out watchdog restarts; config ruled out the
session-009 FIFO (block_size=480 → pass-through) and PTT (disabled).

**Fix (`noise_filter.py`):**
- Floor is now a **minimum statistic**: `_RMS_FLOOR_ALPHA_DOWN=0.05` (fast decay toward quieter
  ambient), `_RMS_FLOOR_ALPHA_UP=0.02` (slow drift up), and frames louder than
  `_RMS_FLOOR_LEARN_CAP=2.0×` the current floor are never learned (transients).
- `_speech_run` decays by 2 on a failing frame instead of hard-resetting to 0, so a single
  10 ms low-confidence frame (stop-consonant closure) doesn't erase a reopen run.

**Verified:** repro scenario now opens the gate in 40 ms with the floor unpoisoned (0.001 vs
0.028 before); quiet speech-like bleed (0.85 prob, 2× floor) still fully blocked in strict mode
(echo protection intact); dipped-onset opens via grace at 70 ms; full 18-check smoke suite
passes. User must restart VocalClear to pick up the fix (running instance predates it).

---

### Session 009 — 2026-07-04 (full audit → resource optimization → UI/UX pass)

**Goal:** Three-phase pass requested by the user: audit for latent bugs and fix them,
lower resource usage, then refine UI/UX and small features.

**Done — audit fixes (correctness):**
- **Cross-thread `QTimer.singleShot(0, fn)` without a context object** (5 sites): from a plain
  Python thread the zero-timeout functor form does NOT queue onto the GUI thread — Qt creates a
  temp receiver in the *calling* thread, so the callback runs on the worker thread (or never).
  Fixed by passing a main-thread QObject context, matching the pattern already used in
  `settings_window._restart`: tray watchdog success/failure (`self._tray`), settings calibrate
  done + test-mic done (`self`), soundboard export/import status (`self`).
  ⚠ The "Key facts" note from session 001 saying `QTimer.singleShot(0, fn)` is the correct
  cross-thread pattern is WRONG — always pass a context QObject: `QTimer.singleShot(0, ctx, fn)`.
- **RNNoise small-block audio corruption** (`noise_filter.py`): with `block_size < 480` the old
  code (a) returned **raw unfiltered mic audio** whenever no full 480-frame was ready — bypassing
  denoise AND the anti-echo VAD gate — and (b) emitted only `len(audio)` of each processed
  480-chunk, silently discarding the rest (416 of every 480 samples at block 64). Replaced with a
  processed-output FIFO (`_rn_out`): silence while priming, then gap-free streaming. At the
  default 480 block the FIFO is exact pass-through (no behavior/latency change).
- **PTT modifiers ignored at runtime**: capture dialog stored "CTRL+X" but `_ptt_loop` polled only
  X, so the bare key opened the mic. New `ptt_mods` config key (list of modifier VKs) captured by
  the dialog and enforced in `_ptt_loop`. Old configs (no mods) behave as before.
- **Settings input-device persisted before Apply**: changing the combo wrote config.json
  immediately, so closing without Apply still switched devices on next launch. Combo is now a
  pending selection (`_pending_input_device()`); config is written only in `_apply_and_restart`.
  TEST MIC now records from the *pending* selection (what Apply would activate).
- **Always-on tanh distorted the mic**: `_mix_soundboard` soft-clipped mic+SFX even with zero
  sounds playing (tanh(0.9)≈0.716 — real compression of speech peaks). Added
  `SoundBoard.has_playing()` fast path: mic passes untouched when the board is idle; tanh only
  guards actual mixes. Also removed the double-tanh (get_mix_frame no longer clips — the engine
  clips the final sum once).
- `config.py` docstring `\.` invalid-escape SyntaxWarning fixed (raw string).
- Removed dead code: `NoiseFilter.feed_calibration` + `_calibration_frames` (no callers).

**Done — resource usage:**
- Audio callback hot path: `indata[:, 0]` view instead of `.flatten()` copy; `_write_out()`
  writes into `outdata` in place (replaces per-callback `column_stack` alloc); dot-product RMS
  (`_rms`) replaces `mean(x**2)` temps (also inside the VAD gate loop); `_apply_gain` clips
  in place; RNNoise int16→float32 divide done in place. Idle-soundboard fast path skips a
  480-sample zeros alloc + add + tanh per callback (100×/s).
- `SnapManager._poll`: hidden windows (main window closed to tray — the dominant idle state) now
  poll at 500 ms instead of 25 ms (40/s → 2/s wakeups + Win32 calls, per window).
- Tray icons rendered once (both states cached) instead of PIL redraw + QIcon conversion per
  toggle. Soundboard refresh timer unified to `TICK_MS` (was 50 ms on first open, 100 ms after).

**Done — UI/UX (kept to existing palette tokens; FEATURE_IDEAS.md items untouched — still gated):**
- All custom-painted buttons (`_IconButton`, `_GlowButton`, `_HeaderBtn`/`_SmallBtn`) now have a
  pressed visual state and fire on **release-inside** (drag off to cancel — matters for QUIT).
  Sound tiles intentionally still fire on press (soundboard latency). `_SmallBtn` deduplicated to
  class attrs (`_FONT`/`_PAD_*`) instead of a copy-pasted paintEvent.
- Mouse-wheel support on all bar sliders (strength, gain, SFX master, volume popup) — 5% per
  notch, persists like a drag release.
- Tooltips across all three windows (main buttons/chips, settings sliders + test mic + apply,
  soundboard header/toggles/SFX bar/tiles); QToolTip QSS added to settings + soundboard windows.
- Global mute hotkey discoverable: bottom hint bar now reads
  "X closes to tray · R-CTRL+\\ toggles mute" (was near-invisible #1a3320; now #588a62).
- Soundboard: new "⌂ FOLDER" header button opens the sounds folder in Explorer.
- Volume popup: live preview — volume applies in-memory while dragging (audible if the sound is
  playing), SAVE persists, Cancel/X rolls back to the original.

**Verification:** all modules compile with `-W error::SyntaxWarning`; 18-check synthetic smoke
test (RNNoise FIFO at 480 + 64 blocks: no raw leak, gap-free post-gate stream; engine helpers;
gain clip; has_playing) passes; offscreen (`QT_QPA_PLATFORM=offscreen`) construction of all three
windows + volume popup + wheel-event math passes with the real RNNoise backend active.

**Problems:** none blocking. Note the audio-path changes (FIFO, idle fast path, in-place writes)
are covered by synthetic tests but should get a real voice-call listen before being trusted.

**Next session should do:** live listening test of the audio path; revisit FEATURE_IDEAS.md items
if the user approves any (AGC is the researched first candidate).

**Addendum (same session) — Windows identity + titlebar:**
- **Task Manager "Python" name — attempted fix, STILL OPEN, not resolved.** Root cause
  diagnosis: the Processes-tab name is the VERSIONINFO `FileDescription` of the exe file hosting
  the process — for `pythonw.exe main.py` that is always "Python"; installs/shortcuts/AUMIDs
  cannot change it. Built `windows_identity.py`: self-contained launcher at
  `%USERPROFILE%\.vocalclear\bin\VocalClear.exe` (patched pythonw copy — version + icon
  resources rewritten via `UpdateResource`; runtime DLLs copied beside it; `._pth` files pin the
  real install's sys.path). Verified in isolation: the built exe's own FileDescription reads
  "VocalClear" via both `version.dll` and PowerShell, and the launcher correctly imports the
  full dependency stack and passes the offscreen GUI smoke test. Registry Run key + desktop
  shortcut (OneDrive desktop) repointed; `config.set_startup` and `create_shortcut.py` prefer
  the launcher; `main.py` refreshes it best-effort at startup.
  **However: user reports Task Manager still shows "Python" after this.** None of the above
  verification actually confirms Task Manager itself renders the fixed name — that step was
  never directly checked. Leading suspects for next session (untested, do not assume): a stale
  `pythonw.exe` process left over from before the fix (one was observed still running,
  PID 6592, at end of the prior session), a launch path that bypasses the updated shortcut,
  Windows Defender/SmartScreen silently interfering with the unsigned patched exe, or Task
  Manager/shell caching. See the "Open Investigation" section in CLAUDE.md for the full
  diagnostic breakdown — do not attempt further code changes here until that's narrowed down.
  ctypes gotcha (still valid, keep): `BeginUpdateResourceW` MUST have `restype=HANDLE` or the
  handle truncates on 64-bit (error 87).
- **vocalclear.ico was broken** (196 bytes, single 16×16 image): `make_ico` saved the 16px image
  first and Pillow silently drops requested sizes larger than the base image. Fixed
  largest-first; regenerated (4.2 KB, 16–256px). This also sharpens taskbar/alt-tab icons.
- **AppUserModelID unified** to "VocalClear.App" (main.py had "VocalClear.NoiseSuppress.1",
  tray_app had "VocalClear.App" — split identity fragments taskbar grouping).
- **Settings white titlebar**: dark-titlebar call centralized in `ui_utils.apply_dark_titlebar`
  (proper HWND/DWORD ctypes types, attribute 20 with 19 fallback) and now RE-ASSERTED in every
  window's `showEvent` — DWM can ignore the attribute when set before a window has ever been
  composed, which is how a lazily-created window ends up white while its siblings are dark.

---

### Session 008 — 2026-06-26 (mic-echo investigation + broad improvement pass)

**Goal:** Root-cause a recurring "mic echoes friends' voices after silence" report, fix what's  
fixable in software, then use idle time (user waiting for friends to test) for a bug/perf/UI/UX  
pass and feature research.

**Done:**
- Root-caused the echo report through elimination, not guesswork: confirmed `rnnoise` is the  
  active backend (not stale builds — both the desktop shortcut and the Windows `Run` registry  
  key launch `pythonw.exe main.py` from source, never the 3-month-stale `dist\` exe); ruled out  
  WASAPI sample-rate mismatch (fails loudly, never silently corrupts); identified the user's  
  headset (Drop+Sennheiser PC38X) is **open-back** (leaks audio by design, no companion app/  
  onboard DSP) and their Windows mic **Boost was +20.0 dB** on top of level 74/100 — likely  
  amplifying the acoustic leak past where any VAD can tell it apart from real speech.
- Added a second, independent VAD-gate signal: in strict mode (after `_LONG_SILENCE_FRAMES`),  
  also require the raw frame's RMS to exceed a learned ambient/bleed noise floor by `_RMS_MARGIN`  
  (3x, ~+9.5 dB) — patches the gap where RNNoise's `speech_prob` (spectral-shape only, no  
  loudness awareness) gets fooled by clean bleed. See `noise_filter.py::_process_rnnoise`.
- Hardened `noise_filter.process()` against clipped input (samples past ±1.0, plausible given  
  high mic Boost): `pyrnnoise.process_mono_frame` silently skips its int16 conversion when input  
  is out of range and then asserts; that exception was being caught by `_stream_callback` but  
  set `last_error`, which the watchdog saw as a stream fault and triggered a full **engine  
  restart** (audible glitch) for what should be a harmless clipped sample. Now clips to [-1, 1]  
  first when out of range (only allocates when actually needed — checked via cheap min/max scan).
- Perf: replaced unconditional `.astype(np.float32)` copies with `np.asarray(..., dtype=...)`  
  (no-copy when already float32) on the per-block audio hot path in `audio_engine.py` and  
  `noise_filter.py`; removed two fully-redundant double-casts. Verified via a synthetic  
  300-block smoke test through the real `pyrnnoise` backend (not just `ast.parse`).
- UI/UX: computed actual WCAG 2.2 contrast ratios (not eyeballed) for every "dim" text color  
  against the specific backgrounds it appears on. Three of them were failing badly (ratios  
  1.5–2.0 — effectively invisible, not just intentionally muted) for text that's actually read  
  (field labels, hints, idle-state button labels). Replaced with minimal hue-matched lighter  
  variants that clear AA while preserving the dim/secondary visual weight. See commit  
  `d7a6288` for the exact before/after hex values and which Qt widgets use them.
- Fixed stale docs in `CLAUDE.md`: it said tkinter (actual: PySide6 throughout, migrated long  
  ago) and `block_size` default 4096 (actual default is 480, matching the RNNoise frame size  
  exactly — the "1024 → 4096" migration note was simply wrong/outdated).
- Researched (not implemented) two larger feature candidates via a background agent — see  
  `FEATURE_IDEAS.md`: true AEC (acoustic echo cancellation, the structurally-correct fix for  
  the echo problem) is high-risk/high-effort on this stack — no maintained, pip-installable  
  Windows AEC3/Speex binding without a C compiler, and `sounddevice`'s WASAPI backend has no  
  loopback-capture support (open `python-sounddevice` issue #281) so there's no clean way to  
  even get the reference signal it would need. A software AGC/leveler (RMS gain-follower,  
  post-noise-suppression) is the opposite: small, well-established DSP, no new dependency,  
  and would let the user turn off the Windows mic Boost that's the likely actual trigger.

**Problems:**
- Hit a *different*, pre-existing crash while the user was testing (`PaErrorCode -9984`,  
  "Incompatible host API specific stream info"). Confirmed via log timestamps this is NOT  
  caused by anything in this session — identical error appears on 2026-06-05 and 2026-06-20,  
  long before today. It's the known flaky WASAPI-exclusive negotiation timing issue already  
  documented above ("Stream restart... WASAPI needs time to release"). Workaround: toggle  
  `wasapi_exclusive` off in Settings if it blocks testing.
- My first attempted smoke test of the RNNoise pipeline failed with an `AssertionError` deep  
  inside `pyrnnoise` — turned out to be my own synthetic test data occasionally exceeding  
  ±1.0 (Gaussian noise has unbounded tails), not a real bug — but tracing *why* it failed is  
  exactly what surfaced the clip-hardening issue above, which **is** real and relevant given  
  this specific user's high mic Boost. Worth remembering: a "broken test" is sometimes pointing  
  at a real edge case, not just bad test data — check both before dismissing either.

**Research / Key Facts:**
- `np.asarray(x, dtype=np.float32)` vs `x.astype(np.float32)`: identical output, but `asarray`  
  skips the copy when `x` is already the target dtype; `astype` always copies unless you pass  
  `copy=False` explicitly. Worth defaulting to `asarray` for cast-only hot-path code generally.
- PortAudio device indices (as returned by `sounddevice.query_devices()`) are **not stable**  
  across reboots/driver updates/device hot-plug — confirmed by observing the same index (33)  
  point to a completely different, output-only device in this session vs. presumably whenever  
  the user last picked it in Settings. `config.json` stores the raw integer index. Low-confidence  
  but real latent fragility — not the active root cause here (the user confirmed via Settings UI  
  the actual selection was correct), but worth remembering if device selection ever silently  
  breaks after a Windows update. A name-based or persistent-ID-based device match (with index  
  as a fallback hint) would be more robust than a bare index — not worth doing speculatively,  
  but worth reaching for if this surfaces as a real complaint later.
- `python-sounddevice` has no WASAPI loopback support (open issue #281); `PyAudioWPatch` (a  
  PyAudio fork) is the common workaround used by other projects needing Windows loopback  
  capture. Relevant if AEC or any "hear what's being played back" feature is ever greenlit.
- `pyrnnoise.process_mono_frame`'s float→int16 conversion is conditional  
  (`if dtype in (float32,float64) and -1<=min and max<=1`) and falls through to an `assert`  
  if the condition is false — i.e. it has no defensive clamp of its own. Any caller passing  
  audio that could exceed [-1,1] must clip before calling it; this project's high-Boost user  
  is exactly the scenario where that assumption could break in the real world, not just in  
  theory.

**Next session should do:** Wait for the user's test results (lowering mic Boost to 0dB,  
disabling Windows audio enhancements, relaunching). If the echo persists even with Boost at  
0dB and a fresh relaunch, the next real investigative step is prototyping the AGC/leveler  
(small, low-risk, see `FEATURE_IDEAS.md`) so the user has a path to keep their needed loudness  
without hardware Boost amplifying whatever bleed remains. Do not start on AEC without an  
explicit go-ahead — it's a multi-day architecture change with real dependency/feasibility risk,  
not a tuning change.

---

### Session 007 — 2026-06-04 (autonomous loop tick 6)

**Goal:** Soundboard volume normalizer, test mic button, close out remaining backlog.

**Done:**
- `soundboard.normalize_volumes(target_rms=0.05)`: iterates all sounds, computes RMS, sets  
  `volume = min(1.0, target_rms / rms)`; "≈ NORMALIZE" header button triggers it.
- Settings test mic: "[ TEST MIC — 2 s ]" records via `sounddevice.rec(device=input_dev)` in  
  a daemon thread then plays back with `sounddevice.play()`. WASAPI exclusive mode guard added  
  (exclusive mode blocks concurrent device readers). `_on_test_done` marshals status back to  
  the Qt main thread via `QTimer.singleShot(0, ...)`.
- Closed settings baseline bug (cosmetic only, not worth fixing).
- Marked global hotkey as blocked — needs user to specify the key combo.

**Problems:**
- `sounddevice.rec()` opens a second stream on the input device. In WASAPI shared mode this is  
  fine. In exclusive mode it will fail with a device-in-use error, hence the guard.
- `sounddevice.play()` plays to the default output device. If the user has VB-CABLE set as their  
  default Windows output, playback won't be heard. This is an edge case; the function still  
  works — it just plays to wherever the OS sends audio.

**Research / Key Facts:**
- `sounddevice.rec()` and `sounddevice.play()` are blocking convenience wrappers around  
  InputStream/OutputStream. They use the global default device unless `device=` is specified.
- Win32 `RegisterHotKey` requires a window handle (HWND) and a message loop. The simplest  
  approach for VocalClear is to register on the main window's HWND and intercept  
  `WM_HOTKEY` via a Qt native event filter (`QAbstractNativeEventFilter`). This is  
  non-trivial and requires user confirmation of the key combo first.

**Backlog status:** All planned items done except global hotkey (blocked on user input).  
The loop can end here — remaining work requires user decision.

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
