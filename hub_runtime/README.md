# Seed-VC V1 managed entry

This `hub` branch adds the shared `ttd-model-runtime` entry without modifying
`api2.py`, `app.py`, `seed_vc_wrapper.py`, the inference modules or the existing V2
entry. Baseline `0e21993` was checked against the running V1/V2 image file hashes.

- `api.py` preserves the V1 `/svc_file` and `/infer_vc` query/form parameter
  locations and tuned defaults. Real inference runs in a synchronous managed
  callback; native generators are fully consumed and closed before completion.
- `postprocess_api.py` copies existing CPU preset APIs and uses the migrated SDK
  audio helpers. Those CPU operations do not acquire a GPU lease.
- `ui.py` copies `app.py:create_v1_interface`, retaining its eight controls,
  bilingual descriptions, examples and streaming/full outputs. Initial steps=50
  and auto F0=false match the existing API; full audio uses the same standard
  postprocessing. Native streaming chunks retain the original streaming path.
- `adapter.py` loads the original SeedVCWrapper, preserves its placement and
  precision, and serializes the native generator while it owns mutable caches.
  There is no old SmartModel/TTL/notifier in this entry.

`python -m hub_runtime describe` exports the API schema without model loading;
`python -m hub_runtime` runs the API and copied UI. Runtime identity, private token
and state directory come from the shared Hub deployment contract.

Existing API aliases `http://svc-api` and `http://seed-vc/v1` are retained. The new
native UI is at `http://svc-api/ui/`; `/v1/ui` redirects to this canonical UI alias.
The shared `/v2` route is owned by the existing V2 service and is not changed.

First deployment targets worker only. Use a conservative single-card reservation
for initial inference evidence, then record observed usage without automatically
reducing its budget or changing official CPU/GPU behavior. Local tests use real
FastAPI/Gradio/audio with fake inference; GPU acceptance is recorded separately.

## Deployed verification (2026-09-15)

V1 is now managed on worker GPU2. Existing `svc-api` and `seed-vc/v1` API aliases
are served by the new CPU entry, with native UI at `http://svc-api/ui/`. Original
V1 was gracefully stopped and its Compose service retired; V2 was not changed.

Real normal (22050Hz) and F0 (44100Hz, pitch shift) conversion, CPU presets,
unload/reload, Gradio HLS playlist/segment and full WAV download all passed.
Production Gateway `svc` passed successful calls, maintenance rejection, stale
grant rejection/recovery and PostgreSQL usage verification. Index regression
through the same Gateway also passed.

The current reservation is 19GiB plus the existing 2GiB safety margin. Initial
20GiB blocked reload after accounting for residual CUDA context and existing
external usage; the reservation was corrected through actor retirement and
replacement, without changing an active lease or model placement/precision.

The HTTP adapter omits redundant X-Forwarded-Host on UI routes to work around
Gradio5.23 dropping the queue path. Native dependencies are unchanged. Native
streaming uses HLS; the verification client checks the browser's stream endpoint
and full-file endpoint separately because Gradio5.23 Client's generic file
downloader does not handle the HLS playlist correctly.

## SVC V2

`HUB_MODEL_VARIANT=v2 python -m hub_runtime` runs a separate managed V2 instance.
`api_v2.py` supplies the Form contract and tuned defaults; `app.py:create_v2_interface`
is copied in `ui_v2.py` with its eleven native controls. The native CUDA/FP16 AR cache
setup and framework placement remain unchanged. Both API and UI hold the runtime
activity and native cache lock until the streaming generator actually completes.

Deploy with `compose.hub-v2.yml` and `compose.hub-v2-business.yml`. Existing
`http://seed-vc/v2` stays valid; `http://svc-v2-api` is the canonical pool/API origin
and `/ui/` serves the copied native UI. The previous `/unload` endpoint returns 409
with the Hub operator drain instruction; lifecycle writes use the authenticated
pool ledger, never a second SmartModel manager. Native checkpoint/cache mounts
remain read-only as in the previous V2 deployment.


V1 concurrent execution: its DiT cache is explicitly disabled, so new config-local mel/window dictionaries and short RMVPE initialization guards replace the whole-generator inference lock. Shared weights/dtype/native algorithms remain unchanged. V2 retains its original exclusive AR cache ownership. Real V1 concurrent GPU output/peaks are validated separately from CPU contract tests.
