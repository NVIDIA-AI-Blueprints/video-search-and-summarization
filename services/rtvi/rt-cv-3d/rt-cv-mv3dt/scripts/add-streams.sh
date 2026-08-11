#!/usr/bin/env bash
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
# add-streams.sh — register your RTSP streams with the perception container via the
# DeepStream REST API (dynamic stream addition), or remove one at runtime.
#
# There is no streaming front-end in this deployment: you supply the RTSP URLs.
# The streams must be time-synchronized across cameras (consistent timestamps),
# and each camera name must match its camInfo file (`<name>` ↔ /tmp/camInfo/<name>.yml)
# so the MV3DT tracker can look up the camera model.
#
# Usage:
#   ./scripts/add-streams.sh Camera=rtsp://host/cam0 Camera_01=rtsp://host/cam1 ...
#   ./scripts/add-streams.sh --file streams.txt          # one NAME=URL per line, # comments
#   ./scripts/add-streams.sh --remove Camera_01                   # remove one stream
#   ./scripts/add-streams.sh --remove Camera_01=rtsp://host/cam1  # also accepted
#   ./scripts/add-streams.sh --remove --file streams.txt          # remove every listed stream
#   ./scripts/add-streams.sh --list                      # show current stream-info
#
# Options / env:
#   --ds-port P        perception REST port      (default: $DS_HTTP_PORT or 9000)
#   --delay S          seconds between adds      (default: 1)
#   --ready-timeout S  wait for ds-ready: YES    (default: 600 — a cold TensorRT
#                      engine build for a new batch size takes minutes)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DS_HOST="${DS_HOST:-localhost}"
DS_PORT="${DS_PORT:-${DS_HTTP_PORT:-9000}}"
DELAY="${DELAY:-1}"
READY_TIMEOUT="${READY_TIMEOUT:-600}"

STREAMS=()
MODE=add
LIST=0

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while (($#)); do
  case "$1" in
    --file)           mapfile -t -O "${#STREAMS[@]}" STREAMS < <(grep -vE '^\s*(#|$)' "$2"); shift 2 ;;
    --remove)         MODE=remove; shift ;;
    --list)           LIST=1; shift ;;
    --ds-host)        DS_HOST="$2"; shift 2 ;;
    --ds-port)        DS_PORT="$2"; shift 2 ;;
    --delay)          DELAY="$2"; shift 2 ;;
    --ready-timeout)  READY_TIMEOUT="$2"; shift 2 ;;
    -h|--help)        usage 0 ;;
    *=*)              STREAMS+=("$1"); shift ;;
    *) if [[ "$MODE" == remove ]]; then STREAMS+=("$1"); shift; else echo "Unknown arg: $1" >&2; usage 2; fi ;;
  esac
done

BASE="http://${DS_HOST}:${DS_PORT}"
STREAM_INFO_JSON=""

show_stream_info() {
  local code tmp
  tmp=$(mktemp)
  code=$(curl -sS -o "$tmp" -w '%{http_code}' \
          --max-time 5 --connect-timeout 3 \
          "${BASE}/api/v1/stream/get-stream-info" 2>/dev/null) || code=000

  if [[ "$code" != "200" ]]; then
    echo "ERROR: Cannot connect to MV3DT perception REST API at ${BASE}." >&2
    echo "Check whether vss-rtvi-cv-mv3dt is running:" >&2
    echo >&2
    echo "  docker ps -a --filter name=vss-rtvi-cv-mv3dt" >&2
    echo "  docker logs --tail 120 vss-rtvi-cv-mv3dt" >&2
    if [[ "$code" != "000" ]]; then
      echo >&2
      echo "HTTP code: ${code}" >&2
    fi
    if [[ -s "$tmp" ]]; then
      echo >&2
      cat "$tmp" >&2 || true
      echo >&2
    fi
    rm -f "$tmp"
    return 1
  fi

  if ! python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f"ERROR: Invalid JSON response from MV3DT REST API: {e}", file=sys.stderr)
    sys.exit(1)
info = d.get("stream-info", {})
print("  stream-count: {}".format(info.get("stream-count", "?")))
for s in info.get("stream-info", []):
    print("    source_id={}  camera_id={}".format(s.get("source_id"), s.get("camera_id")))
' < "$tmp"; then
    rm -f "$tmp"
    return 1
  fi

  rm -f "$tmp"
}

lookup_stream_url() {  # $1=camera_id; prints URL when stream-info includes one.
  local cam="$1"
  if [[ -z "$STREAM_INFO_JSON" ]]; then
    if ! STREAM_INFO_JSON="$(curl -fsS --max-time 5 --connect-timeout 3 \
                             "${BASE}/api/v1/stream/get-stream-info" 2>/dev/null)"; then
      return 2
    fi
  fi
  printf '%s' "$STREAM_INFO_JSON" | python3 -c '
import json, sys

camera_id = sys.argv[1]
d = json.load(sys.stdin)
info = d.get("stream-info", {})
streams = info.get("stream-info", [])
for stream in streams:
    if str(stream.get("camera_id", "")) != camera_id:
        continue
    for key in ("camera_url", "url", "rtsp_url", "uri"):
        value = stream.get(key)
        if isinstance(value, str):
            print(value)
            break
    sys.exit(0)
sys.exit(1)
' "$cam"
}

response_reports_stream_change_failure() {  # $1=response_file  $2=add|remove
  python3 - "$1" "$2" <<'PY'
import json
import re
import sys

path, action = sys.argv[1], sys.argv[2]

try:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
except OSError:
    sys.exit(1)


def normalized(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def value_reports_failure(value):
    folded = normalized(value)
    return (
        f"stream_{action}_fail" in folded
        or f"stream_{action}_failed" in folded
        or (
            "stream" in folded
            and action in folded
            and ("fail" in folded or "error" in folded)
        )
    )


try:
    payload = json.loads(text)
except json.JSONDecodeError:
    sys.exit(0 if value_reports_failure(text) else 1)

stack = [payload]
while stack:
    item = stack.pop()
    if isinstance(item, dict):
        for key, value in item.items():
            if normalized(key) in {"success", "ok"} and value is False:
                sys.exit(0)
            stack.append(value)
    elif isinstance(item, list):
        stack.extend(item)
    elif isinstance(item, str) and value_reports_failure(item):
        sys.exit(0)

sys.exit(1)
PY
}

validate_camera_configured() {  # $1=camera_id
  python3 - "$ROOT" "$1" <<'PY'
import os
import sys

root, camera_id = sys.argv[1], sys.argv[2]
generated_dir = os.path.join(root, "generated")
cam_info_dir = os.path.join(generated_dir, "camInfo")
tracker_config = os.path.join(generated_dir, "configs", "ds-mv3dt-tracker-config.yml")
pub_sub_config = os.path.join(generated_dir, "configs", "pub_sub_info_config.yml")

# Some ad hoc deployments do not stage generated configs beside this helper.
# In that case there is no local source of truth to check.
if not any(os.path.exists(path) for path in (cam_info_dir, tracker_config, pub_sub_config)):
    sys.exit(0)

missing = []
if not any(
    os.path.isfile(os.path.join(cam_info_dir, f"{camera_id}.{ext}"))
    for ext in ("yml", "yaml")
):
    missing.append(f"generated/camInfo/{camera_id}.yml")

try:
    import yaml
except ImportError:
    if missing:
        print(
            f"ERROR: camera_id {camera_id} is not configured in camInfo/tracker/pub-sub config",
            file=sys.stderr,
        )
        for item in missing:
            print(f"  missing: {item}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


def load_yaml(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        rel = os.path.relpath(path, root)
        print(f"ERROR: cannot parse {rel}: {exc}", file=sys.stderr)
        sys.exit(2)


tracker = load_yaml(tracker_config)
if tracker is not None:
    object_model = (
        tracker.get("ObjectModelProjection", {}) if isinstance(tracker, dict) else {}
    )
    camera_models = (
        object_model.get("cameraModelFilepath", {})
        if isinstance(object_model, dict)
        else {}
    )
    if not isinstance(camera_models, dict) or camera_id not in camera_models:
        missing.append(
            "generated/configs/ds-mv3dt-tracker-config.yml "
            "ObjectModelProjection.cameraModelFilepath"
        )

pub_sub = load_yaml(pub_sub_config)
if pub_sub is not None:
    if not isinstance(pub_sub, dict):
        pub_sub = {}
    pub_topics = pub_sub.get("pubBrokerTopicStr", {})
    sub_topics = pub_sub.get("subPeerBrokerTopicStrs", {})
    if not isinstance(pub_topics, dict) or camera_id not in pub_topics:
        missing.append("generated/configs/pub_sub_info_config.yml pubBrokerTopicStr")
    if not isinstance(sub_topics, dict) or camera_id not in sub_topics:
        missing.append("generated/configs/pub_sub_info_config.yml subPeerBrokerTopicStrs")

if missing:
    print(
        f"ERROR: camera_id {camera_id} is not configured in camInfo/tracker/pub-sub config",
        file=sys.stderr,
    )
    for item in missing:
        print(f"  missing: {item}", file=sys.stderr)
    sys.exit(2)
PY
}

post_sensor() {  # $1=camera_id  $2=url  $3=change (camera_add|camera_remove)
  local body code tmp
  body=$(python3 -c '
import json, sys
cid, url, change = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
  "key": "sensor",
  "value": {
    "camera_id": cid, "camera_name": cid, "camera_url": url,
    "change": change,
    "metadata": {"resolution": "1920x1080", "codec": "h264", "framerate": 30},
  },
  "headers": {"source": "manual"},
}))
' "$1" "$2" "$3")
  tmp=$(mktemp)
  code=$(printf '%s' "$body" | curl -sS -o "$tmp" -w '%{http_code}' \
          --max-time 30 --connect-timeout 5 \
          -X POST "${BASE}/api/v1/stream/${3#camera_}" \
          -H 'Content-Type: application/json' --data-binary @-) || code=000
  if response_reports_stream_change_failure "$tmp" "${3#camera_}"; then
    echo "   ✗ HTTP ${code} failed to ${3#camera_} stream"
    cat "$tmp" >&2 || true
    echo >&2
    rm -f "$tmp"; return 1
  fi
  if [[ "$code" == "200" || "$code" == "201" ]]; then
    echo "   ✓ HTTP ${code}  $(grep -o '"reason" *: *"[^"]*"' "$tmp" | head -1 | tr -s ' ')"
    rm -f "$tmp"; return 0
  fi
  if [[ "$code" == "000" ]]; then
    # curl got no reply at all. The service can stop answering while its
    # container is still up -- removing the last registered stream has been seen
    # to leave it that way -- so say that rather than a bare HTTP 000 after a
    # silent wait.
    echo "   ✗ no reply from ${BASE} (timed out or refused)" >&2
    echo "     The perception REST API is not responding. Check whether it is alive:" >&2
    echo "       docker logs --tail 120 vss-rtvi-cv-mv3dt" >&2
    echo "     If it is running but unresponsive, recreate it:" >&2
    echo "       (cd docker && docker compose up -d --force-recreate perception)" >&2
    rm -f "$tmp"; return 1
  fi
  echo "   ✗ HTTP ${code}"; cat "$tmp" >&2 || true; echo >&2
  rm -f "$tmp"; return 1
}

# ── --list mode ──────────────────────────────────────────────────────────────
if (( LIST )); then
  if show_stream_info; then
    exit 0
  fi
  exit 1
fi

(( ${#STREAMS[@]} )) || { echo "ERROR: no streams given (NAME=URL args or --file)" >&2; usage 2; }

# ── --remove mode: delete each listed stream (camera_id or NAME=URL) ──────────
# Paced by --delay, mirroring the add path.
# True when the requested removals would leave the perception service with no
# registered streams. On this build the REST API stops answering once no sources
# remain: the container keeps running but every /api/v1 request times out, and it
# has to be recreated. Worth saying before it happens rather than after a silent
# timeout, but not worth refusing -- clearing every stream is a normal request.
removal_empties_registry() {
  local payload
  payload="$(curl -fsS --max-time 5 --connect-timeout 3 \
             "${BASE}/api/v1/stream/get-stream-info" 2>/dev/null)" || return 1
  STREAM_INFO_PAYLOAD="$payload" python3 -c '
import json, os, sys

wanted = {a.split("=", 1)[0] for a in sys.argv[1:] if a}
try:
    streams = json.loads(os.environ["STREAM_INFO_PAYLOAD"])["stream-info"]["stream-info"]
except Exception:
    sys.exit(1)
registered = {str(s.get("camera_id", "")) for s in streams if isinstance(s, dict)}
registered.discard("")
sys.exit(0 if registered and registered <= wanted else 1)
' "$@"
}

if [[ "$MODE" == remove ]]; then
  if removal_empties_registry "${STREAMS[@]}"; then
    echo "   ⚠ this removes every registered stream. With no sources left the perception" >&2
    echo "     REST API stops responding: the container keeps running but /api/v1 requests" >&2
    echo "     time out, and it has to be recreated before streams can be added again:" >&2
    echo "       (cd docker && docker compose up -d --force-recreate perception)" >&2
  fi
  echo "── Removing ${#STREAMS[@]} stream(s) (delay=${DELAY}s)"
  rc=0; idx=0
  for entry in "${STREAMS[@]}"; do
    if [[ "$entry" == *=* ]]; then
      cam="${entry%%=*}"; url="${entry#*=}"
      if [[ -z "$cam" || "$url" != rtsp://* ]]; then
        echo "   ⚠ skipping malformed removal entry: [${entry}] (want NAME=rtsp://... or camera_id)" >&2
        rc=2; continue
      fi
    else
      cam="$entry"; url=""
      if [[ -z "$cam" ]]; then
        echo "   ⚠ skipping malformed removal entry: [${entry}] (want NAME=rtsp://... or camera_id)" >&2
        rc=2; continue
      fi
      lu=0; url="$(lookup_stream_url "$cam")" || lu=$?
      if (( lu == 2 )); then
        echo "   ✗ cannot reach the perception REST API at ${BASE} to look up [${cam}]" >&2
        echo "     Check whether it is alive:  docker logs --tail 120 vss-rtvi-cv-mv3dt" >&2
        rc=2; continue
      fi
      if (( lu != 0 )); then
        echo "   ⚠ camera_id is not registered: [${cam}] (see --list)" >&2
        rc=2; continue
      fi
    fi
    echo "── Removing camera_id=${cam} (waiting up to 30s for a reply)"
    post_sensor "$cam" "$url" camera_remove || rc=2
    idx=$((idx + 1))
    (( idx < ${#STREAMS[@]} )) && sleep "$DELAY"
  done
  echo; show_stream_info
  exit "$rc"
fi

# ── 1. Wait for the perception REST API ─────────────────────────────────────
echo "── Waiting up to ${READY_TIMEOUT}s for ${BASE}/api/v1/ready → ds-ready: YES"
deadline=$(( SECONDS + READY_TIMEOUT ))
state=""
while (( SECONDS < deadline )); do
  state=$(curl -fsS --max-time 2 "${BASE}/api/v1/ready" 2>/dev/null | grep -o '"ds-ready" : "[A-Z]*"' || true)
  grep -q '"YES"' <<< "$state" && { echo "   ds-ready: YES (${SECONDS}s)"; break; }
  sleep 3
done
grep -q '"YES"' <<< "$state" || { echo "ERROR: perception never reported ready" >&2; exit 1; }

# ── 2. Add each stream ───────────────────────────────────────────────────────
echo "── Adding ${#STREAMS[@]} stream(s) (delay=${DELAY}s)"
idx=0
for entry in "${STREAMS[@]}"; do
  cam="${entry%%=*}"
  url="${entry#*=}"
  if [[ -z "$cam" || "$url" != rtsp://* ]]; then
    echo "   ⚠ skipping malformed entry: [${entry}] (want NAME=rtsp://...)" >&2
    continue
  fi
  echo
  echo ">> [$((idx+1))/${#STREAMS[@]}] camera_id=${cam}"
  echo "                       url=${url}"
  validate_camera_configured "$cam" || exit 2
  post_sensor "$cam" "$url" camera_add || exit 2
  idx=$((idx + 1))
  (( idx < ${#STREAMS[@]} )) && sleep "$DELAY"
done

# ── 3. Report ────────────────────────────────────────────────────────────────
echo
echo "── Reading stream-info"
show_stream_info
echo
echo "Check per-source FPS:  docker logs vss-rtvi-cv-mv3dt 2>&1 | grep -A$(( ${#STREAMS[@]} + 1 )) '\\*\\*PERF' | tail -8"
