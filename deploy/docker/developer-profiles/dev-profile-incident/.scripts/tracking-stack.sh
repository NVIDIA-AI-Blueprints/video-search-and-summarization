#!/usr/bin/env bash
#
# tracking-stack.sh - runs ON THE VM. Owns the person-tracking stack that
# start.sh brings up with ENABLE_TRACKING=true: NvStreamer (file upload and RTSP
# replay), DeepStream perception (RT-DETR warehouse detector + NvDCF tracker),
# Kafka, Logstash, Elasticsearch and the video-analytics API (/frames).
#
# Usage:
#   tracking-stack.sh start               stage the RT-DETR model if missing, then bring the stack up and wait for ready
#   tracking-stack.sh stop                stop the services (keeps containers, volumes and built TensorRT engines)
#   tracking-stack.sh status              one health line per probe; exit 1 if any probe fails
#   tracking-stack.sh restart-perception  restart only perception-2d-fusion and wait for ready
#
# Overrides:
#   TRACKING_ENV_FILE         env file (default: <profile>/generated.env.remote)
#   TRACKING_COMPOSE_PROJECT  compose project (default: COMPOSE_PROJECT_NAME in the env file, else mdx)
#   TRACKING_READY_TIMEOUT    seconds to wait for perception to be ready (default: 900; the first start builds TensorRT engines)
#
# Containers are only stopped, never removed (no compose down, no -v). Everything is pinned to GPU 0.
# The stock compose uses fixed container names (kafka, elasticsearch, vss-rtvi-cv, ...), so only one
# stack with these services can exist on the host.

set -euo pipefail

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export NVIDIA_VISIBLE_DEVICES="${NVIDIA_VISIBLE_DEVICES:-0}"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKER_DIR="$(cd "${PROFILE_DIR}/../.." && pwd)"
ENV_FILE="${TRACKING_ENV_FILE:-${PROFILE_DIR}/generated.env.remote}"
READY_TIMEOUT="${TRACKING_READY_TIMEOUT:-900}"

# Service names, in start order (batches are brought up and awaited in turn).
BATCH_INFRA=(kafka kafka-topic-init-container broker-health-check elasticsearch elasticsearch-init-container)
BATCH_MEDIA=(logstash nvstreamer-2d-fusion perception-2d-init)
BATCH_PERCEPTION=(perception-2d-fusion vss-video-analytics-api-fusion)
# Long-running services (the init containers exit by design and are not stopped).
STOP_SERVICES=(vss-video-analytics-api-fusion perception-2d-fusion nvstreamer-2d-fusion logstash elasticsearch kafka)

say() { echo "[tracking] $*"; }
fail() { echo "[tracking] ERROR: $*" >&2; exit 1; }

# Reads one key from the env file without sourcing it (it holds secrets).
env_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1 | sed -e "s/[[:space:]]*#.*$//" -e "s/^[\"']//" -e "s/[\"']$//"
}

setup() {
  [ -f "$ENV_FILE" ] || fail "env file not found: ${ENV_FILE} (see .docs/incident-profile-operations.md section 5)."
  [ -f "${DOCKER_DIR}/compose.yml" ] || fail "no compose.yml in ${DOCKER_DIR}."
  PROJECT="${TRACKING_COMPOSE_PROJECT:-$(env_value COMPOSE_PROJECT_NAME)}"
  PROJECT="${PROJECT:-mdx}"
  DOCKER=(docker)
  if ! timeout 30 docker info >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null && timeout 30 sudo -n docker info >/dev/null 2>&1; then
      DOCKER=(sudo -n docker)
    else
      fail "docker is not usable for $(id -un): the daemon is down or this user is not in the docker group."
    fi
  fi
  COMPOSE=("${DOCKER[@]}" compose -f compose.yml -f "developer-profiles/dev-profile-incident/compose.override.yml" \
    -f "developer-profiles/dev-profile-incident/compose.tracking.yml" --env-file "$ENV_FILE" -p "$PROJECT")
}

compose() { (cd "$DOCKER_DIR" && "${COMPOSE[@]}" "$@"); }

# Kafka advertises HOST_IP and NvStreamer builds its RTSP URLs from it, so it must be the VM's current
# address. Exported (the shell wins over --env-file) so the secrets file is never rewritten here.
set_host_ip() {
  local vm_ip env_ip
  vm_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)"
  env_ip="$(env_value HOST_IP)"
  [ -n "$vm_ip" ] || vm_ip="$env_ip"
  [ -n "$vm_ip" ] || fail "cannot determine the VM address and HOST_IP is not set in ${ENV_FILE}."
  if [ -n "$env_ip" ] && [ "$vm_ip" != "$env_ip" ]; then
    say "HOST_IP in the env file is ${env_ip} but this VM is ${vm_ip}; using ${vm_ip} for this run (the file is not changed)."
  fi
  export HOST_IP="$vm_ip"
  say "HOST_IP=${HOST_IP}"
}

# The compose volumes for Elasticsearch, Kafka and the API are bind mounts of $VSS_DATA_DIR/data_log/...
# and fail to mount when the directory is missing (dev-profile.sh creates them). Idempotent.
stage_dirs() {
  local data_dir d
  data_dir="$(env_value VSS_DATA_DIR)"
  [ -n "$data_dir" ] || fail "VSS_DATA_DIR is not set in ${ENV_FILE}."
  for d in analytics_cache elastic/data elastic/logs kafka vss_video_analytics_api nvstreamer/vst_data; do
    mkdir -p "${data_dir}/data_log/${d}"
    chmod 777 "${data_dir}/data_log/${d}" 2>/dev/null || true
  done
}

# Stage rtdetr_warehouse_v1.0.2.fp16.onnx into $VSS_DATA_DIR/models (no container downloads it; without
# it vss-rtvi-cv exits at start). Same download as dev-profile.sh. Idempotent.
stage_model() {
  local data_dir models onnx key tmp
  data_dir="$(env_value VSS_DATA_DIR)"
  [ -n "$data_dir" ] || fail "VSS_DATA_DIR is not set in ${ENV_FILE}."
  models="${data_dir}/models"
  onnx="${models}/rtdetr_warehouse_v1.0.2.fp16.onnx"
  if [ -s "$onnx" ]; then
    say "RT-DETR model present (${onnx})."
    return 0
  fi
  command -v ngc >/dev/null 2>&1 || fail "RT-DETR model missing and the 'ngc' CLI is not on PATH (looked in ${HOME}/.local/bin). Install ngc-cli or stage ${onnx} by hand."
  key="$(env_value NGC_CLI_API_KEY)"
  [ -n "$key" ] || fail "RT-DETR model missing and NGC_CLI_API_KEY is not set in ${ENV_FILE}."
  mkdir -p "$models"
  tmp="$(mktemp -d)"
  say "downloading the RT-DETR warehouse model from NGC (251 MB)..."
  if ! (cd "$tmp" && env NGC_CLI_API_KEY="$key" ngc registry model download-version \
        nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2 --org nvidia >/dev/null); then
    rm -rf "$tmp"
    fail "ngc download of nvidia/tao/rtdetr_2d_warehouse:deployable_rn50_v1.0.2 failed (key expired or no network)."
  fi
  if [ ! -s "${tmp}/rtdetr_2d_warehouse_vdeployable_rn50_v1.0.2/rtdetr_warehouse_v1.0.2.fp16.onnx" ]; then
    rm -rf "$tmp"
    fail "the download did not contain rtdetr_warehouse_v1.0.2.fp16.onnx."
  fi
  mv "${tmp}/rtdetr_2d_warehouse_vdeployable_rn50_v1.0.2/rtdetr_warehouse_v1.0.2.fp16.onnx" "$onnx"
  rm -rf "$tmp"
  chmod 777 "$models" 2>/dev/null || true
  chmod 666 "$onnx" 2>/dev/null || true
  say "RT-DETR model staged (${onnx})."
}

# wait_for <what> <timeout-s> <command...>: poll until the command succeeds.
wait_for() {
  local what="$1" timeout="$2" deadline
  shift 2
  deadline=$((SECONDS + timeout))
  until "$@" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      fail "${what} not ready after ${timeout}s."
    fi
    sleep 3
  done
  say "${what} ready."
}

ds_ready() { curl -sf --max-time 5 http://localhost:9000/api/v1/ready | grep -q '"YES"' && curl -sf --max-time 5 http://localhost:9000/api/v1/stream/get-stream-info; }

# A container name used by another compose project cannot be reused (fixed names).
check_name_conflicts() {
  local name proj
  for name in kafka elasticsearch logstash vss-vios-nvstreamer vss-rtvi-cv vss-video-analytics-api; do
    proj="$("${DOCKER[@]}" inspect --type container -f '{{index .Config.Labels "com.docker.compose.project"}}' "$name" 2>/dev/null || true)"
    if [ -n "$proj" ] && [ "$proj" != "$PROJECT" ]; then
      fail "container '${name}' already exists under compose project '${proj}' (this run uses '${PROJECT}'); the stock compose uses fixed names, so stop and remove that stack's containers first."
    fi
  done
}

cmd_start() {
  setup
  set_host_ip
  stage_dirs
  stage_model
  check_name_conflicts
  say "bringing up the tracking stack under compose project '${PROJECT}'..."
  compose up -d --yes "${BATCH_INFRA[@]}"
  compose up -d --yes "${BATCH_MEDIA[@]}"
  wait_for "elasticsearch (:9200)" 180 curl -sf --max-time 5 http://localhost:9200
  wait_for "NvStreamer (:31000)" 180 curl -sf --max-time 5 http://localhost:31000/api/v1/sensor/streams
  compose up -d --yes "${BATCH_PERCEPTION[@]}"
  wait_for "DeepStream perception (:9000)" "$READY_TIMEOUT" ds_ready
  wait_for "video-analytics API (:8081)" 120 curl -sf --max-time 5 http://localhost:8081/livez
  say "tracking stack is up."
}

cmd_stop() {
  setup
  # Stop only what exists, by name; never remove anything.
  local existing=() name
  for name in "${STOP_SERVICES[@]}"; do
    if [ -n "$(compose ps -q "$name" 2>/dev/null)" ]; then existing+=("$name"); fi
  done
  if [ "${#existing[@]}" -eq 0 ]; then
    say "no tracking containers under project '${PROJECT}'."
    return 0
  fi
  say "stopping ${existing[*]} (containers, volumes and engines are kept)..."
  compose stop "${existing[@]}"
}

cmd_status() {
  local rc=0 out
  echo "perception :9000   $(curl -s --max-time 5 http://localhost:9000/api/v1/stream/get-stream-info | tr -d "[:space:]" || echo UNREACHABLE)"
  echo "analytics  :8081   $(curl -s --max-time 5 http://localhost:8081/livez || echo UNREACHABLE)"
  echo "elastic    :9200   $(curl -s --max-time 5 'http://localhost:9200/_cat/indices/mdx-*' | tr '\n' ' ' || echo UNREACHABLE)"
  echo "nvstreamer :31000  $(curl -s --max-time 5 http://localhost:31000/api/v1/sensor/streams || echo UNREACHABLE)"
  out="$(curl -s --max-time 5 http://localhost:9000/api/v1/stream/get-stream-info || true)"
  case "$out" in *stream-count*) ;; *) rc=1 ;; esac
  curl -sf --max-time 5 http://localhost:8081/livez >/dev/null || rc=1
  curl -sf --max-time 5 http://localhost:31000/api/v1/sensor/streams >/dev/null || rc=1
  curl -sf --max-time 5 http://localhost:9200 >/dev/null || rc=1
  return "$rc"
}

cmd_restart_perception() {
  setup
  set_host_ip
  say "restarting perception-2d-fusion only..."
  # up -d starts it when it is Exited (third crash) and recreates it only if its config changed.
  compose up -d --yes --no-deps perception-2d-fusion
  compose restart perception-2d-fusion
  wait_for "DeepStream perception (:9000)" "$READY_TIMEOUT" ds_ready
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  restart-perception) cmd_restart_perception ;;
  *)
    echo "Usage: tracking-stack.sh start|stop|status|restart-perception" >&2
    exit 1
    ;;
esac
