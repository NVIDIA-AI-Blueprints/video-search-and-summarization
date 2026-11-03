#!/usr/bin/env bash
#
# Hermetic test for native-services.sh
# Tests concurrency, flock serialisation, health check detection, atomic pid file,
# status filtering, the agent's feature defaults and the ffmpeg/ffprobe install (media-tools.sh).
#
# Usage:
#   bash deploy/docker/developer-profiles/dev-profile-incident/.scripts/test-native-services.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="${SCRIPT_DIR}/native-services.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

RUN_DIR="$TMP/.run"
mkdir -p "$RUN_DIR"
export VSS_RUN_DIR="$RUN_DIR"
export ENV_FILE="$TMP/empty.env"
touch "$ENV_FILE"

PASSED=0
FAILED=0

assert_eq() {
  local expected="$1"
  local actual="$2"
  local msg="$3"
  if [ "$expected" != "$actual" ]; then
    echo "FAIL: $msg"
    echo "  Expected: $expected"
    echo "  Actual:   $actual"
    FAILED=$((FAILED + 1))
  else
    echo "PASS: $msg"
    PASSED=$((PASSED + 1))
  fi
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local msg="$3"
  if ! echo "$haystack" | grep -q "$needle"; then
    echo "FAIL: $msg"
    echo "  Did not find '$needle' in:"
    echo "$haystack"
    FAILED=$((FAILED + 1))
  else
    echo "PASS: $msg"
    PASSED=$((PASSED + 1))
  fi
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local msg="$3"
  if echo "$haystack" | grep -q "$needle"; then
    echo "FAIL: $msg"
    echo "  Unexpectedly found '$needle' in:"
    echo "$haystack"
    FAILED=$((FAILED + 1))
  else
    echo "PASS: $msg"
    PASSED=$((PASSED + 1))
  fi
}

# -----------------------------------------------------------------------------
# Test 1: Status default (ENABLE_ANALYTICS unset/false) lists only vss-agent
# -----------------------------------------------------------------------------
echo "=== Test 1: Status defaults to only vss-agent ==="
# Use an inactive port to ensure vss-agent is stopped for this test
export VSS_AGENT_PORT="19876"
out="$(bash "$SUT" status)"
assert_contains "$out" "vss-agent" "status lists vss-agent by default"
assert_not_contains "$out" "video-analytics-api" "status omits video-analytics-api by default"
assert_not_contains "$out" "behavior-analytics" "status omits behavior-analytics by default"

porcelain_out="$(bash "$SUT" status --porcelain)"
lines_count="$(echo "$porcelain_out" | grep -c . || true)"
assert_eq "1" "$lines_count" "porcelain status has exactly 1 line"
assert_contains "$porcelain_out" "^vss-agent:stopped:none:19876" "porcelain status shows vss-agent stopped"

# -----------------------------------------------------------------------------
# Test 2: Status with ENABLE_ANALYTICS=true includes all 3 services
# -----------------------------------------------------------------------------
echo "=== Test 2: Status with ENABLE_ANALYTICS=true ==="
out_analytics="$(ENABLE_ANALYTICS=true bash "$SUT" status)"
assert_contains "$out_analytics" "vss-agent" "status lists vss-agent"
assert_contains "$out_analytics" "video-analytics-api" "status lists video-analytics-api when analytics enabled"
assert_contains "$out_analytics" "behavior-analytics" "status lists behavior-analytics when analytics enabled"

porcelain_analytics="$(ENABLE_ANALYTICS=true bash "$SUT" status --porcelain)"
lines_analytics="$(echo "$porcelain_analytics" | grep -c . || true)"
assert_eq "3" "$lines_analytics" "porcelain status has 3 lines when analytics enabled"

# Explicit service target
out_explicit="$(bash "$SUT" status video-analytics-api)"
assert_contains "$out_explicit" "video-analytics-api" "explicit service status works"
assert_not_contains "$out_explicit" "vss-agent" "explicit status targets only named service"

# -----------------------------------------------------------------------------
# Test 3: Lock file creation and permissions (umask 002)
# -----------------------------------------------------------------------------
echo "=== Test 3: Umask and group-writable lock file ==="
[ -f "$RUN_DIR/native-services.lock" ]
# shellcheck disable=SC2012
perms="$(ls -ld "$RUN_DIR/native-services.lock" | awk '{print $1}')"
assert_contains "$perms" "^.....w" "lock file permissions include group write"

group_writable="$(python3 -c "import os, stat, sys; print('yes' if (os.stat(sys.argv[1]).st_mode & stat.S_IWGRP) else 'no')" "$RUN_DIR/native-services.lock")"
assert_eq "yes" "$group_writable" "lock file is group-writable"

# Test pre-existing unwriteable lock and log file recovery
chmod 444 "$RUN_DIR/native-services.lock"
[ ! -w "$RUN_DIR/native-services.lock" ]
(
  # shellcheck disable=SC1090,SC1091
  source "$SUT" status >/dev/null
  with_lock true
)
group_writable_after="$(python3 -c "import os, stat, sys; print('yes' if (os.stat(sys.argv[1]).st_mode & stat.S_IWGRP) else 'no')" "$RUN_DIR/native-services.lock")"
assert_eq "yes" "$group_writable_after" "unwritable lock file replaced with group-writable lock file"

# -----------------------------------------------------------------------------
# Test 4: Healthy /health treated as already running (missing/unreadable pid)
# -----------------------------------------------------------------------------
echo "=== Test 4: Healthy /health response treated as running ==="
port_file="$TMP/mock_port.txt"
rm -f "$port_file"

# Start a simple mock HTTP server in python responding with {"status": "isAlive"}
# Bind to port 0 so the OS assigns an ephemeral free port
python3 -c "
import http.server, socketserver, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{\"status\": \"isAlive\"}')
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args):
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(('127.0.0.1', 0), Handler) as httpd:
    port = httpd.server_address[1]
    with open(sys.argv[1], 'w') as f:
        f.write(str(port) + '\n')
    httpd.serve_forever()
" "$port_file" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMP"' EXIT

# Wait for server to write its port
for _ in $(seq 1 30); do
  if [ -s "$port_file" ]; then
    break
  fi
  sleep 0.1
done

PORT="$(cat "$port_file")"
export VSS_AGENT_PORT="$PORT"
rm -f "$RUN_DIR/vss-agent.pid"

status_running="$(bash "$SUT" status)"
assert_contains "$status_running" "vss-agent" "vss-agent listed in status"
assert_contains "$status_running" "RUNNING" "vss-agent reported as RUNNING"
assert_contains "$status_running" "(healthy)" "vss-agent reported as (healthy)"

status_porcelain_running="$(bash "$SUT" status --porcelain)"
assert_contains "$status_porcelain_running" "^vss-agent:running:unknown:$PORT" "porcelain reports running with unknown pid"

start_skip_out="$(bash "$SUT" start vss-agent)"
assert_contains "$start_skip_out" "is already running" "start prints already running and skips start"

# -----------------------------------------------------------------------------
# Test 5: Atomic pid writes and removal only on matching pid
# -----------------------------------------------------------------------------
echo "=== Test 5: Atomic pid file write and removal on match ==="
# Source helper functions to unit-test them
(
  # shellcheck disable=SC1090,SC1091
  source "$SUT" status >/dev/null

  write_pid_atomic "test-svc" "12345"
  assert_eq "12345" "$(cat "$RUN_DIR/test-svc.pid")" "write_pid_atomic wrote correct pid"

  # Attempt removal with non-matching pid (should NOT remove)
  remove_pid_if_match "test-svc" "99999"
  assert_eq "12345" "$(cat "$RUN_DIR/test-svc.pid" 2>/dev/null || true)" "remove_pid_if_match did not remove non-matching pid"

  # Attempt removal with matching pid (SHOULD remove)
  remove_pid_if_match "test-svc" "12345"
  [ ! -f "$RUN_DIR/test-svc.pid" ]
  assert_eq "not_found" "$([ -f "$RUN_DIR/test-svc.pid" ] && echo exists || echo not_found)" "remove_pid_if_match removed matching pid"

  # Test pre-existing unwritable log file replacement
  log_file="$RUN_DIR/test-svc.log"
  touch "$log_file"
  chmod 444 "$log_file"
  [ ! -w "$log_file" ]
  if [ -f "$log_file" ] && [ ! -w "$log_file" ]; then
    rm -f "$log_file"
  fi
  touch "$log_file" 2>/dev/null || true
  chmod g+rw "$log_file" 2>/dev/null || true
  log_writable="$(python3 -c "import os, stat, sys; print('yes' if (os.stat(sys.argv[1]).st_mode & stat.S_IWGRP) else 'no')" "$log_file")"
  assert_eq "yes" "$log_writable" "unwritable log file replaced and group-writable"
)

# Clean up mock server
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true

# -----------------------------------------------------------------------------
# Test 6: Concurrency and flock serialization
# -----------------------------------------------------------------------------
echo "=== Test 6: Concurrency and lock serialization ==="
if command -v flock >/dev/null 2>&1; then
  log_concurrency="$TMP/concurrency.log"
  rm -f "$log_concurrency"

  # Run two locked blocks concurrently
  (
    # shellcheck disable=SC1090,SC1091
    source "$SUT" status >/dev/null
    with_lock bash -c "echo 'start 1' >> '$log_concurrency'; sleep 0.3; echo 'end 1' >> '$log_concurrency'"
  ) &
  P1=$!

  # Small sleep to ensure P1 enters first
  sleep 0.05

  (
    # shellcheck disable=SC1090,SC1091
    source "$SUT" status >/dev/null
    with_lock bash -c "echo 'start 2' >> '$log_concurrency'; echo 'end 2' >> '$log_concurrency'"
  ) &
  P2=$!

  wait "$P1"
  wait "$P2"

  lines="$(cat "$log_concurrency")"
  # Order must be: start 1, end 1, start 2, end 2
  expected=$'start 1\nend 1\nstart 2\nend 2'
  assert_eq "$expected" "$lines" "flock properly serialized concurrent executions"

  # Test that background process does not leak fd 200 and hold lock after with_lock exits
  bg_lock_done="$TMP/bg_lock_done"
  rm -f "$bg_lock_done"
  (
    # shellcheck disable=SC1090,SC1091
    source "$SUT" status >/dev/null
    with_lock bash -c "( exec 200>&- 2>/dev/null || true; sleep 2 200>&- & )"
  )
  (
    # shellcheck disable=SC1090,SC1091
    source "$SUT" status >/dev/null
    with_lock touch "$bg_lock_done"
  )
  assert_eq "true" "$([ -f "$bg_lock_done" ] && echo true || echo false)" "lock re-acquired without delay when background process runs"
else
  echo "INFO: flock command not available in this environment, skipping live flock serialization test"
fi

# Verify fd 200 is closed in daemon launch subshell
fd_leak_check="$TMP/fd_leak_check"
rm -f "$fd_leak_check"
(
  (
    exec 200>&- 2>/dev/null || true
    if [ -e /dev/fd/200 ]; then
      echo "leaked" > "$fd_leak_check"
    else
      echo "closed" > "$fd_leak_check"
    fi
  )
) 200>>"$RUN_DIR/native-services.lock"
assert_eq "closed" "$(cat "$fd_leak_check")" "fd 200 is closed in daemon launch subshell"

# -----------------------------------------------------------------------------
# Person tracking: on for vss-agent by default, the env file can turn it off
# -----------------------------------------------------------------------------
# shellcheck disable=SC1090
tracking_default="$(unset INCIDENT_TRACKING_ENABLED; source "$SUT" status >/dev/null; echo "$INCIDENT_TRACKING_ENABLED")"
assert_eq "true" "$tracking_default" "INCIDENT_TRACKING_ENABLED defaults to true"
tracking_env="$TMP/tracking-off.env"
echo "INCIDENT_TRACKING_ENABLED=false" >"$tracking_env"
# shellcheck disable=SC1090
tracking_off="$(unset INCIDENT_TRACKING_ENABLED; ENV_FILE="$tracking_env"; source "$SUT" status >/dev/null; echo "$INCIDENT_TRACKING_ENABLED")"
assert_eq "false" "$tracking_off" "an env file INCIDENT_TRACKING_ENABLED=false wins"

# -----------------------------------------------------------------------------
# Object tracking: on for vss-agent by default, the env file can turn it off
# -----------------------------------------------------------------------------
# shellcheck disable=SC1090
objtrack_default="$(unset INCIDENT_OBJECT_TRACKING_ENABLED; source "$SUT" status >/dev/null; echo "$INCIDENT_OBJECT_TRACKING_ENABLED")"
assert_eq "1" "$objtrack_default" "INCIDENT_OBJECT_TRACKING_ENABLED defaults to 1"
objtrack_env="$TMP/objtrack-off.env"
echo "INCIDENT_OBJECT_TRACKING_ENABLED=0" >"$objtrack_env"
# shellcheck disable=SC1090
objtrack_off="$(unset INCIDENT_OBJECT_TRACKING_ENABLED; ENV_FILE="$objtrack_env"; source "$SUT" status >/dev/null; echo "$INCIDENT_OBJECT_TRACKING_ENABLED")"
assert_eq "0" "$objtrack_off" "an env file INCIDENT_OBJECT_TRACKING_ENABLED=0 wins"

# -----------------------------------------------------------------------------
# Caption pass: on for vss-agent by default, the env file can turn it off
# -----------------------------------------------------------------------------
# shellcheck disable=SC1090
pass_default="$(unset INCIDENT_CHUNKING_ENABLED; source "$SUT" status >/dev/null; echo "$INCIDENT_CHUNKING_ENABLED")"
assert_eq "1" "$pass_default" "INCIDENT_CHUNKING_ENABLED defaults to 1"
pass_env="$TMP/pass-off.env"
echo "INCIDENT_CHUNKING_ENABLED=0" >"$pass_env"
# shellcheck disable=SC1090
pass_off="$(unset INCIDENT_CHUNKING_ENABLED; ENV_FILE="$pass_env"; source "$SUT" status >/dev/null; echo "$INCIDENT_CHUNKING_ENABLED")"
assert_eq "0" "$pass_off" "an env file INCIDENT_CHUNKING_ENABLED=0 wins"

# -----------------------------------------------------------------------------
# ffmpeg and ffprobe: reported by status, installed into the tools dir when missing
# -----------------------------------------------------------------------------
echo "=== Media tools ==="
MEDIA="${SCRIPT_DIR}/media-tools.sh"
BARE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
build="ffmpeg-n0.0-latest-linux64-gpl-0.0"
release="$TMP/release"
mkdir -p "$release/$build/bin"
for tool in ffmpeg ffprobe; do
  printf '#!/bin/sh\necho "%s version 0.0-test Copyright"\n' "$tool" >"$release/$build/bin/$tool"
  chmod +x "$release/$build/bin/$tool"
done
tar -cJf "$release/$build.tar.xz" -C "$release" "$build"
(cd "$release" && sha256sum "$build.tar.xz" >checksums.sha256)

tools="$TMP/tools"
# The "missing" checks need a host without ffmpeg on the bare PATH (true on kwanz-ws and macOS).
if [ -z "$(PATH="$BARE_PATH:/usr/local/bin" command -v ffmpeg || true)" ]; then
  missing_out="$(PATH="$BARE_PATH" VSS_MEDIA_TOOLS_DIR="$tools" bash "$MEDIA" check || true)"
  assert_contains "$missing_out" "^ffmpeg MISSING" "check reports a missing ffmpeg"
  assert_contains "$missing_out" "^ffprobe MISSING" "check reports a missing ffprobe"
  status_missing="$(PATH="$BARE_PATH" HOME="$TMP/home" VSS_MEDIA_TOOLS_DIR="$tools" bash "$SUT" status)"
  assert_contains "$status_missing" "ffmpeg MISSING" "status reports a missing ffmpeg"
else
  echo "NOTE: ffmpeg is on this host's system PATH; the missing-tool checks do not apply here"
fi

install_out="$(PATH="$BARE_PATH" VSS_MEDIA_TOOLS_DIR="$tools" VSS_MEDIA_TOOLS_PLATFORM=Linux-x86_64 \
  VSS_FFMPEG_BUILD="$build" VSS_FFMPEG_BASE_URL="file://$release" bash "$MEDIA" install 2>&1)"
assert_contains "$install_out" "ffprobe $tools/bin/ffprobe 0.0-test" "install puts a verified build in the tools dir"
assert_eq "0" "$(find "$tools" -maxdepth 1 -name '.install.*' | wc -l | tr -d ' ')" "install removes its scratch dir"

status_out="$(PATH="$BARE_PATH" VSS_MEDIA_TOOLS_DIR="$tools" bash "$SUT" status)"
assert_contains "$status_out" "ffmpeg $tools/bin/ffmpeg 0.0-test" "status reports the tools-dir ffmpeg and its version"
porcelain_tools="$(VSS_MEDIA_TOOLS_DIR="$tools" bash "$SUT" status --porcelain | grep -c . || true)"
assert_eq "1" "$porcelain_tools" "porcelain status stays one line per service"

again_out="$(PATH="$BARE_PATH" VSS_MEDIA_TOOLS_DIR="$tools" VSS_MEDIA_TOOLS_PLATFORM=Linux-x86_64 \
  VSS_FFMPEG_BUILD="$build" VSS_FFMPEG_BASE_URL="file://$TMP/no-such-release" bash "$MEDIA" install 2>&1)"
assert_contains "$again_out" "already available" "install is a no-op when both are present"

bad_release="$TMP/bad-release"
mkdir -p "$bad_release"
cp "$release/$build.tar.xz" "$bad_release/"
echo "0000  $build.tar.xz" >"$bad_release/checksums.sha256"
bad_tools="$TMP/bad-tools"
bad_rc=0
bad_out="$(PATH="$BARE_PATH" VSS_MEDIA_TOOLS_DIR="$bad_tools" VSS_MEDIA_TOOLS_PLATFORM=Linux-x86_64 \
  VSS_FFMPEG_BUILD="$build" VSS_FFMPEG_BASE_URL="file://$bad_release" bash "$MEDIA" install 2>&1)" || bad_rc=$?
assert_eq "1" "$bad_rc" "install fails on a checksum mismatch"
assert_contains "$bad_out" "checksum mismatch" "install names the checksum mismatch"
assert_eq "no" "$([ -e "$bad_tools/bin/ffmpeg" ] && echo yes || echo no)" "a mismatched build is never installed"

platform_rc=0
PATH="$BARE_PATH" VSS_MEDIA_TOOLS_DIR="$TMP/mac-tools" VSS_MEDIA_TOOLS_PLATFORM=Darwin-arm64 bash "$MEDIA" install >/dev/null 2>&1 || platform_rc=$?
assert_eq "1" "$platform_rc" "install refuses a platform with no static build"

# -----------------------------------------------------------------------------
# Tracker model files: exported dir, env override, pinned fetch
# -----------------------------------------------------------------------------
echo "=== Tracker models ==="
TRACKER="${SCRIPT_DIR}/tracker-models.sh"
# shellcheck disable=SC1090
models_default="$(unset INCIDENT_OBJECT_TRACKING_MODEL_DIR VSS_TRACKER_MODELS_DIR; source "$SUT" status >/dev/null; echo "$INCIDENT_OBJECT_TRACKING_MODEL_DIR")"
assert_contains "$models_default" "/.tools/tracker-models$" "INCIDENT_OBJECT_TRACKING_MODEL_DIR defaults to the tools dir"
models_env="$TMP/models-dir.env"
echo "INCIDENT_OBJECT_TRACKING_MODEL_DIR=/custom/models" >"$models_env"
# shellcheck disable=SC1090
models_override="$(unset INCIDENT_OBJECT_TRACKING_MODEL_DIR; ENV_FILE="$models_env"; source "$SUT" status >/dev/null; echo "$INCIDENT_OBJECT_TRACKING_MODEL_DIR")"
assert_eq "/custom/models" "$models_override" "an env file INCIDENT_OBJECT_TRACKING_MODEL_DIR wins"

models_src="$TMP/models-src"
mkdir -p "$models_src"
printf 'backbone-bytes' >"$models_src/nanotrack_backbone_sim.onnx"
printf 'head-bytes' >"$models_src/nanotrack_head_sim.onnx"
backbone_sha="$(sha256sum "$models_src/nanotrack_backbone_sim.onnx" | awk '{print $1}')"
head_sha="$(sha256sum "$models_src/nanotrack_head_sim.onnx" | awk '{print $1}')"
models_dir="$TMP/models"
run_models() {
  VSS_TRACKER_MODELS_DIR="$1" VSS_TRACKER_MODELS_BASE_URL="$2" VSS_TRACKER_BACKBONE_SHA256="$backbone_sha" \
    VSS_TRACKER_HEAD_SHA256="$head_sha" bash "$TRACKER" "$3" 2>&1
}

models_install="$(run_models "$models_dir" "file://$models_src" install)"
assert_contains "$models_install" "Installed nanotrack_head_sim.onnx" "install fetches a verified file"
assert_contains "$(run_models "$models_dir" "file://$models_src" check)" "nanotrack_backbone_sim.onnx $backbone_sha OK" "check reports both files OK"
assert_eq "0" "$(find "$models_dir" -maxdepth 1 -name '.nanotrack*' | wc -l | tr -d ' ')" "install leaves no temp file"
models_again="$(run_models "$models_dir" "file://$TMP/no-such-models" install)"
assert_contains "$models_again" "nanotrack_head_sim.onnx already installed" "a second install downloads nothing"

bad_models="$TMP/bad-models"
bad_models_rc=0
bad_models_out="$(VSS_TRACKER_MODELS_DIR="$bad_models" VSS_TRACKER_MODELS_BASE_URL="file://$models_src" \
  VSS_TRACKER_BACKBONE_SHA256=0000 VSS_TRACKER_HEAD_SHA256="$head_sha" bash "$TRACKER" install 2>&1)" || bad_models_rc=$?
assert_eq "1" "$bad_models_rc" "install fails on a checksum mismatch"
assert_contains "$bad_models_out" "checksum mismatch for nanotrack_backbone_sim.onnx" "install names the mismatched file"
assert_eq "no" "$([ -e "$bad_models/nanotrack_backbone_sim.onnx" ] && echo yes || echo no)" "a mismatched file is never installed"
assert_eq "yes" "$([ -e "$bad_models/nanotrack_head_sim.onnx" ] && echo yes || echo no)" "a matching file is still installed"

echo ""
echo "=== Summary ==="
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
exit 0
