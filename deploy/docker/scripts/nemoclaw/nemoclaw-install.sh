#!/usr/bin/env bash
# Stepwise NemoClaw + OpenShell install with per-step timing.
# Mirrors ~/.nemoclaw/source/scripts/install.sh (curl|bash / non-interactive path),
# including bootstrap ref verification, CLI shims, and verify_nemoclaw.
#
# Usage:
#   bash install_nemoclaw_stepwise.sh          # run all steps
#   bash install_nemoclaw_stepwise.sh 1        # step 1 only
#   bash install_nemoclaw_stepwise.sh 2 3 4    # steps 2–4
#   STEP=3 bash install_nemoclaw_stepwise.sh   # step 3 only
#
# Environment (optional):
#   NEMOCLAW_INSTALL_REF=v0.0.55   (use latest; pin e.g. v0.0.55 only when intentional)
#   NEMOCLAW_SANDBOX_BASE_TAG=v0.0.55  optional sandbox-base pin for local image builds
#   NEMOCLAW_SRC=$HOME/.nemoclaw/source
#   NEMOCLAW_SANDBOX_NAME=demo
#   NEMOCLAW_PROVIDER=custom|build   (auto: custom if NEMOCLAW_ENDPOINT_URL set, else build)
#   NEMOCLAW_ENDPOINT_URL=...        (required when NEMOCLAW_PROVIDER=custom)
#   COMPATIBLE_API_KEY=...           (required when NEMOCLAW_PROVIDER=custom)
#   NEMOCLAW_MODEL=nvidia/nemotron-3-super-120b-a12b
#   NVIDIA_API_KEY=...                   (required when NEMOCLAW_PROVIDER=build)
#   SKIP_DOCKER=1          skip Docker install check
#   SKIP_CLONE=1           keep existing NEMOCLAW_SRC checkout
#   SKIP_ONBOARD=1         skip step 5 (onboard / sandbox create)
#   SKIP_TUNNEL=1          skip cloudflared tunnel start at end of step 5
#   NO_SANDBOX_CACHE=0     use nemoclaw-quick-sandbox.sh for steps 4–5 (default)
#   NO_SANDBOX_CACHE=1     use install.sh onboard path instead of quick sandbox

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
LOG_DIR="${NEMOCLAW_STEPWISE_LOG_DIR:-$HOME/.nemoclaw/stepwise-install-logs}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/${TIMESTAMP}.log"

export NEMOCLAW_INSTALL_REF="${NEMOCLAW_INSTALL_REF:-latest}"
export NEMOCLAW_SRC="${NEMOCLAW_SRC:-$HOME/.nemoclaw/source}"
export NEMOCLAW_NON_INTERACTIVE="${NEMOCLAW_NON_INTERACTIVE:-1}"
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE="${NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE:-1}"
export NEMOCLAW_SHIM_DIR="${NEMOCLAW_SHIM_DIR:-$HOME/.local/bin}"
export NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-demo}"
export NO_SANDBOX_CACHE="${NO_SANDBOX_CACHE:-0}"
export NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-nvidia/nemotron-3-super-120b-a12b}"
export NVIDIA_API_KEY="${NVIDIA_API_KEY:-${NGC_CLI_API_KEY:-}}"
# Match init_nemoclaw / VSS notebook: custom when an endpoint URL is provided.
if [[ -z "${NEMOCLAW_PROVIDER:-}" ]]; then
  if [[ -n "${NEMOCLAW_ENDPOINT_URL:-}" ]]; then
    export NEMOCLAW_PROVIDER=custom
  else
    export NEMOCLAW_PROVIDER=build
  fi
fi
export PATH="${NEMOCLAW_SHIM_DIR}:${PATH:-}"

_CLI_BIN="nemoclaw"
_CLI_PATH=""
NVM_VERSION="v0.40.4"
NVM_SHA256="4b7412c49960c7d31e8df72da90c1fb5b8cccb419ac99537b737028d497aba4f"
MIN_NODE_VERSION="22.16.0"
MIN_NPM_MAJOR=10
PAYLOAD_MARKER="NEMOCLAW_VERSIONED_INSTALLER_PAYLOAD=1"

mkdir -p "$LOG_DIR"

log() { printf '[%s] %s\n' "$SCRIPT_NAME" "$*" | tee -a "$LOG_FILE"; }
warn() { printf '[%s][WARN] %s\n' "$SCRIPT_NAME" "$*" | tee -a "$LOG_FILE" >&2; }
die() { printf '[%s][ERROR] %s\n' "$SCRIPT_NAME" "$*" | tee -a "$LOG_FILE" >&2; exit 1; }

run_step() {
  local label="$1"
  shift
  log "=== START: ${label} ==="
  local start end elapsed
  start="$(date +%s)"
  if "$@"; then
    end="$(date +%s)"
    elapsed=$((end - start))
    log "=== DONE:  ${label} (${elapsed}s) ==="
  else
    end="$(date +%s)"
    elapsed=$((end - start))
    die "FAILED: ${label} (${elapsed}s)"
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

ensure_nvm_loaded() {
  if have node; then
    return 0
  fi
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    # shellcheck disable=SC1090
    . "$NVM_DIR/nvm.sh"
  fi
}

refresh_path() {
  ensure_nvm_loaded
  local npm_bin
  npm_bin="$(npm config get prefix 2>/dev/null)/bin" || true
  if [[ -n "${npm_bin:-}" && -d "$npm_bin" ]]; then
    export PATH="$npm_bin:$NEMOCLAW_SHIM_DIR:$PATH"
  else
    export PATH="$NEMOCLAW_SHIM_DIR:$PATH"
  fi
  hash -r 2>/dev/null || true
}

resolve_npm_bin() {
  have npm || ensure_nvm_loaded
  have npm || return 1
  local npm_prefix
  npm_prefix="$(npm config get prefix 2>/dev/null || true)"
  [[ -n "$npm_prefix" ]] || return 1
  printf '%s/bin\n' "$npm_prefix"
}

detect_shell_profile() {
  local profile="$HOME/.bashrc"
  case "$(basename "${SHELL:-}")" in
    zsh) profile="$HOME/.zshrc" ;;
    fish) profile="$HOME/.config/fish/config.fish" ;;
    tcsh) profile="$HOME/.tcshrc" ;;
    csh) profile="$HOME/.cshrc" ;;
    *)
      if [[ ! -f "$HOME/.bashrc" && -f "$HOME/.profile" ]]; then
        profile="$HOME/.profile"
      fi
      ;;
  esac
  printf '%s' "$profile"
}

ensure_local_bin_in_profile() {
  local profile
  profile="$(detect_shell_profile)"
  [[ -n "$profile" ]] || return 0
  if [[ -f "$profile" ]] && grep -qF '# NemoClaw PATH setup' "$profile" 2>/dev/null; then
    return 0
  fi
  case "$(basename "${SHELL:-bash}")" in
    fish)
      {
        printf '\n# NemoClaw PATH setup\n'
        printf 'fish_add_path --path --append "%s"\n' "$NEMOCLAW_SHIM_DIR"
        printf '# end NemoClaw PATH setup\n'
      } >>"$profile"
      ;;
    tcsh | csh)
      {
        printf '\n# NemoClaw PATH setup\n'
        printf 'setenv PATH "%s:${PATH}"\n' "$NEMOCLAW_SHIM_DIR"
        printf '# end NemoClaw PATH setup\n'
      } >>"$profile"
      ;;
    *)
      {
        printf '\n# NemoClaw PATH setup\n'
        printf 'export PATH="%s:$PATH"\n' "$NEMOCLAW_SHIM_DIR"
        printf '# end NemoClaw PATH setup\n'
      } >>"$profile"
      ;;
  esac
  log "Appended NemoClaw PATH to ${profile}"
}

ensure_cli_shim() {
  local cli_bin="${1:-$_CLI_BIN}"
  local npm_bin shim_path node_path node_dir cli_path expected_shim
  npm_bin="$(resolve_npm_bin)" || return 1
  shim_path="${NEMOCLAW_SHIM_DIR}/${cli_bin}"

  node_path="$(command -v node 2>/dev/null || true)"
  [[ -n "$node_path" && -x "$node_path" ]] || return 1

  cli_path="$npm_bin/$cli_bin"
  [[ -x "$cli_path" ]] || return 1
  node_dir="$(dirname "$node_path")"

  if [[ "$cli_path" -ef "$shim_path" ]]; then
    refresh_path
    ensure_local_bin_in_profile
    return 0
  fi

  expected_shim="$(
    cat <<EOF
#!/usr/bin/env bash
export PATH="$node_dir:\$PATH"
exec "$cli_path" "\$@"
EOF
  )"

  if [[ -x "$shim_path" ]] && cmp -s "$shim_path" <(printf '%s\n' "$expected_shim"); then
    refresh_path
    ensure_local_bin_in_profile
    return 0
  fi

  mkdir -p "$NEMOCLAW_SHIM_DIR"
  printf '%s\n' "$expected_shim" >"$shim_path"
  chmod +x "$shim_path"
  refresh_path
  ensure_local_bin_in_profile
  log "Created user-local shim at ${shim_path}"
}

ensure_nemoclaw_shim() {
  ensure_cli_shim "$_CLI_BIN" || return 1
}

is_real_nemoclaw_cli() {
  local bin_path="${1:-nemoclaw}"
  local expected_name="${2:-$_CLI_BIN}"
  local version_output
  version_output="$("$bin_path" --version 2>/dev/null)" || return 1
  [[ "$version_output" =~ ^${expected_name}[[:space:]]+v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$ ]]
}

verify_nemoclaw() {
  refresh_path
  if have "$_CLI_BIN"; then
    local resolved_cli
    resolved_cli="$(command -v "$_CLI_BIN")"
    if is_real_nemoclaw_cli "$resolved_cli" "$_CLI_BIN"; then
      _CLI_PATH="$resolved_cli"
      ensure_nemoclaw_shim || true
      log "Verified: ${_CLI_BIN} at ${_CLI_PATH}"
      return 0
    fi
    warn "PATH has ${_CLI_BIN} at ${resolved_cli} but it is not the real CLI — removing placeholder"
    npm uninstall -g nemoclaw 2>/dev/null || true
  fi

  local npm_bin
  npm_bin="$(resolve_npm_bin)" || true
  if [[ -n "$npm_bin" && -x "$npm_bin/$_CLI_BIN" ]]; then
    if is_real_nemoclaw_cli "$npm_bin/$_CLI_BIN" "$_CLI_BIN"; then
      ensure_nemoclaw_shim || true
      if have "$_CLI_BIN"; then
        _CLI_PATH="$(command -v "$_CLI_BIN")"
        log "Verified: ${_CLI_BIN} at ${_CLI_PATH}"
        return 0
      fi
      _CLI_PATH="$npm_bin/$_CLI_BIN"
      warn "Verified ${_CLI_PATH} but PATH does not resolve ${_CLI_BIN} yet — refresh shell PATH"
      return 0
    fi
    warn "Found broken placeholder at $npm_bin/$_CLI_BIN"
    npm uninstall -g nemoclaw 2>/dev/null || true
  fi

  die "verify_nemoclaw failed — ${_CLI_BIN} not found after install"
}

clone_nemoclaw_ref() {
  local ref="$1" dest="$2"
  rm -rf "$dest"
  mkdir -p "$(dirname "$dest")"
  git init --quiet "$dest"
  git -C "$dest" remote add origin https://github.com/NVIDIA/NemoClaw.git
  git -C "$dest" fetch --quiet --depth 1 origin "$ref"
  git -C "$dest" -c advice.detachedHead=false checkout --quiet --detach FETCH_HEAD
}

verify_payload_installer() {
  local payload="$1"
  [[ -f "$payload" ]] || die "Missing installer payload: $payload"
  grep -q "$PAYLOAD_MARKER" "$payload" || die "Not a versioned installer payload: $payload"
  head -1 "$payload" | grep -qE '^#!.*(sh|bash)' || die "Installer missing shell shebang"
}

bootstrap_verify_ref() {
  local ref="$1"
  local tmpdir="${TMPDIR:-/tmp}/nemoclaw-bootstrap-$$"
  local source_root="${tmpdir}/source"
  trap 'rm -rf "${tmpdir:-}"' RETURN
  log "Bootstrap: shallow-clone ${ref} to temp for payload verification"
  clone_nemoclaw_ref "$ref" "$source_root"
  verify_payload_installer "${source_root}/scripts/install.sh"
  log "Bootstrap: payload verified (${source_root}/scripts/install.sh)"
}

clone_persistent_source() {
  local ref="$1"
  log "Cloning ${ref} -> ${NEMOCLAW_SRC}"
  clone_nemoclaw_ref "$ref" "$NEMOCLAW_SRC"
  git -C "$NEMOCLAW_SRC" fetch --depth=1 origin 'refs/tags/v*:refs/tags/v*' 2>/dev/null || true
  git -C "$NEMOCLAW_SRC" describe --tags --match 'v*' 2>/dev/null \
    | sed 's/^v//' >"$NEMOCLAW_SRC/.version" || true
}

ensure_docker() {
  [[ "${SKIP_DOCKER:-}" == "1" ]] && { log "SKIP_DOCKER=1 — skipping Docker setup"; return 0; }
  case "$(uname -s)" in
    Darwin | MINGW* | MSYS*) return 0 ;;
  esac
  if docker info >/dev/null 2>&1; then
    log "Docker OK"
    return 0
  fi
  if ! have docker; then
    local docker_tmp
    docker_tmp="$(mktemp)"
    curl -fsSL https://get.docker.com -o "$docker_tmp"
    head -1 "$docker_tmp" | grep -qE '^#!.*(sh|bash)' || die "Invalid Docker install script"
    sudo sh "$docker_tmp"
    rm -f "$docker_tmp"
  fi
  if have systemctl && ! systemctl is-active --quiet docker 2>/dev/null; then
    sudo systemctl enable --now docker 2>/dev/null || true
  fi
  if [[ "$(id -u)" -ne 0 ]] && ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "$(id -un)" || true
    die "Docker installed but docker group not active — run: newgrp docker  (or re-login), then re-run step 4"
  fi
  docker info >/dev/null 2>&1 || die "Docker not reachable"
}

install_nodejs() {
  ensure_nvm_loaded
  if have node; then
    local current_version current_npm_major
    current_version="$(node --version 2>/dev/null || true)"
    current_npm_major="$(printf '%s' "$(npm --version 2>/dev/null || echo 0)" | cut -d. -f1)"
    if [[ "${current_version#v}" > "$MIN_NODE_VERSION" || "${current_version#v}" == "$MIN_NODE_VERSION" ]] \
      && [[ "$current_npm_major" =~ ^[0-9]+$ ]] \
      && ((current_npm_major >= MIN_NPM_MAJOR)); then
      log "Node.js OK: ${current_version}"
      return 0
    fi
  fi
  local nvm_tmp
  nvm_tmp="$(mktemp)"
  curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" -o "$nvm_tmp"
  if have sha256sum; then
    sha256sum "$nvm_tmp" | awk '{print $1}' | grep -qx "$NVM_SHA256" || die "nvm installer checksum mismatch"
  fi
  bash "$nvm_tmp"
  rm -f "$nvm_tmp"
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck disable=SC1090
  . "$NVM_DIR/nvm.sh"
  nvm install 22 --no-progress
  nvm alias default 22 2>/dev/null || true
  nvm use 22 --silent
  log "Node.js installed: $(node --version), npm $(npm --version)"
}

fix_npm_permissions() {
  [[ "$(uname -s)" != "Linux" ]] && return 0
  local npm_prefix
  npm_prefix="$(npm config get prefix 2>/dev/null || true)"
  [[ -n "$npm_prefix" ]] || return 0
  if [[ -w "$npm_prefix" || -w "$npm_prefix/lib" ]]; then
    return 0
  fi
  mkdir -p "$HOME/.npm-global"
  npm config set prefix "$HOME/.npm-global"
  export PATH="$HOME/.npm-global/bin:$PATH"
  log "npm prefix -> $HOME/.npm-global"
}

accept_usage_notice() {
  mkdir -p "$HOME/.nemoclaw"
  chmod 700 "$HOME/.nemoclaw" 2>/dev/null || true
  if [[ -f "$HOME/.nemoclaw/usage-notice.json" ]]; then
    return 0
  fi
  printf '{\n  "acceptedVersion": "1",\n  "acceptedAt": "%s"\n}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$HOME/.nemoclaw/usage-notice.json"
  chmod 600 "$HOME/.nemoclaw/usage-notice.json" 2>/dev/null || true
}

pre_extract_openclaw() {
  local install_dir="$1"
  local openclaw_version
  openclaw_version="$(
    awk '/^ARG OPENCLAW_VERSION=/ {
      sub(/^[^=]*=/, ""); gsub(/ /, ""); print; exit
    }' "${install_dir}/Dockerfile.base" 2>/dev/null || true
  )"
  [[ -n "$openclaw_version" ]] || { warn "Could not resolve openclaw version — skipping pre-extract"; return 0; }
  local tmpdir tgz
  tmpdir="$(mktemp -d)"
  npm pack "openclaw@${openclaw_version}" --pack-destination "$tmpdir"
  tgz="$(find "$tmpdir" -maxdepth 1 -name 'openclaw-*.tgz' -print -quit)"
  mkdir -p "${install_dir}/node_modules/openclaw"
  tar xzf "$tgz" -C "${install_dir}/node_modules/openclaw" --strip-components=1
  rm -rf "$tmpdir"
  log "Pre-extracted openclaw@${openclaw_version}"
}

install_nemoclaw_from_source() {
  export NEMOCLAW_INSTALLING=1
  cd "$NEMOCLAW_SRC"
  pre_extract_openclaw "$NEMOCLAW_SRC"
  npm install --ignore-scripts
  npm run --if-present build:cli
  (cd nemoclaw && npm install --ignore-scripts && npm run build)
  npm link
}

install_openshell() {
  bash "${NEMOCLAW_SRC}/scripts/install-openshell.sh"
}

registered_sandbox_count() {
  local reg_file="${HOME}/.nemoclaw/sandboxes.json"
  [[ -f "$reg_file" ]] || { printf '0'; return; }
  node -e '
    const fs = require("fs");
    try {
      const data = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
      process.stdout.write(String(Object.keys(data.sandboxes || {}).length));
    } catch {
      process.stdout.write("0");
    }
  ' "$reg_file" 2>/dev/null || printf '0'
}

run_host_preflight() {
  local preflight="${NEMOCLAW_SRC}/dist/lib/onboard/preflight.js"
  [[ -f "$preflight" ]] || { warn "Preflight module missing (build:cli not run?) — skipping"; return 0; }
  node -e '
    const { assessHost, planHostRemediation } = require(process.argv[1]);
    const host = assessHost();
    const blocking = planHostRemediation(host).filter((a) => a && a.blocking);
    if (host.runtime && host.runtime !== "unknown") {
      console.log("runtime:", host.runtime);
    }
    for (const action of blocking) {
      console.log("blocking:", action.title, "-", action.reason);
    }
    process.exit(blocking.length ? 10 : 0);
  ' "$preflight"
}

run_installer_onboard() {
  verify_nemoclaw
  local cli="${_CLI_PATH:-$(command -v "$_CLI_BIN" || true)}"
  [[ -n "$cli" && -x "$cli" ]] || die "nemoclaw CLI not available for onboard"

  local -a cmd=(onboard --non-interactive --yes-i-accept-third-party-software --yes)
  if [[ "${NEMOCLAW_FRESH:-}" == "1" ]]; then
    cmd+=(--fresh)
  fi

  export_provider_env

  log "Running (install.sh style): ${cli} ${cmd[*]} (NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER})"
  if ! "$cli" "${cmd[@]}"; then
    die "nemoclaw onboard failed (exit $?)"
  fi
  if ! sandbox_exists; then
    die "nemoclaw onboard exited OK but sandbox '${NEMOCLAW_SANDBOX_NAME}' was not created — check provider credentials above"
  fi

  local preexisting
  preexisting="$(registered_sandbox_count)"
  if [[ "${preexisting:-0}" -gt 0 ]] 2>/dev/null; then
    log "Checking sandbox upgrades"
    "$cli" upgrade-sandboxes --auto 2>&1 || warn "upgrade-sandboxes failed (non-fatal)"
  fi
}

sandbox_exists() {
  have openshell && openshell sandbox get "$NEMOCLAW_SANDBOX_NAME" >/dev/null 2>&1
}

validate_provider_env() {
  [[ -n "${NEMOCLAW_PROVIDER:-}" ]] || die "NEMOCLAW_PROVIDER is required (build or custom)"
  case "${NEMOCLAW_PROVIDER}" in
    custom)
      [[ -n "${NEMOCLAW_ENDPOINT_URL:-}" ]] || die "NEMOCLAW_PROVIDER=custom requires NEMOCLAW_ENDPOINT_URL"
      [[ -n "${COMPATIBLE_API_KEY:-}" ]] || die "NEMOCLAW_PROVIDER=custom requires COMPATIBLE_API_KEY"
      ;;
    build)
      if [[ -z "${NVIDIA_API_KEY:-}" ]]; then
        die "NEMOCLAW_PROVIDER=build requires NVIDIA_API_KEY (or NGC_CLI_API_KEY) in non-interactive mode"
      fi
      ;;
    *)
      die "NEMOCLAW_PROVIDER=${NEMOCLAW_PROVIDER} is not supported for step 5 (expected build or custom)"
      ;;
  esac
}

export_provider_env() {
  export NEMOCLAW_PROVIDER
  export NEMOCLAW_MODEL
  export NEMOCLAW_NON_INTERACTIVE
  export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE
  export NEMOCLAW_SANDBOX_NAME
  export NVIDIA_API_KEY="${NVIDIA_API_KEY:-}"
  if [[ "${NEMOCLAW_PROVIDER}" == "custom" ]]; then
    export NEMOCLAW_ENDPOINT_URL
    export COMPATIBLE_API_KEY
  fi
}

strip_ansi() {
  sed -E 's/\x1B\[[0-9;]*[[:alpha:]]//g'
}

forward_running_for_sandbox() {
  local port="$1" sandbox_name="$2"
  openshell forward list 2>/dev/null \
    | strip_ansi \
    | awk -v name="$sandbox_name" -v port="$port" \
        '$1 == name && $3 == port && tolower($NF) == "running" { found = 1 } END { exit found ? 0 : 1 }'
}

dashboard_forward_healthy() {
  local port="$1"
  have curl && curl -fsS "http://127.0.0.1:${port}/health" 2>/dev/null \
    | grep -q '"ok"[[:space:]]*:[[:space:]]*true'
}

ensure_dashboard_forward() {
  local port="${NEMOCLAW_DASHBOARD_PORT:-18789}"
  local forward_log="/tmp/nemoclaw-forward-${port}.log"
  have openshell || { warn "OpenShell not available — cannot start dashboard forward"; return 1; }

  log "Starting dashboard port-forward on ${port} for sandbox ${NEMOCLAW_SANDBOX_NAME}"
  if dashboard_forward_healthy "$port"; then
    sleep 2
    if dashboard_forward_healthy "$port"; then
      log "Dashboard forward on ${port} is already healthy"
      return 0
    fi
  fi

  openshell forward stop "$port" "$NEMOCLAW_SANDBOX_NAME" >/dev/null 2>&1 || true
  pkill -TERM -f "[o]penshell forward start ${port} ${NEMOCLAW_SANDBOX_NAME}" >/dev/null 2>&1 || true

  if have setsid; then
    setsid -f openshell forward start "$port" "$NEMOCLAW_SANDBOX_NAME" </dev/null >"$forward_log" 2>&1 || true
  else
    openshell forward start --background "$port" "$NEMOCLAW_SANDBOX_NAME" </dev/null >"$forward_log" 2>&1 || true
  fi

  local attempt
  for attempt in $(seq 1 "${NEMOCLAW_DASHBOARD_FORWARD_TIMEOUT:-60}"); do
    if dashboard_forward_healthy "$port"; then
      log "Dashboard forward on ${port} is healthy"
      return 0
    fi
    sleep 1
  done

  warn "Dashboard forward on ${port} is not healthy — OpenClaw UI may be unreachable at http://127.0.0.1:${port}/"
  if [[ -f "$forward_log" ]]; then
    tail -n 10 "$forward_log" | sed "s/^/[forward log] /" >&2 || true
  fi
  warn "Try manually: openshell forward start --background ${port} ${NEMOCLAW_SANDBOX_NAME}"
  return 1
}

ensure_tunnel_start() {
  [[ "${SKIP_TUNNEL:-}" == "1" ]] && { log "SKIP_TUNNEL=1 — skipping cloudflared tunnel"; return 0; }
  refresh_path
  have nemoclaw || { warn "nemoclaw not on PATH — skipping tunnel start"; return 0; }
  if ! have cloudflared; then
    warn "cloudflared not installed — skipping 'nemoclaw tunnel start' (no public URL)"
    return 0
  fi

  local pid_dir="/tmp/nemoclaw-services-${NEMOCLAW_SANDBOX_NAME}"
  if [[ -f "${pid_dir}/cloudflared.pid" ]]; then
    local pid
    pid="$(cat "${pid_dir}/cloudflared.pid" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      log "cloudflared already running (PID ${pid})"
      return 0
    fi
  fi

  export NEMOCLAW_SANDBOX_NAME
  log "Starting cloudflared tunnel: nemoclaw tunnel start (sandbox=${NEMOCLAW_SANDBOX_NAME})"
  if nemoclaw tunnel start; then
    log "cloudflared tunnel started"
    return 0
  fi
  warn "nemoclaw tunnel start failed (non-fatal) — check ${pid_dir}/cloudflared.log"
  return 1
}

step1_pull() {
  log "STEP 1 — pull dependencies / repo / Docker"
  run_step "bootstrap verify ref" bootstrap_verify_ref "$NEMOCLAW_INSTALL_REF"
  if [[ "${SKIP_CLONE:-}" == "1" ]]; then
    log "SKIP_CLONE=1 — keeping ${NEMOCLAW_SRC}"
    [[ -d "$NEMOCLAW_SRC/.git" ]] || die "SKIP_CLONE=1 but ${NEMOCLAW_SRC} is not a git checkout"
  else
    run_step "clone persistent source" clone_persistent_source "$NEMOCLAW_INSTALL_REF"
  fi
  run_step "docker setup" ensure_docker
}

step2_presteps() {
  log "STEP 2 — install dependencies / presteps"
  [[ -d "$NEMOCLAW_SRC" ]] || die "Missing ${NEMOCLAW_SRC} — run step 1 first"
  run_step "accept usage notice" accept_usage_notice
  run_step "install node.js 22" install_nodejs
  refresh_path
  run_step "setup-jetson" bash "${NEMOCLAW_SRC}/scripts/setup-jetson.sh"
  run_step "fix npm permissions" fix_npm_permissions
}

step3_install_nemoclaw() {
  log "STEP 3 — install NemoClaw + OpenShell"
  [[ -d "$NEMOCLAW_SRC" ]] || die "Missing ${NEMOCLAW_SRC} — run step 1 first"
  refresh_path
  run_step "pre-extract + npm build/link" install_nemoclaw_from_source
  run_step "install openshell" install_openshell
  run_step "verify nemoclaw CLI" verify_nemoclaw
  refresh_path
  log "nemoclaw: $(nemoclaw --version 2>/dev/null || echo missing)"
  log "openshell: $(openshell --version 2>/dev/null || echo missing)"
}

resolve_quick_sandbox_script() {
  local quick_script="${NEMOCLAW_QUICK_SANDBOX_SCRIPT:-$(dirname "$0")/nemoclaw-quick-sandbox.sh}"
  [[ -f "$quick_script" ]] || quick_script="$(dirname "$0")/nemoclaw-quick-sandbox.sh"
  [[ -f "$quick_script" ]] || die "Quick sandbox enabled but missing ${quick_script}"
  printf '%s' "$quick_script"
}

step4_build_sandbox_image() {
  [[ -d "$NEMOCLAW_SRC" ]] || die "Missing ${NEMOCLAW_SRC} — run step 1 first"
  refresh_path
  if [[ "${NO_SANDBOX_CACHE:-0}" == "1" ]]; then
    log "STEP 4 — skipped (NO_SANDBOX_CACHE=1 — using install.sh onboard instead of quick sandbox)"
    return 0
  fi
  log "STEP 4 — build nemoclaw-sandbox:local"
  bash "$(resolve_quick_sandbox_script)" --build-only
}

step5_create_sandbox() {
  [[ -d "$NEMOCLAW_SRC" ]] || die "Missing ${NEMOCLAW_SRC} — run step 1 first"
  refresh_path
  validate_provider_env

  if [[ "${NO_SANDBOX_CACHE:-0}" != "1" ]]; then
    log "STEP 5 — create sandbox from quick sandbox script"
    bash "$(resolve_quick_sandbox_script)" --create-only "${NEMOCLAW_SANDBOX_NAME:-demo}"
  else
    log "STEP 5 — create sandbox from NemoClaw onboard"
    [[ "${SKIP_ONBOARD:-}" == "1" ]] && { log "SKIP_ONBOARD=1 — skipping"; return 0; }
    run_step "host preflight" run_host_preflight
    run_step "nemoclaw onboard" run_installer_onboard
  fi

  run_step "dashboard port-forward" ensure_dashboard_forward || true
  run_step "cloudflared tunnel" ensure_tunnel_start || true
  run_step "sandbox status" bash -c 'openshell sandbox list; nemoclaw status || true'
}

resolve_steps() {
  if [[ "$#" -gt 0 ]]; then
    printf '%s\n' "$@"
    return
  fi
  if [[ -n "${STEP:-}" ]]; then
    printf '%s\n' "$STEP"
    return
  fi
  if [[ "${NO_SANDBOX_CACHE:-0}" != "1" ]]; then
    printf '1\n2\n3\n4\n5\n'
    return
  fi
  printf '1\n2\n3\n5\n'
}

main() {
  local steps
  mapfile -t steps < <(resolve_steps "$@")
  log "Log file: ${LOG_FILE}"
  log "NEMOCLAW_INSTALL_REF=${NEMOCLAW_INSTALL_REF} NEMOCLAW_SRC=${NEMOCLAW_SRC}"
  local total_start total_end
  total_start="$(date +%s)"
  local step
  for step in "${steps[@]}"; do
    case "$step" in
      1) step1_pull ;;
      2) step2_presteps ;;
      3) step3_install_nemoclaw ;;
      4) step4_build_sandbox_image ;;
      5) step5_create_sandbox ;;
      all)
        step1_pull
        step2_presteps
        step3_install_nemoclaw
        [[ "${NO_SANDBOX_CACHE:-0}" != "1" ]] && step4_build_sandbox_image
        step5_create_sandbox
        ;;
      *) die "Unknown step: ${step} (use 1, 2, 3, 4, 5, or all)" ;;
    esac
  done
  total_end="$(date +%s)"
  log "All requested steps finished in $((total_end - total_start))s"
  log "To load PATH in this shell: export PATH=\"${NEMOCLAW_SHIM_DIR}:\$(npm config get prefix 2>/dev/null)/bin:\$PATH\" && source \"\${NVM_DIR:-\$HOME/.nvm}/nvm.sh\" && nvm use 22"
}

main "$@"
