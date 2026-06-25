# VocalClear — Developer Context

Real-time microphone noise suppression for Windows. Runs as a system tray app and routes clean audio through VB-CABLE so Discord/Zoom picks it up automatically.

## How to Run

```bash
pythonw main.py          # silent launch (no console window) — goes to tray
python main.py           # with console — useful for debugging
```

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

- **AppHangB1 on soundboard hotkeys**: `wait_window()` inside Tk event callback nested event loop → deadlock. Fixed by removing hotkey feature entirely.
- **Wiener static/glitching**: block_size=1024 + no OLA carry buffer → boundary artifacts. Fixed: block_size=4096, cross-block OLA with `_prev_input`.
- **Echo from friends' voices**: VAD residual was 5% (leaked speaker bleed). Fixed to hard zero; VAD threshold raised.
- **Invalid sample rate**: Hardcoded 16kHz vs VB-CABLE's 48kHz. Fixed to auto-detect.
- **Mic echoing after silence** (VAD gate re-open): A single 10 ms frame above `vad_thresh` was enough to reopen the gate after silence. RNNoise gives high speech_prob to headphone bleed (it IS real speech, just leaked), so bleed trivially reopened the gate. Fixed with a two-stage debounce in `_process_rnnoise`: after ≥1.5 s of gate-closed silence, requires 5 consecutive frames (50 ms) above `vad_thresh + 0.20` before reopening. Even in normal mode a 2-frame (20 ms) debounce is applied. Do NOT collapse this back to single-frame threshold checks.
- **Mic echoing after silence, round 2** (amplitude-relative floor): the debounce above wasn't fully sufficient because `speech_prob` is a spectral-shape classifier with no notion of loudness — quiet headphone bleed that resembles speech spectrally still scored high confidence. Added a second, independent gate signal: in strict mode, also require the raw frame's RMS to exceed a learned ambient/bleed noise floor (`_noise_floor_rms`, slow EMA over confirmed non-speech frames) by `_RMS_MARGIN` (3x, ~+9.5 dB). Root-caused on the user's machine to an **open-back headset (Drop+Sennheiser PC38X) leaking audio acoustically by design, stacked with a +20 dB Windows mic Boost** amplifying that leak to speech-like loudness — see test plan below. This headset has no companion app/onboard DSP; all gain knobs are Windows/Realtek driver settings, not firmware.

## Open Investigation — Mic Echo After Silence (2026-06-25)

User reports mic picks up and echoes friends' voices specifically after a period of not speaking. Two software-side VAD fixes have shipped (see Bugs Fixed above) but the user had not yet confirmed they fully resolve it at time of writing. Root-cause analysis points to a **hardware/Windows-config contributing factor that software cannot fully override**: the headset (Drop+Sennheiser PC38X) is open-back (leaks audio by design) and the user's Windows mic had **Boost set to +20.0 dB** on top of Microphone level 74/100 — likely amplifying the acoustic leak past the point any VAD can distinguish it from real speech.

**Diagnostic test plan (run in this order, retest with friends after each):**
1. Lower Windows mic Boost from +20.0 dB to 0 dB (Sound Control Panel → Recording → mic → Properties → Levels). Single highest-leverage, free test.
2. Uncheck "Enable audio enhancements" (Properties → Advanced tab) — undocumented DSP bundle that may double-process alongside RNNoise.
3. Fully quit (tray → Quit) and relaunch VocalClear so both VAD fixes above are actually active in the running process (edits to `noise_filter.py` only take effect on next process start).
4. If `PaErrorCode -9984` ("Incompatible host API specific stream info") recurs and blocks testing: this is a **pre-existing, unrelated** flaky WASAPI-exclusive negotiation issue (seen in the log on 2026-06-05, 2026-06-20, and 2026-06-25, long before these fixes) — toggle WASAPI Exclusive Mode off in Settings as a workaround, trading ~3 ms latency for reliability.

**Ruled out during investigation:** stale PyInstaller exe (both desktop shortcut and Windows startup registry launch `pythonw.exe main.py` from source, confirmed via `reg query`/shortcut inspection — never the exe), WASAPI sample-rate mismatch silently corrupting audio (PortAudio/WASAPI fails loudly on real mismatches, never silently resamples in exclusive mode), DeepFilterNet being the active backend (not installed; `rnnoise` confirmed active via log).

**Still open / not yet verified:** whether lowering Boost and/or relaunching actually fixes the symptom for the user. If the symptom *persists* even after Boost is at 0 dB, audio enhancements are off, and the app has been relaunched with current source, the next investigative step is **true acoustic echo cancellation (AEC) with a reference signal** — capturing what's being sent to CABLE Input as a reference and subtracting correlated content from the mic signal — since a VAD-only approach has a structural ceiling it cannot exceed (confirmed via research: this is exactly how Discord/Krisp and NVIDIA Broadcast solve this, and a VAD classifier alone cannot replicate it because it never sees the playback signal). This would be a substantial architecture addition, not a tuning change — do not attempt without confirming the cheaper fixes above were exhausted first.

**Also flagged but not yet re-verified:** `config.json`'s `input_device` index can drift after reboots/driver updates because PortAudio device indices are not stable identifiers — when checked on 2026-06-25, index 33 momentarily enumerated as an output-only device ("Headphones (Realtek HD Audio 2nd output)", 0 input channels) on this machine, though the user confirmed via Settings UI that the actual selected device shown was correct ("System default", matching their headset). Low-confidence finding, included for completeness — not the active root cause, but a latent fragility worth remembering if input device selection ever silently breaks after a Windows update.
