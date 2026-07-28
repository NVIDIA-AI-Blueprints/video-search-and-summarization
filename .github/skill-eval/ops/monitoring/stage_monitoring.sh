#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Stage Telegraf on all eight coordinators. Runner services are untouched.
set -euo pipefail

apply=false
env_file=""
usage() {
    echo "Usage: $0 --env-file PATH [--apply]" >&2
}
while (($#)); do
    case "$1" in
        --env-file) env_file="${2:?missing environment file}"; shift 2 ;;
        --apply) apply=true; shift ;;
        *) usage; exit 2 ;;
    esac
done
[[ -f "$env_file" ]] || { usage; exit 2; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
installer="$script_dir/install_monitoring_agent.sh"
config="$script_dir/telegraf-skill-eval.conf"
hosts=()
for index in $(seq 1 8); do
    hosts+=("vss-skill-validator-distributed-${index}")
done

for host in "${hosts[@]}"; do
    brev exec "$host" "true" >/dev/null
    echo "READY: $host"
done
if [[ "$apply" != true ]]; then
    echo "Preflight only. Re-run with --apply to start Telegraf on all eight hosts."
    exit 0
fi

remote_installer="/tmp/install-vss-monitoring.sh"
remote_config="/tmp/telegraf-skill-eval.conf"
remote_env="/tmp/.vss-monitoring.env"
for host in "${hosts[@]}"; do
    brev copy "$installer" "${host}:${remote_installer}"
    brev copy "$config" "${host}:${remote_config}"
    brev copy "$env_file" "${host}:${remote_env}"
    brev exec "$host" \
        "chmod 700 '$remote_installer' && chmod 600 '$remote_env' && '$remote_installer' --coordinator-id '$host' --env-file '$remote_env' --config-file '$remote_config'; rc=\$?; rm -f '$remote_env' '$remote_config' '$remote_installer'; exit \$rc"
    brev exec "$host" "systemctl is-active --quiet telegraf.service"
done
echo "Telegraf is active on all eight coordinators. Runner services were not changed."

