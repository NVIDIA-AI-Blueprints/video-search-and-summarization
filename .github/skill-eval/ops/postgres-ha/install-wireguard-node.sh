#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Install one pre-rendered WireGuard peer configuration. Private keys are
# generated and retained on the peer; only public keys leave the machine.
set -euo pipefail
umask 077

if ((EUID != 0)); then
    exec sudo -- "$0" "$@"
fi

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

command -v flock >/dev/null || {
    echo "flock is required for serialized WireGuard updates" >&2
    exit 1
}
exec 9>/run/vss-wireguard-peer-update.lock
flock -x 9
etcd_was_active=false
patroni_was_active=false
sudo systemctl is-active --quiet etcd.service && etcd_was_active=true
sudo systemctl is-active --quiet vss-postgres-ha.service &&
    patroni_was_active=true
sudo systemctl unmask wg-quick@wg-vss.service >/dev/null
sudo systemctl stop wg-quick@wg-vss.service >/dev/null 2>&1 || true
if ip link show dev wg-vss >/dev/null 2>&1; then
    sudo wg-quick down wg-vss >/dev/null 2>&1 ||
        sudo ip link delete dev wg-vss
fi
sudo systemctl mask wg-quick@wg-vss.service >/dev/null
if ip link show dev wg-vss >/dev/null 2>&1; then
    echo "WireGuard interface remained active after service quiescence" >&2
    exit 1
fi

sudo timeout --kill-after=30s 900s apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive \
    timeout --kill-after=30s 900s apt-get install -y -qq \
    ca-certificates \
    wireguard-tools

sudo install -d -m 0700 -o root -g root /etc/wireguard
if ! sudo test -f /etc/wireguard/vss-skill-eval.key; then
    wg genkey | sudo tee /etc/wireguard/vss-skill-eval.key >/dev/null
fi
sudo chmod 0600 /etc/wireguard/vss-skill-eval.key

config_tmp="$(mktemp)"
hosts_tmp="$(mktemp)"
client_peers_tmp="$(mktemp)"
trap 'rm -f "$config_tmp" "$hosts_tmp" "$client_peers_tmp"' EXIT
if sudo test -f /etc/wireguard/wg-vss.conf; then
    sudo awk '
        /^# BEGIN VSS CLIENT [A-Za-z0-9_.-]+$/ {copy=1}
        copy {print}
        /^# END VSS CLIENT [A-Za-z0-9_.-]+$/ {copy=0}
    ' /etc/wireguard/wg-vss.conf |
        tee "$client_peers_tmp" >/dev/null
fi
{
    echo "[Interface]"
    echo "Address = $wireguard_address"
    printf 'PrivateKey = '
    sudo cat /etc/wireguard/vss-skill-eval.key
    echo "ListenPort = 51821"
    echo
    cat "$payload_dir/wireguard-peers.conf"
    if [[ -s "$client_peers_tmp" ]]; then
        echo
        cat "$client_peers_tmp"
    fi
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

# WireGuard silently drops unauthenticated packets, so expose its handshake
# port publicly. Do not treat every authenticated overlay peer as trusted:
# database clients get PostgreSQL only, and quorum control ports accept only
# the three database peers.
ufw_status="$(sudo ufw status verbose)"
grep -Fxq 'Status: active' <<<"$ufw_status" || {
    echo "UFW must be active before installing the HA overlay" >&2
    exit 1
}
grep -Eq '^Default: deny \(incoming\)' <<<"$ufw_status" || {
    echo "UFW must enforce default-deny incoming traffic" >&2
    exit 1
}
sudo ufw allow 51821/udp comment 'VSS PostgreSQL HA WireGuard' >/dev/null
delete_ufw_rule() {
    local expected="ufw $*"
    for _ in $(seq 1 20); do
        if ! sudo ufw show added |
            awk -v expected="$expected" '
                $0 == expected || index($0, expected " comment ") == 1 {
                    found=1
                }
                END {exit !found}
            '; then
            return 0
        fi
        sudo ufw --force delete "$@" >/dev/null
    done
    echo "Too many duplicate UFW rules matched: $*" >&2
    return 1
}
delete_ufw_rule allow in on wg-vss
delete_ufw_rule deny in on wg-vss
delete_ufw_rule \
    allow in on wg-vss \
    from 10.203.142.0/24 \
    to any port 5432 proto tcp
for database_peer in 1 2 3; do
    for control_port in 2379 2380 8008; do
        delete_ufw_rule \
            allow in on wg-vss \
            from "10.203.142.${database_peer}" \
            to any port "$control_port" proto tcp
    done
done
if ! ufw_rules="$(sudo ufw show added)"; then
    echo "Could not inspect UFW rules after ingress cleanup" >&2
    exit 1
fi
if grep -Eq '^ufw allow in on wg-vss' <<<"$ufw_rules"; then
    echo "Unexpected WireGuard ingress rule remains after cleanup" >&2
    exit 1
fi
if grep -Eq '^ufw deny in on wg-vss' <<<"$ufw_rules"; then
    echo "Unexpected WireGuard terminal deny remains after cleanup" >&2
    exit 1
fi
sudo ufw insert 1 deny in on wg-vss \
    comment 'VSS PostgreSQL HA terminal deny' >/dev/null
if ((node_index <= 3)); then
    sudo ufw insert 1 allow in on wg-vss \
        from 10.203.142.0/24 \
        to any port 5432 proto tcp \
        comment 'VSS PostgreSQL HA clients' >/dev/null
    for database_peer in 1 2 3; do
        for control_port in 2379 2380 8008; do
            sudo ufw insert 1 allow in on wg-vss \
                from "10.203.142.${database_peer}" \
                to any port "$control_port" proto tcp \
                comment 'VSS PostgreSQL HA quorum' >/dev/null
        done
    done
fi
if ! ufw_rules="$(sudo ufw show added)"; then
    echo "Could not inspect final UFW rules" >&2
    exit 1
fi
grep -Fq 'ufw allow 51821/udp' <<<"$ufw_rules" || {
    echo "WireGuard handshake firewall rule is missing" >&2
    exit 1
}
allow_count="$(
    awk '/^ufw allow in on wg-vss/ {count++} END {print count+0}' \
        <<<"$ufw_rules"
)"
deny_count="$(
    awk '/^ufw deny in on wg-vss/ {count++} END {print count+0}' \
        <<<"$ufw_rules"
)"
terminal_position="$(
    awk '
        /^ufw / {position++}
        /^ufw deny in on wg-vss/ {print position; exit}
    ' <<<"$ufw_rules"
)"
[[ "$deny_count" == 1 ]] || {
    echo "WireGuard terminal deny rule is missing or duplicated" >&2
    exit 1
}
if ((node_index <= 3)); then
    [[ "$allow_count" == 10 && "$terminal_position" == 11 ]] || {
        echo "Database node has an unexpected WireGuard ingress rule count" >&2
        exit 1
    }
    [[ "$(
        grep -Fc \
            'ufw allow in on wg-vss from 10.203.142.0/24 to any port 5432 proto tcp' \
            <<<"$ufw_rules"
    )" == 1 ]] || {
        echo "PostgreSQL overlay firewall rule is missing" >&2
        exit 1
    }
    for database_peer in 1 2 3; do
        for control_port in 2379 2380 8008; do
            [[ "$(
                grep -Fc \
                    "ufw allow in on wg-vss from 10.203.142.${database_peer} to any port ${control_port} proto tcp" \
                    <<<"$ufw_rules"
            )" == 1 ]] || {
                echo "HA control-plane firewall rule is missing: peer=$database_peer port=$control_port" >&2
                exit 1
            }
        done
    done
elif [[ "$allow_count" != 0 || "$terminal_position" != 1 ]]; then
    echo "Coordinator-only node must deny all WireGuard ingress first" >&2
    exit 1
fi
sudo systemctl unmask wg-quick@wg-vss.service >/dev/null
sudo systemctl enable wg-quick@wg-vss.service >/dev/null
sudo systemctl restart wg-quick@wg-vss.service

actual_address="$(ip -4 -o address show dev wg-vss | awk '{print $4}')"
[[ "$actual_address" == "$wireguard_address" ]] || {
    echo "WireGuard address mismatch: expected=$wireguard_address actual=$actual_address" >&2
    exit 1
}
if [[ "$etcd_was_active" == true ]]; then
    sudo systemctl restart etcd.service
    sudo systemctl is-active --quiet etcd.service
fi
if [[ "$patroni_was_active" == true ]]; then
    sudo systemctl restart vss-postgres-ha.service
    sudo systemctl is-active --quiet vss-postgres-ha.service
fi
echo "WireGuard active: ${coordinator_name}=${wireguard_address}"
