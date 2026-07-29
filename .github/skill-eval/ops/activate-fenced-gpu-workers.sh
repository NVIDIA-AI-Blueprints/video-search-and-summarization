#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Enable only GPU workers whose local PostgreSQL fence is installed and healthy.
set -euo pipefail
umask 077

apply=false
confirm_drained=false
for argument in "$@"; do
    case "$argument" in
        --apply) apply=true ;;
        --confirm-drained) confirm_drained=true ;;
        *)
            echo "Unknown argument: $argument" >&2
            exit 2
            ;;
    esac
done

workers_text="${GPU_WORKERS:-}"
admin_dsn_file="${GPU_LEASE_ADMIN_DATABASE_URL_FILE:-}"
for command in base64 python3 ssh; do
    command -v "$command" >/dev/null || {
        echo "Missing GPU activation prerequisite: $command" >&2
        exit 1
    }
done
[[ -n "$workers_text" ]] || {
    echo "Set GPU_WORKERS to the reviewed fenced-worker inventory" >&2
    exit 2
}
read -r -a workers <<<"$(tr '\n,' '  ' <<<"$workers_text")"
[[ ${#workers[@]} -gt 0 ]] || {
    echo "No GPU workers selected" >&2
    exit 2
}
[[ -f "$admin_dsn_file" && "$(stat -c '%a' "$admin_dsn_file")" == "600" ]] || {
    echo "Set GPU_LEASE_ADMIN_DATABASE_URL_FILE to a mode-0600 admin DSN file" >&2
    exit 2
}

declare -A seen=()
for worker in "${workers[@]}"; do
    [[ "$worker" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || {
        echo "Invalid GPU worker name: $worker" >&2
        exit 2
    }
    [[ -z "${seen[$worker]:-}" ]] || {
        echo "Duplicate GPU worker: $worker" >&2
        exit 2
    }
    seen["$worker"]=1
    status="$(
        ssh \
            -o BatchMode=yes \
            -o ConnectTimeout=15 \
            -o ControlMaster=no \
            -o ControlPath=none \
            "$worker" \
            "sudo -n /usr/local/bin/vss-gpu-fence status"
    )"
    python3 - "$worker" "$status" <<'PY'
import json
import sys

worker = sys.argv[1]
status = json.loads(sys.argv[2])
if status.get("version") != "1" or status.get("gpu_id") != worker:
    raise SystemExit(f"wrong fence identity for {worker}")
if not status.get("ok") or status.get("blocked") or status.get("active"):
    raise SystemExit(f"worker is not idle and healthy: {worker}")
PY
    containers="$(
        ssh \
            -o BatchMode=yes \
            -o ConnectTimeout=15 \
            -o ControlMaster=no \
            -o ControlPath=none \
            "$worker" \
            "sudo -n docker ps -aq"
    )"
    [[ -z "$containers" ]] || {
        echo "Worker is not dedicated and drained; containers remain: $worker" >&2
        exit 1
    }
    echo "FENCED, DEDICATED, AND IDLE: $worker"
done

python3 - "$admin_dsn_file" <<'PY'
import pathlib
import sys
import urllib.parse

dsn = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
parsed = urllib.parse.urlsplit(dsn)
query = urllib.parse.parse_qs(parsed.query)
if parsed.scheme not in {"postgres", "postgresql"}:
    raise SystemExit("admin DSN must use postgresql://")
if query.get("sslmode", [""])[0] != "verify-full":
    raise SystemExit("admin DSN must use sslmode=verify-full")
if query.get("target_session_attrs", [""])[0] != "read-write":
    raise SystemExit("admin DSN must require a read-write primary")
PY

if [[ "$apply" != true ]]; then
    echo "Preflight only. Re-run with --apply --confirm-drained."
    exit 0
fi
[[ "$confirm_drained" == true ]] || {
    echo "--confirm-drained is required before changing scheduler inventory" >&2
    exit 2
}

workers_json="$(
    printf '%s\n' "${workers[@]}" |
        python3 -c 'import json,sys; print(json.dumps([line.rstrip() for line in sys.stdin if line.rstrip()]))'
)"
python_code="$(
    cat <<'PY'
import json
import sys

import psycopg

workers = json.loads(sys.stdin.readline())
dsn = sys.stdin.read().strip()
connection = psycopg.connect(dsn)
cursor = connection.cursor()
cursor.executemany(
    """
    INSERT INTO public.gpu_workers AS existing (
        gpu_id,
        enabled,
        fence_ready,
        fence_version,
        fence_attested_at,
        metadata
    )
    VALUES (
        %s,
        true,
        true,
        '1',
        statement_timestamp(),
        '{"fence":"v1","dedicated_worker":true}'::jsonb
    )
    ON CONFLICT (gpu_id) DO UPDATE
    SET enabled = true,
        fence_ready = true,
        fence_version = '1',
        fence_attested_at = statement_timestamp(),
        metadata = existing.metadata || EXCLUDED.metadata,
        updated_at = statement_timestamp()
    """,
    [(worker,) for worker in workers],
)
connection.commit()
cursor.execute(
    """
    SELECT gpu_id, enabled, fence_ready, fence_version, metadata
    FROM public.gpu_workers
    WHERE gpu_id = ANY(%s)
    ORDER BY gpu_id
    """,
    (workers,),
)
rows = cursor.fetchall()
cursor.close()
connection.close()
if (
    len(rows) != len(workers)
    or any(
        not enabled
        or not fence_ready
        or version != "1"
        or metadata.get("dedicated_worker") is not True
        for _, enabled, fence_ready, version, metadata in rows
    )
):
    raise SystemExit("database did not enable the complete fenced inventory")
print(json.dumps(rows))
PY
)"
python_code_b64="$(printf '%s' "$python_code" | base64 -w0)"
{
    printf '%s\n' "$workers_json"
    cat "$admin_dsn_file"
} | ssh \
    -o BatchMode=yes \
    -o ControlMaster=no \
    -o ControlPath=none \
    vss-skill-validator-distributed-1 \
    "/home/ubuntu/eval-coordinator/venv/bin/python -c \"import base64; exec(base64.b64decode('$python_code_b64'))\""

echo "Enabled ${#workers[@]} healthy fenced GPU workers."
