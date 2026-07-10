#!/usr/bin/env bash
#
# stage-configs.sh — assemble the per-run DeepStream config dir the perception
# container mounts (generated/configs). Run this before `docker compose up`.
#
# The staged dir is configs/* (the sample set, including the single
# ds-main-config-mv3dt.txt) overlaid with the generated sparse
# pub_sub_info_config.yml when scripts/generate-configs.sh has been run.
# The tracker config (TRACKER_CONFIG, default the bundled sample) is staged
# with its ObjectModelProjection.cameraModelFilepath map rewritten to the
# cameras in generated/camInfo. The staged main config is then edited in place:
#   - batch-size / max-batch-size = NUM_CAMS
#   - tiled-display grid sized to NUM_CAMS
#   - REST http-port = DS_HTTP_PORT
#   - kafka sink conn-str = KAFKA_BOOTSTRAP host;port;RAW_TOPIC
#   - [sink0] on-screen display: enabled with OSD=1 (needs an X display), else off
#   - RT-DETR model-engine-file batch suffix = NUM_CAMS
#
# Usage:  [OSD=0|1] [TRACKER_CONFIG=/path/to/tracker.yml] ./scripts/stage-configs.sh
# Reads NUM_CAMS / DS_HTTP_PORT / KAFKA_BOOTSTRAP / RAW_TOPIC from docker/.env
# (already-exported environment values take precedence).
#   TRACKER_CONFIG  base tracker config to stage (default:
#                   configs/ds-mv3dt-tracker-config.yml). Point this at your
#                   own tracker config to use it instead of the sample.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# docker/.env provides defaults; anything already in the environment wins.
if [ -f "$ROOT/docker/.env" ]; then
  while IFS='=' read -r k v; do
    [[ "$k" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    [ -n "${!k:-}" ] || eval "export $k=\"$v\""
  done < <(grep -E '^[A-Z_]+=' "$ROOT/docker/.env")
fi

NUM_CAMS="${NUM_CAMS:?NUM_CAMS not set (see docker/.env)}"
DS_HTTP_PORT="${DS_HTTP_PORT:-9000}"
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
RAW_TOPIC="${RAW_TOPIC:-mdx-raw}"
OSD="${OSD:-0}"
TRACKER_CONFIG="${TRACKER_CONFIG:-$ROOT/configs/ds-mv3dt-tracker-config.yml}"
[ -f "$TRACKER_CONFIG" ] || { echo "ERROR: tracker config not found: $TRACKER_CONFIG" >&2; exit 1; }

KAFKA_HOST="${KAFKA_BOOTSTRAP%%:*}"
KAFKA_PORT_ONLY="${KAFKA_BOOTSTRAP##*:}"

STAGE="$ROOT/generated/configs"
GEN="$ROOT/generated"

# Adaptive tiled-display grid: rows = floor(sqrt(b)), cols = ceil(b/rows) — a
# landscape rectangle that fits all sources with the fewest empty tiles.
read -r ROWS COLS < <(python3 -c "import math,sys;b=max(1,int(sys.argv[1]));r=max(1,math.isqrt(b));print(r, -(-b//r))" "$NUM_CAMS")

echo "── Staging configs → $STAGE  (NUM_CAMS=$NUM_CAMS grid=${ROWS}x${COLS} port=$DS_HTTP_PORT kafka=$KAFKA_HOST:$KAFKA_PORT_ONLY OSD=$OSD)"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$ROOT"/configs/* "$STAGE/"
rm -f "$STAGE/mosquitto.conf"   # broker conf, not a DeepStream config

# Overlay generated files (scripts/generate-configs.sh outputs), when present.
if [ -d "$GEN" ]; then
  for f in pub_sub_info_config.yml; do
    [ -f "$GEN/$f" ] && { cp "$GEN/$f" "$STAGE/$f"; echo "   using generated $f"; }
  done
fi

# camInfo must exist for the tracker + the ds-start pub/sub fallback.
if [ ! -d "$ROOT/generated/camInfo" ] || ! ls "$ROOT/generated/camInfo"/*.yml >/dev/null 2>&1; then
  echo "   ⚠ no camInfo at generated/camInfo — run scripts/generate-configs.sh first" >&2
else
  # Stage the tracker config (TRACKER_CONFIG or the bundled sample) with its
  # cameraModelFilepath map rewritten to the generated cameras (the sample
  # hardcodes the 4-cam warehouse names).
  echo "   staging tracker config: $TRACKER_CONFIG (cameraModelFilepath → generated/camInfo)"
  python3 - "$TRACKER_CONFIG" "$ROOT/generated/camInfo" "$STAGE/ds-mv3dt-tracker-config.yml" <<'PY'
import re, sys
from pathlib import Path

base, caminfo_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
cams = sorted(p.stem for p in caminfo_dir.glob("*.yml"))
assert cams, f"no camInfo files in {caminfo_dir}"

lines = base.read_text().splitlines(keepends=True)
result, i = [], 0
while i < len(lines):
    line = lines[i]
    result.append(line)
    m = re.match(r"^(\s*)cameraModelFilepath:\s*$", line)
    i += 1
    if not m:
        continue
    indent = m.group(1)
    # skip the existing (more-indented) map entries
    while i < len(lines) and (lines[i].strip() == "" or
                              (len(lines[i]) - len(lines[i].lstrip())) > len(indent)):
        if lines[i].strip() == "" :
            break
        i += 1
    for cam in cams:
        result.append(f"{indent}  {cam}: /tmp/camInfo/{cam}.yml\n")
out.write_text("".join(result))
print(f"   {len(cams)} cameras: {', '.join(cams[:6])}{' ...' if len(cams) > 6 else ''}")
PY
fi

# RT-DETR engine file name follows the batch size (TensorRT builds it on first
# run if missing — a cold build takes minutes).
ONNX=$(sed -nE 's/^[[:space:]]*onnx-file:[[:space:]]*([^[:space:]]+).*/\1/p' "$STAGE/ds-pgie-config.yml" | head -1)
[ -n "$ONNX" ] && sed -i -E "s#(model-engine-file:[[:space:]]*).*#\1${ONNX}_b${NUM_CAMS}_gpu0_fp16.engine#" "$STAGE/ds-pgie-config.yml"

# Edit the staged main config in place (section-aware: both sinks use "enable=").
MAIN="$STAGE/ds-main-config-mv3dt.txt"
awk -v rows="$ROWS" -v cols="$COLS" -v b="$NUM_CAMS" -v port="$DS_HTTP_PORT" \
    -v osd="$OSD" -v khost="$KAFKA_HOST" -v kport="$KAFKA_PORT_ONLY" -v ktopic="$RAW_TOPIC" '
  /^\[/ { section=$0 }
  /^rows=/           { print "rows=" rows; next }
  /^columns=/        { print "columns=" cols; next }
  /^max-batch-size=/ { print "max-batch-size=" b; next }
  /^batch-size=/     { print "batch-size=" b; next }
  /^http-port=/      { print "http-port=" port; next }
  /^msg-broker-conn-str=/ { print "msg-broker-conn-str=" khost ";" kport ";" ktopic; next }
  section=="[sink0]" && /^enable=/ { print (osd=="1" ? "enable=1" : "enable=0"); next }
  section=="[sink1]" && /^enable=/ { print "enable=1"; next }
  { print }
' "$MAIN" > "$MAIN.tmp" && mv "$MAIN.tmp" "$MAIN"

# The container's runtime user must be able to read the bind-mounted configs
# regardless of the host umask (e.g. 027 leaves them group-only).
chmod -R o+rX "$ROOT/generated"

echo "── Staged. Launch from the docker/ directory:"
echo "   cd docker && COMPOSE_PROFILES=mosquitto,kafka docker compose up -d   # bundled brokers"
echo "   cd docker && docker compose up -d                                    # own brokers"
