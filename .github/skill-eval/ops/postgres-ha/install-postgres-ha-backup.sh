#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

payload_dir="${1:-}"
[[ "$(id -u)" -eq 0 && -d "$payload_dir" ]] || {
    echo "Usage: sudo $0 ROOT_ONLY_PAYLOAD_DIR" >&2
    exit 2
}
required=(
    backup.env
    backup-encryption-passphrase
    backup-key.sha256
    backup.pgpass
    ca.crt
    postgres-ha-backup.sh
    postgres-ha-restore-test.sh
    vss-postgres-ha-backup.service
    vss-postgres-ha-backup.timer
    vss-postgres-ha-restore-test.service
    vss-postgres-ha-restore-test.timer
)
for file in "${required[@]}"; do
    [[ -f "$payload_dir/$file" ]] || {
        echo "Missing backup deployment file: $file" >&2
        exit 1
    }
done
for secret in \
    backup.env \
    backup-encryption-passphrase \
    backup-key.sha256 \
    backup.pgpass; do
    [[ "$(stat -c '%a' "$payload_dir/$secret")" == "600" ]] || {
        echo "Backup secret must have mode 0600: $secret" >&2
        exit 1
    }
done

install -d -m 0755 -o root -g root /etc/postgresql-common
cluster_policy="/etc/postgresql-common/createcluster.conf"
cluster_policy_backup="$(mktemp)"
cluster_policy_existed=false
if [[ -f "$cluster_policy" ]]; then
    cp "$cluster_policy" "$cluster_policy_backup"
    cluster_policy_existed=true
fi
restore_cluster_policy() {
    if [[ "$cluster_policy_existed" == true ]]; then
        cp "$cluster_policy_backup" "$cluster_policy"
    else
        rm -f "$cluster_policy"
    fi
    rm -f "$cluster_policy_backup"
}
trap restore_cluster_policy EXIT
printf 'create_main_cluster = false\n' >"$cluster_policy"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates \
    gnupg \
    postgresql-16
restore_cluster_policy
trap - EXIT
if ! getent passwd vss-pg-backup >/dev/null; then
    useradd \
        --system \
        --home-dir /var/lib/vss-postgres-ha-backup \
        --create-home \
        --shell /usr/sbin/nologin \
        vss-pg-backup
fi
install -d -m 0755 -o root -g root /usr/local/libexec/vss-postgres-ha
install -d -m 0755 -o root -g root /etc/vss-postgres-ha
install -d -m 0700 -o vss-pg-backup -g vss-pg-backup \
    /var/backups/vss-postgres-ha/logical
expected_key_sha="$(<"$payload_dir/backup-key.sha256")"
[[ "$expected_key_sha" =~ ^[0-9a-f]{64}$ ]] || {
    echo "Invalid authoritative backup key fingerprint" >&2
    exit 1
}
incoming_key_sha="$(
    sha256sum "$payload_dir/backup-encryption-passphrase" | awk '{print $1}'
)"
[[ "$incoming_key_sha" == "$expected_key_sha" ]] || {
    echo "Backup key payload does not match its authoritative fingerprint" >&2
    exit 1
}
key_target="/etc/vss-postgres-ha/backup-encryption-passphrase"
[[ ! -L "$key_target" && ( ! -e "$key_target" || -f "$key_target" ) ]] || {
    echo "Backup key target must be a regular file" >&2
    exit 1
}
key_candidate="$(mktemp /etc/vss-postgres-ha/.backup-key.XXXXXX)"
trap 'rm -f "$key_candidate"' EXIT
install -m 0640 -o root -g vss-pg-backup \
    "$payload_dir/backup-encryption-passphrase" \
    "$key_candidate"
if [[ -e "$key_target" ]]; then
    cmp --silent "$key_candidate" "$key_target" || {
        echo "Refusing to overwrite a different backup encryption key" >&2
        exit 1
    }
elif ! ln "$key_candidate" "$key_target"; then
    if [[ ! -f "$key_target" ]] ||
       ! cmp --silent "$key_candidate" "$key_target"; then
        echo "Concurrent backup key registration installed a different key" >&2
        exit 1
    fi
fi
rm -f "$key_candidate"
trap - EXIT
chown root:vss-pg-backup "$key_target"
chmod 0640 "$key_target"
[[ "$(sha256sum "$key_target" | awk '{print $1}')" == "$expected_key_sha" ]] || {
    echo "Installed backup key fingerprint verification failed" >&2
    exit 1
}
install -m 0755 -o root -g root \
    "$payload_dir/postgres-ha-backup.sh" \
    /usr/local/libexec/vss-postgres-ha/postgres-ha-backup.sh
install -m 0755 -o root -g root \
    "$payload_dir/postgres-ha-restore-test.sh" \
    /usr/local/libexec/vss-postgres-ha/postgres-ha-restore-test.sh
install -m 0644 -o root -g root \
    "$payload_dir/ca.crt" \
    /etc/vss-postgres-ha/ca.crt
install -m 0640 -o root -g vss-pg-backup \
    "$payload_dir/backup.env" \
    /etc/vss-postgres-ha/backup.env
install -m 0600 -o vss-pg-backup -g vss-pg-backup \
    "$payload_dir/backup.pgpass" \
    /etc/vss-postgres-ha/backup.pgpass
for unit in \
    vss-postgres-ha-backup.service \
    vss-postgres-ha-backup.timer \
    vss-postgres-ha-restore-test.service \
    vss-postgres-ha-restore-test.timer; do
    install -m 0644 -o root -g root \
        "$payload_dir/$unit" \
        "/etc/systemd/system/$unit"
done

systemctl daemon-reload
systemctl enable --now \
    vss-postgres-ha-backup.timer \
    vss-postgres-ha-restore-test.timer >/dev/null
echo "PostgreSQL HA backup timers installed."
