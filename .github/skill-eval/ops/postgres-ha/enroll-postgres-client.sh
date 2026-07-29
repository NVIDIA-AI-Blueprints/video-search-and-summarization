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
ssh_options=(
    -o BatchMode=yes
    -o ControlMaster=no
    -o ControlPath=none
    -o ConnectTimeout=15
    -o ServerAliveInterval=15
    -o ServerAliveCountMax=4
)
ssh_command=(timeout 120 ssh "${ssh_options[@]}")

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
peer_update_script="$script_dir/update-wireguard-client-peer.py"
ca_cert="$state_dir/secrets/ca.crt"
hosts_entries="$state_dir/bundle/hosts.entries"
for required in \
    "$prepare_script" \
    "$install_script" \
    "$peer_update_script" \
    "$ca_cert" \
    "$hosts_entries"; do
    [[ -f "$required" ]] || { echo "Missing deployment input: $required" >&2; exit 1; }
done

remote_exec() {
    if [[ "$registered" == true ]]; then
        "${ssh_command[@]}" "$worker" "$@"
    else
        timeout 120 brev exec "$worker" "$@"
    fi
}

remote_exec_long() {
    if [[ "$registered" == true ]]; then
        timeout 1200 ssh "${ssh_options[@]}" "$worker" "$@"
    else
        timeout 1200 brev exec "$worker" "$@"
    fi
}

remote_copy() {
    local source="$1"
    local destination="$2"
    if [[ "$registered" == true ]]; then
        timeout 300 scp -q \
            "${ssh_options[@]}" \
            "$source" "${worker}:${destination}"
    else
        timeout 300 brev copy "$source" "${worker}:${destination}"
    fi
}

remote_exec "true" >/dev/null
for index in $(seq 1 3); do
    "${ssh_command[@]}" \
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

operation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
remote_dir="/tmp/vss-postgres-client-install-${operation_id}"
client_state_dir="$state_dir/clients"
install -d -m 0700 "$client_state_dir"
state_file="$client_state_dir/${worker}.tsv"
if [[ -f "$state_file" ]]; then
    IFS=$'\t' read -r previous_worker _ _ _ \
        <"$state_file"
    [[ "$previous_worker" == "$worker" ]] || {
        echo "Invalid prior client state for $worker" >&2
        exit 1
    }
elif [[ "$rotate_key" == true ]]; then
    echo "Cannot rotate an untracked WireGuard client key: $worker" >&2
    exit 1
fi

payload="$state_dir/bundle/client-${address_octet}-${operation_id}"
client_address="10.203.142.${address_octet}/32"
server_helper="/run/vss-update-wireguard-client-peer-${worker}-${operation_id}.py"
client_backup="/run/vss-wireguard-client-${worker}-${operation_id}.rollback"
success=false
server_updates_started=false
server_lock_indexes=()
client_backup_created=false
public_key=""
declare -A server_preimage_present=()
declare -A server_preimage_key=()
declare -A server_preimage_address=()
server_preimages_captured=false
state_tmp=""
client_operation_claimed=false

update_client_operation() {
    local action="$1"
    remote_exec "sudo bash -s -- '$action' '$operation_id'" <<'REMOTE'
set -euo pipefail
action="$1"
operation_id="$2"
marker="/run/vss-wireguard-client-enrollment"
exec 9>/run/vss-wireguard-peer-update.lock
flock -x 9
current_operation=""
expires_at=0
if [[ -f "$marker" ]]; then
    read -r current_operation expires_at <"$marker" || true
fi
now="$(date +%s)"
case "$action" in
    claim)
        if [[ "$current_operation" != "$operation_id" &&
              "$expires_at" =~ ^[0-9]+$ &&
              "$expires_at" -gt "$now" ]]; then
            echo "Another client enrollment operation is active" >&2
            exit 1
        fi
        ;;
    renew)
        [[ "$current_operation" == "$operation_id" &&
           "$expires_at" =~ ^[0-9]+$ &&
           "$expires_at" -gt "$now" ]] || {
            echo "Client enrollment operation ownership expired or changed" >&2
            exit 1
        }
        ;;
    release)
        if [[ "$current_operation" == "$operation_id" ]]; then
            rm -f "$marker"
        fi
        exit 0
        ;;
    *)
        exit 2
        ;;
esac
marker_tmp="$(mktemp /run/.vss-wireguard-client-enrollment.XXXXXX)"
trap 'rm -f "$marker_tmp"' EXIT
printf '%s %s\n' "$operation_id" "$((now + 21600))" >"$marker_tmp"
chmod 0600 "$marker_tmp"
mv -f "$marker_tmp" "$marker"
trap - EXIT
REMOTE
}

renew_operation_ownership() {
    local index
    local database_host
    for index in "${server_lock_indexes[@]}"; do
        database_host="vss-skill-validator-distributed-${index}"
        "${ssh_command[@]}" \
            "$database_host" \
            "sudo python3 '$server_helper' --mode renew --name '$worker' --operation-id '$operation_id'"
    done
    if [[ "$client_operation_claimed" == true ]]; then
        update_client_operation renew
    fi
}

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    cleanup_failed=false
    operation_owned=true
    server_restore_complete=true
    if ((${#server_lock_indexes[@]} > 0)); then
        if ! renew_operation_ownership; then
            operation_owned=false
            cleanup_failed=true
        fi
    fi
    if [[ "$success" != true &&
          "$server_updates_started" == true &&
          "$server_preimages_captured" == true &&
          -n "$public_key" ]]; then
        if [[ "$operation_owned" == true ]]; then
            for index in $(seq 1 3); do
                database_host="vss-skill-validator-distributed-${index}"
                restore_command=(
                    sudo python3 "$server_helper"
                    --mode restore
                    --name "$worker"
                    --public-key "$public_key"
                    --address "$client_address"
                    --operation-id "$operation_id"
                )
                if [[ "${server_preimage_present[$index]}" == true ]]; then
                    restore_command+=(
                        --previous-public-key "${server_preimage_key[$index]}"
                        --previous-address "${server_preimage_address[$index]}"
                    )
                fi
                if ! "${ssh_command[@]}" \
                    "$database_host" \
                    "${restore_command[*]}"; then
                    server_restore_complete=false
                    cleanup_failed=true
                fi
            done
        else
            server_restore_complete=false
        fi
    fi
    rollback_allowed=false
    if [[ "$operation_owned" == true &&
          "$server_restore_complete" == true ]]; then
        rollback_allowed=true
    fi
    if [[ "$success" != true && "$client_backup_created" == true ]]; then
        if ! remote_exec \
            "sudo bash -s -- '$client_backup' '$operation_id' '$rollback_allowed'" <<'REMOTE'
set -euo pipefail
backup="$1"
operation_id="$2"
rollback_allowed="$3"
marker="/run/vss-wireguard-client-enrollment"
exec 9>/run/vss-wireguard-peer-update.lock
flock -x 9
current_operation=""
expires_at=0
if [[ -f "$marker" ]]; then
    read -r current_operation expires_at <"$marker" || true
fi
now="$(date +%s)"
if [[ "$current_operation" != "$operation_id" ||
      ! "$expires_at" =~ ^[0-9]+$ ||
      "$expires_at" -le "$now" ]]; then
    echo "Skipping stale client rollback: operation ownership changed" >&2
    exit 0
fi
systemctl unmask wg-quick@wg-vss.service >/dev/null
systemctl stop wg-quick@wg-vss.service >/dev/null 2>&1 || true
if ip link show dev wg-vss >/dev/null 2>&1; then
    wg-quick down wg-vss >/dev/null 2>&1 || ip link delete dev wg-vss
fi
systemctl mask wg-quick@wg-vss.service >/dev/null
if ip link show dev wg-vss >/dev/null 2>&1; then
    echo "WireGuard interface remained active during rollback" >&2
    exit 1
fi
if [[ "$rollback_allowed" != true ]]; then
    rm -f "$marker"
    echo "Client left masked because server rollback was not authoritative" >&2
    exit 1
fi
rm -f \
    /etc/wireguard/vss-skill-eval.key \
    /etc/wireguard/wg-vss.conf \
    /etc/vss-postgres-ha/ca.crt \
    /usr/local/share/ca-certificates/vss-postgres-ha.crt
tar -C / -xpf "$backup/files.tar"
update-ca-certificates >/dev/null

prior_enablement="$(<"$backup/enabled")"
case "$prior_enablement" in
    masked)
        rm -f "$marker"
        exit 0
        ;;
    masked-runtime)
        systemctl unmask wg-quick@wg-vss.service >/dev/null
        systemctl mask --runtime wg-quick@wg-vss.service >/dev/null
        rm -f "$marker"
        exit 0
        ;;
    enabled | disabled) ;;
    *)
        echo "Invalid prior WireGuard enablement state" >&2
        rm -f "$marker"
        exit 1
        ;;
esac

firewall_safe=false
if ufw_status="$(ufw status verbose 2>/dev/null)" &&
   grep -Fxq 'Status: active' <<<"$ufw_status" &&
   grep -Eq '^Default: deny \(incoming\)' <<<"$ufw_status" &&
   ufw_rules="$(ufw show added 2>/dev/null)"; then
    first_rule="$(awk '/^ufw / {print; exit}' <<<"$ufw_rules")"
    deny_count="$(grep -Ec '^ufw deny in on wg-vss' <<<"$ufw_rules" || true)"
    allow_count="$(grep -Ec '^ufw allow in on wg-vss' <<<"$ufw_rules" || true)"
    if [[ "$first_rule" == \
          "ufw deny in on wg-vss comment 'VSS PostgreSQL HA terminal deny'" &&
          "$deny_count" == 1 &&
          "$allow_count" == 0 ]]; then
        firewall_safe=true
    fi
fi
if [[ "$firewall_safe" == true ]]; then
    systemctl unmask wg-quick@wg-vss.service >/dev/null
    if [[ "$prior_enablement" == enabled ]]; then
        systemctl enable wg-quick@wg-vss.service >/dev/null
    else
        systemctl disable wg-quick@wg-vss.service >/dev/null
    fi
    if [[ "$(<"$backup/active")" == active ]]; then
        systemctl start wg-quick@wg-vss.service
    fi
    rm -f "$marker"
else
    rm -f "$marker"
    echo "Rollback restored files but left WireGuard masked: firewall is unsafe" >&2
    exit 1
fi
REMOTE
        then
            cleanup_failed=true
        fi
    fi
    if [[ "$client_operation_claimed" == true ]]; then
        if ! update_client_operation release; then
            cleanup_failed=true
        fi
    fi
    for index in "${server_lock_indexes[@]}"; do
        database_host="vss-skill-validator-distributed-${index}"
        if ! "${ssh_command[@]}" \
            "$database_host" \
            "sudo python3 '$server_helper' --mode unlock --name '$worker' --operation-id '$operation_id'"; then
            cleanup_failed=true
        fi
    done
    for index in $(seq 1 3); do
        database_host="vss-skill-validator-distributed-${index}"
        if ! "${ssh_command[@]}" \
            "$database_host" \
            "sudo rm -f '$server_helper'" \
            >/dev/null 2>&1; then
            cleanup_failed=true
        fi
    done
    if ! remote_exec "sudo rm -rf '$client_backup'; rm -rf '$remote_dir'" \
        >/dev/null 2>&1; then
        cleanup_failed=true
    fi
    rm -rf "$payload"
    [[ -z "$state_tmp" ]] || rm -f "$state_tmp"
    if [[ "$status" -eq 0 && "$cleanup_failed" == true ]]; then
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

for index in $(seq 1 3); do
    database_host="vss-skill-validator-distributed-${index}"
    "${ssh_command[@]}" \
        "$database_host" \
        "sudo rm -f '$server_helper' && sudo tee '$server_helper' >/dev/null && sudo chmod 700 '$server_helper'" \
        <"$peer_update_script"
done
for index in $(seq 1 3); do
    database_host="vss-skill-validator-distributed-${index}"
    server_lock_indexes+=("$index")
    "${ssh_command[@]}" \
        "$database_host" \
        "sudo python3 '$server_helper' --mode lock --name '$worker' --operation-id '$operation_id'"
done
client_operation_claimed=true
update_client_operation claim
renew_operation_ownership

remote_exec "sudo bash -s -- '$client_backup'" <<'REMOTE'
set -euo pipefail
backup="$1"
rm -rf "$backup"
install -d -m 700 -o root -g root "$backup"
enabled_state="$(systemctl is-enabled wg-quick@wg-vss.service 2>/dev/null || true)"
case "$enabled_state" in
    enabled | disabled | masked | masked-runtime) ;;
    *) enabled_state=disabled ;;
esac
printf '%s\n' "$enabled_state" >"$backup/enabled"
if systemctl is-active --quiet wg-quick@wg-vss.service; then
    printf 'active\n' >"$backup/active"
else
    printf 'inactive\n' >"$backup/active"
fi
paths=(etc/hosts)
for path in \
    etc/wireguard/vss-skill-eval.key \
    etc/wireguard/wg-vss.conf \
    etc/vss-postgres-ha/ca.crt \
    usr/local/share/ca-certificates/vss-postgres-ha.crt; do
    [[ -e "/$path" ]] && paths+=("$path")
done
tar -C / -cpf "$backup/files.tar" "${paths[@]}"
chmod 600 "$backup"/*
REMOTE
client_backup_created=true
renew_operation_ownership

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
    echo "Worker returned an invalid WireGuard public key" >&2
    exit 1
}

canonical_preimage=""
for index in $(seq 1 3); do
    database_host="vss-skill-validator-distributed-${index}"
    preimage="$(
        "${ssh_command[@]}" \
            "$database_host" \
            "sudo python3 '$server_helper' --mode snapshot --name '$worker' --public-key '$public_key' --address '$client_address' --operation-id '$operation_id'"
    )"
    if [[ -z "$canonical_preimage" ]]; then
        canonical_preimage="$preimage"
    elif [[ "$preimage" != "$canonical_preimage" ]]; then
        echo "WireGuard client preimage differs across database nodes" >&2
        exit 1
    fi
    readarray -t preimage_fields < <(
        python3 -c '
import json
import sys

value = json.loads(sys.stdin.read())
present = value.get("present")
if not isinstance(present, bool):
    raise SystemExit("invalid peer snapshot")
print("true" if present else "false")
print(value.get("public_key", ""))
print(value.get("address", ""))
' <<<"$preimage"
    )
    [[ "${#preimage_fields[@]}" -eq 3 ]] || {
        echo "Invalid WireGuard peer snapshot from $database_host" >&2
        exit 1
    }
    server_preimage_present[$index]="${preimage_fields[0]}"
    server_preimage_key[$index]="${preimage_fields[1]}"
    server_preimage_address[$index]="${preimage_fields[2]}"
    if [[ "${server_preimage_present[$index]}" == true ]]; then
        [[ "${server_preimage_key[$index]}" =~ ^[A-Za-z0-9+/]{43}=$ &&
           "${server_preimage_address[$index]}" =~ ^10[.]203[.]142[.][0-9]{1,3}/32$ ]] || {
            echo "Invalid existing WireGuard peer from $database_host" >&2
            exit 1
        }
    elif [[ "${server_preimage_present[$index]}" != false ||
            -n "${server_preimage_key[$index]}" ||
            -n "${server_preimage_address[$index]}" ]]; then
        echo "Invalid absent WireGuard peer snapshot from $database_host" >&2
        exit 1
    fi
done
server_preimages_captured=true

rm -rf "$payload"
install -d -m 0700 "$payload"
install -m 0644 "$ca_cert" "$payload/ca.crt"
install -m 0644 "$hosts_entries" "$payload/hosts.entries"
printf '%s\n' "$client_address" >"$payload/wireguard-address"
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

# Validate all three servers before changing any of them. The helper serializes
# each local read-modify-write and performs its own runtime rollback.
for index in $(seq 1 3); do
    database_host="vss-skill-validator-distributed-${index}"
    "${ssh_command[@]}" \
        "$database_host" \
        "sudo python3 '$server_helper' --mode check --name '$worker' --public-key '$public_key' --address '$client_address' --operation-id '$operation_id'"
done
renew_operation_ownership
server_updates_started=true
for index in $(seq 1 3); do
    database_host="vss-skill-validator-distributed-${index}"
    apply_command=(
        sudo python3 "$server_helper"
        --mode apply
        --name "$worker"
        --public-key "$public_key"
        --address "$client_address"
        --operation-id "$operation_id"
    )
    if [[ "${server_preimage_present[$index]}" == true ]]; then
        apply_command+=(
            --previous-public-key "${server_preimage_key[$index]}"
            --previous-address "${server_preimage_address[$index]}"
        )
    fi
    "${ssh_command[@]}" \
        "$database_host" \
        "${apply_command[*]}"
done
renew_operation_ownership

for file in ca.crt hosts.entries wireguard-address wireguard-peers.conf; do
    remote_copy "$payload/$file" "$remote_dir/$file"
done
remote_copy "$install_script" "$remote_dir/install-wireguard-client.sh"
if remote_exec_long \
    "chmod 700 '$remote_dir/install-wireguard-client.sh' && '$remote_dir/install-wireguard-client.sh' '$remote_dir' '$operation_id' && for database in 10.203.142.1 10.203.142.2 10.203.142.3; do timeout 5 bash -c \"</dev/tcp/\${database}/5432\"; done"; then
    remote_exec "rm -rf '$remote_dir'"
else
    remote_exec "rm -rf '$remote_dir'" || true
    exit 1
fi
renew_operation_ownership

state_tmp="$(mktemp "$client_state_dir/.${worker}.XXXXXX")"
printf '%s\t%s\t%s\t%s\n' \
    "$worker" "$address_octet" "$public_key" "$registered" \
    >"$state_tmp"
chmod 0600 "$state_tmp"
# The network and state transitions are now complete. Ignore termination during
# the atomic rename so cleanup cannot roll networking back after committing the
# new local state.
trap '' HUP INT TERM
mv -f "$state_tmp" "$state_file"
state_tmp=""
success=true
trap cleanup HUP INT TERM
echo "ENROLLED: $worker=10.203.142.${address_octet}"
