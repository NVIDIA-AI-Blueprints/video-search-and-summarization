#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# RT-DETR + MV3DT pipeline start script for single-container deployment.
#
# Generated files:
#   /tmp/generated/pub_sub_info_config.yml

echo "##### RT-DETR + MV3DT pipeline #####"

ARCH="$(uname -m)"
# libgomp/libGLdispatch must load first to reserve static TLS; keep any
# preloads supplied by the image or operator after them.
MV3DT_PRELOAD="/usr/lib/${ARCH}-linux-gnu/libgomp.so.1:/usr/lib/${ARCH}-linux-gnu/libGLdispatch.so.0"
export LD_PRELOAD="${MV3DT_PRELOAD}${LD_PRELOAD:+:${LD_PRELOAD}}"

# ── Display preflight ─────────────────────────────────────────────────────────
# OSD renders through an EGL sink needing an X display connection. Without one
# the app dies at "Failed to set pipeline to PAUSED" with nothing pointing at
# the display (bug 6636932). This names the cause before launch, and fixes
# DISPLAY when the choice is unambiguous, so it need not be exported.
#
# On Tegra the tracker maps buffers via NvBufSurfaceMapEglImage and so needs an
# EGL connection even with OSD off; there the checks run advisory only.
osd_preflight() {
  local cfg="$1" enabled advisory=0 sockets n disp probe

  [ -f "${cfg}" ] || return 0
  enabled="$(awk '/^[[:space:]]*\[/ { s = ($0 ~ /^[[:space:]]*\[sink0\]/) }
                  s && /^[[:space:]]*enable[[:space:]]*=/ { sub(/.*=[[:space:]]*/, ""); print $1; exit }' "${cfg}")"

  if [ "${enabled}" = 1 ]; then
    echo "── OSD preflight (sink0 enabled)"
  elif [ "$(uname -m)" = aarch64 ] && [ -d /usr/lib/aarch64-linux-gnu/tegra ]; then
    advisory=1
    echo "── display preflight (OSD off; Tegra needs EGL for buffer sharing, advisory)"
  else
    echo "** INFO: OSD disabled (sink0 enable=${enabled:-0}); skipping display preflight"
    return 0
  fi
  echo "   uid=$(id -u) gid=$(id -g) groups=$(id -G | tr ' ' ,)"

  # Blocking condition. Advisory mode reports but never stops the pipeline.
  bail() {
    [ "${advisory}" = 1 ] && { echo "   ⚠ $1"; return 0; }
    { echo "** ERROR: $1"; shift; printf '          %s\n' "$@"; } >&2
    return 1
  }

  [ -d /tmp/.X11-unix ] || { bail "/tmp/.X11-unix is not mounted, so no X server is reachable" \
      "add to the perception service:  volumes: [ /tmp/.X11-unix:/tmp/.X11-unix ]"; return $?; }

  sockets="$(ls /tmp/.X11-unix 2>/dev/null | grep -E '^X[0-9]+$' | sort)"
  [ -n "${sockets}" ] || { bail "no X server sockets in /tmp/.X11-unix" \
      "start an X session on the host"; return $?; }
  n="$(printf '%s\n' "${sockets}" | wc -l)"
  echo "   X sockets: $(printf '%s' "${sockets}" | tr '\n' ' ')"

  # DISPLAY must name one of them. Correct it when there is only one candidate:
  # the compose default of :0 is simply wrong on a host whose session is :1.
  disp="${DISPLAY#*:}"; disp="${disp%%.*}"
  if [ -n "${DISPLAY}" ] && [ -S "/tmp/.X11-unix/X${disp}" ]; then
    echo "   DISPLAY=${DISPLAY}"
  elif [ "${n}" = 1 ]; then
    echo "   DISPLAY '${DISPLAY:-unset}' does not exist here; using :${sockets#X}"
    export DISPLAY=":${sockets#X}"
  else
    bail "DISPLAY='${DISPLAY}' matches no X socket and several exist" \
         "set DISPLAY in docker/.env to one of: $(printf '%s' "${sockets}" | sed 's/^X/:/' | tr '\n' ' ')"
    return $?
  fi

  # Complete an X handshake, presenting the cookie as the real client does.
  # Probing without it would report "refused" wherever access control is on,
  # even with a valid XAUTHORITY mounted.
  probe="$(DISPLAY="${DISPLAY}" XAUTHORITY="${XAUTHORITY:-${HOME:-/root}/.Xauthority}" python3 - <<'PY' 2>/dev/null
import os, socket, struct, sys
num = os.environ.get("DISPLAY", "").split(":", 1)[-1].split(".")[0]
name = data = b""; src = "none"
auth = os.environ.get("XAUTHORITY", "")
if auth and os.path.exists(auth):
    try:
        b = open(auth, "rb").read(); i = 0
        while i + 2 <= len(b):
            i += 2                                     # family
            f = []
            for _ in range(4):                         # addr, display, name, data
                ln = struct.unpack(">H", b[i:i+2])[0]; i += 2
                f.append(b[i:i+ln]); i += ln
            if f[2] == b"MIT-MAGIC-COOKIE-1" and f[1].decode("latin-1") in (num, ""):
                name, data, src = f[2], f[3], "cookie"; break
    except Exception:
        src = "unreadable"
try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(5)
    s.connect("/tmp/.X11-unix/X%s" % num)
except OSError as e:
    print("NOSERVER %s" % e); sys.exit(0)
pad = lambda k: (4 - k % 4) % 4
s.sendall(struct.pack("<BBHHHH2x", 0x6C, 0, 11, 0, len(name), len(data))
          + name + b"\0" * pad(len(name)) + data + b"\0" * pad(len(data)))
h = s.recv(8)
if len(h) < 8:      print("SHORT")
elif h[0] == 1:     print("OK %s" % src)
else:               print("REFUSED[%s] %s" % (src, s.recv(1024)[:h[1]].decode("latin-1", "replace").strip()))
PY
)"
  case "${probe}" in
    OK*)       echo "   X connection: OK (auth: ${probe#OK })" ;;
    NOSERVER*) bail "nothing is listening on ${DISPLAY}: ${probe#NOSERVER }"; return $? ;;
    REFUSED*)  bail "the X server on ${DISPLAY} refused this client: ${probe#REFUSED?*? }" \
                    "this is X access control, not a device or driver problem" \
                    "on the host, grant this container's uid:  xhost +SI:localuser:#$(id -u)" \
                    "or open it to all local clients:  xhost +local:" \
                    "or mount the display cookie and set XAUTHORITY (see README)"
               return $? ;;
    *)         echo "   X connection: unverified (${probe:-no result})" ;;
  esac

  [ -d /dev/dri ] && echo "   /dev/dri: $(find /dev/dri -maxdepth 1 -type c -readable -writable 2>/dev/null | wc -l) node(s) usable" \
                  || echo "   /dev/dri: not mapped"
  echo "   NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-unset}"
  echo "   confinement: seccomp=$(awk '/^Seccomp:/{print $2}' /proc/self/status 2>/dev/null)" \
       "apparmor=$(tr -d '\0' </proc/self/attr/current 2>/dev/null)"

  # Exercise the sink rather than guess: a one-buffer pipeline through the same
  # EGL sink either returns in about a second or hangs exactly as the real
  # pipeline would. Skipped in advisory mode, where no sink is in use.
  if [ "${advisory}" = 1 ]; then
    echo "   display checks passed (advisory)"
  elif ! command -v gst-launch-1.0 >/dev/null 2>&1; then
    echo "   EGL sink probe: skipped (gst-launch-1.0 not available)"
  elif timeout 20 gst-launch-1.0 -q videotestsrc num-buffers=1 ! nveglglessink >/tmp/egl-probe.log 2>&1; then
    echo "   EGL sink probe: OK"
    echo "   OSD preflight passed"
  else
    # sink0 is the EGL sink this probe exercises, so a failure here is the
    # pipeline's failure: stop rather than stall at PAUSED.
    { echo "** ERROR: the EGL sink could not render a test frame, so OSD will not work."
      grep -iE 'drm|dri2|EGL' /tmp/egl-probe.log 2>/dev/null | tail -3 | sed 's/^/          /'
      # Same failure, opposite remedies, so pick by what the probe logged:
      # hybrid graphics render through a DRM device, multi-GPU hosts through an
      # NVIDIA GPU the container was not given.
      if grep -qiE 'mesa|dri2|drm' /tmp/egl-probe.log 2>/dev/null; then
        echo "          The display is rendered through Mesa, so it needs the DRM device."
        echo "          Uncomment the devices block in docker/compose.yml."
      else
        echo "          The GPUs given to this container do not include the one driving"
        echo "          ${DISPLAY}. Restage if docker/.env changed since, and if staging"
        echo "          reported it could not tell, add that GPU to GPU_DEVICE by hand."
        echo "          This is GPU selection, not permissions: privileged and group_add"
        echo "          do not help."
      fi; } >&2
    return 1
  fi
  return 0
}

MQTT_HOST=${MQTT_HOST:-localhost}
MQTT_PORT=${MQTT_PORT:-1883}
MQTT_ENDPOINT="${MQTT_HOST}:${MQTT_PORT}"
REID_ENABLED=${REID_ENABLED:-0}
REID_SERVICE_HOST=${REID_SERVICE_HOST:-127.0.0.1}
REID_SERVICE_PORT=${REID_SERVICE_PORT:-8088}
REID_RESET_BEFORE_RUN=${REID_RESET_BEFORE_RUN:-1}
REID_READY_TIMEOUT_SEC=${REID_READY_TIMEOUT_SEC:-300}
REID_DIMENSION=${REID_DIMENSION:-1280}
REID_EXTRACTION_INTERVAL=${REID_EXTRACTION_INTERVAL:-8}
REID_INPUT_TOPIC=${REID_INPUT_TOPIC:-${RAW_TOPIC:-mdx-raw}}

# MQTT is rewritten at every start, so docker/.env alone is enough. Kafka is
# baked into the staged config, so an edit without a restage leaves the old
# endpoint and the app retries a broker that is not there. Compare and report.
kafka_endpoint_matches_env() {  # $1=staged main config
  local cfg="$1" staged want_host want_port
  [ -n "${KAFKA_BOOTSTRAP:-}" ] || return 0          # nothing to compare against
  [ -f "$cfg" ] || return 0
  staged="$(awk '/^[[:space:]]*\[/ { s = ($0 ~ /^[[:space:]]*\[sink1\]/) }
                 s && /^[[:space:]]*msg-broker-conn-str[[:space:]]*=/ {
                   sub(/.*=[[:space:]]*/, ""); print $1; exit }' "$cfg")"
  [ -n "$staged" ] || return 0
  want_host="${KAFKA_BOOTSTRAP%%:*}"
  want_port="${KAFKA_BOOTSTRAP##*:}"
  [ "$staged" = "${want_host};${want_port};${RAW_TOPIC:-mdx-raw}" ] && return 0

  { echo "** ERROR: the staged Kafka endpoint does not match docker/.env."
    echo "          staged  [sink1] msg-broker-conn-str = ${staged}"
    echo "          .env    KAFKA_BOOTSTRAP=${KAFKA_BOOTSTRAP} RAW_TOPIC=${RAW_TOPIC:-mdx-raw}"
    echo "          Unlike MQTT, the Kafka endpoint is written into the staged config, so"
    echo "          editing docker/.env is not enough. Restage, then bring the stack up:"
    echo "            ./scripts/stage-configs.sh"
    echo "          Starting now would retry a broker that is not there and then abort."; } >&2
  return 1
}
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app
APP_DIR="$(pwd)"
CONFIG_DIR="${APP_DIR}/configs"

if ! kafka_endpoint_matches_env "${CONFIG_DIR}/ds-main-config-mv3dt.txt"; then
  exit 1
fi

# A refused restage leaves the previous staged config in place, so the stack can
# still come up against it. Compare what was staged with what is mounted.
batch_matches_caminfo() {  # $1=staged main config
  local cfg="$1" staged mounted
  [ -f "$cfg" ] || return 0
  [ -d /tmp/camInfo ] || return 0
  mounted="$(find /tmp/camInfo -maxdepth 1 -name '*.yml' 2>/dev/null | wc -l)"
  [ "$mounted" -gt 0 ] || return 0
  staged="$(awk '/^[[:space:]]*\[/ { s = ($0 ~ /^[[:space:]]*\[streammux\]/) }
                 s && /^[[:space:]]*batch-size[[:space:]]*=/ {
                   sub(/.*=[[:space:]]*/, ""); print $1; exit }' "$cfg")"
  [ -n "$staged" ] || return 0
  [ "$staged" = "$mounted" ] && return 0

  { echo "** ERROR: the staged config and the mounted camera set disagree."
    echo "          staged  [streammux] batch-size = ${staged}"
    echo "          mounted generated/camInfo      = ${mounted} camera(s)"
    echo "          This is a stale staging: scripts/stage-configs.sh refused the last"
    echo "          run and left the previous config in place. Restage for this camera"
    echo "          set, with NUM_CAMS matching it:"
    echo "            ./scripts/stage-configs.sh"; } >&2
  return 1
}

if ! batch_matches_caminfo "${CONFIG_DIR}/ds-main-config-mv3dt.txt"; then
  exit 1
fi

# Staging owns the tracker-side ReID switches. Refuse a stale config rather than
# silently run the requested experiment with the opposite behavior.
tracker_yaml_scalar() {  # $1=file $2=top-level section $3=key
  awk -v section="$2" -v key="$3" '
    $0 == section ":" { in_section=1; next }
    in_section && /^[^[:space:]#]/ { in_section=0 }
    in_section && $0 ~ "^[[:space:]]+" key ":[[:space:]]*" {
      sub("^[[:space:]]+" key ":[[:space:]]*", "")
      sub(/[[:space:]]+#.*$/, "")
      gsub(/^"|"$/, "")
      print
      exit
    }
  ' "$1"
}

tracker_reid_matches_env() {
  local cfg="$1" reid_type service_type extraction_interval feature_size output_tensor service_host service_port
  [ -f "$cfg" ] || { echo "** ERROR: staged tracker config is missing: $cfg" >&2; return 1; }
  reid_type="$(tracker_yaml_scalar "$cfg" ReID reidType)"
  service_type="$(tracker_yaml_scalar "$cfg" ReIDService reidServiceType)"

  if [ "$REID_ENABLED" = 0 ]; then
    { [ -z "$reid_type" ] || [ "$reid_type" = 0 ]; } && \
      { [ -z "$service_type" ] || [ "$service_type" = 0 ]; } && return 0
    { echo "** ERROR: REID_ENABLED=0, but the staged tracker config still enables ReID."
      echo "          staged ReID.reidType=${reid_type:-missing} ReIDService.reidServiceType=${service_type:-missing}"
      echo "          Run ./scripts/stage-configs.sh after changing docker/.env."; } >&2
    return 1
  fi

  if [ "$REID_ENABLED" != 1 ]; then
    echo "** ERROR: REID_ENABLED must be 0 or 1 (got '$REID_ENABLED')" >&2
    return 1
  fi
  if [ "$REID_INPUT_TOPIC" != "${RAW_TOPIC:-mdx-raw}" ]; then
    { echo "** ERROR: ReID is consuming '$REID_INPUT_TOPIC', but perception publishes '${RAW_TOPIC:-mdx-raw}'."
      echo "          Set REID_INPUT_TOPIC=RAW_TOPIC and restage before launching."; } >&2
    return 1
  fi
  extraction_interval="$(tracker_yaml_scalar "$cfg" TrajectoryManagement reidExtractionInterval)"
  feature_size="$(tracker_yaml_scalar "$cfg" ReID reidFeatureSize)"
  output_tensor="$(tracker_yaml_scalar "$cfg" ReID outputReidTensor)"
  service_host="$(tracker_yaml_scalar "$cfg" ReIDService serviceAddress)"
  service_port="$(tracker_yaml_scalar "$cfg" ReIDService servicePort)"
  if [ "$reid_type" = 2 ] && [ "$service_type" = 1 ] && \
     [ "$extraction_interval" = "$REID_EXTRACTION_INTERVAL" ] && \
     [ "$feature_size" = "$REID_DIMENSION" ] && [ "$output_tensor" = 1 ] && \
     [ "$service_host" = "$REID_SERVICE_HOST" ] && [ "$service_port" = "$REID_SERVICE_PORT" ]; then
    return 0
  fi

  { echo "** ERROR: REID_ENABLED=1 does not match the staged tracker config."
    echo "          expected: reidType=2 serviceType=1 extractionInterval=$REID_EXTRACTION_INTERVAL featureSize=$REID_DIMENSION outputTensor=1"
    echo "                    service=${REID_SERVICE_HOST}:${REID_SERVICE_PORT}"
    echo "          staged:   reidType=${reid_type:-missing} serviceType=${service_type:-missing} extractionInterval=${extraction_interval:-missing} featureSize=${feature_size:-missing} outputTensor=${output_tensor:-missing}"
    echo "                    service=${service_host:-missing}:${service_port:-missing}"
    echo "          Run ./scripts/stage-configs.sh after changing docker/.env."; } >&2
  return 1
}

if ! tracker_reid_matches_env "${CONFIG_DIR}/ds-mv3dt-tracker-config.yml"; then
  exit 1
fi

if ! osd_preflight "${CONFIG_DIR}/ds-main-config-mv3dt.txt"; then
  { echo
    echo "** ERROR: not starting the pipeline: it would fail at 'Failed to set pipeline"
    echo "          to PAUSED' with no explanation. Fix the cause reported above, or"
    echo "          drop the on-screen display:  OSD=0 ./scripts/stage-configs.sh"; } >&2
  exit 1
fi

GENERATED_DIR="/tmp/generated"
mkdir -p "${GENERATED_DIR}"
PUB_SUB_OUT="${GENERATED_DIR}/pub_sub_info_config.yml"

echo "Generating MQTT pub/sub config..."
PROVIDED_PUB_SUB=""
for candidate in "${CONFIG_DIR}/pub_sub_info_config.yml"; do
  [ -f "${candidate}" ] && PROVIDED_PUB_SUB="${candidate}" && break
done

if [ -n "${PROVIDED_PUB_SUB}" ]; then
  echo "Using provided pub/sub config: ${PROVIDED_PUB_SUB} (rewriting host:port to ${MQTT_ENDPOINT})"
  sed -E "s|[a-zA-Z0-9._-]+:[0-9]+|${MQTT_ENDPOINT}|g" "${PROVIDED_PUB_SUB}" > "${PUB_SUB_OUT}"
else
  mapfile -t CAM_NAMES < <(for f in /tmp/camInfo/*.yml; do [ -e "${f}" ] || continue; basename "${f}" .yml; done | sort -V)
  [ ${#CAM_NAMES[@]} -gt 0 ] || { echo "ERROR: No camera info files found under /tmp/camInfo"; exit 1; }

  {
    echo "pubBrokerTopicStr:"
    for cam in "${CAM_NAMES[@]}"; do
      echo "  ${cam}: ${MQTT_ENDPOINT};/trck/${cam}"
    done
    echo "subPeerBrokerTopicStrs:"
    for cam in "${CAM_NAMES[@]}"; do
      echo "  ${cam}:"
      for peer in "${CAM_NAMES[@]}"; do
        [ "${peer}" != "${cam}" ] && echo "  - ${MQTT_ENDPOINT};/trck/${peer}"
      done
    done
  } > "${PUB_SUB_OUT}"
fi

echo -e "\nPub/sub config:"
cat "${PUB_SUB_OUT}"

echo -e "\nPGIE config:"
cat "${CONFIG_DIR}/ds-pgie-config.yml"

echo -e "\nTracker config:"
cat "${CONFIG_DIR}/ds-mv3dt-tracker-config.yml"

prepare_reid_service() {
  [ "$REID_ENABLED" = 1 ] || return 0
  case "$REID_RESET_BEFORE_RUN" in
    0|1) ;;
    *) echo "** ERROR: REID_RESET_BEFORE_RUN must be 0 or 1 (got '$REID_RESET_BEFORE_RUN')" >&2; return 1 ;;
  esac
  [[ "$REID_READY_TIMEOUT_SEC" =~ ^[0-9]+$ ]] && [ "$REID_READY_TIMEOUT_SEC" -gt 0 ] || {
    echo "** ERROR: REID_READY_TIMEOUT_SEC must be a positive integer" >&2; return 1; }

  echo -e "\nWaiting up to ${REID_READY_TIMEOUT_SEC}s for ReID at ${REID_SERVICE_HOST}:${REID_SERVICE_PORT}..."
  python3 - "$REID_SERVICE_HOST" "$REID_SERVICE_PORT" "$REID_READY_TIMEOUT_SEC" "$REID_RESET_BEFORE_RUN" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

host, port, timeout, do_reset = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] == "1"
base = f"http://{host}:{port}"
deadline = time.monotonic() + timeout
last_error = "not contacted"
next_report = 0.0

while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(base + "/health/ready", timeout=5) as response:
            if 200 <= response.status < 300:
                print("ReID readiness: OK", flush=True)
                break
            last_error = f"HTTP {response.status}"
    except Exception as error:
        last_error = str(error)
    now = time.monotonic()
    if now >= next_report:
        print(f"  still waiting ({last_error})", flush=True)
        next_report = now + 10
    time.sleep(min(2, max(0, deadline - time.monotonic())))
else:
    print(f"ERROR: ReID did not become ready within {timeout}s: {last_error}", file=sys.stderr)
    sys.exit(1)

if do_reset:
    request = urllib.request.Request(
        base + "/reset?clear_main=true&clear_compressed=true", method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
            if not 200 <= response.status < 300:
                raise RuntimeError(f"HTTP {response.status}: {body}")
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = body
            print(f"ReID reset: OK ({payload})", flush=True)
    except Exception as error:
        print(f"ERROR: ReID reset failed: {error}", file=sys.stderr)
        sys.exit(1)
else:
    print("ReID reset skipped (REID_RESET_BEFORE_RUN=0)", flush=True)
PY
}

if ! prepare_reid_service; then
  echo "** ERROR: not starting perception without a ready, reset ReID service." >&2
  exit 1
fi

REID_ARGS=()
if [ "$REID_ENABLED" = 1 ]; then
  REID_ARGS+=(--tracker-reid)
fi

if [ "${STREAM_TYPE}" = "redis" ]; then
  echo -e "\nRunning metropolis_perception_app with redis (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt"
  exec ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1 --tiledtext "${REID_ARGS[@]}"
else
  [ "${STREAM_TYPE}" = "kafka" ] || echo "STREAM_TYPE not set or invalid. Defaulting to kafka..."
  echo -e "\nRunning metropolis_perception_app with kafka (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-config-mv3dt.txt"
  exec ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1 --tiledtext "${REID_ARGS[@]}"
fi
