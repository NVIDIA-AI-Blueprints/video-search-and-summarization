#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

backup_root="${BACKUP_ROOT:-/var/backups/vss-postgres-ha/logical}"
retention="${BACKUP_RETENTION:-168}"
[[ "$retention" =~ ^[1-9][0-9]*$ ]] || {
    echo "BACKUP_RETENTION must be a positive integer" >&2
    exit 2
}
[[ "${PGSSLMODE:-}" == "verify-full" ]] || {
    echo "PostgreSQL backups require PGSSLMODE=verify-full" >&2
    exit 2
}
[[ -r "${PGPASSFILE:-}" ]] || {
    echo "PostgreSQL backup password file is unreadable" >&2
    exit 2
}
encryption_key_file="${BACKUP_ENCRYPTION_KEY_FILE:-}"
[[ -r "$encryption_key_file" ]] || {
    echo "PostgreSQL backup encryption key is unreadable" >&2
    exit 2
}
[[ -d "${GNUPGHOME:-}" ]] || {
    echo "GNUPGHOME is unavailable" >&2
    exit 2
}

mkdir -p "$backup_root"
chmod 0700 "$backup_root"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
partial="$backup_root/.eval-${timestamp}.dump.gpg.partial"
backup="$backup_root/eval-${timestamp}.dump.gpg"
checksum="${backup}.sha256"
trap 'rm -f "$partial" "${checksum}.partial"' EXIT HUP INT TERM

pg_dump \
    --format=custom \
    --no-password \
    --serializable-deferrable |
    gpg \
        --batch \
        --homedir "$GNUPGHOME" \
        --no-options \
        --no-tty \
        --pinentry-mode loopback \
        --passphrase-file "$encryption_key_file" \
        --symmetric \
        --cipher-algo AES256 \
        --compress-algo none \
        --output "$partial"
gpg \
    --batch \
    --homedir "$GNUPGHOME" \
    --no-options \
    --no-tty \
    --pinentry-mode loopback \
    --passphrase-file "$encryption_key_file" \
    --decrypt "$partial" |
    pg_restore --list >/dev/null
mv "$partial" "$backup"
(
    cd "$backup_root"
    sha256sum "$(basename "$backup")" >"$(basename "$checksum").partial"
)
mv "${checksum}.partial" "$checksum"
ln -sfn "$(basename "$backup")" "$backup_root/.latest.dump.gpg"
mv -Tf "$backup_root/.latest.dump.gpg" "$backup_root/latest.dump.gpg"
printf '%s\n' "$timestamp" >"$backup_root/last-success"

mapfile -t backups < <(
    printf '%s\n' "$backup_root"/eval-*.dump.gpg |
        sort
)
while ((${#backups[@]} > retention)); do
    expired="${backups[0]}"
    rm -f "$expired" "${expired}.sha256"
    backups=("${backups[@]:1}")
done

trap - EXIT HUP INT TERM
echo "Verified PostgreSQL backup: $backup"
