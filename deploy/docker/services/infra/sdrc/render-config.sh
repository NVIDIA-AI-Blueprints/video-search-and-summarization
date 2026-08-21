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
# Usage (init container — render every *.tmpl in a directory in-place):
#   sh /render-config.sh /tmpl
#
# Usage (init container — single file):
#   sh /render-config.sh /tmpl/config.yml.tmpl /out/config.yml
#
# Defaults (host):
#   TMPL = <script_dir>/configs/config.yml.tmpl
#   OUT  = <script_dir>/configs/config.yml
#
# The *.tmpl discovery loop lives here (not in docker-compose.yaml) so nested
# Compose includes cannot interpolate shell loop variables like $tmpl away
# before the container starts.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Allowlist of variables to expand. Add new placeholders here when the
# template starts referencing additional variables.
ALLOWED_VARS='${HOST_IP} ${NUM_STREAMS} ${NUM_SENSORS} ${ALERTS_2D_ENABLE} ${RTVI_CV_WDM_KFK_ENABLE} ${VST_USE_SDRC_ENABLE}'

prepare_env() {
  : "${HOST_IP:?HOST_IP must be set (export it or pass it inline: HOST_IP=1.2.3.4 $0)}"
  if [ -z "${NUM_STREAMS:-}" ] && [ -z "${NUM_SENSORS:-}" ]; then
    echo "render-config.sh: NUM_STREAMS or NUM_SENSORS must be set" >&2
    exit 1
  fi
  NUM_STREAMS="${NUM_STREAMS:-$NUM_SENSORS}"
  NUM_SENSORS="${NUM_SENSORS:-$NUM_STREAMS}"

  # Auto-derive ALERTS_2D_ENABLE from the warehouse variant selector: true iff
  # the active workload is the 2D agents profile (the only variant that runs
  # docker-workload-alerts-2d).
  #
  # A pre-set ALERTS_2D_ENABLE is left untouched so callers can force one. When
  # BP_PROFILE/MODE are absent (for direct host invocations as documented in the
  # header), the fallback below still guarantees envsubst receives a well-formed
  # boolean, so the rendered template never emits `enable: ` (null).
  if [ -z "${ALERTS_2D_ENABLE:-}" ]; then
    case "${BP_PROFILE:-}:${MODE:-}" in
      bp_wh:2d) ALERTS_2D_ENABLE=true ;;
      *)        ALERTS_2D_ENABLE=false ;;
    esac
  fi
  ALERTS_2D_ENABLE="${ALERTS_2D_ENABLE:-false}"
  VST_USE_SDRC_ENABLE="${VST_USE_SDRC_ENABLE:-${VST_USE_SDRC:-false}}"

  # RT-CV SDR consumes Kafka notifications for Kafka-backed warehouse profiles and
  # Redis stream events for Redis-backed profiles. Derive this from STREAM_TYPE so
  # native docker compose and blueprint-deploy render the same workload config.
  if [ -z "${RTVI_CV_WDM_KFK_ENABLE:-}" ]; then
    case "${STREAM_TYPE:-}" in
      redis) RTVI_CV_WDM_KFK_ENABLE=false ;;
      *)     RTVI_CV_WDM_KFK_ENABLE=true ;;
    esac
  fi
  RTVI_CV_WDM_KFK_ENABLE="${RTVI_CV_WDM_KFK_ENABLE:-true}"
  export NUM_STREAMS NUM_SENSORS ALERTS_2D_ENABLE RTVI_CV_WDM_KFK_ENABLE VST_USE_SDRC_ENABLE

  if ! command -v envsubst >/dev/null 2>&1; then
    if command -v apk >/dev/null 2>&1; then
      apk add --no-cache gettext >/dev/null
    else
      echo "render-config.sh: 'envsubst' not found. Install gettext (e.g. 'sudo apt-get install gettext-base')." >&2
      exit 1
    fi
  fi
}

render_one() {
  tmpl="$1"
  out="$2"

  if [ ! -f "$tmpl" ]; then
    echo "render-config.sh: template not found: $tmpl" >&2
    exit 1
  fi

  mkdir -p "$(dirname "$out")"

  tmp_out="$(mktemp "${out}.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -f '$tmp_out'" EXIT INT TERM

  envsubst "$ALLOWED_VARS" <"$tmpl" >"$tmp_out"
  mv "$tmp_out" "$out"
  trap - EXIT INT TERM

  echo "render-config.sh: wrote $out (from $tmpl)"
}

render_dir() {
  dir="$1"
  rendered=0
  found=0

  for tmpl in "$dir"/*.tmpl; do
    [ -e "$tmpl" ] || continue
    found=1
    base="$(basename "$tmpl" .tmpl)"
    render_one "$tmpl" "$dir/$base"
    rendered=$((rendered + 1))
  done

  if [ "$found" -eq 0 ]; then
    echo "render-config.sh: no *.tmpl files found in $dir" >&2
    exit 1
  fi

  echo "render-config.sh: rendered $rendered template(s) in $dir"
}

prepare_env

# Directory mode (init container): render every *.tmpl in-place.
if [ "$#" -eq 1 ] && [ -d "$1" ]; then
  render_dir "$1"
  exit 0
fi

# Single-file mode (host default or explicit paths).
TMPL="${1:-$SCRIPT_DIR/configs/config.yml.tmpl}"
OUT="${2:-$SCRIPT_DIR/configs/config.yml}"
render_one "$TMPL" "$OUT"
