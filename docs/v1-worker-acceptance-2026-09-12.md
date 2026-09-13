# SVC V1 worker migration acceptance — 2026-09-12

Runtime migration and real V1 conversion verification completed. V2's matcher
was read for before/after comparison only; no V2 inference request, configuration,
container action or rebuild was performed. No algorithm, CPU/offload, GPU guard,
limit, model parameter or driver change was made.

## Source, image and final instance

- Deployment source: `a8ff5be1499442c65ed99396bc5e12c592b2ca8d`.
- Image: `registry.ttd/seed-vc/svc:h-1ec093c04a6f@sha256:e01468f9477df0f859ebaec0b508e24c87f7bd856487852924efd34b7e9f4ffe`.
- Image ID matches the previous edge instance:
  `sha256:644347280aeba85d88652c94262f6646b366fb4cda6d9827ac4ccb5c540e88c0`.
- Worker project `seed-vc-v1-worker`, service/container `svc-v1`, config
  `/opt/seed-vc-v1-worker/compose.yaml`, final rendered SHA256
  `ab9075b7a59ce70a4c809063cacba75ede42b5e823e60ecf4731886743a5c5fa`.
- Final container `df375d72b28b103a7786a275c3302d45ac85f00e979ab874e0ec1eff2c393dab`
  was running/healthy. Python PID3889252 mapped to worker GPU UUID
  `GPU-aee06b60-5da4-ae60-2775-88f094ffeab7`, using 3998MiB in the final sample.
  Qwen remained on that GPU at 5370MiB; no action was taken on it.
- Candidate and formal render manifests are on the control host:
  `/tmp/svc-v1-worker-a8ff5be.{candidate,formal}.yaml.json`.
- Final Caddy `svc-api` and `seed-vc` `/v1` routes each contain only
  `10.0.3.140:7856`, the worker container's confirmed overlay address. The `/v2`
  matcher and upstream were structurally identical to the pre-migration capture.

## Real conversion evidence

Input audio was the committed `examples/source/yae_0.wav` and
`examples/reference/dingzhen_0.wav`, confirmed as real RIFF WAVs. Every output
below decoded as finite, non-silent PCM WAV, 22050Hz, 10.99465 seconds, 484908 bytes.

| Phase / route | Steps | Standard postprocessing | HTTP | Elapsed |
|---|---:|---|---:|---:|
| Candidate dedicated route | 4 (query parameter) | Disabled explicitly | 200 | 27.23s cold |
| Candidate dedicated route | Default50 | Enabled, default settings | 200 | 2.27s |
| Formal worker direct HTTP before edge retirement | 4 | Disabled explicitly | 200 | 15.43s cold |
| Final svc-api after edge retirement | Default50 | Enabled, default settings | 200 | 2.34s |
| Final seed-vc/v1 after edge retirement | Default50 | Enabled, default settings | 200 | 2.33s |

Both public V1 routes were also exercised successfully before edge retirement.
Candidate Python PID3883329 was observed on the target GPU at 3968MiB during the
real request. Observation was bounded; this is not a claim of exhaustive peak
memory or concurrent-load capacity testing. Audio was decoded/measured; no
subjective listening or voice-similarity score is claimed.

Outputs, per-request JSON metadata and final Caddy capture:
`/tmp/svc-v1-migrate-20260912/` on the control host. The worker direct HTTP WAV is
inside the retained final container at `/tmp/svc-v1-worker-before-retire.wav`.

## Edge retirement and rollback

The modern edge `svc-v1` had completed its latest requests and was observed idle
in `do_epoll_wait`. Graceful Docker stop allowed application shutdown and the
SmartModel unload to complete; logs reported `Application shutdown complete`.
Tini exited143 following SIGTERM, not a timeout/SIGKILL. The container was then
removed without deleting any volume or shared data.

Before archiving, the active edge Compose service list was exactly `svc-v1` and
its SHA256 was checked twice against
`dc74555f7f3d2f3a0eb804b7012345afaae02014833e798b4d5892cf1c15f1cf`.
It now resides at `/opt/seed-vc/retired-v1-20260912/compose.retired.yaml`;
`/opt/seed-vc/compose.yaml` no longer exists. The `.env` and shared data were kept.

The already-stopped legacy V1 container
`44bc4e9d5ab2bfba7064732a6e6bb9c8b9284238b4da8ea4d3dce39b8ceb8ad2`
was separately confirmed as `svc-api-1`, command `python api2.py`, and removed
without force. Its Compose service list was exactly `svc-api-1`, SHA256
`e0edbd2db0e5c0a63f8b17fe6e5a31fae6dad6a7b631028f19fcc54f5a51ce64`.
The old startup file is archived at
`/home/docker/svc-api/retired-v1-20260912/compose.retired.yaml`, with the original
active file absent. No other service was selected for cleanup.

Preflight source copies and nonsecret container summaries are retained locally
at `/tmp/svc-v1-preflight-20260912/`, with restricted filesystem permissions.
Rollback restores only modern edge V1 using its captured V1-only config and
`/opt/seed-vc/.env`, then validates V1 conversion before removing worker V1.
Do not restore the older dual-service repository file or resurrect legacy
`svc-api-1`. Use exact V1 service names, never project-wide down/remove-orphans.

## Existing host limitation

Worker reports NVIDIA driver570.153.02 and proprietary module license `NVIDIA`.
There is no `dkms` command or NVIDIA DKMS directory on this existing host, so the
shared policy's DKMS-installation evidence was not obtained. The explicitly
selected worker and existing driver were retained unchanged; this record does
not claim that every host-driver gate passed. No driver remediation was added
to the authorized V1 migration.
