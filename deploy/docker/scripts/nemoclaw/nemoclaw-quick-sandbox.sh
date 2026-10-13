#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Fast sandbox creation (<1m when a pre-built image exists).
# Extracted from nemoclaw-quickstart — skips install, uses openshell sandbox
# create directly instead of `nemoclaw onboard`.
#
# Intended after:  bash nemoclaw-install.sh 1 2 3
#
# Usage:
#   bash nemoclaw-install.sh 4              # build image only (default quick path)
#   bash nemoclaw-install.sh 5              # create sandbox (starts gateway automatically)
#   ./nemoclaw-quick-sandbox.sh --build-only [sandbox-name]
#   ./nemoclaw-quick-sandbox.sh --create-only [sandbox-name]
#
# Environment (aligned with nemoclaw-install.sh):
#   NEMOCLAW_SRC              Source tree (default: ~/.nemoclaw/source)
#   NEMOCLAW_SANDBOX_NAME     Sandbox name (default: demo)
#   NEMOCLAW_PROVIDER         build | custom | openai | anthropic | gemini
#   NEMOCLAW_MODEL            Model id
#   NVIDIA_API_KEY            Required for build provider
#   NEMOCLAW_ENDPOINT_URL     Required for custom provider
#   COMPATIBLE_API_KEY        Required for custom provider
#   CHAT_UI_URL               Optional CORS origin (auto-detected on Brev)
#   BREV_DETECT_TUNNEL=0      Disable Brev cloudflared FQDN detection
#   NEMOCLAW_SANDBOX_BASE_TAG Optional pin for ghcr.io/nvidia/nemoclaw/sandbox-base
#   NEMOCLAW_FORCE_SANDBOX_BUILD=1  Rebuild nemoclaw-sandbox:local even if present
#   NEMOCLAW_SANDBOX_POLICY_FILE  Policy YAML for openshell sandbox create (default: openclaw-sandbox.yaml)

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'
info() { echo -e "${GREEN}>>>${NC} $1"; }
warn() { echo -e "${YELLOW}>>>${NC} $1"; }
fail() { echo -e "${RED}>>>${NC} $1"; exit 1; }

SCRIPT_START="$(date +%s)"

DOCKER_BRIDGE_POOL_CIDR="172.16.0.0/12"
OPENSHELL_GATEWAY_PORT="8080"
OLLAMA_AUTH_PROXY_PORT="11435"
NEMOCLAW_SANDBOX_IMAGE="nemoclaw-sandbox:local"

usage() {
  cat <<'EOF'
Fast NemoClaw sandbox creation (nemoclaw-quickstart sandbox path).

Run after:  bash nemoclaw-install.sh 1 2 3 4

Usage:
  nemoclaw-quick-sandbox.sh [--build-only | --create-only] [--policy-file PATH] [sandbox-name]

Modes:
  --build-only   Build nemoclaw-sandbox:local only (nemoclaw-install.sh step 4)
  --create-only  Create sandbox; starts OpenShell gateway if needed (step 5)
  --policy-file  Policy YAML for openshell sandbox create (or NEMOCLAW_SANDBOX_POLICY_FILE)
  (default)      Build/load image if needed, then create sandbox

Providers (NEMOCLAW_PROVIDER):
  build      NVIDIA API (default) — needs NVIDIA_API_KEY
  custom     OpenAI-compatible — needs NEMOCLAW_ENDPOINT_URL + COMPATIBLE_API_KEY
  openai | anthropic | gemini — cloud APIs

Speed: uses nemoclaw-sandbox:local when present; loads from
/var/cache/nemoclaw/sandbox-image.tar or builds locally if missing.

Build failures on patch-openclaw-chat-send.js usually mean the NemoClaw
source ref and sandbox-base tag are mismatched. Set NEMOCLAW_INSTALL_REF=latest
or export NEMOCLAW_SANDBOX_BASE_TAG=v0.0.55 to match your checkout.
EOF
}

# Pin sandbox-base to the same release as the NemoClaw source. Building v0.0.55
# patches against sandbox-base:latest often fails because GHCR :latest moved on.
resolve_sandbox_base_image() {
  local tag="${NEMOCLAW_SANDBOX_BASE_TAG:-}"
  local ver=""

  if [[ -z "$tag" && -f "${NEMOCLAW_DIR}/.version" ]]; then
    ver="$(tr -d '[:space:]' <"${NEMOCLAW_DIR}/.version")"
    [[ -n "$ver" ]] && tag="v${ver#v}"
  fi
  if [[ -z "$tag" ]]; then
    ver="$(sed -nE 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' \
      "${NEMOCLAW_DIR}/package.json" | head -1)"
    [[ -n "$ver" && "$ver" != "0.0.0" ]] && tag="v${ver#v}"
  fi
  if [[ -z "$tag" ]]; then
    tag="${NEMOCLAW_INSTALL_REF:-latest}"
  fi
  tag="${tag#refs/tags/}"
  local image=""
  case "$tag" in
    latest) image="ghcr.io/nvidia/nemoclaw/sandbox-base:latest" ;;
    v*) image="ghcr.io/nvidia/nemoclaw/sandbox-base:${tag}" ;;
    *) image="ghcr.io/nvidia/nemoclaw/sandbox-base:v${tag}" ;;
  esac
  if [[ "$tag" != "latest" ]] && ! docker manifest inspect "$image" >/dev/null 2>&1; then
    warn "sandbox-base tag ${tag} not found on GHCR — falling back to :latest"
    image="ghcr.io/nvidia/nemoclaw/sandbox-base:latest"
  fi
  printf '%s' "$image"
}

run_build_phase() {
  local image="$NEMOCLAW_SANDBOX_IMAGE" start end
  start="$(date +%s)"
  if [[ "${NEMOCLAW_FORCE_SANDBOX_BUILD:-}" == "1" ]]; then
    docker rmi "$image" 2>/dev/null || true
  fi
  if docker image inspect "$image" >/dev/null 2>&1 \
    && [[ "${NEMOCLAW_FORCE_SANDBOX_BUILD:-}" != "1" ]]; then
    info "Image ${image} already exists — skipping build (set NEMOCLAW_FORCE_SANDBOX_BUILD=1 to rebuild)"
    return 0
  fi
  ensure_local_sandbox_image "$image" || fail "Could not build ${image}"
  end="$(date +%s)"
  info "Build phase finished in $((end - start))s"
}

strip_ansi() {
  sed -E 's/\x1B\[[0-9;]*[[:alpha:]]//g'
}

gateway_port_open() {
  ss -tln 2>/dev/null | grep -q ':8080 ' || \
    curl -fsS -o /dev/null -m 2 "${GATEWAY_ENDPOINT:-http://127.0.0.1:8080}/" 2>/dev/null
}

gateway_has_legacy_start() {
  openshell gateway --help 2>&1 | grep -Eq '^[[:space:]]+start[[:space:]]+Deploy/start the gateway'
}

register_gateway() {
  local status
  if openshell gateway select nemoclaw >/dev/null 2>&1; then
    status="$(openshell status 2>&1 | strip_ansi || true)"
    printf '%s' "$status" | grep -qiE 'Status:.*Connected' && return 0
  fi
  openshell gateway remove nemoclaw >/dev/null 2>&1 || true
  openshell gateway add "${GATEWAY_ENDPOINT}" --local --name nemoclaw >/dev/null 2>&1 || {
    openshell gateway remove nemoclaw >/dev/null 2>&1 || true
    openshell gateway add "${GATEWAY_ENDPOINT}" --local --name nemoclaw >/dev/null 2>&1 || return 1
  }
  openshell gateway select nemoclaw >/dev/null 2>&1
}

gateway_connected() {
  local status
  status="$(openshell status 2>&1 | strip_ansi || true)"
  printf '%s' "$status" | grep -qiE 'Status:.*Connected'
}

start_nemoclaw_gateway() {
  local onboard_js="${NEMOCLAW_DIR}/dist/lib/onboard.js"
  local gw_log
  [[ -f "$onboard_js" ]] || {
    warn "Missing ${onboard_js} — run: bash nemoclaw-install.sh 3"
    return 1
  }
  if gateway_port_open && gateway_connected; then
    return 0
  fi
  info "Starting OpenShell gateway (not full onboard — gateway only)..."
  gw_log="$(mktemp /tmp/nemoclaw-gw-start-XXXXXX.log)"
  if node -e '
    const onboard = require(process.argv[1]);
    onboard.startGatewayForRecovery(null)
      .then(() => process.exit(0))
      .catch((e) => { console.error(e.message || e); process.exit(1); });
  ' "$onboard_js" >"$gw_log" 2>&1; then
    rm -f "$gw_log"
    return 0
  fi
  grep -Ev '^\s*\[2/8\]|^─+$' "$gw_log" | tail -10 >&2 || true
  rm -f "$gw_log"
  warn "Gateway start failed — log: ${HOME}/.local/state/nemoclaw/openshell-docker-gateway/openshell-gateway.log"
  return 1
}

start_gateway() {
  start_nemoclaw_gateway && return 0
  if command -v openshell-gateway >/dev/null 2>&1 && command -v openshell-sandbox >/dev/null 2>&1; then
    sudo -n systemctl start openshell-gateway.service >/dev/null 2>&1 \
      || systemctl start openshell-gateway.service >/dev/null 2>&1 \
      || true
    if [[ -x /usr/local/bin/nemoclaw-openshell-gateway-service ]]; then
      nohup /usr/local/bin/nemoclaw-openshell-gateway-service >/tmp/nemoclaw-openshell-gateway.log 2>&1 &
    fi
    return 0
  fi
  gateway_has_legacy_start && openshell gateway start --name nemoclaw >/dev/null 2>&1 || true
}

gateway_ready() {
  register_gateway >/dev/null 2>&1 || true
  gateway_port_open && gateway_connected
}

ensure_gateway_ready() {
  GATEWAY_ENDPOINT="${NEMOCLAW_OPENSHELL_GATEWAY_ENDPOINT:-http://127.0.0.1:8080}"
  register_gateway >/dev/null 2>&1 || true
  if gateway_port_open && gateway_connected; then
    info "OpenShell gateway already running"
    return 0
  fi
  info "Waiting for OpenShell gateway..."
  local i started=false
  for i in $(seq 1 30); do
    gateway_ready && break
    if [[ "$started" != true ]]; then
      start_gateway && started=true
    fi
    [[ "$i" -eq 30 ]] && fail "Gateway not ready after 60s — log: ${HOME}/.local/state/nemoclaw/openshell-docker-gateway/openshell-gateway.log"
    sleep 2
  done
  info "Gateway is ready"
}

build_local_sandbox_image() {
  local image="$1"
  local build_ctx base_image openclaw_version
  build_ctx="$(mktemp -d)"
  base_image="$(resolve_sandbox_base_image)"
  openclaw_version="$(awk '/^ARG OPENCLAW_VERSION=/{sub(/^ARG OPENCLAW_VERSION=/,""); print; exit}' \
    "${NEMOCLAW_DIR}/Dockerfile" 2>/dev/null || true)"

  case "$image" in
    "$NEMOCLAW_SANDBOX_IMAGE")
      info "Building ${image} from ${NEMOCLAW_DIR}/Dockerfile (first run — may take several minutes)..."
      info "Using sandbox base image: ${base_image}"
      cp "$NEMOCLAW_DIR/Dockerfile" "$build_ctx/"
      cp -r "$NEMOCLAW_DIR/nemoclaw" "$build_ctx/nemoclaw"
      cp -r "$NEMOCLAW_DIR/nemoclaw-blueprint" "$build_ctx/nemoclaw-blueprint"
      cp -r "$NEMOCLAW_DIR/scripts" "$build_ctx/scripts"
      rm -rf "$build_ctx/nemoclaw/node_modules"
      if ! docker pull "$base_image" >/dev/null 2>&1; then
        warn "Could not pre-pull ${base_image} — docker build will pull or fail"
      fi
      if ! docker build \
        --build-arg "BASE_IMAGE=${base_image}" \
        --build-arg NEMOCLAW_DISABLE_DEVICE_AUTH=1 \
        ${openclaw_version:+--build-arg "OPENCLAW_VERSION=${openclaw_version}"} \
        -t "$image" \
        "$build_ctx"; then
        rm -rf "$build_ctx"
        warn "docker build failed for ${image}"
        return 1
      fi
      ;;
    *)
      warn "Cannot build unknown image tag: ${image}"
      rm -rf "$build_ctx"
      return 1
      ;;
  esac

  rm -rf "$build_ctx"
  info "Built ${image}"
}

resolve_nemoclaw_dir() {
  local candidate
  for candidate in \
    "${NEMOCLAW_SRC:-}" \
    "${HOME}/.nemoclaw/source" \
    "${HOME}/NemoClaw"; do
    [[ -n "$candidate" && -f "$candidate/Dockerfile" ]] || continue
    printf '%s' "$candidate"
    return 0
  done
  return 1
}

refresh_path() {
  export PATH="${NEMOCLAW_SHIM_DIR:-$HOME/.local/bin}:${PATH:-}"
  if command -v npm >/dev/null 2>&1; then
    local npm_bin
    npm_bin="$(npm config get prefix 2>/dev/null)/bin"
    [[ -d "$npm_bin" ]] && export PATH="$npm_bin:$PATH"
  fi
  if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
    # shellcheck disable=SC1090
    . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  fi
  hash -r 2>/dev/null || true
}

detect_brev_tunnel() {
  [[ "${BREV_DETECT_TUNNEL:-1}" == "0" ]] && return 0
  [[ -n "${CHAT_UI_URL:-}" ]] && return 0
  command -v journalctl >/dev/null 2>&1 || return 0

  local svc tunnel_port hostname
  for svc in cloudflared cloudflared-ingress-tunnel; do
    for tunnel_port in 80 18789; do
      hostname="$(journalctl -u "$svc" --no-pager -o cat 2>/dev/null \
        | sed 's/\\"/"/g' \
        | grep -o '"hostname":"[^"]*","service":"http://localhost:'"$tunnel_port"'"' \
        | grep -o '"hostname":"[^"]*"' \
        | head -1 \
        | sed 's/"hostname":"//;s/"//')" || true
      if [[ -n "$hostname" ]]; then
        export CHAT_UI_URL="https://${hostname}"
        info "Detected Brev tunnel FQDN: $CHAT_UI_URL"
        return 0
      fi
    done
  done
}

allow_docker_bridge_host_port() {
  local port="$1" label="$2"
  command -v ufw >/dev/null 2>&1 || return 0
  if sudo -n ufw allow from "$DOCKER_BRIDGE_POOL_CIDR" to any port "$port" proto tcp >/dev/null 2>&1; then
    info "Allowed Docker bridge traffic to $label on port $port"
  else
    warn "Could not add UFW allow rule for $label on port $port"
  fi
}

configure_openshell_bridge_firewall() {
  allow_docker_bridge_host_port "$OPENSHELL_GATEWAY_PORT" "OpenShell gateway"
  allow_docker_bridge_host_port "$OLLAMA_AUTH_PROXY_PORT" "Ollama auth proxy"
}

ensure_local_sandbox_image() {
  local image="$1"
  docker image inspect "$image" >/dev/null 2>&1 && return 0

  local tar=""
  case "$image" in
    "$NEMOCLAW_SANDBOX_IMAGE") tar="/var/cache/nemoclaw/sandbox-image.tar" ;;
  esac

  if [[ -n "$tar" && -f "$tar" ]]; then
    info "Loading cached image from $tar ..."
    docker load -i "$tar" >/dev/null
    docker image inspect "$image" >/dev/null 2>&1 && return 0
  fi

  build_local_sandbox_image "$image"
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { usage; exit 0; }

QUICK_SANDBOX_MODE="${NEMOCLAW_QUICK_SANDBOX_MODE:-all}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only) QUICK_SANDBOX_MODE=build; shift ;;
    --create-only) QUICK_SANDBOX_MODE=create; shift ;;
    --policy-file)
      [[ -n "${2:-}" ]] || fail "--policy-file requires a path"
      NEMOCLAW_SANDBOX_POLICY_FILE="$2"
      shift 2
      ;;
    *) break ;;
  esac
done

refresh_path
command -v docker >/dev/null 2>&1 || fail "docker not available — run: bash nemoclaw-install.sh 1"
if [[ "$QUICK_SANDBOX_MODE" != "build" ]]; then
  command -v openshell >/dev/null 2>&1 || fail "openshell not on PATH — run: bash nemoclaw-install.sh 3"
fi

NEMOCLAW_DIR="$(resolve_nemoclaw_dir)" || fail "NemoClaw source not found — run: bash nemoclaw-install.sh 1 2 3"
export NEMOCLAW_SRC="${NEMOCLAW_SRC:-$NEMOCLAW_DIR}"

SANDBOX_NAME="${1:-${NEMOCLAW_SANDBOX_NAME:-demo}}"

if [[ "$QUICK_SANDBOX_MODE" == "build" ]]; then
  run_build_phase
  exit 0
fi

if [[ -z "${NEMOCLAW_PROVIDER:-}" ]]; then
  if [[ -n "${NEMOCLAW_ENDPOINT_URL:-}" ]]; then
    NEMOCLAW_PROVIDER=custom
  else
    NEMOCLAW_PROVIDER=build
  fi
fi
case "$NEMOCLAW_PROVIDER" in
  cloud | nvidia | nvidia-nim | nvidia-prod) NEMOCLAW_PROVIDER=build ;;
esac

DEFAULT_NEMOCLAW_MODEL=""
GATEWAY_PROVIDER_NAME=""
PROVIDER_TYPE=""
CREDENTIAL_ENV=""
ENDPOINT_CONFIG_KEY="OPENAI_BASE_URL"
ENDPOINT_URL=""
SKIP_VERIFY=false
NEMOCLAW_PROVIDER_KEY="inference"
NEMOCLAW_OPENCLAW_BASE_URL="https://inference.local/v1"
NEMOCLAW_OPENCLAW_API="openai-completions"
NEMOCLAW_OPENCLAW_COMPAT=""

case "$NEMOCLAW_PROVIDER" in
  build)
    DEFAULT_NEMOCLAW_MODEL="nvidia/nemotron-3-super-120b-a12b"
    GATEWAY_PROVIDER_NAME="nvidia-nim"
    PROVIDER_TYPE="openai"
    CREDENTIAL_ENV="NVIDIA_API_KEY"
    ENDPOINT_URL="https://integrate.api.nvidia.com/v1"
    SKIP_VERIFY=true
    ;;
  custom)
    GATEWAY_PROVIDER_NAME="compatible-endpoint"
    PROVIDER_TYPE="openai"
    CREDENTIAL_ENV="COMPATIBLE_API_KEY"
    ENDPOINT_URL="${NEMOCLAW_ENDPOINT_URL:-}"
    SKIP_VERIFY=true
    ;;
  openai)
    DEFAULT_NEMOCLAW_MODEL="gpt-5.4"
    GATEWAY_PROVIDER_NAME="openai-api"
    PROVIDER_TYPE="openai"
    CREDENTIAL_ENV="OPENAI_API_KEY"
    ENDPOINT_URL="https://api.openai.com/v1"
    SKIP_VERIFY=true
    NEMOCLAW_PROVIDER_KEY="openai"
    ;;
  anthropic)
    DEFAULT_NEMOCLAW_MODEL="claude-sonnet-4-6"
    GATEWAY_PROVIDER_NAME="anthropic-prod"
    PROVIDER_TYPE="anthropic"
    CREDENTIAL_ENV="ANTHROPIC_API_KEY"
    ENDPOINT_CONFIG_KEY="ANTHROPIC_BASE_URL"
    ENDPOINT_URL="https://api.anthropic.com"
    NEMOCLAW_PROVIDER_KEY="anthropic"
    NEMOCLAW_OPENCLAW_BASE_URL="https://inference.local"
    NEMOCLAW_OPENCLAW_API="anthropic-messages"
    ;;
  gemini)
    DEFAULT_NEMOCLAW_MODEL="gemini-2.5-flash"
    GATEWAY_PROVIDER_NAME="gemini-api"
    PROVIDER_TYPE="openai"
    CREDENTIAL_ENV="GEMINI_API_KEY"
    ENDPOINT_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
    SKIP_VERIFY=true
    NEMOCLAW_OPENCLAW_COMPAT="supportsStore=false"
    ;;
  *)
    fail "Unsupported NEMOCLAW_PROVIDER '$NEMOCLAW_PROVIDER' (use build, custom, openai, anthropic, gemini)"
    ;;
esac

if [[ "$NEMOCLAW_PROVIDER" == "custom" && -z "$ENDPOINT_URL" ]]; then
  fail "NEMOCLAW_ENDPOINT_URL is required for custom provider"
fi

CREDENTIAL_VALUE="${!CREDENTIAL_ENV:-}"
if [[ -z "$CREDENTIAL_VALUE" ]]; then
  fail "$CREDENTIAL_ENV not set for provider '$NEMOCLAW_PROVIDER'"
fi

NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-$DEFAULT_NEMOCLAW_MODEL}"
[[ -n "$NEMOCLAW_MODEL" ]] || fail "NEMOCLAW_MODEL is required for provider '$NEMOCLAW_PROVIDER'"

NEMOCLAW_PRIMARY_MODEL_REF="${NEMOCLAW_PROVIDER_KEY}/${NEMOCLAW_MODEL}"

info "Source:   $NEMOCLAW_DIR"
info "Sandbox:  $SANDBOX_NAME"
info "Provider: $NEMOCLAW_PROVIDER ($GATEWAY_PROVIDER_NAME / $NEMOCLAW_MODEL)"

detect_brev_tunnel
configure_openshell_bridge_firewall

ensure_gateway_ready

SANDBOX_IMAGE="$NEMOCLAW_SANDBOX_IMAGE"
if [[ "$QUICK_SANDBOX_MODE" == "create" ]]; then
  docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 \
    || fail "Missing ${SANDBOX_IMAGE} — run: bash nemoclaw-install.sh 4"
elif ! docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
  ensure_local_sandbox_image "$SANDBOX_IMAGE" || fail "Could not load or build ${SANDBOX_IMAGE}"
fi

IMAGE_IN_CTR=false
if command -v openshell-gateway >/dev/null 2>&1 && docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
  info "Using local sandbox image ($SANDBOX_IMAGE)"
  IMAGE_IN_CTR=true
elif docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
  info "Local sandbox image exists ($SANDBOX_IMAGE) — using direct image reference"
  IMAGE_IN_CTR=true
else
  warn "No local image — sandbox create will build from Dockerfile via OpenShell (slow)"
fi

info "Configuring ${GATEWAY_PROVIDER_NAME} provider..."
PROVIDER_LOG="$(mktemp /tmp/nemoclaw-provider-XXXXXX.log)"
set +e
openshell provider create --name "$GATEWAY_PROVIDER_NAME" --type "$PROVIDER_TYPE" \
  --credential "${CREDENTIAL_ENV}=${CREDENTIAL_VALUE}" \
  --config "${ENDPOINT_CONFIG_KEY}=${ENDPOINT_URL}" >"$PROVIDER_LOG" 2>&1
PROVIDER_RC=$?
set -e
if [[ "$PROVIDER_RC" == "0" ]]; then
  info "Created ${GATEWAY_PROVIDER_NAME} provider"
elif grep -q "AlreadyExists" "$PROVIDER_LOG"; then
  openshell provider update "$GATEWAY_PROVIDER_NAME" \
    --credential "${CREDENTIAL_ENV}=${CREDENTIAL_VALUE}" \
    --config "${ENDPOINT_CONFIG_KEY}=${ENDPOINT_URL}" >/dev/null
  info "Updated ${GATEWAY_PROVIDER_NAME} provider"
else
  grep -Ev "NVIDIA_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|COMPATIBLE_API_KEY" "$PROVIDER_LOG" >&2 || true
  rm -f "$PROVIDER_LOG"
  fail "Could not configure ${GATEWAY_PROVIDER_NAME} provider"
fi
rm -f "$PROVIDER_LOG"

info "Setting inference route..."
INFERENCE_ARGS=(inference set --provider "$GATEWAY_PROVIDER_NAME" --model "$NEMOCLAW_MODEL")
[[ "$SKIP_VERIFY" == true ]] && INFERENCE_ARGS+=(--no-verify)
openshell "${INFERENCE_ARGS[@]}" >/dev/null 2>&1

info "Creating sandbox '$SANDBOX_NAME'..."
openshell sandbox delete "$SANDBOX_NAME" >/dev/null 2>&1 || true

SANDBOX_SOURCE=""
SANDBOX_POLICY=""
BUILD_CTX=""
if [[ "$IMAGE_IN_CTR" == true ]]; then
  SANDBOX_SOURCE="$SANDBOX_IMAGE"
else
  BUILD_CTX="$(mktemp -d)"
  cp "$NEMOCLAW_DIR/Dockerfile" "$BUILD_CTX/"
  cp -r "$NEMOCLAW_DIR/nemoclaw" "$BUILD_CTX/nemoclaw"
  cp -r "$NEMOCLAW_DIR/nemoclaw-blueprint" "$BUILD_CTX/nemoclaw-blueprint"
  cp -r "$NEMOCLAW_DIR/scripts" "$BUILD_CTX/scripts"
  rm -rf "$BUILD_CTX/nemoclaw/node_modules"
  [[ -n "${CHAT_UI_URL:-}" ]] && sed -i "s|ARG CHAT_UI_URL=http://127.0.0.1:18789|ARG CHAT_UI_URL=$CHAT_UI_URL|" "$BUILD_CTX/Dockerfile"
  sed -i "s|ARG NEMOCLAW_MODEL=.*|ARG NEMOCLAW_MODEL=$NEMOCLAW_MODEL|" "$BUILD_CTX/Dockerfile"
  sed -i "s|ARG NEMOCLAW_PROVIDER_KEY=.*|ARG NEMOCLAW_PROVIDER_KEY=$NEMOCLAW_PROVIDER_KEY|" "$BUILD_CTX/Dockerfile"
  sed -i "s|ARG NEMOCLAW_PRIMARY_MODEL_REF=.*|ARG NEMOCLAW_PRIMARY_MODEL_REF=$NEMOCLAW_PRIMARY_MODEL_REF|" "$BUILD_CTX/Dockerfile"
  sed -i "s|ARG NEMOCLAW_DISABLE_DEVICE_AUTH=.*|ARG NEMOCLAW_DISABLE_DEVICE_AUTH=1|" "$BUILD_CTX/Dockerfile"
  SANDBOX_SOURCE="$BUILD_CTX/Dockerfile"
fi

if [[ -n "${NEMOCLAW_SANDBOX_POLICY_FILE:-}" ]]; then
  [[ -f "${NEMOCLAW_SANDBOX_POLICY_FILE}" ]] \
    || fail "NEMOCLAW_SANDBOX_POLICY_FILE not found: ${NEMOCLAW_SANDBOX_POLICY_FILE}"
  SANDBOX_POLICY="${NEMOCLAW_SANDBOX_POLICY_FILE}"
elif [[ -f "$NEMOCLAW_DIR/nemoclaw-blueprint/policies/openclaw-sandbox.yaml" ]]; then
  SANDBOX_POLICY="$NEMOCLAW_DIR/nemoclaw-blueprint/policies/openclaw-sandbox.yaml"
fi
[[ -n "$SANDBOX_POLICY" ]] && info "Sandbox policy: ${SANDBOX_POLICY}"

CREATE_RC=1
for attempt in 1 2 3; do
  info "Sandbox create attempt $attempt/3..."
  CREATE_LOG="$(mktemp /tmp/nemoclaw-create-XXXXXX.log)"
  CREATE_ARGS=(sandbox create --from "$SANDBOX_SOURCE" --name "$SANDBOX_NAME")
  [[ -n "$SANDBOX_POLICY" ]] && CREATE_ARGS+=(--policy "$SANDBOX_POLICY")
  CREATE_ARGS+=(--provider "$GATEWAY_PROVIDER_NAME")
  set +e
  openshell "${CREATE_ARGS[@]}" -- env "${CREDENTIAL_ENV}=${CREDENTIAL_VALUE}" >"$CREATE_LOG" 2>&1
  CREATE_RC=$?
  set -e
  grep -E "^  (Step |Building |Built |Pushing |\[progress\]|Successfully |Created sandbox|Image )|✓" "$CREATE_LOG" || true
  if [[ "$CREATE_RC" == "0" ]]; then
    rm -f "$CREATE_LOG"
    break
  fi
  warn "Attempt $attempt failed"
  [[ "$attempt" -lt 3 ]] && { openshell sandbox delete "$SANDBOX_NAME" >/dev/null 2>&1 || true; sleep 5; }
  [[ "$attempt" -eq 3 ]] && { tail -20 "$CREATE_LOG" | grep -Ev "API_KEY" >&2 || true; rm -f "$CREATE_LOG"; fail "Sandbox creation failed"; }
  rm -f "$CREATE_LOG"
done
[[ -n "$BUILD_CTX" ]] && rm -rf "$BUILD_CTX"

SANDBOX_LINE="$(openshell sandbox list 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | awk -v name="$SANDBOX_NAME" '$1 == name { print; exit }')"
echo "$SANDBOX_LINE" | grep -q "Ready" || fail "Sandbox not Ready: ${SANDBOX_LINE:-unknown}"

info "Registering sandbox in ~/.nemoclaw/sandboxes.json ..."
mkdir -p "${HOME}/.nemoclaw"
cat >"${HOME}/.nemoclaw/sandboxes.json" <<JSON
{
  "sandboxes": {
    "${SANDBOX_NAME}": {
      "name": "${SANDBOX_NAME}",
      "model": "${NEMOCLAW_MODEL}",
      "provider": "${GATEWAY_PROVIDER_NAME}",
      "agent": "openclaw"
    }
  },
  "defaultSandbox": "${SANDBOX_NAME}"
}
JSON
chmod 600 "${HOME}/.nemoclaw/sandboxes.json"

DASHBOARD_TOKEN=""
TMPCONF="$(mktemp -d /tmp/nemoclaw-conf-XXXXXX)"
if openshell sandbox download "$SANDBOX_NAME" /sandbox/.openclaw/openclaw.json "$TMPCONF" 2>/dev/null; then
    TMPCONF="$TMPCONF" \
    NEMOCLAW_MODEL="$NEMOCLAW_MODEL" \
    NEMOCLAW_PROVIDER_KEY="$NEMOCLAW_PROVIDER_KEY" \
    NEMOCLAW_PRIMARY_MODEL_REF="$NEMOCLAW_PRIMARY_MODEL_REF" \
    NEMOCLAW_OPENCLAW_BASE_URL="$NEMOCLAW_OPENCLAW_BASE_URL" \
    NEMOCLAW_OPENCLAW_API="$NEMOCLAW_OPENCLAW_API" \
    NEMOCLAW_OPENCLAW_COMPAT="$NEMOCLAW_OPENCLAW_COMPAT" \
    CHAT_UI_URL="${CHAT_UI_URL:-}" \
    python3 <<'PY'
import json, os, secrets
path = os.path.join(os.environ["TMPCONF"], "openclaw.json")
cfg = json.load(open(path))
model = os.environ.get("NEMOCLAW_MODEL", "").strip()
provider_key = os.environ.get("NEMOCLAW_PROVIDER_KEY", "inference").strip() or "inference"
primary_model_ref = os.environ.get("NEMOCLAW_PRIMARY_MODEL_REF", "").strip()
base_url = os.environ.get("NEMOCLAW_OPENCLAW_BASE_URL", "https://inference.local/v1").strip()
api = os.environ.get("NEMOCLAW_OPENCLAW_API", "openai-completions").strip()
chat_ui_url = os.environ.get("CHAT_UI_URL", "").strip()

if model:
    providers = cfg.get("models", {}).get("providers", {})
    provider = providers.get(provider_key) if isinstance(providers, dict) else None
    if not isinstance(provider, dict):
        provider = {}
    provider["baseUrl"] = base_url
    provider["apiKey"] = provider.get("apiKey") or "unused"
    provider["api"] = api
    existing_model = next((e for e in provider.get("models", []) if isinstance(e, dict)), {})
    existing_model["id"] = model
    existing_model["name"] = primary_model_ref or f"inference/{model}"
    provider["models"] = [existing_model]
    cfg.setdefault("models", {})["mode"] = "merge"
    cfg["models"]["providers"] = {provider_key: provider}
    cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})["primary"] = primary_model_ref or f"{provider_key}/{model}"

cfg.setdefault("gateway", {}).setdefault("auth", {})["token"] = secrets.token_hex(32)
origins = cfg.setdefault("gateway", {}).setdefault("controlUi", {}).get("allowedOrigins", [])
for o in ["http://127.0.0.1", "http://127.0.0.1:80", "http://localhost", chat_ui_url]:
    if o and o not in origins:
        origins.append(o)
cfg["gateway"]["controlUi"]["allowedOrigins"] = [x for x in origins if x]
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
os.chmod(path, 0o600)
PY
    DASHBOARD_TOKEN="$(python3 -c "import json; print(json.load(open('$TMPCONF/openclaw.json')).get('gateway',{}).get('auth',{}).get('token',''))" 2>/dev/null || true)"
    openshell sandbox upload "$SANDBOX_NAME" "$TMPCONF/openclaw.json" /sandbox/.openclaw/openclaw.json 2>/dev/null \
      || warn "Could not upload patched openclaw.json"
fi
rm -rf "$TMPCONF"

info "Starting agent inside sandbox..."
openshell sandbox ssh-config "$SANDBOX_NAME" >/tmp/nemoclaw-ssh-config
SSH_OPTS="-F /tmp/nemoclaw-ssh-config -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
ssh $SSH_OPTS "openshell-${SANDBOX_NAME}" "nohup nemoclaw-start > /tmp/nemoclaw-start.log 2>&1 &"
rm -f /tmp/nemoclaw-ssh-config

DASHBOARD_PORT=18789
openshell forward stop "$DASHBOARD_PORT" 2>/dev/null || true
openshell forward start --background "$DASHBOARD_PORT" "$SANDBOX_NAME"
sleep 2

DASHBOARD_BASE="${CHAT_UI_URL:-http://127.0.0.1:$DASHBOARD_PORT}"
if [[ -n "$DASHBOARD_TOKEN" ]]; then
  DASHBOARD_URL="${DASHBOARD_BASE}/#token=${DASHBOARD_TOKEN}"
else
  DASHBOARD_URL="${DASHBOARD_BASE}/"
fi

ELAPSED=$(( $(date +%s) - SCRIPT_START ))
echo ""
info "Ready in ${ELAPSED}s"
info "DASHBOARD_URL=${DASHBOARD_URL}"
echo "  nemoclaw status"
echo "  nemoclaw chat"
echo ""
