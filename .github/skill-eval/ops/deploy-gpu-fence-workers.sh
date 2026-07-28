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
command -v brev >/dev/null

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

declare -A registered=()
for worker in ${REGISTERED_GPU_WORKERS:-}; do
    registered["${worker,,}"]=1
done

validate_worker() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]
}

remote_exec() {
    local worker="$1"
    shift
    if [[ -n "${registered[${worker,,}]:-}" ]]; then
        ssh -o BatchMode=yes -o ConnectTimeout=15 "${worker,,}" "$@"
    else
        brev exec "$worker" "$@"
    fi
}

remote_copy() {
    local source="$1"
    local worker="$2"
    local destination="$3"
    if [[ -n "${registered[${worker,,}]:-}" ]]; then
        scp -q "$source" "${worker,,}:$destination"
    else
        brev copy "$source" "${worker}:$destination"
    fi
}

echo "Preflighting ${#workers[@]} GPU workers..."
for worker in "${workers[@]}"; do
    validate_worker "$worker" || {
        echo "Invalid GPU worker name: $worker" >&2
        exit 2
    }
    remote_exec "$worker" "true" >/dev/null
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

for worker in "${workers[@]}"; do
    remote_dir="/tmp/vss-gpu-fence-install"
    remote_exec "$worker" "rm -rf '$remote_dir' && mkdir -m 700 '$remote_dir'"
    remote_copy "$installer" "$worker" "$remote_dir/install-gpu-fence-worker.sh"
    remote_copy "$fence_module" "$worker" "$remote_dir/gpu_fence.py"
    remote_copy "$unit_file" "$worker" "$remote_dir/vss-gpu-fence.service"
    remote_copy "$database_url_file" "$worker" "$remote_dir/database-url"
    if ! remote_exec "$worker" \
        "chmod 700 '$remote_dir/install-gpu-fence-worker.sh' && chmod 600 '$remote_dir/database-url' && '$remote_dir/install-gpu-fence-worker.sh' --gpu-id '$worker' --database-url-file '$remote_dir/database-url'"; then
        remote_exec "$worker" "rm -rf '$remote_dir'" || true
        echo "GPU fence install failed on $worker" >&2
        exit 1
    fi
    remote_exec "$worker" "rm -rf '$remote_dir'"
done

for worker in "${workers[@]}"; do
    remote_exec "$worker" \
        "sudo systemctl is-active --quiet vss-gpu-fence.service && sudo /usr/local/bin/vss-gpu-fence status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"version\"] == \"1\" and d[\"gpu_id\"] == \"$worker\" and not d[\"blocked\"]'"
done
echo "Verified GPU fencing on ${#workers[@]} workers."
