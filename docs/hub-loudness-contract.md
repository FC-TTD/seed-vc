# Managed loudness normalization

The managed V1 router restores `POST /postprocess/normalize-loudness`, consumed
by Model Gateway `loudness-normalize`. The endpoint added to the old API branch
at `fb07d8b` was absent from the managed router, producing upstream 404 / Gateway
502 for Dayan result processing.

Multipart fields remain `file`, `target_loudness` (-23 LUFS by default, -40 to
-10), optional `lufs` override, `enable_limiter` (true), and `limiter_threshold`
(0.98, range 0.5 to 1). Processing uses existing CPU pyloudnorm and limiter
dependencies, with no model load, residency grant or GPU task decoration.

Unlike the voice-specific shared helper, this endpoint preserves mono/stereo
channel count and uses one measured integrated loudness and common gain across
channels. Sample rate and frame count are preserved. Only measurement is padded
for clips below 400 ms. Silence/below-gate signals remain unchanged. Optional
limiting uses the requested threshold; disabling it retains the legacy full-scale
safety ceiling. Peak limiting can prevent reaching the requested LUFS. No EQ,
preset, saturation, delay or reverb is applied. Response is PCM24 WAV with an
explicit byte length. Malformed, empty and nonfinite audio is rejected as 400.

`tests/test_loudness_api.py` exercises the real managed HTTP router and audio DSP
with stereo channels carrying different frequencies/amplitudes, LUFS measurement,
waveform/common-gain comparison, silence/short signals, limiter/alias behavior and
invalid input. GPU-task wrappers fail if invoked by these CPU tests.

Deploy `deploy/Dockerfile.loudness-overlay` over the immutable accepted runtime
image via the Hub managed release coordinator for both prepared candidates.
The overlay only copies the router; it does not reinstall dependencies or change
GPU placement, precision, concurrency, SDK, entry or node components. Normal Hub
release acceptance still requires a real native SVC task; CPU normalization alone
must not be represented as GPU lifecycle acceptance. Public Gateway and Windows
result processing require separate live validation before release completion.
