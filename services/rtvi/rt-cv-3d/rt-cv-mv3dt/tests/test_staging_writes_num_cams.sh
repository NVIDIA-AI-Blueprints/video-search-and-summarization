#!/usr/bin/env bash
# Regression test for the MV3DT standalone scripts. Self-contained: paths
# default to this checkout, env vars still override.
# Run:  REPO=<vss repo root> bash "$0"
#
# generate-configs.sh used to print "set NUM_CAMS=N in docker/.env" and leave it
# to the user. That hand copy is where NUM_CAMS drifts from the generated camera
# set, and compose passes the stale value to bev-fusion as MAX_EXPECTED_SENSORS.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"
: "${ADD_STREAMS:=$MV3DT/scripts/add-streams.sh}"
: "${STAGE_CONFIGS:=$MV3DT/scripts/stage-configs.sh}"
: "${REPO}"
CAL="$REPO/services/rtvi/rt-cv/configs/warehouse-3d/calibration.json"
pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1 (want '$3', got '$2')"; fail=$((fail+1)); fi; }

T=$(mktemp -d)
mkdir -p "$T/services/rtvi/rt-cv-3d"
cp -r "$REPO/services/rtvi/rt-cv-3d/rt-cv-mv3dt" "$T/services/rtvi/rt-cv-3d/"
cp -r "$REPO/tools" "$T/" 2>/dev/null
C="$T/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
rm -rf "$C/generated"
gen() { ( cd "$C" && timeout 300 ./scripts/generate-configs.sh "$CAL" ) >/dev/null 2>&1; }
val() { grep -m1 -oP '(?<=^NUM_CAMS=)[0-9]+' "$C/docker/.env"; }

sed -i 's|^NUM_CAMS=.*|NUM_CAMS=99|' "$C/docker/.env"; gen
chk "a wrong NUM_CAMS is corrected"            "$(val)" "4"

gen
chk "re-running leaves it correct"             "$(val)" "4"

sed -i '/^NUM_CAMS=/d' "$C/docker/.env"; gen
chk "a missing NUM_CAMS is added"              "$(val)" "4"
chk "exactly one NUM_CAMS line"                "$(grep -c '^NUM_CAMS=' "$C/docker/.env")" "1"

sed -i 's|^NUM_CAMS=.*|NUM_CAMS=99  # keep me|' "$C/docker/.env"; gen
chk "a trailing comment is preserved"          "$(grep -m1 '^NUM_CAMS=' "$C/docker/.env")" "NUM_CAMS=4  # keep me"

# and the value it writes must satisfy the staging check
( cd "$C" && OSD=0 INPUT_MODE=stream ./scripts/stage-configs.sh ) >/dev/null 2>&1
chk "the written value passes stage-configs"   "$?" "0"

rm -rf "$T"
echo "  6564660 NUM_CAMS: $pass passed, $fail failed"
[ "$fail" = 0 ]
