#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-verify}"
shift || true

case "${mode}" in
  verify) tags="sync" ;;
  build) tags="sync,ci" ;;
  formal) tags="sync,ci,prepare,deploy,verify,smoke" ;;
  *)
    echo "usage: ./deploy.sh [verify|build|formal] [ansible-playbook arguments...]" >&2
    exit 2
    ;;
esac

if ! git -C "${root_dir}" diff --quiet || ! git -C "${root_dir}" diff --cached --quiet; then
  echo "formal deployment input must be committed before build or deployment" >&2
  exit 1
fi
if [[ -n "$(git -C "${root_dir}" ls-files --others --exclude-standard)" ]]; then
  echo "formal deployment input contains untracked files" >&2
  exit 1
fi

source_commit="$(git -C "${root_dir}" rev-parse HEAD)"
snapshot_root="$(mktemp -d /tmp/seed-vc-formal.XXXXXX)"
cleanup() {
  rm -rf -- "${snapshot_root}"
}
trap cleanup EXIT

git -C "${root_dir}" archive "${source_commit}" | tar -x -C "${snapshot_root}"
export SVC_SOURCE_COMMIT="${source_commit}"
export SVC_SOURCE_SNAPSHOT="${snapshot_root}"

ansible-playbook \
  -i "${snapshot_root}/ansible/inventory.ini" \
  "${snapshot_root}/ansible/site.yml" \
  --tags "${tags}" \
  "$@"
