#!/usr/bin/env bash
set -euo pipefail

BUILD_HOST="${BUILD_HOST:-ttd-nest}"
BUILD_DIR="${BUILD_DIR:-/home/docker/svc-api-build}"
TARGET_HOST="${TARGET_HOST:-ttd-edge}"
TARGET_DIR="${TARGET_DIR:-/home/docker/svc-api}"
IMAGE="${IMAGE:-registry.ttd/svc-api:latest}"
SERVICE_URL="${SERVICE_URL:-http://svc-api}"
LOCAL_URL="${LOCAL_URL:-http://127.0.0.1:7000}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
svc-api compose deploy

Usage:
  ./deploy.sh [--check]

Environment:
  BUILD_HOST   Remote build host. Default: ttd-nest
  BUILD_DIR    Remote build directory. Default: /home/docker/svc-api-build
  TARGET_HOST  Remote Docker host. Default: ttd-edge
  TARGET_DIR   Remote project directory. Default: /home/docker/svc-api
  IMAGE        Image tag to build and deploy. Default: registry.ttd/svc-api:latest
  SERVICE_URL  Caddy-routed service URL for smoke. Default: http://svc-api
  LOCAL_URL    Host-local published URL for smoke. Default: http://127.0.0.1:7000
EOF
}

CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -c|--check)
      CHECK_ONLY=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

EXCLUDES=(
  "--exclude=.git/"
  "--exclude=.conda/"
  "--exclude=__pycache__/"
  "--exclude=.pytest_cache/"
  "--exclude=.mypy_cache/"
  "--exclude=.ruff_cache/"
  "--exclude=.cache/"
  "--exclude=checkpoints/"
  "--exclude=reconstructed/"
  "--exclude=*.pyc"
  "--exclude=*.log"
)

if [[ "$CHECK_ONLY" == "1" ]]; then
  rsync -azn --delete "${EXCLUDES[@]}" "$ROOT_DIR/" "$BUILD_HOST:$BUILD_DIR/"
  rsync -azn --delete compose.yaml "$TARGET_HOST:$TARGET_DIR/"
  ssh "$BUILD_HOST" "docker buildx version >/dev/null"
  ssh "$TARGET_HOST" "cd '$TARGET_DIR' && docker compose config --quiet"
  exit 0
fi

rsync -az --delete "${EXCLUDES[@]}" "$ROOT_DIR/" "$BUILD_HOST:$BUILD_DIR/"
ssh "$BUILD_HOST" "cd '$BUILD_DIR' && \
  docker build --network host -t '$IMAGE' . && \
  docker push '$IMAGE'"

rsync -az --delete compose.yaml "$TARGET_HOST:$TARGET_DIR/"
ssh "$TARGET_HOST" "cd '$TARGET_DIR' && \
  docker compose pull svc-api-1 && \
  docker compose up -d svc-api-1 && \
  docker compose ps svc-api-1"

ssh "$TARGET_HOST" "set -e; \
  curl -fsS '$LOCAL_URL/health'; echo; \
  curl -fsS '$LOCAL_URL/postprocess/presets' | head -c 500; echo; \
  curl -fsS '$SERVICE_URL/postprocess/presets' | head -c 500; echo; \
  python3 - <<'PY'
import math
import struct
import wave

path = '/tmp/svc-api-smoke.wav'
sr = 16000
with wave.open(path, 'wb') as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(sr)
    frames = bytearray()
    for i in range(sr // 4):
        sample = int(0.1 * 32767 * math.sin(2 * math.pi * 440 * i / sr))
        frames.extend(struct.pack('<h', sample))
    f.writeframes(frames)
PY
  curl -fsS -o /tmp/svc-api-local-normalized.wav -F file=@/tmp/svc-api-smoke.wav -F target_loudness=-16 '$LOCAL_URL/postprocess/normalize-loudness'; \
  test -s /tmp/svc-api-local-normalized.wav; \
  curl -fsS -o /tmp/svc-api-caddy-normalized.wav -F file=@/tmp/svc-api-smoke.wav -F target_loudness=-16 '$SERVICE_URL/postprocess/normalize-loudness'; \
  test -s /tmp/svc-api-caddy-normalized.wav"
