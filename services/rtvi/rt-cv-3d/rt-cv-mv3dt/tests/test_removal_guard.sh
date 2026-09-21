#!/bin/bash
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
# Unit tests for the removal safety guard in scripts/add-streams.sh.
# Sources the two guard functions with a stubbed `docker` on PATH, so no
# deployment is needed. Run: tests/test_removal_guard.sh

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../scripts/add-streams.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail=0
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s (%s)\n' "$1" "$2"; fail=1; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "want $3, got $2"; }

# The guard is embedded in a script that runs on source, so take just the two
# functions. If either rename breaks this, the test fails loudly rather than
# silently passing.
awk '/^required_cameras\(\) \{/,/^\}/'        "$SRC" >  "$TMP/funcs.sh"
awk '/^pipeline_activated_once\(\) \{/,/^\}/' "$SRC" >> "$TMP/funcs.sh"
grep -q '^required_cameras()'        "$TMP/funcs.sh" || { echo "required_cameras not found in $SRC"; exit 1; }
grep -q '^pipeline_activated_once()' "$TMP/funcs.sh" || { echo "pipeline_activated_once not found in $SRC"; exit 1; }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/docker" <<'STUB'
#!/bin/bash
case "$1" in
  inspect) [ "${STUB_INSPECT:-ok}" = ok ] && exit 0 || exit 1 ;;
  logs)
    case "${STUB_LOGS:-nomatch}" in
      match)   echo "Active sources : ${STUB_COUNT:-4}"; exit 0 ;;
      nomatch) echo "starting up"; exit 0 ;;
      fail)    echo "logging driver does not support reading" >&2; exit 1 ;;
    esac ;;
esac
STUB
chmod +x "$TMP/bin/docker"
PATH="$TMP/bin:$PATH"
ROOT="$TMP/root"; mkdir -p "$ROOT/generated/camInfo"
PERCEPTION_CONTAINER="stub-perception"   # set by the script outside these functions
# shellcheck disable=SC1090
source "$TMP/funcs.sh"

echo "pipeline_activated_once:"
STUB_LOGS=match   NUM_CAMS=4 pipeline_activated_once; is "full count in log -> activated"      "$?" 0
STUB_LOGS=nomatch NUM_CAMS=4 pipeline_activated_once; is "count absent -> not activated"       "$?" 1
STUB_LOGS=fail    NUM_CAMS=4 pipeline_activated_once; is "unreadable logs -> cannot tell"      "$?" 2
STUB_INSPECT=bad  NUM_CAMS=4 pipeline_activated_once; is "no such container -> cannot tell"    "$?" 2

echo "required_cameras:"
touch "$ROOT/generated/camInfo/"{a,b,c,d}.yml "$ROOT/generated/camInfo/stray.yaml"
is "staged beats a lower NUM_CAMS" "$(NUM_CAMS=2 required_cameras)" 4
is "stray .yaml is not counted"    "$(unset NUM_CAMS; required_cameras)" 4
is "higher NUM_CAMS wins"          "$(NUM_CAMS=8 required_cameras)" 8
rm -f "$ROOT/generated/camInfo/"*
(unset NUM_CAMS; required_cameras >/dev/null 2>&1); is "nothing to go on -> error" "$?" 1

exit "$fail"
