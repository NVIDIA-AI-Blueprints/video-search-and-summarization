#!/usr/bin/env bash
set -euo pipefail

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5}"
OLLAMA_HOST="${OLLAMA_HOST:-0.0.0.0:11434}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.openshell.internal:11434/v1}"
NEMOCLAW_PROVIDER="${NEMOCLAW_PROVIDER:-ollama}"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
MODE="${1:-setup}"

log() {
  printf '[setup_ollama] %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

refresh_path() {
  local npm_bin shim_dir
  shim_dir="${HOME}/.local/bin"
  npm_bin="$(npm config get prefix 2>/dev/null)/bin" || true

  if [ -n "${npm_bin:-}" ] && [ -d "$npm_bin" ] && [[ ":$PATH:" != *":$npm_bin:"* ]]; then
    export PATH="$npm_bin:$PATH"
  fi
  if [ -d "$shim_dir" ] && [[ ":$PATH:" != *":$shim_dir:"* ]]; then
    export PATH="$shim_dir:$PATH"
  fi
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

configure_openshell_provider_for_ollama() {
  refresh_path

  if ! have openshell; then
    log "OpenShell not available yet; skipping Ollama provider configuration"
    return
  fi

  log "Configuring OpenShell provider $NEMOCLAW_PROVIDER for Ollama"
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

main() {
  case "$MODE" in
    setup)
      install_ollama_if_needed
      start_ollama_server
      ensure_ollama_model
      ;;
    configure)
      configure_openshell_provider_for_ollama
      ;;
    *)
      log "Unknown mode: $MODE (expected: setup or configure)"
      exit 1
      ;;
  esac
}

main "$@"
