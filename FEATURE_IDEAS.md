# VocalClear — Feature Ideas (Researched, Not Implemented)

This file holds novel feature ideas that have been researched in depth but **deliberately not
implemented**. Each entry has enough detail to make an informed go/no-go call. Mark an item
`[approved]`, `[rejected]`, or leave a comment inline, and a future session will pick it up.

Do not implement anything in this file without an explicit go-ahead — that's the point of it.

---

## 1. Software AGC / Leveler

**What:** A slow-acting RMS-based gain follower applied to the mic signal *after* noise
suppression and *before* output — automatically normalizes loudness so quiet talkers get
boosted and loud talkers get gently pulled down, without a manual gain slider.

**Why now:** Directly motivated by tonight's mic-echo investigation. The user's Windows mic
**Boost is currently set to +20.0 dB** (on top of a level-74 input) — almost certainly the
single biggest amplifier of the open-back headset's acoustic leak. A software leveler would let
them turn that hardware Boost down to 0 dB (removing the amplification of the leak at the
source) while still getting consistent, comfortable loudness in Discord/Zoom.

**How it would work (sketch, not yet built):**
- Track a slow RMS envelope of the *output* signal (after `noise_filter.process()`, in
  `audio_engine.py`'s existing `_apply_gain`/`_to_out` path — these are already the right
  insertion points, no new architecture needed).
- Compute `target_gain = target_level / envelope`, smoothed with separate attack/release time
  constants (fast-ish attack to catch loud spikes, slow release so gain doesn't pump/breathe
  audibly between words).
- Clamp the gain to a sane max (e.g. 1.5–2x) so it can't runaway-amplify the noise floor or any
  residual bleed during silence — this is the standard safety rail for any AGC design.

**Risk/effort:** Low. This is decades-old, well-understood DSP (every broadcast/telecom chain
uses some variant). No new dependency — pure NumPy, a handful of lines. Testable in isolation
(feed it synthetic loud/quiet bursts and check the gain curve) before it ever touches real audio.

**Important caveat:** This does **not** reduce how often bleed gets *through* the VAD gate — it
only normalizes the loudness of whatever passes. It's a complement to the existing VAD
debounce/noise-floor fixes, not a replacement. If bleed is still getting through after the
Boost/enhancements test, AGC won't fix that on its own — but it removes the specific "+20dB is
amplifying a marginal leak into an unambiguous one" failure mode.

**Status:** Researched 2026-06-26. Not implemented. Good first candidate if the Boost/relaunch
test doesn't fully resolve the echo issue.

---

## 2. True Acoustic Echo Cancellation (AEC)

**What:** The structurally-correct fix for "mic picks up what's playing through headphones" —
not a VAD classifier, but a reference-signal-based adaptive filter that subtracts the *known*
played-back waveform from the mic signal (the same family of technique Discord/Krisp and NVIDIA
Broadcast use). Unlike the VAD gate, this works even while bleed is loud and confident, because
it cancels by waveform correlation, not by guessing "is this speech."

**Why it's tempting:** It's the only approach with no fundamental ceiling — the VAD gate (even
with the debounce + noise-floor fixes shipped tonight) can always in principle be defeated by
bleed that's loud and clean enough to be indistinguishable from real speech. AEC doesn't care
how loud or speech-like the bleed is; it cancels it directly.

**Why it's hard — researched in depth, two independent blockers:**

1. **No good Python/Windows library.** `webrtc-audio-processing` Python bindings (which include
   WebRTC's AEC3, the best available open algorithm) are effectively dead — last released 2018,
   require compiling WebRTC's C++ sources (needs a compiler — exactly the MSVC dependency this
   project has always avoided), no prebuilt Windows wheels. `speexdsp` bindings have the same
   compiler problem; the one wheel that exists (`pyspeexaec`) is tied to Python 3.8 and abandoned
   since 2022, and Speex's AEC is a much older/weaker algorithm than AEC3 anyway. A pure-NumPy
   NLMS adaptive filter (e.g. the `padasip` library) is the only no-compiler option, but
   AEC-quality echo cancellation needs filter lengths covering the room/headphone delay+decay
   tail — roughly 100–300ms, which at 48kHz is 5,000–15,000 taps. Recomputing that every 10ms
   block in Python/NumPy is a real CPU feasibility risk that would need prototyping to even know
   if it's viable on a consumer CPU, not just a known-good integration.

2. **No reference signal to cancel against.** AEC needs a copy of what's actually being played
   back to the user (Discord/Zoom's render stream) to know what to subtract. VocalClear has no
   hook into that today — it only sees the mic input and its own output to CABLE Input.
   `sounddevice`'s WASAPI backend has **no loopback-capture support** (confirmed: this is a
   long-standing open feature request, `python-sounddevice` issue #281, still unresolved).
   Getting it would mean adding a second, different audio library (`PyAudioWPatch`, a PyAudio
   fork with loopback support) running a second parallel stream with its own buffering/clock —
   and then manually resampling and aligning it against the mic stream's timing, since playback
   and mic capture aren't on the same clock domain. This is a well-known pattern in dedicated
   conferencing software, but it would be new architecture for this codebase, not a tuning change.

**Risk/effort:** High on two independent axes (library availability + reference-signal capture).
This is a multi-day project with real feasibility risk, not a quick win. Don't start without an
explicit decision to invest that time, and ideally only after confirming the cheaper fixes
(Boost, audio enhancements, AGC) were exhausted and insufficient.

**Status:** Researched 2026-06-26. Not implemented. Treat as "last resort if everything else
fails" rather than a near-term plan.

---

## 3. Soundboard Hotkeys v2

**What:** Bring back per-sound trigger hotkeys for the soundboard — this existed once and was
removed.

**Why it's safe to revisit now:** The original feature was removed because of `AppHangB1`: a
`wait_window()` call inside a Tk event callback created a nested event loop that deadlocked.
That bug is **specific to Tkinter's event loop model** — VocalClear has since fully migrated to
PySide6/Qt (single `QApplication`, single event loop, no Tk anywhere). The deadlock's root cause
doesn't exist in the current architecture.

Better still: this codebase already has **two proven-safe hotkey-detection patterns** running in
production right now, so this isn't even new territory:
- `tray_app.py::_check_toggle_hotkey` — a `QTimer` polling `GetAsyncKeyState` every 50ms on the
  Qt main thread (used for the global R-Ctrl+`\` mute toggle). Simplest pattern, good for a
  small fixed set of global combos.
- `settings_window.py::_PTTCaptureDialog._capture_loop` — a background thread that polls
  `GetAsyncKeyState` and queues detected keys, drained by a `QTimer` on the main thread (used for
  PTT key capture). Better suited if hotkeys need to be captured/assigned dynamically per sound,
  since it doesn't block the main thread while listening.

**How it would fit:** Extend `Sound`/`_PlayingInstance` (in `soundboard.py`) with an optional
hotkey field, persist it in `sounds_config.json` (already has a per-sound dict — easy to extend),
and reuse the `_PTTCaptureDialog` pattern for the "press a key to assign" UI in the soundboard's
context menu (which already has Rename/Remove/Volume entries to add a "Set Hotkey…" alongside).

**Risk/effort:** Low-to-moderate. The hard part (safe hotkey detection without deadlocking) is
already solved and battle-tested elsewhere in this exact codebase. The remaining work is mostly
plumbing: config schema, UI for assignment, and dispatch from the polling loop to `sb.play(name)`.

**Status:** Researched 2026-06-26. Not implemented. Good candidate for a future loop session —
low risk, clear precedent, no new dependencies.

---

## 4. Bleed/Echo Live Indicator

**What:** A small UI indicator (e.g. in the main window header, near the PTT/mute chips) that
lights up when the VAD gate's strict-mode logic actually blocks a candidate "speech" frame for
failing the amplitude-floor check — i.e. visual feedback that bleed was detected and suppressed,
or (in a more advanced version) a warning if bleed-like patterns are detected *passing through*
the gate.

**Why this is genuinely novel, not just another knob:** Every noise-suppression tool the
research turned up (Discord/Krisp, NVIDIA Broadcast) is a black box — you can't tell *why* it let
something through or blocked something, you just find out when a friend says "hey I can hear
someone else." VocalClear's RNNoise + VAD-gate pipeline already computes exactly the signals
needed to expose this (`speech_prob`, `frame_rms`, the learned `_noise_floor_rms`) as part of
tonight's fixes — it's instrumentation that already exists internally and just isn't surfaced.

**How it would work (sketch):**
- `NoiseFilter` already updates `_silence_run`, `_speech_run`, `_noise_floor_rms` per frame.
  Add a lightweight counter (e.g. `_strict_blocks_last_n_sec`) incremented whenever the
  strict-mode RMS-floor check specifically rejects a frame that the probability check alone
  would have passed — this isolates "bleed was caught" from "user was just quiet."
- Expose it as a property the main window's `_tick()` can poll (same pattern as
  `engine.xrun_count`/`process_time_ms` already being polled today).
- A small chip/dot, similar to the existing PTT/mute chips, that flashes briefly when blocks
  happen — purely informational, no new audio-path risk since it only *reads* state that
  already exists.

**Risk/effort:** Low. Almost entirely UI plumbing reusing the existing chip/tick pattern; the
instrumentation hooks already exist from tonight's noise_filter.py changes. The only design
decision is exactly what to count and how sensitive the indicator should be (avoid being so
twitchy it's just visual noise during normal pauses in conversation).

**Status:** Researched 2026-06-26. Not implemented. Pairs naturally with the AGC idea above —
together they'd give the user both a fix (AGC) and visibility (this indicator) instead of having
to take VocalClear's word for it that bleed is or isn't happening.

---

## Explicitly considered and not worth pursuing right now

- **Cross-platform (Mac/Linux) support** — VB-CABLE is Windows-only, and the codebase is deeply
  Windows-specific (ctypes/winreg/WASAPI/Win32 window-snapping throughout). This isn't a
  "feature," it's closer to a fork. Not pursued given this is a personal tool, not a product
  with cross-platform users.
- **Per-environment noise profile presets** — mostly relevant to the Wiener filter tier (RNNoise
  is self-calibrating, no manual profile needed), and RNNoise is the tier actually in use. Low
  value for this specific setup; not pursued.
- **Auto-update mechanism** — not pursued; this isn't currently published/distributed anywhere
  that would need it.
