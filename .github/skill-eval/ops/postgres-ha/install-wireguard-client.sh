#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

if ((EUID != 0)); then
    exec sudo bash "$0" "$@"
fi

payload_dir="${1:-}"
operation_id="${2:-}"
[[ -d "$payload_dir" ]] || {
    echo "Usage: $0 PAYLOAD_DIR OPERATION_ID" >&2
    exit 2
}
[[ "$operation_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || {
    echo "Invalid enrollment operation ID" >&2
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

command -v flock >/dev/null || {
    echo "flock is required for serialized WireGuard updates" >&2
    exit 1
}
exec 9>/run/vss-wireguard-peer-update.lock
flock -x 9
client_operation_file="/run/vss-wireguard-client-enrollment"
require_client_operation() {
    local current_operation=""
    local expires_at=""
    local now
    [[ -f "$client_operation_file" ]] &&
        read -r current_operation expires_at <"$client_operation_file"
    now="$(date +%s)"
    [[ "$current_operation" == "$operation_id" &&
       "$expires_at" =~ ^[0-9]+$ &&
       "$expires_at" -gt "$now" ]] || {
        echo "Client enrollment operation ownership expired or changed" >&2
        exit 1
    }
}
refresh_client_operation() {
    local marker_tmp
    marker_tmp="$(mktemp /run/.vss-wireguard-client-enrollment.XXXXXX)"
    printf '%s %s\n' "$operation_id" "$(($(date +%s) + 21600))" >"$marker_tmp"
    chmod 0600 "$marker_tmp"
    mv -f "$marker_tmp" "$client_operation_file"
}
require_client_operation
refresh_client_operation
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
sudo DEBIAN_FRONTEND=noninteractive timeout --kill-after=30s 900s \
    apt-get install -y -qq ca-certificates wireguard-tools
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

# Database clients initiate connections and do not accept arbitrary inbound
# traffic from other authenticated overlay peers.
ufw_status="$(sudo ufw status verbose)"
grep -Fxq 'Status: active' <<<"$ufw_status" || {
    echo "UFW must be active before installing the HA overlay" >&2
    exit 1
}
grep -Eq '^Default: deny \(incoming\)' <<<"$ufw_status" || {
    echo "UFW must enforce default-deny incoming traffic" >&2
    exit 1
}
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
if ! ufw_rules="$(sudo ufw show added)"; then
    echo "Could not inspect UFW rules after broad-rule removal" >&2
    exit 1
fi
if grep -Eq '^ufw (allow|deny) in on wg-vss' <<<"$ufw_rules"; then
    echo "Database client must not accept WireGuard ingress" >&2
    exit 1
fi
sudo ufw insert 1 deny in on wg-vss \
    comment 'VSS PostgreSQL HA terminal deny' >/dev/null
if ! ufw_rules="$(sudo ufw show added)"; then
    echo "Could not inspect final UFW rules" >&2
    exit 1
fi
[[ "$(
    awk '/^ufw / {print; exit}' <<<"$ufw_rules"
)" == "ufw deny in on wg-vss comment 'VSS PostgreSQL HA terminal deny'" ]] || {
    echo "WireGuard terminal deny must precede every generic allow" >&2
    exit 1
}
[[ "$(grep -Ec '^ufw deny in on wg-vss' <<<"$ufw_rules" || true)" == 1 &&
   "$(grep -Ec '^ufw allow in on wg-vss' <<<"$ufw_rules" || true)" == 0 ]] || {
    echo "Database client WireGuard ingress policy is not exact" >&2
    exit 1
}
require_client_operation
refresh_client_operation
sudo systemctl unmask wg-quick@wg-vss.service >/dev/null
sudo systemctl enable wg-quick@wg-vss.service >/dev/null
sudo systemctl restart wg-quick@wg-vss.service
[[ "$(ip -4 -o address show dev wg-vss | awk '{print $4}')" == "$address" ]]
echo "WireGuard database client active: $address"
