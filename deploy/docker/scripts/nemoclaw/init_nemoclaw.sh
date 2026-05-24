#!/usr/bin/env bash
# Configures NemoClaw to use either NVIDIA's hosted Nemotron model (NEMOCLAW_PROVIDER=build)
# or an OpenAI-compatible endpoint (NEMOCLAW_PROVIDER=custom).
# For "build": requires NVIDIA_API_KEY (via --nvidia-api-key, env var, or interactive prompt).
# For "custom": requires NEMOCLAW_ENDPOINT_URL and COMPATIBLE_API_KEY; NVIDIA_API_KEY is unused.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
VSS_REPO_DIR="${VSS_REPO_DIR:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"
NEMOCLAW_REPO_DIR="${NEMOCLAW_REPO_DIR:-${HOME}/NemoClaw}"
NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-demo}"
# NEMOCLAW_PROVIDER selects the Nemoclaw onboard/install provider. Required — no default.
# Accepted values: "build" (NVIDIA Endpoints / integrate.api.nvidia.com) or "custom" (OpenAI-compatible endpoint).
NEMOCLAW_PROVIDER="${NEMOCLAW_PROVIDER:?NEMOCLAW_PROVIDER is required}"
# Custom-provider settings — required when NEMOCLAW_PROVIDER=custom (OpenAI-compatible endpoint).
NEMOCLAW_ENDPOINT_URL="${NEMOCLAW_ENDPOINT_URL:-}"
COMPATIBLE_API_KEY="${COMPATIBLE_API_KEY:-}"
# OpenShell provider display name (separate from Nemoclaw's NEMOCLAW_PROVIDER for onboard).
OPENCLAW_PLUGIN_VARIANT="${OPENCLAW_PLUGIN_VARIANT:-}"
OPENSHELL_PROVIDER_NAME="${OPENSHELL_PROVIDER_NAME:-nvidia}"
NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-nvidia/nemotron-3-super-120b-a12b}"
NEMOCLAW_NON_INTERACTIVE=1
NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
NVIDIA_API_KEY="${NVIDIA_API_KEY:-}"
NVIDIA_BASE_URL="${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}"
NEMOCLAW_SHIM_DIR="${HOME}/.local/bin"
OPENCLAW_CONFIG_UPDATE_SCRIPT="${OPENCLAW_CONFIG_UPDATE_SCRIPT:-${SCRIPT_DIR}/update_openclaw_config.py}"
NEMOCLAW_POLICY_FILE="${NEMOCLAW_POLICY_FILE:-${VSS_REPO_DIR}/assets/vss_nemoclaw_policy.yaml}"
OPENCLAW_PLUGIN_DIR="${OPENCLAW_PLUGIN_DIR:-${VSS_REPO_DIR}/.openclaw}"
VSS_NAMESPACE="${VSS_NAMESPACE:-openshell}"
VSS_REMOTE_CONFIG_PATH="/sandbox/.openclaw/openclaw.json"

log() {
  printf '[init_nemoclaw] %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

node_major_version() {
  node -e 'process.stdout.write(String(parseInt(process.versions.node, 10)))' 2>/dev/null || printf '0'
}

usage() {
  cat <<'EOF'
Usage:
  bash init_nemoclaw.sh [--nvidia-api-key <KEY>] [options]
  NVIDIA_API_KEY=<key> bash init_nemoclaw.sh [options]

  When NEMOCLAW_PROVIDER=build, the NVIDIA API key is resolved in this order:
    1. --nvidia-api-key flag (overrides env)
    2. NVIDIA_API_KEY environment variable
    3. Interactive prompt (if neither is set)
  When NEMOCLAW_PROVIDER=custom, NVIDIA_API_KEY is ignored — use --endpoint-url and --compatible-api-key.

Options:
  --nvidia-api-key KEY        NVIDIA API key (required when NEMOCLAW_PROVIDER=build; ignored for "custom")
  --sandbox-name NAME         Sandbox name (default: demo)
  --model NAME                NVIDIA model ID (default: nvidia/nemotron-3-super-120b-a12b)
  --nvidia-base-url URL       NVIDIA API base URL (default: https://integrate.api.nvidia.com/v1)
  --endpoint-url URL          OpenAI-compatible endpoint URL (REQUIRED when --provider=custom)
  --compatible-api-key KEY    API key for the OpenAI-compatible endpoint (REQUIRED when --provider=custom)
  --nemoclaw-repo-dir PATH    Path to NemoClaw source checkout (default: $HOME/NemoClaw)
  --openclaw-config-script PATH
                              Path to the OpenClaw config update helper
  --policy-file PATH          Path to the custom sandbox policy file
  --help                      Show this help

Environment (non-interactive Nemoclaw / OpenShell):
  NEMOCLAW_PROVIDER           Nemoclaw onboard/install provider (REQUIRED; must be "build" = NVIDIA Endpoints / integrate.api.nvidia.com, or "custom" = OpenAI-compatible)
  NEMOCLAW_ENDPOINT_URL       OpenAI-compatible endpoint URL (REQUIRED when NEMOCLAW_PROVIDER=custom)
  COMPATIBLE_API_KEY          API key for the OpenAI-compatible endpoint (REQUIRED when NEMOCLAW_PROVIDER=custom)
  OPENSHELL_PROVIDER_NAME     Name for openshell OpenAI-compatible provider (default: nvidia)
  OPENCLAW_PLUGIN_DIR              Path to the OpenClaw plugin source to pack and install
                              (default: <VSS_REPO_DIR>/.openclaw)
EOF
}

parse_args() {
  local positional=()

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --nvidia-api-key)
        NVIDIA_API_KEY="$2"
        shift 2
        ;;
      --sandbox-name)
        NEMOCLAW_SANDBOX_NAME="$2"
        shift 2
        ;;
      --model)
        NEMOCLAW_MODEL="$2"
        shift 2
        ;;
      --nvidia-base-url)
        NVIDIA_BASE_URL="$2"
        shift 2
        ;;
      --endpoint-url)
        NEMOCLAW_ENDPOINT_URL="$2"
        shift 2
        ;;
      --compatible-api-key)
        COMPATIBLE_API_KEY="$2"
        shift 2
        ;;
      --nemoclaw-repo-dir)
        NEMOCLAW_REPO_DIR="$2"
        shift 2
        ;;
      --openclaw-config-script)
        OPENCLAW_CONFIG_UPDATE_SCRIPT="$2"
        shift 2
        ;;
      --policy-file)
        NEMOCLAW_POLICY_FILE="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      --*)
        log "Unknown option: $1"
        usage
        exit 1
        ;;
      *)
        positional+=("$1")
        shift
        ;;
    esac
  done

  if [ "${#positional[@]}" -ge 1 ]; then
    NEMOCLAW_SANDBOX_NAME="${positional[0]}"
  fi
  if [ "${#positional[@]}" -gt 1 ]; then
    log "Too many positional arguments"
    usage
    exit 1
  fi

  # NVIDIA_API_KEY is only required for the "build" provider (NVIDIA Endpoints).
  # In "custom" mode the OpenAI-compatible endpoint uses COMPATIBLE_API_KEY instead,
  # which is validated separately in validate_custom_provider_settings().
  if [ "${NEMOCLAW_PROVIDER}" = "build" ] && [ -z "${NVIDIA_API_KEY:-}" ]; then
    read -rsp "Enter your NVIDIA API key: " NVIDIA_API_KEY
    printf '\n'
    if [ -z "${NVIDIA_API_KEY:-}" ]; then
      log "ERROR: NVIDIA API key is required when NEMOCLAW_PROVIDER=build."
      exit 1
    fi
  fi
}

ensure_nvm_loaded() {
  if have node && [ "$(node_major_version)" -ge 22 ]; then
    return 0
  fi
  if [ -z "${NVM_DIR:-}" ]; then
    export NVM_DIR="$HOME/.nvm"
  fi
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # shellcheck disable=SC1090
    . "$NVM_DIR/nvm.sh"
    # Sourcing nvm.sh alone can leave an older node first on PATH; nemoclaw requires Node 22+.
    if ! have node || [ "$(node_major_version)" -lt 22 ]; then
      nvm use 22 >/dev/null 2>&1 || nvm use default >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || true
    fi
    if [ -n "${NVM_BIN:-}" ] && [ -d "${NVM_BIN}" ]; then
      export PATH="${NVM_BIN}:${PATH}"
      hash -r 2>/dev/null || true
    fi
  fi
}

refresh_path() {
  ensure_nvm_loaded

  local npm_bin
  npm_bin="$(npm config get prefix 2>/dev/null)/bin" || true
  if [ -n "${npm_bin:-}" ] && [ -d "$npm_bin" ] && [[ ":$PATH:" != *":$npm_bin:"* ]]; then
    export PATH="$npm_bin:$PATH"
  fi

  if [ -d "$NEMOCLAW_SHIM_DIR" ] && [[ ":$PATH:" != *":$NEMOCLAW_SHIM_DIR:"* ]]; then
    export PATH="$NEMOCLAW_SHIM_DIR:$PATH"
  fi
}

resolve_nemoclaw() {
  refresh_path

  if have nemoclaw; then
    command -v nemoclaw
    return 0
  fi

  local npm_bin candidate
  npm_bin="$(npm config get prefix 2>/dev/null)/bin" || true

  for candidate in \
    "$NEMOCLAW_SHIM_DIR/nemoclaw" \
    "${npm_bin:-}/nemoclaw"
  do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

configure_openshell_provider() {
  if ! have openshell; then
    log "OpenShell not available yet; skipping provider setup for now"
    return
  fi

  # Pick endpoint + key based on NEMOCLAW_PROVIDER. "build" uses NVIDIA Endpoints;
  # "custom" uses the user-supplied OpenAI-compatible endpoint (e.g. a local vLLM).
  local openai_base_url openai_api_key
  case "${NEMOCLAW_PROVIDER}" in
    build)
      openai_base_url="${NVIDIA_BASE_URL}"
      openai_api_key="${NVIDIA_API_KEY}"
      ;;
    custom)
      openai_base_url="${NEMOCLAW_ENDPOINT_URL}"
      openai_api_key="${COMPATIBLE_API_KEY}"
      ;;
    *)
      log "ERROR: NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER} is not supported by configure_openshell_provider (expected 'build' or 'custom')."
      return 1
      ;;
  esac

  log "Configuring OpenShell provider ${OPENSHELL_PROVIDER_NAME} (NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER}, base=${openai_base_url})"
  local action provider_args
  if openshell provider get "$OPENSHELL_PROVIDER_NAME" >/dev/null 2>&1; then
    action="update"
    provider_args=(provider update --credential OPENAI_API_KEY --config "OPENAI_BASE_URL=$openai_base_url" "$OPENSHELL_PROVIDER_NAME")
  else
    action="create"
    provider_args=(provider create --name "$OPENSHELL_PROVIDER_NAME" --type openai --credential OPENAI_API_KEY --config "OPENAI_BASE_URL=$openai_base_url")
  fi
  if ! OPENAI_API_KEY="$openai_api_key" openshell "${provider_args[@]}"; then
    log "Provider ${action} failed; continuing with existing provider config"
  fi

  openshell inference set --provider "$OPENSHELL_PROVIDER_NAME" --model "$NEMOCLAW_MODEL"
  openshell inference get || true
}

# `nemoclaw onboard` creates the dashboard port-forward (default 18789) that exposes the in-pod
# openclaw-gateway (and its /hooks endpoint) to the host. When the sandbox already exists we skip
# onboard, and the forward can also die independently between runs — so refresh it unconditionally.
forward_running_for_sandbox() {
  local port="$1"
  local sandbox_name="$2"
  openshell forward list 2>/dev/null \
    | strip_ansi \
    | awk -v name="$sandbox_name" -v port="$port" \
        '$1 == name && $3 == port && tolower($NF) == "running" { found = 1 } END { exit found ? 0 : 1 }'
}

forward_process_running_for_sandbox() {
  local port="$1"
  local sandbox_name="$2"
  local args
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
  local port="$1"
  local sandbox_name="$2"
  forward_running_for_sandbox "$port" "$sandbox_name" || forward_process_running_for_sandbox "$port" "$sandbox_name"
}

dashboard_forward_healthy() {
  local port="$1"
  have curl && curl -fsS "http://127.0.0.1:${port}/health" 2>/dev/null \
    | grep -q '"ok"[[:space:]]*:[[:space:]]*true'
}

ensure_dashboard_forward() {
  local port="${NEMOCLAW_DASHBOARD_PORT:-18789}"
  local forward_log="/tmp/nemoclaw-forward-${port}.log"
  if ! have openshell; then
    log "ERROR: OpenShell not available; cannot refresh dashboard port-forward"
    return 1
  fi
  log "Refreshing dashboard port-forward on ${port} for sandbox ${NEMOCLAW_SANDBOX_NAME}"
  if dashboard_forward_healthy "$port"; then
    sleep 2
    if dashboard_forward_healthy "$port" && forward_owned_by_sandbox "$port" "$NEMOCLAW_SANDBOX_NAME"; then
      log "Dashboard port-forward on ${port} is already healthy; keeping existing listener"
      return
    fi
    log "Existing listener on ${port} is not the expected forward for sandbox ${NEMOCLAW_SANDBOX_NAME}; restarting scoped OpenShell forward"
  fi

  openshell forward stop "$port" "$NEMOCLAW_SANDBOX_NAME" >/dev/null 2>&1 || true
  pkill -TERM -f "[o]penshell forward start ${port} ${NEMOCLAW_SANDBOX_NAME}" >/dev/null 2>&1 || true

  if have setsid; then
    setsid -f openshell forward start "$port" "$NEMOCLAW_SANDBOX_NAME" </dev/null >"$forward_log" 2>&1 || true
  else
    openshell forward start --background "$port" "$NEMOCLAW_SANDBOX_NAME" </dev/null >"$forward_log" 2>&1 || true
  fi

  for _attempt in $(seq 1 30); do
    if forward_owned_by_sandbox "$port" "$NEMOCLAW_SANDBOX_NAME" && dashboard_forward_healthy "$port"; then
      log "Dashboard port-forward on ${port} is healthy for sandbox ${NEMOCLAW_SANDBOX_NAME}"
      return
    fi
    sleep 1
  done

  log "ERROR: could not (re)start dashboard forward on ${port}; the OpenClaw UI and /hooks endpoint are unreachable at http://127.0.0.1:${port}"
  if [ -f "$forward_log" ]; then
    tail -n 20 "$forward_log" | sed 's/^/[init_nemoclaw] forward log: /' >&2 || true
  fi
  return 1
}

update_openclaw_allowed_origin() {
  local script="${OPENCLAW_CONFIG_UPDATE_SCRIPT}"

  if [ ! -f "$script" ]; then
    log "ERROR: OpenClaw config update script ${script} is not available"
    return 1
  fi

  if ! have python3; then
    log "ERROR: python3 is not available; cannot run OpenClaw config update script ${script}"
    return 1
  fi

  log "Updating OpenClaw config for sandbox ${NEMOCLAW_SANDBOX_NAME} using script ${script}"
  if ! python3 "$script" "$NEMOCLAW_SANDBOX_NAME" --config-path "$VSS_REMOTE_CONFIG_PATH"; then
    log "ERROR: OpenClaw config update failed for sandbox ${NEMOCLAW_SANDBOX_NAME}"
    return 1
  fi
}

resolve_vss_gateway_container() {
  if [ -n "${VSS_CONTAINER_NAME:-}" ]; then
    printf '%s\n' "${VSS_CONTAINER_NAME}"
    return 0
  fi

  # Match either the legacy kubectl-driver gateway (openshell-cluster-*) or the
  # newer Docker-driver gateway (nemoclaw-openshell-*) emitted by NemoClaw >= v0.0.40.
  docker ps --format '{{.Names}}' | awk '/^(openshell-cluster-|nemoclaw-openshell-)/{print; exit}'
}

apply_vss_policy() {
  local policy_file="${NEMOCLAW_POLICY_FILE}"

  if ! have nemoclaw; then
    log "ERROR: nemoclaw CLI is not available; cannot apply preset from ${policy_file}"
    return 1
  fi

  if [ ! -f "$policy_file" ]; then
    log "ERROR: Policy file ${policy_file} is not available"
    return 1
  fi

  # The VSS preset is applied via `nemoclaw policy-add --from-file`, which
  # merges into the live sandbox policy. `openshell policy set --policy`
  # was the legacy path; it replaces the whole policy (including base
  # filesystem/landlock/process rules) and OpenShell rejects the result.
  log "Applying VSS preset ${policy_file} to sandbox ${NEMOCLAW_SANDBOX_NAME}"
  nemoclaw "$NEMOCLAW_SANDBOX_NAME" policy-add --from-file "$policy_file" --yes
}

restart_vss_openclaw_gateway() {
  local port attempt
  port="${NEMOCLAW_DASHBOARD_PORT:-18789}"

  if ! have openshell; then
    log "OpenShell is not available; cannot restart OpenClaw gateway"
    return 1
  fi

  log "Restarting OpenClaw gateway in sandbox ${NEMOCLAW_SANDBOX_NAME}"
  openshell sandbox exec -n "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc \
    "pkill -TERM -f '[o]penclaw-gateway' || true" || true

  for attempt in $(seq 1 30); do
    if openshell sandbox exec -n "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc \
        "curl -fsS http://127.0.0.1:${port}/health >/dev/null"; then
      log "OpenClaw gateway is healthy after restart"
      return 0
    fi
    sleep 1
  done

  log "WARN: OpenClaw gateway did not become healthy within 30 seconds after restart"
  return 1
}

install_vss_openclaw_plugin() {
  local plugin_dir tgz_name tgz_path container_name remote_tgz install_cmd shell_cmd
  plugin_dir="${OPENCLAW_PLUGIN_DIR}"

  if [ ! -f "${plugin_dir}/package.json" ]; then
    log "${plugin_dir} is not a packable OpenClaw plugin; skipping plugin install"
    return
  fi

  if ! have npm; then
    log "npm is not available; cannot pack VSS OpenClaw plugin"
    return 1
  fi

  if ! have openshell; then
    log "OpenShell is not available; skipping VSS plugin install"
    return
  fi

  if ! openshell sandbox list >/dev/null 2>&1; then
    log "OpenShell sandbox access is not ready; skipping VSS plugin install"
    return
  fi

  container_name="$(resolve_vss_gateway_container)"
  if [ -z "${container_name}" ]; then
    log "Could not determine the OpenShell gateway container; skipping VSS plugin install"
    return
  fi

  if [ ! -d "${VSS_REPO_DIR}/skills" ]; then
    log "ERROR: ${VSS_REPO_DIR}/skills is missing; prepack (cp -r ../skills skills) will fail. Cannot pack VSS OpenClaw plugin."
    return 1
  fi

  log "Packing VSS OpenClaw plugin in ${plugin_dir}"
  tgz_name="$(cd "${plugin_dir}" && npm pack | tail -n1)"
  if [ -z "${tgz_name}" ] || [ ! -f "${plugin_dir}/${tgz_name}" ]; then
    log "ERROR: npm pack did not produce a tarball in ${plugin_dir}"
    return 1
  fi
  tgz_path="${plugin_dir}/${tgz_name}"
  remote_tgz="/tmp/${tgz_name}"
  # Clean up the local tarball on every return path (success, upload failure, install failure).
  trap 'rm -f "${tgz_path}"; trap - RETURN' RETURN

  # --dangerously-force-unsafe-install: the plugin's index.ts uses child_process (npx skills add agent-browser,
  # systemctl daemon-reload), which OpenClaw's install-time scanner flags. We trust this first-party plugin.
  # printf %q shell-escapes both interpolated values so a quote in tgz_name or
  # OPENCLAW_PLUGIN_VARIANT can't break out of the remote shell command.
  printf -v install_cmd 'OPENCLAW_PLUGIN_VARIANT=%q openclaw plugins install %q --force --dangerously-force-unsafe-install' \
    "${OPENCLAW_PLUGIN_VARIANT}" "${remote_tgz}"
  log "Installing VSS OpenClaw plugin ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME} (variant=${OPENCLAW_PLUGIN_VARIANT})"
  log "Plugin install command: ${install_cmd}"

  if [[ "${container_name}" == nemoclaw-openshell-* ]]; then
    log "Streaming ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME}:${remote_tgz}"
    printf -v shell_cmd 'cat > %q' "${remote_tgz}"
    if ! openshell sandbox exec -n "${NEMOCLAW_SANDBOX_NAME}" -- sh -c "${shell_cmd}" < "${tgz_path}"; then
      log "ERROR: failed to stream ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME}"
      return 1
    fi

    printf -v shell_cmd '%s && rm -f %q' "${install_cmd}" "${remote_tgz}"
    if ! openshell sandbox exec -n "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc "${shell_cmd}"; then
      log "ERROR: openclaw plugins install failed for ${tgz_name}"
      return 1
    fi
  else
    log "Streaming ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME}:${remote_tgz}"
    if ! sudo docker exec -i "${container_name}" kubectl exec -i -n "${VSS_NAMESPACE}" "${NEMOCLAW_SANDBOX_NAME}" -- \
        sh -c "cat > '${remote_tgz}'" < "${tgz_path}"; then
      log "ERROR: failed to stream ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME}"
      return 1
    fi

    if ! sudo docker exec "${container_name}" kubectl exec -n "${VSS_NAMESPACE}" "${NEMOCLAW_SANDBOX_NAME}" -- \
        sh -lc "$(printf 'su - sandbox -c %q && rm -f %q' "${install_cmd}" "${remote_tgz}")"; then
      log "ERROR: openclaw plugins install failed for ${tgz_name}"
      return 1
    fi
  fi

  log "VSS OpenClaw plugin installed"
  restart_vss_openclaw_gateway || return 1
  ensure_dashboard_forward || return 1
}

validate_custom_provider() {
  if [ "${NEMOCLAW_PROVIDER}" != "custom" ]; then
    return 0
  fi
  if [ -z "${NEMOCLAW_ENDPOINT_URL}" ]; then
    log "ERROR: NEMOCLAW_PROVIDER=custom requires NEMOCLAW_ENDPOINT_URL (or --endpoint-url)."
    exit 1
  fi
  if [ -z "${COMPATIBLE_API_KEY}" ]; then
    log "ERROR: NEMOCLAW_PROVIDER=custom requires COMPATIBLE_API_KEY (or --compatible-api-key)."
    exit 1
  fi
}

export_provider_env() {
  export NEMOCLAW_PROVIDER
  export NEMOCLAW_MODEL
  export NEMOCLAW_NON_INTERACTIVE
  export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE
  export NVIDIA_API_KEY
  if [ "${NEMOCLAW_PROVIDER}" = "custom" ]; then
    export NEMOCLAW_ENDPOINT_URL
    export COMPATIBLE_API_KEY
  fi
}

run_onboard() {
  local nemoclaw_cmd
  nemoclaw_cmd="$(resolve_nemoclaw)" || {
    log "nemoclaw is not currently resolvable"
    exit 1
  }

  log "Running nemoclaw onboard (NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER})"
  export_provider_env
  "$nemoclaw_cmd" onboard --non-interactive
}

run_install() {
  local install_script="${NEMOCLAW_REPO_DIR}/install.sh"

  if [ ! -x "$install_script" ]; then
    log "${install_script} is not available"
    exit 1
  fi

  log "Running NemoClaw installer (NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER})"
  (
    export_provider_env
    export NEMOCLAW_SANDBOX_NAME
    cd "$NEMOCLAW_REPO_DIR" && ./install.sh --non-interactive
  )
}

sandbox_exists() {
  have openshell && openshell sandbox get "$NEMOCLAW_SANDBOX_NAME" >/dev/null 2>&1
}

strip_ansi() {
  sed -E 's/\x1B\[[0-9;]*[[:alpha:]]//g'
}

sandbox_ready() {
  have openshell || return 1
  openshell sandbox list 2>/dev/null \
    | strip_ansi \
    | awk -v name="$NEMOCLAW_SANDBOX_NAME" '$1 == name && $NF == "Ready" { found = 1 } END { exit found ? 0 : 1 }' \
    || return 1
  openshell sandbox exec -n "$NEMOCLAW_SANDBOX_NAME" -- sh -lc 'command -v openclaw >/dev/null' >/dev/null 2>&1
}

wait_for_sandbox_ready() {
  local timeout deadline
  timeout="${1:-${NEMOCLAW_SANDBOX_READY_TIMEOUT:-300}}"
  deadline=$((SECONDS + timeout))

  if have nemoclaw; then
    log "NemoClaw status before sandbox readiness check"
    nemoclaw status || true
  fi

  log "Waiting for sandbox ${NEMOCLAW_SANDBOX_NAME} to be Ready"
  while [ "$SECONDS" -lt "$deadline" ]; do
    if sandbox_ready; then
      log "Sandbox ${NEMOCLAW_SANDBOX_NAME} is Ready"
      return 0
    fi
    sleep 5
  done

  log "ERROR: sandbox ${NEMOCLAW_SANDBOX_NAME} did not become Ready within ${timeout}s"
  openshell sandbox get "$NEMOCLAW_SANDBOX_NAME" || true
  return 1
}

main() {
  # Non-interactive shells often skip .bashrc; load nvm/node before nemoclaw (env node shebang).
  refresh_path

  if sandbox_exists; then
    log "Sandbox ${NEMOCLAW_SANDBOX_NAME} already exists; skipping NemoClaw onboard/install"
    configure_openshell_provider
  else
    log "Start installing/onboarding NemoClaw"
    if have nemoclaw; then
      run_onboard
    else
      run_install
    fi
    log "Finished installing/onboarding NemoClaw"
  fi

  # Onboard can return before OpenClaw is executable inside the sandbox.
  wait_for_sandbox_ready
  refresh_path
  ensure_dashboard_forward
  apply_vss_policy
  update_openclaw_allowed_origin
  # Policy/config updates can briefly flap gateway readiness before plugin install.
  wait_for_sandbox_ready "${NEMOCLAW_POST_CONFIG_READY_TIMEOUT:-60}"
  install_vss_openclaw_plugin

  log "To use nemoclaw in your current shell, run:"
  printf '\n  . "%s/nvm.sh"\n\n' "${NVM_DIR:-$HOME/.nvm}"
}

parse_args "$@"
validate_custom_provider
export NEMOCLAW_SANDBOX_NAME NEMOCLAW_PROVIDER OPENSHELL_PROVIDER_NAME NEMOCLAW_MODEL NEMOCLAW_NON_INTERACTIVE NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE
export NEMOCLAW_ENDPOINT_URL COMPATIBLE_API_KEY
export NEMOCLAW_REPO_DIR OPENCLAW_CONFIG_UPDATE_SCRIPT NEMOCLAW_POLICY_FILE

main
