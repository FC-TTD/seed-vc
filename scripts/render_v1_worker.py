#!/usr/bin/env python3
"""Render only committed V1 deployment data; never contacts a Docker host."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml

REPO = Path(__file__).resolve().parents[1]
SOURCE = "deploy/compose.v1-worker.yaml"
IMAGE = "registry.ttd/seed-vc/svc:h-1ec093c04a6f@sha256:e01468f9477df0f859ebaec0b508e24c87f7bd856487852924efd34b7e9f4ffe"
GPU = "GPU-aee06b60-5da4-ae60-2775-88f094ffeab7"


def render(document, phase):
    if set(document.get("services", {})) != {"svc-v1"}:
        raise ValueError("This entrypoint requires exactly the svc-v1 service")
    result = deepcopy(document)
    service = result["services"]["svc-v1"]
    if service.get("image") != IMAGE or service.get("command") != ["python", "api2.py"]:
        raise ValueError("V1 migration must preserve the reviewed image and inference entrypoint")
    devices = service["deploy"]["resources"]["reservations"]["devices"]
    if len(devices) != 1 or devices[0].get("device_ids") != [GPU]:
        raise ValueError("Worker GPU2 UUID does not match the reviewed placement")
    if phase == "candidate":
        service["labels"] = {"caddy_network": "caddy", "caddy": "http://svc-v1-candidate",
                             "caddy.reverse_proxy": "{{upstreams 7856}}"}
    elif phase != "formal":
        raise ValueError("phase must be candidate or formal")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--phase", choices=["candidate", "formal"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        parser.error("--commit must be a full reviewed source commit")
    raw = subprocess.check_output(["git", "-C", str(REPO), "show", f"{args.commit}:{SOURCE}"])
    document = render(yaml.safe_load(raw), args.phase)
    output = Path(args.output)
    if output.exists():
        parser.error("output already exists; use a new artifact path")
    output.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(document, sort_keys=False)
    output.write_text(content)
    envelope = {"source_commit": args.commit, "source_file": SOURCE,
                "source_sha256": hashlib.sha256(raw).hexdigest(), "phase": args.phase,
                "compose_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "host": "ttd-worker", "project": "seed-vc-v1-worker", "service": "svc-v1",
                "gpu": GPU, "image": IMAGE}
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(envelope, indent=2))
    print(json.dumps(envelope))


if __name__ == "__main__":
    main()
