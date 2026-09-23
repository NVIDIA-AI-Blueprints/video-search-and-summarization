#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [[ "$#" -ne 1 || -z "$1" ]]; then
  echo "usage: seed_videomme_memory.sh <video-id>" >&2
  exit 2
fi

video_id="$1"
: "${VSS_GATEWAY_ORIGIN:?missing VSS_GATEWAY_ORIGIN}"
: "${VSS_BINDING:?missing VSS_BINDING}"
: "${VSS_EVAL_RUN_ID:?missing VSS_EVAL_RUN_ID}"
: "${VSS_EVAL_TASK_ID:?missing VSS_EVAL_TASK_ID}"

workspace="${HOME:-/sandbox}/.openclaw/workspace"
log_dir="/logs/agent"
memory_index="$(
  printf 'video-mme-%s-%s' "${VSS_EVAL_RUN_ID}" "${VSS_EVAL_TASK_ID}" |
    tr '[:upper:]_/' '[:lower:]---' |
    tr -cd 'a-z0-9._-'
)"
memory_index="${memory_index:0:220}"

mkdir -p "${workspace}/memory" "${log_dir}"

sensor_id="$(
  jq -er --arg video_id "${video_id}" '
    [
      to_entries[]
      | select(
          (.key | split("/")[-1] | sub("\\.[^.]+$"; "")) == $video_id
        )
    ]
    | if length == 1 then
        .[0].value
      elif length == 0 then
        error("video ID is absent from VSS_BINDING")
      else
        error("video ID is ambiguous in VSS_BINDING")
      end
  ' <<<"${VSS_BINDING}"
)"

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

summary_attempts="${VSS_SETUP_SUMMARY_ATTEMPTS:-3}"
summary_chunk_duration="${VSS_SETUP_SUMMARY_CHUNK_DURATION_SECS:-60}"
summary_ok=false

for attempt in $(seq 1 "${summary_attempts}"); do
  attempt_log="${log_dir}/setup-summary-attempt-${attempt}.jsonl"
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
    tee "${attempt_log}"
  summary_rc="${PIPESTATUS[0]}"
  set -e

  if [[ "${summary_rc}" -eq 0 ]] && \
      jq -se 'any(.[]; .persist.status == "complete" and .record == "closed")' \
        "${attempt_log}" >/dev/null && \
      jq -se 'any(.[]; .memory_note.written == true)' \
        "${attempt_log}" >/dev/null; then
    cp "${attempt_log}" "${log_dir}/setup-summary.jsonl"
    summary_ok=true
    break
  fi

  if [[ "${attempt}" -lt "${summary_attempts}" ]]; then
    sleep "$((attempt * 20))"
  fi
done

if [[ "${summary_ok}" != true ]]; then
  echo "LVS summary failed after ${summary_attempts} attempt(s)" >&2
  exit 6
fi

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

jq -n \
  --arg video_id "${video_id}" \
  --arg sensor_id "${sensor_id}" \
  --arg task_id "${VSS_EVAL_TASK_ID}" \
  --arg memory_index "${memory_index}" \
  --arg job_id "${job_id}" \
  --arg creation_time "${creation_time}" \
  '{
    state: "succeeded",
    video_id: $video_id,
    sensor_id: $sensor_id,
    task_id: $task_id,
    memory_index: $memory_index,
    job_id: $job_id,
    creation_time: $creation_time,
    elasticsearch_verified: true,
    markdown_verified: true
  }' > "${log_dir}/setup-result.json"
