#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Install one pre-rendered PostgreSQL/Patroni/etcd node. The operator deployer
# supplies a root-only payload and starts the three nodes only after every
# node has been configured successfully.
set -euo pipefail
umask 077

payload_dir=""
node_index=""
coordinator_name=""
confirm_reset=false
confirm_etcd_reset=false

usage() {
    echo "Usage: $0 --payload-dir DIR --node-index 1..3 --coordinator-name NAME --confirm-reset-local-postgres --confirm-reset-local-etcd" >&2
}

while (($#)); do
    case "$1" in
        --payload-dir) payload_dir="${2:-}"; shift 2 ;;
        --node-index) node_index="${2:-}"; shift 2 ;;
        --coordinator-name) coordinator_name="${2:-}"; shift 2 ;;
        --confirm-reset-local-postgres) confirm_reset=true; shift ;;
        --confirm-reset-local-etcd) confirm_etcd_reset=true; shift ;;
        *) usage; exit 2 ;;
    esac
done

[[ "$node_index" =~ ^[1-3]$ ]] || { usage; exit 2; }
[[ "$coordinator_name" == "vss-skill-validator-distributed-${node_index}" ]] || {
    echo "Coordinator identity does not match node index" >&2
    exit 2
}
[[ -d "$payload_dir" ]] || { echo "Missing payload directory" >&2; exit 2; }
[[ "$confirm_reset" == true ]] || {
    echo "--confirm-reset-local-postgres is required" >&2
    exit 2
}
[[ "$confirm_etcd_reset" == true ]] || {
    echo "--confirm-reset-local-etcd is required" >&2
    exit 2
}
required=(
    ca.crt
    etcd.env
    etcd-node.crt
    etcd-node.key
    patroni-etcd-client.crt
    patroni-etcd-client.key
    patroni.yml
    postgres-server.crt
    postgres-server.key
    vss-postgres-ha.service
)
for file in "${required[@]}"; do
    [[ -f "$payload_dir/$file" ]] || {
        echo "Missing payload file: $file" >&2
        exit 1
    }
done

for secret in \
    etcd-node.key \
    patroni-etcd-client.key \
    patroni.yml \
    postgres-server.key; do
    [[ "$(stat -c '%a' "$payload_dir/$secret")" == "600" ]] || {
        echo "Payload secret must have mode 0600: $secret" >&2
        exit 1
    }
done

sudo install -d -m 0755 -o root -g root /etc/vss-postgres-ha
sudo touch /etc/vss-postgres-ha/.install-started
sudo chmod 0600 /etc/vss-postgres-ha/.install-started

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates \
    etcd-client \
    etcd-server \
    patroni \
    postgresql-16 \
    postgresql-client-16 \
    python3-etcd

# Package installation may start empty default services. Stop them before
# replacing configuration or data.
sudo systemctl disable --now patroni.service patroni@.service 2>/dev/null || true
sudo systemctl disable --now postgresql.service 2>/dev/null || true
sudo systemctl stop vss-postgres-ha.service etcd.service 2>/dev/null || true

backup_root="/var/backups/vss-postgres-ha"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 0700 -o root -g root "$backup_root"
first_install=true
if [[ -f /etc/vss-postgres-ha/.node-configured ]]; then
    first_install=false
fi

if [[ "$first_install" == true ]] &&
   sudo pg_lsclusters --no-header 2>/dev/null |
       awk '$1 == "16" && $2 == "main" {found=1} END {exit !found}'; then
    if [[ ! -f "$backup_root/pre-patroni.dumped" ]]; then
        dump_path="/tmp/vss-pre-patroni-${timestamp}.sql"
        started_for_backup=false
        if ! sudo pg_ctlcluster 16 main status >/dev/null 2>&1; then
            sudo pg_ctlcluster 16 main start
            started_for_backup=true
        fi
        if ! sudo -u postgres pg_dumpall |
            sudo tee "$dump_path" >/dev/null; then
            sudo rm -f "$dump_path"
            if [[ "$started_for_backup" == true ]]; then
                sudo pg_ctlcluster 16 main stop || true
            fi
            echo "Could not back up the existing PostgreSQL 16/main cluster" >&2
            exit 1
        fi
        sudo install -m 0600 -o root -g root \
            "$dump_path" "$backup_root/pre-patroni-${timestamp}.sql"
        sudo rm -f "$dump_path"
        if [[ "$started_for_backup" == true ]]; then
            sudo pg_ctlcluster 16 main stop
        fi
        sudo touch "$backup_root/pre-patroni.dumped"
    fi
    sudo pg_dropcluster --stop 16 main
fi

if [[ "$first_install" == true &&
      -d /var/lib/etcd/vss-postgres-ha &&
      ! -f "$backup_root/pre-etcd.saved" ]]; then
    if sudo test -n "$(sudo ls -A /var/lib/etcd/vss-postgres-ha 2>/dev/null)"; then
        sudo tar \
            -C /var/lib/etcd \
            -czf "$backup_root/pre-etcd-${timestamp}.tar.gz" \
            vss-postgres-ha
    fi
    sudo touch "$backup_root/pre-etcd.saved"
fi
if [[ "$first_install" == true ]]; then
    sudo rm -rf /var/lib/etcd/vss-postgres-ha
fi
sudo install -d -m 0700 -o etcd -g etcd /var/lib/etcd/vss-postgres-ha

# The service-specific keys remain group-restricted below. The shared parent
# must be traversable by both the postgres and etcd service accounts.
sudo install -d -m 0755 -o root -g root /etc/vss-postgres-ha
sudo install -m 0644 -o root -g root \
    "$payload_dir/ca.crt" /etc/vss-postgres-ha/ca.crt
sudo install -m 0644 -o root -g root \
    "$payload_dir/postgres-server.crt" \
    /etc/vss-postgres-ha/postgres-server.crt
sudo install -m 0600 -o postgres -g postgres \
    "$payload_dir/postgres-server.key" \
    /etc/vss-postgres-ha/postgres-server.key
sudo install -m 0640 -o root -g postgres \
    "$payload_dir/patroni.yml" /etc/vss-postgres-ha/patroni.yml
sudo install -m 0644 -o root -g root \
    "$payload_dir/patroni-etcd-client.crt" \
    /etc/vss-postgres-ha/patroni-etcd-client.crt
sudo install -m 0600 -o postgres -g postgres \
    "$payload_dir/patroni-etcd-client.key" \
    /etc/vss-postgres-ha/patroni-etcd-client.key

sudo install -d -m 0750 -o root -g etcd /etc/vss-postgres-ha/etcd
sudo install -m 0644 -o root -g etcd \
    "$payload_dir/ca.crt" /etc/vss-postgres-ha/etcd/ca.crt
sudo install -m 0644 -o root -g etcd \
    "$payload_dir/etcd-node.crt" /etc/vss-postgres-ha/etcd/node.crt
sudo install -m 0640 -o root -g etcd \
    "$payload_dir/etcd-node.key" /etc/vss-postgres-ha/etcd/node.key
sudo install -m 0640 -o root -g etcd \
    "$payload_dir/etcd.env" /etc/default/etcd

sudo install -m 0644 -o root -g root \
    "$payload_dir/vss-postgres-ha.service" \
    /etc/systemd/system/vss-postgres-ha.service

sudo tee /etc/modules-load.d/vss-patroni-watchdog.conf >/dev/null <<'EOF'
softdog
EOF
sudo tee /etc/udev/rules.d/60-vss-patroni-watchdog.rules >/dev/null <<'EOF'
KERNEL=="watchdog", OWNER="postgres", GROUP="postgres", MODE="0600"
KERNEL=="watchdog0", OWNER="postgres", GROUP="postgres", MODE="0600"
EOF
sudo modprobe softdog
sudo udevadm trigger --subsystem-match=misc
sudo udevadm settle
for watchdog_device in /dev/watchdog /dev/watchdog0; do
    [[ -e "$watchdog_device" ]] || continue
    sudo chown postgres:postgres "$watchdog_device"
    sudo chmod 0600 "$watchdog_device"
done
sudo -u postgres test -w /dev/watchdog || {
    echo "Patroni cannot access /dev/watchdog" >&2
    exit 1
}

sudo install -d -m 0700 -o postgres -g postgres /var/lib/postgresql/vss-ha
sudo install -d -m 2775 -o postgres -g postgres /run/postgresql

sudo install -d -m 0755 /etc/systemd/system/etcd.service.d
sudo tee /etc/systemd/system/etcd.service.d/vss-resource-limits.conf >/dev/null <<'EOF'
[Unit]
Requires=wg-quick@wg-vss.service
After=wg-quick@wg-vss.service

[Service]
Restart=on-failure
RestartSec=5
OOMScoreAdjust=-800
CPUWeight=1000
IOWeight=1000
MemoryHigh=512M
MemoryMax=1G
TasksMax=1024
EOF

sudo systemctl daemon-reload
sudo systemctl enable etcd.service vss-postgres-ha.service >/dev/null
sudo systemctl reset-failed etcd.service vss-postgres-ha.service
sudo touch /etc/vss-postgres-ha/.node-configured
sudo chmod 0600 /etc/vss-postgres-ha/.node-configured
sudo rm -f /etc/vss-postgres-ha/.install-started

echo "Configured PostgreSQL HA node ${node_index}; services remain stopped."
