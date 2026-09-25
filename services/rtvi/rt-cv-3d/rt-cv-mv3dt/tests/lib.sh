# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
#
# Shared helpers for MV3DT shell tests. Sourced by run.sh and by any
# repo-local tests under services/rtvi/rt-cv-3d/rt-cv-mv3dt/tests/.
#
# Provides: stub_start, stub_stop, closed_port, ok/fail/run_test,
#           assert_contains, assert_not_contains, assert_status.

MV3DT_TESTS_DIR="${MV3DT_TESTS_DIR:-/opt/mv3dt-tests}"
STUB="${MV3DT_TESTS_DIR}/stub_ds_api.py"

_TESTS_RUN=0
_TESTS_FAILED=0
_CURRENT_TEST=""

# ---- reporting -------------------------------------------------------------

ok()   { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n     %s\n' "$_CURRENT_TEST" "$1" >&2; _TESTS_FAILED=$((_TESTS_FAILED+1)); }

run_test() {  # $1=name  $2=function
  _CURRENT_TEST="$1"
  _TESTS_RUN=$((_TESTS_RUN+1))
  local before="$_TESTS_FAILED"
  if ! "$2"; then
    # Only synthesize a failure if the body did not already report one, so a
    # single failing assertion counts once.
    if [[ "$_TESTS_FAILED" == "$before" ]]; then
      fail "test function returned non-zero"
    fi
  fi
  if [[ "$_TESTS_FAILED" == "$before" ]]; then ok "$1"; fi
}

tests_summary() {  # returns non-zero if anything failed
  printf '\n%s: %d run, %d failed\n' "${1:-tests}" "$_TESTS_RUN" "$_TESTS_FAILED"
  [[ "$_TESTS_FAILED" -eq 0 ]]
}

# ---- assertions ------------------------------------------------------------

assert_contains() {  # $1=haystack $2=needle [$3=label]
  case "$1" in
    *"$2"*) return 0 ;;
    *) fail "expected ${3:-output} to contain: $2${NL}got: $(printf '%s' "$1" | head -c 400)"; return 1 ;;
  esac
}

assert_not_contains() {  # $1=haystack $2=needle [$3=label]
  case "$1" in
    *"$2"*) fail "expected ${3:-output} NOT to contain: $2${NL}got: $(printf '%s' "$1" | head -c 400)"; return 1 ;;
    *) return 0 ;;
  esac
}

assert_status() {  # $1=actual $2=expected [$3=label]
  if [[ "$1" != "$2" ]]; then
    fail "expected ${3:-exit status} $2, got $1"
    return 1
  fi
}

NL='
'

# ---- stub server lifecycle -------------------------------------------------

STUB_PID=""
STUB_PORT=""

# stub_start [--mode X]... [--seed-stream CAM=URL]...
# Sets STUB_PORT and STUB_PID. Blocks until the port is printed.
stub_start() {
  local out
  out="$(mktemp)"
  python3 "$STUB" "$@" >"$out" 2>&1 &
  STUB_PID="$!"
  for _ in $(seq 1 50); do
    STUB_PORT="$(sed -n 's/^PORT=//p' "$out" | head -1)"
    [[ -n "$STUB_PORT" ]] && break
    kill -0 "$STUB_PID" 2>/dev/null || break
    sleep 0.1
  done
  if [[ -z "$STUB_PORT" ]]; then
    echo "stub failed to start: $(cat "$out")" >&2
    rm -f "$out"
    return 1
  fi
  rm -f "$out"
}

stub_stop() {
  if [[ -n "$STUB_PID" ]] && kill -0 "$STUB_PID" 2>/dev/null; then
    kill "$STUB_PID" 2>/dev/null || true
    wait "$STUB_PID" 2>/dev/null || true
  fi
  STUB_PID=""
  STUB_PORT=""
}

# A port with nothing listening on it — for "API unavailable" cases.
closed_port() {
  python3 -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
port = s.getsockname()[1]
s.close()
print(port)
"
}
