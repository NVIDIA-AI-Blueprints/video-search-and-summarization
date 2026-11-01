#!/usr/bin/env bash
#
# native-services.sh — Control script for managing native processes on kwanz-ws
# (vss-agent, video-analytics-api, behavior-analytics)
#
# Usage:
#   ./native-services.sh start [service...]
#   ./native-services.sh stop [service...]
#   ./native-services.sh restart [service...]
#   ./native-services.sh status [--porcelain] [service...]
#   ./native-services.sh logs [service] [-f]
#
# Services supported:
#   - vss-agent (default port 8000)
#   - video-analytics-api (default port 8081)
#   - behavior-analytics (default port 8080)
#
# Specifying "all" targets all 3 services.
# Specifying "analytics" targets video-analytics-api and behavior-analytics.
# Default 'start', 'restart', and 'status' without arguments target vss-agent (plus analytics if ENABLE_ANALYTICS=true).

set -euo pipefail
umask 002

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"

# Ensure common user binary locations are on PATH
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:${PATH}"

# Pin all native processes to GPU 0 to protect GPU 1 for co-tenants
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export NVIDIA_VISIBLE_DEVICES="${NVIDIA_VISIBLE_DEVICES:-0}"

# Directory for PID files and log output
RUN_DIR="${VSS_RUN_DIR:-${REPO_ROOT}/.run}"
mkdir -p "${RUN_DIR}"
chmod g+rwx "${RUN_DIR}" 2>/dev/null || true

LOCK_FILE="${RUN_DIR}/native-services.lock"
if [ -f "${LOCK_FILE}" ] && [ ! -w "${LOCK_FILE}" ]; then
  rm -f "${LOCK_FILE}"
fi
touch "${LOCK_FILE}" 2>/dev/null || true
chmod g+rw "${LOCK_FILE}" 2>/dev/null || true

# -----------------------------------------------------------------------------
# Concurrency & Locking Helpers
# -----------------------------------------------------------------------------
with_lock() {
  local lock_file="${LOCK_FILE:-${RUN_DIR}/native-services.lock}"
  if [ "${_VSS_LOCK_HELD:-0}" = "1" ]; then
    "$@"
    return $?
  fi

  if [ -f "${lock_file}" ] && [ ! -w "${lock_file}" ]; then
    rm -f "${lock_file}"
  fi
  if [ ! -f "${lock_file}" ]; then
    touch "${lock_file}" 2>/dev/null || true
  fi
  chmod g+rw "${lock_file}" 2>/dev/null || true

  if command -v flock >/dev/null 2>&1; then
    (
      # shellcheck disable=SC2030
      export _VSS_LOCK_HELD=1
      flock -x 200
      "$@"
    ) 200>>"${lock_file}"
  else
    # shellcheck disable=SC2031
    export _VSS_LOCK_HELD=1
    "$@"
  fi
}

write_pid_atomic() {
  local svc="$1"
  local pid="$2"
  local pid_file="${RUN_DIR}/${svc}.pid"
  local tmp_file
  tmp_file="$(mktemp "${RUN_DIR}/${svc}.pid.XXXXXX" 2>/dev/null || echo "${pid_file}.$$.${RANDOM}")"
  echo "${pid}" > "${tmp_file}"
  chmod 664 "${tmp_file}" 2>/dev/null || chmod g+rw "${tmp_file}" 2>/dev/null || true
  mv -f "${tmp_file}" "${pid_file}"
}

remove_pid_if_match() {
  local svc="$1"
  local expected_pid="$2"
  local pid_file="${RUN_DIR}/${svc}.pid"
  if [ -f "${pid_file}" ]; then
    local current_pid
    current_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [ "${current_pid}" = "${expected_pid}" ]; then
      rm -f "${pid_file}"
    fi
  fi
}

# -----------------------------------------------------------------------------
# Environment loading
# -----------------------------------------------------------------------------
load_env() {
  local env_file="${ENV_FILE:-}"
  if [ -z "${env_file}" ]; then
    if [ -f "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-incident/generated.env.remote" ]; then
      env_file="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-incident/generated.env.remote"
    elif [ -f "${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-incident/.env" ]; then
      env_file="${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-incident/.env"
    else
      env_file="$(command ls "${REPO_ROOT}"/deploy/docker/developer-profiles/dev-profile-*/generated.env* 2>/dev/null | head -1 || true)"
    fi
  fi

  if [ -n "${env_file}" ] && [ -f "${env_file}" ]; then
    # Export vars from env file safely, ignoring comments and blanks
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
  fi
}

load_env

# Person tracking is on for vss-agent unless the env file (or the caller) turns it off. With the
# tracking stack down the agent skips the step and the analysis runs as before.
export INCIDENT_TRACKING_ENABLED="${INCIDENT_TRACKING_ENABLED:-true}"

# Object tracking (INCIDENT_OBJECT_TRACKING_ENABLED) is off for vss-agent: it is not exported here, so only an
# env file (or the caller) that sets it to 1/true/yes/on turns it on.

# The caption pass is on for vss-agent unless the env file (or the caller) sets it to 0; the agent reads
# only "1" as on, and with it off the analysis runs without it.
export INCIDENT_CHUNKING_ENABLED="${INCIDENT_CHUNKING_ENABLED:-1}"

# ffmpeg and ffprobe (caption pass, tracking) come from the repo-local tools dir when the host has none;
# _start_vss_agent installs them there on first start. See media-tools.sh.
# shellcheck source=SCRIPTDIR/media-tools.sh
source "${SCRIPT_DIR}/media-tools.sh"
PATH="$(media_tools_path)"
export PATH

# -----------------------------------------------------------------------------
# Service Helpers
# -----------------------------------------------------------------------------
get_pid() {
  local svc="$1"
  local pid_file="${RUN_DIR}/${svc}.pid"
  if [ -f "${pid_file}" ]; then
    local pid
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [ -n "${pid}" ]; then
      if kill -0 "${pid}" 2>/dev/null || ps -p "${pid}" >/dev/null 2>&1; then
        echo "${pid}"
        return 0
      fi
    fi
  fi
  echo ""
}

is_running() {
  local svc="$1"
  local pid
  pid="$(get_pid "${svc}")"
  if [ -n "${pid}" ]; then
    return 0
  fi
  if [ "${svc}" = "vss-agent" ]; then
    local port="${VSS_AGENT_PORT:-8000}"
    if curl -sf --max-time 2 "http://localhost:${port}/health" 2>/dev/null | grep -q "isAlive"; then
      return 0
    fi
  fi
  return 1
}

# -----------------------------------------------------------------------------
# vss-agent
# -----------------------------------------------------------------------------
start_vss_agent() {
  with_lock _start_vss_agent
}

_start_vss_agent() {
  local svc="vss-agent"
  local host="${VSS_AGENT_HOST:-127.0.0.1}"
  local port="${VSS_AGENT_PORT:-8000}"

  if curl -sf --max-time 2 "http://localhost:${port}/health" 2>/dev/null | grep -q "isAlive"; then
    local pid
    pid="$(get_pid "${svc}")"
    if [ -n "${pid}" ]; then
      echo "  ${svc} is already running (pid ${pid})"
    else
      echo "  ${svc} is already running (healthy on port ${port})"
    fi
    return 0
  fi

  if is_running "${svc}"; then
    echo "  ${svc} is already running (pid $(get_pid "${svc}"))"
    return 0
  fi

  local nat_bin="${REPO_ROOT}/services/agent/.venv/bin/nat"
  if [ ! -x "${nat_bin}" ]; then
    echo "ERROR: nat binary not found or not executable at ${nat_bin}" >&2
    echo "  Ensure /services/agent/.venv is created and nat is installed." >&2
    return 1
  fi

  local cfg="${VSS_AGENT_CONFIG_FILE:-${REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config.yml}"
  if [[ "${cfg}" != /* ]]; then
    cfg="${REPO_ROOT}/${cfg}"
  fi
  if [ ! -f "${cfg}" ]; then
    echo "ERROR: vss-agent config file not found: ${cfg}" >&2
    return 1
  fi

  if ! media_tools_install; then
    echo "WARNING: ffmpeg/ffprobe unavailable; vss-agent will skip the caption pass and person tracking" >&2
  fi

  # Loopback by default: the agent has no auth. The laptop reaches it through the SSH tunnel.
  local log_file="${RUN_DIR}/${svc}.log"
  local pid_file="${RUN_DIR}/${svc}.pid"

  if [ -f "${log_file}" ] && [ ! -w "${log_file}" ]; then
    rm -f "${log_file}"
  fi
  touch "${log_file}" 2>/dev/null || true
  chmod g+rw "${log_file}" 2>/dev/null || true

  echo "  Starting ${svc} (host=${host}, port=${port})..."
  echo "    command: ${nat_bin} serve --config_file ${cfg} --host ${host} --port ${port}"
  echo "    logging: ${log_file}"

  (
    exec 200>&- 2>/dev/null || true
    cd "${REPO_ROOT}"
    # shellcheck disable=SC2086
    nohup "${nat_bin}" serve --config_file "${cfg}" --host "${host}" --port "${port}" > "${log_file}" 2>&1 200>&- &
    write_pid_atomic "${svc}" "$!"
  )

  local pid
  pid="$(cat "${pid_file}")"
  echo "    launched with pid ${pid}; waiting for healthcheck (http://localhost:${port}/health)..."

  local healthy=""
  for _ in $(seq 1 45); do
    if ! kill -0 "${pid}" 2>/dev/null && ! ps -p "${pid}" >/dev/null 2>&1; then
      echo "ERROR: ${svc} process (pid ${pid}) exited unexpectedly. Log tail:" >&2
      tail -n 20 "${log_file}" >&2
      remove_pid_if_match "${svc}" "${pid}"
      return 1
    fi
    if curl -sf --max-time 3 "http://localhost:${port}/health" 2>/dev/null | grep -q "isAlive"; then
      healthy="1"
      break
    fi
    sleep 2
  done

  if [ -z "${healthy}" ]; then
    echo "ERROR: ${svc} did not pass healthcheck on port ${port} within timeout. Log tail:" >&2
    tail -n 30 "${log_file}" >&2
    remove_pid_if_match "${svc}" "${pid}"
    return 1
  fi

  echo "  ${svc} is UP and healthy (pid ${pid}, port ${port})"
}

# -----------------------------------------------------------------------------
# video-analytics-api
# -----------------------------------------------------------------------------
start_video_analytics_api() {
  with_lock _start_video_analytics_api
}

_start_video_analytics_api() {
  local svc="video-analytics-api"
  if is_running "${svc}"; then
    echo "  ${svc} is already running (pid $(get_pid "${svc}"))"
    return 0
  fi

  local app_dir="${REPO_ROOT}/services/analytics/video-analytics-api/src/app"
  if [ ! -d "${app_dir}" ]; then
    echo "ERROR: ${svc} app dir not found at ${app_dir}" >&2
    return 1
  fi

  local node_bin
  node_bin="$(which node 2>/dev/null || true)"
  if [ -z "${node_bin}" ]; then
    for candidate in "${HOME}/.local/bin/node" "${HOME}/.nvm/versions/node"/*/bin/node /usr/local/bin/node /usr/bin/node; do
      if [ -x "${candidate}" ]; then
        node_bin="${candidate}"
        break
      fi
    done
  fi
  if [ -z "${node_bin}" ]; then
    echo "ERROR: node binary not found on PATH or standard locations." >&2
    return 1
  fi

  local cfg="${VSS_VIDEO_ANALYTICS_API_CONFIG_FILE:-}"
  if [ -z "${cfg}" ]; then
    if [ -f "${REPO_ROOT}/services/analytics/video-analytics-api/configs/vss-video-analytics-api-config.json" ]; then
      cfg="${REPO_ROOT}/services/analytics/video-analytics-api/configs/vss-video-analytics-api-config.json"
    else
      cfg="${REPO_ROOT}/services/analytics/video-analytics-api/configs/default-configs/config.json"
    fi
  fi
  if [[ "${cfg}" != /* ]]; then
    cfg="${REPO_ROOT}/${cfg}"
  fi

  local port="8081"
  local log_file="${RUN_DIR}/${svc}.log"
  local pid_file="${RUN_DIR}/${svc}.pid"

  if [ -f "${log_file}" ] && [ ! -w "${log_file}" ]; then
    rm -f "${log_file}"
  fi
  touch "${log_file}" 2>/dev/null || true
  chmod g+rw "${log_file}" 2>/dev/null || true

  echo "  Starting ${svc} on port ${port}..."
  echo "    command: ${node_bin} index.js --config ${cfg}"
  echo "    logging: ${log_file}"

  (
    exec 200>&- 2>/dev/null || true
    cd "${app_dir}"
    nohup "${node_bin}" index.js --config "${cfg}" > "${log_file}" 2>&1 200>&- &
    write_pid_atomic "${svc}" "$!"
  )

  local pid
  pid="$(cat "${pid_file}")"
  echo "    launched with pid ${pid}; waiting for service startup..."

  local healthy=""
  for _ in $(seq 1 15); do
    if ! kill -0 "${pid}" 2>/dev/null && ! ps -p "${pid}" >/dev/null 2>&1; then
      echo "ERROR: ${svc} process (pid ${pid}) exited unexpectedly. Log tail:" >&2
      tail -n 20 "${log_file}" >&2
      remove_pid_if_match "${svc}" "${pid}"
      return 1
    fi
    if curl -sf --max-time 2 "http://localhost:${port}/" >/dev/null 2>&1 || curl -sf --max-time 2 "http://localhost:${port}/livez" >/dev/null 2>&1 || grep -q "Listening on port: ${port}" "${log_file}" 2>/dev/null; then
      healthy="1"
      break
    fi
    sleep 1
  done

  if [ -z "${healthy}" ]; then
    echo "WARNING: ${svc} (pid ${pid}) started, but port ${port} did not respond yet. Check logs with: ./native-services.sh logs ${svc}"
  else
    echo "  ${svc} is UP (pid ${pid}, port ${port})"
  fi
}

# -----------------------------------------------------------------------------
# behavior-analytics
# -----------------------------------------------------------------------------
start_behavior_analytics() {
  with_lock _start_behavior_analytics
}

_start_behavior_analytics() {
  local svc="behavior-analytics"
  if is_running "${svc}"; then
    echo "  ${svc} is already running (pid $(get_pid "${svc}"))"
    return 0
  fi

  local app_dir="${REPO_ROOT}/services/analytics/behavior-analytics"
  if [ ! -d "${app_dir}" ]; then
    echo "ERROR: ${svc} directory not found at ${app_dir}" >&2
    return 1
  fi

  local py_bin="${app_dir}/.venv/bin/python3"
  if [ ! -x "${py_bin}" ]; then
    py_bin="$(which python3 2>/dev/null || true)"
  fi
  if [ -z "${py_bin}" ]; then
    echo "ERROR: python3 binary not found for ${svc}" >&2
    return 1
  fi

  local cfg="${VSS_BEHAVIOR_ANALYTICS_CONFIG_FILE:-}"
  if [ -z "${cfg}" ]; then
    if [ -f "${app_dir}/configs/vss-behavior-analytics-config.json" ]; then
      cfg="${app_dir}/configs/vss-behavior-analytics-config.json"
    elif [ -f "${app_dir}/configs/warehouse_2d_config.json" ]; then
      cfg="${app_dir}/configs/warehouse_2d_config.json"
    else
      cfg="${app_dir}/configs/dev_example_config.json"
    fi
  fi
  if [[ "${cfg}" != /* ]]; then
    cfg="${REPO_ROOT}/${cfg}"
  fi

  local app_script="${VSS_BEHAVIOR_ANALYTICS_APP:-apps/analytics/main_analytics_2d_app.py}"
  local port="8080"
  local log_file="${RUN_DIR}/${svc}.log"
  local pid_file="${RUN_DIR}/${svc}.pid"

  if [ -f "${log_file}" ] && [ ! -w "${log_file}" ]; then
    rm -f "${log_file}"
  fi
  touch "${log_file}" 2>/dev/null || true
  chmod g+rw "${log_file}" 2>/dev/null || true

  echo "  Starting ${svc} (port=${port})..."
  echo "    command: PYTHONPATH=src ${py_bin} ${app_script} --config ${cfg}"
  echo "    logging: ${log_file}"

  (
    exec 200>&- 2>/dev/null || true
    cd "${app_dir}"
    nohup env PYTHONPATH="src:${PYTHONPATH:-}" "${py_bin}" "${app_script}" --config "${cfg}" > "${log_file}" 2>&1 200>&- &
    write_pid_atomic "${svc}" "$!"
  )

  local pid
  pid="$(cat "${pid_file}")"
  echo "    launched with pid ${pid}; checking process health..."

  sleep 3
  if ! kill -0 "${pid}" 2>/dev/null && ! ps -p "${pid}" >/dev/null 2>&1; then
    echo "ERROR: ${svc} process (pid ${pid}) exited immediately. Log tail:" >&2
    tail -n 25 "${log_file}" >&2
    remove_pid_if_match "${svc}" "${pid}"
    return 1
  fi

  echo "  ${svc} is UP (pid ${pid}, port ${port})"
}

# -----------------------------------------------------------------------------
# Stop helper
# -----------------------------------------------------------------------------
stop_service() {
  local svc="$1"
  local pid_file="${RUN_DIR}/${svc}.pid"
  local pid
  pid="$(get_pid "${svc}")"

  if [ -z "${pid}" ]; then
    # Check if a stale pid file exists
    if [ -f "${pid_file}" ]; then
      rm -f "${pid_file}"
    fi
    echo "  ${svc}: not running"
    return 0
  fi

  echo "  Stopping ${svc} (pid ${pid})..."
  kill -15 "${pid}" 2>/dev/null || true

  local stopped=""
  for _ in $(seq 1 10); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      stopped="1"
      break
    fi
    sleep 1
  done

  if [ -z "${stopped}" ]; then
    echo "    pid ${pid} did not stop gracefully; sending SIGKILL..."
    kill -9 "${pid}" 2>/dev/null || true
  fi

  rm -f "${pid_file}"
  echo "  ${svc}: stopped"
}

# -----------------------------------------------------------------------------
# Status helper
# -----------------------------------------------------------------------------
status_service() {
  local svc="$1"
  local porcelain="${2:-false}"
  local pid
  pid="$(get_pid "${svc}")"

  local port="unknown"
  case "${svc}" in
    vss-agent) port="${VSS_AGENT_PORT:-8000}" ;;
    video-analytics-api) port="8081" ;;
    behavior-analytics) port="8080" ;;
  esac

  local healthy=false
  if [ "${svc}" = "vss-agent" ]; then
    if curl -sf --max-time 2 "http://localhost:${port}/health" 2>/dev/null | grep -q "isAlive"; then
      healthy=true
    fi
  fi

  local running=false
  if [ -n "${pid}" ] || [ "${healthy}" = "true" ]; then
    running=true
  fi

  if [ "${porcelain}" = "true" ]; then
    if [ "${running}" = "true" ]; then
      echo "${svc}:running:${pid:-unknown}:${port}"
    else
      echo "${svc}:stopped:none:${port}"
    fi
  else
    if [ "${running}" = "true" ]; then
      local health_note=""
      if [ "${svc}" = "vss-agent" ]; then
        if [ "${healthy}" = "true" ]; then
          health_note=" (healthy)"
        else
          health_note=" (unresponsive)"
        fi
      fi
      printf "  %-22s RUNNING  (pid %-7s port %-5s)%s\n" "${svc}" "${pid:-unknown}" "${port}" "${health_note}"
    else
      printf "  %-22s STOPPED  (port %s)\n" "${svc}" "${port}"
    fi
    if [ "${svc}" = "vss-agent" ]; then
      local tool_line
      while read -r tool_line; do
        printf "    %s\n" "${tool_line}"
      done < <(media_tools_check || true)
    fi
  fi
}

# -----------------------------------------------------------------------------
# Action routing
# -----------------------------------------------------------------------------
ACTION="${1:-status}"
shift || true

PORCELAIN=false
SERVICES=()

for arg in "$@"; do
  if [ "${arg}" = "--porcelain" ]; then
    PORCELAIN=true
  else
    SERVICES+=("${arg}")
  fi
done

# Expand shorthand targets
resolve_services() {
  local input=("$@")
  local resolved=()
  for item in "${input[@]}"; do
    case "${item}" in
      all)
        resolved+=("vss-agent" "video-analytics-api" "behavior-analytics")
        ;;
      analytics)
        resolved+=("video-analytics-api" "behavior-analytics")
        ;;
      *)
        resolved+=("${item}")
        ;;
    esac
  done
  echo "${resolved[@]}"
}

_start_all_targets() {
  for svc in "${TARGETS[@]}"; do
    case "${svc}" in
      vss-agent) start_vss_agent ;;
      video-analytics-api) start_video_analytics_api ;;
      behavior-analytics) start_behavior_analytics ;;
      *)
        echo "WARNING: Unknown native service '${svc}' (supported: vss-agent, video-analytics-api, behavior-analytics)" >&2
        ;;
    esac
  done
}

case "${ACTION}" in
  start)
    if [ "${#SERVICES[@]}" -eq 0 ]; then
      TARGETS=("vss-agent")
      if [ "${ENABLE_ANALYTICS:-false}" = "true" ]; then
        TARGETS+=("video-analytics-api" "behavior-analytics")
      fi
    else
      read -r -a TARGETS <<< "$(resolve_services "${SERVICES[@]}")"
    fi

    echo "=== Starting Native Services ==="
    with_lock _start_all_targets
    ;;

  stop)
    if [ "${#SERVICES[@]}" -eq 0 ]; then
      TARGETS=("vss-agent" "video-analytics-api" "behavior-analytics")
    else
      read -r -a TARGETS <<< "$(resolve_services "${SERVICES[@]}")"
    fi

    echo "=== Stopping Native Services ==="
    for svc in "${TARGETS[@]}"; do
      stop_service "${svc}"
    done
    ;;

  restart)
    if [ "${#SERVICES[@]}" -eq 0 ]; then
      TARGETS=("vss-agent")
      if [ "${ENABLE_ANALYTICS:-false}" = "true" ]; then
        TARGETS+=("video-analytics-api" "behavior-analytics")
      fi
    else
      read -r -a TARGETS <<< "$(resolve_services "${SERVICES[@]}")"
    fi

    echo "=== Restarting Native Services ==="
    for svc in "${TARGETS[@]}"; do
      stop_service "${svc}"
      case "${svc}" in
        vss-agent) start_vss_agent ;;
        video-analytics-api) start_video_analytics_api ;;
        behavior-analytics) start_behavior_analytics ;;
      esac
    done
    ;;

  status)
    if [ "${#SERVICES[@]}" -eq 0 ]; then
      TARGETS=("vss-agent")
      if [ "${ENABLE_ANALYTICS:-false}" = "true" ]; then
        TARGETS+=("video-analytics-api" "behavior-analytics")
      fi
    else
      read -r -a TARGETS <<< "$(resolve_services "${SERVICES[@]}")"
    fi

    if [ "${PORCELAIN}" != "true" ]; then
      echo "=== Native Services Status (kwanz-ws) ==="
    fi
    for svc in "${TARGETS[@]}"; do
      status_service "${svc}" "${PORCELAIN}"
    done
    ;;

  logs)
    target_svc="${SERVICES[0]:-vss-agent}"
    log_file="${RUN_DIR}/${target_svc}.log"
    if [ ! -f "${log_file}" ]; then
      echo "No log file found for ${target_svc} at ${log_file}" >&2
      exit 1
    fi
    shift || true
    exec tail -n 100 -f "${log_file}"
    ;;

  *)
    echo "Usage: $0 {start|stop|restart|status|logs} [service...] [--porcelain]" >&2
    exit 1
    ;;
esac
