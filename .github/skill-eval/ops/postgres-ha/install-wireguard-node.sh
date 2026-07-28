#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Install one pre-rendered WireGuard peer configuration. Private keys are
# generated and retained on the peer; only public keys leave the machine.
set -euo pipefail
umask 077

payload_dir=""
node_index=""
coordinator_name=""

usage() {
    echo "Usage: $0 --payload-dir DIR --node-index 1..8 --coordinator-name NAME" >&2
}

while (($#)); do
    case "$1" in
        --payload-dir) payload_dir="${2:-}"; shift 2 ;;
        --node-index) node_index="${2:-}"; shift 2 ;;
        --coordinator-name) coordinator_name="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[[ "$node_index" =~ ^[1-8]$ ]] || { usage; exit 2; }
[[ "$coordinator_name" == "vss-skill-validator-distributed-${node_index}" ]] || {
    echo "Coordinator identity does not match node index" >&2
    exit 2
}
[[ -d "$payload_dir" ]] || { echo "Missing payload directory" >&2; exit 2; }
for file in ca.crt hosts.entries wireguard-address wireguard-peers.conf; do
    [[ -f "$payload_dir/$file" ]] || {
        echo "Missing payload file: $file" >&2
        exit 1
    }
done

wireguard_address="$(<"$payload_dir/wireguard-address")"
[[ "$wireguard_address" == "10.203.142.${node_index}/32" ]] || {
    echo "Unexpected WireGuard address: $wireguard_address" >&2
    exit 2
}

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates \
    wireguard-tools

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
    echo "Address = $wireguard_address"
    printf 'PrivateKey = '
    sudo cat /etc/wireguard/vss-skill-eval.key
    echo "ListenPort = 51821"
    echo
    cat "$payload_dir/wireguard-peers.conf"
} >"$config_tmp"
chmod 0600 "$config_tmp"
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

# WireGuard silently drops unauthenticated packets, so exposing only its UDP
# handshake port is safe. PostgreSQL, etcd, and Patroni listen solely on the
# encrypted overlay and are accepted only through wg-vss.
sudo ufw allow 51821/udp comment 'VSS PostgreSQL HA WireGuard' >/dev/null
sudo ufw allow in on wg-vss comment 'VSS PostgreSQL HA overlay' >/dev/null
sudo systemctl enable wg-quick@wg-vss.service >/dev/null
sudo systemctl restart wg-quick@wg-vss.service

actual_address="$(ip -4 -o address show dev wg-vss | awk '{print $4}')"
[[ "$actual_address" == "$wireguard_address" ]] || {
    echo "WireGuard address mismatch: expected=$wireguard_address actual=$actual_address" >&2
    exit 1
}
echo "WireGuard active: ${coordinator_name}=${wireguard_address}"
