#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Bootstrap roles/schema after the Patroni cluster is healthy. This command
# never restarts infrastructure and is safe to retry after a partial deploy.
set -euo pipefail
umask 077

repository="${GITHUB_REPOSITORY:-NVIDIA-AI-Blueprints/video-search-and-summarization}"
state_dir="${POSTGRES_HA_STATE_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/vss-skill-eval/postgres-ha}"
publish_github_secret=false
case "$#" in
    0) ;;
    1)
        [[ "$1" == "--publish-github-secret" ]] || {
            echo "Usage: $0 [--publish-github-secret]" >&2
            exit 2
        }
        publish_github_secret=true
        ;;
    *)
        echo "Usage: $0 [--publish-github-secret]" >&2
        exit 2
        ;;
esac

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel)"
schema="$repo_root/.github/skill-eval/postgres-gpu-leases.sql"
migration_generator="$script_dir/generate-legacy-migration.py"
backup_deployer="$script_dir/deploy-postgres-ha-backup.sh"
bundle_dir="$state_dir/bundle"
secret_dir="$state_dir/secrets"
legacy_inventory="$bundle_dir/legacy-inventory.json"
ssh_options=(-o BatchMode=yes -o ControlMaster=no -o ControlPath=none)

for required in \
    "$schema" \
    "$migration_generator" \
    "$backup_deployer" \
    "$legacy_inventory" \
    "$secret_dir/postgres-password" \
    "$secret_dir/lease-role-password" \
    "$secret_dir/fence-role-password" \
    "$secret_dir/backup-role-password"; do
    [[ -s "$required" ]] || { echo "Missing secure deployment state: $required" >&2; exit 1; }
done

cluster_json="$bundle_dir/patroni-cluster.json"
cluster_source=""
for index in $(seq 1 3); do
    host="vss-skill-validator-distributed-${index}"
    cluster_capture="${cluster_json}.capture"
    if ssh "${ssh_options[@]}" "$host" \
        "sudo -u postgres patronictl -c /etc/vss-postgres-ha/patroni.yml list --format json" \
        >"$cluster_capture" 2>/dev/null; then
        mv "$cluster_capture" "$cluster_json"
        cluster_source="$host"
        break
    fi
    rm -f "$cluster_capture"
done
[[ -n "$cluster_source" ]] || {
    echo "No PostgreSQL HA node returned Patroni cluster health" >&2
    exit 1
}
leader="$(
    python3 - "$cluster_json" <<'PY'
import json
import sys

members = json.load(open(sys.argv[1], encoding="utf-8"))
if len(members) != 3:
    raise SystemExit("expected exactly three Patroni members")
if any(member.get("State") not in {"running", "streaming"} for member in members):
    raise SystemExit("a Patroni member is not healthy")
leaders = [member["Member"] for member in members if member.get("Role") == "Leader"]
if len(leaders) != 1 or not any(member.get("Role") == "Sync Standby" for member in members):
    raise SystemExit("cluster lacks one leader and a synchronous standby")
print(leaders[0])
PY
)"
[[ "$leader" =~ ^vss-pg-[1-3]$ ]] || { echo "Invalid Patroni leader: $leader" >&2; exit 1; }
leader_index="${leader##*-}"
leader_host="vss-skill-validator-distributed-${leader_index}"

lease_password="$(<"$secret_dir/lease-role-password")"
fence_password="$(<"$secret_dir/fence-role-password")"
backup_password="$(<"$secret_dir/backup-role-password")"
postgres_password="$(<"$secret_dir/postgres-password")"

migration_sql="$bundle_dir/legacy-inventory.sql"
python3 "$migration_generator" "$legacy_inventory" "$migration_sql"

bootstrap_sql="$bundle_dir/bootstrap-lease-database.sql"
cat >"$bootstrap_sql" <<EOF
\set ON_ERROR_STOP on
DO \$bootstrap\$
DECLARE
    membership record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'skill_eval_owner') THEN
        CREATE ROLE skill_eval_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'skill_eval_lease') THEN
        CREATE ROLE skill_eval_lease LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'skill_eval_fence') THEN
        CREATE ROLE skill_eval_fence LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'skill_eval_backup') THEN
        CREATE ROLE skill_eval_backup LOGIN;
    END IF;
    FOR membership IN
        SELECT parent.rolname AS granted_role, member.rolname AS member_role
        FROM pg_catalog.pg_auth_members AS relation
        JOIN pg_catalog.pg_roles AS parent ON parent.oid = relation.roleid
        JOIN pg_catalog.pg_roles AS member ON member.oid = relation.member
        WHERE member.rolname IN (
            'skill_eval_owner',
            'skill_eval_lease',
            'skill_eval_fence',
            'skill_eval_backup'
        )
    LOOP
        EXECUTE format(
            'REVOKE %I FROM %I',
            membership.granted_role,
            membership.member_role
        );
    END LOOP;
    ALTER ROLE skill_eval_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    EXECUTE format('ALTER ROLE skill_eval_lease LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD %L', '${lease_password}');
    EXECUTE format('ALTER ROLE skill_eval_fence LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD %L', '${fence_password}');
    EXECUTE format('ALTER ROLE skill_eval_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD %L', '${backup_password}');
END
\$bootstrap\$;
SELECT 'CREATE DATABASE eval OWNER skill_eval_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'eval')
\gexec
\connect eval
REVOKE CONNECT ON DATABASE eval FROM PUBLIC;
GRANT CONNECT ON DATABASE eval TO skill_eval_lease, skill_eval_fence, skill_eval_backup;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET ROLE skill_eval_owner;
\ir postgres-gpu-leases.sql
\ir legacy-inventory.sql
RESET ROLE;
EOF
chmod 0600 "$bootstrap_sql"

bootstrap_payload="$(mktemp -d "$bundle_dir/.bootstrap-payload.XXXXXX")"
bootstrap_archive="$(mktemp "$bundle_dir/.bootstrap-payload.XXXXXX.tar.gz")"
remote_pending=false
remote_dir=""
cleanup_local_bootstrap() {
    rm -rf "$bootstrap_payload" "$bootstrap_archive"
    if [[ "$remote_pending" == true ]]; then
        ssh "${ssh_options[@]}" -o ConnectTimeout=5 "$leader_host" \
            "sudo rm -rf '$remote_dir'" >/dev/null 2>&1 || true
    fi
}
trap cleanup_local_bootstrap EXIT HUP INT TERM
install -m 0600 "$bootstrap_sql" \
    "$bootstrap_payload/bootstrap-lease-database.sql"
install -m 0600 "$schema" "$bootstrap_payload/postgres-gpu-leases.sql"
install -m 0600 "$migration_sql" "$bootstrap_payload/legacy-inventory.sql"
tar -C "$bootstrap_payload" -czf "$bootstrap_archive" .

remote_dir="/run/vss-postgres-ha-bootstrap"
remote_archive="$remote_dir/payload.tar.gz"
remote_pending=true
# Both paths are fixed local constants, expanded before the remote command.
# shellcheck disable=SC2029
ssh "${ssh_options[@]}" "$leader_host" \
    "sudo rm -rf '$remote_dir' && sudo install -d -m 700 -o root -g root '$remote_dir' && sudo tee '$remote_archive' >/dev/null && sudo chmod 600 '$remote_archive'" \
    <"$bootstrap_archive"
# shellcheck disable=SC2029
ssh "${ssh_options[@]}" "$leader_host" "sudo bash -s -- '$remote_dir'" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
trap 'rm -rf "$remote_dir"' EXIT HUP INT TERM
tar --no-same-owner -xzf "$remote_dir/payload.tar.gz" -C "$remote_dir"
rm -f "$remote_dir/payload.tar.gz"
chown -R postgres:postgres "$remote_dir"
chmod 700 "$remote_dir"
chmod 600 "$remote_dir"/*.sql
sudo -u postgres psql \
    --no-psqlrc \
    -f "$remote_dir/bootstrap-lease-database.sql"
REMOTE
remote_pending=false
cleanup_local_bootstrap
trap - EXIT HUP INT TERM

hosts_uri="vss-pg-1:5432,vss-pg-2:5432,vss-pg-3:5432"
query="sslmode=verify-full&sslrootcert=/etc/vss-postgres-ha/ca.crt&target_session_attrs=read-write&connect_timeout=5"
lease_dsn="$secret_dir/lease-dsn"
fence_dsn="$secret_dir/fence-dsn"
admin_dsn="$secret_dir/admin-dsn"
printf 'postgresql://skill_eval_lease:%s@%s/eval?%s\n' \
    "$lease_password" "$hosts_uri" "$query" >"$lease_dsn"
printf 'postgresql://skill_eval_fence:%s@%s/eval?%s\n' \
    "$fence_password" "$hosts_uri" "$query" >"$fence_dsn"
printf 'postgresql://postgres:%s@%s/eval?%s\n' \
    "$postgres_password" "$hosts_uri" "$query" >"$admin_dsn"
chmod 0600 "$lease_dsn" "$fence_dsn" "$admin_dsn"

for index in $(seq 1 8); do
    host="vss-skill-validator-distributed-${index}"
    ssh "${ssh_options[@]}" "$host" \
        "/home/ubuntu/eval-coordinator/venv/bin/python -c 'import sys, psycopg; dsn = sys.stdin.read().strip(); connection = psycopg.connect(dsn, autocommit=True); cursor = connection.cursor(); cursor.execute(\"SELECT NOT pg_is_in_recovery()\"); result = cursor.fetchone(); cursor.close(); connection.close(); assert result == (True,)' " \
        <"$lease_dsn"
    echo "DATABASE READY: $host"
done

POSTGRES_HA_STATE_DIR="$state_dir" "$backup_deployer" --apply

if [[ "$publish_github_secret" == true ]]; then
    gh secret set GPU_LEASE_DATABASE_URL --repo "$repository" <"$lease_dsn"
fi

echo "Lease database finalized on $leader_host."
echo "Coordinator DSN: $lease_dsn"
echo "GPU fence DSN: $fence_dsn"
echo "Administrator DSN: $admin_dsn"
