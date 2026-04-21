#!/usr/bin/env bash
# Configures NemoClaw to use NVIDIA's hosted Nemotron model via the NVIDIA API.
# Requires a valid NVIDIA API key passed via --api-key, NVIDIA_API_KEY env var, or interactive prompt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
VSS_REPO_DIR="${VSS_REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-demo}"
# Nemoclaw onboard/install only accepts: build, openai, … — "build" is NVIDIA Endpoints (integrate.api.nvidia.com).
NEMOCLAW_ONBOARD_PROVIDER="${NEMOCLAW_ONBOARD_PROVIDER:-build}"
# OpenShell provider display name (separate from Nemoclaw's NEMOCLAW_PROVIDER for onboard).
OPENSHELL_PROVIDER_NAME="${OPENSHELL_PROVIDER_NAME:-nvidia}"
NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-nvidia/nemotron-3-super-120b-a12b}"
NEMOCLAW_NON_INTERACTIVE=1
NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
NVIDIA_API_KEY="${NVIDIA_API_KEY:-}"
NVIDIA_BASE_URL="${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}"
NEMOCLAW_SHIM_DIR="${HOME}/.local/bin"
OPENCLAW_CONFIG_UPDATE_SCRIPT="${OPENCLAW_CONFIG_UPDATE_SCRIPT:-${SCRIPT_DIR}/update_openclaw_config.py}"
NEMOCLAW_POLICY_FILE="${NEMOCLAW_POLICY_FILE:-${VSS_REPO_DIR}/assets/vss_nemoclaw_policy.yaml}"
VSS_PLUGIN_ID="openclaw-vss"
VSS_NAMESPACE="${VSS_NAMESPACE:-openshell}"
VSS_REMOTE_EXTENSIONS_ROOT="/sandbox/.openclaw-data/extensions"
VSS_REMOTE_PLUGIN_DIR="${VSS_REMOTE_EXTENSIONS_ROOT}/${VSS_PLUGIN_ID}"
VSS_REMOTE_CONFIG_PATH="/sandbox/.openclaw-data/openclaw.json"
VSS_REMOTE_UPLOAD_DIR="/tmp/${VSS_PLUGIN_ID}-package"

log() {
  printf '[init_nvidia_remote] %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

usage() {
  cat <<'EOF'
Usage:
  bash init_nemoclaw.sh [--nvidia-api-key <KEY>] [options]
  NVIDIA_API_KEY=<key> bash init_nemoclaw.sh [options]

  The API key is resolved in this order:
    1. --nvidia-api-key / --api-key flag (overrides env)
    2. NVIDIA_API_KEY environment variable
    3. Interactive prompt (if neither is set)

Options:
  --nvidia-api-key KEY        NVIDIA API key (optional if NVIDIA_API_KEY env is set)
  --sandbox-name NAME         Sandbox name (default: demo)
  --model NAME                NVIDIA model ID (default: nvidia/nemotron-3-super-120b-a12b)
  --nvidia-base-url URL       NVIDIA API base URL (default: https://integrate.api.nvidia.com/v1)
  --openclaw-config-script PATH
                              Path to the OpenClaw config update helper
  --policy-file PATH          Path to the custom sandbox policy file
  --help                      Show this help

Environment (non-interactive Nemoclaw / OpenShell):
  NEMOCLAW_ONBOARD_PROVIDER   Nemoclaw onboard/install provider (default: build = NVIDIA Endpoints / integrate.api.nvidia.com)
  OPENSHELL_PROVIDER_NAME     Name for openshell OpenAI-compatible provider (default: nvidia)
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
  if have node; then
    return 0
  fi
  if [ -z "${NVM_DIR:-}" ]; then
    export NVM_DIR="$HOME/.nvm"
  fi
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # shellcheck disable=SC1090
    . "$NVM_DIR/nvm.sh"
    # Sourcing nvm.sh alone often leaves no node on PATH; nemoclaw uses `#!/usr/bin/env node`.
    if ! have node; then
      nvm use default >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || true
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

  log "Configuring OpenShell provider $OPENSHELL_PROVIDER_NAME for NVIDIA remote API"
  if openshell provider get "$OPENSHELL_PROVIDER_NAME" >/dev/null 2>&1; then
    if ! env OPENAI_API_KEY="$NVIDIA_API_KEY" openshell provider update \
      --credential OPENAI_API_KEY \
      --config "OPENAI_BASE_URL=$NVIDIA_BASE_URL" \
      "$OPENSHELL_PROVIDER_NAME"; then
      log "Provider update failed; continuing with existing provider config"
    fi
  else
    if ! env OPENAI_API_KEY="$NVIDIA_API_KEY" openshell provider create \
      --name "$OPENSHELL_PROVIDER_NAME" \
      --type openai \
      --credential OPENAI_API_KEY \
      --config "OPENAI_BASE_URL=$NVIDIA_BASE_URL"; then
      log "Provider create failed; continuing"
    fi
  fi

  openshell inference set --provider "$OPENSHELL_PROVIDER_NAME" --model "$NEMOCLAW_MODEL"
  openshell inference get || true
}

update_openclaw_allowed_origin() {
  local script="${OPENCLAW_CONFIG_UPDATE_SCRIPT}"

  if [ ! -f "$script" ]; then
    log "$script is not available; skipping OpenClaw config update"
    return
  fi

  if ! have python3; then
    log "python3 is not available; skipping OpenClaw config update"
    return
  fi

  log "Updating OpenClaw config for sandbox $NEMOCLAW_SANDBOX_NAME"
  if ! python3 "$script" "$NEMOCLAW_SANDBOX_NAME"; then
    log "OpenClaw config update failed; continuing"
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
    log "OpenShell is not available; skipping custom policy apply"
    return
  fi

  if [ ! -f "$policy_file" ]; then
    log "$policy_file is not available; skipping custom policy apply"
    return
  fi

  log "Applying custom policy to sandbox $NEMOCLAW_SANDBOX_NAME"
  openshell policy set --policy "$policy_file" --wait "$NEMOCLAW_SANDBOX_NAME"
}

install_vss_openclaw_plugin() {
  local skills_root remote_skills_dir remote_upload_dir container_name
  skills_root="${VSS_REPO_DIR}/skills"
  remote_skills_dir="/sandbox/.openclaw-data/workspace/skills"
  remote_upload_dir="/tmp/${VSS_PLUGIN_ID}-skills"

  if [ ! -d "${skills_root}" ]; then
    log "${skills_root} is not available; skipping VSS skills install"
    return
  fi

  if ! have openshell; then
    log "OpenShell is not available; skipping VSS skills install"
    return
  fi

  if ! openshell sandbox list >/dev/null 2>&1; then
    log "OpenShell sandbox access is not ready; skipping VSS skills install"
    return
  fi

  container_name="$(resolve_vss_gateway_container)"
  if [ -z "${container_name}" ]; then
    log "Could not determine the OpenShell gateway container; skipping VSS skills install"
    return
  fi

  log "Preparing sandbox skills staging directory inside ${NEMOCLAW_SANDBOX_NAME}"
  sudo docker exec "${container_name}" kubectl exec -n "${VSS_NAMESPACE}" "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc \
    "rm -rf '${remote_upload_dir}' && su - sandbox -c \"mkdir -p '${remote_upload_dir}'\""

  log "Uploading ${skills_root} to sandbox ${NEMOCLAW_SANDBOX_NAME}:${remote_upload_dir}"
  openshell sandbox upload "${NEMOCLAW_SANDBOX_NAME}" "${skills_root}" "${remote_upload_dir}"

  log "Copying staged skills into sandbox workspace inside ${NEMOCLAW_SANDBOX_NAME} via ${container_name}"
  sudo docker exec "${container_name}" kubectl exec -n "${VSS_NAMESPACE}" "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc \
    "rm -rf '${remote_skills_dir}' && su - sandbox -c \"mkdir -p '${remote_skills_dir}' && cp -r '${remote_upload_dir}/'* '${remote_skills_dir}/'\""

  log "VSS skills installed"
}

run_onboard() {
  local nemoclaw_cmd
  nemoclaw_cmd="$(resolve_nemoclaw)" || {
    log "nemoclaw is not currently resolvable"
    exit 1
  }

  log "Running nemoclaw onboard (NEMOCLAW_PROVIDER=${NEMOCLAW_ONBOARD_PROVIDER})"
  env \
    NEMOCLAW_PROVIDER="${NEMOCLAW_ONBOARD_PROVIDER}" \
    NEMOCLAW_MODEL="${NEMOCLAW_MODEL}" \
    NEMOCLAW_NON_INTERACTIVE="${NEMOCLAW_NON_INTERACTIVE}" \
    NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE="${NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE}" \
    NVIDIA_API_KEY="${NVIDIA_API_KEY}" \
    "$nemoclaw_cmd" onboard --non-interactive
}

run_install() {
  if [ ! -x /home/ubuntu/NemoClaw/install.sh ]; then
    log "/home/ubuntu/NemoClaw/install.sh is not available"
    exit 1
  fi

  log "Running NemoClaw installer (NEMOCLAW_PROVIDER=${NEMOCLAW_ONBOARD_PROVIDER})"
  (
    cd /home/ubuntu/NemoClaw && env \
      NEMOCLAW_PROVIDER="${NEMOCLAW_ONBOARD_PROVIDER}" \
      NEMOCLAW_MODEL="${NEMOCLAW_MODEL}" \
      NEMOCLAW_NON_INTERACTIVE="${NEMOCLAW_NON_INTERACTIVE}" \
      NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE="${NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE}" \
      NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME}" \
      NVIDIA_API_KEY="${NVIDIA_API_KEY}" \
      ./install.sh --non-interactive
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
  configure_openshell_provider
  apply_vss_policy
  install_vss_openclaw_plugin
  update_openclaw_allowed_origin

  log "To use nemoclaw in your current shell, run:"
  printf '\n  . "%s/nvm.sh"\n\n' "${NVM_DIR:-$HOME/.nvm}"
}

parse_args "$@"
export NEMOCLAW_SANDBOX_NAME NEMOCLAW_ONBOARD_PROVIDER OPENSHELL_PROVIDER_NAME NEMOCLAW_MODEL NEMOCLAW_NON_INTERACTIVE NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE
export OPENCLAW_CONFIG_UPDATE_SCRIPT NEMOCLAW_POLICY_FILE

main
