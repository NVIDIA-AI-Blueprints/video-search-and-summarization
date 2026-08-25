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
                    "grant by uid (this container is uid $(id -u)):  xhost +SI:localuser:\$(id -un)" \
                    "or open it:  xhost +local:" \
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
  elif timeout 20 gst-launch-1.0 -q videotestsrc num-buffers=1 ! nveglglessink >/tmp/egl-probe.log 2>&1; then
    echo "   EGL sink probe: OK"
    echo "   OSD preflight passed"
  else
    { echo "** WARNING: the EGL sink could not render a test frame, so OSD will not work."
      grep -iE 'drm|dri2|EGL' /tmp/egl-probe.log 2>/dev/null | tail -3 | sed 's/^/          /'
      # The NVIDIA runtime always exposes a couple of DRI nodes, so their
      # presence says nothing about whether the full set is mapped.
      grep -qiE 'drm|dri2' /tmp/egl-probe.log 2>/dev/null &&
        echo "          Mesa cannot reach the DRM device: uncomment the devices block in docker/compose.yml"
      echo "          Continuing; the pipeline may stall at PAUSED."; } >&2
  fi
  return 0
}

MQTT_HOST=${MQTT_HOST:-localhost}
MQTT_PORT=${MQTT_PORT:-1883}
MQTT_ENDPOINT="${MQTT_HOST}:${MQTT_PORT}"
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app
APP_DIR="$(pwd)"
CONFIG_DIR="${APP_DIR}/configs"

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

if [ "${STREAM_TYPE}" = "redis" ]; then
  echo -e "\nRunning metropolis_perception_app with redis (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt"
  ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1 --tiledtext
else
  [ "${STREAM_TYPE}" = "kafka" ] || echo "STREAM_TYPE not set or invalid. Defaulting to kafka..."
  echo -e "\nRunning metropolis_perception_app with kafka (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-config-mv3dt.txt"
  ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1 --tiledtext
fi
