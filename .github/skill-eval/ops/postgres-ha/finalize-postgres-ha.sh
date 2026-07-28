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
[[ "${1:-}" == "--publish-github-secret" ]] && publish_github_secret=true
[[ $# -le 1 ]] || {
    echo "Usage: $0 [--publish-github-secret]" >&2
    exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel)"
schema="$repo_root/.github/skill-eval/postgres-gpu-leases.sql"
bundle_dir="$state_dir/bundle"
secret_dir="$state_dir/secrets"
legacy_inventory="$bundle_dir/legacy-inventory.json"
ssh_options=(-o BatchMode=yes -o ControlMaster=no -o ControlPath=none)

for required in \
    "$schema" \
    "$legacy_inventory" \
    "$secret_dir/postgres-password" \
    "$secret_dir/lease-role-password" \
    "$secret_dir/fence-role-password"; do
    [[ -s "$required" ]] || { echo "Missing secure deployment state: $required" >&2; exit 1; }
done

cluster_json="$bundle_dir/patroni-cluster.json"
ssh "${ssh_options[@]}" vss-skill-validator-distributed-1 \
    "sudo -u postgres patronictl -c /etc/vss-postgres-ha/patroni.yml list --format json" \
    >"$cluster_json"
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
postgres_password="$(<"$secret_dir/postgres-password")"

migration_sql="$bundle_dir/legacy-inventory.sql"
python3 - "$legacy_inventory" "$migration_sql" <<'PY'
import json
import pathlib
import re
import sys

inventory = json.load(open(sys.argv[1], encoding="utf-8"))


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


statements = ["-- Preserve monotonic generations from the inactive local DB."]
for worker in inventory:
    gpu_id = worker["gpu_id"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", gpu_id):
        raise SystemExit(f"invalid legacy gpu_id: {gpu_id!r}")
    if worker.get("live"):
        raise SystemExit(f"refusing to migrate a live lease: {gpu_id}")
    generation = int(worker["generation"])
    if generation < 0:
        raise SystemExit(f"invalid generation for {gpu_id}")
    enabled = "true" if worker.get("enabled") else "false"
    metadata = json.dumps(worker.get("metadata", {}), separators=(",", ":"))
    statements.extend(
        [
            (
                "INSERT INTO public.gpu_workers (gpu_id, enabled, metadata) "
                f"VALUES ({quote(gpu_id)}, {enabled}, {quote(metadata)}::jsonb) "
                "ON CONFLICT (gpu_id) DO UPDATE SET "
                "enabled = EXCLUDED.enabled, metadata = EXCLUDED.metadata, "
                "updated_at = statement_timestamp();"
            ),
            (
                "UPDATE public.gpu_leases SET "
                f"generation = GREATEST(generation, {generation}), "
                "owner_id = NULL, lease_token = NULL, acquired_at = NULL, "
                "renewed_at = statement_timestamp(), "
                "lease_expires_at = statement_timestamp() "
                f"WHERE gpu_id = {quote(gpu_id)};"
            ),
        ]
    )
pathlib.Path(sys.argv[2]).write_text("\n".join(statements) + "\n", encoding="utf-8")
PY
chmod 0600 "$migration_sql"

bootstrap_sql="$bundle_dir/bootstrap-lease-database.sql"
cat >"$bootstrap_sql" <<EOF
\set ON_ERROR_STOP on
DO \$bootstrap\$
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
    EXECUTE format('ALTER ROLE skill_eval_lease LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', '${lease_password}');
    EXECUTE format('ALTER ROLE skill_eval_fence LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', '${fence_password}');
END
\$bootstrap\$;
SELECT 'CREATE DATABASE eval OWNER skill_eval_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'eval')
\gexec
\connect eval
REVOKE CONNECT ON DATABASE eval FROM PUBLIC;
GRANT CONNECT ON DATABASE eval TO skill_eval_lease, skill_eval_fence;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET ROLE skill_eval_owner;
\ir postgres-gpu-leases.sql
\ir legacy-inventory.sql
RESET ROLE;
EOF
chmod 0600 "$bootstrap_sql"

remote_dir="/tmp/vss-postgres-ha-bootstrap"
ssh "${ssh_options[@]}" "$leader_host" \
    "rm -rf '$remote_dir' && mkdir -m 700 '$remote_dir'"
scp "${ssh_options[@]}" -q \
    "$bootstrap_sql" "$schema" "$migration_sql" "${leader_host}:${remote_dir}/"
if ssh "${ssh_options[@]}" "$leader_host" \
    "sudo chown -R postgres:postgres '$remote_dir' && sudo chmod 700 '$remote_dir' && sudo chmod 600 '$remote_dir/bootstrap-lease-database.sql' '$remote_dir/postgres-gpu-leases.sql' '$remote_dir/legacy-inventory.sql' && sudo -u postgres psql --no-psqlrc -f '$remote_dir/bootstrap-lease-database.sql'"; then
    ssh "${ssh_options[@]}" "$leader_host" "sudo rm -rf '$remote_dir'"
else
    ssh "${ssh_options[@]}" "$leader_host" "sudo rm -rf '$remote_dir'" || true
    exit 1
fi

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
    remote_dsn="/tmp/.vss-postgres-ha-validation-dsn"
    scp "${ssh_options[@]}" -q "$lease_dsn" "${host}:${remote_dsn}"
    ssh "${ssh_options[@]}" "$host" \
        "chmod 600 '$remote_dsn' && /home/ubuntu/eval-coordinator/venv/bin/python - '$remote_dsn' <<'PY'
import pathlib
import sys
import psycopg
dsn = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').strip()
with psycopg.connect(dsn, autocommit=True) as connection:
    with connection.cursor() as cursor:
        cursor.execute('SELECT NOT pg_is_in_recovery()')
        assert cursor.fetchone() == (True,)
PY
status=\$?
rm -f '$remote_dsn'
exit \$status"
    echo "DATABASE READY: $host"
done

if [[ "$publish_github_secret" == true ]]; then
    gh secret set GPU_LEASE_DATABASE_URL --repo "$repository" <"$lease_dsn"
fi

echo "Lease database finalized on $leader_host."
echo "Coordinator DSN: $lease_dsn"
echo "GPU fence DSN: $fence_dsn"
echo "Administrator DSN: $admin_dsn"
