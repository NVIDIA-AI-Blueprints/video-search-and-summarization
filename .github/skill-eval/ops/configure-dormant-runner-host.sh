#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Configure four repository runners on one CPU coordinator. Runners are
# registered with a quarantine label and their services remain disabled and
# stopped. This script never activates a runner.
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

usage() {
    echo "Usage: $0 --token-file PATH --coordinator-env-file PATH --brev-binary PATH --brev-config-archive PATH --coordinator-id NAME [--repository OWNER/REPO] [--runner-count 4]" >&2
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

chmod 600 "$token_file"
registration_token="$(<"$token_file")"
trap 'registration_token=""; rm -f "$token_file"' EXIT
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
python3 - "$coordinator_env_file" <<'PY'
import pathlib
import sys

values = {}
for raw_line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    values[name.strip()] = value.strip()

required = {
    "ANTHROPIC_API_KEY",
    "NGC_CLI_API_KEY",
}
missing = sorted(name for name in required if not values.get(name))
if missing:
    raise SystemExit(
        "coordinator environment is missing required values: " + ", ".join(missing)
    )
PY
install -m 600 "$coordinator_env_file" "$coordinator_root/.env"
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
                --replace \
                --disableupdate
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
    sudo systemctl disable --now "$service_name"
    if systemctl is-active --quiet "$service_name"; then
        echo "FATAL: $service_name is active after standby configuration" >&2
        exit 1
    fi
    echo "STANDBY: $runner_name ($service_name disabled and stopped)"
done

rm -f "$archive"
echo "Configured ${runner_count} dormant runners on ${coordinator_id}; none activated."

