#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

payload_dir="${1:-}"
[[ -d "$payload_dir" ]] || {
    echo "Usage: $0 PAYLOAD_DIR" >&2
    exit 2
}
for file in ca.crt hosts.entries wireguard-address wireguard-peers.conf; do
    [[ -f "$payload_dir/$file" ]] || { echo "Missing payload: $file" >&2; exit 1; }
done

address="$(<"$payload_dir/wireguard-address")"
[[ "$address" =~ ^10\.203\.142\.(10[0-9]|1[1-9][0-9]|2[0-4][0-9]|25[0-4])/32$ ]] || {
    echo "Invalid client overlay address: $address" >&2
    exit 2
}

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates wireguard-tools
sudo install -d -m 0700 -o root -g root /etc/wireguard
if ! sudo test -f /etc/wireguard/vss-skill-eval.key; then
    wg genkey | sudo tee /etc/wireguard/vss-skill-eval.key >/dev/null
fi
sudo chmod 0600 /etc/wireguard/vss-skill-eval.key

config_tmp="$(mktemp)"
hosts_tmp="$(mktemp)"
trap 'rm -f "$config_tmp" "$hosts_tmp"' EXIT
{
    echo "[Interface]"
    echo "Address = $address"
    printf 'PrivateKey = '
    sudo cat /etc/wireguard/vss-skill-eval.key
    echo "ListenPort = 51821"
    echo
    cat "$payload_dir/wireguard-peers.conf"
} >"$config_tmp"
sudo install -m 0600 -o root -g root "$config_tmp" /etc/wireguard/wg-vss.conf

awk '
    $0 == "# BEGIN VSS POSTGRES HA" {skip=1; next}
    $0 == "# END VSS POSTGRES HA" {skip=0; next}
    !skip {print}
' /etc/hosts >"$hosts_tmp"
{
    cat "$hosts_tmp"
    echo "# BEGIN VSS POSTGRES HA"
    cat "$payload_dir/hosts.entries"
    echo "# END VSS POSTGRES HA"
} >"$config_tmp"
sudo install -m 0644 -o root -g root "$config_tmp" /etc/hosts

sudo install -d -m 0755 -o root -g root /etc/vss-postgres-ha
sudo install -m 0644 -o root -g root \
    "$payload_dir/ca.crt" /etc/vss-postgres-ha/ca.crt
sudo install -m 0644 -o root -g root \
    "$payload_dir/ca.crt" \
    /usr/local/share/ca-certificates/vss-postgres-ha.crt
sudo update-ca-certificates >/dev/null

sudo ufw allow in on wg-vss comment 'VSS PostgreSQL HA overlay' >/dev/null
sudo systemctl enable wg-quick@wg-vss.service >/dev/null
sudo systemctl restart wg-quick@wg-vss.service
[[ "$(ip -4 -o address show dev wg-vss | awk '{print $4}')" == "$address" ]]
echo "WireGuard database client active: $address"
