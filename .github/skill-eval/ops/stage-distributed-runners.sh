#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Stage 8 x 4 repository runners over direct SSH. Each host's standby services
# are stopped while credentials transit a root-only /run payload.
# Pass --apply explicitly; the default is a read-only preflight.
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

for command in brev gh python3 ssh tar; do
    command -v "$command" >/dev/null || {
        echo "Missing runner staging prerequisite: $command" >&2
        exit 1
    }
done
gh auth status >/dev/null

echo "Preflighting all eight coordinators before making changes..."
for host in "${hosts[@]}"; do
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
        "$host" \
        "sudo -n true && command -v flock >/dev/null && command -v pgrep >/dev/null" \
        >/dev/null
    echo "READY: $host"
done

if [[ "$apply" != true ]]; then
    echo "Preflight only. Re-run with --apply to register 32 online standby runners."
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

work_dir="$(mktemp -d)"
chmod 700 "$work_dir"
token_file="$work_dir/registration-token"
brev_config_archive="$work_dir/brev-config.tar.gz"
preexisting_runners="$work_dir/preexisting-runners.json"
expected_file="$work_dir/expected-runners"
runners_file="$work_dir/runners.json"
payload="$work_dir/payload"
archive="$work_dir/payload.tar.gz"
operation_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
remote_dir="/run/vss-runner-stage-${operation_id}"
remote_archive="$remote_dir/payload.tar.gz"
remote_pending_host=""
cleanup() {
    rm -rf "$work_dir"
    if [[ -n "$remote_pending_host" ]]; then
        ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none \
            -o ConnectTimeout=5 \
            "$remote_pending_host" \
            "sudo rm -rf '$remote_dir'" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT HUP INT TERM
touch "$token_file" "$brev_config_archive" "$preexisting_runners"
chmod 600 "$token_file" "$brev_config_archive" "$preexisting_runners"
tar -C "$(dirname -- "$brev_config_dir")" \
    -czf "$brev_config_archive" "$(basename -- "$brev_config_dir")"

gh api --paginate \
    "repos/${repository}/actions/runners?per_page=100" \
    >"$preexisting_runners"
python3 - "$preexisting_runners" <<'PY'
import json
import re
import sys

raw = open(sys.argv[1], encoding="utf-8").read()
decoder = json.JSONDecoder()
position = 0
unsafe = []
pattern = re.compile(r"^vss-skill-validator-distributed-[1-8]-runner-[1-4]$")
while position < len(raw):
    while position < len(raw) and raw[position].isspace():
        position += 1
    if position == len(raw):
        break
    page, position = decoder.raw_decode(raw, position)
    for runner in page.get("runners", []):
        if not pattern.fullmatch(runner.get("name", "")):
            continue
        labels = {label["name"] for label in runner.get("labels", [])}
        production = {
            "vss-skill-eval-canary",
            "vss-skill-eval-postgres",
            "vss-skill-eval-runner",
        } & labels
        if runner.get("busy"):
            unsafe.append(f"{runner['name']}: busy")
        if production:
            unsafe.append(
                f"{runner['name']}: scheduling labels present: {sorted(production)}"
            )
if unsafe:
    print("\n".join(unsafe), file=sys.stderr)
    raise SystemExit("refusing to restage runners that may accept work")
PY

for host in "${hosts[@]}"; do
    gh api --paginate \
        "repos/${repository}/actions/runners?per_page=100" \
        >"$work_dir/current-runners.json"
    python3 - "$host" "$work_dir/current-runners.json" <<'PY'
import json, re, sys
host = sys.argv[1]
raw = open(sys.argv[2], encoding="utf-8").read()
decoder = json.JSONDecoder()
position = 0
runners = []
while position < len(raw):
    while position < len(raw) and raw[position].isspace():
        position += 1
    if position == len(raw):
        break
    page, position = decoder.raw_decode(raw, position)
    runners.extend(
        runner
        for runner in page.get("runners", [])
        if re.fullmatch(re.escape(host) + r"-runner-[1-4]", runner.get("name", ""))
    )
for runner in runners:
    labels = {label["name"] for label in runner.get("labels", [])}
    if runner.get("busy") or labels & {
        "vss-skill-eval-canary",
        "vss-skill-eval-postgres",
        "vss-skill-eval-runner",
    }:
        raise SystemExit(f"runner became schedulable or busy: {runner['name']}")
PY
    gh api \
        --method POST \
        "repos/${repository}/actions/runners/registration-token" \
        --jq .token >"$token_file"
    [[ -s "$token_file" ]] || {
        echo "GitHub returned an empty registration token for $host" >&2
        exit 1
    }
    rm -rf "$payload" "$archive"
    install -d -m 0700 "$payload"
    install -m 0755 "$host_script" \
        "$payload/configure-dormant-runner-host.sh"
    install -m 0600 "$token_file" "$payload/registration-token"
    install -m 0600 "$coordinator_env_file" "$payload/coordinator.env"
    install -m 0755 "$brev_binary" "$payload/brev"
    install -m 0600 "$brev_config_archive" "$payload/brev-config.tar.gz"
    tar -C "$payload" -czf "$archive" .

    remote_pending_host="$host"
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$host" \
        "sudo rm -rf '$remote_dir' && sudo install -d -m 700 -o root -g root '$remote_dir' && sudo tee '$remote_archive' >/dev/null && sudo chmod 600 '$remote_archive'" \
        <"$archive"
    ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none "$host" \
        "sudo bash -s -- '$remote_dir' '$host' '$repository'" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
coordinator_id="$2"
repository="$3"
umask 077
install -d -m 0700 -o root -g root /run/vss-runner-stage-lock
exec 9>/run/vss-runner-stage-lock/lock
flock -x 9

discover_runner_units() {
    local loaded_units unit_files
    unit_files="$(
        systemctl list-unit-files \
            --type=service \
            --no-legend \
            'actions.runner.*.service'
    )" || {
        echo "Could not enumerate installed GitHub runner units" >&2
        return 1
    }
    loaded_units="$(
        systemctl list-units \
            --all \
            --type=service \
            --no-legend \
            'actions.runner.*.service'
    )" || {
        echo "Could not enumerate loaded GitHub runner units" >&2
        return 1
    }
    {
        for file in /home/ubuntu/actions-runners/runner-*/.service; do
            [[ -f "$file" ]] && cat "$file"
        done
        awk '{print $1}' <<<"$unit_files"
        awk '{print $1}' <<<"$loaded_units"
    } | awk 'NF' | sort -u
}

verify_runner_quiescence() {
    local active_state service
    for service in "$@"; do
        active_state="$(
            systemctl show "$service" --property ActiveState --value
        )" || {
            echo "Could not inspect runner service: $service" >&2
            return 1
        }
        if [[ "$active_state" != inactive && "$active_state" != failed ]]; then
            echo "Runner service is not quiescent: $service=$active_state" >&2
            return 1
        fi
    done
    local runner_processes
    if runner_processes="$(pgrep -af 'Runner[.](Listener|Worker)')"; then
        echo "A GitHub runner process survived service shutdown" >&2
        printf '%s\n' "$runner_processes" >&2
        return 1
    elif [[ "$?" -ne 1 ]]; then
        echo "Could not inspect GitHub runner processes" >&2
        return 1
    fi
}

success=false
runner_units=()
final_units=()
cleanup_remote() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -rf "$remote_dir"
    if [[ "$success" != true ]]; then
        ((status != 0)) || status=1
        cleanup_units=()
        cleanup_units_text="$(discover_runner_units 2>/dev/null)" || cleanup_units_text=""
        if [[ -n "$cleanup_units_text" ]]; then
            mapfile -t cleanup_units <<<"$cleanup_units_text"
        fi
        for service in \
            "${runner_units[@]}" \
            "${final_units[@]}" \
            "${cleanup_units[@]}"; do
            [[ -n "$service" ]] || continue
            systemctl mask --runtime --now "$service" >/dev/null 2>&1 || true
        done
    fi
    exit "$status"
}
trap cleanup_remote EXIT HUP INT TERM

runner_units_text="$(discover_runner_units)" || exit 1
if [[ -n "$runner_units_text" ]]; then
    mapfile -t runner_units <<<"$runner_units_text"
fi
for service in "${runner_units[@]}"; do
    systemctl mask --runtime --now "$service"
done
verify_runner_quiescence "${runner_units[@]}"

mapfile -t tracked_units < <(
    for file in /home/ubuntu/actions-runners/runner-*/.service; do
        [[ -f "$file" ]] && cat "$file"
    done | awk 'NF' | sort -u
)
if [[ "${#tracked_units[@]}" -ne 0 && "${#tracked_units[@]}" -ne 4 ]]; then
    echo "Expected zero or four tracked runner services" >&2
    exit 1
fi
for service in "${runner_units[@]}"; do
    tracked=false
    for expected in "${tracked_units[@]}"; do
        [[ "$service" == "$expected" ]] && tracked=true
    done
    [[ "$tracked" == true ]] || {
        echo "Refusing to expose credentials with an untracked runner unit: $service" >&2
        exit 1
    }
done

tar --no-same-owner -xzf "$remote_dir/payload.tar.gz" -C "$remote_dir"
rm -f "$remote_dir/payload.tar.gz"
verify_runner_quiescence "${runner_units[@]}"
chown root:ubuntu "$remote_dir"
chmod 0710 "$remote_dir"
chown root:ubuntu \
    "$remote_dir/registration-token" \
    "$remote_dir/coordinator.env" \
    "$remote_dir/brev-config.tar.gz"
chmod 0640 \
    "$remote_dir/registration-token" \
    "$remote_dir/coordinator.env" \
    "$remote_dir/brev-config.tar.gz"
chmod 0755 \
    "$remote_dir/configure-dormant-runner-host.sh" \
    "$remote_dir/brev"
sudo -H -u ubuntu bash "$remote_dir/configure-dormant-runner-host.sh" \
    --token-file "$remote_dir/registration-token" \
    --coordinator-env-file "$remote_dir/coordinator.env" \
    --brev-binary "$remote_dir/brev" \
    --brev-config-archive "$remote_dir/brev-config.tar.gz" \
    --coordinator-id "$coordinator_id" \
    --repository "$repository" \
    --runner-count 4 \
    --defer-start

mapfile -t final_units < <(
    for file in /home/ubuntu/actions-runners/runner-*/.service; do
        [[ -f "$file" ]] && cat "$file"
    done | awk 'NF' | sort -u
)
[[ "${#final_units[@]}" -eq 4 ]] || {
    echo "Runner configuration did not produce exactly four services" >&2
    exit 1
}
all_units_after=()
all_units_after_text="$(discover_runner_units)" || exit 1
if [[ -n "$all_units_after_text" ]]; then
    mapfile -t all_units_after <<<"$all_units_after_text"
fi
[[ "${all_units_after[*]}" == "${final_units[*]}" ]] || {
    echo "Untracked GitHub runner unit exists after staging" >&2
    exit 1
}
verify_runner_quiescence "${final_units[@]}"

rm -rf "$remote_dir"
for service in "${final_units[@]}"; do
    systemctl unmask --runtime "$service"
    systemctl enable --now "$service"
    systemctl is-active --quiet "$service"
done
success=true
trap - EXIT HUP INT TERM
REMOTE
    remote_pending_host=""
done

touch "$expected_file" "$runners_file"
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
    if runner.get("status") != "online":
        unsafe.append(f"{name}: status={runner.get('status')}")
    if runner.get("busy"):
        unsafe.append(f"{name}: runner is unexpectedly busy")
    if "vss-skill-eval-standby" not in labels:
        unsafe.append(f"{name}: standby label missing")
    if {
        "vss-skill-eval-canary",
        "vss-skill-eval-runner",
        "vss-skill-eval-postgres",
    } & labels:
        unsafe.append(f"{name}: scheduling label present")
if missing or unsafe:
    for item in missing:
        print(f"MISSING: {item}", file=sys.stderr)
    for item in unsafe:
        print(f"UNSAFE: {item}", file=sys.stderr)
    raise SystemExit(1)
print(f"Verified {len(expected)} runners: online, idle, standby-labeled, production label absent.")
PY
