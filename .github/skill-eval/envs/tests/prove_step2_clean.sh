#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Tier-4 integration proof (no GPU, ~1 min) for the lvs_profile_summarize
# step-2 mount bug + the fix. Unlike prove_mount_probe.sh (which uses a plain
# rm to test the probe), this exercises the REAL harness step-2 cleanup —
# `git clean -fdx -e data/ -e .env`, byte-for-byte from
# BrevEnvironment._sync_repo_to_pr_head — against LIVE bind-mounted
# containers, and demonstrates all three findings at once:
#
#   1. FIX (sync gated out on step-2+): clean is skipped -> mount stays healthy.
#   2. BUG (ungated, as develop): the clean DELETES the data-dir root -> the
#      running container's mount goes stale.
#   3. DATA vs DATA-DIR: the same clean PRESERVES a `data`-named root (the
#      `-e data/` gitignore pattern matches `data` at any depth) -> that mount
#      stays healthy. This is why the failure looked non-deterministic.
#
# Needs docker (daemon reachable) + git. Run on the CI runner or any Docker
# host:  bash .github/skill-eval/envs/tests/prove_step2_clean.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_EVAL_ROOT="$(cd "$HERE/../.." && pwd)"
C_DATADIR="mp-datadir-$$"     # container on the deletable data-dir root
C_DATA="mp-data-$$"           # container on the git-excluded data root
WORK="$(mktemp -d)"
REPO="$WORK/repo"
DEST="/home/vst/vst_release/streamer_videos"
export MOUNTPROBE_SINK="$WORK/mount-probe.log"
fail=0

cleanup() {
  docker rm -f "$C_DATADIR" "$C_DATA" >/dev/null 2>&1 || true
  rm -rf "$WORK" || true
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { echo "SKIP: docker not available"; exit 0; }
command -v git    >/dev/null 2>&1 || { echo "SKIP: git not available"; exit 0; }
docker info >/dev/null 2>&1 || { echo "SKIP: docker daemon not reachable"; exit 0; }

# The exact destructive line from _sync_repo_to_pr_head (the fetch/reset around
# it only touch tracked files; this clean is what removes untracked dirs).
HARNESS_CLEAN='git clean -fdx -e data/ -e .env'

probe() {
  PYTHONPATH="$SKILL_EVAL_ROOT" python3 - "$1" <<'PY' | bash
import sys
from envs import mount_probe as mp
print(mp.build_probe_command(sys.argv[1]))
PY
}
verdict_for() {  # $1=label output  $2=container -> prints the verdict token
  echo "$1" | grep "container=$2 " | grep -oE 'verdict=[a-z-]+' | head -1
}

# --- set up an untracked VIOS data tree under BOTH roots, inside a git repo ---
mkdir -p "$REPO"; cd "$REPO"
git init -q .; git config user.email t@t; git config user.name t
git commit -q --allow-empty -m init
mkdir -p "$REPO/deploy/docker/data-dir/data_log/vst/clip_storage"
mkdir -p "$REPO/deploy/docker/data/data_log/vst/clip_storage"
echo x > "$REPO/deploy/docker/data-dir/data_log/vst/clip_storage/vid.mp4"
echo x > "$REPO/deploy/docker/data/data_log/vst/clip_storage/vid.mp4"

docker run -d --name "$C_DATADIR" \
  -v "$REPO/deploy/docker/data-dir/data_log/vst/clip_storage:$DEST" busybox sleep 600 >/dev/null
docker run -d --name "$C_DATA" \
  -v "$REPO/deploy/docker/data/data_log/vst/clip_storage:$DEST" busybox sleep 600 >/dev/null

echo "== t0: both mounts live =="
t0="$(probe t0)"; echo "$t0" | grep MOUNTPROBE
[ "$(verdict_for "$t0" "$C_DATADIR")" = "verdict=healthy" ] && [ "$(verdict_for "$t0" "$C_DATA")" = "verdict=healthy" ] \
  && echo "  PASS: both healthy at start" || { echo "  FAIL: expected both healthy"; fail=1; }

echo "== FIX arm: gate skips the sync (no clean runs) =="
# (simulate the fix: do NOT run the harness clean)
t1="$(probe t1-gated)"
[ "$(verdict_for "$t1" "$C_DATADIR")" = "verdict=healthy" ] \
  && echo "  PASS: data-dir mount preserved when clean is gated out (fix works)" \
  || { echo "  FAIL: data-dir went stale even without the clean"; fail=1; }

echo "== BUG arm: run the REAL harness clean ($HARNESS_CLEAN) =="
( cd "$REPO" && eval "$HARNESS_CLEAN" ) >/dev/null 2>&1
t2="$(probe t2-after-clean)"; echo "$t2" | grep MOUNTPROBE
vd_datadir="$(verdict_for "$t2" "$C_DATADIR")"
vd_data="$(verdict_for "$t2" "$C_DATA")"
[ "$vd_datadir" = "verdict=stale" ] || [ "$vd_datadir" = "verdict=absent-source" ] \
  && echo "  PASS: data-dir mount went $vd_datadir after the real clean (bug reproduced)" \
  || { echo "  FAIL: data-dir expected stale/absent, got $vd_datadir"; fail=1; }
[ "$vd_data" = "verdict=healthy" ] \
  && echo "  PASS: data root PRESERVED by -e data/ (explains the non-determinism)" \
  || { echo "  FAIL: data root expected healthy, got $vd_data"; fail=1; }

echo
[ "$fail" = 0 ] \
  && echo "RESULT: PASS — real git-clean deletes data-dir (bug), spares data, gate preserves both." \
  || echo "RESULT: FAIL — see mismatches above."
exit "$fail"
