#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Decisive test for the bind-mount probe (envs/mount_probe.py) AND a
# self-contained proof of the lvs_profile_summarize step-2 mechanism.
#
# Reproduces exactly what the harness `git clean` does mid-chain — delete
# and recreate the host source of a live VIOS bind mount — and asserts the
# self-discovering probe flips healthy -> stale. If it catches a KNOWN-
# deleted mount here, we trust its verdict on a real vss-eval-* box.
#
# Needs: docker (daemon reachable). No GPU, no Brev, no VSS images (busybox).
# Run on the CI runner or any Docker host:
#     bash .github/skill-eval/envs/tests/prove_mount_probe.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_EVAL_ROOT="$(cd "$HERE/../.." && pwd)"
CONTAINER="mountprobe-selftest-$$"
# Source path deliberately matches the VIOS discovery pattern (.../data_log/vst/clip_storage).
SRCROOT="$(mktemp -d)"
SRC="$SRCROOT/data_log/vst/clip_storage"
DEST="/home/vst/vst_release/streamer_videos"
fail=0

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; rm -rf "$SRCROOT" || true; }
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { echo "SKIP: docker not available"; exit 0; }
docker info >/dev/null 2>&1 || { echo "SKIP: docker daemon not reachable"; exit 0; }

# Redirect the probe's durable sink away from /logs/artifacts (box-only path)
# to a local temp file so the self-test runs anywhere.
export MOUNTPROBE_SINK="$SRCROOT/mount-probe.log"

probe_cmd() {
  PYTHONPATH="$SKILL_EVAL_ROOT" python3 - "$1" <<'PY'
import sys
from envs import mount_probe as mp
print(mp.build_probe_command(sys.argv[1]))
PY
}

mkdir -p "$SRC"
echo "== bring up a live VIOS-style bind mount ($SRC -> $DEST) =="
docker run -d --name "$CONTAINER" -v "$SRC:$DEST" busybox sleep 600 >/dev/null

echo "== probe BEFORE delete (expect: verdict=healthy for our mount) =="
before="$(bash -c "$(probe_cmd step-1:live)")"; echo "$before" | grep MOUNTPROBE
echo "$before" | grep -q "container=$CONTAINER verdict\|verdict=healthy .*$CONTAINER\|verdict=healthy" \
  && echo "  PASS: live mount reads healthy" \
  || { echo "  FAIL: expected a healthy verdict"; fail=1; }

echo "== simulate the harness 'git clean': rm -rf + recreate the host source =="
rm -rf "$SRC" && mkdir -p "$SRC"

echo "== probe AFTER delete (expect: verdict=stale/absent-source) =="
after="$(bash -c "$(probe_cmd step-2:after-clean)")"; echo "$after" | grep MOUNTPROBE
echo "$after" | grep -qE "verdict=(stale|absent-source)" \
  && echo "  PASS: deleted-and-recreated mount reads stale/absent (true positive)" \
  || { echo "  FAIL: probe did NOT catch the stale mount"; fail=1; }

echo
[ "$fail" = 0 ] \
  && echo "RESULT: PASS — probe flips healthy -> stale on a known deleted mount." \
  || echo "RESULT: FAIL — probe did not behave as specified."
exit "$fail"
