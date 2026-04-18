#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
VSS_REPO_DIR="${VSS_REPO_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-demo}"
NEMOCLAW_PROVIDER="nvidia-remote"
NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-nvidia/nvidia-nemotron-nano-9b-v2}"
NEMOCLAW_NON_INTERACTIVE=1
NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
NEMOCLAW_REMOTE_BASE_URL="${NEMOCLAW_REMOTE_BASE_URL:-https://integrate.api.nvidia.com/v1}"
NEMOCLAW_REMOTE_API_KEY="${NEMOCLAW_REMOTE_API_KEY:-${NVIDIA_API_KEY:-}}"
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
  printf '[run_nemoclaw_install] %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

usage() {
  cat <<'EOF'
Usage:
  bash /home/ubuntu/run_nemoclaw_install.sh [options]
  bash /home/ubuntu/run_nemoclaw_install.sh [sandbox-name] [model]

Options:
  --sandbox-name NAME         Sandbox name (default: demo)
  --model NAME                Model name for NemoClaw inference
  --remote-base-url URL       OpenAI-compatible base URL for remote provider
  --nvidia-api-key KEY        API key for remote provider (fallback: NVIDIA_API_KEY)
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
        shift 2
        ;;
      --remote-base-url)
        NEMOCLAW_REMOTE_BASE_URL="$2"
        shift 2
        ;;
      --nvidia-api-key)
        NEMOCLAW_REMOTE_API_KEY="$2"
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

ensure_openshell_installed() {
  refresh_path
  if have openshell; then
    log "OpenShell already installed"
    return
  fi

  if ! have curl; then
    log "curl is required to install OpenShell"
    exit 1
  fi
  if ! have tar; then
    log "tar is required to install OpenShell"
    exit 1
  fi

  local arch pattern repo download_tag tmpdir archive install_dir
  repo="NVIDIA/OpenShell"
  download_tag="$OPENSHELL_RELEASE_TAG"
  case "$(uname -m)" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      log "Unsupported architecture for OpenShell: $(uname -m)"
      exit 1
      ;;
  esac

  pattern="openshell-${arch}-unknown-linux-musl.tar.gz"
  tmpdir="$(mktemp -d)"
  archive="$tmpdir/$pattern"
  install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

  log "Installing OpenShell release $download_tag"
  if command -v gh >/dev/null 2>&1 && \
    gh release download "$download_tag" --repo "$repo" --pattern "$pattern" --dir "$tmpdir" >/dev/null 2>&1; then
    log "Downloaded OpenShell with gh"
  else
    log "Using direct OpenShell release download to avoid gh auth"
    if ! curl -fsSL "https://github.com/${repo}/releases/download/${download_tag}/${pattern}" -o "$archive"; then
      log "Direct release download failed; falling back to upstream install.sh"
      rm -rf "$tmpdir"
      curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
      refresh_path
      return
    fi
  fi

  mkdir -p "$install_dir"
  tar xzf "$archive" -C "$tmpdir"
  install -m 755 "$tmpdir/openshell" "$install_dir/openshell"
  rm -rf "$tmpdir"
  refresh_path
}

ensure_openshell_installed() {
  refresh_path
  if have openshell; then
    log "OpenShell already installed"
    return
  fi

  if ! have curl; then
    log "curl is required to install OpenShell"
    exit 1
  fi
  if ! have tar; then
    log "tar is required to install OpenShell"
    exit 1
  fi

  local arch pattern repo download_tag tmpdir archive install_dir
  repo="NVIDIA/OpenShell"
  download_tag="$OPENSHELL_RELEASE_TAG"
  case "$(uname -m)" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      log "Unsupported architecture for OpenShell: $(uname -m)"
      exit 1
      ;;
  esac

  pattern="openshell-${arch}-unknown-linux-musl.tar.gz"
  tmpdir="$(mktemp -d)"
  archive="$tmpdir/$pattern"
  install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

  log "Installing OpenShell release $download_tag"
  if command -v gh >/dev/null 2>&1 && \
    gh release download "$download_tag" --repo "$repo" --pattern "$pattern" --dir "$tmpdir" >/dev/null 2>&1; then
    log "Downloaded OpenShell with gh"
  else
    log "Using direct OpenShell release download to avoid gh auth"
    if ! curl -fsSL "https://github.com/${repo}/releases/download/${download_tag}/${pattern}" -o "$archive"; then
      log "Direct release download failed; falling back to upstream install.sh"
      rm -rf "$tmpdir"
      curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
      refresh_path
      return
    fi
  fi

  mkdir -p "$install_dir"
  tar xzf "$archive" -C "$tmpdir"
  install -m 755 "$tmpdir/openshell" "$install_dir/openshell"
  rm -rf "$tmpdir"
  refresh_path
}

ensure_openshell_installed() {
  refresh_path
  if have openshell; then
    log "OpenShell already installed"
    return
  fi

  if ! have curl; then
    log "curl is required to install OpenShell"
    exit 1
  fi
  if ! have tar; then
    log "tar is required to install OpenShell"
    exit 1
  fi

  local arch pattern repo download_tag tmpdir archive install_dir
  repo="NVIDIA/OpenShell"
  download_tag="$OPENSHELL_RELEASE_TAG"
  case "$(uname -m)" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      log "Unsupported architecture for OpenShell: $(uname -m)"
      exit 1
      ;;
  esac

  pattern="openshell-${arch}-unknown-linux-musl.tar.gz"
  tmpdir="$(mktemp -d)"
  archive="$tmpdir/$pattern"
  install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

  log "Installing OpenShell release $download_tag"
  if command -v gh >/dev/null 2>&1 && \
    gh release download "$download_tag" --repo "$repo" --pattern "$pattern" --dir "$tmpdir" >/dev/null 2>&1; then
    log "Downloaded OpenShell with gh"
  else
    log "Using direct OpenShell release download to avoid gh auth"
    if ! curl -fsSL "https://github.com/${repo}/releases/download/${download_tag}/${pattern}" -o "$archive"; then
      log "Direct release download failed; falling back to upstream install.sh"
      rm -rf "$tmpdir"
      curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
      refresh_path
      return
    fi
  fi

  mkdir -p "$install_dir"
  tar xzf "$archive" -C "$tmpdir"
  install -m 755 "$tmpdir/openshell" "$install_dir/openshell"
  rm -rf "$tmpdir"
  refresh_path
}

ensure_openshell_installed() {
  refresh_path
  if have openshell; then
    log "OpenShell already installed"
    return
  fi

  if ! have curl; then
    log "curl is required to install OpenShell"
    exit 1
  fi
  if ! have tar; then
    log "tar is required to install OpenShell"
    exit 1
  fi

  local arch pattern repo download_tag tmpdir archive install_dir
  repo="NVIDIA/OpenShell"
  download_tag="$OPENSHELL_RELEASE_TAG"
  case "$(uname -m)" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      log "Unsupported architecture for OpenShell: $(uname -m)"
      exit 1
      ;;
  esac

  pattern="openshell-${arch}-unknown-linux-musl.tar.gz"
  tmpdir="$(mktemp -d)"
  archive="$tmpdir/$pattern"
  install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

  log "Installing OpenShell release $download_tag"
  if command -v gh >/dev/null 2>&1 && \
    gh release download "$download_tag" --repo "$repo" --pattern "$pattern" --dir "$tmpdir" >/dev/null 2>&1; then
    log "Downloaded OpenShell with gh"
  else
    log "Using direct OpenShell release download to avoid gh auth"
    if ! curl -fsSL "https://github.com/${repo}/releases/download/${download_tag}/${pattern}" -o "$archive"; then
      log "Direct release download failed; falling back to upstream install.sh"
      rm -rf "$tmpdir"
      curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
      refresh_path
      return
    fi
  fi

  mkdir -p "$install_dir"
  tar xzf "$archive" -C "$tmpdir"
  install -m 755 "$tmpdir/openshell" "$install_dir/openshell"
  rm -rf "$tmpdir"
  refresh_path
}

ensure_openshell_installed() {
  refresh_path
  if have openshell; then
    log "OpenShell already installed"
    return
  fi

  if ! have curl; then
    log "curl is required to install OpenShell"
    exit 1
  fi
  if ! have tar; then
    log "tar is required to install OpenShell"
    exit 1
  fi

  local arch pattern repo download_tag tmpdir archive install_dir
  repo="NVIDIA/OpenShell"
  download_tag="$OPENSHELL_RELEASE_TAG"
  case "$(uname -m)" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      log "Unsupported architecture for OpenShell: $(uname -m)"
      exit 1
      ;;
  esac

  pattern="openshell-${arch}-unknown-linux-musl.tar.gz"
  tmpdir="$(mktemp -d)"
  archive="$tmpdir/$pattern"
  install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

  log "Installing OpenShell release $download_tag"
  if command -v gh >/dev/null 2>&1 && \
    gh release download "$download_tag" --repo "$repo" --pattern "$pattern" --dir "$tmpdir" >/dev/null 2>&1; then
    log "Downloaded OpenShell with gh"
  else
    log "Using direct OpenShell release download to avoid gh auth"
    if ! curl -fsSL "https://github.com/${repo}/releases/download/${download_tag}/${pattern}" -o "$archive"; then
      log "Direct release download failed; falling back to upstream install.sh"
      rm -rf "$tmpdir"
      curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
      refresh_path
      return
    fi
  fi

  mkdir -p "$install_dir"
  tar xzf "$archive" -C "$tmpdir"
  install -m 755 "$tmpdir/openshell" "$install_dir/openshell"
  rm -rf "$tmpdir"
  refresh_path
}

ensure_openshell_installed() {
  refresh_path
  if have openshell; then
    log "OpenShell already installed"
    return
  fi

  if ! have curl; then
    log "curl is required to install OpenShell"
    exit 1
  fi
  if ! have tar; then
    log "tar is required to install OpenShell"
    exit 1
  fi

  local arch pattern repo download_tag tmpdir archive install_dir
  repo="NVIDIA/OpenShell"
  download_tag="$OPENSHELL_RELEASE_TAG"
  case "$(uname -m)" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      log "Unsupported architecture for OpenShell: $(uname -m)"
      exit 1
      ;;
  esac

  pattern="openshell-${arch}-unknown-linux-musl.tar.gz"
  tmpdir="$(mktemp -d)"
  archive="$tmpdir/$pattern"
  install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"

  log "Installing OpenShell release $download_tag"
  if command -v gh >/dev/null 2>&1 && \
    gh release download "$download_tag" --repo "$repo" --pattern "$pattern" --dir "$tmpdir" >/dev/null 2>&1; then
    log "Downloaded OpenShell with gh"
  else
    log "Using direct OpenShell release download to avoid gh auth"
    if ! curl -fsSL "https://github.com/${repo}/releases/download/${download_tag}/${pattern}" -o "$archive"; then
      log "Direct release download failed; falling back to upstream install.sh"
      rm -rf "$tmpdir"
      curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
      refresh_path
      return
    fi
  fi

  mkdir -p "$install_dir"
  tar xzf "$archive" -C "$tmpdir"
  install -m 755 "$tmpdir/openshell" "$install_dir/openshell"
  rm -rf "$tmpdir"
  refresh_path
}

configure_openshell_provider() {
  if ! have openshell; then
    log "OpenShell not available yet; skipping provider setup for now"
    return
  fi

  local provider_api_key provider_base_url
  provider_base_url="$NEMOCLAW_REMOTE_BASE_URL"
  provider_api_key="${NEMOCLAW_REMOTE_API_KEY:-}"

  log "Configuring OpenShell provider $NEMOCLAW_PROVIDER for remote model API"
  if [ -z "$provider_api_key" ]; then
    log "NEMOCLAW_REMOTE_API_KEY is required for remote provider (or set NVIDIA_API_KEY)"
    exit 1
  fi

  if openshell provider get "$NEMOCLAW_PROVIDER" >/dev/null 2>&1; then
    if ! env OPENAI_API_KEY="$provider_api_key" openshell provider update \
      --credential OPENAI_API_KEY \
      --config "OPENAI_BASE_URL=$provider_base_url" \
      "$NEMOCLAW_PROVIDER"; then
      log "Provider update failed; continuing with existing provider config"
    fi
  else
    if ! env OPENAI_API_KEY="$provider_api_key" openshell provider create \
      --name "$NEMOCLAW_PROVIDER" \
      --type openai \
      --credential OPENAI_API_KEY \
      --config "OPENAI_BASE_URL=$provider_base_url"; then
      log "Provider create failed; continuing"
    fi
  fi

  openshell inference set --provider "$NEMOCLAW_PROVIDER" --model "$NEMOCLAW_MODEL"
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

     # rm -f /sandbox/.openclaw-data/agents/main/sessions/main.jsonl /sandbox/.openclaw-data/agents/main/sessions/main.jsonl.lock
  log "VSS OpenClaw plugin installed"

  rm -rf "${tmpdir}"
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
  log "Using remote NVIDIA-hosted model provider"

  log "Start installing/onboarding NemoClaw"
  # ensure_openshell_installed
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
export NEMOCLAW_REMOTE_BASE_URL NEMOCLAW_REMOTE_API_KEY
export OPENCLAW_CONFIG_UPDATE_SCRIPT NEMOCLAW_POLICY_FILE

main