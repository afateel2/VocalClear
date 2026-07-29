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


# RNNoise processes exactly 480 samples per frame at 48 kHz (10 ms)
_RNNOISE_FRAME = 480
# Hold time after speech ends before gating kicks in (in frames)
_HOLD_FRAMES   = 20   # 20 × 10 ms = 200 ms

# Gate re-open debounce — prevents headphone bleed from reopening the gate
# after a period of silence.  Problem: RNNoise correctly classifies leaked
# speaker audio as speech (high speech_prob), so a single 10 ms frame above
# vad_thresh immediately re-opens the gate and echoes friends' voices back.
# Solution: after _LONG_SILENCE_FRAMES of gate-closed silence, require
# _OPEN_FRAMES_STRICT consecutive frames above a stricter threshold before
# the gate reopens.  Even in normal mode a 2-frame (20 ms) debounce is applied
# to reject transient noise spikes.
_LONG_SILENCE_FRAMES = 150  # 1.5 s of gate-closed silence → strict mode
_OPEN_FRAMES         = 2    # 20 ms min sustained speech to reopen (normal)
_OPEN_FRAMES_STRICT  = 5    # 50 ms min sustained speech after long silence
_THRESH_BUMP_STRICT  = 0.20 # additional speech-prob margin in strict mode

# speech_prob alone is a spectral-shape classifier — it has no notion of
# loudness, so quiet, clean headphone bleed can still score high confidence
# "speech" if its spectral shape resembles a voice.  As a second, independent
# signal, track the ambient noise floor (RMS of confirmed non-speech frames)
# and in strict mode also require the candidate frame to be louder than that
# floor by _RMS_MARGIN.  Direct mic speech is normally tens of dB above the
# room/bleed floor, so this rejects bleed even when RNNoise is fooled.
#
# The floor is a MINIMUM statistic, not an average.  Breaths and unvoiced
# fricatives (s/f/sh) are loud but score low speech_prob — averaging them in
# inflated the floor toward speech level exactly while the user was talking
# into a closed gate, locking their own voice out until a few seconds of
# silence decayed the floor again ("mic dead until I wait and retry" bug,
# fixed 2026-07-11).  Rules: adapt down fast, drift up slowly, and NEVER
# learn from a frame louder than _RMS_FLOOR_LEARN_CAP × the current floor.
_RMS_FLOOR_ALPHA_DOWN = 0.05  # fast decay toward quieter ambient
_RMS_FLOOR_ALPHA_UP   = 0.02  # slow rise for genuine ambient drift
_RMS_FLOOR_LEARN_CAP  = 2.0   # louder-than-2×floor frames are transients — skip
_RMS_MARGIN           = 3.0   # candidate must exceed floor by this (~+9.5 dB)


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
        self._rn_out:   np.ndarray = np.array([], dtype=np.float32)
        self._hold_ctr:    int   = 0
        self._speech_run:  int   = 0   # consecutive frames above threshold (gate closed)
        self._silence_run: int   = 0   # consecutive frames of gate-fully-closed silence
        self._noise_floor_rms: float = 0.0   # learned ambient/bleed RMS floor

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
            self._rn_out       = np.array([], dtype=np.float32)
            self._hold_ctr     = 0
            self._speech_run   = 0
            self._silence_run  = 0
            self._noise_floor_rms = 0.0
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
        from scipy import signal as scipy_signal  # lazy: only loaded if Wiener is needed

        self.n_fft     = 512    # 10.7 ms at 48 kHz — was 2048 (42 ms), huge latency cut
        self.hop       = self.n_fft // 2
        self.window    = np.hanning(self.n_fft).astype(np.float32)
        self._ola_norm = self.window ** 2

        nyq  = self.sample_rate / 2.0
        low  = 80.0 / nyq
        high = min(8000.0 / nyq, 0.98)
        self._bp_sos    = scipy_signal.butter(4, [low, high], btype="band", output="sos")
        self._bp_zi     = scipy_signal.sosfilt_zi(self._bp_sos).astype(np.float32)
        self._sosfilt   = scipy_signal.sosfilt  # store to avoid per-call module lookup

        self.noise_psd: Optional[np.ndarray] = None
        self._prev_input = np.zeros(self.hop, dtype=np.float32)
        self._alpha_noise = 0.05
        self.is_calibrated = False

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Process one block of mono float32 audio. Same length returned."""
        # np.asarray only copies if audio isn't already float32 — the audio
        # engine's hot path always passes float32, so this is normally free.
        audio = np.asarray(audio, dtype=np.float32)
        # Defensive clip: a hot mic (e.g. high Windows "Boost" gain) can
        # occasionally produce samples just past +/-1.0.  pyrnnoise's int16
        # conversion silently skips out-of-range frames and then asserts,
        # which the audio callback catches — but that sets last_error, which
        # the watchdog sees as a stream fault and restarts the whole engine
        # (audible glitch) over what should be a harmless clipped sample.
        if audio.size and (audio.min() < -1.0 or audio.max() > 1.0):
            audio = np.clip(audio, -1.0, 1.0)
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
        # audio is already float32 here — process() casts it before dispatch.
        # Input carry accumulates until a full 480-sample RNNoise frame exists;
        # processed audio is queued in an output FIFO (_rn_out) and emitted in
        # exactly len(audio)-sized blocks.  Never emit raw input — with
        # block_size < 480 that would leak unfiltered mic audio past the VAD
        # gate — and never drop processed samples.  At the default block size
        # of 480 the FIFO is pass-through (in 480 → out 480 every call).
        combined  = np.concatenate([self._rn_carry, audio])
        n_full    = (len(combined) // _RNNOISE_FRAME) * _RNNOISE_FRAME
        self._rn_carry = combined[n_full:].copy()

        # VAD gate threshold: strength 0 → 0.20, strength 1 → 0.70
        # Higher base prevents speaker bleed (friends' voices picked up by mic)
        # from opening the gate when the user is silent.
        vad_thresh = 0.20 + 0.50 * self.strength

        chunks: list[np.ndarray] = []
        frames = (combined[:n_full].reshape(-1, _RNNOISE_FRAME)
                  if n_full else ())

        for frame in frames:
            # process_mono_frame: float32 [-1,1] in → (int16 denoised, speech_prob)
            denoised_i16, speech_prob = self._rn_proc(self._rn_state, frame)
            denoised_f = denoised_i16.astype(np.float32)
            denoised_f /= 32767.0

            # ── VAD gate with hold time and re-open debounce ──────
            if self._hold_ctr > 0:
                # Gate is open (fade-out countdown running).
                self._silence_run = 0
                if speech_prob >= vad_thresh:
                    self._hold_ctr   = _HOLD_FRAMES   # speech detected — reset hold
                    self._speech_run = 0
                    chunks.append(denoised_f)
                else:
                    self._hold_ctr  -= 1
                    self._speech_run = 0
                    # Smooth fade-out so the gate close is click-free.
                    denoised_f *= self._hold_ctr / _HOLD_FRAMES
                    chunks.append(denoised_f)
            else:
                # Gate is fully closed.  After prolonged silence, raise the
                # threshold and require several consecutive speech frames
                # before reopening — headphone bleed is usually brief and
                # lower-confidence than real user speech.
                self._silence_run += 1
                strict = self._silence_run >= _LONG_SILENCE_FRAMES
                if strict:
                    eff_thresh    = min(0.95, vad_thresh + _THRESH_BUMP_STRICT)
                    frames_needed = _OPEN_FRAMES_STRICT
                else:
                    eff_thresh    = vad_thresh
                    frames_needed = _OPEN_FRAMES

                passes_prob = speech_prob >= eff_thresh

                # Second, independent signal: speech_prob is a spectral-shape
                # classifier with no notion of loudness, so quiet headphone
                # bleed can still score high confidence.  In strict mode also
                # require the raw frame to be louder than the learned ambient
                # floor — direct mic speech is normally far above bleed level.
                # dot-product RMS avoids the frame**2 temp array (hot path)
                frame_rms = float(np.sqrt(np.dot(frame, frame) / frame.size))
                if strict and self._noise_floor_rms > 1e-6:
                    passes_rms = frame_rms >= self._noise_floor_rms * _RMS_MARGIN
                else:
                    passes_rms = True

                if passes_prob and passes_rms:
                    self._speech_run += 1
                    if self._speech_run >= frames_needed:
                        # Enough sustained speech — open the gate.
                        self._hold_ctr    = _HOLD_FRAMES
                        self._silence_run = 0
                        self._speech_run  = 0
                        chunks.append(denoised_f)
                    else:
                        # Still accumulating — remain silent.
                        chunks.append(np.zeros_like(denoised_f))
                else:
                    # Decay instead of hard reset: real speech onsets contain
                    # single 10 ms low-confidence frames (stop closures), and
                    # one dip must not erase the whole accumulated run.
                    # Sustained non-speech still drains the run to zero.
                    self._speech_run = max(0, self._speech_run - 2)
                    # Learn the ambient/bleed floor from confirmed non-speech
                    # frames — as a minimum statistic (see constants above):
                    # loud transients (breaths, fricatives, keyboard) must not
                    # inflate it, or the user's own speech gets locked out.
                    if speech_prob < vad_thresh:
                        floor = self._noise_floor_rms
                        if floor <= 1e-6:
                            self._noise_floor_rms = frame_rms
                        elif frame_rms < floor:
                            self._noise_floor_rms = floor + \
                                _RMS_FLOOR_ALPHA_DOWN * (frame_rms - floor)
                        elif frame_rms < floor * _RMS_FLOOR_LEARN_CAP:
                            self._noise_floor_rms = floor + \
                                _RMS_FLOOR_ALPHA_UP * (frame_rms - floor)
                    # Non-speech: hard zero prevents all speaker bleed-through.
                    chunks.append(np.zeros_like(denoised_f))

        if chunks:
            if self._rn_out.size:
                self._rn_out = np.concatenate([self._rn_out] + chunks)
            elif len(chunks) == 1:
                self._rn_out = chunks[0]
            else:
                self._rn_out = np.concatenate(chunks)

        n = len(audio)
        if self._rn_out.size >= n:
            out = self._rn_out[:n]
            self._rn_out = self._rn_out[n:]
            return out
        # FIFO still priming (only possible while block_size < 480):
        # emit silence and keep queued samples so ordering is preserved.
        return np.zeros(n, dtype=np.float32)

    # ------------------------------------------------------------------ #
    # Tier 3 — Wiener spectral subtraction                               #
    # ------------------------------------------------------------------ #

    def _process_wiener(self, audio: np.ndarray) -> np.ndarray:
        if self.noise_psd is None:
            self._prev_input = audio[-self.hop:].copy()
            return audio.copy()

        filtered, self._bp_zi = self._sosfilt(
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
