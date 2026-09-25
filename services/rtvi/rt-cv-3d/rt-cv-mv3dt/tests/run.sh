#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Run every regression test for the MV3DT standalone scripts.
#
#   bash services/rtvi/rt-cv-3d/rt-cv-mv3dt/tests/run.sh
#
# No arguments and no environment needed: paths resolve from this file. Each test
# is also runnable on its own. These stub the perception REST API, so they check
# the scripts' contract, not pipeline behaviour; a live deployment is still the
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
# state makes the suite hermetic; a test needing staged config creates it.
MV3DT_SRC="$(cd "$HERE/.." && pwd)"
SCRATCH="${TMPDIR:-/tmp}/mv3dt-tests-scratch"
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
cp -r "$MV3DT_SRC/." "$SCRATCH/"
rm -rf "$SCRATCH/generated" "$SCRATCH/video-output" "$SCRATCH/bev-output" "$SCRATCH/utils/venv"
export MV3DT_SCRATCH="$SCRATCH"
export ADD_STREAMS="$SCRATCH/scripts/add-streams.sh"
trap 'rm -rf "$SCRATCH"' EXIT

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
