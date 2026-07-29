#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Remove the temporary --disableupdate policy from the 32 staged runners.
set -euo pipefail

[[ "${1:-}" == "--apply" && $# -eq 1 ]] || {
    echo "Usage: $0 --apply" >&2
    exit 2
}
repository="${GITHUB_REPOSITORY:-NVIDIA-AI-Blueprints/video-search-and-summarization}"
install_root="${RUNNER_INSTALL_ROOT:-/home/ubuntu/actions-runners}"
ssh_options=(-o BatchMode=yes -o ControlMaster=no -o ControlPath=none)

command -v gh >/dev/null
gh auth status >/dev/null

assert_runner_safe() {
    local runner_name="$1"
    local state
    local busy
    local status
    local labels
    state="$(
        gh api \
            --paginate \
            "repos/${repository}/actions/runners" \
            --jq ".runners[] | select(.name == \"${runner_name}\") | [.busy,.status,([.labels[].name]|join(\",\"))] | @tsv"
    )"
    [[ -n "$state" && "$(printf '%s\n' "$state" | wc -l)" -eq 1 ]] || {
        echo "Expected exactly one registered runner: $runner_name" >&2
        return 1
    }
    IFS=$'\t' read -r busy status labels <<<"$state"
    [[ "$busy" == "false" && "$status" == "online" ]] || {
        echo "Runner is not online and idle: $runner_name ($status, busy=$busy)" >&2
        return 1
    }
    case ",$labels," in
        *,vss-skill-eval-canary,*|*,vss-skill-eval-postgres,*|*,vss-skill-eval-runner,*)
            echo "Runner has a scheduling label: $runner_name ($labels)" >&2
            return 1
            ;;
    esac
}

for host_index in $(seq 1 8); do
    host="vss-skill-validator-distributed-${host_index}"
    for runner_index in $(seq 1 4); do
        runner_dir="$install_root/runner-${runner_index}"
        runner_name="${host}-runner-${runner_index}"
        assert_runner_safe "$runner_name"
        # runner_dir is locally constructed from a fixed numeric index.
        # shellcheck disable=SC2029
        ssh "${ssh_options[@]}" "$host" \
            "set -euo pipefail
changed=\$(python3 - '$runner_dir/.runner' <<'PY'
import json
import os
import pathlib
import tempfile
import sys

path = pathlib.Path(sys.argv[1])
configuration = json.loads(path.read_text(encoding='utf-8-sig'))
if configuration.get('disableUpdate') is False:
    print('false')
    raise SystemExit(0)
configuration['disableUpdate'] = False
descriptor, temporary = tempfile.mkstemp(
    dir=str(path.parent),
    prefix='.runner.',
)
try:
    with os.fdopen(descriptor, 'w', encoding='utf-8-sig') as output:
        json.dump(configuration, output, separators=(',', ':'))
        output.write('\\n')
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print('true')
PY
)
service=\$(< '$runner_dir/.service')
if [[ \"\$changed\" == true ]]; then
    sudo systemctl restart \"\$service\"
fi
for _ in \$(seq 1 30); do
    sudo systemctl is-active --quiet \"\$service\" && exit 0
    sleep 1
done
exit 1"
        for _ in $(seq 1 30); do
            if assert_runner_safe "$runner_name"; then
                break
            fi
            sleep 2
        done
        assert_runner_safe "$runner_name"
        echo "AUTO-UPDATE: $runner_name"
    done
done

online_count=0
for _ in $(seq 1 30); do
    online_count="$(
        gh api \
            --paginate \
            "repos/${repository}/actions/runners" \
            --jq '[.runners[] | select(.name | test("^vss-skill-validator-distributed-[1-8]-runner-[1-4]$")) | select(.status == "online")] | length' |
            awk '{sum += $1} END {print sum+0}'
    )"
    [[ "$online_count" == "32" ]] && break
    sleep 2
done
[[ "$online_count" == "32" ]] || {
    echo "Expected 32 online distributed runners; observed $online_count" >&2
    exit 1
}
echo "All 32 distributed runners are online with automatic updates enabled."
