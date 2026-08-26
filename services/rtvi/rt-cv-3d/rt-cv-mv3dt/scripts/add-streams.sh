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
#   ./scripts/add-streams.sh --remove Camera_01=rtsp://host/cam1  # also accepted, any URL scheme
#   ./scripts/add-streams.sh --remove --file streams.txt          # remove every listed stream
#   ./scripts/add-streams.sh --remove-all                         # remove every registered stream
#   ./scripts/add-streams.sh --remove-all --yes                   # same, no confirmation prompt
#   ./scripts/add-streams.sh --list                      # show current stream-info
#
# Options / env:
#   --ds-port P        perception REST port      (default: $DS_HTTP_PORT or 9000)
#   --delay S          seconds between adds      (default: 1)
#   --no-url-check     skip the pre-add RTSP reachability check
#   --no-sei-check     skip the VST SEI frame-ID prerequisite check
#   -y, --yes          answer yes to the --remove-all confirmation
#   --activation-timeout S  wait for added streams to produce frames (default: 60;
#                      0 disables). A stream the server accepts but never decodes
#                      is reported as inactive.
#   --ready-timeout S  wait for ds-ready: YES    (default: 600 — a cold TensorRT
#                      engine build for a new batch size takes minutes)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DS_HOST="${DS_HOST:-localhost}"
DS_PORT="${DS_PORT:-${DS_HTTP_PORT:-9000}}"
DELAY="${DELAY:-1}"
READY_TIMEOUT="${READY_TIMEOUT:-600}"
# Seconds to wait for added streams to start producing frames before reporting
# them as inactive. 0 disables the check.
ACTIVATION_TIMEOUT="${ACTIVATION_TIMEOUT:-60}"
# Seconds to wait for a TCP connection to an RTSP endpoint before adding it.
# 0 disables the pre-add reachability check.
RTSP_PROBE_TIMEOUT="${RTSP_PROBE_TIMEOUT:-2}"
# VST management API, used to confirm the proxy emits SEI frame IDs before
# streams are registered. Port is VST's http_port; host is taken from the RTSP
# URLs. Set VST_HTTP_PORT=0 or pass --no-sei-check to skip.
VST_HTTP_PORT="${VST_HTTP_PORT:-30000}"

STREAMS=()
MODE=add
LIST=0
REMOVE_ALL=0
ASSUME_YES=0

# Print the commented Usage/Options block, skipping the SPDX licence header.
usage() { sed -n '/^# Usage:/,/^set -euo pipefail/p' "$0" | sed '$d; s/^#\{0,1\} \{0,1\}//'; exit "${1:-0}"; }

while (($#)); do
  case "$1" in
    --file)           mapfile -t -O "${#STREAMS[@]}" STREAMS \
                        < <(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$2" | grep -vE '^(#|$)'); shift 2 ;;
    --remove)         MODE=remove; shift ;;
    --remove-all)     MODE=remove; REMOVE_ALL=1; shift ;;
    -y|--yes)         ASSUME_YES=1; shift ;;
    --list)           LIST=1; shift ;;
    --ds-host)        DS_HOST="$2"; shift 2 ;;
    --ds-port)        DS_PORT="$2"; shift 2 ;;
    --delay)          DELAY="$2"; shift 2 ;;
    --ready-timeout)  READY_TIMEOUT="$2"; shift 2 ;;
    --activation-timeout) ACTIVATION_TIMEOUT="$2"; shift 2 ;;
    --no-url-check)   RTSP_PROBE_TIMEOUT=0; shift ;;
    --no-sei-check)   VST_HTTP_PORT=0; shift ;;
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

  STREAM_INFO_JSON="$(<"$tmp")"
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

show_registration_progress() {  # args: camera IDs known to have been added in this run.
  local payload
  if [[ -n "$STREAM_INFO_JSON" ]]; then
    payload="$STREAM_INFO_JSON"
  elif ! payload="$(curl -fsS --max-time 5 --connect-timeout 3 \
                    "${BASE}/api/v1/stream/get-stream-info" 2>/dev/null)"; then
    return 0
  else
    STREAM_INFO_JSON="$payload"
  fi

  STREAM_INFO_PAYLOAD="$payload" NUM_CAMS_VALUE="${NUM_CAMS:-}" \
  python3 - "$ROOT" "$@" <<'PY' || true
import glob, json, os, sys

root, known_registered = sys.argv[1], sys.argv[2:]


def parse_int(value, minimum):
    try:
        value = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= minimum else None


def unique(items):
    result, seen = [], set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def configured_camera_ids():
    generated = os.path.join(root, "generated")
    tracker = os.path.join(generated, "configs", "ds-mv3dt-tracker-config.yml")
    try:
        import yaml
        with open(tracker, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        models = data.get("ObjectModelProjection", {}).get("cameraModelFilepath", {})
        if isinstance(models, dict) and models:
            return unique(str(camera_id) for camera_id in models)
    except Exception:
        pass

    patterns = (
        os.path.join(generated, "camInfo", "*.yml"),
        os.path.join(generated, "camInfo", "*.yaml"),
    )
    return unique(
        os.path.splitext(os.path.basename(path))[0]
        for pattern in patterns
        for path in sorted(glob.glob(pattern))
    )


try:
    info = json.loads(os.environ.get("STREAM_INFO_PAYLOAD", "")).get("stream-info", {})
except (AttributeError, json.JSONDecodeError):
    sys.exit(0)
if not isinstance(info, dict):
    sys.exit(0)

streams = info.get("stream-info", [])
streams = streams if isinstance(streams, list) else []
registered = parse_int(info.get("stream-count"), 0)
registered = len(streams) if registered is None else registered
expected_ids = configured_camera_ids()
required = parse_int(os.environ.get("NUM_CAMS_VALUE"), 1) or len(expected_ids)
if not required:
    sys.exit(0)

expected_ids = expected_ids[:required]
registered_ids, unnamed_sources = set(), []
for stream in streams:
    if not isinstance(stream, dict):
        continue
    camera_id = stream.get("camera_id")
    if isinstance(camera_id, str) and camera_id:
        registered_ids.add(camera_id)
    else:
        unnamed_sources.append(parse_int(stream.get("source_id"), 0))

for camera_id in known_registered:
    if len(registered_ids) >= registered:
        break
    registered_ids.add(camera_id)
for source_id in unnamed_sources:
    if len(registered_ids) >= registered:
        break
    if source_id is not None and source_id < len(expected_ids):
        registered_ids.add(expected_ids[source_id])

missing = [camera_id for camera_id in expected_ids if camera_id not in registered_ids]
remaining = max(required - registered, 0)

print(f"Registered streams: {registered}/{required}")
if registered < required:
    print(f"INFO: MV3DT requires {required} streams.")
    if missing:
        print("Waiting for: " + ", ".join(missing))
    elif remaining:
        suffix = "stream" if remaining == 1 else "streams"
        print(f"Waiting for {remaining} additional {suffix}.")
PY
}

# True when camera_id is currently registered. That is all the removal needs:
# the REST API identifies a stream by camera_id and accepts an empty
# camera_url (nvbugs/6557680).
#
# Returns 0 registered, 1 not registered, 2 API unreachable.
stream_is_registered() {  # $1=camera_id
  local cam="$1"
  if [[ -z "$STREAM_INFO_JSON" ]]; then
    if ! STREAM_INFO_JSON="$(curl -fsS --max-time 5 --connect-timeout 3 \
                             "${BASE}/api/v1/stream/get-stream-info" 2>/dev/null)"; then
      return 2
    fi
  fi
  printf '%s' "$STREAM_INFO_JSON" | python3 -c '
import json, sys
try:
    streams = json.load(sys.stdin)["stream-info"]["stream-info"]
except Exception:
    sys.exit(1)
sys.exit(0 if any(str(s.get("camera_id", "")) == sys.argv[1] for s in streams
                  if isinstance(s, dict)) else 1)
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
    # camInfo exists, but without pyyaml the tracker and pub/sub membership checks
    # cannot run. Say so rather than reporting a pass the check did not make: an
    # id present in camInfo but absent from pub_sub_info_config.yml still crashes
    # the tracker, which is the failure this validation exists to prevent.
    print(
        f"   ⚠ pyyaml unavailable: checked only generated/camInfo/{camera_id}.yml,",
        file=sys.stderr,
    )
    print(
        "     not the tracker cameraModelFilepath or pub/sub topic entries.",
        file=sys.stderr,
    )
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
    if grep -q 'Source url empty' "$tmp"; then
      echo "     This build of the API will not drop a stream by camera_id alone." >&2
      echo "     Retry with the source URL:  --remove ${1}=<url>" >&2
    fi
    rm -f "$tmp"; return 1
  fi
  if [[ "$code" == "200" || "$code" == "201" ]]; then
    echo "   ✓ HTTP ${code}  $(grep -o '"reason" *: *"[^"]*"' "$tmp" | head -1 | tr -s ' ')"
    STREAM_INFO_JSON=""
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
    show_registration_progress
    exit 0
  fi
  exit 1
fi

(( ${#STREAMS[@]} || REMOVE_ALL )) || { echo "ERROR: no streams given (NAME=URL args, --file, or --remove-all)" >&2; usage 2; }

# ── --remove mode: delete each listed stream (camera_id or NAME=URL) ──────────
# Paced by --delay, mirroring the add path.
# Camera ids currently registered, one per line. No output means either an empty
# registry or an unreachable API; callers tell them apart by the exit status.
registered_camera_ids() {
  local payload
  payload="$(curl -fsS --max-time 5 --connect-timeout 3 \
             "${BASE}/api/v1/stream/get-stream-info" 2>/dev/null)" || return 1
  STREAM_INFO_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    streams = json.loads(os.environ["STREAM_INFO_PAYLOAD"])["stream-info"]["stream-info"]
except Exception:
    sys.exit(1)
for cam in sorted({str(s.get("camera_id", "")) for s in streams if isinstance(s, dict)} - {""}):
    print(cam)
'
}

# Recovery guidance, printed only once the API has actually stopped answering.
# Removing the last source can wedge the REST server (bug 6631012), but it does
# not always, so report it after the fact instead of predicting it.
report_api_lost() {
  echo >&2
  echo "   ⚠ the perception REST API stopped responding after the removal." >&2
  echo "     The container keeps running but /api/v1 requests time out; recreate it" >&2
  echo "     before adding or listing streams again:" >&2
  echo "       (cd docker && docker compose up -d --force-recreate perception)" >&2
}

if [[ "$MODE" == remove ]]; then
  if (( REMOVE_ALL )); then
    if ! mapfile -t STREAMS < <(registered_camera_ids); then
      echo "ERROR: cannot reach the perception REST API at ${BASE} to list streams." >&2
      exit 1
    fi
    (( ${#STREAMS[@]} )) || { echo "No streams are registered; nothing to remove."; exit 0; }
    echo "── ${#STREAMS[@]} registered stream(s) will be removed:"
    printf '     %s\n' "${STREAMS[@]}"
    if ! (( ASSUME_YES )); then
      if [[ ! -t 0 ]]; then
        echo "ERROR: --remove-all needs confirmation but stdin is not a terminal." >&2
        echo "       Re-run with --yes to confirm non-interactively." >&2
        exit 1
      fi
      read -r -p "   Remove all of them? [y/N] " reply || reply=""
      [[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
    fi
  fi

  echo "── Removing ${#STREAMS[@]} stream(s) (delay=${DELAY}s)"
  rc=0; idx=0
  for entry in "${STREAMS[@]}"; do
    if [[ "$entry" == *=* ]]; then
      cam="${entry%%=*}"; url="${entry#*=}"
      if [[ -z "$cam" || "$url" != *://* ]]; then
        echo "   ⚠ skipping malformed removal entry: [${entry}] (want NAME=URL or camera_id)" >&2
        rc=2; continue
      fi
    else
      cam="$entry"; url=""
      if [[ -z "$cam" ]]; then
        echo "   ⚠ skipping malformed removal entry: [${entry}] (want NAME=URL or camera_id)" >&2
        rc=2; continue
      fi
      lu=0; stream_is_registered "$cam" || lu=$?
      if (( lu == 2 )); then
        echo "   ✗ cannot reach the perception REST API at ${BASE} to check [${cam}]" >&2
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
  echo
  # On failure show_stream_info prints its own connectivity block, which just
  # repeats what the removal already said. Keep its success output, replace its
  # error with the one message that explains the removal context.
  if ! show_stream_info 2>/dev/null; then
    report_api_lost
    (( rc )) || rc=1
  fi
  exit "$rc"
fi

# The REST API accepts any well-formed URL, including one pointing at a closed
# port, and reports STREAM_ADD_SUCCESS for a source that will never decode. A TCP
# connect to the endpoint tells us that much before the stream is registered and
# occupies one of the max-batch-size slots.
#
# Only a refused connection or an unresolvable host is treated as definitive. A
# timeout is inconclusive -- a slow or filtered network looks the same as a dead
# one -- so it warns and continues, and verify_streams_active catches it later if
# the source really is dead. Reachability is judged from this host, which is the
# same network namespace the perception container uses.
#
# Returns 0 reachable, 1 definitely unreachable, 2 inconclusive.
probe_rtsp_endpoint() {  # $1=rtsp url
  [[ "$RTSP_PROBE_TIMEOUT" =~ ^[0-9]+$ ]] || return 0
  (( RTSP_PROBE_TIMEOUT > 0 )) || return 0
  RTSP_URL="$1" RTSP_TIMEOUT="$RTSP_PROBE_TIMEOUT" python3 -c '
import os, socket, sys
from urllib.parse import urlparse

url = os.environ["RTSP_URL"]
try:
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or 554
except Exception:
    sys.exit(0)
if not host:
    sys.exit(0)
try:
    with socket.create_connection((host, port), timeout=float(os.environ["RTSP_TIMEOUT"])):
        sys.exit(0)
except (ConnectionRefusedError, socket.gaierror) as exc:
    print(f"{host}:{port}: {exc}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"{host}:{port}: {exc}", file=sys.stderr)
    sys.exit(2)
'
}

# With INPUT_MODE=stream every RTSP source must carry NVDS_CUSTOMMETA SEI: the
# staged DeepStream config sets extract-sei-sim-time=1 with
# attach-sys-ts-as-ntp=0, so frame timestamps come from the SEI. File input is
# staged the other way round (SEI extraction off, attach-sys-ts-as-ntp=1) and
# takes them from the host clock, so this prerequisite is specific to the live
# stream path, not to MV3DT as such.
# In this deployment the VST proxy is what injects that SEI. With
# "enable_proxy_server_sei_metadata": false the proxy serves video without it,
# and the perception service accepts every stream but never activates any
# source -- no bbox, no mdx-raw. That misconfiguration is invisible from the
# perception side, so ask VST directly before registering anything.
#
# VST exposes it at GET /api/v1/proxy/configuration on its http_port as
# "enableProxyServerFrameIdSupport". Fails open: deployments that feed RTSP
# from cameras rather than a VST proxy have no such endpoint, and must not be
# blocked by a check that cannot apply to them.
check_sei_frame_ids() {  # $1=an rtsp:// url the streams will come from
  [[ "$VST_HTTP_PORT" =~ ^[0-9]+$ ]] || return 0
  (( VST_HTTP_PORT > 0 )) || return 0

  local host payload enabled
  host="${1#rtsp://}"; host="${host%%/*}"; host="${host%%:*}"
  [[ -n "$host" ]] || return 0

  payload="$(curl -fsS --max-time 3 --connect-timeout 2 \
             "http://${host}:${VST_HTTP_PORT}/api/v1/proxy/configuration" 2>/dev/null)" || return 0

  enabled="$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
v = d.get("enableProxyServerFrameIdSupport")
if isinstance(v, bool):
    print("true" if v else "false")
' 2>/dev/null)"

  [[ "$enabled" == "false" ]] || return 0

  echo "ERROR: the VST proxy at ${host}:${VST_HTTP_PORT} is not emitting SEI frame IDs" >&2
  echo "       (enableProxyServerFrameIdSupport=false)." >&2
  echo "       MV3DT stamps frames from the host clock or from NVDS_CUSTOMMETA SEI," >&2
  echo "       depending on the input mode. This deployment is staged for live streams" >&2
  echo "       (extract-sei-sim-time=1 with attach-sys-ts-as-ntp=0), so the SEI is required" >&2
  echo "       and there is no host-time fallback: the streams would be accepted, ds-ready" >&2
  echo "       would report YES, and no source would ever activate -- no bbox in the OSD" >&2
  echo "       and no mdx-raw metadata." >&2
  echo >&2
  echo "       Set \"enable_proxy_server_sei_metadata\": true in the VST and NVStreamer" >&2
  echo "       vst_config.json your deployment uses, redeploy, and confirm inside the" >&2
  echo "       containers (see README section 4). Pass --no-sei-check to override." >&2
  echo "       A source that is not proxied by VST must carry NVDS_CUSTOMMETA SEI itself." >&2
  return 1
}

# ── 1. Wait for the perception REST API ─────────────────────────────────────
# One run can add streams from more than one VST host, and each host carries its
# own enable_proxy_server_sei_metadata. Check every distinct host, not just the
# first: a later host with it off would otherwise register and never activate.
# Deduplicated so the usual single-host case still makes one request.
sei_checked=""
for entry in "${STREAMS[@]}"; do
  sei_url="${entry#*=}"
  sei_host="${sei_url#rtsp://}"; sei_host="${sei_host%%/*}"; sei_host="${sei_host%%:*}"
  [[ -n "$sei_host" ]] || continue
  case " $sei_checked " in *" $sei_host "*) continue ;; esac
  sei_checked="$sei_checked $sei_host"
  check_sei_frame_ids "$sei_url" || exit 2
done

echo "── Waiting up to ${READY_TIMEOUT}s for ${BASE}/api/v1/ready → ds-ready: YES"
deadline=$(( SECONDS + READY_TIMEOUT ))
state=""
while (( SECONDS < deadline )); do
  state=$(curl -fsS --max-time 2 "${BASE}/api/v1/ready" 2>/dev/null | grep -o '"ds-ready" : "[A-Z]*"' || true)
  grep -q '"YES"' <<< "$state" && { echo "   ds-ready: YES (${SECONDS}s)"; break; }
  sleep 3
done
grep -q '"YES"' <<< "$state" || { echo "ERROR: perception never reported ready" >&2; exit 1; }

# Streams the server accepted but which never produce frames are invisible to
# /api/v1/stream/get-stream-info: it reports registration, not health. The
# /api/v1/metrics endpoint reports per-stream stats and omits sources that are
# not decoding, so a camera present in stream-info but absent from stream-stats
# was accepted and is dead.
#
# Deliberately NOT keyed on the reported fps: on a healthy 4-camera deployment
# three of the four sources report fps 0.0 while their frame_number advances at
# ~30/s, so fps is not a usable liveness signal. Presence in stream-stats is.
# Echoes "<registered> <required>": the streams the perception service currently
# has, and how many the deployment expects -- NUM_CAMS when set, else the number
# of configured camInfo entries. Echoes "0 0" when neither can be determined.
registered_and_required() {
  local payload
  payload="$(curl -fsS --max-time 5 --connect-timeout 3 \
             "${BASE}/api/v1/stream/get-stream-info" 2>/dev/null)" || { echo "0 0"; return 0; }
  STREAM_INFO_PAYLOAD="$payload" NUM_CAMS_VALUE="${NUM_CAMS:-}" ROOT_DIR="$ROOT" \
  python3 -c '
import glob, json, os

try:
    info = json.loads(os.environ["STREAM_INFO_PAYLOAD"])["stream-info"]
    registered = int(info.get("stream-count") or 0)
except Exception:
    registered = 0

raw = (os.environ.get("NUM_CAMS_VALUE") or "").strip()
if raw.isdigit() and int(raw) > 0:
    required = int(raw)
else:
    cams = set()
    root = os.environ.get("ROOT_DIR", ".")
    for pat in ("*.yml", "*.yaml"):
        for f in glob.glob(os.path.join(root, "generated", "camInfo", pat)):
            cams.add(os.path.splitext(os.path.basename(f))[0])
    required = len(cams)

print(registered, required)
' 2>/dev/null || echo "0 0"
}

verify_streams_active() {  # args: camera IDs added in this run
  (( $# )) || return 0
  [[ "$ACTIVATION_TIMEOUT" =~ ^[0-9]+$ ]] || return 0
  (( ACTIVATION_TIMEOUT > 0 )) || return 0

  # MV3DT batches its sources: until every configured camera is registered,
  # nvstreammux has no complete batch and no source produces frames. That is the
  # normal state while streams are added one at a time, so checking then would
  # stall for the whole timeout and then report a failure that is not one.
  local registered required
  read -r registered required <<<"$(registered_and_required)"
  if [[ -n "$required" ]] && (( required > 0 )) && (( registered < required )); then
    return 0
  fi

  # Liveness is frame_number advancing, not membership of stream-stats. That list
  # is a rolling buffer of recent per-source samples, not one row per stream: a
  # single payload can carry the same sensor_id twice with consecutive frame
  # numbers, and stream-count can exceed the number of distinct sensors in it. A
  # source that is decoding therefore shows up repeatedly across a few seconds of
  # polling with a rising frame_number, while one that is not either never
  # appears or stays frozen -- observed live at frame_number 296 while DeepStream
  # retried its RTSP connect. Both count as not producing; only the wording of
  # the report differs, because only cameras added by this run are judged.
  echo "── Waiting up to ${ACTIVATION_TIMEOUT}s for the sources to produce frames"

  local out rc=0
  out="$(BASE="$BASE" ACTIVATION_TIMEOUT="$ACTIVATION_TIMEOUT" \
         python3 - "$@" <<'PY'
import json
import os
import sys
import time
import urllib.request

base = os.environ["BASE"]
timeout = int(os.environ["ACTIVATION_TIMEOUT"])
wanted = [c for c in sys.argv[1:] if c]

# Bypass any http_proxy in the environment: this endpoint is local.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sample():
    """{sensor_id: frame_number}, or None when the endpoint is not there."""
    try:
        with opener.open(base + "/api/v1/metrics", timeout=5) as resp:
            payload = json.load(resp)
    except Exception:
        return None
    stats = payload.get("metrics-info", {}).get("stream-stats")
    if not isinstance(stats, list):
        return {}
    seen = {}
    for entry in stats:
        if not isinstance(entry, dict):
            continue
        sensor = entry.get("sensor_id")
        try:
            frames = int(entry.get("frame_number"))
        except (TypeError, ValueError):
            continue
        if sensor is not None:
            seen[str(sensor)] = frames
    return seen


if sample() is None:
    sys.exit(3)          # older perception build, no metrics endpoint

baseline, producing, last = {}, set(), {}
deadline = time.monotonic() + timeout
while True:
    current = sample()
    if current is None:
        sys.exit(3)
    for sensor, frames in current.items():
        if sensor not in baseline:
            baseline[sensor] = frames
        elif frames > baseline[sensor]:
            producing.add(sensor)
    if all(cam in producing for cam in wanted):
        sys.exit(0)
    last = current
    if time.monotonic() >= deadline:
        break
    time.sleep(2)

for cam in wanted:
    if cam in producing:
        continue
    print(("STATIC " if cam in baseline else "UNSEEN ") + cam)
for sensor in sorted(last or {}):
    print("OBS %s frame_number=%s" % (sensor, last[sensor]))
sys.exit(1)
PY
)" || rc=$?

  if (( rc == 0 || rc == 3 )); then
    return 0
  fi

  echo >&2
  echo "ERROR: the perception service accepted these streams but they are not producing frames" >&2
  echo "       after ${ACTIVATION_TIMEOUT}s:" >&2
  printf '%s\n' "$out" | grep -v '^OBS ' | sed 's/^/         /' >&2
  echo "       Their frame_number never advanced in /api/v1/metrics: STATIC means the" >&2
  echo "       stream was sampled but frozen, UNSEEN that it was never sampled at all." >&2
  if printf '%s\n' "$out" | grep -q '^OBS '; then
    echo "       Last /api/v1/metrics sample:" >&2
    printf '%s\n' "$out" | sed -n 's/^OBS /         /p' >&2
  fi
  echo "       A stream re-added into a running batch has to realign with the sources" >&2
  echo "       already going, which usually takes under 15s but is not bounded. If the" >&2
  echo "       sample above looks healthy, re-check before treating this as a failure:" >&2
  echo "         curl -s ${BASE}/api/v1/metrics" >&2
  echo "       A source that cannot decode stalls the whole batch, so expect" >&2
  echo "       \"Active sources : 0\" in the perception log until it is removed." >&2
  echo "       Check the RTSP URL is reachable, then re-add. Use --activation-timeout to" >&2
  echo "       wait longer, or 0 to skip this check." >&2
  return 1
}

# ── 2. Add each stream ───────────────────────────────────────────────────────
echo "── Adding ${#STREAMS[@]} stream(s) (delay=${DELAY}s)"
idx=0
ADDED_CAMS=()
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
  # After the config check: that one is local and instant, this one costs a
  # connect attempt, and an unconfigured camera_id is the more basic mistake.
  probe_rc=0; probe_err="$(probe_rtsp_endpoint "$url" 2>&1 >/dev/null)" || probe_rc=$?
  if (( probe_rc == 1 )); then
    echo "   ✗ nothing is listening at ${probe_err}" >&2
    echo "     The REST API would accept this stream and never activate it, holding a" >&2
    echo "     source slot. Check the URL, or pass --no-url-check to add it anyway." >&2
    exit 2
  elif (( probe_rc == 2 )); then
    echo "   ⚠ no answer from ${probe_err}; adding anyway" >&2
    echo "     If the source never starts it is reported after the add." >&2
  fi
  post_sensor "$cam" "$url" camera_add || exit 2
  ADDED_CAMS+=("$cam")
  show_registration_progress "${ADDED_CAMS[@]}"
  idx=$((idx + 1))
  (( idx < ${#STREAMS[@]} )) && sleep "$DELAY"
done

# ── 3. Report ────────────────────────────────────────────────────────────────
echo
echo "── Reading stream-info"
show_stream_info

verify_streams_active "${ADDED_CAMS[@]}" || exit 2
echo
echo "Check per-source FPS:  docker logs vss-rtvi-cv-mv3dt 2>&1 | grep -A$(( ${#STREAMS[@]} + 1 )) '\\*\\*PERF' | tail -8"
