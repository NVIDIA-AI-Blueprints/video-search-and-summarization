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
# Bug 6786802. In file mode the staged source list is built from the camera names
# in camInfo, file:///videos/<camera>.mp4, and nothing looked at what VIDEO_DIR
# really held. A calibration naming Camera_01 with a directory holding
# Wrong_Camera.mp4 instead staged cleanly, and the mismatch only surfaced when
# DeepStream failed to open a source. Staging must name every missing, unexpected
# and ambiguous clip and write nothing.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${STAGE_CONFIGS:=$MV3DT/scripts/stage-configs.sh}"

source "${MV3DT_TESTS_DIR}/lib.sh"

CAMS4=(Camera Camera_01 Camera_02 Camera_03)

# $1 = scratch dir. A copy of the component with a four-camera calibration.
setup() {
  local t="$1" c
  rm -rf "$t"; mkdir -p "$t"
  cp -r "$(dirname "$STAGE_CONFIGS")/.." "$t/src" 2>/dev/null
  cp -r "$t/src/." "$t/" 2>/dev/null; rm -rf "$t/src" "$t/generated"
  mkdir -p "$t/generated/camInfo"
  for c in "${CAMS4[@]}"; do printf 'name: %s\n' "$c" > "$t/generated/camInfo/$c.yml"; done
  python3 - "$t" "${CAMS4[@]}" <<'PY'
import json, sys
t, cams = sys.argv[1], sys.argv[2:]
json.dump({"sensors": [{"id": c} for c in cams]}, open(t + "/generated/calibration.json", "w"))
with open(t + "/generated/pub_sub_info_config.yml", "w") as f:
    f.write("pubBrokerTopicStr:\n")
    for c in cams:
        f.write("  %s: localhost:1883;/trck/%s\n" % (c, c))
PY
}

# $1 = directory, then the file names to create (empty files are enough).
clips() {
  local d="$1" f; shift
  rm -rf "$d"; mkdir -p "$d"
  for f in "$@"; do : > "$d/$f"; done
}

# Sets OUT and STATUS. $1 = scratch, $2 = VIDEO_DIR, then extra VAR=value words.
stage() {
  local t="$1" vd="$2"; shift 2
  OUT="$(cd "$t" && env OSD=0 INPUT_MODE=file NUM_CAMS=4 VIDEO_DIR="$vd" "$@" ./scripts/stage-configs.sh 2>&1)"
  STATUS=$?
}

staged() { [[ -f "$1/generated/configs/ds-main-config-mv3dt.txt" ]]; }

new_scratch() { T="$(mktemp -d)/s"; setup "$T"; V="$T/clips"; }
drop_scratch() { rm -rf "$(dirname "$T")"; }

t_matching_clips_stage() {
  new_scratch
  clips "$V" Camera.mp4 Camera_01.mp4 Camera_02.mp4 Camera_03.mp4
  stage "$T" "$V"
  local ok=0
  assert_status "$STATUS" 0 "a matching set stages" || { printf '%s\n' "$OUT"; ok=1; }
  staged "$T" || { fail "nothing was staged"; ok=1; }
  drop_scratch; return $ok
}

t_the_reported_case() {
  # Camera_01.mp4 replaced by Wrong_Camera.mp4: four files, four cameras, so a
  # count of the clips cannot tell. Both sides of the mismatch are named.
  new_scratch
  clips "$V" Camera.mp4 Camera_02.mp4 Camera_03.mp4 Wrong_Camera.mp4
  stage "$T" "$V"
  local ok=0
  assert_status "$STATUS" 1 "a swapped clip is refused" || ok=1
  assert_contains "$OUT" "Missing video for calibration sensor: Camera_01" "the missing camera is named" || ok=1
  assert_contains "$OUT" "Unexpected video not present in calibration: Wrong_Camera" "the stray clip is named" || ok=1
  assert_contains "$OUT" "nothing was staged" "says nothing was staged" || ok=1
  staged "$T" && { fail "a refused run must not stage a config"; ok=1; }
  [[ $ok -eq 0 ]] || printf '%s\n' "$OUT"
  drop_scratch; return $ok
}

t_a_missing_clip_alone() {
  new_scratch
  clips "$V" Camera.mp4 Camera_01.mp4 Camera_02.mp4
  stage "$T" "$V"
  local ok=0
  assert_status "$STATUS" 1 "a missing clip is refused" || ok=1
  assert_contains "$OUT" "Missing video for calibration sensor: Camera_03" "Camera_03 is named" || ok=1
  assert_not_contains "$OUT" "Unexpected video" "nothing is called unexpected" || ok=1
  [[ $ok -eq 0 ]] || printf '%s\n' "$OUT"
  drop_scratch; return $ok
}

t_an_extra_clip_alone() {
  new_scratch
  clips "$V" Camera.mp4 Camera_01.mp4 Camera_02.mp4 Camera_03.mp4 Spare.mp4
  stage "$T" "$V"
  local ok=0
  assert_status "$STATUS" 1 "an extra clip is refused" || ok=1
  assert_contains "$OUT" "Unexpected video not present in calibration: Spare" "Spare is named" || ok=1
  assert_not_contains "$OUT" "Missing video" "nothing is called missing" || ok=1
  [[ $ok -eq 0 ]] || printf '%s\n' "$OUT"
  drop_scratch; return $ok
}

t_a_case_variant_is_named_once() {
  # DeepStream opens the exact name on a case-sensitive filesystem, so
  # camera_01.mp4 does not serve Camera_01. Say so once, not as two problems.
  new_scratch
  clips "$V" Camera.mp4 camera_01.mp4 Camera_02.mp4 Camera_03.mp4
  stage "$T" "$V"
  local ok=0
  assert_status "$STATUS" 1 "a case variant is refused" || ok=1
  assert_contains "$OUT" "Missing video for calibration sensor: Camera_01 (found camera_01.mp4, which differs only by case)" "the case difference is explained" || ok=1
  assert_not_contains "$OUT" "Unexpected video not present in calibration: camera_01" "and not also reported as unexpected" || ok=1
  [[ $ok -eq 0 ]] || printf '%s\n' "$OUT"
  drop_scratch; return $ok
}

t_two_clips_for_one_sensor_are_ambiguous() {
  new_scratch
  clips "$V" Camera.mp4 Camera_01.mp4 camera_01.MP4 Camera_02.mp4 Camera_03.mp4
  stage "$T" "$V"
  local ok=0
  assert_status "$STATUS" 1 "an ambiguous pair is refused" || ok=1
  assert_contains "$OUT" "Ambiguous videos for one sensor:" "the ambiguity is reported" || ok=1
  assert_contains "$OUT" "Camera_01.mp4" "names the first clip" || ok=1
  assert_contains "$OUT" "camera_01.MP4" "names the second clip" || ok=1
  [[ $ok -eq 0 ]] || printf '%s\n' "$OUT"
  drop_scratch; return $ok
}

t_other_file_types_are_ignored() {
  new_scratch
  clips "$V" Camera.mp4 Camera_01.mp4 Camera_02.mp4 Camera_03.mp4 README.txt .DS_Store notes.mov
  stage "$T" "$V"
  local ok=0
  assert_status "$STATUS" 0 "non-mp4 files do not count" || { printf '%s\n' "$OUT"; ok=1; }
  drop_scratch; return $ok
}

t_skip_video_check_stages_anyway() {
  new_scratch
  clips "$V" Camera.mp4 Camera_02.mp4 Camera_03.mp4 Wrong_Camera.mp4
  stage "$T" "$V" SKIP_VIDEO_CHECK=1
  local ok=0
  assert_status "$STATUS" 0 "SKIP_VIDEO_CHECK=1 bypasses the check" || { printf '%s\n' "$OUT"; ok=1; }
  staged "$T" || { fail "the bypass should still stage"; ok=1; }
  drop_scratch; return $ok
}

t_stream_mode_never_looks_at_clips() {
  new_scratch
  clips "$V" Wrong_Camera.mp4
  OUT="$(cd "$T" && env OSD=0 INPUT_MODE=stream NUM_CAMS=4 VIDEO_DIR="$V" ./scripts/stage-configs.sh 2>&1)"; STATUS=$?
  local ok=0
  assert_status "$STATUS" 0 "stream mode ignores VIDEO_DIR" || { printf '%s\n' "$OUT"; ok=1; }
  drop_scratch; return $ok
}

t_a_missing_directory_warns_and_stages() {
  # Staging before the clips are copied in is a legitimate order, and several
  # tests stage file mode against a placeholder path. Nothing to compare, so warn.
  new_scratch
  stage "$T" "$T/no-such-dir"
  local ok=0
  assert_status "$STATUS" 0 "a missing VIDEO_DIR does not block staging" || { printf '%s\n' "$OUT"; ok=1; }
  assert_contains "$OUT" "is not a directory" "but says the clips could not be checked" || ok=1
  drop_scratch; return $ok
}

t_a_relative_directory_resolves_from_docker() {
  # Compose resolves a relative VIDEO_DIR from docker/, so the check has to as
  # well. If it resolved from the working directory it would find nothing to
  # compare and stage the mismatch with only a warning.
  new_scratch
  clips "$T/docker/clips" Camera.mp4 Camera_02.mp4 Camera_03.mp4 Wrong_Camera.mp4
  stage "$T" "clips"
  local ok=0
  assert_status "$STATUS" 1 "a relative VIDEO_DIR is checked" || { printf '%s\n' "$OUT"; ok=1; }
  assert_contains "$OUT" "Missing video for calibration sensor: Camera_01" "resolved against docker/" || ok=1
  clips "$T/docker/clips" Camera.mp4 Camera_01.mp4 Camera_02.mp4 Camera_03.mp4
  stage "$T" "clips"
  assert_status "$STATUS" 0 "and a matching relative directory stages" || { printf '%s\n' "$OUT"; ok=1; }
  drop_scratch; return $ok
}

run_test "a matching clip set stages"                                    t_matching_clips_stage
run_test "the reported case: Wrong_Camera for Camera_01 is refused"      t_the_reported_case
run_test "a missing clip alone is named"                                 t_a_missing_clip_alone
run_test "an extra clip alone is named"                                  t_an_extra_clip_alone
run_test "a case variant is explained and not double reported"           t_a_case_variant_is_named_once
run_test "two clips for one sensor are ambiguous"                        t_two_clips_for_one_sensor_are_ambiguous
run_test "non-mp4 files are ignored"                                     t_other_file_types_are_ignored
run_test "SKIP_VIDEO_CHECK=1 stages anyway"                              t_skip_video_check_stages_anyway
run_test "stream mode never looks at the clips"                          t_stream_mode_never_looks_at_clips
run_test "a missing directory warns and stages"                          t_a_missing_directory_warns_and_stages
run_test "a relative VIDEO_DIR resolves from docker/"                    t_a_relative_directory_resolves_from_docker
tests_summary "video and sensor matching"
