#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Fresh, operator-managed VSS skill-eval worker bootstrap. No disk formatting.
set -Eeuo pipefail

mode=check
repair_docker=false
allow_ubuntu_22=false
data_mount=/data
docker_root=
min_driver=595.58.03
min_root_gb=200
min_data_gb=500

usage() {
  printf 'Usage: %s [--check|--apply] [--repair-docker] [--allow-ubuntu-22-04] [--min-driver VERSION]\n' "$0"
  printf '  --check  Validate prerequisites and show planned changes (default).\n'
  printf '  --apply  Prepare storage on a fresh worker and ensure uv exists.\n'
  printf '  --repair-docker  On --apply, pin Docker and set cgroupfs on an idle, empty worker.\n'
  printf '  --allow-ubuntu-22-04  Explicit test-only exception to the Ubuntu 24.04 baseline.\n'
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
say() { printf '[vss-worker] %s\n' "$*"; }

while (($#)); do
  case "$1" in
    --check) mode=check ;;
    --apply) mode=apply ;;
    --repair-docker) repair_docker=true ;;
    --allow-ubuntu-22-04) allow_ubuntu_22=true ;;
    --min-driver)
      (($# >= 2)) || die '--min-driver requires a version'
      min_driver=$2
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

[[ $min_driver =~ ^[0-9]+(\.[0-9]+){1,2}$ ]] || die 'invalid --min-driver version'
(( EUID != 0 )) || die 'run as the normal Brev user, not root (sudo is used only for system changes)'

# shellcheck source=/dev/null
source /etc/os-release
[[ ${ID:-} == ubuntu ]] || die "VSS x86 workers require Ubuntu; found ${PRETTY_NAME:-unknown}"
if [[ ${VERSION_ID:-} != 24.04 ]]; then
  [[ $allow_ubuntu_22 == true && ${VERSION_ID:-} == 22.04 ]] ||
    die "VSS x86 baseline is Ubuntu 24.04; found ${PRETTY_NAME:-unknown} (test-only --allow-ubuntu-22-04 available)"
  say 'WARNING: Ubuntu 22.04 is a test-only exception; this check does not certify VSS support'
fi
[[ $(uname -m) == x86_64 ]] || die 'this bootstrap currently supports x86_64 only'

command -v nvidia-smi >/dev/null || die 'nvidia-smi missing; provision a VSS-supported GPU driver first'
gpu_rows=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader) || die 'nvidia-smi failed'
[[ -n $gpu_rows ]] || die 'no GPUs found'
gpu_count=$(printf '%s\n' "$gpu_rows" | wc -l | tr -d ' ')
while IFS=, read -r gpu_name driver; do
  driver=${driver//[[:space:]]/}
  [[ $driver =~ ^[0-9]+(\.[0-9]+){1,2}$ ]] || die "unparseable driver for $gpu_name: $driver"
  lowest=$(printf '%s\n%s\n' "$min_driver" "$driver" | sort -V | head -n 1)
  [[ $lowest == "$min_driver" ]] || die "driver $driver is below required $min_driver"
done <<< "$gpu_rows"
say "GPU count: $gpu_count; driver floor: $min_driver"

size_gb() { df -BG --output=size "$1" | tail -n 1 | tr -dc '0-9'; }
root_gb=$(size_gb /)
(( root_gb >= min_root_gb )) ||
  die "root disk is ${root_gb}G; this eval task declares a ${min_root_gb}G root-disk floor"
if mountpoint -q "$data_mount"; then
  data_source=$(findmnt -n -o SOURCE --target "$data_mount")
  root_source=$(findmnt -n -o SOURCE --target /)
  [[ -n $data_source && $data_source != "$root_source" ]] || die "$data_mount is not on a separate filesystem"
  data_gb=$(size_gb "$data_mount")
  (( data_gb >= min_data_gb )) || die "$data_mount is ${data_gb}G; expected at least ${min_data_gb}G"
  docker_root=/data/docker
  say "storage: / ${root_gb}G; $data_mount ${data_gb}G on $data_source; will use $docker_root"
else
  (( root_gb >= min_data_gb )) ||
    die "no separate $data_mount mount and root is only ${root_gb}G; provide at least ${min_data_gb}G usable Docker storage"
  docker_root=/var/lib/docker
  say "storage: / ${root_gb}G; no separate $data_mount; will retain Docker on root"
fi

command -v docker >/dev/null || die 'Docker Engine missing; install a VSS-supported version before bootstrap'
command -v python3 >/dev/null || die 'python3 missing'
docker info >/dev/null || die 'Docker daemon is unavailable to this user; check service and docker-group membership'
docker_version=$(docker version --format '{{.Server.Version}}')
docker_version=${docker_version%%[-+]*}
[[ $docker_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "unparseable Docker version: $docker_version"
cgroup_driver=$(docker info --format '{{.CgroupDriver}}')
docker_compatible=true
if [[ $(printf '%s\n%s\n' 28.3.3 "$docker_version" | sort -V | head -n 1) != 28.3.3 ]] ||
   [[ $(printf '%s\n%s\n' "$docker_version" 29.5.0 | sort -V | head -n 1) != "$docker_version" ]] ||
   [[ $docker_version == 29.5.0 || $cgroup_driver != cgroupfs ]]; then
  docker_compatible=false
  [[ $repair_docker == true ]] || die "Docker $docker_version / $cgroup_driver is outside VSS's [28.3.3,29.5.0) / cgroupfs requirement; use --repair-docker on a fresh worker"
  say "Docker $docker_version / $cgroup_driver needs repair"
fi
current_root=$(docker info --format '{{.DockerRootDir}}')
[[ $current_root == /var/lib/docker || $current_root == "$docker_root" ]] ||
  die "Docker uses unexpected data root $current_root; refusing to overwrite its configuration"
if [[ $docker_root == /data/docker && $current_root != "$docker_root" ]]; then
  [[ -z $(docker ps -aq) && -z $(docker images -q) && -z $(docker volume ls -q) ]] ||
    die 'Docker already has containers, images or volumes; this fresh-worker script will not migrate or hide them'
  [[ ! -d $docker_root || -z $(find "$docker_root" -mindepth 1 -print -quit) ]] ||
    die "$docker_root is not empty; refusing to replace existing Docker state"
fi
command -v nvidia-ctk >/dev/null || die 'NVIDIA Container Toolkit missing; install it before bootstrap'
toolkit_version=$(nvidia-ctk --version | sed -nE 's/.*version[[:space:]]+v?([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -n 1)
[[ -n $toolkit_version ]] || die 'could not read NVIDIA Container Toolkit version'
[[ $(printf '%s\n%s\n' 1.17.8 "$toolkit_version" | sort -V | head -n 1) == 1.17.8 ]] ||
  die "NVIDIA Container Toolkit $toolkit_version is below VSS minimum 1.17.8"
docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"' ||
  die 'Docker NVIDIA runtime is missing; configure the Container Toolkit before bootstrap'
say "Docker $docker_version root: $current_root; NVIDIA Container Toolkit $toolkit_version runtime present"

if [[ $mode == check ]]; then
  if [[ $docker_compatible == false ]]; then
    say 'would pin Docker packages and set cgroupfs; verify package transaction and worker idleness before --apply'
  fi
  if [[ $current_root == "$docker_root" ]]; then
    say 'Docker data-root already configured'
  else
    say "would set Docker data-root to $docker_root and require $data_mount before Docker starts"
  fi
  if command -v uv >/dev/null || [[ -x $HOME/.local/bin/uv ]]; then
    say 'uv present'
  else
    say 'would install uv for the worker-side verifier runtime'
  fi
  say 'check complete; no changes made'
  exit 0
fi

sudo -n true || die 'passwordless sudo is required for non-interactive bootstrap'
if [[ $docker_compatible == false ]]; then
  [[ -z $(docker ps -aq) && -z $(docker images -q) && -z $(docker volume ls -q) ]] ||
    die 'Docker repair requires a fresh worker with no containers, images or volumes'
  pin_script=$(dirname -- "${BASH_SOURCE[0]}")/pin_docker_version.sh
  if [[ ! -f $pin_script ]]; then
    pin_script=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../deploy/docker/scripts" && pwd)/pin_docker_version.sh
  fi
  [[ -f $pin_script ]] || die "Docker repair script missing: $pin_script (copy the VSS checkout or stage the companion script)"
  say "repairing Docker with $pin_script; packages may downgrade and Docker will restart"
  bash "$pin_script"
  docker_version=$(docker version --format '{{.Server.Version}}')
  docker_version=${docker_version%%[-+]*}
  cgroup_driver=$(docker info --format '{{.CgroupDriver}}')
  [[ $(printf '%s\n%s\n' 28.3.3 "$docker_version" | sort -V | head -n 1) == 28.3.3 ]] || die "Docker repair left unsupported version $docker_version"
  [[ $(printf '%s\n%s\n' "$docker_version" 29.5.0 | sort -V | head -n 1) == "$docker_version" && $docker_version != 29.5.0 ]] || die "Docker repair left unsupported version $docker_version"
  [[ $cgroup_driver == cgroupfs ]] || die "Docker repair left cgroup driver $cgroup_driver"
  say "Docker repaired: $docker_version / $cgroup_driver"
fi
if ! command -v uv >/dev/null && [[ ! -x $HOME/.local/bin/uv ]]; then
  command -v curl >/dev/null || die 'curl missing; needed for the official uv installer'
  say 'installing uv for the normal user'
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || die 'uv installation did not place uv on PATH'
if [[ $docker_root == /var/lib/docker ]]; then
  say 'large root disk is adequate; leaving Docker configuration unchanged'
  exit 0
fi

dropin_dir=/etc/systemd/system/docker.service.d
dropin=$dropin_dir/20-vss-data-mount.conf
expected_dropin='[Unit]
RequiresMountsFor=/data'
if sudo test -e "$dropin"; then
  actual_dropin=$(sudo cat "$dropin")
  [[ $actual_dropin == "$expected_dropin" ]] || die "$dropin exists with different content"
fi
if [[ $current_root == "$docker_root" ]] && sudo test -e "$dropin"; then
  say 'already configured; no Docker restart needed'
  exit 0
fi
[[ -z $(docker ps -q) ]] || die 'running containers detected; refusing to restart Docker'

sudo install -d -m 0755 "$docker_root" "$dropin_dir" /etc/docker
backup="/etc/docker/daemon.json.vss-bootstrap-$(date -u +%Y%m%dT%H%M%SZ)"
had_daemon_json=false
had_dropin=false
if sudo test -e /etc/docker/daemon.json; then
  sudo cp -a /etc/docker/daemon.json "$backup"
  had_daemon_json=true
fi
if sudo test -e "$dropin"; then
  had_dropin=true
fi
rollback=true
restore_on_error() {
  if [[ $rollback == true ]]; then
    say "configuration failed; restoring prior Docker config (backup: $backup)"
    if [[ $had_daemon_json == true ]]; then
      sudo cp -a "$backup" /etc/docker/daemon.json
    else
      sudo rm -f /etc/docker/daemon.json
    fi
    if [[ $had_dropin == false ]]; then
      sudo rm -f "$dropin"
    fi
    sudo systemctl daemon-reload
    sudo systemctl start docker.socket docker.service
  fi
}
trap restore_on_error EXIT
say 'stopping Docker on the fresh worker to update its data root'
sudo systemctl stop docker.service docker.socket

# Merge only data-root; preserve NVIDIA runtime, MTU and other image settings.
sudo python3 - "$docker_root" <<'PY'
import json
import os
import sys
import tempfile

path = '/etc/docker/daemon.json'
if os.path.exists(path):
    with open(path, encoding='utf-8') as src:
        raw = src.read()
else:
    raw = ''
config = json.loads(raw) if raw.strip() else {}
if not isinstance(config, dict):
    raise ValueError('Docker daemon.json must be an object')
config['data-root'] = sys.argv[1]
fd, tmp = tempfile.mkstemp(prefix='.daemon-vss-', dir='/etc/docker')
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(config, out, indent=2, sort_keys=True)
        out.write('\n')
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
printf '%s\n' "$expected_dropin" | sudo tee "$dropin" >/dev/null
sudo chmod 0644 "$dropin"
sudo systemctl daemon-reload
sudo systemctl start docker.socket docker.service
docker info >/dev/null
[[ $(docker info --format '{{.DockerRootDir}}') == "$docker_root" ]] || die 'Docker started with the wrong data root'
rollback=false
trap - EXIT
say "ready: Docker root $docker_root; prior daemon config backup $backup (if one existed)"
say 'the eval harness creates /logs, /tests, /solution and /skills per trial; no persistent copies are needed'
