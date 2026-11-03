#!/usr/bin/env bash
#
# vm-ensure-docker.sh — runs ON THE VM. start.sh pipes it over SSH
# (`ssh host bash -s -- <args> < vm-ensure-docker.sh`), so the laptop copy is
# always the one that runs, even when the VM checkout is behind.
#
# Usage: vm-ensure-docker.sh <repo-root> <repo-url> <pull-timeout-s> <up-timeout-s> <compose-service>...
#
# Takes the VM from "whatever state" to "these Docker appliances are running",
# failing with a specific message (never hanging) at each step:
#   1. repo checkout present (clones <repo-url> if it is missing)
#   2. generated.env.remote present (secrets: cannot be created here), and its
#      HOST_IP equal to the VM's current LAN address (backed up, then fixed: a
#      stale HOST_IP makes Kafka advertise a dead address and the broker health
#      check hang)
#   3. docker usable
#   4. images of the services (and their dependencies) present, else pulled
#   5. `docker compose up -d --yes <services>` under a timeout (--yes: recreate on config mismatch, never prompt), with diagnostics on
#      timeout or failure
# Exit codes: 0 ok, 10 repo, 11 env file, 12 docker, 13 image pull, 14 up failed, 124 up timed out.

set -uo pipefail

# Pin all Docker operations / subprocesses to GPU 0 to protect GPU 1 for co-tenants
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export NVIDIA_VISIBLE_DEVICES="${NVIDIA_VISIBLE_DEVICES:-0}"

REPO_ROOT="${1:?repo root}"
REPO_URL="${2:-}"
PULL_TIMEOUT="${3:-1800}"
UP_TIMEOUT="${4:-600}"
shift 4
SERVICES=("$@")

PROFILE_DIR="developer-profiles/dev-profile-incident"
ENV_FILE="${PROFILE_DIR}/generated.env.remote"
say() { echo "[vm] $*"; }
fail() { local rc="$1"; shift; echo "[vm] ERROR: $*" >&2; exit "$rc"; }

# 1. repo checkout
if [ ! -f "${REPO_ROOT}/deploy/docker/compose.yml" ]; then
  say "no VSS checkout at ${REPO_ROOT}."
  [ -n "$REPO_URL" ] || fail 10 "no checkout at ${REPO_ROOT} and no repo URL to clone. Clone the repo there, then re-run."
  say "cloning ${REPO_URL} into ${REPO_ROOT} (timeout ${PULL_TIMEOUT}s)..."
  if ! timeout "$PULL_TIMEOUT" git clone "$REPO_URL" "$REPO_ROOT"; then
    fail 10 "git clone of ${REPO_URL} into ${REPO_ROOT} failed (permissions on $(dirname "$REPO_ROOT"), or no git credentials on the VM). Clone it by hand, then re-run."
  fi
fi
cd "${REPO_ROOT}/deploy/docker" || fail 10 "cannot enter ${REPO_ROOT}/deploy/docker"

# 2. env file and HOST_IP
if [ ! -f "$ENV_FILE" ]; then
  fail 11 "${REPO_ROOT}/deploy/docker/${ENV_FILE} not found. It holds secrets, so create it by hand: cp ${REPO_ROOT}/deploy/docker/${PROFILE_DIR}/.env ${REPO_ROOT}/deploy/docker/${ENV_FILE} and fill in the real values (.docs/incident-profile-operations.md section 5)."
fi
vm_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)"
env_ip="$(sed -n 's/^HOST_IP=//p' "$ENV_FILE" | head -n 1 | tr -d "\"' ")"
if [ -n "$vm_ip" ] && [ -n "$env_ip" ] && [ "$vm_ip" != "$env_ip" ]; then
  backup="${ENV_FILE}.bak-$(date +%Y%m%d-%H%M%S)"
  say "HOST_IP in generated.env.remote is ${env_ip} but this VM is ${vm_ip}; backing up to ${backup} and updating it."
  cp -p "$ENV_FILE" "$backup" || fail 11 "could not back up ${ENV_FILE}"
  # Rewrite in place (cat >) so the file keeps its owner, group and mode.
  if ! { sed "s|^HOST_IP=.*|HOST_IP=${vm_ip}|" "$backup" >"${backup}.new" && cat "${backup}.new" >"$ENV_FILE"; }; then
    fail 11 "could not update HOST_IP in ${ENV_FILE}"
  fi
  rm -f "${backup}.new"
  say "HOST_IP is now ${vm_ip}; 'up' recreates the containers whose environment changed."
fi

# 3. docker usable (sudo only when passwordless, as before)
DOCKER=(docker)
if ! timeout 30 docker info >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null && timeout 30 sudo -n docker info >/dev/null 2>&1; then
    DOCKER=(sudo -n docker)
  else
    fail 12 "docker is not usable for $(id -un): the daemon is down, hung, or this user is not in the 'docker' group. Try 'sudo systemctl start docker' on the VM, then re-run."
  fi
fi
COMPOSE=("${DOCKER[@]}" compose -f compose.yml -f "${PROFILE_DIR}/compose.override.yml" --env-file "$ENV_FILE")

if [ "${#SERVICES[@]}" -eq 0 ]; then
  say "no Docker services requested."
  exit 0
fi

# 4. images (the service images plus their dependencies and init containers)
missing=()
while read -r image; do
  [ -n "$image" ] || continue
  "${DOCKER[@]}" image inspect "$image" >/dev/null 2>&1 || missing+=("$image")
done < <("${COMPOSE[@]}" config --images "${SERVICES[@]}" 2>/dev/null | sort -u)
if [ "${#missing[@]}" -gt 0 ]; then
  say "images not on this VM: ${missing[*]}"
  say "pulling registry images (timeout ${PULL_TIMEOUT}s); services built from source (mdx-*) are built by 'up' below..."
  timeout --kill-after=15 "$PULL_TIMEOUT" "${COMPOSE[@]}" pull --ignore-buildable --include-deps --policy missing "${SERVICES[@]}"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    if [ "$rc" -eq 124 ]; then
      fail 13 "image pull did not finish within ${PULL_TIMEOUT}s (slow or stalled registry link). Re-run to resume, or raise VSS_PULL_TIMEOUT."
    fi
    fail 13 "image pull failed (exit ${rc}). For nvcr.io images this is usually an expired NGC login: on the VM run 'source ${REPO_ROOT}/deploy/docker/${PROFILE_DIR}/.scripts/ngc-env.sh' then 'echo \$NGC_CLI_API_KEY | docker login nvcr.io -u \\\$oauthtoken --password-stdin', then re-run."
  fi
else
  say "all images present."
fi

# 5. up, bounded
say "docker compose up -d ${SERVICES[*]} (timeout ${UP_TIMEOUT}s)..."
timeout --kill-after=15 "$UP_TIMEOUT" "${COMPOSE[@]}" up -d --yes "${SERVICES[@]}"
rc=$?
if [ "$rc" -ne 0 ]; then
  if [ "$rc" -eq 124 ]; then
    echo "[vm] ERROR: 'docker compose up' did not finish within ${UP_TIMEOUT}s - a dependency health check is stuck." >&2
  else
    echo "[vm] ERROR: 'docker compose up' failed (exit ${rc})." >&2
  fi
  echo "[vm] Container states:" >&2
  "${DOCKER[@]}" ps -a --filter label=com.docker.compose.project=mdx --format '  {{.Names}}: {{.Status}}' >&2 || true
  for c in kafka vss-broker-health-check; do
    if "${DOCKER[@]}" inspect "$c" >/dev/null 2>&1; then
      echo "[vm] last log lines of ${c}:" >&2
      "${DOCKER[@]}" logs --tail 8 "$c" 2>&1 | sed 's/^/    /' >&2 || true
    fi
  done
  echo "[vm] Hint: a stuck Kafka/broker check usually means HOST_IP in generated.env.remote (now ${env_ip:-unset}; VM is ${vm_ip:-unknown}) is not this VM's address. Fix it and re-run; containers made with the old value are recreated by 'up'." >&2
  [ "$rc" -eq 124 ] && exit 124
  exit 14
fi

say "services requested: ${SERVICES[*]}"
"${DOCKER[@]}" ps --filter label=com.docker.compose.project=mdx --format '  {{.Names}}: {{.Status}}'
