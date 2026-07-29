#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Install and start Telegraf monitoring without changing runner services.
set -euo pipefail

coordinator_id=""
env_file=""
config_file="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/telegraf-skill-eval.conf"
health_probe_file="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/ha_health_probe.sh"

usage() {
    echo "Usage: $0 --coordinator-id NAME --env-file PATH [--config-file PATH] [--health-probe-file PATH]" >&2
}

while (($#)); do
    case "$1" in
        --coordinator-id) coordinator_id="${2:?missing coordinator ID}"; shift 2 ;;
        --env-file) env_file="${2:?missing environment file}"; shift 2 ;;
        --config-file) config_file="${2:?missing config file}"; shift 2 ;;
        --health-probe-file) health_probe_file="${2:?missing health probe}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

case "$coordinator_id" in
    vss-skill-validator-distributed-[1-8]) ;;
    *) echo "Invalid coordinator ID: $coordinator_id" >&2; exit 2 ;;
esac
[[ -f "$env_file" ]] || { echo "Missing environment file: $env_file" >&2; exit 2; }
[[ -f "$config_file" ]] || { echo "Missing Telegraf config: $config_file" >&2; exit 2; }
[[ -f "$health_probe_file" ]] || { echo "Missing health probe: $health_probe_file" >&2; exit 2; }

if ! command -v telegraf >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq telegraf
fi

runtime_env="$(mktemp)"
trap 'rm -f "$runtime_env"' EXIT
python3 - "$env_file" "$coordinator_id" >"$runtime_env" <<'PY'
import pathlib
import re
import shlex
import sys

required = {"INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET"}
values = {}
for line_number, raw_line in enumerate(
    pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(),
    start=1,
):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
    if not match:
        raise SystemExit(f"unsupported monitoring environment syntax: line {line_number}")
    name, raw_value = match.groups()
    if name not in required:
        raise SystemExit(f"unsupported monitoring environment variable: {name}")
    if name in values:
        raise SystemExit(f"duplicate monitoring environment variable: {name}")
    parsed = shlex.split(raw_value, comments=False, posix=True)
    if len(parsed) != 1 or not parsed[0]:
        raise SystemExit(f"monitoring environment value is missing or invalid: {name}")
    values[name] = parsed[0]
missing = sorted(required - values.keys())
if missing:
    raise SystemExit("missing monitoring environment values: " + ", ".join(missing))
print(f"COORDINATOR_ID={shlex.quote(sys.argv[2])}")
for name in sorted(values):
    print(f"{name}={shlex.quote(values[name])}")
PY
chmod 600 "$runtime_env"

sudo install -o root -g telegraf -m 0640 \
    "$runtime_env" /etc/telegraf/vss-skill-eval.env
sudo install -o root -g root -m 0644 \
    "$config_file" /etc/telegraf/telegraf.d/vss-skill-eval.conf
sudo install -d -o root -g root -m 0755 /usr/local/libexec/vss-skill-eval
sudo install -o root -g root -m 0755 \
    "$health_probe_file" /usr/local/libexec/vss-skill-eval/ha-health-probe.sh
printf '%s\n' \
    'telegraf ALL=(root) NOPASSWD: /usr/local/libexec/vss-skill-eval/ha-health-probe.sh' \
    | sudo tee /etc/sudoers.d/vss-skill-eval-monitoring >/dev/null
sudo chmod 0440 /etc/sudoers.d/vss-skill-eval-monitoring
sudo visudo -cf /etc/sudoers.d/vss-skill-eval-monitoring >/dev/null
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
