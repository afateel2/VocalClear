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
tray_app.py          pystray tray icon, menu, toggle, settings/soundboard launchers
audio_engine.py      sounddevice duplex stream (48kHz, block=4096), calibration watchdog
noise_filter.py      3-tier noise engine (see below)
settings_window.py   tkinter dark-theme settings GUI (runs in daemon thread)
soundboard.py        Sound dataclass, file watcher, playback via sounddevice
soundboard_window.py tkinter soundboard UI (hotkey feature was removed — caused AppHangB1)
main_window.py       Main app window (status, toggle button, quick controls)
config.py            JSON config at %USERPROFILE%\.vocalclear\config.json
icon.py              PIL-based tray/window icon generator (RGBA, equalizer bars)
VocalClear.spec      PyInstaller build spec (see PyInstaller Notes below)
```

## Audio Pipeline

```
Real mic (WASAPI, 48 kHz) → sounddevice duplex Stream (block_size=4096)
  → input queue → processing thread → NoiseFilter.process()
  → output queue → CABLE Input (VB-Audio Virtual Cable)
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

Stored at `%USERPROFILE%\.vocalclear\config.json`:
```json
{
  "input_device": null,
  "output_device": null,
  "strength": 0.50,
  "enabled": true,
  "start_with_windows": false,
  "sample_rate": 16000,
  "block_size": 4096
}
```
`block_size` migration: load() auto-upgrades 1024 → 4096. `sample_rate` in config is ignored at runtime — actual rate is auto-detected from the output device.

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
