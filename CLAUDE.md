# VocalClear — Developer Context

Real-time microphone noise suppression for Windows. Runs as a system tray app and routes clean audio through VB-CABLE so Discord/Zoom picks it up automatically.

## How to Run

```bash
"%USERPROFILE%\.vocalclear\bin\VocalClear.exe" main.py   # intended fix — see open issue below, not yet confirmed working
pythonw main.py          # silent launch (shows as "Python" in Task Manager)
python main.py           # with console — useful for debugging
```

## Process Identity Launcher (`windows_identity.py`) — attempted fix, NOT yet working

**Status: OPEN PROBLEM.** Task Manager still displays the process as "Python" even after this
launcher was built and wired in. Do not assume this is solved — see the Open Investigation
section below before touching this again.

Task Manager's name column comes from the **version-info resource of the exe hosting the
process** — running via pythonw.exe always shows "Python"; no install/shortcut/AUMID changes
that. Attempted fix (2026-07-04): `windows_identity.py` builds a self-contained launcher at
`%USERPROFILE%\.vocalclear\bin\`:
- `VocalClear.exe` — byte copy of pythonw.exe with VERSIONINFO (FileDescription="VocalClear")
  and icon-group resources rewritten via Win32 `UpdateResource` (ctypes; note: explicit
  `restype`/`argtypes` are mandatory or 64-bit HANDLEs truncate → error 87)
- `python312.dll`/`python3.dll`/`vcruntime140*.dll` copied beside it (loader searches exe dir)
- `VocalClear._pth` + `python312._pth` — bake the real install's `sys.path` (embeddable-distro
  mechanism); regenerate by running `python windows_identity.py` from the real interpreter
- `source.json` — records the source install for staleness checks

The Run registry key, the desktop shortcut, and `config.set_startup` all point at this
launcher. `main.py` refreshes it best-effort on startup (daemon thread; no-op unless Python
was upgraded; silently skips if the exe is locked by the running instance). C:\Python312 is
NOT writable without elevation — that's why the launcher lives in the user profile.

### Open Investigation — Task Manager still shows "Python" (2026-07-11+)

User reports Task Manager continues to display the running process as "Python" after this
launcher was built, `FileDescription` was verified correct on the exe itself, and the Run
registry key + desktop shortcut were both repointed at `VocalClear.exe`.

**What was actually verified working (do not re-verify, these are confirmed facts):**
- `windows_identity.ensure_launcher()` successfully builds `%USERPROFILE%\.vocalclear\bin\VocalClear.exe`.
- Reading the built exe's `FileDescription` back via `version.dll` (`GetFileVersionInfoW`/
  `VerQueryValueW`) returns `"VocalClear"`.
- `(Get-Item VocalClear.exe).VersionInfo.FileDescription` in PowerShell independently confirms
  `"VocalClear"` on the same file.
- The launcher successfully imports the full app dependency stack (numpy, sounddevice,
  soundfile, PySide6) — it is not a broken/non-functional copy.
- Registry `HKCU\...\Run\VocalClear` and the desktop `.lnk` were both updated to point at
  `VocalClear.exe`, confirmed by reading them back.

**What is NOT yet confirmed — likely where the real bug is:**
- Whether the user's running process, at the moment they checked Task Manager, was actually
  hosted by the new `VocalClear.exe` rather than a stale `pythonw.exe` instance started before
  the fix (a leftover `pythonw.exe` process was observed still running at the end of the prior
  session — PID 6592 — and the user was told to quit-from-tray and relaunch; not confirmed
  whether that relaunch happened via the updated shortcut specifically, vs. Start Menu search,
  a pinned taskbar icon, or some other stale launch path that might not have been repointed).
- Whether the exe actually launches successfully at all in practice — a byte-patched, unsigned
  copy of `pythonw.exe` could be silently blocked or altered by Windows Defender / SmartScreen
  / AV real-time protection, which would not necessarily surface as a visible error if something
  upstream falls back silently.
- Whether Task Manager's simple "Processes" tab (grouped/friendly view) actually sources its
  name the same way the "Details" tab / `version.dll` does — this was assumed based on general
  Win32 behavior but never directly confirmed against Task Manager's actual rendering on this
  machine. The Details tab's "Description" column is the more reliable thing to check next,
  as a way to isolate whether this is a resource problem (would fail there too) or a
  Task-Manager-simple-view-specific quirk (would show correctly there but not in Processes).
- Whether Windows Shell/Task Manager caching (icon cache, running-app identity cache) from the
  years of `pythonw.exe` being launched under this exact AppUserModelID is holding onto a stale
  "Python" association that a plain relaunch doesn't invalidate — would need first-principles
  testing (fresh reboot, or `taskkill` + Explorer restart) to rule in or out.

**Next session should:** get the user to (1) fully quit VocalClear from the tray, confirm via
Task Manager's Details tab that no `pythonw.exe`/`VocalClear.exe` process remains, (2) launch
strictly via the Desktop shortcut, (3) check BOTH the Processes tab name AND the Details tab
Description column, and report what each shows — that single data point determines whether this
is a resource/build problem (fix code) or a caching/stale-process problem (no code change
needed, just a clean relaunch procedure). Do not modify `windows_identity.py` again until that
diagnostic step narrows down which of the two failure modes above is actually occurring.

## How to Build (PyInstaller exe)

```bash
python -m PyInstaller VocalClear.spec --noconfirm
# Output: dist\VocalClear\VocalClear.exe
```

**Always kill any running VocalClear.exe before building** — it locks `dist\VocalClear\_internal\` and the build will fail with PermissionError.

## Architecture

```
main.py              Entry point — single-instance mutex, logging to ~/.vocalclear/vocalclear.log
tray_app.py          PySide6 QSystemTrayIcon, menu, toggle, settings/soundboard launchers, _ToastOverlay
audio_engine.py      sounddevice duplex stream (48kHz, block=480 default), calibration watchdog
noise_filter.py      3-tier noise engine (see below)
settings_window.py   PySide6 QMainWindow, dark oscilloscope-theme settings GUI
soundboard.py        Sound dataclass, file watcher, playback via sounddevice, WinMM local monitor mixer
soundboard_window.py PySide6 soundboard UI (hotkey feature was removed — caused AppHangB1)
main_window.py       PySide6 main app window (VU meter, history graph, status, toggle/mute buttons)
window_snapper.py    Win32 SetWindowPos-based window snapping between main/settings/soundboard
config.py            JSON config at %USERPROFILE%\.vocalclear\config.json
icon.py              PIL-based tray/window icon generator (RGBA, equalizer bars)
VocalClear.spec      PyInstaller build spec (see PyInstaller Notes below)
```

All windows run on a single Qt main thread/event loop (one `QApplication`) — there is no separate Tk thread or pystray daemon thread; this was migrated from an earlier tkinter+pystray architecture (do not re-introduce daemon-thread UI windows).

## Audio Pipeline

```
Real mic (WASAPI, 48 kHz) → sounddevice duplex Stream (block_size=480 by default, config-driven)
  → RNNoise/Wiener: processed INLINE in the callback (no queue — cuts ~10ms latency)
  → DeepFilterNet only: input queue → dedicated processing thread → output queue (too heavy for inline)
  → soundboard mix → CABLE Input (VB-Audio Virtual Cable)
  → Discord/Zoom reads from CABLE Output
```

## NoiseFilter — 3-tier cascade (`noise_filter.py`)

| Tier | Backend | Status | Notes |
|------|---------|--------|-------|
| 1 | DeepFilterNet3 | Optional | `pip install deepfilternet` — needs MSVC Build Tools |
| 2 | RNNoise | **Active in exe** | `pip install pyrnnoise` — pre-built wheels |
| 3 | Wiener filter | Always available | Pure Python fallback |

Auto-selected at startup. `NoiseFilter.backend` → `"deepfilter"` / `"rnnoise"` / `"wiener"`.

**Strength slider** → `NoiseFilter.strength` (0.0–1.0) — controls VAD threshold (RNNoise) or spectral suppression amount (Wiener).

## Config (`config.py`)

Stored at `%USERPROFILE%\.vocalclear\config.json`. Real defaults (`config.py` `DEFAULTS`):
```json
{
  "input_device": null,
  "output_device": null,
  "strength": 0.50,
  "enabled": true,
  "start_with_windows": false,
  "sample_rate": 48000,
  "block_size": 480,
  "wasapi_exclusive": false,
  "output_gain": 1.0,
  "ptt_enabled": false
}
```
`block_size` default is 480 (10 ms at 48 kHz — matches the RNNoise frame size exactly, no carry-over needed). Schema migration (`schema: 0→1`) resets any saved `block_size` outside `[64, 480]` back to 480 — it does NOT upgrade to 4096; that note was stale. `sample_rate` in config is ignored at runtime — actual rate is auto-detected from the output device.

## PyInstaller Notes (`VocalClear.spec`)

Critical hidden imports that MUST stay in the spec:
- `pystray._win32` — Win32 tray backend
- `PIL.IcoImagePlugin` — **required** for pystray to render the tray icon. pystray serializes the icon to a temp `.ico` file via PIL. Without this plugin the file is corrupt, LoadImage returns NULL, and the icon is invisible (no exception thrown).
- `PIL.PngImagePlugin`, `PIL.BmpImagePlugin` — needed for PIL ICO encoding
- `pyrnnoise.rnnoise` — the ctypes-only submodule (bypasses audiolab dependency)

**pyrnnoise bundling**: `pyrnnoise/__init__.py` imports `audiolab` (not bundled). The exe bypasses this by injecting a fake `pyrnnoise` package into `sys.modules` before importing `pyrnnoise.rnnoise` directly. See `noise_filter.py` Tier 2 init code. `rnnoise.dll` is placed at `_internal/pyrnnoise/rnnoise.dll` matching the `__file__`-relative path the module uses.

## Known Tray Icon Rules (pystray)

1. **`icon.visible = True` must be set in the setup callback**. If you pass a custom `setup=` to `icon.run()`, pystray does NOT auto-show the icon — you must call `icon.visible = True` yourself. The default (no setup) auto-shows.
2. **Cross-thread icon updates are guarded by `self._icon_lock`**. Don't call `self._icon.icon = ...` or `self._icon.title = ...` from multiple threads without acquiring this lock.
3. **Keepalive thread** calls `icon.title = self._tooltip()` every 30 s to prevent Windows from auto-hiding the icon to the `^` overflow after a period of inactivity.

## Audio Engine Key Facts

- Output device is always auto-detected (finds VB-CABLE via name matching, no UI selector)
- Input device is user-selectable in Settings
- Calibration only applies to Wiener filter (RNNoise/DeepFilterNet are self-calibrating)
- Stream restart: skips if only input device changed; retries 3× with 1.5 s delays (WASAPI needs time to release)

## Bugs Fixed (Historical — don't re-introduce)

- **Cross-thread `QTimer.singleShot(0, fn)` without a context object**: from a plain Python thread the 2-arg form does NOT queue onto the Qt main thread (Qt creates the temp receiver in the *calling* thread — the functor runs on the worker thread, or never). Always use the 3-arg form with a main-thread QObject: `QTimer.singleShot(0, ctx, fn)`. All sites fixed in session 009 (2026-07-04).

- **AppHangB1 on soundboard hotkeys**: `wait_window()` inside Tk event callback nested event loop → deadlock. Fixed by removing hotkey feature entirely.
- **Wiener static/glitching**: block_size=1024 + no OLA carry buffer → boundary artifacts. Fixed: block_size=4096, cross-block OLA with `_prev_input`.
- **Echo from friends' voices**: VAD residual was 5% (leaked speaker bleed). Fixed to hard zero; VAD threshold raised.
- **Invalid sample rate**: Hardcoded 16kHz vs VB-CABLE's 48kHz. Fixed to auto-detect.
- **Mic echoing after silence** (VAD gate re-open): A single 10 ms frame above `vad_thresh` was enough to reopen the gate after silence. RNNoise gives high speech_prob to headphone bleed (it IS real speech, just leaked), so bleed trivially reopened the gate. Fixed with a two-stage debounce in `_process_rnnoise`: after ≥1.5 s of gate-closed silence, requires 5 consecutive frames (50 ms) above `vad_thresh + 0.20` before reopening. Even in normal mode a 2-frame (20 ms) debounce is applied. Do NOT collapse this back to single-frame threshold checks.
- **Mic echoing after silence, round 2** (amplitude-relative floor): the debounce above wasn't fully sufficient because `speech_prob` is a spectral-shape classifier with no notion of loudness — quiet headphone bleed that resembles speech spectrally still scored high confidence. Added a second, independent gate signal: in strict mode, also require the raw frame's RMS to exceed a learned ambient/bleed noise floor (`_noise_floor_rms`) by `_RMS_MARGIN` (3x, ~+9.5 dB). Root-caused on the user's machine to an **open-back headset (Drop+Sennheiser PC38X) leaking audio acoustically by design, stacked with a +20 dB Windows mic Boost** amplifying that leak to speech-like loudness — see test plan below. This headset has no companion app/onboard DSP; all gain knobs are Windows/Realtek driver settings, not firmware. **User confirmed the echo fixed on 2026-07-11.**

- **Mic dead for the first seconds after silence** (strict-gate lockout, fixed 2026-07-11): the amplitude floor above was originally a plain EMA over all low-`speech_prob` frames. Breaths and unvoiced fricatives (s/f/sh) are LOUD but score low speech-probability, so speaking into a closed gate inflated the floor toward speech level — the 3× RMS check then rejected the user's own voice, and whole utterances were swallowed until a few seconds of silence decayed the floor back down (reproduced deterministically in simulation; boost/strength changes can't help because it's a ratio test). Fixed by making the floor a **minimum statistic**: adapt down fast (`_RMS_FLOOR_ALPHA_DOWN`), drift up slowly (`_RMS_FLOOR_ALPHA_UP`), and NEVER learn from frames louder than `_RMS_FLOOR_LEARN_CAP`(2×) the current floor. Also `_speech_run` now decays by 2 on a failing frame instead of hard-resetting, so one 10 ms dip (stop consonant) doesn't erase a reopen run. Do NOT revert the floor to a plain EMA and do NOT restore the hard reset — quiet-bleed blocking was verified intact with both changes.

## Open Investigation — Mic Echo After Silence (2026-06-25)

User reports mic picks up and echoes friends' voices specifically after a period of not speaking. Two software-side VAD fixes have shipped (see Bugs Fixed above) but the user had not yet confirmed they fully resolve it at time of writing. Root-cause analysis points to a **hardware/Windows-config contributing factor that software cannot fully override**: the headset (Drop+Sennheiser PC38X) is open-back (leaks audio by design) and the user's Windows mic had **Boost set to +20.0 dB** on top of Microphone level 74/100 — likely amplifying the acoustic leak past the point any VAD can distinguish it from real speech.

**Diagnostic test plan (run in this order, retest with friends after each):**
1. Lower Windows mic Boost from +20.0 dB to 0 dB (Sound Control Panel → Recording → mic → Properties → Levels). Single highest-leverage, free test.
2. Uncheck "Enable audio enhancements" (Properties → Advanced tab) — undocumented DSP bundle that may double-process alongside RNNoise.
3. Fully quit (tray → Quit) and relaunch VocalClear so both VAD fixes above are actually active in the running process (edits to `noise_filter.py` only take effect on next process start).
4. If `PaErrorCode -9984` ("Incompatible host API specific stream info") recurs and blocks testing: this is a **pre-existing, unrelated** flaky WASAPI-exclusive negotiation issue (seen in the log on 2026-06-05, 2026-06-20, and 2026-06-25, long before these fixes) — toggle WASAPI Exclusive Mode off in Settings as a workaround, trading ~3 ms latency for reliability.

**Ruled out during investigation:** stale PyInstaller exe (both desktop shortcut and Windows startup registry launch `pythonw.exe main.py` from source, confirmed via `reg query`/shortcut inspection — never the exe), WASAPI sample-rate mismatch silently corrupting audio (PortAudio/WASAPI fails loudly on real mismatches, never silently resamples in exclusive mode), DeepFilterNet being the active backend (not installed; `rnnoise` confirmed active via log).

**RESOLVED (2026-07-11):** the user confirmed the echo is fixed (after lowering Boost from +20 to +10 dB with the two VAD fixes active). The follow-on regression — mic swallowing the first utterance after silence — was root-caused to floor-poisoning and fixed the same day (see Bugs Fixed above). Kept for history: if echo ever *returns*, the next investigative step is **true acoustic echo cancellation (AEC) with a reference signal** — capturing what's being sent to CABLE Input as a reference and subtracting correlated content from the mic signal — since a VAD-only approach has a structural ceiling it cannot exceed (confirmed via research: this is exactly how Discord/Krisp and NVIDIA Broadcast solve this, and a VAD classifier alone cannot replicate it because it never sees the playback signal). This would be a substantial architecture addition, not a tuning change — do not attempt without confirming the cheaper fixes above were exhausted first.

**Also flagged but not yet re-verified:** `config.json`'s `input_device` index can drift after reboots/driver updates because PortAudio device indices are not stable identifiers — when checked on 2026-06-25, index 33 momentarily enumerated as an output-only device ("Headphones (Realtek HD Audio 2nd output)", 0 input channels) on this machine, though the user confirmed via Settings UI that the actual selected device shown was correct ("System default", matching their headset). Low-confidence finding, included for completeness — not the active root cause, but a latent fragility worth remembering if input device selection ever silently breaks after a Windows update.
