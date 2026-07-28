#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Install the generation-validating watchdog on one dedicated GPU worker.
set -euo pipefail
umask 077

gpu_id=""
database_url_file=""
eval_user="$(id -un)"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gpu-id)
            gpu_id="${2:-}"
            shift 2
            ;;
        --database-url-file)
            database_url_file="${2:-}"
            shift 2
            ;;
        --eval-user)
            eval_user="${2:-}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

[[ "$gpu_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || {
    echo "Invalid --gpu-id" >&2
    exit 2
}
[[ "$eval_user" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,31}$ ]] || {
    echo "Invalid --eval-user" >&2
    exit 2
}
id "$eval_user" >/dev/null
[[ -f "$database_url_file" ]] || {
    echo "Missing --database-url-file" >&2
    exit 2
}
[[ -f "$script_dir/gpu_fence.py" ]] || {
    echo "Missing $script_dir/gpu_fence.py" >&2
    exit 1
}
[[ -f "$script_dir/vss-gpu-fence.service" ]] || {
    echo "Missing $script_dir/vss-gpu-fence.service" >&2
    exit 1
}

cleanup_secret() {
    if command -v shred >/dev/null 2>&1; then
        shred -u "$database_url_file" 2>/dev/null || rm -f "$database_url_file"
    else
        rm -f "$database_url_file"
    fi
    [[ -z "${config_tmp:-}" ]] || rm -f "$config_tmp"
}
trap cleanup_secret EXIT

python3 - "$database_url_file" <<'PY'
import sys
import urllib.parse

dsn = open(sys.argv[1], encoding="utf-8").read().strip()
parsed = urllib.parse.urlsplit(dsn)
query = urllib.parse.parse_qs(parsed.query)
sslmode = query.get("sslmode", [""])[0]
if parsed.scheme not in {"postgres", "postgresql"}:
    raise SystemExit("GPU fence DSN must use postgresql://")
if not parsed.hostname or not parsed.path.strip("/") or not parsed.username:
    raise SystemExit("GPU fence DSN requires host, database, and user")
if sslmode != "verify-full":
    raise SystemExit("GPU fence DSN must use sslmode=verify-full")
PY

sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv
sudo install -d -m 0755 /opt/vss-gpu-fence
sudo install -m 0755 "$script_dir/gpu_fence.py" /opt/vss-gpu-fence/gpu_fence.py
if [[ ! -x /opt/vss-gpu-fence/venv/bin/python ]]; then
    sudo python3 -m venv /opt/vss-gpu-fence/venv
fi
sudo /opt/vss-gpu-fence/venv/bin/python -m pip install -q \
    'psycopg[binary]>=3.2,<4'

sudo tee /usr/local/bin/vss-gpu-fence >/dev/null <<'EOF'
#!/usr/bin/env sh
exec /opt/vss-gpu-fence/venv/bin/python /opt/vss-gpu-fence/gpu_fence.py "$@"
EOF
sudo chmod 0755 /usr/local/bin/vss-gpu-fence

config_tmp="$(mktemp)"
chmod 0600 "$config_tmp"
python3 - "$database_url_file" "$gpu_id" >"$config_tmp" <<'PY'
import shlex
import sys

dsn = open(sys.argv[1], encoding="utf-8").read().strip()
print(f"GPU_FENCE_DATABASE_URL={shlex.quote(dsn)}")
print(f"GPU_FENCE_GPU_ID={shlex.quote(sys.argv[2])}")
PY
sudo install -m 0600 -o root -g root \
    "$config_tmp" /etc/vss-gpu-fence.env
rm -f "$config_tmp"
config_tmp=""

sudo install -m 0644 "$script_dir/vss-gpu-fence.service" \
    /etc/systemd/system/vss-gpu-fence.service
sudo tee /etc/sudoers.d/vss-gpu-fence >/dev/null <<EOF
${eval_user} ALL=(root) NOPASSWD: /usr/local/bin/vss-gpu-fence claim *
${eval_user} ALL=(root) NOPASSWD: /usr/local/bin/vss-gpu-fence exec *
${eval_user} ALL=(root) NOPASSWD: /usr/local/bin/vss-gpu-fence status
EOF
sudo chmod 0440 /etc/sudoers.d/vss-gpu-fence
sudo visudo -cf /etc/sudoers.d/vss-gpu-fence >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable vss-gpu-fence.service
sudo systemctl restart vss-gpu-fence.service
ready=false
for _ in $(seq 1 30); do
    if sudo systemctl is-active --quiet vss-gpu-fence.service &&
       status_json="$(sudo /usr/local/bin/vss-gpu-fence status 2>/dev/null)" &&
       python3 - "$gpu_id" "$status_json" >/dev/null 2>&1 <<'PY'
import json
import sys

status = json.loads(sys.argv[2])
if status.get("gpu_id") != sys.argv[1]:
    raise SystemExit("GPU fence status returned wrong gpu_id")
if status.get("version") != "1":
    raise SystemExit("GPU fence status returned wrong version")
if status.get("blocked"):
    raise SystemExit(f"GPU fence is blocked: {status.get('blocked_reason')}")
PY
    then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    sudo systemctl status --no-pager vss-gpu-fence.service >&2 || true
    sudo journalctl -u vss-gpu-fence.service -n 50 --no-pager >&2 || true
    echo "GPU fence did not become ready within 30 seconds" >&2
    exit 1
fi
echo "GPU fence active: gpu_id=$gpu_id eval_user=$eval_user"
