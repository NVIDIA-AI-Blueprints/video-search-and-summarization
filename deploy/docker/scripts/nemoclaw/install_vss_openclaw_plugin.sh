#!/usr/bin/env bash
# Install the VSS OpenClaw plugin (skills + workspace) into a running NemoClaw sandbox.
#
# Patched drop-in for init_nemoclaw.sh::install_vss_openclaw_plugin — compiles
# index.ts → index.js before pack/install so OpenClaw 2026.5.x accepts the plugin
# (tarball-only TypeScript installs fail with "requires compiled runtime output").
#
# Usage:
#   bash install_vss_openclaw_plugin.sh [sandbox-name]
#   NEMOCLAW_SANDBOX_NAME=demo OPENCLAW_PLUGIN_VARIANT=nemoclaw bash install_vss_openclaw_plugin.sh
#
# Optional:
#   SKIP_OPENCLAW_CONFIG=1   skip update_openclaw_config.py (workspace path in openclaw.json)
#   ENV_FILE=path/to/.env    load env before run (default: same dir as this script /.env)
#
# Environment (same as init_nemoclaw.sh):
#   NEMOCLAW_SANDBOX_NAME       default: demo
#   OPENCLAW_PLUGIN_VARIANT     default: nemoclaw (_nemoclaw workspace overlay)
#   VSS_REPO_DIR                repo root (default: auto)
#   OPENCLAW_PLUGIN_DIR         default: ${VSS_REPO_DIR}/.openclaw
#   OPENCLAW_CONFIG_UPDATE_SCRIPT
#   NEMOCLAW_DASHBOARD_PORT     default: 18789

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
NEMOCLAW_SHIM_DIR="${NEMOCLAW_SHIM_DIR:-${HOME}/.local/bin}"
REMOTE_PLUGIN_DIR="/tmp/vss-openclaw-plugin"
VSS_REMOTE_CONFIG_PATH="/sandbox/.openclaw/openclaw.json"

ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ $# -ge 1 ]]; then
  NEMOCLAW_SANDBOX_NAME="$1"
fi

# Script-local defaults (always pin config script path; ignore stale exports from sourced init).
VSS_REPO_DIR="${VSS_REPO_DIR:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-demo}"
OPENCLAW_PLUGIN_DIR="${OPENCLAW_PLUGIN_DIR:-${VSS_REPO_DIR}/.openclaw}"
OPENCLAW_PLUGIN_VARIANT="${OPENCLAW_PLUGIN_VARIANT:-nemoclaw}"
OPENCLAW_CONFIG_UPDATE_SCRIPT="${SCRIPT_DIR}/update_openclaw_config.py"
NEMOCLAW_DASHBOARD_PORT="${NEMOCLAW_DASHBOARD_PORT:-18789}"
NEMOCLAW_DIR="${NEMOCLAW_DIR:-${HOME}/.nemoclaw/source}"
GATEWAY_ENDPOINT="${NEMOCLAW_OPENSHELL_GATEWAY_ENDPOINT:-http://127.0.0.1:8080}"

log() { printf '[install_vss_plugin] %s\n' "$*" >&2; }
warn() { printf '[install_vss_plugin][WARN] %s\n' "$*" >&2; }
die() { printf '[install_vss_plugin][ERROR] %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

strip_ansi() { sed -E 's/\x1B\[[0-9;]*[[:alpha:]]//g'; }

gateway_port_open() {
  ss -tln 2>/dev/null | grep -q ':8080 ' || \
    curl -fsS -o /dev/null -m 2 "${GATEWAY_ENDPOINT}/" 2>/dev/null
}

gateway_connected() {
  local status
  status="$(openshell status 2>&1 | strip_ansi || true)"
  printf '%s' "$status" | grep -qiE 'Status:.*Connected'
}

register_openshell_gateway() {
  if openshell gateway select nemoclaw >/dev/null 2>&1; then
    gateway_connected && return 0
  fi
  openshell gateway remove nemoclaw >/dev/null 2>&1 || true
  openshell gateway add "${GATEWAY_ENDPOINT}" --local --name nemoclaw >/dev/null 2>&1 || return 1
  openshell gateway select nemoclaw >/dev/null 2>&1
}

start_openshell_gateway() {
  local onboard_js="${NEMOCLAW_DIR}/dist/lib/onboard.js"
  [[ -f "${onboard_js}" ]] || die "OpenShell gateway is down and ${onboard_js} is missing — run: bash nemoclaw-install.sh 3"
  log "Starting OpenShell gateway (required for openshell sandbox exec)..."
  node -e '
    const onboard = require(process.argv[1]);
    onboard.startGatewayForRecovery(null)
      .then(() => process.exit(0))
      .catch((e) => { console.error(e.message || e); process.exit(1); });
  ' "${onboard_js}" >&2
}

ensure_openshell_gateway_ready() {
  have openshell || die "openshell is not on PATH"
  register_openshell_gateway >/dev/null 2>&1 || true
  if gateway_port_open && gateway_connected; then
    return 0
  fi
  start_openshell_gateway
  local i
  for i in $(seq 1 30); do
    register_openshell_gateway >/dev/null 2>&1 || true
    gateway_port_open && gateway_connected && return 0
    sleep 2
  done
  die "OpenShell gateway not ready on ${GATEWAY_ENDPOINT} — see ${HOME}/.local/state/nemoclaw/openshell-docker-gateway/openshell-gateway.log"
}

ensure_nvm_loaded() {
  if have node; then
    return 0
  fi
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
    # shellcheck disable=SC1090
    . "${NVM_DIR}/nvm.sh"
    nvm use 22 >/dev/null 2>&1 || nvm use default >/dev/null 2>&1 || true
  fi
}

refresh_path() {
  ensure_nvm_loaded
  local npm_bin
  npm_bin="$(npm config get prefix 2>/dev/null)/bin" || true
  if [[ -n "${npm_bin:-}" && -d "${npm_bin}" ]]; then
    export PATH="${npm_bin}:${NEMOCLAW_SHIM_DIR}:${PATH:-}"
  else
    export PATH="${NEMOCLAW_SHIM_DIR}:${PATH:-}"
  fi
  hash -r 2>/dev/null || true
}

resolve_sandbox_container() {
  local name="$1"
  docker ps --format '{{.Names}}' | awk -v sandbox="$name" \
    '$0 ~ "^openshell-" sandbox "-" { print; exit }'
}

resolve_vss_gateway_container() {
  if [[ -n "${VSS_CONTAINER_NAME:-}" ]]; then
    printf '%s' "${VSS_CONTAINER_NAME}"
    return 0
  fi
  docker ps --format '{{.Names}}' | awk '/^(openshell-cluster-|nemoclaw-openshell-)/{print; exit}'
}

ensure_sandbox_workspace_writable() {
  local sandbox_name="$1"
  local container

  container="$(resolve_sandbox_container "${sandbox_name}")"
  [[ -z "${container}" ]] && return 0

  log "Ensuring /sandbox/.openclaw/workspace is writable by sandbox user"
  docker exec -u 0 "${container}" sh -lc \
    'mkdir -p /sandbox/.openclaw/workspace && chown -R sandbox:sandbox /sandbox/.openclaw/workspace'
}

stage_vss_plugin() {
  local plugin_dir="$1"
  local stage
  stage="$(mktemp -d)"

  [[ -f "${plugin_dir}/package.json" ]] || die "Missing ${plugin_dir}/package.json"
  [[ -d "${VSS_REPO_DIR}/skills" ]] || die "Missing ${VSS_REPO_DIR}/skills"

  log "Staging plugin in ${stage}"
  cp -a "${plugin_dir}/." "${stage}/"
  cp -a "${VSS_REPO_DIR}/skills" "${stage}/skills"
  rm -f "${stage}/index.ts"

  log "Compiling index.ts → index.js (OpenClaw requires JS entrypoint in packages)"
  npx --yes esbuild "${plugin_dir}/index.ts" --platform=node --format=esm \
    --outfile="${stage}/index.js" >/dev/null 2>&1

  node -e "
const fs = require('fs');
const path = require('path');
const pkgPath = path.join('${stage}', 'package.json');
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
pkg.openclaw = { extensions: ['./index.js'] };
const files = new Set([...(pkg.files || []), 'index.js', 'skills/', 'workspace/', 'openclaw.plugin.json', 'README.md']);
pkg.files = [...files].filter((f) => f !== 'index.ts');
fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
"

  printf '%s' "${stage}"
}

copy_plugin_to_sandbox() {
  local stage="$1"
  local sandbox_name="$2"
  local remote_dir="$3"
  local container gateway

  container="$(resolve_sandbox_container "${sandbox_name}")"
  if [[ -n "${container}" ]]; then
    log "Copying staged plugin into sandbox container ${container}:${remote_dir}"
    docker exec -u 0 "${container}" rm -rf "${remote_dir}"
    docker exec -u 0 "${container}" mkdir -p "${remote_dir}"
    docker cp "${stage}/." "${container}:${remote_dir}/"
    return 0
  fi

  gateway="$(resolve_vss_gateway_container)"
  if [[ -n "${gateway}" ]] && have kubectl; then
    log "Copying staged plugin via gateway ${gateway} (kubectl driver)"
    local tgz tgz_name
    tgz="$(mktemp)"
    tar czf "${tgz}" -C "${stage}" .
    tgz_name="$(basename "${tgz}")"
    docker exec -i "${gateway}" kubectl exec -i -n "${VSS_NAMESPACE:-openshell}" "${sandbox_name}" -- \
      sh -c "rm -rf '${remote_dir}' && mkdir -p '${remote_dir}' && cat > '/tmp/${tgz_name}'" < "${tgz}"
    docker exec "${gateway}" kubectl exec -n "${VSS_NAMESPACE:-openshell}" "${sandbox_name}" -- \
      sh -lc "tar xzf '/tmp/${tgz_name}' -C '${remote_dir}' && rm -f '/tmp/${tgz_name}'"
    rm -f "${tgz}"
    return 0
  fi

  die "Could not find sandbox container (openshell-${sandbox_name}-*) or kubectl gateway"
}

install_vss_openclaw_plugin() {
  local plugin_dir="${OPENCLAW_PLUGIN_DIR}"
  local stage="" remote_dir="${REMOTE_PLUGIN_DIR}" install_cmd

  [[ -f "${plugin_dir}/package.json" ]] || die "${plugin_dir} is not a packable OpenClaw plugin"
  have npm || die "npm is not available"
  have openshell || die "openshell is not on PATH"
  openshell sandbox list >/dev/null 2>&1 || die "OpenShell sandbox access is not ready"

  stage="$(stage_vss_plugin "${plugin_dir}")"
  trap '[[ -n "${stage:-}" ]] && rm -rf "${stage}"; trap - RETURN' RETURN

  copy_plugin_to_sandbox "${stage}" "${NEMOCLAW_SANDBOX_NAME}" "${remote_dir}"
  ensure_sandbox_workspace_writable "${NEMOCLAW_SANDBOX_NAME}"

  printf -v install_cmd \
    'OPENCLAW_PLUGIN_VARIANT=%q openclaw plugins install %q --force --dangerously-force-unsafe-install' \
    "${OPENCLAW_PLUGIN_VARIANT}" "${remote_dir}"

  log "Installing plugin (variant=${OPENCLAW_PLUGIN_VARIANT}) from ${remote_dir}"
  if ! openshell sandbox exec -n "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc "${install_cmd}" </dev/null; then
    die "openclaw plugins install failed"
  fi

  log "VSS OpenClaw plugin installed"
}

fix_openclaw_config_permissions_for_gateway() {
  local container
  container="$(resolve_sandbox_container "${NEMOCLAW_SANDBOX_NAME}")"
  [[ -z "${container}" ]] && return 0
  log "Ensuring OpenClaw config is readable by the gateway user"
  docker exec -u 0 "${container}" sh -lc '
    chmod 2770 /sandbox/.openclaw 2>/dev/null || true
    chown sandbox:sandbox /sandbox/.openclaw 2>/dev/null || true
    chmod 660 /sandbox/.openclaw/openclaw.json 2>/dev/null || true
    chown sandbox:sandbox /sandbox/.openclaw/openclaw.json 2>/dev/null || true
    touch /tmp/gateway.log
    chown sandbox:sandbox /tmp/gateway.log
    chmod 644 /tmp/gateway.log
  ' || true
}

run_openclaw_gateway_recovery() {
  local port="${NEMOCLAW_DASHBOARD_PORT}" container runtime_js recovery_script attempt out

  runtime_js="${NEMOCLAW_DIR}/dist/lib/agent/runtime.js"
  [[ -f "${runtime_js}" ]] || die "Missing ${runtime_js} — run: bash nemoclaw-install.sh 3"

  container="$(resolve_sandbox_container "${NEMOCLAW_SANDBOX_NAME}")"
  if [[ -n "${container}" ]]; then
    fix_openclaw_config_permissions_for_gateway
    recovery_script="$(node -e "console.log(require('${runtime_js}').buildOpenClawRecoveryScript(${port}))")"
    log "Recovering OpenClaw gateway in sandbox ${NEMOCLAW_SANDBOX_NAME} (nemoclaw recovery script)"
    out="$(docker exec -u sandbox "${container}" bash -lc "${recovery_script}" 2>&1 || true)"
    case "${out}" in
      *ALREADY_RUNNING*|*GATEWAY_PID=*)
        log "OpenClaw gateway is running in sandbox"
        return 0
        ;;
      *GATEWAY_FAILED*|*GATEWAY_STALE_PROCESSES*|*OPENCLAW_MISSING*)
        warn "Gateway recovery failed inside sandbox:"
        printf '%s\n' "${out}" | tail -5 >&2
        return 1
        ;;
    esac
    for attempt in $(seq 1 60); do
      if docker exec "${container}" curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
        log "OpenClaw gateway is healthy after recovery"
        return 0
      fi
      sleep 1
    done
    return 1
  fi

  have nemoclaw || die "nemoclaw not on PATH and no local docker sandbox container found"
  log "Recovering OpenClaw gateway via nemoclaw recover"
  nemoclaw "${NEMOCLAW_SANDBOX_NAME}" recover
}

stop_openclaw_gateway_for_plugin_reload() {
  local container="${1:-}" port="${NEMOCLAW_DASHBOARD_PORT}"
  log "Stopping OpenClaw gateway so plugin changes take effect"
  if [[ -n "${container}" ]]; then
    docker exec -u sandbox "${container}" bash -lc \
      'pkill -TERM -f "[o]penclaw gateway" 2>/dev/null || pkill -TERM -f "[o]penclaw-gateway" 2>/dev/null || true' \
      2>/dev/null || true
  else
    openshell sandbox exec -n "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc \
      'pkill -TERM -f "[o]penclaw gateway" 2>/dev/null || pkill -TERM -f "[o]penclaw-gateway" 2>/dev/null || true' \
      </dev/null || true
  fi
  sleep 2
}

forward_running_for_sandbox() {
  local port="$1" sandbox_name="$2"
  openshell forward list 2>/dev/null \
    | strip_ansi \
    | awk -v name="$sandbox_name" -v port="$port" \
        '$1 == name && $3 == port && tolower($NF) == "running" { found = 1 } END { exit found ? 0 : 1 }'
}

forward_process_running_for_sandbox() {
  local port="$1" sandbox_name="$2" args
  while IFS= read -r args; do
    case "$args" in
      *"openshell forward start ${port} ${sandbox_name}"*|*"openshell forward start --background ${port} ${sandbox_name}"*)
        return 0
        ;;
    esac
  done < <(ps -eo args= 2>/dev/null || true)
  return 1
}

forward_owned_by_sandbox() {
  local port="$1" sandbox_name="$2"
  forward_running_for_sandbox "$port" "$sandbox_name" || forward_process_running_for_sandbox "$port" "$sandbox_name"
}

cleanup_orphan_dashboard_forward() {
  local port="$1" sandbox_name="$2" pid cmdline owner
  forward_owned_by_sandbox "$port" "$sandbox_name" && return 0

  pid="$(ss -tlnp 2>/dev/null | grep ":${port} " | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1)"
  [[ -z "${pid}" ]] && return 0

  cmdline="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ "${cmdline}" == *openshell* ]] || return 0

  owner="$(openshell forward list 2>/dev/null | strip_ansi | awk -v port="${port}" '$3 == port { print $1; exit }')"
  if [[ -n "${owner}" && "${owner}" != "${sandbox_name}" ]]; then
    warn "Port ${port} held by sandbox '${owner}'; not killing orphan listener"
    return 0
  fi

  log "Cleaning orphaned OpenShell SSH forward on port ${port} (pid ${pid})"
  kill "${pid}" 2>/dev/null || true
  sleep 1
}

dashboard_forward_healthy() {
  local port="$1"
  have curl && curl -fsS "http://127.0.0.1:${port}/health" 2>/dev/null \
    | grep -q '"ok"[[:space:]]*:[[:space:]]*true'
}

sandbox_gateway_healthy() {
  local port="$1" container
  container="$(resolve_sandbox_container "${NEMOCLAW_SANDBOX_NAME}")"
  [[ -n "${container}" ]] || return 1
  docker exec "${container}" curl -fsS "http://127.0.0.1:${port}/health" 2>/dev/null \
    | grep -q '"ok"[[:space:]]*:[[:space:]]*true'
}

dashboard_forward_ready() {
  local port="$1"
  forward_owned_by_sandbox "${port}" "${NEMOCLAW_SANDBOX_NAME}" || return 1
  dashboard_forward_healthy "${port}" && return 0
  # Host curl can fail while forward list shows running when the OpenShell SSH
  # tunnel is stale; trust in-container health in the Docker-driver path.
  sandbox_gateway_healthy "${port}"
}

# Match nemoclaw process-recovery.ts: always use `forward start --background`
# and confirm via `openshell forward list` (not curl alone).
ensure_dashboard_forward() {
  local port="${NEMOCLAW_DASHBOARD_PORT}"
  local forward_log="/tmp/nemoclaw-forward-${port}.log"
  local attempt

  have openshell || { warn "openshell not on PATH — cannot start dashboard forward"; return 1; }

  if dashboard_forward_ready "${port}"; then
    log "Dashboard port-forward on ${port} is ready"
    return 0
  fi

  log "Refreshing dashboard port-forward on ${port} for sandbox ${NEMOCLAW_SANDBOX_NAME}"
  cleanup_orphan_dashboard_forward "${port}" "${NEMOCLAW_SANDBOX_NAME}"
  openshell forward stop "${port}" "${NEMOCLAW_SANDBOX_NAME}" >/dev/null 2>&1 || true
  pkill -TERM -f "[o]penshell forward start.*${port}.*${NEMOCLAW_SANDBOX_NAME}" >/dev/null 2>&1 || true

  if ! openshell forward start --background "${port}" "${NEMOCLAW_SANDBOX_NAME}" </dev/null >"${forward_log}" 2>&1; then
    warn "openshell forward start failed — see ${forward_log}"
    [[ -f "${forward_log}" ]] && tail -n 10 "${forward_log}" | sed 's/^/[forward log] /' >&2 || true
    return 1
  fi

  for attempt in $(seq 1 60); do
    if dashboard_forward_ready "${port}"; then
      if dashboard_forward_healthy "${port}"; then
        log "Dashboard port-forward on ${port} is healthy (host /health OK)"
      else
        log "Dashboard forward registered on ${port}; in-sandbox gateway is healthy"
        warn "Host http://127.0.0.1:${port}/health is not responding — OpenShell SSH tunnel may be stale"
        warn "Brev URL may still work; if not: openshell forward stop ${port} ${NEMOCLAW_SANDBOX_NAME} && openshell forward start --background ${port} ${NEMOCLAW_SANDBOX_NAME}"
      fi
      return 0
    fi
    [[ $((attempt % 10)) -eq 0 ]] && log "Waiting for dashboard forward on ${port} (${attempt}/60)"
    sleep 1
  done

  if forward_owned_by_sandbox "${port}" "${NEMOCLAW_SANDBOX_NAME}" && sandbox_gateway_healthy "${port}"; then
    warn "Forward is registered and in-sandbox gateway is healthy, but host /health did not respond"
    return 0
  fi

  warn "Dashboard forward on ${port} did not become ready"
  warn "Try: nemoclaw ${NEMOCLAW_SANDBOX_NAME} recover"
  warn "Or:  openshell forward start --background ${port} ${NEMOCLAW_SANDBOX_NAME}"
  [[ -f "${forward_log}" ]] && tail -n 10 "${forward_log}" | sed 's/^/[forward log] /' >&2 || true
  return 1
}

reload_openclaw_after_plugin_install() {
  local container
  container="$(resolve_sandbox_container "${NEMOCLAW_SANDBOX_NAME}")"
  stop_openclaw_gateway_for_plugin_reload "${container}"
  run_openclaw_gateway_recovery || warn "OpenClaw gateway recovery failed — UI may be down until: nemoclaw ${NEMOCLAW_SANDBOX_NAME} recover"
  ensure_dashboard_forward || warn "Dashboard port-forward not healthy — run: nemoclaw ${NEMOCLAW_SANDBOX_NAME} recover"
}

update_openclaw_workspace_config() {
  [[ "${SKIP_OPENCLAW_CONFIG:-}" == "1" ]] && { log "SKIP_OPENCLAW_CONFIG=1 — skipping"; return 0; }
  [[ -f "${OPENCLAW_CONFIG_UPDATE_SCRIPT}" ]] || die "Missing ${OPENCLAW_CONFIG_UPDATE_SCRIPT}"
  have python3 || die "python3 is required for ${OPENCLAW_CONFIG_UPDATE_SCRIPT}"
  log "Updating OpenClaw config (agents.defaults.workspace) for ${NEMOCLAW_SANDBOX_NAME}"
  python3 "${OPENCLAW_CONFIG_UPDATE_SCRIPT}" "${NEMOCLAW_SANDBOX_NAME}" --config-path "${VSS_REMOTE_CONFIG_PATH}"
}

main() {
  refresh_path
  ensure_openshell_gateway_ready
  update_openclaw_workspace_config
  install_vss_openclaw_plugin
  reload_openclaw_after_plugin_install

  log "Verify: openshell forward list"
  log "Verify: nemoclaw ${NEMOCLAW_SANDBOX_NAME} dashboard-url --quiet"
}

main "$@"
