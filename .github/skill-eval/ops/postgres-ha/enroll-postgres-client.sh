#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Enroll one GPU worker into the database WireGuard overlay without restarting
# any database-side interface.
set -euo pipefail
umask 077

worker=""
address_octet=""
registered=false
apply=false
rotate_key=false
state_dir="${POSTGRES_HA_STATE_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/vss-skill-eval/postgres-ha}"

usage() {
    echo "Usage: $0 --worker NAME --address 100..254 [--registered] [--apply] [--rotate-key]" >&2
}

while (($#)); do
    case "$1" in
        --worker) worker="${2:-}"; shift 2 ;;
        --address) address_octet="${2:-}"; shift 2 ;;
        --registered) registered=true; shift ;;
        --apply) apply=true; shift ;;
        --rotate-key) rotate_key=true; shift ;;
        *) usage; exit 2 ;;
    esac
done

[[ "$worker" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || { usage; exit 2; }
[[ "$address_octet" =~ ^(10[0-9]|1[1-9][0-9]|2[0-4][0-9]|25[0-4])$ ]] || {
    usage
    exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
prepare_script="$script_dir/prepare-wireguard-key.sh"
install_script="$script_dir/install-wireguard-client.sh"
ca_cert="$state_dir/secrets/ca.crt"
hosts_entries="$state_dir/bundle/hosts.entries"
for required in "$prepare_script" "$install_script" "$ca_cert" "$hosts_entries"; do
    [[ -f "$required" ]] || { echo "Missing deployment input: $required" >&2; exit 1; }
done

remote_exec() {
    if [[ "$registered" == true ]]; then
        ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$worker" "$@"
    else
        brev exec "$worker" "$@"
    fi
}

remote_copy() {
    local source="$1"
    local destination="$2"
    if [[ "$registered" == true ]]; then
        scp -q \
            -o BatchMode=yes \
            -o ControlMaster=no \
            -o ControlPath=none \
            "$source" "${worker}:${destination}"
    else
        brev copy "$source" "${worker}:${destination}"
    fi
}

remote_exec "true" >/dev/null
for index in $(seq 1 3); do
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
        "vss-skill-validator-distributed-${index}" \
        "systemctl is-active --quiet etcd.service vss-postgres-ha.service"
done
echo "READY: $worker -> 10.203.142.${address_octet}"

if [[ "$apply" != true ]]; then
    [[ "$rotate_key" != true ]] || {
        echo "--rotate-key requires --apply" >&2
        exit 2
    }
    echo "Preflight only. Re-run with --apply."
    exit 0
fi

remote_dir="/tmp/vss-postgres-client-install"
remote_exec "rm -rf '$remote_dir' && mkdir -m 700 '$remote_dir'"
if [[ "$rotate_key" == true ]]; then
    remote_exec "sudo rm -f /etc/wireguard/vss-skill-eval.key"
fi
remote_copy "$prepare_script" "$remote_dir/prepare-wireguard-key.sh"
key_output="$(
    remote_exec \
        "chmod 700 '$remote_dir/prepare-wireguard-key.sh' && '$remote_dir/prepare-wireguard-key.sh'"
)"
public_key="$(
    awk 'length($0) == 44 && $0 ~ /=$/ {value=$0} END {print value}' \
        <<<"$key_output"
)"
[[ "$public_key" =~ ^[A-Za-z0-9+/]{43}=$ ]] || {
    remote_exec "rm -rf '$remote_dir'" || true
    echo "Worker returned an invalid WireGuard public key" >&2
    exit 1
}

client_state_dir="$state_dir/clients"
payload="$state_dir/bundle/client-${address_octet}"
install -d -m 0700 "$client_state_dir"
rm -rf "$payload"
install -d -m 0700 "$payload"
install -m 0644 "$ca_cert" "$payload/ca.crt"
install -m 0644 "$hosts_entries" "$payload/hosts.entries"
printf '10.203.142.%s/32\n' "$address_octet" >"$payload/wireguard-address"
: >"$payload/wireguard-peers.conf"

for index in $(seq 1 3); do
    IFS=$'\t' read -r inventory_index _ public_ip database_public_key \
        <"$state_dir/inventory/node-${index}.tsv"
    [[ "$inventory_index" == "$index" ]] || { echo "Invalid node inventory" >&2; exit 1; }
    cat >>"$payload/wireguard-peers.conf" <<EOF
# vss-pg-${index}
[Peer]
PublicKey = ${database_public_key}
AllowedIPs = 10.203.142.${index}/32
Endpoint = ${public_ip}:51821
PersistentKeepalive = 25

EOF
done

for index in $(seq 1 3); do
    database_host="vss-skill-validator-distributed-${index}"
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
        "$database_host" \
        "sudo python3 - '$worker' '$public_key' '10.203.142.${address_octet}/32' <<'PY'
import os
import pathlib
import re
import subprocess
import sys
import tempfile

name, public_key, address = sys.argv[1:]
path = pathlib.Path('/etc/wireguard/wg-vss.conf')
text = path.read_text(encoding='utf-8')
start = f'# BEGIN VSS CLIENT {name}'
end = f'# END VSS CLIENT {name}'
pattern = re.compile(
    rf'(?ms)^{re.escape(start)}\\n.*?^{re.escape(end)}\\n?'
)
for block in re.finditer(
    r'(?ms)^# BEGIN VSS CLIENT ([A-Za-z0-9_.-]+)\\n'
    r'.*?^# END VSS CLIENT \\1\\n?',
    text,
):
    other_name = block.group(1)
    if other_name == name:
        continue
    other_text = block.group(0)
    if re.search(rf'^AllowedIPs = {re.escape(address)}$', other_text, re.MULTILINE):
        raise SystemExit(
            f'overlay address {address} is already assigned to {other_name}'
        )
    if re.search(
        rf'^PublicKey = {re.escape(public_key)}$', other_text, re.MULTILINE
    ):
        raise SystemExit(
            f'WireGuard public key is already assigned to {other_name}'
        )
old_match = pattern.search(text)
if old_match:
    old_key = re.search(r'^PublicKey = (\\S+)$', old_match.group(0), re.MULTILINE)
    if old_key and old_key.group(1) != public_key:
        subprocess.run(['wg', 'set', 'wg-vss', 'peer', old_key.group(1), 'remove'], check=True)
    text = pattern.sub('', text).rstrip() + '\\n'
block = (
    f'\\n{start}\\n[Peer]\\nPublicKey = {public_key}\\n'
    f'AllowedIPs = {address}\\n{end}\\n'
)
fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.wg-vss.')
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write(text + block)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
subprocess.run(
    ['wg', 'set', 'wg-vss', 'peer', public_key, 'allowed-ips', address],
    check=True,
)
subprocess.run(
    ['ip', 'route', 'replace', address, 'dev', 'wg-vss'],
    check=True,
)
PY"
done

for file in ca.crt hosts.entries wireguard-address wireguard-peers.conf; do
    remote_copy "$payload/$file" "$remote_dir/$file"
done
remote_copy "$install_script" "$remote_dir/install-wireguard-client.sh"
if remote_exec \
    "chmod 700 '$remote_dir/install-wireguard-client.sh' && '$remote_dir/install-wireguard-client.sh' '$remote_dir' && for database in 10.203.142.1 10.203.142.2 10.203.142.3; do ping -c1 -W5 \"\$database\" >/dev/null; done"; then
    remote_exec "rm -rf '$remote_dir'"
else
    remote_exec "rm -rf '$remote_dir'" || true
    exit 1
fi

printf '%s\t%s\t%s\t%s\n' \
    "$worker" "$address_octet" "$public_key" "$registered" \
    >"$client_state_dir/${worker}.tsv"
chmod 0600 "$client_state_dir/${worker}.tsv"
echo "ENROLLED: $worker=10.203.142.${address_octet}"
