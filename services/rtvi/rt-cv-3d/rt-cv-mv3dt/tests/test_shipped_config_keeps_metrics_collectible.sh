#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Regression test for the MV3DT standalone scripts. Self-contained: paths
# default to this checkout, env vars still override.
# Run:  bash tests/run.sh          (or: bash "$0" for just this one)
#
# Bug 6821633. PR #2154 added an activation check that reads /api/v1/metrics and,
# in the same change, set nvdslogger=0 in both sink blocks. Those statistics come
# from nvdslogger, so the check could never verify anything: live streams were
# reported UNSEEN and the run exited 2.
#
# Measured on a four-camera stack rather than assumed: with it off, /api/v1/metrics
# returns stream-count 0 and no stream-stats while the pipeline runs at 30 FPS. The
# **PERF console blocks are unaffected either way, so this is only about the check.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"

pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1));
        else echo "  FAIL $1 (want '$3', got '$2')"; fail=$((fail+1)); fi; }

CFG="$MV3DT/configs/ds-main-config-mv3dt.txt"

chk "the shipped config exists"                  "$([ -f "$CFG" ] && echo yes || echo no)" "yes"
chk "no sink disables nvdslogger"                "$(grep -c '^[[:space:]]*nvdslogger[[:space:]]*=[[:space:]]*0' "$CFG")" "0"
chk "every sink block enables it"                "$(grep -c '^[[:space:]]*nvdslogger[[:space:]]*=[[:space:]]*1' "$CFG")" \
                                                 "$(grep -c '^[[:space:]]*nvdslogger[[:space:]]*=' "$CFG")"

# And the staged output, since that is what the container actually loads.
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cp -r "$MV3DT" "$T/c" 2>/dev/null
rm -rf "$T/c/generated"
( cd "$T/c" && OSD=0 INPUT_MODE=stream ./scripts/stage-configs.sh ) >/dev/null 2>&1
STAGED="$T/c/generated/configs/ds-main-config-mv3dt.txt"
chk "staging produces a config"                  "$([ -f "$STAGED" ] && echo yes || echo no)" "yes"
chk "the staged config keeps it enabled"         "$(grep -c '^[[:space:]]*nvdslogger[[:space:]]*=[[:space:]]*0' "$STAGED" 2>/dev/null)" "0"

echo "  6821633 metrics collectible: $pass passed, $fail failed"
[ "$fail" = 0 ]
