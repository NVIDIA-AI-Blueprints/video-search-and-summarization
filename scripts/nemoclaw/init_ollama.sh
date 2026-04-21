#!/usr/bin/env bash
# Sets up a local Ollama server (qwen3.5 by default) and installs the VSS
# OpenClaw plugin into a NemoClaw sandbox.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
VSS_REPO_DIR="${VSS_REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-demo}"
NEMOCLAW_PROVIDER="ollama"
NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-qwen3.5}"
NEMOCLAW_NON_INTERACTIVE=1
NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
OLLAMA_MODEL="${OLLAMA_MODEL:-$NEMOCLAW_MODEL}"
OLLAMA_HOST="${OLLAMA_HOST:-0.0.0.0:11434}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.openshell.internal:11434/v1}"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
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
  printf '[init_vss_ollama] %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

usage() {
  cat <<'EOF'
Usage:
  bash init_vss_ollama.sh [options]
  bash init_vss_ollama.sh [sandbox-name] [model]

Options:
  --sandbox-name NAME         Sandbox name (default: demo)
  --model NAME                Model name for NemoClaw and Ollama (default: qwen3.5)
  --ollama-model NAME         Ollama model name override
  --ollama-host HOST:PORT     Ollama bind address (default: 0.0.0.0:11434)
  --ollama-base-url URL       OpenShell base URL for Ollama
  --cuda-visible-devices IDS  CUDA device selection (default: 1)
  --openclaw-config-script PATH
                              Path to the OpenClaw config update helper
  --policy-file PATH          Path to the custom sandbox policy file
  --help                      Show this help
EOF
}

parse_args() {
  local positional=()

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --sandbox-name)
        NEMOCLAW_SANDBOX_NAME="$2"
        shift 2
        ;;
      --model)
        NEMOCLAW_MODEL="$2"
        OLLAMA_MODEL="$2"
        shift 2
        ;;
      --ollama-model)
        OLLAMA_MODEL="$2"
        shift 2
        ;;
      --ollama-host)
        OLLAMA_HOST="$2"
        shift 2
        ;;
      --ollama-base-url)
        OLLAMA_BASE_URL="$2"
        shift 2
        ;;
      --cuda-visible-devices)
        CUDA_VISIBLE_DEVICES="$2"
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
  if [ "${#positional[@]}" -ge 2 ]; then
    NEMOCLAW_MODEL="${positional[1]}"
    OLLAMA_MODEL="${positional[1]}"
  fi
  if [ "${#positional[@]}" -gt 2 ]; then
    log "Too many positional arguments"
    usage
    exit 1
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

install_ollama_if_needed() {
  if have ollama; then
    log "Ollama already installed"
    return
  fi

  if ! have curl; then
    log "curl is required to install Ollama"
    exit 1
  fi

  log "Installing Ollama"
  local tmpdir script
  tmpdir="$(mktemp -d)"
  script="$tmpdir/install_ollama.sh"
  curl -fsSL https://ollama.com/install.sh -o "$script"
  sh "$script"
  rm -rf "$tmpdir"
}

start_ollama_server() {
  if have systemctl && have sudo; then
    sudo -n systemctl stop ollama >/dev/null 2>&1 || true
  fi

  pkill -f "ollama serve" >/dev/null 2>&1 || true

  log "Starting Ollama server on $OLLAMA_HOST"
  nohup env OLLAMA_HOST="$OLLAMA_HOST" CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES" \
    ollama serve >/tmp/ollama.log 2>&1 &

  local attempt
  for attempt in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      log "Ollama is ready"
      return
    fi
    sleep 1
  done

  log "Ollama did not become ready; check /tmp/ollama.log"
  exit 1
}

ensure_ollama_model() {
  local installed_model
  while IFS= read -r installed_model; do
    if [ "$installed_model" = "$OLLAMA_MODEL" ]; then
      log "Ollama model $OLLAMA_MODEL already present"
      return
    fi
    if [[ "$OLLAMA_MODEL" != *:* ]] && [ "$installed_model" = "${OLLAMA_MODEL}:latest" ]; then
      log "Ollama model $installed_model already present"
      return
    fi
  done < <(ollama list 2>/dev/null | awk 'NR > 1 && $1 != "" { print $1 }')

  log "Pulling Ollama model $OLLAMA_MODEL"
  ollama pull "$OLLAMA_MODEL"
}

configure_openshell_provider() {
  if ! have openshell; then
    log "OpenShell not available yet; skipping provider setup for now"
    return
  fi

  log "Configuring OpenShell provider $NEMOCLAW_PROVIDER for local Ollama"
  if openshell provider get "$NEMOCLAW_PROVIDER" >/dev/null 2>&1; then
    if ! env OPENAI_API_KEY=empty openshell provider update \
      --credential OPENAI_API_KEY \
      --config "OPENAI_BASE_URL=$OLLAMA_BASE_URL" \
      "$NEMOCLAW_PROVIDER"; then
      log "Provider update failed; continuing with existing provider config"
    fi
  else
    if ! env OPENAI_API_KEY=empty openshell provider create \
      --name "$NEMOCLAW_PROVIDER" \
      --type openai \
      --credential OPENAI_API_KEY \
      --config "OPENAI_BASE_URL=$OLLAMA_BASE_URL"; then
      log "Provider create failed; continuing"
    fi
  fi

  openshell inference set --provider "$NEMOCLAW_PROVIDER" --model "$OLLAMA_MODEL"
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
  if [ ! -d "${VSS_REPO_DIR}/.openclaw" ]; then
    log "${VSS_REPO_DIR}/.openclaw is not available; skipping VSS OpenClaw plugin install"
    return
  fi

  if ! have openshell; then
    log "OpenShell is not available; skipping VSS OpenClaw plugin install"
    return
  fi

  if ! have python3; then
    log "python3 is not available; skipping VSS OpenClaw plugin install"
    return
  fi

  if ! openshell sandbox list >/dev/null 2>&1; then
    log "OpenShell sandbox access is not ready; skipping VSS OpenClaw plugin install"
    return
  fi

  local tmpdir normalized_dir container_name
  tmpdir="$(mktemp -d)"
  normalized_dir="${tmpdir}/${VSS_PLUGIN_ID}-package"
  mkdir -p "${normalized_dir}"

  log "Normalizing VSS OpenClaw plugin layout from ${VSS_REPO_DIR}"
  python3 - <<'PY' "${VSS_REPO_DIR}" "${normalized_dir}"
import json
import pathlib
import shutil
import sys

extract_dir = pathlib.Path(sys.argv[1])
normalized_dir = pathlib.Path(sys.argv[2])

plugin_root = extract_dir / ".openclaw"
if not plugin_root.is_dir():
    raise SystemExit(f"Expected plugin directory missing: {plugin_root}")

skills_root = extract_dir / "skills"
if not skills_root.is_dir():
    alt_skills_root = plugin_root / "skills"
    if alt_skills_root.is_dir():
        skills_root = alt_skills_root
    else:
        raise SystemExit(
            f"Expected skills directory missing: {extract_dir / 'skills'}"
        )

for name in ("README.md", "index.ts", "openclaw.plugin.json", "package.json"):
    src = plugin_root / name
    if not src.is_file():
        raise SystemExit(f"Expected file missing: {src}")
    shutil.copy2(src, normalized_dir / name)

workspace_root = plugin_root / "workspace"
if workspace_root.is_dir():
    shutil.copytree(workspace_root, normalized_dir / "workspace", dirs_exist_ok=True)

shutil.copytree(skills_root, normalized_dir / "skills", dirs_exist_ok=True)

replacements = {
    "http://localhost:30888": "http://host.openshell.internal:30888",
    "http://localhost:8000": "http://host.openshell.internal:8000",
    "http://localhost:9901": "http://host.openshell.internal:9901",
    "http://localhost:3000": "http://host.openshell.internal:3000",
    "http://<HOST_IP>:30888": "http://host.openshell.internal:30888",
    "http://<HOST_IP>:8000": "http://host.openshell.internal:8000",
    "http://<HOST_IP>:3000": "http://host.openshell.internal:3000",
}

for skill_file in (normalized_dir / "skills").rglob("SKILL.md"):
    content = skill_file.read_text()
    updated = content
    for old, new in replacements.items():
        updated = updated.replace(old, new)
    if updated != content:
        skill_file.write_text(updated)

manifest_path = normalized_dir / "openclaw.plugin.json"
manifest = json.loads(manifest_path.read_text())
manifest["skills"] = ["./skills"]
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
PY

  log "Uploading VSS OpenClaw plugin to sandbox ${NEMOCLAW_SANDBOX_NAME}"
  openshell sandbox upload "${NEMOCLAW_SANDBOX_NAME}" "${normalized_dir}" "${VSS_REMOTE_UPLOAD_DIR}"

  container_name="$(resolve_vss_gateway_container)"
  if [ -z "${container_name}" ]; then
    log "Could not determine the OpenShell gateway container; skipping VSS OpenClaw plugin install"
    rm -rf "${tmpdir}"
    return
  fi

  log "Installing VSS OpenClaw plugin inside sandbox ${NEMOCLAW_SANDBOX_NAME} via ${container_name}"
  sudo docker exec "${container_name}" kubectl exec -n "${VSS_NAMESPACE}" "${NEMOCLAW_SANDBOX_NAME}" -- sh -lc \
    "set -e; \
     mkdir -p '${VSS_REMOTE_EXTENSIONS_ROOT}' /sandbox/.openclaw-data /sandbox/.openclaw-data/agents/main/sessions; \
     if [ ! -f '${VSS_REMOTE_CONFIG_PATH}' ] && [ -f /sandbox/.openclaw/openclaw.json ]; then \
       cp /sandbox/.openclaw/openclaw.json '${VSS_REMOTE_CONFIG_PATH}'; \
     fi; \
     rm -rf '${VSS_REMOTE_PLUGIN_DIR}' '${VSS_REMOTE_EXTENSIONS_ROOT}/skills'; \
     chown -R sandbox:sandbox /sandbox/.openclaw-data '${VSS_REMOTE_UPLOAD_DIR}'; \
     su - sandbox -c \"OPENCLAW_STATE_DIR=/sandbox/.openclaw-data OPENCLAW_CONFIG_PATH='${VSS_REMOTE_CONFIG_PATH}' openclaw plugins install '${VSS_REMOTE_UPLOAD_DIR}'\"; \
     su - sandbox -c \"OPENCLAW_STATE_DIR=/sandbox/.openclaw-data OPENCLAW_CONFIG_PATH='${VSS_REMOTE_CONFIG_PATH}' openclaw skills list | grep -i openclaw-extra || true\";"

  log "VSS OpenClaw plugin installed"
  rm -rf "${tmpdir}"
}

sync_vss_skills_to_sandbox_workspace() {
  if ! have openshell; then
    log "OpenShell is not available; skipping workspace skills upload"
    return
  fi
  if [ ! -d "${VSS_REPO_DIR}/skills" ]; then
    log "${VSS_REPO_DIR}/skills not found; skipping workspace skills upload"
    return
  fi
  if ! openshell sandbox list >/dev/null 2>&1; then
    log "OpenShell sandbox access is not ready; skipping workspace skills upload"
    return
  fi

  local remote_skills_dir="/sandbox/.openclaw/workspace/skills"
  log "Uploading ${VSS_REPO_DIR}/skills to sandbox ${NEMOCLAW_SANDBOX_NAME}:${remote_skills_dir}"
  if ! openshell sandbox upload "${NEMOCLAW_SANDBOX_NAME}" "${VSS_REPO_DIR}/skills" "${remote_skills_dir}"; then
    log "openshell sandbox upload for skills failed; copy manually, e.g.: openshell sandbox upload ${NEMOCLAW_SANDBOX_NAME} ${VSS_REPO_DIR}/skills ${remote_skills_dir}"
  fi
}

run_onboard() {
  local nemoclaw_cmd
  nemoclaw_cmd="$(resolve_nemoclaw)" || {
    log "nemoclaw is not currently resolvable"
    exit 1
  }

  log "Running nemoclaw onboard"
  "$nemoclaw_cmd" onboard --non-interactive
}

run_install() {
  if [ ! -x /home/ubuntu/NemoClaw/install.sh ]; then
    log "/home/ubuntu/NemoClaw/install.sh is not available"
    exit 1
  fi

  log "Running NemoClaw installer"
  (cd /home/ubuntu/NemoClaw && ./install.sh --non-interactive)
}

main() {
  install_ollama_if_needed
  start_ollama_server
  ensure_ollama_model

  refresh_path
  log "Start installing/onboarding NemoClaw"
  if have nemoclaw; then
    run_onboard
  else
    run_install
  fi
  log "Finished installing/onboarding NemoClaw"

  refresh_path
  configure_openshell_provider
  apply_vss_policy
  install_vss_openclaw_plugin
  update_openclaw_allowed_origin

  log "To use nemoclaw in your current shell, run:"
  printf '\n  . "%s/nvm.sh"\n\n' "${NVM_DIR:-$HOME/.nvm}"
}

parse_args "$@"
export NEMOCLAW_SANDBOX_NAME NEMOCLAW_PROVIDER NEMOCLAW_MODEL NEMOCLAW_NON_INTERACTIVE NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE
export OLLAMA_MODEL OLLAMA_HOST OLLAMA_BASE_URL CUDA_VISIBLE_DEVICES
export OPENCLAW_CONFIG_UPDATE_SCRIPT NEMOCLAW_POLICY_FILE

main
