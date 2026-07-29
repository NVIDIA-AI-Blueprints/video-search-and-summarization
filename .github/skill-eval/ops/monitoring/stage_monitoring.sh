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
[[ "$(stat -c '%a' "$env_file")" == "600" ]] || {
    echo "Monitoring environment file must have mode 0600" >&2
    exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
installer="$script_dir/install_monitoring_agent.sh"
config="$script_dir/telegraf-skill-eval.conf"
health_probe="$script_dir/ha_health_probe.sh"
for command in ssh tar; do
    command -v "$command" >/dev/null || {
        echo "Missing monitoring deployment prerequisite: $command" >&2
        exit 1
    }
done
hosts=()
for index in $(seq 1 8); do
    hosts+=("vss-skill-validator-distributed-${index}")
done

for host in "${hosts[@]}"; do
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$host" "true"
    echo "READY: $host"
done
if [[ "$apply" != true ]]; then
    echo "Preflight only. Re-run with --apply to start Telegraf on all eight hosts."
    exit 0
fi

payload="$(mktemp -d)"
archive="$(mktemp --suffix=.tar.gz)"
remote_dir="/run/vss-monitoring-install"
remote_archive="$remote_dir/payload.tar.gz"
remote_pending_host=""
cleanup_local() {
    rm -rf "$payload" "$archive"
    if [[ -n "$remote_pending_host" ]]; then
        ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
            -o ConnectTimeout=5 \
            "$remote_pending_host" \
            "sudo rm -rf '$remote_dir'" >/dev/null 2>&1 || true
    fi
}
trap cleanup_local EXIT HUP INT TERM
install -m 0700 "$installer" "$payload/install-vss-monitoring.sh"
install -m 0644 "$config" "$payload/telegraf-skill-eval.conf"
install -m 0755 "$health_probe" "$payload/vss-ha-health-probe.sh"
install -m 0600 "$env_file" "$payload/monitoring.env"
tar -C "$payload" -czf "$archive" .

for host in "${hosts[@]}"; do
    remote_pending_host="$host"
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$host" \
        "sudo rm -rf '$remote_dir' && sudo install -d -m 700 -o root -g root '$remote_dir' && sudo tee '$remote_archive' >/dev/null && sudo chmod 600 '$remote_archive'" \
        <"$archive"
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$host" \
        "sudo bash -s -- '$remote_dir' '$host'" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
coordinator_id="$2"
trap 'rm -rf "$remote_dir"' EXIT HUP INT TERM
tar --no-same-owner -xzf "$remote_dir/payload.tar.gz" -C "$remote_dir"
rm -f "$remote_dir/payload.tar.gz"
chmod 700 "$remote_dir" "$remote_dir/install-vss-monitoring.sh"
chmod 600 "$remote_dir/monitoring.env"
bash "$remote_dir/install-vss-monitoring.sh" \
    --coordinator-id "$coordinator_id" \
    --env-file "$remote_dir/monitoring.env" \
    --config-file "$remote_dir/telegraf-skill-eval.conf" \
    --health-probe-file "$remote_dir/vss-ha-health-probe.sh"
systemctl is-active --quiet telegraf.service
REMOTE
    remote_pending_host=""
done
echo "Telegraf is active on all eight coordinators. Runner services were not changed."
