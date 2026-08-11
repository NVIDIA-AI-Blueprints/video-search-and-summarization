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
#   ./scripts/add-streams.sh --remove Camera_01=rtsp://host/cam1  # remove one stream
#   ./scripts/add-streams.sh --remove --file streams.txt          # remove every listed stream
#   ./scripts/add-streams.sh --list                      # show current stream-info
#
# Options / env:
#   --ds-port P        perception REST port      (default: $DS_HTTP_PORT or 9000)
#   --delay S          seconds between adds      (default: 1)
#   --ready-timeout S  wait for ds-ready: YES    (default: 600 — a cold TensorRT
#                      engine build for a new batch size takes minutes)
set -euo pipefail

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
    *) echo "Unknown arg: $1" >&2; usage 2 ;;
  esac
done

BASE="http://${DS_HOST}:${DS_PORT}"

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
  if [[ "$code" == "200" || "$code" == "201" ]]; then
    echo "   ✓ HTTP ${code}  $(grep -o '"reason" *: *"[^"]*"' "$tmp" | head -1 | tr -s ' ')"
    rm -f "$tmp"; return 0
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

# ── --remove mode: delete each listed stream (same NAME=URL as adding) ────────
# Paced by --delay, mirroring the add path.
if [[ "$MODE" == remove ]]; then
  echo "── Removing ${#STREAMS[@]} stream(s) (delay=${DELAY}s)"
  rc=0; idx=0
  for entry in "${STREAMS[@]}"; do
    cam="${entry%%=*}"; url="${entry#*=}"
    if [[ -z "$cam" || "$url" == "$entry" || "$url" != rtsp://* ]]; then
      echo "   ⚠ removal needs NAME=rtsp://... (the exact registered URL): [${entry}]" >&2
      rc=2; continue
    fi
    echo "── Removing camera_id=${cam}"
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
