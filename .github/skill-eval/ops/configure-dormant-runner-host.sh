#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Configure four repository runners on one CPU coordinator. Runners stay
# online with a standby-only label that no production workflow selects.
set -euo pipefail

repository="${GITHUB_REPOSITORY:-NVIDIA-AI-Blueprints/video-search-and-summarization}"
runner_count="${RUNNER_COUNT:-4}"
token_file=""
coordinator_env_file=""
brev_binary=""
brev_config_archive=""
install_root="${RUNNER_INSTALL_ROOT:-/home/ubuntu/actions-runners}"
coordinator_root="${COORDINATOR_ROOT:-/home/ubuntu/eval-coordinator}"
standby_label="${RUNNER_STANDBY_LABEL:-vss-skill-eval-standby}"
coordinator_id="${COORDINATOR_ID:-}"
defer_start=false

usage() {
    echo "Usage: $0 --token-file PATH --coordinator-env-file PATH --brev-binary PATH --brev-config-archive PATH --coordinator-id NAME [--repository OWNER/REPO] [--runner-count 4] [--defer-start]" >&2
}

while (($#)); do
    case "$1" in
        --token-file) token_file="${2:?missing token file}"; shift 2 ;;
        --coordinator-env-file) coordinator_env_file="${2:?missing coordinator environment file}"; shift 2 ;;
        --brev-binary) brev_binary="${2:?missing Brev binary}"; shift 2 ;;
        --brev-config-archive) brev_config_archive="${2:?missing Brev configuration archive}"; shift 2 ;;
        --repository) repository="${2:?missing repository}"; shift 2 ;;
        --runner-count) runner_count="${2:?missing runner count}"; shift 2 ;;
        --install-root) install_root="${2:?missing install root}"; shift 2 ;;
        --coordinator-id) coordinator_id="${2:?missing coordinator ID}"; shift 2 ;;
        --defer-start) defer_start=true; shift ;;
        *) usage; exit 2 ;;
    esac
done

[[ -n "$token_file" && -f "$token_file" ]] || { usage; exit 2; }
[[ -n "$coordinator_env_file" && -r "$coordinator_env_file" ]] || {
    echo "Coordinator environment file is missing or unreadable" >&2
    usage
    exit 2
}
[[ -n "$brev_binary" && -r "$brev_binary" ]] || {
    echo "Brev binary is missing or unreadable" >&2
    usage
    exit 2
}
[[ -n "$brev_config_archive" && -r "$brev_config_archive" ]] || {
    echo "Brev configuration archive is missing or unreadable" >&2
    usage
    exit 2
}
[[ "$runner_count" =~ ^[1-9][0-9]*$ ]] || { echo "runner count must be positive" >&2; exit 2; }
[[ "$(id -u)" -ne 0 ]] || {
    echo "Run as the coordinator user, not root (the script uses sudo where needed)." >&2
    exit 2
}

[[ -n "$coordinator_id" ]] || coordinator_id="$(hostname -s)"
case "$coordinator_id" in
    vss-skill-validator-distributed-[1-8]) ;;
    *)
        echo "Refusing unexpected coordinator ID: $coordinator_id" >&2
        exit 2
        ;;
esac

registration_token="$(<"$token_file")"
trap 'registration_token=""; rm -f "$token_file" "${validated_env:-}" 2>/dev/null || true' EXIT
[[ -n "$registration_token" ]] || { echo "registration token is empty" >&2; exit 2; }

sudo apt-get update -qq
sudo apt-get install -y -qq curl git jq python3 python3-venv tar

sudo install -m 0755 "$brev_binary" /usr/local/bin/brev
tar -xzf "$brev_config_archive" -C "$HOME"
[[ -d "$HOME/.brev" ]] || {
    echo "Brev configuration archive did not contain .brev/" >&2
    exit 1
}
chmod -R go-rwx "$HOME/.brev"
timeout 30 /usr/local/bin/brev ls --json >/dev/null

mkdir -p "$install_root" "$coordinator_root"
validated_env="$(mktemp)"
chmod 0600 "$validated_env"
python3 - "$coordinator_env_file" >"$validated_env" <<'PY'
import pathlib
import re
import shlex
import sys

allowed = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "BREV_REGISTERED_POOL",
    "BREV_RTX4090_POOL",
    "GIT_USER_EMAIL",
    "GIT_USER_NAME",
    "HF_TOKEN",
    "JUDGE_MODEL",
    "LLM_REMOTE_MODEL",
    "LLM_REMOTE_URL",
    "NGC_CLI_API_KEY",
    "NVIDIA_API_KEY",
    "SKILLS_EVAL_MODEL",
    "VLM_REMOTE_MODEL",
    "VLM_REMOTE_URL",
}
values = {}
for line_number, raw_line in enumerate(
    pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(),
    start=1,
):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
    if not match:
        raise SystemExit(
            f"unsupported coordinator environment syntax on line {line_number}"
        )
    name, raw_value = match.groups()
    if name not in allowed:
        raise SystemExit(f"unsupported coordinator environment variable: {name}")
    if name in values:
        raise SystemExit(f"duplicate coordinator environment variable: {name}")
    parsed = shlex.split(raw_value, comments=False, posix=True)
    if len(parsed) > 1:
        raise SystemExit(f"coordinator environment value must be one token: {name}")
    values[name] = parsed[0] if parsed else ""

required = {
    "ANTHROPIC_API_KEY",
    "NGC_CLI_API_KEY",
}
missing = sorted(name for name in required if not values.get(name))
if missing:
    raise SystemExit(
        "coordinator environment is missing required values: " + ", ".join(missing)
    )
for name, value in values.items():
    print(f"{name}={shlex.quote(value)}")
PY
sudo install -m 0640 -o root -g "$(id -gn)" \
    "$validated_env" "$coordinator_root/.env"
python3 -m venv "$coordinator_root/venv"
"$coordinator_root/venv/bin/python" -m pip install -q --upgrade pip
"$coordinator_root/venv/bin/python" -m pip install -q "psycopg[binary]>=3.2,<4"

release_json="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)"
readarray -t release_fields < <(
    python3 -c '
import json, sys
release = json.load(sys.stdin)
assets = [a for a in release["assets"] if a["name"].startswith("actions-runner-linux-x64-") and a["name"].endswith(".tar.gz")]
if len(assets) != 1:
    raise SystemExit(f"expected one linux-x64 runner asset, found {len(assets)}")
asset = assets[0]
digest = asset.get("digest") or ""
if not digest.startswith("sha256:"):
    raise SystemExit("GitHub release asset has no SHA-256 digest")
print(release["tag_name"].removeprefix("v"))
print(asset["browser_download_url"])
print(digest.removeprefix("sha256:"))
' <<<"$release_json"
)
runner_version="${release_fields[0]}"
archive_url="${release_fields[1]}"
archive_sha256="${release_fields[2]}"
archive="/tmp/actions-runner-linux-x64-${runner_version}.tar.gz"

curl -fL --retry 3 --output "$archive" "$archive_url"
printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum --check --status

for index in $(seq 1 "$runner_count"); do
    runner_name="${coordinator_id}-runner-${index}"
    runner_dir="${install_root}/runner-${index}"
    mkdir -p "$runner_dir"

    if [[ ! -x "$runner_dir/config.sh" ]]; then
        tar -xzf "$archive" -C "$runner_dir"
    fi

    if [[ ! -f "$runner_dir/.runner" ]]; then
        (
            cd "$runner_dir"
            ./config.sh \
                --url "https://github.com/${repository}" \
                --token "$registration_token" \
                --name "$runner_name" \
                --labels "${standby_label},${coordinator_id}" \
                --work "_work" \
                --unattended \
                --replace
        )
    fi

    cat >"$runner_dir/.env" <<EOF
COORDINATOR_ID=${runner_name}
GPU_LEASE_MODE=postgres
PATH=${coordinator_root}/venv/bin:/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin
EOF
    chmod 600 "$runner_dir/.env"

    if [[ ! -f "$runner_dir/.service" ]]; then
        (cd "$runner_dir" && sudo ./svc.sh install "$(id -un)")
    fi
    service_name="$(<"$runner_dir/.service")"
    if [[ "$defer_start" == false ]]; then
        sudo systemctl enable "$service_name"
        sudo systemctl start "$service_name"
        if ! systemctl is-active --quiet "$service_name"; then
            echo "FATAL: $service_name is inactive after standby configuration" >&2
            exit 1
        fi
        echo "STANDBY: $runner_name ($service_name online, standby-only)"
    else
        sudo systemctl mask --runtime "$service_name"
        echo "STAGED: $runner_name ($service_name remains stopped)"
    fi
done

rm -f "$archive"
if [[ "$defer_start" == true ]]; then
    echo "Configured ${runner_count} stopped standby runners on ${coordinator_id}."
else
    echo "Configured ${runner_count} online standby runners on ${coordinator_id}."
fi
