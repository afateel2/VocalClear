"""
Real-time audio engine.

Flow (lightweight backends — rnnoise / wiener):
  Real microphone  →  input callback  →  [process inline]  →  output callback
  Latency: ~1 block = 10 ms at 480 samples / 48 kHz

Flow (heavy backend — deepfilter):
  Real microphone  →  input callback  →  input_queue
  processing thread  →  output_queue  →  output callback
  Latency: ~2 blocks = 20 ms (processing needs a dedicated thread)

WASAPI exclusive mode (optional):
  Bypasses Windows audio mixer → reduces shared-mode overhead from ~20 ms to ~3 ms.
  Safe for VocalClear because exclusive mic access is the desired behaviour —
  all other apps read from VB-CABLE (the cleaned output), not directly from the mic.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional, Callable

import numpy as np
import sounddevice as sd

from noise_filter import NoiseFilter
from config import Config
from soundboard import SoundBoard


def find_vbcable_device() -> Optional[int]:
    """Return the device index of VB-CABLE Input (stereo output), or None."""
    devices = list(enumerate(sd.query_devices()))
    for i, d in devices:
        if (d["name"].lower().startswith("cable input")
                and "vb-audio" in d["name"].lower()
                and d["max_output_channels"] == 2
                and d["max_input_channels"] == 0):
            return i
    for i, d in devices:
        if "cable input" in d["name"].lower() and d["max_output_channels"] > 0:
            return i
    return None


def list_input_devices() -> list[dict]:
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def list_output_devices() -> list[dict]:
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_output_channels"] > 0
    ]


class AudioEngine:
    # Queue depth only used for DeepFilterNet (heavy processing thread)
    _QUEUE_MAXSIZE = 6

    def __init__(self, config: Config, noise_filter: NoiseFilter):
        self.config       = config
        self.noise_filter = noise_filter

        self._input_q:  queue.Queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._output_q: queue.Queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)

        self._stream:      Optional[sd.Stream]    = None
        self._proc_thread: Optional[threading.Thread] = None
        self._running = False

        # Calibration state
        self._calibrating    = False
        self._calib_buffer:  list[np.ndarray] = []
        self._calib_done_cb: Optional[Callable] = None
        self._calib_target:  int = 0

        # Status / monitoring
        self.last_error:         Optional[str] = None   # startup/device errors only
        self.xrun_count:         int           = 0      # runtime xruns (non-fatal)
        self.output_device_name: str           = ""
        self.active_input_device               = None
        self.input_rms:          float         = 0.0
        self.output_rms:         float         = 0.0

        # SoundBoard (optional — attach after construction)
        self._soundboard: Optional[SoundBoard] = None

        # Push-to-talk — polled every 10 ms by a background thread
        # True means "mic is open" (either PTT disabled, or key held down)
        self._ptt_active: bool = True
        self._ptt_thread: Optional[threading.Thread] = None

    def attach_soundboard(self, sb: Optional[SoundBoard]) -> None:
        """Attach or detach a SoundBoard. Thread-safe — swap is atomic."""
        self._soundboard = sb

    # ──────────────────────────────────────────────────────────────────────────
    # Start / Stop / Restart
    # ──────────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        bs         = self.config["block_size"]
        input_dev  = self.config["input_device"]
        self.active_input_device = input_dev

        output_dev = self.config["output_device"]
        if output_dev is None:
            vbc = find_vbcable_device()
            if vbc is not None:
                output_dev = vbc

        if output_dev is not None:
            try:
                self.output_device_name = sd.query_devices(output_dev)["name"]
            except Exception:
                self.output_device_name = str(output_dev)
        else:
            self.output_device_name = "System default"

        # Match sample rate to output device
        sr = 48000
        if output_dev is not None:
            try:
                sr = int(sd.query_devices(output_dev)["default_samplerate"])
            except Exception:
                pass

        if sr != self.noise_filter.sample_rate:
            self.noise_filter.reset_sample_rate(sr)

        # ── WASAPI exclusive mode (optional) ──────────────────────────────────
        # Apply exclusive mode ONLY to the input (microphone) device.
        # The output (VB-Cable Input) must stay in shared mode so that
        # VB-Cable's driver can route audio to VB-Cable Output for Discord.
        # Passing a single WasapiSettings would apply exclusive to BOTH
        # devices, which breaks the VB-Cable passthrough.
        extra_settings = None
        if self.config.get("wasapi_exclusive", False):
            try:
                extra_settings = (
                    sd.WasapiSettings(exclusive=True),    # input  → exclusive
                    sd.WasapiSettings(exclusive=False),   # output → shared
                )
            except AttributeError:
                pass   # older sounddevice build without WasapiSettings

        # ── Start processing thread only for DeepFilterNet ────────────────────
        # Lightweight backends (rnnoise / wiener) process inline in the callback
        # to eliminate the queue pipeline and cut ~10 ms of latency.
        self._running = True
        if self.noise_filter.backend == "deepfilter":
            self._proc_thread = threading.Thread(
                target=self._process_loop, daemon=True, name="VocalClear-proc"
            )
            self._proc_thread.start()

        self._ptt_thread = threading.Thread(
            target=self._ptt_loop, daemon=True, name="VocalClear-ptt"
        )
        self._ptt_thread.start()

        # Explicit numeric latency target (5 ms) — more aggressive than "low"
        self._stream = sd.Stream(
            samplerate   = sr,
            blocksize    = bs,
            dtype        = "float32",
            channels     = 1,
            device       = (input_dev, output_dev),
            callback     = self._stream_callback,
            latency      = 0.005,
            **({"extra_settings": extra_settings} if extra_settings else {}),
        )
        self._stream.start()

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._proc_thread is not None:
            self._proc_thread.join(timeout=2.0)
            self._proc_thread = None
        if self._ptt_thread is not None:
            self._ptt_thread.join(timeout=0.5)
            self._ptt_thread = None

    def restart(self) -> None:
        self.stop()
        for q in (self._input_q, self._output_q):
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break
        for attempt in range(3):
            time.sleep(1.0 + attempt)
            try:
                self.start()
                return
            except Exception as e:
                self.last_error = str(e)
        raise RuntimeError(self.last_error or "Stream failed to reopen")

    # ──────────────────────────────────────────────────────────────────────────
    # Calibration
    # ──────────────────────────────────────────────────────────────────────────

    def start_calibration(self, duration_s: float = 3.0,
                          done_cb: Optional[Callable] = None) -> None:
        self._calib_buffer.clear()
        self._calib_target  = int(duration_s * self.noise_filter.sample_rate)
        self._calib_done_cb = done_cb
        self._calibrating   = True

        def _watchdog():
            time.sleep(duration_s + 2.0)
            if self._calibrating:
                self._calibrating = False
                self._calib_buffer.clear()
                if self._calib_done_cb:
                    threading.Thread(target=self._calib_done_cb, daemon=True).start()

        threading.Thread(target=_watchdog, daemon=True,
                         name="VocalClear-calib-watchdog").start()

    # ──────────────────────────────────────────────────────────────────────────
    # Stream callback — runs on PortAudio's high-priority thread
    # ──────────────────────────────────────────────────────────────────────────

    def _stream_callback(
        self, indata: np.ndarray, outdata: np.ndarray,
        frames: int, time_info, status,
    ) -> None:
        if status:
            # xrun callbacks (input_overflow, output_underflow) are normal under
            # load — don't clobber last_error which is reserved for startup failures
            self.xrun_count = getattr(self, "xrun_count", 0) + 1

        mono = indata.flatten()
        self.input_rms = float(np.sqrt(np.mean(mono ** 2)))

        # ── Calibration ───────────────────────────────────────────────────────
        if self._calibrating:
            self._calib_buffer.append(mono.copy())
            accumulated = np.concatenate(self._calib_buffer)
            if len(accumulated) >= self._calib_target:
                self.noise_filter.update_noise_profile(accumulated)
                self._calibrating = False
                self._calib_buffer.clear()
                if self._calib_done_cb:
                    threading.Thread(
                        target=self._calib_done_cb, daemon=True).start()
            outdata.fill(0.0)
            return

        # ── DeepFilterNet: queue-based (too heavy for inline) ─────────────────
        if self.noise_filter.backend == "deepfilter":
            try:
                self._input_q.put_nowait(indata.copy())
            except queue.Full:
                pass
            try:
                processed = self._output_q.get_nowait()
                mic_frame = processed.flatten() if self._ptt_active else np.zeros(frames, np.float32)
                mixed = self._mix_soundboard(mic_frame, frames)
                mixed = self._apply_gain(mixed)
                outdata[:] = mixed.reshape(-1, 1)
                self.output_rms = float(np.sqrt(np.mean(mixed ** 2)))
            except queue.Empty:
                # No mic yet — still mix soundboard so SFX come through
                sb_only = self._mix_soundboard(np.zeros(frames, np.float32), frames)
                sb_only = self._apply_gain(sb_only)
                outdata[:] = sb_only.reshape(-1, 1)
                self.output_rms = float(np.sqrt(np.mean(sb_only ** 2)))
            return

        # ── RNNoise / Wiener: inline processing (0.5 ms, safe in callback) ───
        try:
            processed = self.noise_filter.process(mono)
            if not self._ptt_active:
                processed = np.zeros_like(processed)
            mixed     = self._mix_soundboard(processed, frames)
            mixed     = self._apply_gain(mixed)
            outdata[:] = mixed.reshape(-1, 1).astype(np.float32)
            self.output_rms = float(np.sqrt(np.mean(mixed ** 2)))
        except Exception as exc:
            self.last_error = str(exc)
            outdata[:] = indata
            self.output_rms = self.input_rms

    # ──────────────────────────────────────────────────────────────────────────
    # Push-to-talk polling thread
    # ──────────────────────────────────────────────────────────────────────────

    def _ptt_loop(self) -> None:
        import ctypes as _ct
        while self._running:
            enabled = self.config.get("ptt_enabled", False)
            vk      = self.config.get("ptt_vk", 0)
            if enabled and vk:
                state = _ct.windll.user32.GetAsyncKeyState(int(vk))
                self._ptt_active = bool(state & 0x8000)
            else:
                self._ptt_active = True   # PTT off → always open
            time.sleep(0.010)

    # ──────────────────────────────────────────────────────────────────────────
    # Soundboard mixing helper
    # ──────────────────────────────────────────────────────────────────────────

    def _mix_soundboard(self, mic: np.ndarray, n: int) -> np.ndarray:
        """Add soundboard frame to mic audio. Returns float32 mono array."""
        sb = self._soundboard
        if sb is None:
            return mic.astype(np.float32)
        sfx = sb.get_mix_frame(n)
        mixed = mic.astype(np.float32) + sfx
        # Soft clip to prevent clipping when SFX is loud
        np.tanh(mixed, out=mixed)
        return mixed

    def _apply_gain(self, audio: np.ndarray) -> np.ndarray:
        """Apply output_gain from config. No-op if gain == 1.0."""
        gain = self.config.get("output_gain", 1.0)
        if gain == 1.0:
            return audio
        return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)

    # ──────────────────────────────────────────────────────────────────────────
    # Processing thread — only used for DeepFilterNet
    # ──────────────────────────────────────────────────────────────────────────

    def _process_loop(self) -> None:
        while self._running:
            try:
                raw: np.ndarray = self._input_q.get(timeout=0.1)
            except queue.Empty:
                continue

            mono = raw.flatten()

            try:
                processed = self.noise_filter.process(mono)
            except Exception as exc:
                self.last_error = str(exc)
                processed = mono

            out = processed.reshape(-1, 1).astype(np.float32)
            try:
                self._output_q.put_nowait(out)
            except queue.Full:
                pass
