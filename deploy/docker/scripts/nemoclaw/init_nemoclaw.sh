#!/usr/bin/env bash
# Configures NemoClaw to use NVIDIA's hosted Nemotron model via the NVIDIA API.
# Requires a valid NVIDIA API key passed via --nvidia-api-key, NVIDIA_API_KEY env var, or interactive prompt.
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
OPENCLAW_CHANNEL_SLACK_ENABLED="${OPENCLAW_CHANNEL_SLACK_ENABLED:-0}"
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
SLACK_APP_TOKEN="${SLACK_APP_TOKEN:-}"
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
  printf '[init_nvidia_remote] %s\n' "$*"
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

  The API key is resolved in this order:
    1. --nvidia-api-key flag (overrides env)
    2. NVIDIA_API_KEY environment variable
    3. Interactive prompt (if neither is set)

Options:
  --nvidia-api-key KEY        NVIDIA API key (optional if NVIDIA_API_KEY env is set)
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
  OPENCLAW_CHANNEL_SLACK_ENABLED   Set to "1" to register the Slack messaging channel during
                              onboard. Requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN. Accepts only
                              0 or 1. (default: 0)
  SLACK_BOT_TOKEN             Slack Bot User OAuth token (xoxb-...) — required when
                              OPENCLAW_CHANNEL_SLACK_ENABLED=1.
  SLACK_APP_TOKEN             Slack App-Level token for Socket Mode (xapp-...) — required when
                              OPENCLAW_CHANNEL_SLACK_ENABLED=1.
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

  if [ -z "${NVIDIA_API_KEY:-}" ]; then
    read -rsp "Enter your NVIDIA API key: " NVIDIA_API_KEY
    printf '\n'
    if [ -z "${NVIDIA_API_KEY:-}" ]; then
      log "ERROR: NVIDIA API key is required."
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
  if ! env OPENAI_API_KEY="$openai_api_key" openshell "${provider_args[@]}"; then
    log "Provider ${action} failed; continuing with existing provider config"
  fi

  openshell inference set --provider "$OPENSHELL_PROVIDER_NAME" --model "$NEMOCLAW_MODEL"
  openshell inference get || true
}

# `nemoclaw onboard` creates the dashboard port-forward (default 18789) that exposes the in-pod
# openclaw-gateway (and its /hooks endpoint) to the host. When the sandbox already exists we skip
# onboard, and the forward can also die independently between runs — so refresh it unconditionally.
ensure_dashboard_forward() {
  local port="${NEMOCLAW_DASHBOARD_PORT:-18789}"
  if ! have openshell; then
    log "OpenShell not available yet; skipping dashboard port-forward refresh"
    return
  fi
  log "Refreshing dashboard port-forward on ${port} for sandbox ${NEMOCLAW_SANDBOX_NAME}"
  openshell forward stop "$port" >/dev/null 2>&1 || true
  if ! openshell forward start --background "$port" "$NEMOCLAW_SANDBOX_NAME" </dev/null >/dev/null 2>&1; then
    log "WARN: could not (re)start dashboard forward on ${port}; the OpenClaw UI and /hooks endpoint will be unreachable at http://127.0.0.1:${port}"
  fi
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

  docker ps --format '{{.Names}}' | awk '/^openshell-cluster-/{print; exit}'
}

apply_vss_policy() {
  local policy_file="${NEMOCLAW_POLICY_FILE}"

  if ! have openshell; then
    log "ERROR: OpenShell is not available; cannot apply custom policy from ${policy_file}"
    return 1
  fi

  if [ ! -f "$policy_file" ]; then
    log "ERROR: Policy file ${policy_file} is not available"
    return 1
  fi

  log "Applying custom policy file ${policy_file} to sandbox ${NEMOCLAW_SANDBOX_NAME}"
  openshell policy set --policy "$policy_file" --wait "$NEMOCLAW_SANDBOX_NAME"
}

install_vss_openclaw_plugin() {
  local plugin_dir tgz_name tgz_path container_name remote_tgz install_cmd
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

  # Stream the tarball into the agent container via kubectl exec stdin. `openshell
  # sandbox upload` silently dropped the file (reported success, but it never landed
  # anywhere visible to the install step), so we write the bytes directly through the
  # same kubectl exec path the install will use.
  log "Streaming ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME}:${remote_tgz}"
  if ! sudo docker exec -i "${container_name}" kubectl exec -i -n "${VSS_NAMESPACE}" "${NEMOCLAW_SANDBOX_NAME}" -- \
      sh -c "cat > '${remote_tgz}'" < "${tgz_path}"; then
    log "ERROR: failed to stream ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME}"
    return 1
  fi

  # --dangerously-force-unsafe-install: the plugin's index.ts uses child_process (npx skills add agent-browser,
  # systemctl daemon-reload), which OpenClaw's install-time scanner flags. We trust this first-party plugin.
  # printf %q shell-escapes both interpolated values so a quote in tgz_name or
  # OPENCLAW_PLUGIN_VARIANT can't break out of `su - sandbox -c`'s quoting.
  printf -v install_cmd 'OPENCLAW_PLUGIN_VARIANT=%q openclaw plugins install %q --force --dangerously-force-unsafe-install' \
    "${OPENCLAW_PLUGIN_VARIANT}" "${remote_tgz}"
  log "Installing VSS OpenClaw plugin ${tgz_name} into sandbox ${NEMOCLAW_SANDBOX_NAME} (variant=${OPENCLAW_PLUGIN_VARIANT})"
  log "Plugin install command: ${install_cmd}"
  if ! sudo docker exec "${container_name}" kubectl exec -n "${VSS_NAMESPACE}" "${NEMOCLAW_SANDBOX_NAME}" -- \
      sh -lc "$(printf 'su - sandbox -c %q && rm -f %q' "${install_cmd}" "${remote_tgz}")"; then
    log "ERROR: openclaw plugins install failed for ${tgz_name}"
    return 1
  fi

  log "VSS OpenClaw plugin installed"
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

validate_slack_channel() {
  if [ "${OPENCLAW_CHANNEL_SLACK_ENABLED:-0}" != "1" ]; then
    return 0
  fi
  if [ -z "${SLACK_BOT_TOKEN}" ]; then
    log "ERROR: OPENCLAW_CHANNEL_SLACK_ENABLED=1 requires SLACK_BOT_TOKEN (xoxb-...)."
    exit 1
  fi
  if [ -z "${SLACK_APP_TOKEN}" ]; then
    log "ERROR: OPENCLAW_CHANNEL_SLACK_ENABLED=1 requires SLACK_APP_TOKEN (xapp-...)."
    exit 1
  fi
}

build_provider_env_args() {
  local -n out=$1
  out=(
    "NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER}"
    "NEMOCLAW_MODEL=${NEMOCLAW_MODEL}"
    "NEMOCLAW_NON_INTERACTIVE=${NEMOCLAW_NON_INTERACTIVE}"
    "NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=${NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE}"
    "NVIDIA_API_KEY=${NVIDIA_API_KEY}"
  )
  if [ "${OPENCLAW_CHANNEL_SLACK_ENABLED:-0}" = "1" ]; then
    out+=(
      "SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}"
      "SLACK_APP_TOKEN=${SLACK_APP_TOKEN}"
    )
  fi
  if [ "${NEMOCLAW_PROVIDER}" = "custom" ]; then
    out+=(
      "NEMOCLAW_ENDPOINT_URL=${NEMOCLAW_ENDPOINT_URL}"
      "COMPATIBLE_API_KEY=${COMPATIBLE_API_KEY}"
    )
  fi
}

run_onboard() {
  local nemoclaw_cmd
  nemoclaw_cmd="$(resolve_nemoclaw)" || {
    log "nemoclaw is not currently resolvable"
    exit 1
  }

  log "Running nemoclaw onboard (NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER})"
  local env_args
  build_provider_env_args env_args
  env "${env_args[@]}" "$nemoclaw_cmd" onboard --non-interactive
}

run_install() {
  local install_script="${NEMOCLAW_REPO_DIR}/install.sh"

  if [ ! -x "$install_script" ]; then
    log "${install_script} is not available"
    exit 1
  fi

  log "Running NemoClaw installer (NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER})"
  local env_args
  build_provider_env_args env_args
  env_args+=( "NEMOCLAW_SANDBOX_NAME=${NEMOCLAW_SANDBOX_NAME}" )
  (
    cd "$NEMOCLAW_REPO_DIR" && env "${env_args[@]}" ./install.sh --non-interactive
  )
}

sandbox_exists() {
  have openshell && openshell sandbox get "$NEMOCLAW_SANDBOX_NAME" >/dev/null 2>&1
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

  refresh_path
  ensure_dashboard_forward
  apply_vss_policy
  update_openclaw_allowed_origin
  install_vss_openclaw_plugin

  log "To use nemoclaw in your current shell, run:"
  printf '\n  . "%s/nvm.sh"\n\n' "${NVM_DIR:-$HOME/.nvm}"
}

parse_args "$@"
validate_custom_provider
validate_slack_channel
export NEMOCLAW_SANDBOX_NAME NEMOCLAW_PROVIDER OPENSHELL_PROVIDER_NAME NEMOCLAW_MODEL NEMOCLAW_NON_INTERACTIVE NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE
export NEMOCLAW_ENDPOINT_URL COMPATIBLE_API_KEY
export NEMOCLAW_REPO_DIR OPENCLAW_CONFIG_UPDATE_SCRIPT NEMOCLAW_POLICY_FILE

main
