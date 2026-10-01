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
# Regression test for the MV3DT standalone scripts. Self-contained: paths
# default to this checkout, env vars still override.
# Run:  STAGE_CONFIGS=<repo>/scripts/stage-configs.sh bash "$0"
#
# Bug 6564660. staging succeeded with NUM_CAMS, calibration, camInfo and
# pub/sub disagreeing, and the mismatch only surfaced when sources failed later.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"
: "${STAGE_CONFIGS:=$MV3DT/scripts/stage-configs.sh}"
: "${STAGE_CONFIGS}"
pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1 (want $3, got $2)"; fail=$((fail+1)); fi; }

setup() {  # $1=tmp  $2..=cameras in camInfo
  local t="$1"; shift
  rm -rf "$t"; mkdir -p "$t/generated/camInfo" "$t/generated/configs" "$t/scripts" "$t/configs"
  cp -r "$(dirname "$STAGE_CONFIGS")/.." "$t/src" 2>/dev/null
  cp -r "$t/src/." "$t/" 2>/dev/null; rm -rf "$t/src" "$t/generated"
  mkdir -p "$t/generated/camInfo"
  local c
  for c in "$@"; do printf 'name: %s\n' "$c" > "$t/generated/camInfo/$c.yml"; done
  python3 - "$t" "$@" <<'PY'
import json, sys
t, cams = sys.argv[1], sys.argv[2:]
json.dump({"sensors": [{"id": c} for c in cams]}, open(t + "/generated/calibration.json", "w"))
with open(t + "/generated/pub_sub_info_config.yml", "w") as f:
    f.write("pubBrokerTopicStr:\n")
    for c in cams:
        f.write("  %s: localhost:1883;/trck/%s\n" % (c, c))
PY
}

T=$(mktemp -d)/s
setup "$T" Camera Camera_01 Camera_02 Camera_03
( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=4 ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "consistent set stages" "$?" 0

( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=8 ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "NUM_CAMS disagreeing with camInfo is refused" "$?" 1

out="$( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=8 ./scripts/stage-configs.sh 2>&1 )"
case "$out" in *"NUM_CAMS=8 but generated/camInfo has 4"*) chk "the mismatch is named" ok ok ;;
               *) chk "the mismatch is named" "missing" ok ;; esac

# calibration listing a sensor camInfo does not have
python3 - "$T" <<'PY2'
import json, sys
t = sys.argv[1]
cams = ["Camera", "Camera_01", "Camera_02", "Camera_03", "Camera_04"]
json.dump({"sensors": [{"id": c} for c in cams]}, open(t + "/generated/calibration.json", "w"))
PY2
out="$( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=4 ./scripts/stage-configs.sh 2>&1 )"
case "$out" in *"calibration.json sensors"*) chk "calibration/camInfo mismatch is caught" ok ok ;;
               *) chk "calibration/camInfo mismatch is caught" missing ok ;; esac
python3 - "$T" <<'PY3'
import json, sys
t = sys.argv[1]
cams = ["Camera", "Camera_01", "Camera_02", "Camera_03"]
json.dump({"sensors": [{"id": c} for c in cams]}, open(t + "/generated/calibration.json", "w"))
PY3

# pub/sub listing a sensor camInfo does not have
{ echo "pubBrokerTopicStr:"
  for c in Camera Camera_01 Camera_02 Camera_09; do echo "  $c: localhost:1883;/trck/$c"; done
} > "$T/generated/pub_sub_info_config.yml"
out="$( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=4 ./scripts/stage-configs.sh 2>&1 )"
case "$out" in *"pub_sub_info_config.yml sensors"*) chk "pub/sub mismatch is caught" ok ok ;;
               *) chk "pub/sub mismatch is caught" missing ok ;; esac
{ echo "pubBrokerTopicStr:"
  for c in Camera Camera_01 Camera_02 Camera_03; do echo "  $c: localhost:1883;/trck/$c"; done
} > "$T/generated/pub_sub_info_config.yml"

# a refused restage must leave the previous staged config intact
( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=4 ./scripts/stage-configs.sh ) >/dev/null 2>&1
before="$(grep -m1 '^batch-size' "$T/generated/configs/ds-main-config-mv3dt.txt")"
( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=9 ./scripts/stage-configs.sh ) >/dev/null 2>&1
after="$(grep -m1 '^batch-size' "$T/generated/configs/ds-main-config-mv3dt.txt")"
chk "a refused restage writes nothing partial" "$after" "$before"

rm -rf "$T/generated/camInfo"
( cd "$T" && OSD=0 INPUT_MODE=stream NUM_CAMS=4 ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "no camInfo yet is not fatal in stream mode" "$?" 0

rm -rf "$(dirname "$T")"
echo "  6564660: $pass passed, $fail failed"
[ "$fail" = 0 ]
