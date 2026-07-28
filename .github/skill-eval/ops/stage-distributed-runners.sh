#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Stage 8 x 4 repository runners through Brev. This command registers runners
# but leaves every service disabled/stopped and omits the production workflow
# label. Pass --apply explicitly; the default is a read-only preflight.
set -euo pipefail

repository="${GITHUB_REPOSITORY:-NVIDIA-AI-Blueprints/video-search-and-summarization}"
coordinator_env_file="${COORDINATOR_ENV_FILE:-}"
brev_config_dir="${BREV_CONFIG_DIR:-$HOME/.brev}"
apply=false

usage() {
    echo "Usage: COORDINATOR_ENV_FILE=/secure/path/coordinator.env $0 [--apply]" >&2
}

while (($#)); do
    case "$1" in
        --apply) apply=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

hosts=()
for index in $(seq 1 8); do
    hosts+=("vss-skill-validator-distributed-${index}")
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
host_script="$script_dir/configure-dormant-runner-host.sh"
[[ -f "$host_script" ]] || { echo "Missing $host_script" >&2; exit 1; }
[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
    echo "Invalid GITHUB_REPOSITORY: $repository" >&2
    exit 2
}

command -v brev >/dev/null
command -v gh >/dev/null
command -v tar >/dev/null
gh auth status >/dev/null

echo "Preflighting all eight Brev coordinators before making changes..."
for host in "${hosts[@]}"; do
    brev exec "$host" "true" >/dev/null
    echo "READY: $host"
done

if [[ "$apply" != true ]]; then
    echo "Preflight only. Re-run with --apply to register 32 offline standby runners."
    exit 0
fi
[[ -n "$coordinator_env_file" && -r "$coordinator_env_file" ]] || {
    echo "COORDINATOR_ENV_FILE must name a readable protected environment file" >&2
    exit 2
}
brev_binary="$(command -v brev)"
[[ "$(basename -- "$brev_config_dir")" == ".brev" ]] || {
    echo "BREV_CONFIG_DIR must point to a directory named .brev" >&2
    exit 2
}
for required in \
    "$brev_config_dir/active_org.json" \
    "$brev_config_dir/brev.pem" \
    "$brev_config_dir/cloudflared" \
    "$brev_config_dir/credentials.json"; do
    [[ -r "$required" ]] || {
        echo "Missing required Brev runtime file: $required" >&2
        exit 2
    }
done

token_file="$(mktemp)"
brev_config_archive="$(mktemp)"
chmod 600 "$token_file" "$brev_config_archive"
tar -C "$(dirname -- "$brev_config_dir")" \
    -czf "$brev_config_archive" "$(basename -- "$brev_config_dir")"
trap 'rm -f "$token_file" "$brev_config_archive"' EXIT

remote_script="/tmp/configure-dormant-runner-host.sh"
remote_token="/tmp/.actions-runner-registration-token"
remote_env="/tmp/.eval-coordinator.env"
remote_brev="/tmp/.brev-client"
remote_brev_config="/tmp/.brev-config.tar.gz"
for host in "${hosts[@]}"; do
    gh api \
        --method POST \
        "repos/${repository}/actions/runners/registration-token" \
        --jq .token >"$token_file"
    [[ -s "$token_file" ]] || {
        echo "GitHub returned an empty registration token for $host" >&2
        exit 1
    }
    brev copy "$host_script" "${host}:${remote_script}"
    brev copy "$token_file" "${host}:${remote_token}"
    brev copy "$coordinator_env_file" "${host}:${remote_env}"
    brev copy "$brev_binary" "${host}:${remote_brev}"
    brev copy "$brev_config_archive" "${host}:${remote_brev_config}"
    brev exec "$host" \
        "chmod 700 '$remote_script' '$remote_brev' && chmod 600 '$remote_token' '$remote_env' '$remote_brev_config' && trap 'rm -f \"$remote_env\" \"$remote_brev\" \"$remote_brev_config\"' EXIT && '$remote_script' --token-file '$remote_token' --coordinator-env-file '$remote_env' --brev-binary '$remote_brev' --brev-config-archive '$remote_brev_config' --coordinator-id '$host' --repository '$repository' --runner-count 4"
done

expected_file="$(mktemp)"
runners_file="$(mktemp)"
trap 'rm -f "$token_file" "$brev_config_archive" "$expected_file" "$runners_file"' EXIT
for host in "${hosts[@]}"; do
    for index in $(seq 1 4); do
        echo "${host}-runner-${index}" >>"$expected_file"
    done
done
gh api --paginate "repos/${repository}/actions/runners?per_page=100" >"$runners_file"

python3 - "$expected_file" "$runners_file" <<'PY'
import json
import sys

expected = set(open(sys.argv[1], encoding="utf-8").read().splitlines())
raw = open(sys.argv[2], encoding="utf-8").read()
decoder = json.JSONDecoder()
pages = []
position = 0
while position < len(raw):
    while position < len(raw) and raw[position].isspace():
        position += 1
    if position == len(raw):
        break
    page, position = decoder.raw_decode(raw, position)
    pages.extend(page if isinstance(page, list) else [page])
runners = {
    runner["name"]: runner
    for page in pages
    for runner in page.get("runners", [])
}
missing = sorted(expected - runners.keys())
unsafe = []
for name in sorted(expected & runners.keys()):
    runner = runners[name]
    labels = {label["name"] for label in runner.get("labels", [])}
    if runner.get("status") != "offline":
        unsafe.append(f"{name}: status={runner.get('status')}")
    if "vss-skill-eval-standby" not in labels:
        unsafe.append(f"{name}: standby label missing")
    if {"vss-skill-eval-runner", "vss-skill-eval-postgres"} & labels:
        unsafe.append(f"{name}: legacy or PostgreSQL production label present")
if missing or unsafe:
    for item in missing:
        print(f"MISSING: {item}", file=sys.stderr)
    for item in unsafe:
        print(f"UNSAFE: {item}", file=sys.stderr)
    raise SystemExit(1)
print(f"Verified {len(expected)} runners: offline, standby-labeled, production label absent.")
PY
