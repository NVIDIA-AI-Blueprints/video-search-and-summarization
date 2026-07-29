#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Fence and capture the retired single-node lease database, or prove that no
# such database exists. Inventory JSON is the only stdout output.
set -euo pipefail
umask 077

mode="${1:-}"
[[ "$mode" == "drained" || "$mode" == "absent" ]] || {
    echo "Usage: $0 drained|absent" >&2
    exit 2
}
[[ "$(id -u)" -eq 0 ]] || {
    echo "Legacy inventory capture must run as root" >&2
    exit 2
}

database_name="skill_eval_leases"
no_postgres_cluster_or_data() {
    if command -v pg_lsclusters >/dev/null &&
       pg_lsclusters --no-header 2>/dev/null |
           awk 'NF {found=1} END {exit !found}'; then
        return 1
    fi
    if compgen -G "/var/lib/postgresql/*" >/dev/null; then
        return 1
    fi
    return 0
}

if ! command -v psql >/dev/null; then
    if [[ "$mode" == "absent" ]] && no_postgres_cluster_or_data; then
        printf '[]\n'
        exit 0
    fi
    echo "PostgreSQL client is unavailable; database absence is not proven" >&2
    exit 1
fi

if ! runuser -u postgres -- psql \
    --no-psqlrc \
    --dbname postgres \
    --tuples-only \
    --no-align \
    --command "SELECT 1" >/dev/null 2>&1; then
    if [[ "$mode" == "absent" ]] && no_postgres_cluster_or_data; then
        printf '[]\n'
        exit 0
    fi
    echo "PostgreSQL is unreachable; database absence is not proven" >&2
    exit 1
fi

database_exists="$(
    runuser -u postgres -- psql \
        --no-psqlrc \
        --dbname postgres \
        --tuples-only \
        --no-align \
        --command "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname = '${database_name}')"
)"
case "$database_exists" in
    t)
        if [[ "$mode" == "absent" ]]; then
            echo "Legacy database ${database_name} still exists" >&2
            exit 1
        fi
        ;;
    f)
        if [[ "$mode" == "drained" ]]; then
            echo "Legacy database ${database_name} does not exist" >&2
            exit 1
        fi
        printf '[]\n'
        exit 0
        ;;
    *)
        echo "Could not determine whether the legacy database exists" >&2
        exit 1
        ;;
esac

# Commit admission fencing before terminating sessions. CONNECTION LIMIT 0
# blocks new non-superuser clients even if a role has an explicit CONNECT
# grant; terminating existing clients closes the remaining acquisition race.
runuser -u postgres -- psql \
    --no-psqlrc \
    --dbname postgres \
    --set ON_ERROR_STOP=1 \
    --command "ALTER DATABASE ${database_name} CONNECTION LIMIT 0" >/dev/null
runuser -u postgres -- psql \
    --no-psqlrc \
    --dbname postgres \
    --set ON_ERROR_STOP=1 \
    --command "REVOKE CONNECT ON DATABASE ${database_name} FROM PUBLIC" >/dev/null
runuser -u postgres -- psql \
    --no-psqlrc \
    --dbname postgres \
    --set ON_ERROR_STOP=1 \
    --command "SELECT pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity WHERE datname = '${database_name}' AND pid <> pg_backend_pid()" \
    >/dev/null

remaining_clients="$(
    runuser -u postgres -- psql \
        --no-psqlrc \
        --dbname postgres \
        --tuples-only \
        --no-align \
        --command "SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE datname = '${database_name}' AND usename <> 'postgres'"
)"
[[ "$remaining_clients" == "0" ]] || {
    echo "Legacy database still has non-administrator sessions" >&2
    exit 1
}

runuser -u postgres -- psql \
    --no-psqlrc \
    --dbname "$database_name" \
    --tuples-only \
    --no-align \
    --set ON_ERROR_STOP=1 \
    --command "SELECT COALESCE(jsonb_agg(jsonb_build_object('gpu_id', w.gpu_id, 'enabled', w.enabled, 'metadata', w.metadata, 'generation', l.generation, 'live', l.owner_id IS NOT NULL OR l.lease_token IS NOT NULL OR l.lease_expires_at > statement_timestamp()) ORDER BY w.gpu_id), '[]'::jsonb) FROM public.gpu_workers AS w JOIN public.gpu_leases AS l USING (gpu_id)"
