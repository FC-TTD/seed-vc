# Move only SVC V1 to worker

Status: prepared for review; not committed or deployed by this preparation step.
The runtime image, CUDA inference code, sampling defaults, lifecycle timeout,
shared memory setting and mounts are preserved. No CPU/offload, memory limit,
GPU guard, algorithm or model optimization is part of this migration.

V2 is owned by its separate P5000 deployment. Do not run `deploy.sh formal`,
`ansible/site.yml`, `docker compose down`, `--remove-orphans`, or any command
which selects V2. The old checkout's dual-service Compose/playbook is historical
source evidence, not an input to this V1 migration.

## Facts and risks to report before runtime writes

- Edge currently has one running `svc-v1`, command `python api2.py`, fixed image
  `registry.ttd/seed-vc/svc:h-1ec093c04a6f` resolved to digest
  `sha256:e01468f9477df0f859ebaec0b508e24c87f7bd856487852924efd34b7e9f4ffe`.
  Its active Compose project is `seed-vc` at `/opt/seed-vc/compose.yaml`.
- The latest edge Compose file is already V1-only, changed by the other session.
  Do not overwrite it with the older dual-service repository copy. Recheck its
  hash before retirement so a concurrent edit is not lost.
- Worker GPU2 UUID `GPU-aee06b60-5da4-ae60-2775-88f094ffeab7` had 19,197 MiB
  free at sampling; edge V1's attributable process used 3,552 MiB. This is a
  candidate placement, not a concurrent peak-memory guarantee. Qwen also uses
  this worker GPU. Worker GPU0 is reserved for the separate MOSS migration.
- Three existing `/TTD-Data/seed-vc` mounts resolve through worker's NFS share.
  No model copy/rebuild or ownership changes are required. Both V1 instances
  can temporarily read the same weights/cache; do not run simultaneous writes
  to shared outputs while checking the candidate.
- Promotion recreates only worker's V1 container to install formal Caddy labels,
  causing a cold model load. Edge V1 stays available until the candidate passes.
  The brief overlap produces two equivalent V1 upstreams; final route evidence
  must show only worker after edge retirement. Do not change the V2 matcher.
- The API's `steps`, `length_adjust`, `inference_cfg_rate`, `f0_conditioned`,
  `auto_f0_adjust` and `pitch_shift` are query parameters. Sending `steps` as
  a multipart field silently leaves the default 50 steps. `post_process`,
  `lufs`, `trim_silence`, and `enable_eq` are multipart fields.

## Reviewed source and isolated deployment commands

The only migration definition is `deploy/compose.v1-worker.yaml`. It contains
exactly one service, `svc-v1`. `scripts/render_v1_worker.py` reads it via
`git show <full-commit>:<file>`, preserves the pinned image/GPU and rejects
additional services. It has no SSH or Docker actions.

After review and commit, render candidate and formal artifacts from that same
commit. Use new output files; both include a JSON source/phase/hash manifest:

```bash
python scripts/render_v1_worker.py --commit FULL_COMMIT --phase candidate --output /tmp/svc-v1-worker.candidate.yaml
python scripts/render_v1_worker.py --commit FULL_COMMIT --phase formal --output /tmp/svc-v1-worker.formal.yaml
```

After reporting the above risks and receiving execution coordination, copy the
candidate artifact to worker `/opt/seed-vc-v1-worker/compose.yaml`. Verify worker
GPU2 UUID, free memory, all three directories, and the external `caddy` network.
The only container creation command is explicitly scoped:

```bash
ssh root@ttd-worker 'docker compose --project-name seed-vc-v1-worker -f /opt/seed-vc-v1-worker/compose.yaml config --services'
ssh root@ttd-worker 'docker compose --project-name seed-vc-v1-worker -f /opt/seed-vc-v1-worker/compose.yaml pull svc-v1'
ssh root@ttd-worker 'docker compose --project-name seed-vc-v1-worker -f /opt/seed-vc-v1-worker/compose.yaml up -d --no-deps svc-v1'
```

Require `config --services` to output only `svc-v1`. Candidate labels expose only
`http://svc-v1-candidate`; no production or V2 route is present. Smoke candidate,
then replace only this worker-owned file with the reviewed formal artifact and
run the same `up -d --no-deps svc-v1`. Never replace `/opt/seed-vc/compose.yaml`
from the old dual-service repository.

Recheck both formal V1 routes, wait for in-flight edge requests to finish, then
stop/remove only edge's explicitly named `svc-v1` container. Archive/retire the
edge V1-only deployment declaration so a future deployment cannot resurrect it;
first confirm its current services list still contains only V1. Leave shared
weights/cache and all other projects unchanged. Record source/management/runtime
agreement and verify the final Caddy V1 upstream resolves only to worker.

## Minimal real conversion smoke

Use the tracked real samples `examples/source/yae_0.wav` and
`examples/reference/dingzhen_0.wav`; verify these are actual WAV files, not LFS
pointers. Candidate quick smoke may explicitly select 4 steps in the URL; it is
only an operational check, not a change to service defaults. Formal acceptance
should also run the default 50-step path or the user's normal settings.

```bash
curl --fail --silent --show-error --max-time 900 \
  -F src_file=@examples/source/yae_0.wav \
  -F ref_file=@examples/reference/dingzhen_0.wav \
  -F post_process=false \
  'http://svc-v1-candidate/svc_file?steps=4' --output /tmp/svc-v1-candidate.wav
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,sample_rate,duration \
  -of default=noprint_wrappers=1 /tmp/svc-v1-candidate.wav
```

Validate decoded finite, non-silent audio and duration versus the source, and
listen for actual voice conversion. Repeat through both `http://svc-api/svc_file`
and `http://seed-vc/v1/svc_file` after promotion. Keep waveform outputs and evidence
outside Git, with phase and source commit recorded. Capture GPU PID/container
assignment and peak memory before/during generation; health200 alone is not
acceptance. Do not run a V2 smoke as part of this V1-only task.

## Rollback boundary

Before writes save the edge V1-only Compose bytes, allowlisted environment,
container image digest and metadata, and candidate/formal manifests. The first
read-only preparation saved these under `/tmp/svc-v1-preflight-20260912` on the
control host; refresh before execution. An acceptance failure restores only
edge V1 through its captured V1-only source configuration, and stops/removes
only worker's V1. Do not restore any older dual-service file or run a project-wide
command that could touch the separate V2 deployment. Shared data is retained.
