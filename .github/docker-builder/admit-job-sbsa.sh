#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Job-started hook for the *new* SBSA builder cohort (horde-sbsa-NN).
# Do not install this on horde-docker-0[1-4]: that pool keeps its own hook
# and continues to serve the amd64 vss-rt-vlm canary.
set -euo pipefail

deny() {
  echo "::error::SBSA builder rejected this job: $1"
  exit 1
}

readonly expected_repo="NVIDIA-AI-Blueprints/video-search-and-summarization"
readonly build_workflow="Build Dev Images (GHCR)"
readonly canary_workflow="SBSA builder canary"
readonly build_job="build-native-platforms"

[[ "${GITHUB_REPOSITORY:-}" == "$expected_repo" ]] ||
  deny "repository is not approved"
[[ "${RUNNER_NAME:-}" =~ ^horde-sbsa-[0-9]{2}$ ]] ||
  deny "runner is not an approved SBSA builder"

workflow="${GITHUB_WORKFLOW:-}"
job="${GITHUB_JOB:-}"

if [[ "$workflow" == "$canary_workflow" ]]; then
  [[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]] ||
    deny "canary is workflow_dispatch only"
  [[ "$job" == "preflight" ]] || deny "canary job is not approved"
  exit 0
fi

[[ "$workflow" == "$build_workflow" ]] || deny "workflow is not approved"
[[ "${GITHUB_EVENT_NAME:-}" == "push" ]] ||
  deny "only copied push events are approved"
[[ "$job" == "$build_job" ]] || deny "job is not approved"
[[ "${GITHUB_ACTOR:-}" == "copy-pr-bot[bot]" ]] ||
  deny "actor is not approved"
[[ "${GITHUB_REF:-}" =~ ^refs/heads/pull-request/[0-9]+$ ]] ||
  deny "ref is not an approved copied PR branch"
[[ "${GITHUB_WORKFLOW_REF:-}" == \
  "$expected_repo/.github/workflows/build-dev-images.yml@${GITHUB_REF}" ]] ||
  deny "workflow source does not match the copied PR ref"

job_name="${GITHUB_JOB_DISPLAY_NAME:-${GITHUB_JOB_NAME:-}}"
[[ -n "$job_name" ]] || deny "job display name is unavailable"
[[ "$job_name" =~ ^Build[[:space:]].+-sbsa[[:space:]]\(arm64,[[:space:]]native\)$ ]] ||
  deny "job display name is not an SBSA native cell"

event_path="${GITHUB_EVENT_PATH:-}"
[[ -n "$event_path" && -r "$event_path" ]] ||
  deny "event payload is unavailable"

python3 - "$event_path" "$expected_repo" <<'PY'
import json
import re
import sys

path, expected_repo = sys.argv[1:]
with open(path, encoding="utf-8") as event_file:
    event = json.load(event_file)

sender = (event.get("sender") or {}).get("login")
repo = (event.get("repository") or {}).get("full_name")
ref = str(event.get("ref") or "")
allowed = (
    sender == "copy-pr-bot[bot]"
    and repo == expected_repo
    and re.fullmatch(r"refs/heads/pull-request/[0-9]+", ref)
)
sys.exit(0 if allowed else 1)
PY
