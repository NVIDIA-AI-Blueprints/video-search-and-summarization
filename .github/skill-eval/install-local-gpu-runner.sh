#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Install one repository-scoped GitHub Actions runner directly on a GPU VM.
# The short-lived registration token is read from stdin and is never persisted.
# Example:
#   printf '%s\n' "$TOKEN" | sudo ./install-local-gpu-runner.sh \
#     --repo-url https://github.com/org/repo --runner-name gpu-1 \
#     --labels vss-skill-eval-gpu,gpu-nvidia-rtx-pro-6000-blackwell,gpu-count-2,gpu-node-pro-1 \
#     --local-instance gpu-1 --runner-version 2.336.0 \
#     --runner-sha256 <published-linux-x64-sha256> \
#     --nvidia-pin 580.105.08
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
job_hook="$script_dir/local-gpu-job-started.sh"
supervisor="$script_dir/local-gpu-runner-supervise.sh"
service_unit="$script_dir/local-gpu-runner.service"
repo_url=""
runner_name=""
runner_labels=""
local_instance=""
runner_version=""
runner_sha256=""
nvidia_pin=""
anthropic_base_url="https://inference-api.nvidia.com"
anthropic_model="aws/anthropic/bedrock-claude-opus-4-6"
runner_dir="/opt/actions-runner"
source_env="/root/.eval_env"
coordinator_env="/root/eval-coordinator/.env"

while (($#)); do
  case "$1" in
    --repo-url) repo_url="$2"; shift 2 ;;
    --runner-name) runner_name="$2"; shift 2 ;;
    --labels) runner_labels="$2"; shift 2 ;;
    --local-instance) local_instance="$2"; shift 2 ;;
    --runner-version) runner_version="$2"; shift 2 ;;
    --runner-sha256) runner_sha256="$2"; shift 2 ;;
    --nvidia-pin) nvidia_pin="$2"; shift 2 ;;
    --anthropic-base-url) anthropic_base_url="$2"; shift 2 ;;
    --anthropic-model) anthropic_model="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ((EUID != 0)); then
  echo "run as root" >&2
  exit 1
fi

for value in \
  repo_url runner_name runner_labels local_instance runner_version runner_sha256 \
  nvidia_pin
do
  if [[ -z "${!value}" ]]; then
    echo "missing --${value//_/-}" >&2
    exit 2
  fi
done
if [[ ! "$runner_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "--runner-sha256 must be a lowercase SHA-256 digest" >&2
  exit 2
fi
if [[ ! "$nvidia_pin" =~ ^[0-9]+([.][0-9]+){1,3}$ ]]; then
  echo "--nvidia-pin must be a numeric driver version" >&2
  exit 2
fi
for command in curl docker flock install nvidia-ctk nvidia-smi pgrep python3 \
  sha256sum systemctl tar
do
  command -v "$command" >/dev/null || {
    echo "missing required command: $command" >&2
    exit 1
  }
done
test -s "$job_hook" || {
  echo "missing job hook next to installer: $job_hook" >&2
  exit 1
}
test -s "$service_unit" || {
  echo "missing systemd unit next to installer: $service_unit" >&2
  exit 1
}
test -s "$supervisor" || {
  echo "missing supervisor next to installer: $supervisor" >&2
  exit 1
}

IFS= read -r registration_token
if [[ -z "$registration_token" ]]; then
  echo "missing registration token on stdin" >&2
  exit 2
fi

exec 9>/run/vss-skill-eval-gpu-runner-install.lock
flock -x 9

mapfile -t gpu_names < <(
  nvidia-smi --query-gpu=name --format=csv,noheader,nounits
)
gpu_count="${#gpu_names[@]}"
((gpu_count > 0)) || {
  echo "nvidia-smi reported no GPUs" >&2
  exit 1
}
case "${gpu_names[0]}" in
  *"RTX PRO 6000 Blackwell"*)
    expected_model_label="gpu-nvidia-rtx-pro-6000-blackwell"
    ;;
  *"GeForce RTX 4090"*)
    expected_model_label="gpu-nvidia-geforce-rtx-4090"
    ;;
  *)
    echo "unsupported local GPU model: ${gpu_names[0]}" >&2
    exit 1
    ;;
esac
for gpu_name in "${gpu_names[@]}"; do
  [[ "$gpu_name" == "${gpu_names[0]}" ]] || {
    echo "mixed GPU models are not supported: ${gpu_names[*]}" >&2
    exit 1
  }
done
label_set=",$runner_labels,"
for required_label in \
  vss-skill-eval-gpu "$expected_model_label" "gpu-count-$gpu_count"
do
  [[ "$label_set" == *",$required_label,"* ]] || {
    echo "runner labels must include $required_label" >&2
    exit 2
  }
done
[[ "$label_set" == *",gpu-node-"* ]] || {
  echo "runner labels must include a unique gpu-node-* label" >&2
  exit 2
}
nvidia-smi -L
docker info >/dev/null
test -s "$source_env"

mkdir -p "$(dirname "$coordinator_env")"
env_tmp="$(mktemp "$(dirname "$coordinator_env")/.env.XXXXXX")"
# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
  rm -f "$env_tmp"
  unset registration_token
}
trap cleanup EXIT
python3 - "$source_env" "$env_tmp" <<'PY'
import re
import sys
from pathlib import Path

allowed = {
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_DISABLE_THINKING",
    "HF_TOKEN",
    "IS_SANDBOX",
    "LLM_REMOTE_MODEL",
    "LLM_REMOTE_URL",
    "NGC_CLI_API_KEY",
    "NVIDIA_API_KEY",
    "RTSP_SAMPLE_URL",
    "VLM_REMOTE_MODEL",
    "VLM_REMOTE_URL",
}
source, target = map(Path, sys.argv[1:])
lines = []
for line in source.read_text(encoding="utf-8").splitlines():
    match = re.match(
        r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=",
        line,
    )
    if match and match.group(1) in allowed:
        lines.append(line)
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
chown root:root "$env_tmp"
chmod 0600 "$env_tmp"
{
  printf '\nexport ANTHROPIC_BASE_URL=%q\n' "$anthropic_base_url"
  printf 'export ANTHROPIC_MODEL=%q\n' "$anthropic_model"
  printf 'export IS_SANDBOX=1\n'
} >>"$env_tmp"
mv -f "$env_tmp" "$coordinator_env"
chmod 0600 "$coordinator_env"

if [[ -e "$runner_dir/.runner" ]]; then
  echo "$runner_dir is already configured; refusing to replace it" >&2
  exit 1
fi
mkdir -p "$runner_dir"
archive="$runner_dir/actions-runner.tar.gz"
curl --fail --location --retry 3 --output "$archive" \
  "https://github.com/actions/runner/releases/download/v${runner_version}/actions-runner-linux-x64-${runner_version}.tar.gz"
printf '%s  %s\n' "$runner_sha256" "$archive" | sha256sum --check --status
tar -xzf "$archive" -C "$runner_dir"
rm -f "$archive"

mkdir -p "$runner_dir/hooks" "$runner_dir/_diag"
install -m 0755 -o root -g root \
  "$job_hook" "$runner_dir/hooks/job-started.sh"

cat >"$runner_dir/.env" <<EOF
ACTIONS_RUNNER_HOOK_JOB_STARTED=$runner_dir/hooks/job-started.sh
BREV_INSTANCE=$local_instance
SKILL_EVAL_ENV_FILE=$coordinator_env
SKILL_EVAL_LOCAL_GPU_INSTANCE=$local_instance
SKILL_EVAL_NVIDIA_PIN=$nvidia_pin
EOF
chmod 0600 "$runner_dir/.env"

export RUNNER_ALLOW_RUNASROOT=1
(
  cd "$runner_dir"
  ./config.sh \
    --unattended \
    --url "$repo_url" \
    --token "$registration_token" \
    --name "$runner_name" \
    --labels "$runner_labels" \
    --work _work
)
unset registration_token

install -d -m 0755 -o root -g root /etc/vss-skill-eval
printf '%s\n' "$runner_name" >/etc/vss-skill-eval/direct-gpu-runner
chmod 0644 /etc/vss-skill-eval/direct-gpu-runner
rm -f /etc/vss-skill-eval/direct-gpu-runner.enabled

cat >"$runner_dir/launch.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$runner_dir"
exec env -i \
  HOME=/root \
  USER=root \
  LOGNAME=root \
  LANG=C.UTF-8 \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  RUNNER_ALLOW_RUNASROOT=1 \
  ./run.sh
EOF
chmod 0755 "$runner_dir/launch.sh"

install -m 0755 -o root -g root "$supervisor" "$runner_dir/supervise.sh"

install -m 0644 -o root -g root \
  "$service_unit" /etc/systemd/system/vss-skill-eval-gpu-runner.service
systemctl stop vss-skill-eval-gpu-runner.service 2>/dev/null || true
for pid in $(pgrep -f \
  "$runner_dir/(supervise[.]sh|run[.]sh|bin/Runner[.]Listener run)" || true)
do
  kill -TERM "$pid" 2>/dev/null || true
done
sleep 2
systemctl daemon-reload
systemctl enable --now vss-skill-eval-gpu-runner.service

for _ in $(seq 1 30); do
  if systemctl is-active --quiet vss-skill-eval-gpu-runner.service \
    && pgrep -f "$runner_dir/bin/Runner[.]Listener run" >/dev/null
  then
    echo "runner started: $runner_name"
    echo "runner is staged; create /etc/vss-skill-eval/direct-gpu-runner.enabled after legacy jobs drain"
    exit 0
  fi
  sleep 2
done

echo "runner listener failed to start" >&2
exit 1
