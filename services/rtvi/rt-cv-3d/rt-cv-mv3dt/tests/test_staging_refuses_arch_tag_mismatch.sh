#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Regression test for the MV3DT standalone scripts. Self-contained: paths
# default to this checkout, env vars still override.
# Run:  REPO=<vss repo root> bash "$0"
#
# Bug 6575042. The aarch64 perception build ships under a separate -sbsa tag. The
# default tag's arm64 variant contains no NVIDIA decoder at all, so nvv4l2decoder
# cannot be created, extract-sei-type5-data is unavailable, and nvstreammux drops
# every buffer: sources register, ds-ready says YES, and no frame ever arrives.
# Staging must refuse the mismatch and name it, rather than let the operator find
# out from a GStreamer element failure at runtime.
#
# uname is stubbed because the aarch64 path cannot be exercised on an x86 runner.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../.." && pwd)"
MV3DT="$REPO_ROOT/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
: "${MV3DT_TESTS_DIR:=$HERE}"
: "${REPO:=$REPO_ROOT}"

pass=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1));
        else echo "  FAIL $1 (want '$3', got '$2')"; fail=$((fail+1)); fi; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/services/rtvi/rt-cv-3d"
cp -r "$MV3DT" "$T/services/rtvi/rt-cv-3d/"
C="$T/services/rtvi/rt-cv-3d/rt-cv-mv3dt"

# Pretend to be whichever machine the case needs. Anything but -m passes through.
mkdir -p "$T/bin"
cat > "$T/bin/uname" <<'STUB'
#!/bin/sh
[ "$1" = "-m" ] && { echo "${FAKE_ARCH:-x86_64}"; exit 0; }
exec /usr/bin/uname "$@"
STUB
chmod +x "$T/bin/uname"

stage() {  # $1=FAKE_ARCH  $2=PERCEPTION_TAG  [$3=extra env assignment]
  # env goes first so every setting is one of its arguments. Written as a bare
  # assignment prefix, an empty ${3:+...} ends the prefix at parse time and the
  # next word is run as a command.
  ( cd "$C" && env PATH="$T/bin:$PATH" FAKE_ARCH="$1" PERCEPTION_TAG="$2" \
      OSD=0 INPUT_MODE=stream ${3:+"$3"} ./scripts/stage-configs.sh ) >"$T/out" 2>&1
  echo $?
}
said() { grep -qF "$1" "$T/out" && echo yes || echo no; }

# The reported case: aarch64 host left on the default tag.
chk "aarch64 with the default tag is refused"        "$(stage aarch64 develop-latest)" "1"
chk "  the architecture is named"                    "$(said 'aarch64 needs the -sbsa tag')" "yes"
chk "  the bug's symptom is explained"               "$(said 'no NVIDIA decoder')" "yes"
chk "  the exact fix is given"                       "$(said 'PERCEPTION_TAG="develop-latest-sbsa"')" "yes"
chk "  nothing was staged"                           "$(said 'nothing was staged')" "yes"

# The mirror image: x86 host pointed at the aarch64 build.
chk "x86_64 with the sbsa tag is refused"            "$(stage x86_64 develop-latest-sbsa)" "1"
chk "  the correct tag is suggested"                 "$(said 'PERCEPTION_TAG="develop-latest"')" "yes"

# Matching pairs must stage silently.
chk "aarch64 with the sbsa tag stages"               "$(stage aarch64 develop-latest-sbsa)" "0"
chk "x86_64 with the default tag stages"             "$(stage x86_64 develop-latest)" "0"

# Escape hatches: a deliberate override and a non-stock image.
chk "SKIP_ARCH_CHECK=1 stages anyway"                "$(stage aarch64 develop-latest SKIP_ARCH_CHECK=1)" "0"
chk "a custom image is not second-guessed"           "$(stage aarch64 some-tag PERCEPTION_IMAGE=my.registry/perception)" "0"

# An unrecognised machine must not block staging.
chk "an unknown architecture is left alone"          "$(stage riscv64 develop-latest)" "0"

echo "  6575042 arch/tag guard: $pass passed, $fail failed"
[ "$fail" = 0 ]
