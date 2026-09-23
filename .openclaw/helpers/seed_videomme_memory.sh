#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [[ "$#" -ne 1 || -z "$1" ]]; then
  echo "usage: seed_videomme_memory.sh <sensor-id>" >&2
  exit 2
fi

sensor_id="$1"
video_id="${VSS_VIDEO_ID:-${sensor_id}}"
run_id="${VSS_EVAL_RUN_ID:-}"
: "${VSS_GATEWAY_ORIGIN:?missing VSS_GATEWAY_ORIGIN}"
: "${VSS_EVAL_TASK_ID:?missing VSS_EVAL_TASK_ID}"

workspace="${HOME:-/sandbox}/.openclaw/workspace"
log_dir="${VSS_SETUP_LOG_DIR:-/logs/agent}"
state_dir="${VSS_SETUP_STATE_DIR:-${HOME:-/sandbox}/.vss/videomme-seed}"
identity_file="${HOME:-/sandbox}/.vss/videomme-trial.json"
lock_file="${state_dir}/seed.lock"
in_progress_file="${state_dir}/in-progress.json"
success_file="${log_dir}/setup-result.json"
failure_file="${log_dir}/setup-failure.json"
stage="initialization"
setup_complete=false
failure_observed=false
lock_acquired=false
identity_tmp=""
state_tmp=""

mkdir -p \
  "${workspace}/memory" \
  "${log_dir}" \
  "${state_dir}" \
  "$(dirname "${identity_file}")"

write_failure() {
  local exit_code="$1"
  local reason="$2"
  local failure_tmp="${failure_file}.tmp.$$"

  jq -n \
    --argjson exit_code "${exit_code}" \
    --arg stage "${stage}" \
    --arg reason "${reason}" \
    --arg run_id "${run_id}" \
    --arg task_id "${VSS_EVAL_TASK_ID}" \
    --arg video_id "${video_id}" \
    --arg sensor_id "${sensor_id}" \
    '{
      state: "failed",
      exit_code: $exit_code,
      stage: $stage,
      reason: $reason,
      run_id: $run_id,
      task_id: $task_id,
      video_id: $video_id,
      sensor_id: $sensor_id
    }' > "${failure_tmp}" && mv "${failure_tmp}" "${failure_file}"
  rm -f "${failure_tmp}"
}

on_exit() {
  local exit_code="$?"
  trap - EXIT INT TERM
  rm -f "${identity_tmp:-}" "${state_tmp:-}"

  if [[ "${lock_acquired}" == true && "${setup_complete}" != true && \
      "${failure_observed}" != true && "${exit_code}" -ne 0 ]]; then
    set +e
    write_failure "${exit_code}" "setup command failed"
    rm -f "${in_progress_file}"
    set -e
  fi

  exit "${exit_code}"
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# One setup operation is allowed in a sandbox. A concurrent duplicate waits
# here, then observes the first invocation's verified success or sticky
# failure instead of starting another full-video summary.
exec 9>"${lock_file}"
if ! flock -w "${VSS_SETUP_LOCK_TIMEOUT_SECS:-3600}" 9; then
  echo "timed out waiting for the VideoMME memory setup lock" >&2
  exit 7
fi
lock_acquired=true

if [[ -e "${success_file}" ]]; then
  if jq -e \
      --arg sensor_id "${sensor_id}" \
      --arg task_id "${VSS_EVAL_TASK_ID}" \
      '.state == "succeeded" and
       .sensor_id == $sensor_id and
       .task_id == $task_id and
       .elasticsearch_verified == true and
       .markdown_verified == true and
       (.job_id | type == "string" and length > 0)' \
      "${success_file}" >/dev/null; then
    setup_complete=true
    exit 0
  fi

  stage="cached-result-validation"
  write_failure 7 "existing setup-result.json is invalid for this task"
  failure_observed=true
  echo "existing VideoMME setup result is invalid; refusing to reseed" >&2
  exit 7
fi

if [[ -e "${failure_file}" ]]; then
  failure_exit_code="$(jq -er '.exit_code' "${failure_file}" 2>/dev/null || printf '7')"
  if [[ ! "${failure_exit_code}" =~ ^[1-9][0-9]*$ ]] || \
      [[ "${failure_exit_code}" -gt 255 ]]; then
    failure_exit_code=7
  fi
  failure_observed=true
  echo "VideoMME memory setup previously failed; refusing to start another summary" >&2
  exit "${failure_exit_code}"
fi

# If a process died without executing its EXIT trap (for example SIGKILL), its
# lock is released but this marker remains. Fail closed rather than silently
# creating a second summary operation.
if [[ -e "${in_progress_file}" ]]; then
  stage="stale-operation-detection"
  write_failure 7 "a prior setup operation ended without a terminal result"
  rm -f "${in_progress_file}"
  failure_observed=true
  echo "stale VideoMME setup state found; refusing to start another summary" >&2
  exit 7
fi

state_tmp="${in_progress_file}.tmp.$$"
jq -n \
  --arg run_id "${run_id}" \
  --arg task_id "${VSS_EVAL_TASK_ID}" \
  --arg video_id "${video_id}" \
  --arg sensor_id "${sensor_id}" \
  '{
    state: "running",
    run_id: $run_id,
    task_id: $task_id,
    video_id: $video_id,
    sensor_id: $sensor_id
  }' > "${state_tmp}"
mv "${state_tmp}" "${in_progress_file}"
state_tmp=""

stage="trial-identity"
if [[ -s "${identity_file}" ]]; then
  trial_uuid="$(jq -er '.trial_uuid' "${identity_file}")"
  memory_index="$(jq -er '.memory_index' "${identity_file}")"
else
  trial_uuid="$(cat /proc/sys/kernel/random/uuid)"
  # The public Elasticsearch edge admits unified-memory document writes only
  # for `vss-memory` and its suffixed variants. The first real summary record
  # creates this per-sandbox index through Elasticsearch auto-creation.
  memory_index="vss-memory-video-mme-${trial_uuid}"
  identity_tmp="${identity_file}.tmp.$$"
  jq -n \
    --arg trial_uuid "${trial_uuid}" \
    --arg memory_index "${memory_index}" \
    '{
      trial_uuid: $trial_uuid,
      memory_index: $memory_index
    }' > "${identity_tmp}"
  chmod 600 "${identity_tmp}"
  mv "${identity_tmp}" "${identity_file}"
  identity_tmp=""
fi

if [[ ! "${trial_uuid}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
    [[ "${memory_index}" != "vss-memory-video-mme-${trial_uuid}" ]]; then
  echo "invalid VideoMME trial identity in ${identity_file}" >&2
  exit 4
fi

stage="vss-configuration"
vss configure --base-url "${VSS_GATEWAY_ORIGIN}"
vss configure memory \
  --enable \
  --backend elasticsearch \
  --index "${memory_index}" \
  --persist-by-default \
  --markdown \
  --harness openclaw \
  --workspace "${workspace}" \
  --write-notes-by-default

vss configure memory introspection \
  --judge-endpoint "${VSS_MEMORY_JUDGE_ENDPOINT:-http://inference-shim.vss-eval-harness.svc.cluster.local:8080/v1}" \
  --judge-model "${VSS_MEMORY_JUDGE_MODEL:-anthropic/claude-opus-5}" \
  --clear-judge-backend-model \
  --judge-api-key-env "${VSS_MEMORY_JUDGE_API_KEY_ENV:-ANTHROPIC_API_KEY}"

vss configure memory check

stage="video-resolution"
vss vios timeline --sensor "${sensor_id}" --raw \
  > "${log_dir}/setup-timeline.json"
creation_time="$(
  jq -er '.segments | map(.start_time) | min' \
    "${log_dir}/setup-timeline.json"
)"

vss vios clip --sensor "${sensor_id}" --raw \
  > "${log_dir}/setup-clip.json"
external_media_url="$(jq -er '.media_url' "${log_dir}/setup-clip.json")"
lvs_media_origin="${VSS_LVS_MEDIA_ORIGIN:-http://vst-ingress:30888}"
media_url="$(
  jq -nr \
    --arg url "${external_media_url}" \
    --arg origin "${lvs_media_origin}" \
    '$url | sub("^https?://[^/]+"; $origin)'
)"
media_name="$(jq -er '.name' "${log_dir}/setup-clip.json")"

# One sandbox owns exactly one summary operation. A failed operation is
# terminal for this sandbox and is recorded by the EXIT trap below.
summary_chunk_duration="${VSS_SETUP_SUMMARY_CHUNK_DURATION_SECS:-60}"
stage="full-video-summary"
set +e
vss summarize run \
  --url "${media_url}" \
  --video-id "${sensor_id}" \
  --media-source "${sensor_id}" \
  --media-name "${media_name}" \
  --creation-time "${creation_time}" \
  --scenario "activity monitoring" \
  --event "notable activity" \
  --prompt "Summarize the complete video, preserving the important actions and events." \
  --chunk-duration "${summary_chunk_duration}" \
  --seed 1 \
  --write-memory-note \
  --request-timeout-seconds 1800 \
  --raw |
  tee "${log_dir}/setup-summary.jsonl"
summary_rc="${PIPESTATUS[0]}"
set -e

if [[ "${summary_rc}" -ne 0 ]] || \
    ! jq -se 'any(.[]; .persist.status == "complete" and .record == "closed")' \
      "${log_dir}/setup-summary.jsonl" >/dev/null || \
    ! jq -se 'any(.[]; .memory_note.written == true)' \
      "${log_dir}/setup-summary.jsonl" >/dev/null; then
  echo "LVS summary failed or did not persist a complete result" >&2
  exit 6
fi

stage="persistence-validation"
job_id="$(
  jq -sr '[.[] | select(type == "object" and .job_id != null)][0].job_id // empty' \
    "${log_dir}/setup-summary.jsonl"
)"
test -n "${job_id}"

vss memory get --job-id "${job_id}" --pretty \
  > "${log_dir}/setup-memory-record.json"
jq -e --arg job_id "${job_id}" \
  '.job.job_id == $job_id and .job.status == "completed"' \
  "${log_dir}/setup-memory-record.json" >/dev/null
grep -R -F "<!-- vss-job:${job_id} -->" "${workspace}/memory" >/dev/null

stage="result-write"
state_tmp="${success_file}.tmp.$$"
jq -n \
  --arg trial_uuid "${trial_uuid}" \
  --arg run_id "${run_id}" \
  --arg video_id "${video_id}" \
  --arg sensor_id "${sensor_id}" \
  --arg task_id "${VSS_EVAL_TASK_ID}" \
  --arg memory_index "${memory_index}" \
  --arg job_id "${job_id}" \
  --arg creation_time "${creation_time}" \
  '{
    state: "succeeded",
    trial_uuid: $trial_uuid,
    run_id: $run_id,
    video_id: $video_id,
    sensor_id: $sensor_id,
    task_id: $task_id,
    memory_index: $memory_index,
    job_id: $job_id,
    creation_time: $creation_time,
    elasticsearch_verified: true,
    markdown_verified: true
  }' > "${state_tmp}"
mv "${state_tmp}" "${success_file}"
state_tmp=""
rm -f "${in_progress_file}" "${failure_file}"
stage="complete"
setup_complete=true
