#!/bin/sh
# Render configs/config.yml from configs/config.yml.tmpl by substituting an
# allowlist of environment variables. Any other literal $... in the template
# is preserved as-is.
#
# Runs both on the host and inside a small Alpine init container (see the
# 'render-config' service in docker-compose.yaml). POSIX sh on purpose.
#
# Usage (host):
#   HOST_IP=127.0.0.1 ./render-config.sh
#   HOST_IP=127.0.0.1 NUM_STREAMS=8 ./render-config.sh path/to/tmpl path/to/out
#   HOST_IP=127.0.0.1 NUM_SENSORS=8 ./render-config.sh path/to/tmpl path/to/out
#
# Usage (init container):
#   sh /render-config.sh /tmpl/config.yml.tmpl /out/config.yml
#
# Defaults (host):
#   TMPL = <script_dir>/configs/config.yml.tmpl
#   OUT  = <script_dir>/configs/config.yml
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMPL="${1:-$SCRIPT_DIR/configs/config.yml.tmpl}"
OUT="${2:-$SCRIPT_DIR/configs/config.yml}"

# Allowlist of variables to expand. Add new placeholders here when the
# template starts referencing additional variables.
ALLOWED_VARS='${HOST_IP} ${NUM_STREAMS} ${NUM_SENSORS}'

if [ ! -f "$TMPL" ]; then
  echo "render-config.sh: template not found: $TMPL" >&2
  exit 1
fi

: "${HOST_IP:?HOST_IP must be set (export it or pass it inline: HOST_IP=1.2.3.4 $0)}"
if [ -z "${NUM_STREAMS:-}" ] && [ -z "${NUM_SENSORS:-}" ]; then
  echo "render-config.sh: NUM_STREAMS or NUM_SENSORS must be set" >&2
  exit 1
fi
NUM_STREAMS="${NUM_STREAMS:-$NUM_SENSORS}"
NUM_SENSORS="${NUM_SENSORS:-$NUM_STREAMS}"
export NUM_STREAMS NUM_SENSORS

if ! command -v envsubst >/dev/null 2>&1; then
  if command -v apk >/dev/null 2>&1; then
    apk add --no-cache gettext >/dev/null
  else
    echo "render-config.sh: 'envsubst' not found. Install gettext (e.g. 'sudo apt-get install gettext-base')." >&2
    exit 1
  fi
fi

mkdir -p "$(dirname "$OUT")"

TMP_OUT="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$TMP_OUT"' EXIT INT TERM

envsubst "$ALLOWED_VARS" <"$TMPL" >"$TMP_OUT"
mv "$TMP_OUT" "$OUT"
trap - EXIT INT TERM

echo "render-config.sh: wrote $OUT (from $TMPL)"
