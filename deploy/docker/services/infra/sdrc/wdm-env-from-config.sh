#!/bin/sh
# Reads compose/config.yml (mounted at /config.yml), writes /env/wdm.env for sdr-envoy-proxy
# and wait-for-redis (WDM_WL_REDIS_* from first enable:true workload), and
# /env/docker-workload-containers.txt for wait-for-docker-workloads (all docker-type workloads).
# Also writes /env/config.yml: a copy of /config.yml with ${HOST_IP} expanded,
# for sdr-controller to mount (sdr-mw-l does not expand env vars when loading YAML).
set -e

OUT="${WDM_ENV_OUT:-/env/wdm.env}"
EXPANDED_CONFIG="${EXPANDED_CONFIG_OUT:-/env/config.yml}"
mkdir -p "$(dirname "$OUT")"

# envsubst lives in the gettext package; install on alpine if missing.
if ! command -v envsubst >/dev/null 2>&1; then
  apk add --no-cache gettext >/dev/null
fi

# Expand only the variables we want (avoid clobbering literal $... in the YAML).
: "${HOST_IP:?HOST_IP must be set}"
if [ -z "${NUM_STREAMS:-}" ] && [ -z "${NUM_SENSORS:-}" ]; then
  echo "NUM_STREAMS or NUM_SENSORS must be set" >&2
  exit 1
fi
NUM_STREAMS="${NUM_STREAMS:-$NUM_SENSORS}"
NUM_SENSORS="${NUM_SENSORS:-$NUM_STREAMS}"
export NUM_STREAMS NUM_SENSORS
EXPAND_VARS='${HOST_IP} ${NUM_STREAMS} ${NUM_SENSORS}'
envsubst "$EXPAND_VARS" < /config.yml > "$EXPANDED_CONFIG"

ENABLED_LEN=$(yq '[. | to_entries[] | select(.value != null and (.value | tag) == "!!map" and .value.enable == true)] | length' /config.yml)
CONT_LIST="/env/docker-workload-containers.txt"
if [ "$ENABLED_LEN" -eq 0 ]; then
  printf '%s\n' '# No enable:true workload in config.yml; envoy entrypoint uses built-in defaults.' >"$OUT"
  : >"$CONT_LIST"
  exit 0
fi

WORKLOAD=$(yq '[. | to_entries[] | select(.value != null and (.value | tag) == "!!map" and .value.enable == true)] | .[0].value' /config.yml)

# Substitute ${HOST_IP} / {HOST_IP} placeholders coming from config.yml.
# yq returns YAML values verbatim — no shell expansion happens — so we do it here.
expand() {
  v=$1
  v=$(printf '%s' "$v" | sed -e "s|\${HOST_IP}|${HOST_IP}|g" -e "s|{HOST_IP}|${HOST_IP}|g")
  printf '%s' "$v"
}

RS=$(expand "$(echo "$WORKLOAD" | yq -r '.WDM_WL_REDIS_SERVER // "redis" | tostring')")
RP=$(expand "$(echo "$WORKLOAD" | yq -r '.WDM_WL_REDIS_PORT // "6379" | tostring')")

KFK=$(expand "$(echo "$WORKLOAD" | yq -r '.WDM_KFK_BOOTSTRAP_URL // "" | tostring')")
KHOST=${KFK%%:*}
[ -z "$KHOST" ] && KHOST=10.127.22.222

DHOST=$(expand "$(echo "$WORKLOAD" | yq -r '.WDM_ENVOY_UPSTREAM_HOST // "" | tostring')")
[ -z "$DHOST" ] && DHOST=$KHOST

XHOST=$(expand "$(echo "$WORKLOAD" | yq -r '.WDM_ENVOY_XDS_HOST // "" | tostring')")
[ -z "$XHOST" ] && XHOST=$KHOST

DPORT=$(echo "$WORKLOAD" | yq -r '.WDM_ENVOY_DIRECT_PORT // 5005 | tostring')
XPORT=$(echo "$WORKLOAD" | yq -r '.WDM_ENVOY_XDS_PORT // 5005 | tostring')

{
  echo "WDM_WL_REDIS_SERVER=$RS"
  echo "WDM_WL_REDIS_PORT=$RP"
  echo "WDM_DIRECT_UPSTREAM_HOST=$DHOST"
  echo "WDM_XDS_CLUSTER_HOST=$XHOST"
  echo "WDM_DIRECT_UPSTREAM_PORT=$DPORT"
  echo "WDM_XDS_CLUSTER_PORT=$XPORT"
} >"$OUT"

# One name per line: Docker containers from every top-level enable:true workload with WDM_CLUSTER_TYPE: docker.
yq '[. | to_entries[] | select(.value != null and (.value | tag) == "!!map" and .value.enable == true and (.value.WDM_CLUSTER_TYPE // "") == "docker" and .value.WDM_CLUSTER_CONTAINER_NAMES != null) | .value.WDM_CLUSTER_CONTAINER_NAMES | fromjson | .[]] | unique | .[]' -r /config.yml >"${CONT_LIST}.tmp"
mv "${CONT_LIST}.tmp" "$CONT_LIST"
