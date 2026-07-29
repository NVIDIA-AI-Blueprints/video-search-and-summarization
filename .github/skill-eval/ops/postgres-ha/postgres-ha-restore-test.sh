#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

backup_root="${BACKUP_ROOT:-/var/backups/vss-postgres-ha/logical}"
encryption_key_file="${BACKUP_ENCRYPTION_KEY_FILE:-}"
[[ -r "$encryption_key_file" ]] || {
    echo "PostgreSQL backup encryption key is unreadable" >&2
    exit 2
}
[[ -d "${GNUPGHOME:-}" ]] || {
    echo "GNUPGHOME is unavailable" >&2
    exit 2
}
latest_link="$backup_root/latest.dump.gpg"
[[ -L "$latest_link" ]] || {
    echo "No completed PostgreSQL backup is available" >&2
    exit 1
}
backup="$(readlink -f "$latest_link")"
[[ "$backup" == "$backup_root"/eval-*.dump.gpg && -f "$backup" ]] || {
    echo "Latest PostgreSQL backup link escapes the backup directory" >&2
    exit 1
}
checksum="${backup}.sha256"
[[ -f "$checksum" ]] || {
    echo "PostgreSQL backup checksum is missing" >&2
    exit 1
}
(
    cd "$backup_root"
    sha256sum --check "$(basename "$checksum")"
)
gpg \
    --batch \
    --homedir "$GNUPGHOME" \
    --no-options \
    --no-tty \
    --pinentry-mode loopback \
    --passphrase-file "$encryption_key_file" \
    --decrypt "$backup" |
    pg_restore --list >/dev/null

# The service shares backup connection settings with pg_dump. Never let a
# multi-host production PGPORT/PGHOST leak into the isolated local server.
unset PGDATABASE PGHOST PGPASSFILE PGPORT PGSSLMODE PGSSLROOTCERT PGUSER
restore_root="${RESTORE_ROOT:-/run/vss-postgres-ha-restore-test}"
data_dir="$restore_root/data"
socket_dir="$restore_root/socket"
port="$((54000 + ($$ % 1000)))"
postgres_bindir="$(pg_config --bindir)"
local_user="$(id -un)"
rm -rf "$restore_root"
mkdir -p "$data_dir" "$socket_dir"
chmod 0700 "$restore_root" "$data_dir" "$socket_dir"
server_started=false
cleanup() {
    if [[ "$server_started" == true ]]; then
        "$postgres_bindir/pg_ctl" \
            --pgdata="$data_dir" \
            --mode=fast \
            stop >/dev/null 2>&1 || true
    fi
    rm -rf "$restore_root"
}
trap cleanup EXIT HUP INT TERM

"$postgres_bindir/initdb" \
    --pgdata="$data_dir" \
    --username="$local_user" \
    --auth=trust \
    --no-locale >/dev/null
"$postgres_bindir/pg_ctl" \
    --pgdata="$data_dir" \
    --options="-k $socket_dir -p $port -c listen_addresses=''" \
    --wait \
    start >/dev/null
server_started=true
local_psql=(
    env
    -u PGPASSFILE
    -u PGSSLMODE
    -u PGSSLROOTCERT
    PGHOST="$socket_dir"
    PGPORT="$port"
    PGUSER="$local_user"
)
"${local_psql[@]}" psql \
    --no-psqlrc \
    --dbname=postgres \
    --set ON_ERROR_STOP=1 <<'SQL' >/dev/null
CREATE ROLE skill_eval_owner NOLOGIN;
CREATE ROLE skill_eval_lease NOLOGIN;
CREATE ROLE skill_eval_fence NOLOGIN;
CREATE ROLE skill_eval_backup NOLOGIN;
CREATE DATABASE eval OWNER skill_eval_owner;
SQL
gpg \
    --batch \
    --homedir "$GNUPGHOME" \
    --no-options \
    --no-tty \
    --pinentry-mode loopback \
    --passphrase-file "$encryption_key_file" \
    --decrypt "$backup" |
    "${local_psql[@]}" pg_restore \
        --exit-on-error \
        --no-password \
        --dbname=eval
restored_state="$(
    "${local_psql[@]}" psql \
        --no-psqlrc \
        --dbname=eval \
        --tuples-only \
        --no-align \
        --set ON_ERROR_STOP=1 \
        --command "SELECT to_regclass('public.gpu_workers') IS NOT NULL AND to_regclass('public.gpu_leases') IS NOT NULL AND to_regclass('public.skill_eval_migrations') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'gpu_workers' AND column_name = 'fence_ready') AND (SELECT count(*) = 4 FROM pg_catalog.pg_roles WHERE rolname IN ('skill_eval_owner', 'skill_eval_lease', 'skill_eval_fence', 'skill_eval_backup'))"
)"
[[ "$restored_state" == "t" ]] || {
    echo "Restored database is missing lease safety tables" >&2
    exit 1
}
cleanup
server_started=false
trap - EXIT HUP INT TERM
date -u +%Y%m%dT%H%M%SZ >"$backup_root/last-restore-test"
echo "PostgreSQL backup restore test passed: $(basename "$backup")"
