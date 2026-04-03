"""
Noise suppression engine — three-tier cascade:

  Tier 1  DeepFilterNet3   Deep learning, Krisp-class quality.
                           Requires: pip install deepfilternet
                           (needs Rust + MSVC to build from source)

  Tier 2  RNNoise          Mozilla's RNN noise suppressor.
                           Pre-built wheels — no compilation needed.
                           Requires: pip install pyrnnoise
                           Includes built-in VAD for breath/plosive gating.

  Tier 3  Wiener filter    Pure-Python spectral subtraction.
                           Always available, no extra install.

The active tier is chosen automatically at startup and exposed via
`self.backend` ("deepfilter" | "rnnoise" | "wiener").
"""

from __future__ import annotations
from typing import Optional

import numpy as np
from scipy import signal as scipy_signal


# RNNoise processes exactly 480 samples per frame at 48 kHz (10 ms)
_RNNOISE_FRAME = 480
# Hold time after speech ends before gating kicks in (in frames)
_HOLD_FRAMES   = 20   # 20 × 10 ms = 200 ms


class NoiseFilter:
    # ------------------------------------------------------------------ #
    # Construction & backend selection                                     #
    # ------------------------------------------------------------------ #

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate: int   = 48000   # all backends target 48 kHz
        self.enabled:     bool  = True
        self.strength:    float = 0.50    # 0.0 – 1.0
        self.backend:     str   = "none"
        self.is_calibrated: bool = False

        # Backend-specific state (set inside _init_* methods)
        self._model     = None    # DeepFilterNet
        self._df_state  = None
        self._enhance   = None
        self._rn_state  = None    # RNNoise ctypes state pointer
        self._rn_proc   = None    # process_mono_frame callable
        self._rn_carry: np.ndarray = np.array([], dtype=np.float32)
        self._hold_ctr: int = 0

        self._init_backend()

    def _init_backend(self) -> None:
        # ── Tier 1: DeepFilterNet ─────────────────────────────────────
        try:
            from df.enhance import enhance, init_df          # type: ignore
            self._model, self._df_state, _ = init_df()
            self._enhance      = enhance
            self.backend       = "deepfilter"
            self.is_calibrated = True
            print("[NoiseFilter] DeepFilterNet3 — AI noise suppression active")
            return
        except Exception as exc:
            print(f"[NoiseFilter] DeepFilterNet unavailable ({exc})")

        # ── Tier 2: RNNoise ───────────────────────────────────────────
        # pyrnnoise/__init__.py imports audiolab (not bundled in the exe).
        # We inject a fake parent package into sys.modules so Python skips
        # __init__.py and goes straight to pyrnnoise.rnnoise (ctypes + DLL only).
        try:
            import sys as _sys, types as _types, os as _os
            if 'pyrnnoise' not in _sys.modules:
                _fake = _types.ModuleType('pyrnnoise')
                _fake.__package__ = 'pyrnnoise'
                # Resolve real package path for non-frozen (source) usage
                _path: list = []
                try:
                    import importlib.util as _ilu
                    _spec = _ilu.find_spec('pyrnnoise')
                    if _spec and _spec.submodule_search_locations:
                        _path = list(_spec.submodule_search_locations)
                except Exception:
                    pass
                # Frozen (PyInstaller) fallback
                if not _path and getattr(_sys, 'frozen', False):
                    _path = [_os.path.join(_sys._MEIPASS, 'pyrnnoise')]
                _fake.__path__ = _path
                _sys.modules['pyrnnoise'] = _fake
            from pyrnnoise.rnnoise import (                  # type: ignore
                create as _rn_create, process_mono_frame,
            )
            self._rn_state     = _rn_create()
            self._rn_proc      = process_mono_frame
            self._rn_carry     = np.array([], dtype=np.float32)
            self._hold_ctr     = 0
            self.backend       = "rnnoise"
            self.is_calibrated = True
            print("[NoiseFilter] RNNoise — AI noise suppression active")
            return
        except Exception as exc:
            print(f"[NoiseFilter] RNNoise unavailable ({exc})")

        # ── Tier 3: Wiener filter ─────────────────────────────────────
        print("[NoiseFilter] Using Wiener filter fallback")
        self._init_wiener()
        self.backend = "wiener"

    # ------------------------------------------------------------------ #
    # Wiener-filter state                                                 #
    # ------------------------------------------------------------------ #

    def _init_wiener(self) -> None:
        self.n_fft     = 512    # 10.7 ms at 48 kHz — was 2048 (42 ms), huge latency cut
        self.hop       = self.n_fft // 2
        self.window    = np.hanning(self.n_fft).astype(np.float32)
        self._ola_norm = self.window ** 2

        nyq  = self.sample_rate / 2.0
        low  = 80.0 / nyq
        high = min(8000.0 / nyq, 0.98)
        self._bp_sos = scipy_signal.butter(
            4, [low, high], btype="band", output="sos")
        self._bp_zi  = scipy_signal.sosfilt_zi(
            self._bp_sos).astype(np.float32)

        self.noise_psd: Optional[np.ndarray] = None
        self._calibration_frames: list       = []
        self._prev_input = np.zeros(self.hop, dtype=np.float32)
        self._alpha_noise = 0.05
        self.is_calibrated = False

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Process one block of mono float32 audio. Same length returned."""
        audio = audio.astype(np.float32)
        if not self.enabled:
            return audio.copy()
        if self.backend == "deepfilter":
            return self._process_deepfilter(audio)
        if self.backend == "rnnoise":
            return self._process_rnnoise(audio)
        return self._process_wiener(audio)

    def reset_sample_rate(self, sample_rate: int) -> None:
        """Called by AudioEngine when the actual stream sample rate is known."""
        self.sample_rate = sample_rate
        if self.backend == "wiener":
            self._init_wiener()
        # DeepFilterNet and RNNoise are fixed at 48 kHz — nothing to rebuild.

    # ── calibration — only meaningful for Wiener fallback ─────────────

    def update_noise_profile(self, audio: np.ndarray) -> None:
        if self.backend == "wiener":
            self._wiener_update_noise(audio)

    def feed_calibration(self, audio: np.ndarray) -> bool:
        if self.backend != "wiener":
            return True
        for i in range(0, len(audio) - self.n_fft + 1, self.hop):
            frame = audio[i: i + self.n_fft].astype(np.float32) * self.window
            mag   = np.abs(np.fft.rfft(frame)).astype(np.float32)
            self._calibration_frames.append(mag ** 2)
        target = int(2.0 * self.sample_rate / self.hop)
        if len(self._calibration_frames) >= target:
            self.noise_psd = np.median(
                self._calibration_frames, axis=0).astype(np.float32)
            self._calibration_frames.clear()
            self.is_calibrated = True
            return True
        return False

    # ------------------------------------------------------------------ #
    # Tier 1 — DeepFilterNet                                              #
    # ------------------------------------------------------------------ #

    @property
    def _atten_lim_db(self) -> Optional[float]:
        if self.strength >= 0.99:
            return None
        return float(3 + self.strength * 97)

    def _process_deepfilter(self, audio: np.ndarray) -> np.ndarray:
        try:
            enhanced = self._enhance(
                self._model,
                self._df_state,
                audio[np.newaxis, :],          # (1, T)
                atten_lim_db=self._atten_lim_db,
            )
            return enhanced[0].astype(np.float32)
        except Exception as exc:
            print(f"[NoiseFilter] DeepFilterNet error: {exc}")
            return audio.copy()

    # ------------------------------------------------------------------ #
    # Tier 2 — RNNoise                                                    #
    # ------------------------------------------------------------------ #

    def _process_rnnoise(self, audio: np.ndarray) -> np.ndarray:
        # Carry is float32 in [-1, 1]; process_mono_frame handles int16 scaling.
        combined  = np.concatenate([self._rn_carry, audio.astype(np.float32)])
        n_full    = (len(combined) // _RNNOISE_FRAME) * _RNNOISE_FRAME
        self._rn_carry = combined[n_full:].copy()

        if n_full == 0:
            return audio.copy()

        frames = combined[:n_full].reshape(-1, _RNNOISE_FRAME)
        chunks: list[np.ndarray] = []

        # VAD gate threshold: strength 0 → 0.20, strength 1 → 0.70
        # Higher base prevents speaker bleed (friends' voices picked up by mic)
        # from opening the gate when the user is silent.
        vad_thresh = 0.20 + 0.50 * self.strength

        for frame in frames:
            # process_mono_frame: float32 [-1,1] in → (int16 denoised, speech_prob)
            denoised_i16, speech_prob = self._rn_proc(self._rn_state, frame)
            denoised_f = denoised_i16.astype(np.float32) / 32767.0

            # ── VAD gate with hold time ───────────────────────────
            if speech_prob >= vad_thresh:
                self._hold_ctr = _HOLD_FRAMES   # reset hold
                chunks.append(denoised_f)
            elif self._hold_ctr > 0:
                self._hold_ctr -= 1
                # Smooth fade-out over the hold window
                fade = self._hold_ctr / _HOLD_FRAMES
                chunks.append(denoised_f * fade)
            else:
                # Non-speech: complete silence. The 200 ms fade-out from the
                # hold counter already smooths the transition, so hard zero
                # here causes no clicks and prevents all speaker bleed-through.
                chunks.append(np.zeros_like(denoised_f))

        if not chunks:
            return audio.copy()

        output = np.concatenate(chunks).astype(np.float32)

        # Trim or pad to match the original block length
        if len(output) >= len(audio):
            return output[:len(audio)]
        return np.pad(output, (0, len(audio) - len(output)))

    # ------------------------------------------------------------------ #
    # Tier 3 — Wiener spectral subtraction                               #
    # ------------------------------------------------------------------ #

    def _process_wiener(self, audio: np.ndarray) -> np.ndarray:
        if self.noise_psd is None:
            self._prev_input = audio[-self.hop:].copy()
            return audio.copy()

        filtered, self._bp_zi = scipy_signal.sosfilt(
            self._bp_sos, audio, zi=self._bp_zi)

        extended = np.concatenate([
            self._prev_input,
            filtered,
            np.zeros(self.hop, dtype=np.float32),
        ])
        self._prev_input = filtered[-self.hop:].copy()

        n_ext   = len(extended)
        out_ext = np.zeros(n_ext, dtype=np.float32)
        nrm_ext = np.zeros(n_ext, dtype=np.float32)

        for start in range(0, n_ext - self.n_fft + 1, self.hop):
            frame = extended[start: start + self.n_fft] * self.window
            spec  = np.fft.rfft(frame)
            mag   = np.abs(spec).astype(np.float32)
            phase = np.angle(spec)

            noise_est = self.noise_psd * self.strength
            snr       = np.maximum(mag ** 2 / (noise_est + 1e-12) - 1.0, 0.0)
            gain      = snr / (snr + 1.0)
            gain      = np.maximum(gain, 0.05)

            clean_spec  = (mag * gain) * np.exp(1j * phase)
            clean_frame = np.fft.irfft(clean_spec).astype(np.float32)

            out_ext[start: start + self.n_fft] += clean_frame * self.window
            nrm_ext[start: start + self.n_fft] += self._ola_norm

        mask = nrm_ext > 1e-6
        out_ext[mask] /= nrm_ext[mask]
        return out_ext[self.hop: self.hop + len(audio)]

    def _wiener_update_noise(self, audio: np.ndarray) -> None:
        frames = []
        for i in range(0, len(audio) - self.n_fft + 1, self.hop):
            frame = audio[i: i + self.n_fft] * self.window
            mag   = np.abs(np.fft.rfft(frame)).astype(np.float32)
            frames.append(mag ** 2)
        if frames:
            new_psd = np.mean(frames, axis=0)
            self.noise_psd = (
                new_psd if self.noise_psd is None
                else self._alpha_noise * new_psd + (1 - self._alpha_noise) * self.noise_psd
            )
            self.is_calibrated = True
