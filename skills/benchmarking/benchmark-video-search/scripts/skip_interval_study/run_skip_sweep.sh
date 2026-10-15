#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Usage: run_skip_sweep.sh
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
CFG=deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/deepstream/configs/ds-main-config.txt
FROZEN=$(mktemp /tmp/run_skip_eval.XXXXXX.sh)
cp "$HERE/run_skip_eval.sh" "$FROZEN" && chmod +x "$FROZEN"
run() { "$FROZEN" "$@" > "/tmp/eval-$2.log" 2>&1; echo "[$(date +%H:%M:%S)] $2 EXIT=$?"; }
run 1 vad-r1-v2-benchmark-skip1-notags
run 2 vad-r1-v2-benchmark-skip2-notags
run 5 vad-r1-v2-benchmark-skip5-notags
run 6 vad-r1-v2-benchmark-skip6-notags
run 3 vad-r1-v2-benchmark-skip3-pgie0-notags 0
git -C "$REPO" checkout -- "$CFG" && docker restart vss-rtvi-cv >/dev/null && echo "[$(date +%H:%M:%S)] config reverted to git, rtvi-cv restarted"
echo SWEEP_DONE
