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
install_root="${RUNNER_INSTALL_ROOT:-/home/ubuntu/actions-runners}"
coordinator_root="${COORDINATOR_ROOT:-/home/ubuntu/eval-coordinator}"
standby_label="${RUNNER_STANDBY_LABEL:-vss-skill-eval-standby}"
coordinator_id="${COORDINATOR_ID:-}"

usage() {
    echo "Usage: $0 --token-file PATH --coordinator-id NAME [--repository OWNER/REPO] [--runner-count 4]" >&2
}

while (($#)); do
    case "$1" in
        --token-file) token_file="${2:?missing token file}"; shift 2 ;;
        --repository) repository="${2:?missing repository}"; shift 2 ;;
        --runner-count) runner_count="${2:?missing runner count}"; shift 2 ;;
        --install-root) install_root="${2:?missing install root}"; shift 2 ;;
        --coordinator-id) coordinator_id="${2:?missing coordinator ID}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[[ -n "$token_file" && -f "$token_file" ]] || { usage; exit 2; }
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

mkdir -p "$install_root" "$coordinator_root"
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
COORDINATOR_ID=${coordinator_id}:runner-${index}
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

