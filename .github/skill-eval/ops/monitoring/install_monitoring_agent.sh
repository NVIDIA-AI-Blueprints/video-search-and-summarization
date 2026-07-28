#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Install and start Telegraf monitoring without changing runner services.
set -euo pipefail

coordinator_id=""
env_file=""
config_file="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/telegraf-skill-eval.conf"

usage() {
    echo "Usage: $0 --coordinator-id NAME --env-file PATH [--config-file PATH]" >&2
}

while (($#)); do
    case "$1" in
        --coordinator-id) coordinator_id="${2:?missing coordinator ID}"; shift 2 ;;
        --env-file) env_file="${2:?missing environment file}"; shift 2 ;;
        --config-file) config_file="${2:?missing config file}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

case "$coordinator_id" in
    vss-skill-validator-distributed-[1-8]) ;;
    *) echo "Invalid coordinator ID: $coordinator_id" >&2; exit 2 ;;
esac
[[ -f "$env_file" ]] || { echo "Missing environment file: $env_file" >&2; exit 2; }
[[ -f "$config_file" ]] || { echo "Missing Telegraf config: $config_file" >&2; exit 2; }

for key in INFLUX_URL INFLUX_TOKEN INFLUX_ORG INFLUX_BUCKET; do
    if ! grep -Eq "^${key}=.+" "$env_file"; then
        echo "Missing ${key} in ${env_file}" >&2
        exit 2
    fi
done

if ! command -v telegraf >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq telegraf
fi

runtime_env="$(mktemp)"
trap 'rm -f "$runtime_env"' EXIT
{
    printf 'COORDINATOR_ID=%s\n' "$coordinator_id"
    grep -E '^(INFLUX_URL|INFLUX_TOKEN|INFLUX_ORG|INFLUX_BUCKET)=' "$env_file"
} >"$runtime_env"
chmod 600 "$runtime_env"

sudo install -o root -g telegraf -m 0640 \
    "$runtime_env" /etc/telegraf/vss-skill-eval.env
sudo install -o root -g root -m 0644 \
    "$config_file" /etc/telegraf/telegraf.d/vss-skill-eval.conf
sudo mkdir -p /etc/systemd/system/telegraf.service.d
printf '%s\n' \
    '[Service]' \
    'EnvironmentFile=/etc/telegraf/vss-skill-eval.env' \
    'ExecStart=' \
    'ExecStart=/usr/bin/telegraf --config /etc/telegraf/telegraf.d/vss-skill-eval.conf' \
    | sudo tee /etc/systemd/system/telegraf.service.d/vss-skill-eval.conf >/dev/null

set -a
# shellcheck disable=SC1090
source "$runtime_env"
set +a
telegraf \
    --config /etc/telegraf/telegraf.d/vss-skill-eval.conf \
    --test --test-wait 5 >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now telegraf.service
sudo systemctl is-active --quiet telegraf.service
echo "Monitoring active: ${coordinator_id}"

