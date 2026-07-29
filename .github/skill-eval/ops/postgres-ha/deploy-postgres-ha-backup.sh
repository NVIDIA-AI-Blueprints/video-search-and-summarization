#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Install off-cluster logical backups and restore tests on a non-database
# coordinator (4-8). Run once per desired backup replica.
set -euo pipefail
umask 077

[[ "${1:-}" == "--apply" && $# -eq 1 ]] || {
    echo "Usage: $0 --apply" >&2
    exit 2
}
state_dir="${POSTGRES_HA_STATE_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/vss-skill-eval/postgres-ha}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
backup_host="${POSTGRES_HA_BACKUP_HOST:-vss-skill-validator-distributed-4}"
secret_dir="$state_dir/secrets"
bundle_dir="$state_dir/bundle"
ca_cert="$secret_dir/ca.crt"
backup_password_file="$secret_dir/backup-role-password"
backup_encryption_key="$secret_dir/backup-encryption-passphrase"
admin_dsn="$secret_dir/admin-dsn"
installer="$script_dir/install-postgres-ha-backup.sh"
[[ "$backup_host" =~ ^vss-skill-validator-distributed-[4-8]$ ]] || {
    echo "Backups must remain off the three database nodes" >&2
    exit 2
}
for command in base64 openssl python3 sha256sum ssh stat tar; do
    command -v "$command" >/dev/null || {
        echo "Missing backup deployment prerequisite: $command" >&2
        exit 1
    }
done
if [[ ! -s "$backup_encryption_key" ]]; then
    for index in $(seq 4 8); do
        key_state="$(
            ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
                "vss-skill-validator-distributed-${index}" \
                "if sudo test -s /etc/vss-postgres-ha/backup-encryption-passphrase; then echo configured; else echo absent; fi"
        )" || {
            echo "Cannot prove backup key absence on coordinator $index" >&2
            exit 1
        }
        if [[ "$key_state" == "configured" ]]; then
            echo "Refusing to replace missing operator backup key while a backup host is configured" >&2
            exit 1
        fi
        [[ "$key_state" == "absent" ]] || {
            echo "Unexpected backup key state on coordinator $index" >&2
            exit 1
        }
    done
    key_candidate="$(mktemp "$secret_dir/.backup-encryption-passphrase.XXXXXX")"
    trap 'rm -f "$key_candidate"' EXIT HUP INT TERM
    openssl rand -hex 64 >"$key_candidate"
    chmod 0600 "$key_candidate"
    if ! ln "$key_candidate" "$backup_encryption_key" 2>/dev/null; then
        [[ -s "$backup_encryption_key" ]] || {
            echo "Concurrent operator key registration left invalid state" >&2
            exit 1
        }
    fi
    rm -f "$key_candidate"
    trap - EXIT HUP INT TERM
fi
[[ -f "$backup_encryption_key" && ! -L "$backup_encryption_key" ]] || {
    echo "Operator backup encryption key must be a regular file" >&2
    exit 1
}
[[ "$(stat -c '%a' "$backup_encryption_key")" == 600 ]] || {
    echo "Operator backup encryption key must have mode 0600" >&2
    exit 1
}
inputs=(
    "$ca_cert"
    "$backup_password_file"
    "$backup_encryption_key"
    "$admin_dsn"
    "$installer"
    "$script_dir/postgres-ha-backup.sh"
    "$script_dir/postgres-ha-restore-test.sh"
    "$script_dir/vss-postgres-ha-backup.service"
    "$script_dir/vss-postgres-ha-backup.timer"
    "$script_dir/vss-postgres-ha-restore-test.service"
    "$script_dir/vss-postgres-ha-restore-test.timer"
)
for input in "${inputs[@]}"; do
    [[ -s "$input" ]] || {
        echo "Missing PostgreSQL backup input: $input" >&2
        exit 1
    }
done
local_key_sha="$(sha256sum "$backup_encryption_key" | awk '{print $1}')"
for index in $(seq 4 8); do
    peer_host="vss-skill-validator-distributed-${index}"
    remote_key_sha="$(
        ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
            "$peer_host" \
            "if sudo test -s /etc/vss-postgres-ha/backup-encryption-passphrase; then sudo sha256sum /etc/vss-postgres-ha/backup-encryption-passphrase | awk '{print \$1}'; else echo absent; fi"
    )" || {
        echo "Cannot verify backup encryption key state on $peer_host" >&2
        exit 1
    }
    [[ "$remote_key_sha" == "absent" ||
       "$remote_key_sha" == "$local_key_sha" ]] || {
        echo "Refusing deployment: backup encryption key differs on $peer_host" >&2
        exit 1
    }
done

registry_code="$(
    cat <<'PY'
import json
import re
import sys

import psycopg

payload = json.load(sys.stdin)
fingerprint = payload.get("fingerprint", "")
if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
    raise SystemExit("invalid backup key fingerprint")
with psycopg.connect(payload["dsn"]) as connection:
    connection.execute(
        """
        INSERT INTO public.backup_key_registry (singleton, sha256)
        VALUES (true, %s)
        ON CONFLICT (singleton) DO NOTHING
        """,
        (fingerprint,),
    )
    authoritative = connection.execute(
        """
        SELECT sha256
        FROM public.backup_key_registry
        WHERE singleton
        """
    ).fetchone()
    if authoritative != (fingerprint,):
        raise SystemExit("backup key differs from the authoritative fingerprint")
print("Authoritative backup key fingerprint verified.")
PY
)"
registry_code_b64="$(printf '%s' "$registry_code" | base64 -w0)"
registry_verified=false
for index in 1 2 3; do
    registry_host="vss-skill-validator-distributed-${index}"
    set +e
    python3 - "$admin_dsn" "$local_key_sha" <<'PY' |
import json
import pathlib
import sys

print(
    json.dumps(
        {
            "dsn": pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip(),
            "fingerprint": sys.argv[2],
        }
    )
)
PY
        ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
        -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=2 \
        "$registry_host" \
        "/home/ubuntu/eval-coordinator/venv/bin/python -c \"import base64; exec(base64.b64decode('$registry_code_b64'))\""
    registry_status=("${PIPESTATUS[@]}")
    set -e
    payload_status="${registry_status[0]}"
    ssh_status="${registry_status[1]}"
    if ((ssh_status == 255)); then
        echo "Coordinator $index is unreachable; trying the next database coordinator" >&2
        continue
    fi
    ((payload_status == 0)) || {
        echo "Failed to construct the backup-key registry request" >&2
        exit "$payload_status"
    }
    ((ssh_status == 0)) || {
        echo "Backup-key registry verification failed through coordinator $index" >&2
        exit "$ssh_status"
    }
    echo "Backup-key registry verified through coordinator $index."
    registry_verified=true
    break
done
[[ "$registry_verified" == true ]] || {
    echo "No database coordinator is reachable for backup-key registry verification" >&2
    exit 1
}

payload="$(mktemp -d "$bundle_dir/.backup-payload.XXXXXX")"
archive="$(mktemp --suffix=.tar.gz "$bundle_dir/.backup-payload.XXXXXX")"
operation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
remote_dir="/run/vss-postgres-ha-backup-install-${operation_id}"
remote_archive="$remote_dir/payload.tar.gz"
remote_staged=false
cleanup_local() {
    rm -rf "$payload" "$archive"
    if [[ "$remote_staged" == true ]]; then
        ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
            -o ConnectTimeout=5 \
            "$backup_host" \
            "sudo rm -rf '$remote_dir'" >/dev/null 2>&1 || true
    fi
}
trap cleanup_local EXIT HUP INT TERM

install -m 0644 "$ca_cert" "$payload/ca.crt"
install -m 0600 \
    "$backup_encryption_key" \
    "$payload/backup-encryption-passphrase"
printf '%s\n' "$local_key_sha" >"$payload/backup-key.sha256"
chmod 0600 "$payload/backup-key.sha256"
for file in \
    install-postgres-ha-backup.sh \
    postgres-ha-backup.sh \
    postgres-ha-restore-test.sh \
    vss-postgres-ha-backup.service \
    vss-postgres-ha-backup.timer \
    vss-postgres-ha-restore-test.service \
    vss-postgres-ha-restore-test.timer; do
    install -m 0600 "$script_dir/$file" "$payload/$file"
done
cat >"$payload/backup.env" <<'EOF'
PGHOST=vss-pg-1,vss-pg-2,vss-pg-3
PGPORT=5432,5432,5432
PGUSER=skill_eval_backup
PGDATABASE=eval
PGSSLMODE=verify-full
PGSSLROOTCERT=/etc/vss-postgres-ha/ca.crt
PGTARGETSESSIONATTRS=read-write
PGCONNECT_TIMEOUT=5
PGPASSFILE=/etc/vss-postgres-ha/backup.pgpass
BACKUP_ENCRYPTION_KEY_FILE=/etc/vss-postgres-ha/backup-encryption-passphrase
BACKUP_ROOT=/var/backups/vss-postgres-ha/logical
BACKUP_RETENTION=168
EOF
chmod 0600 "$payload/backup.env"
backup_password="$(<"$backup_password_file")"
: >"$payload/backup.pgpass"
for database_host in vss-pg-1 vss-pg-2 vss-pg-3; do
    printf '%s:5432:*:skill_eval_backup:%s\n' \
        "$database_host" \
        "$backup_password" \
        >>"$payload/backup.pgpass"
done
backup_password=""
chmod 0600 "$payload/backup.pgpass"
tar -C "$payload" -czf "$archive" .

remote_staged=true
ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$backup_host" \
    "sudo rm -rf '$remote_dir' && sudo install -d -m 700 -o root -g root '$remote_dir' && sudo tee '$remote_archive' >/dev/null && sudo chmod 600 '$remote_archive'" \
    <"$archive"

ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$backup_host" \
    "sudo bash -s -- '$remote_dir'" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
trap 'rm -rf "$remote_dir"' EXIT HUP INT TERM
exec 9>/run/vss-postgres-ha-backup-install.lock
flock -x 9
tar --no-same-owner -xzf "$remote_dir/payload.tar.gz" -C "$remote_dir"
rm -f "$remote_dir/payload.tar.gz"
chmod 700 "$remote_dir"
chmod 600 \
    "$remote_dir/backup.env" \
    "$remote_dir/backup-encryption-passphrase" \
    "$remote_dir/backup-key.sha256" \
    "$remote_dir/backup.pgpass"
chmod 700 "$remote_dir/install-postgres-ha-backup.sh"
bash "$remote_dir/install-postgres-ha-backup.sh" "$remote_dir"
validation_started_epoch="$(date +%s)"
systemctl start vss-postgres-ha-backup.service
systemctl start vss-postgres-ha-restore-test.service
systemctl is-active --quiet vss-postgres-ha-backup.timer
systemctl is-active --quiet vss-postgres-ha-restore-test.timer
[[ "$(systemctl show vss-postgres-ha-backup.service --property=Result --value)" == success ]]
[[ "$(systemctl show vss-postgres-ha-restore-test.service --property=Result --value)" == success ]]
utc_marker_epoch() {
    local value="$1"
    [[ "$value" =~ ^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$ ]] ||
        return 1
    date -u \
        --date="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]} ${BASH_REMATCH[4]}:${BASH_REMATCH[5]}:${BASH_REMATCH[6]} UTC" \
        +%s
}
for marker in last-success last-restore-test; do
    marker_value="$(</var/backups/vss-postgres-ha/logical/$marker)"
    marker_epoch="$(utc_marker_epoch "$marker_value")"
    ((marker_epoch >= validation_started_epoch - 1))
done
latest_target="$(
    readlink /var/backups/vss-postgres-ha/logical/latest.dump.gpg
)"
[[ "$latest_target" =~ ^eval-[0-9]{8}T[0-9]{6}Z[.]dump[.]gpg$ ]]
(
    cd /var/backups/vss-postgres-ha/logical
    sha256sum --check --status "${latest_target}.sha256"
)
REMOTE

remote_staged=false
cleanup_local
trap - EXIT HUP INT TERM
echo "Off-cluster PostgreSQL backup and restore test are healthy on $backup_host."
