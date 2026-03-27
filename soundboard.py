"""
SoundBoard — audio source that mixes into VocalClear's output stream.

Architecture:
  - Fixed home folder  ~/.vocalclear/sounds/  — all sounds live here permanently
  - Files added via "+ ADD SOUND" are copied there automatically
  - Hotkeys and per-sound volumes are persisted to sounds_config.json
  - Resamples everything to 48 kHz at load time — zero work at playback time
  - Provides get_mix_frame(n) for the VB-Cable stream (AudioEngine callback)
  - Optional local monitor: plays sounds through default speakers simultaneously

Supported formats (via soundfile): WAV, FLAC, OGG, AIFF
MP3: decoded via ffmpeg subprocess (imageio-ffmpeg)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable
from math import gcd

import ctypes
import ctypes.wintypes

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

# Log file for diagnostics (print() is swallowed by pythonw.exe)
_LOG_FILE = Path.home() / ".vocalclear" / "vocalclear.log"

def _log(msg: str) -> None:
    try:
        import datetime
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


# ── waveOut structures for simultaneous monitor playback ─────────────────────
# Using waveOut directly (not winsound) so each sound opens its own device
# handle and multiple sounds can play at the same time without blocking.

class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag",      ctypes.c_uint16),
        ("nChannels",       ctypes.c_uint16),
        ("nSamplesPerSec",  ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign",     ctypes.c_uint16),
        ("wBitsPerSample",  ctypes.c_uint16),
        ("cbSize",          ctypes.c_uint16),
    ]

class _WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData",          ctypes.c_void_p),   # LPSTR
        ("dwBufferLength",  ctypes.c_uint32),
        ("dwBytesRecorded", ctypes.c_uint32),
        ("dwUser",          ctypes.c_size_t),   # DWORD_PTR
        ("dwFlags",         ctypes.c_uint32),
        ("dwLoops",         ctypes.c_uint32),
        ("lpNext",          ctypes.c_void_p),
        ("reserved",        ctypes.c_size_t),
    ]

_WHDR_DONE   = 0x00000001
_WAVE_MAPPER = ctypes.c_uint(-1)  # default output device
_winmm       = ctypes.windll.winmm

TARGET_SR   = 48_000
AUDIO_EXTS  = {".mp3", ".ogg", ".m4a", ".wav", ".flac"}
WATCH_POLL  = 2.0     # seconds between folder scans
_CFG_FILE   = "sounds_config.json"


# ── Per-sound data ──────────────────────────────────────────────────────────────

@dataclass
class Sound:
    name:   str           # display name (stem of filename)
    path:   str           # file path (always inside sounds_dir after copy)
    data:   np.ndarray    # float32 mono, 48 kHz
    volume: float = 1.0   # 0.0–1.0, per-sound


@dataclass
class _PlayingInstance:
    sound: Sound
    pos:   int = 0


# ── Local monitor mixer ─────────────────────────────────────────────────────────

class _MonitorMixer:
    """
    Plays soundboard audio through the Windows default output device
    using the waveOut API directly (WinMM).

    Each triggered sound opens its own waveOut device handle and streams
    PCM-16 data asynchronously in a daemon thread.  Because every sound has
    an independent handle the OS mixer combines them, so N sounds triggered
    in quick succession all play simultaneously without any one blocking
    another.  waveOutReset() is used for immediate mid-playback stop when
    the epoch changes.
    """

    def __init__(self, sample_rate: int = TARGET_SR):
        self._sr      = sample_rate
        self._enabled = False
        self._epoch   = 0   # incremented to prevent queued sounds from playing

    @property
    def active(self) -> bool:
        return self._enabled

    def start(self, device=None) -> None:
        self._enabled = True
        print("[Monitor] Local monitor enabled (WinMM)")

    def stop(self) -> None:
        self._enabled = False
        self._epoch  += 1
        print("[Monitor] Local monitor disabled")

    def add(self, data: np.ndarray, name: str = "") -> None:
        epoch = self._epoch
        threading.Thread(
            target=self._play_thread,
            args=(data, epoch),
            daemon=True,
            name=f"Monitor-{name}",
        ).start()

    def clear(self, name: Optional[str] = None) -> None:
        """Prevent any queued sounds from starting (in-progress sound plays out)."""
        self._epoch += 1

    def _play_thread(self, data: np.ndarray, epoch: int) -> None:
        if self._epoch != epoch:
            return   # stop/clear was issued before we even started

        try:
            # Convert float32 → int16 PCM
            pcm = (data * 32767.0).clip(-32768, 32767).astype(np.int16)
            pcm_bytes = pcm.tobytes()

            # Fill WAVEFORMATEX
            wfx = _WAVEFORMATEX(
                wFormatTag      = 1,   # WAVE_FORMAT_PCM
                nChannels       = 1,
                nSamplesPerSec  = self._sr,
                wBitsPerSample  = 16,
                nBlockAlign     = 2,
                nAvgBytesPerSec = self._sr * 2,
                cbSize          = 0,
            )

            # Open a new waveOut handle — each call gets its own handle so
            # simultaneous sounds play in parallel without blocking each other.
            hwo = ctypes.c_void_p()
            rc = _winmm.waveOutOpen(
                ctypes.byref(hwo),
                _WAVE_MAPPER,
                ctypes.byref(wfx),
                ctypes.c_void_p(0),
                ctypes.c_void_p(0),
                0,  # CALLBACK_NULL
            )
            if rc != 0:
                _log(f"[Monitor] waveOutOpen failed: {rc}")
                return

            # Keep the PCM buffer alive in a ctypes array for the duration
            buf = (ctypes.c_char * len(pcm_bytes)).from_buffer_copy(pcm_bytes)

            hdr = _WAVEHDR()
            hdr.lpData         = ctypes.addressof(buf)
            hdr.dwBufferLength = len(pcm_bytes)

            _winmm.waveOutPrepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
            _winmm.waveOutWrite(hwo, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))

            # Poll until done or stopped
            while not (hdr.dwFlags & _WHDR_DONE):
                if self._epoch != epoch:
                    _winmm.waveOutReset(hwo)   # immediate stop
                    break
                time.sleep(0.01)

            _winmm.waveOutUnprepareHeader(hwo, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
            _winmm.waveOutClose(hwo)

        except Exception as exc:
            _log(f"[Monitor] ERROR in _play_thread: {exc}")
            print(f"[Monitor] playback error: {exc}")


# ── Main class ──────────────────────────────────────────────────────────────────

class SoundBoard:
    def __init__(self, sounds_dir: Optional[Path] = None):
        self.sounds_dir: Optional[Path] = sounds_dir
        self.master_volume: float       = 0.80
        self.overlap:       bool        = True
        self.monitor_volume: float      = 0.80   # local speaker level

        # Loaded sounds keyed by name
        self._sounds:      dict[str, Sound]       = {}
        self._sounds_lock = threading.Lock()

        # Currently playing instances (VB-Cable stream)
        self._playing:    list[_PlayingInstance]  = []
        self._play_lock   = threading.Lock()

        # Local monitor mixer
        self._monitor        = _MonitorMixer()
        self._monitor_enabled = True
        self._monitor.start()   # sync active flag with initial enabled state

        # Change callbacks for UI
        self._on_sounds_changed: Optional[Callable] = None
        self._on_play_changed:   Optional[Callable] = None

        # Folder watcher
        self._watch_thread:  Optional[threading.Thread] = None
        self._watch_running  = False
        self._watched_paths: set[str] = set()
        self._watch_lock     = threading.Lock()   # guards _watched_paths mutations

    # ──────────────────────────────────────────────────────────────────────────
    # Monitor property
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def monitor_enabled(self) -> bool:
        return self._monitor_enabled

    @monitor_enabled.setter
    def monitor_enabled(self, value: bool) -> None:
        self._monitor_enabled = value
        if value:
            self._monitor.start()
        else:
            self._monitor.stop()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API — called from UI thread
    # ──────────────────────────────────────────────────────────────────────────

    def set_sounds_dir(self, path: Path) -> None:
        """Point to a folder and reload all sounds found there."""
        self.sounds_dir = path
        self._start_watcher()
        self._scan_folder()

    def load_file(self, path: Path) -> Optional[str]:
        """
        Load a single audio file, resample to 48 kHz, store it.

        If the file is not inside sounds_dir it is copied there first so that
        it survives restarts.  Returns the sound name on success, None on error.
        """
        # Copy to sounds_dir so the file persists across restarts
        if self.sounds_dir is not None:
            try:
                dest = self.sounds_dir / path.name
                if path.resolve() != dest.resolve() and not dest.exists():
                    shutil.copy2(str(path), str(dest))
                    print(f"[SoundBoard] Copied '{path.name}' → sounds folder")
                path = dest
            except Exception as exc:
                print(f"[SoundBoard] Warning: could not copy to sounds folder: {exc}")

        name = path.stem
        try:
            data = _decode(path)
        except Exception as exc:
            print(f"[SoundBoard] Failed to load {path.name}: {exc}")
            return None

        # Restore saved settings (hotkey, volume) from the config file
        saved = self._load_sounds_config().get(name, {})

        with self._sounds_lock:
            existing = self._sounds.get(name)
            snd = Sound(
                name   = name,
                path   = str(path),
                data   = data,
                volume = existing.volume if existing else saved.get("volume", 1.0),
            )
            self._sounds[name] = snd

        print(f"[SoundBoard] Loaded '{name}'  ({len(data)/TARGET_SR:.1f} s)")
        self._fire_sounds_changed()
        return name

    def remove_sound(self, name: str) -> None:
        """
        Remove a sound from the board and delete its copy from the sounds folder.
        The user's original source file is never touched.
        """
        with self._sounds_lock:
            snd = self._sounds.pop(name, None)
        with self._play_lock:
            self._playing = [p for p in self._playing if p.sound.name != name]
        if self._monitor_enabled:
            self._monitor.clear(name)

        # Delete the sounds_dir copy so the watcher doesn't re-add it
        if snd and self.sounds_dir:
            path = Path(snd.path)
            try:
                self._watched_paths.discard(str(path))
                if path.parent.resolve() == self.sounds_dir.resolve():
                    path.unlink(missing_ok=True)
            except Exception as exc:
                print(f"[SoundBoard] Warning: could not delete '{path.name}': {exc}")

        self._save_sounds_config()
        self._fire_sounds_changed()

    def play(self, name: str) -> None:
        """Trigger a sound. Respects overlap setting. Fires monitor if enabled."""
        with self._sounds_lock:
            snd = self._sounds.get(name)
        if snd is None:
            return

        with self._play_lock:
            if not self.overlap:
                self._playing.clear()
            self._playing.append(_PlayingInstance(sound=snd, pos=0))

        # Local monitor: play through speakers so the operator can hear it
        if self._monitor_enabled and self._monitor.active:
            if not self.overlap:
                self._monitor.clear()
            scaled = (snd.data * snd.volume * self.monitor_volume).astype(np.float32)
            self._monitor.add(scaled, name=name)

        self._fire_play_changed()

    def stop(self, name: Optional[str] = None) -> None:
        """Stop a specific sound or all sounds if name is None."""
        with self._play_lock:
            if name is None:
                self._playing.clear()
            else:
                self._playing = [p for p in self._playing if p.sound.name != name]
        if self._monitor_enabled:
            self._monitor.clear(name)
        self._fire_play_changed()

    def set_volume(self, name: str, volume: float) -> None:
        with self._sounds_lock:
            if name in self._sounds:
                self._sounds[name].volume = max(0.0, min(1.0, volume))
        self._save_sounds_config()

    @property
    def sounds(self) -> dict[str, Sound]:
        with self._sounds_lock:
            return dict(self._sounds)

    @property
    def playing_names(self) -> set[str]:
        with self._play_lock:
            return {p.sound.name for p in self._playing}

    def stop_watcher(self) -> None:
        self._watch_running = False
        self._monitor.stop()

    # ──────────────────────────────────────────────────────────────────────────
    # Audio mixing — called from AudioEngine callback (high-priority thread)
    # ──────────────────────────────────────────────────────────────────────────

    def get_mix_frame(self, n_frames: int) -> np.ndarray:
        """
        Return n_frames samples of mixed soundboard audio (float32, mono).
        Called every audio callback — must be fast and non-blocking.
        """
        out  = np.zeros(n_frames, dtype=np.float32)
        done = []

        with self._play_lock:
            for inst in self._playing:
                data  = inst.sound.data
                vol   = inst.sound.volume * self.master_volume
                start = inst.pos
                end   = start + n_frames
                chunk = data[start:end]

                if len(chunk) < n_frames:
                    out[:len(chunk)] += chunk * vol
                    done.append(inst)
                else:
                    out += chunk * vol
                    inst.pos = end

            for inst in done:
                self._playing.remove(inst)

        if done:
            self._fire_play_changed()

        np.tanh(out, out=out)
        return out

    # ──────────────────────────────────────────────────────────────────────────
    # Config persistence (hotkeys + volumes)
    # ──────────────────────────────────────────────────────────────────────────

    def _save_sounds_config(self) -> None:
        if not self.sounds_dir:
            return
        per_sound: dict[str, dict] = {}
        with self._sounds_lock:
            for name, snd in self._sounds.items():
                per_sound[name] = {"volume": snd.volume}
        cfg = {
            "_global": {
                "master_volume":  self.master_volume,
                "monitor_volume": self.monitor_volume,
                "overlap":        self.overlap,
            },
            **per_sound,
        }
        try:
            cfg_path = self.sounds_dir / _CFG_FILE
            tmp_path = cfg_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            import os as _os
            _os.replace(tmp_path, cfg_path)
        except Exception as exc:
            print(f"[SoundBoard] Failed to save config: {exc}")

    def _load_sounds_config(self) -> dict:
        if not self.sounds_dir:
            return {}
        cfg_path = self.sounds_dir / _CFG_FILE
        if not cfg_path.exists():
            return {}
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            # Restore global soundboard settings if present
            g = raw.pop("_global", {})
            if "master_volume" in g:
                self.master_volume = float(g["master_volume"])
            if "monitor_volume" in g:
                self.monitor_volume = float(g["monitor_volume"])
            if "overlap" in g:
                self.overlap = bool(g["overlap"])
            return raw
        except Exception:
            return {}

    # ──────────────────────────────────────────────────────────────────────────
    # Rename / Export / Import
    # ──────────────────────────────────────────────────────────────────────────

    def rename_sound(self, old_name: str, new_name: str) -> bool:
        """
        Rename a sound in-memory and on disk.
        Returns True on success, False if old_name not found, new_name taken, or OS error.
        """
        new_name = new_name.strip()
        if not new_name:
            return False
        with self._sounds_lock:
            if old_name not in self._sounds or new_name in self._sounds:
                return False
            snd = self._sounds[old_name]
            old_path = Path(snd.path)
            new_path = old_path.parent / (new_name + old_path.suffix)
            try:
                old_path.rename(new_path)
            except OSError:
                return False
            from dataclasses import replace as dc_replace
            new_snd = dc_replace(snd, name=new_name, path=str(new_path))
            del self._sounds[old_name]
            self._sounds[new_name] = new_snd

        # Stop any playing instance of this sound
        with self._play_lock:
            self._playing = [i for i in self._playing if i.sound.name != old_name]

        with self._watch_lock:
            self._watched_paths.discard(str(old_path))
            self._watched_paths.add(str(new_path))

        self._save_sounds_config()
        self._fire_sounds_changed()
        return True

    def export_profile(self, dest_path: Path) -> None:
        """
        Write a ZIP archive containing all sounds + sounds_config.json.
        Flushes config first so the ZIP reflects the latest state.
        """
        import zipfile
        self._save_sounds_config()
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if self.sounds_dir:
                cfg_path = self.sounds_dir / _CFG_FILE
                if cfg_path.exists():
                    zf.write(cfg_path, _CFG_FILE)
                for p in self.sounds_dir.iterdir():
                    if p.suffix.lower() in AUDIO_EXTS and p.is_file():
                        zf.write(p, p.name)

    def import_profile(self, zip_path: Path, merge: bool = False) -> None:
        """
        Load a ZIP profile exported by export_profile().
        If merge=False, existing sounds are replaced; if True, they are merged.
        Must be called from a non-audio thread.
        """
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if _CFG_FILE not in names:
                raise ValueError("ZIP does not contain sounds_config.json")

            if not merge:
                # Stop all playing sounds first
                with self._play_lock:
                    self._playing.clear()
                # Remove existing sound files from disk
                if self.sounds_dir:
                    for p in list(self.sounds_dir.iterdir()):
                        if p.suffix.lower() in AUDIO_EXTS and p.is_file():
                            try:
                                p.unlink()
                            except OSError:
                                pass
                with self._sounds_lock:
                    self._sounds.clear()
                with self._watch_lock:
                    self._watched_paths.clear()

            # Extract everything into sounds_dir
            if self.sounds_dir:
                zf.extractall(self.sounds_dir)

        # Reload from the freshly-extracted folder
        if self.sounds_dir:
            self._scan_folder()
        self._fire_sounds_changed()

    # ──────────────────────────────────────────────────────────────────────────
    # Folder watcher
    # ──────────────────────────────────────────────────────────────────────────

    def _start_watcher(self) -> None:
        if self._watch_running:
            return
        self._watch_running = True
        self._watch_thread  = threading.Thread(
            target=self._watch_loop, daemon=True, name="SoundBoard-watcher"
        )
        self._watch_thread.start()

    def _watch_loop(self) -> None:
        while self._watch_running:
            self._scan_folder()
            time.sleep(WATCH_POLL)

    def _scan_folder(self) -> None:
        if self.sounds_dir is None or not self.sounds_dir.exists():
            return
        found = {
            str(p) for p in self.sounds_dir.iterdir()
            if p.suffix.lower() in AUDIO_EXTS and p.is_file()
        }
        with self._watch_lock:
            new        = found - self._watched_paths
            gone_paths = self._watched_paths - found
            self._watched_paths = found

        for p in gone_paths:
            name = Path(p).stem
            # Only auto-remove if it's not in _sounds already removed by user
            with self._sounds_lock:
                if name in self._sounds:
                    self._sounds.pop(name)
            self._fire_sounds_changed()

        for p in new:
            self.load_file(Path(p))

    # ──────────────────────────────────────────────────────────────────────────
    # Callbacks
    # ──────────────────────────────────────────────────────────────────────────

    def _fire_sounds_changed(self) -> None:
        if self._on_sounds_changed:
            try:
                self._on_sounds_changed()
            except Exception:
                pass

    def _fire_play_changed(self) -> None:
        if self._on_play_changed:
            try:
                self._on_play_changed()
            except Exception:
                pass


# ── Audio decoding ──────────────────────────────────────────────────────────────

def _decode(path: Path) -> np.ndarray:
    """Decode any supported audio file to float32 mono at TARGET_SR."""
    ext = path.suffix.lower()

    if ext in (".mp3", ".m4a"):
        # soundfile cannot handle MP3/M4A — use ffmpeg
        data, sr = _decode_via_ffmpeg(path)
    else:
        # WAV / OGG / FLAC: try soundfile first, fall back to ffmpeg
        try:
            data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        except Exception:
            data, sr = _decode_via_ffmpeg(path)

    if data.ndim > 1:
        data = data.mean(axis=1)

    if sr != TARGET_SR:
        data = _resample(data, sr, TARGET_SR)

    return data.astype(np.float32)


def _resample(data: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    g = gcd(from_sr, to_sr)
    up, down = to_sr // g, from_sr // g
    return resample_poly(data, up, down).astype(np.float32)


def _decode_via_ffmpeg(path: Path) -> tuple[np.ndarray, int]:
    """Decode MP3 / M4A (and any ffmpeg-supported format) to raw PCM float32."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe  # type: ignore
        ffmpeg = get_ffmpeg_exe()
    except Exception:
        ffmpeg = "ffmpeg"

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(path),
             "-ac", "1", "-ar", str(TARGET_SR),
             "-f", "wav", tmp_path],
            capture_output=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        data, sr = sf.read(tmp_path, dtype="float32", always_2d=False)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return data, sr
