#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# setup-lambda.sh - Prepare a Lambda GPU instance and bring up the VSS "base" profile.
#
# Run this ON the Lambda instance (Ubuntu 22.04/24.04, NVIDIA GPU) from the repo root.
# It reconciles the host to VSS's validated prerequisites, then hands off to the
# existing deploy/docker/scripts/dev-profile.sh helper (which owns data dirs, sysctl,
# env merge, and compose bring-up).
#
# Required:
#   NGC_CLI_API_KEY   NGC / NVIDIA API key with nvcr.io pull access (nvapi-...).
#                     Self-hosting the NIM models is impossible without it.
# Optional:
#   NVIDIA_API_KEY    Only if you later switch a model to a remote build.nvidia.com endpoint.
#   HARDWARE_PROFILE  Override the auto-detected profile (H100 | L40S | RTXPRO6000BW | OTHER).
#   MIN_DISK_GIB      Required free space on the Docker data-root (default 400).
#   VLM_BACKEND       nim_cosmos (default) | rtvlm. rtvlm self-hosts the VLM in the
#                     vss-rtvi-vlm container (OpenAI API on :8018) instead of the in-process
#                     Cosmos NIM; both models then share the single 80 GB GPU.
#
# Usage:
#   export NGC_CLI_API_KEY='nvapi-...'
#   ./deploy/lambda/setup-lambda.sh

set -euo pipefail

MIN_DISK_GIB="${MIN_DISK_GIB:-400}"

# VLM backend: nim_cosmos (default, in-process Cosmos NIM VLM) or rtvlm (self-host the VLM in
# the vss-rtvi-vlm container, OpenAI-compatible API on :8018).
VLM_BACKEND="${VLM_BACKEND:-nim_cosmos}"
case "${VLM_BACKEND}" in
  nim_cosmos|rtvlm) ;;
  *) printf '\033[1;31m[setup] ERROR:\033[0m VLM_BACKEND must be nim_cosmos or rtvlm (got %q)\n' "${VLM_BACKEND}" >&2; exit 1 ;;
esac

log()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup] WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[setup] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then command -v sudo >/dev/null && SUDO="sudo" || die "Need root or sudo."; fi

# Resolve repo root from this script's location so it works from anywhere.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"
DEV_PROFILE="${REPO_ROOT}/deploy/docker/scripts/dev-profile.sh"
[[ -x "$DEV_PROFILE" ]] || die "Cannot find ${DEV_PROFILE}. Run this from inside the VSS repo."

# ---------------------------------------------------------------------------
# 1. Preflight
# ---------------------------------------------------------------------------
log "Preflight checks"
[[ -r /etc/os-release ]] && . /etc/os-release || die "Cannot read /etc/os-release"
case "${VERSION_ID:-}" in
  22.04|24.04) log "OS: ${PRETTY_NAME}" ;;
  *) warn "OS is '${PRETTY_NAME:-unknown}'. VSS validates Ubuntu 22.04/24.04; continuing anyway." ;;
esac

[[ -n "${NGC_CLI_API_KEY:-}" ]] || die \
"NGC_CLI_API_KEY is not set. Self-hosting the NIM models needs an NGC / NVIDIA API key
 with nvcr.io pull access (NVIDIA AI Enterprise / developer account).
 Get one at https://ngc.nvidia.com/ (or https://build.nvidia.com/) then:
   export NGC_CLI_API_KEY='nvapi-...'"

command -v nvidia-smi >/dev/null || die "nvidia-smi not found. This must run on a GPU instance with NVIDIA drivers."
driver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1 | tr -d ' ')"
vram_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | sort -n | tail -n1 | tr -d ' ')"
gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)"
log "GPU: ${gpu_name}  |  driver: ${driver}  |  largest VRAM: ${vram_mib} MiB"
[[ "${driver%%.*}" == "580" ]] || warn "Driver ${driver} is not the validated 580.x series; VSS may still work."
[[ "${vram_mib:-0}" -ge 79000 ]] || warn "Largest GPU has <80 GB VRAM. Base shared LLM+VLM expects ~80 GB; you may hit OOM."
if [[ "${VLM_BACKEND}" == "rtvlm" ]]; then
  log "VLM backend: rtvlm (self-hosted vss-rtvi-vlm). LLM (Nemotron-Nano-9B) and the VLM (cosmos3-nano) share one GPU."
  log "  On OOM, lower the VLM's vLLM memory fraction: re-run with RTVI_VLLM_GPU_MEMORY_UTILIZATION exported (default 0.4 on H100)."
fi

# Pick a hardware profile for dev-profile.sh (--hardware-profile).
if [[ -z "${HARDWARE_PROFILE:-}" ]]; then
  case "$gpu_name" in
    *H100*)                 HARDWARE_PROFILE="H100" ;;
    *L40S*)                 HARDWARE_PROFILE="L40S" ;;
    *"RTX PRO 6000"*|*RTXPRO6000*) HARDWARE_PROFILE="RTXPRO6000BW" ;;
    *)                      HARDWARE_PROFILE="OTHER" ;;
  esac
fi
log "Hardware profile: ${HARDWARE_PROFILE}"

# ---------------------------------------------------------------------------
# 2. Docker Engine in the supported range [28.3.3, 29.5.0)
# ---------------------------------------------------------------------------
# Returns 0 if version $1 satisfies  min ($2) <= $1 < maxexcl ($3).
ver_in_range() { # ver min maxexcl
  local ver="$1" min="$2" max="$3"
  # min <= ver  ->  min sorts first (or equal)
  [[ "$(printf '%s\n%s\n' "$min" "$ver" | sort -V | head -n1)" == "$min" ]] || return 1
  # ver < max   ->  ver != max and max sorts last
  [[ "$ver" != "$max" && "$(printf '%s\n%s\n' "$ver" "$max" | sort -V | tail -n1)" == "$max" ]]
}

install_docker() {
  log "Installing supported Docker Engine (28.3.x) from Docker's apt repo"
  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update -y
  # Pin to a known-good 28.3.x build inside the [28.3.3, 29.5.0) window.
  local pkg
  pkg="$(apt-cache madison docker-ce | awk '{print $3}' | grep -E '^5:28\.3\.' | sort -V | tail -n1 || true)"
  if [[ -n "$pkg" ]]; then
    $SUDO apt-get install -y --allow-downgrades \
      docker-ce="$pkg" docker-ce-cli="$pkg" containerd.io docker-buildx-plugin docker-compose-plugin
  else
    warn "No 28.3.x candidate in apt; installing latest docker-ce (verify it stays < 29.5.0)."
    $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  $SUDO systemctl enable --now docker
}

need_docker=1
if command -v docker >/dev/null; then
  dver="$(docker version -f '{{.Server.Version}}' 2>/dev/null || echo 0.0.0)"
  if ver_in_range "$dver" "28.3.3" "29.5.0"; then
    log "Docker Engine ${dver} is in the supported range."
    need_docker=0
  else
    warn "Docker Engine ${dver} is outside [28.3.3, 29.5.0) (29.5.0+ fails NGC pulls). Reinstalling."
  fi
fi
[[ "$need_docker" -eq 1 ]] && { $SUDO apt-get update -y || true; install_docker; }

# Let the current user run docker without sudo. If we must add the group, it only
# takes effect in a NEW session, so we run the deploy under `sg docker` (below) to
# avoid dev-profile.sh's unsudoed `docker compose` hitting a socket-permission error.
ADDED_DOCKER_GROUP=0
if [[ -n "$SUDO" ]] && ! docker info >/dev/null 2>&1; then
  $SUDO usermod -aG docker "$USER" || true
  DOCKER="$SUDO docker"
  ADDED_DOCKER_GROUP=1
else
  DOCKER="docker"
fi
cver="$($DOCKER compose version --short 2>/dev/null || echo 0)"
log "docker compose: ${cver}"

# ---------------------------------------------------------------------------
# 3. NVIDIA Container Toolkit (>= 1.17.8) + Docker runtime wiring
# ---------------------------------------------------------------------------
install_toolkit() {
  log "Installing/upgrading NVIDIA Container Toolkit"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | $SUDO gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | $SUDO tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  $SUDO apt-get update -y
  $SUDO apt-get install -y nvidia-container-toolkit
}
if ! command -v nvidia-ctk >/dev/null; then
  install_toolkit
else
  tk="$(nvidia-ctk --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || echo 0)"
  if [[ "$(printf '%s\n1.17.8\n' "$tk" | sort -V | head -n1)" != "1.17.8" ]]; then
    warn "NVIDIA Container Toolkit ${tk} < 1.17.8; upgrading."; install_toolkit
  else
    log "NVIDIA Container Toolkit ${tk} OK."
  fi
fi
$SUDO nvidia-ctk runtime configure --runtime=docker
$SUDO systemctl restart docker
log "GPU-in-Docker smoke test"
$DOCKER run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L \
  || die "Docker cannot see the GPU. Check the toolkit install and 'nvidia-ctk runtime configure'."

# ---------------------------------------------------------------------------
# 3b. NVDEC video-decode library (libnvcuvid)
# ---------------------------------------------------------------------------
# Lambda's default GPU image ships a compute-only driver WITHOUT libnvcuvid.so.1.
# The Cosmos VLM decodes H.264/H.265 on the GPU (NVDEC) — without this library the
# UI reports "video analysis failed ... libnvcuvid.so.1: cannot open shared object".
# apt only offers newer point releases (which mismatch the running kernel driver), so
# we pull the EXACT-version 64-bit lib straight from NVIDIA's driver installer.
if ldconfig -p 2>/dev/null | grep -q 'libnvcuvid\.so\.1' || ls /usr/lib/x86_64-linux-gnu/libnvcuvid.so.1 >/dev/null 2>&1; then
  log "libnvcuvid (NVDEC) already present on host."
else
  drv="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1 | tr -d ' ')"
  warn "libnvcuvid.so.1 missing (GPU video decode). Fetching exact match for driver ${drv}."
  run="/tmp/nvidia-${drv}.run"; ok=0
  for url in \
    "https://us.download.nvidia.com/tesla/${drv}/NVIDIA-Linux-x86_64-${drv}.run" \
    "https://us.download.nvidia.com/XFree86/Linux-x86_64/${drv}/NVIDIA-Linux-x86_64-${drv}.run" \
    "https://international.download.nvidia.com/tesla/${drv}/NVIDIA-Linux-x86_64-${drv}.run"; do
    if curl -fSL --retry 2 -o "$run" "$url"; then ok=1; break; fi
  done
  if [[ "$ok" -eq 1 ]]; then
    ( cd /tmp && sh "$run" --extract-only >/dev/null 2>&1 )
    lib="/tmp/NVIDIA-Linux-x86_64-${drv}/libnvcuvid.so.${drv}"   # top-level = 64-bit (32-bit is under 32/)
    if [[ -f "$lib" ]]; then
      $SUDO cp -f "$lib" /usr/lib/x86_64-linux-gnu/
      $SUDO ln -sf "libnvcuvid.so.${drv}" /usr/lib/x86_64-linux-gnu/libnvcuvid.so.1
      $SUDO ln -sf "libnvcuvid.so.1"      /usr/lib/x86_64-linux-gnu/libnvcuvid.so
      $SUDO ldconfig
      log "Installed libnvcuvid.so.${drv} (NVDEC) on host."
    else
      warn "Could not find 64-bit libnvcuvid in the installer; GPU video decode may fail. Video Q&A on H.264/H.265 will error until this is resolved."
    fi
  else
    warn "Could not download the matching driver installer; GPU video decode may fail. Video Q&A on H.264/H.265 will error until libnvcuvid.so.1 is installed."
  fi
fi

# ---------------------------------------------------------------------------
# 4. Storage: model caches are named Docker volumes under the Docker data-root.
# ---------------------------------------------------------------------------
data_root="$($DOCKER info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
free_gib="$($SUDO df -BG --output=avail "$data_root" 2>/dev/null | tail -n1 | tr -dc '0-9' || echo 0)"
log "Docker data-root: ${data_root} (${free_gib} GiB free; need >= ${MIN_DISK_GIB})"
if [[ "${free_gib:-0}" -lt "$MIN_DISK_GIB" ]]; then
  # Find the largest writable mount (excluding the current small root) to relocate onto.
  big="$($SUDO df -BG --output=target,avail -x tmpfs -x overlay 2>/dev/null \
        | tail -n +2 | sort -k2 -n | awk '{t=$1; a=$2} END{gsub("G","",a); if(a+0>='"$MIN_DISK_GIB"') print t}')"
  if [[ -n "$big" && "$big" != "/" ]]; then
    warn "Relocating Docker data-root to ${big}/docker to fit ~250 GB of model caches."
    $SUDO systemctl stop docker
    $SUDO mkdir -p "${big}/docker"
    $SUDO mkdir -p /etc/docker
    if [[ -f /etc/docker/daemon.json ]]; then
      $SUDO cp /etc/docker/daemon.json /etc/docker/daemon.json.bak.$$
      warn "Existing /etc/docker/daemon.json backed up; set \"data-root\" to ${big}/docker manually if this fails."
    fi
    printf '{\n  "data-root": "%s/docker"\n}\n' "$big" | $SUDO tee /etc/docker/daemon.json >/dev/null
    $SUDO systemctl start docker
    log "Docker data-root now: $($DOCKER info -f '{{.DockerRootDir}}')"
  else
    warn "No mount with >= ${MIN_DISK_GIB} GiB free found. The VLM cache alone can be ~200 GB; deploy may fail on disk space."
  fi
fi

# ---------------------------------------------------------------------------
# 5. Log in to nvcr.io so NIM images/models can be pulled
# ---------------------------------------------------------------------------
log "Logging in to nvcr.io"
echo "$NGC_CLI_API_KEY" | $DOCKER login nvcr.io -u '$oauthtoken' --password-stdin \
  || die "docker login nvcr.io failed. Check NGC_CLI_API_KEY has nvcr.io pull access."

# ---------------------------------------------------------------------------
# 6. Bring up the VSS base profile via the existing helper
# ---------------------------------------------------------------------------
export NGC_CLI_API_KEY
[[ -n "${NVIDIA_API_KEY:-}" ]] && export NVIDIA_API_KEY
# Let an exported RTVI_VLLM_GPU_MEMORY_UTILIZATION reach dev-profile.sh (rtvlm OOM knob).
[[ -n "${RTVI_VLLM_GPU_MEMORY_UTILIZATION:-}" ]] && export RTVI_VLLM_GPU_MEMORY_UTILIZATION

DP_ARGS=(up --profile base --hardware-profile "$HARDWARE_PROFILE")
[[ "$VLM_BACKEND" == "rtvlm" ]] && DP_ARGS+=(--vlm-backend rtvi)

log "Starting VSS base profile (dev-profile.sh ${DP_ARGS[*]})"
log "First run pulls multi-hundred-GB model images/weights — expect 20-60+ min."
cd "$REPO_ROOT"
if [[ "$ADDED_DOCKER_GROUP" -eq 1 ]]; then
  # docker group isn't active in this shell yet; run the helper with it active so its
  # (unsudoed) `docker compose` can reach the daemon. NGC_CLI_API_KEY is exported above
  # and is inherited by the sg subshell.
  printf -v _dp_cmd '%q ' "$DEV_PROFILE" "${DP_ARGS[@]}"
  sg docker -c "$_dp_cmd"
else
  "$DEV_PROFILE" "${DP_ARGS[@]}"
fi

# ---------------------------------------------------------------------------
# 7. Health wait
# ---------------------------------------------------------------------------
# VLM health endpoint depends on the backend: the in-process Cosmos NIM listens on
# :30082; the self-hosted rtvi-vlm listens on :8018.
if [[ "$VLM_BACKEND" == "rtvlm" ]]; then VLM_PORT=8018; VLM_LABEL="VLM (rtvi-vlm, cosmos3-nano)"; else VLM_PORT=30082; VLM_LABEL="VLM (Cosmos3 NIM)"; fi

log "Waiting for the LLM NIM to report ready (:30081) — this is the slow one on first boot"
[[ "$VLM_BACKEND" == "rtvlm" ]] && log "rtvi-vlm has a ~20 min start_period on first boot (vLLM warmup + model pull); 'not ready' early is expected."
ready=0
for _ in $(seq 1 180); do   # up to ~90 min
  if curl -fsS http://127.0.0.1:30081/v1/health/ready >/dev/null 2>&1; then ready=1; break; fi
  sleep 30
done
# Give the VLM a bounded extra window to come up (LLM usually leads on shared-GPU boot).
vlm_ready=0
for _ in $(seq 1 40); do   # up to ~20 min
  if curl -fsS "http://127.0.0.1:${VLM_PORT}/v1/health/ready" >/dev/null 2>&1; then vlm_ready=1; break; fi
  sleep 30
done
$DOCKER ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
# Public IP for the SSH tunnel (hostname -I returns the private IP on Lambda).
host_ip="$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || true)"
[[ -z "$host_ip" ]] && host_ip="<instance-public-ip>"
if [[ "$ready" -eq 1 ]]; then
  log "LLM is ready."
else
  warn "LLM not ready yet after the wait window. Check 'docker logs' for the nemotron container; it may just still be downloading."
fi
if [[ "$vlm_ready" -eq 1 ]]; then
  log "${VLM_LABEL} is ready. VSS base is up."
else
  warn "${VLM_LABEL} not ready yet on :${VLM_PORT}. Check its container logs; on first boot it may still be pulling weights / warming up."
fi
cat <<EOF

============================================================================
  VSS base profile deployed (VLM backend: ${VLM_BACKEND}).
  Open the UI from your laptop over an SSH tunnel:
      ssh -L 7777:localhost:7777 ubuntu@${host_ip:-<instance-ip>}
      # then browse to  http://localhost:7777

  Useful checks on the instance:
      docker ps
      curl -f http://127.0.0.1:30081/v1/health/ready       # LLM
      curl -f http://127.0.0.1:${VLM_PORT}/v1/health/ready   # ${VLM_LABEL}

  Tear the stack down (keeps the instance):
      ./deploy/docker/scripts/dev-profile.sh down
============================================================================
EOF
