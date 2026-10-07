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
# Run every regression test for the MV3DT standalone scripts.
#
#   bash services/rtvi/rt-cv-3d/rt-cv-mv3dt/tests/run.sh
#
# No arguments and no environment needed: paths resolve from this file. Each test
# is also runnable on its own. These stub the perception REST API, so they check
# the scripts' contract, not pipeline behaviour. A live deployment is still the
# only way to verify FPS and activation for real.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MV3DT_TESTS_DIR="$HERE"

# Tests run against a scratch copy, not this tree.
#
# add-streams.sh resolves its config directory relative to its own location
# ($ROOT/generated). That directory is gitignored, so it is absent from a fresh
# clone but present in an operator checkout with a real deployment staged.
# Running in place therefore gives different results in the two environments:
# a staged extract-sei-sim-time=1 makes the SEI prerequisite fire on fixture
# camera names that a fresh clone accepts. Copying the component minus its run
# state makes the suite hermetic. A test needing staged config creates it.
MV3DT_SRC="$(cd "$HERE/.." && pwd)"
# mktemp, not a fixed path: two suites running at once would otherwise delete
# each other's scratch mid-run, and a fixed name could belong to something else.
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/mv3dt-tests-XXXXXX")"
cp -r "$MV3DT_SRC/." "$SCRATCH/"
rm -rf "$SCRATCH/generated" "$SCRATCH/video-output" "$SCRATCH/bev-output" "$SCRATCH/utils/venv"
export MV3DT_SCRATCH="$SCRATCH"
export ADD_STREAMS="$SCRATCH/scripts/add-streams.sh"
trap 'rm -rf "$SCRATCH"' EXIT

# Every test states the bug it defends, or says explicitly that there is none.
# Without it the behaviour-to-bug map can only be rebuilt by reading assertions,
# which is how three tests ended up citing their bug in three different ways.
untraced=()
for t in "$HERE"/test_*.sh; do
  grep -qE '^# (Bug 6[0-9]{6}\.|No bug( filed)?\.)' "$t" || untraced+=("$(basename "$t")")
done
if (( ${#untraced[@]} )); then
  echo "  untraceable tests, add '# Bug <id>.' or '# No bug.' to each:"
  printf '    %s\n' "${untraced[@]}"
  exit 1
fi

pass=0 fail=0 failed=()
for t in "$HERE"/test_*.sh; do
  name="$(basename "$t")"
  printf '  %-58s ' "$name"
  if out="$(timeout 300 bash "$t" 2>&1)"; then
    echo "PASS"; pass=$((pass + 1))
  else
    echo "FAIL"; fail=$((fail + 1)); failed+=("$name")
    printf '%s\n' "$out" | sed 's/^/      /'
  fi
done

echo
echo "  ${pass} passed, ${fail} failed"
(( fail == 0 )) || { printf '  failed: %s\n' "${failed[*]}"; exit 1; }
