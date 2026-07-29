#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy the PostgreSQL-validating fence to enabled dedicated GPU workers.
# Default is a read-only preflight; pass --apply to install and start services.
set -euo pipefail

apply=false
confirm_drained=false
for argument in "$@"; do
    case "$argument" in
        --apply) apply=true ;;
        --confirm-drained) confirm_drained=true ;;
        *)
            echo "Unknown argument: $argument" >&2
            exit 2
            ;;
    esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
installer="$script_dir/install-gpu-fence-worker.sh"
fence_module="$script_dir/../gpu_fence.py"
unit_file="$script_dir/vss-gpu-fence.service"
database_url_file="${GPU_FENCE_DATABASE_URL_FILE:-}"

for required in "$installer" "$fence_module" "$unit_file"; do
    [[ -f "$required" ]] || {
        echo "Missing deployment input: $required" >&2
        exit 1
    }
done
for command in ssh tar; do
    command -v "$command" >/dev/null || {
        echo "Missing GPU fence deployment prerequisite: $command" >&2
        exit 1
    }
done

workers_text="${GPU_WORKERS:-}"
[[ -n "$workers_text" ]] || {
    echo "Set GPU_WORKERS to the reviewed enabled-worker inventory" >&2
    exit 2
}
read -r -a workers <<<"$(tr '\n,' '  ' <<<"$workers_text")"
[[ ${#workers[@]} -gt 0 ]] || {
    echo "No enabled GPU workers selected" >&2
    exit 2
}

validate_worker() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]
}

remote_exec() {
    local worker="$1"
    shift
    ssh \
        -o BatchMode=yes \
        -o ConnectTimeout=15 \
        -o ControlMaster=no \
        -o ControlPath=none \
        "$worker" "$@"
}

echo "Preflighting ${#workers[@]} GPU workers..."
for worker in "${workers[@]}"; do
    validate_worker "$worker" || {
        echo "Invalid GPU worker name: $worker" >&2
        exit 2
    }
    remote_exec "$worker" "sudo -n true" >/dev/null
    echo "READY: $worker"
done

if [[ "$apply" != true ]]; then
    echo "Preflight only. Re-run with --apply --confirm-drained after draining leases."
    exit 0
fi
[[ "$confirm_drained" == true ]] || {
    echo "--confirm-drained is required because service startup fences existing work" >&2
    exit 2
}

[[ -f "$database_url_file" ]] || {
    echo "Set GPU_FENCE_DATABASE_URL_FILE to a mode-0600 DSN file" >&2
    exit 2
}
permissions="$(stat -c '%a' "$database_url_file")"
[[ "$permissions" == "600" ]] || {
    echo "GPU_FENCE_DATABASE_URL_FILE must have mode 0600" >&2
    exit 2
}

payload="$(mktemp -d)"
archive="$(mktemp --suffix=.tar.gz)"
remote_dir="/run/vss-gpu-fence-install"
remote_archive="$remote_dir/payload.tar.gz"
remote_pending_worker=""
cleanup() {
    rm -rf "$payload" "$archive"
    if [[ -n "$remote_pending_worker" ]]; then
        remote_exec "$remote_pending_worker" \
            "sudo rm -rf '$remote_dir'" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT HUP INT TERM
install -m 0700 "$installer" "$payload/install-gpu-fence-worker.sh"
install -m 0700 "$fence_module" "$payload/gpu_fence.py"
install -m 0600 "$unit_file" "$payload/vss-gpu-fence.service"
install -m 0600 "$database_url_file" "$payload/database-url"
tar -C "$payload" -czf "$archive" .

for worker in "${workers[@]}"; do
    remote_pending_worker="$worker"
    remote_exec "$worker" \
        "sudo rm -rf '$remote_dir' && sudo install -d -m 700 -o root -g root '$remote_dir' && sudo tee '$remote_archive' >/dev/null && sudo chmod 600 '$remote_archive'" \
        <"$archive"
    if ! remote_exec "$worker" \
        "sudo bash -s -- '$remote_dir' '$worker'" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
gpu_id="$2"
eval_user="${SUDO_USER:?sudo did not preserve the SSH user identity}"
trap 'rm -rf "$remote_dir"' EXIT HUP INT TERM
tar --no-same-owner -xzf "$remote_dir/payload.tar.gz" -C "$remote_dir"
rm -f "$remote_dir/payload.tar.gz"
chmod 700 "$remote_dir" \
    "$remote_dir/install-gpu-fence-worker.sh" \
    "$remote_dir/gpu_fence.py"
chmod 600 \
    "$remote_dir/vss-gpu-fence.service" \
    "$remote_dir/database-url"
bash "$remote_dir/install-gpu-fence-worker.sh" \
    --gpu-id "$gpu_id" \
    --database-url-file "$remote_dir/database-url" \
    --eval-user "$eval_user" \
    --confirm-drained
REMOTE
    then
        echo "GPU fence install failed on $worker" >&2
        exit 1
    fi
    remote_pending_worker=""
done

cleanup
trap - EXIT HUP INT TERM
for worker in "${workers[@]}"; do
    remote_exec "$worker" \
        "sudo systemctl is-active --quiet vss-gpu-fence.service && sudo /usr/local/bin/vss-gpu-fence status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"version\"] == \"1\" and d[\"gpu_id\"] == \"$worker\" and not d[\"blocked\"]'"
done
echo "Verified GPU fencing on ${#workers[@]} workers."
