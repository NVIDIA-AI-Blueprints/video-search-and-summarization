#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Idempotently ingest the two release search fixtures into a prepared profile.
set -uo pipefail

TIMEOUT_SECONDS=${1:-2400}
VSS_REPO_ROOT=${VSS_REPO_ROOT:-${HOME}/video-search-and-summarization}

emit_error() {
  local message=$1
  jq -cn --arg error "${message}" '{error:$error}' >&2
  exit 1
}

[[ ${TIMEOUT_SECONDS} =~ ^[1-9][0-9]*$ ]] || {
  echo "usage: $0 [TIMEOUT_SECONDS]" >&2
  exit 2
}
[[ -f ${VSS_REPO_ROOT}/services/agent/pyproject.toml ]] || emit_error "VSS checkout is unavailable"
for command in curl jq uv ngc tar python3 sort timeout; do
  command -v "${command}" >/dev/null || emit_error "${command} is unavailable"
done

VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)
DEADLINE=$(($(date +%s) + TIMEOUT_SECONDS))
FIXTURE_ROOT=$(mktemp -d /tmp/vss-search-fixtures.XXXXXX) || emit_error "could not create fixture directory"
COMPLETE_DIR=$(mktemp -d /tmp/vss-search-complete.XXXXXX) || emit_error "could not create completion directory"
trap 'rm -rf -- "${FIXTURE_ROOT}" "${COMPLETE_DIR}"' EXIT

remaining() {
  local cap=$1 available
  available=$((DEADLINE - $(date +%s)))
  (( available > 0 )) || return 1
  (( cap < available )) && printf '%s\n' "${cap}" || printf '%s\n' "${available}"
}

REQUEST_TIMEOUT=$(remaining 60) || emit_error "deadline exhausted before configuration lookup"
CONFIG_JSON=$(timeout --foreground "${REQUEST_TIMEOUT}" "${VSS[@]}" configure show 2>/dev/null) || \
  emit_error "prepared vss configuration is unavailable"
VSS_ORIGIN=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '.base_url | select(type == "string" and length > 0)' 2>/dev/null) || emit_error "configured origin is missing"
VSS_ORIGIN=${VSS_ORIGIN%/}
AGENT_URL=${VSS_ORIGIN}
VST_URL=${VSS_ORIGIN}
ES_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '.services.elasticsearch.url | select(type == "string" and length > 0)' 2>/dev/null) || \
  emit_error "configured Elasticsearch URL is missing"
ES_URL=${ES_URL%/}
RTVI_EMBED_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '.services.rt_embed.url | select(type == "string" and length > 0)' 2>/dev/null) || \
  emit_error "configured RT-Embed URL is missing"
printf '%s' "${CONFIG_JSON}" | jq -e \
  '.services.rt_embed.models | type == "array" and length > 0' >/dev/null 2>&1 || \
  emit_error "configured RT-Embed model is missing"
RTVI_CV_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '.services.rtvi_cv.url | select(type == "string" and length > 0)' 2>/dev/null) || \
  emit_error "configured RT-CV URL is missing"

REQUEST_TIMEOUT=$(remaining 15) || emit_error "deadline exhausted before Agent health"
curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" "${AGENT_URL}/health" >/dev/null 2>&1 || \
  emit_error "prepared Agent is unhealthy"
REQUEST_TIMEOUT=$(remaining 15) || emit_error "deadline exhausted before VST health"
curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
  "${VST_URL}/vst/api/v1/sensor/list" >/dev/null 2>&1 || emit_error "prepared VST is unhealthy"
REQUEST_TIMEOUT=$(remaining 30) || emit_error "deadline exhausted before RT-Embed readiness"
EMBED_MODELS=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
  "${RTVI_EMBED_URL%/}/v1/models" 2>/dev/null) || emit_error "RT-Embed model listing failed"
printf '%s' "${EMBED_MODELS}" | jq -e \
  '.data | type == "array" and length > 0 and all(.[]; .id | type == "string" and length > 0)' \
  >/dev/null 2>&1 || emit_error "RT-Embed serves no model"

while REQUEST_TIMEOUT=$(remaining 15); do
  RTVI_CV_READY=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
    "${RTVI_CV_URL%/}/api/v1/ready" 2>/dev/null || true)
  if printf '%s' "${RTVI_CV_READY}" | jq -e \
    '(."ds-ready" // ."ready-info"."ds-ready" // "") == "YES"' >/dev/null 2>&1; then
    break
  fi
  sleep 10
done
printf '%s' "${RTVI_CV_READY:-}" | jq -e \
  '(."ds-ready" // ."ready-info"."ds-ready" // "") == "YES"' >/dev/null 2>&1 || \
  emit_error "RT-CV did not become ready before the deadline"

REQUEST_TIMEOUT=$(remaining 15) || emit_error "deadline exhausted before cleanup listing"
SENSORS=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
  "${VST_URL}/vst/api/v1/sensor/list" 2>/dev/null) || emit_error "fixture cleanup listing failed"
printf '%s' "${SENSORS}" | jq -e \
  'type == "array" and all(.[]; type == "object" and (.sensorId | type == "string" and length > 0) and (.name | type == "string" and length > 0))' \
  >/dev/null 2>&1 || emit_error "fixture cleanup listing was not a valid sensor array"
mapfile -t SENSORS_TO_DELETE < <(printf '%s' "${SENSORS}" | jq -er \
  '.[] | select(.name == "warehouse_sample" or .name == "warehouse-ladder" or
                .name == "sample-warehouse-ladder") |
          .sensorId | select(type == "string" and length > 0)' 2>/dev/null | sort -u)
for SENSOR_TO_DELETE in "${SENSORS_TO_DELETE[@]}"; do
  REQUEST_TIMEOUT=$(remaining 300) || emit_error "deadline exhausted during fixture cleanup"
  DELETE_RESPONSE=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" -X DELETE \
    "${AGENT_URL}/api/v1/videos/${SENSOR_TO_DELETE}" 2>/dev/null) || emit_error "Agent fixture cleanup failed"
  printf '%s' "${DELETE_RESPONSE}" | jq -e '.status == "success"' >/dev/null 2>&1 || \
    emit_error "Agent fixture cleanup did not report success"
done
while REQUEST_TIMEOUT=$(remaining 15); do
  SENSORS=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
    "${VST_URL}/vst/api/v1/sensor/list" 2>/dev/null) || emit_error "fixture absence listing failed"
  printf '%s' "${SENSORS}" | jq -e \
    'type == "array" and all(.[]; type == "object" and (.sensorId | type == "string" and length > 0) and (.name | type == "string" and length > 0))' \
    >/dev/null 2>&1 || emit_error "fixture absence listing was not a valid sensor array"
  if ! printf '%s' "${SENSORS}" | jq -e \
    'any(.[]; .name == "warehouse_sample" or .name == "warehouse-ladder" or
              .name == "sample-warehouse-ladder")' \
    >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
printf '%s' "${SENSORS}" | jq -e \
  'any(.[]; .name == "warehouse_sample" or .name == "warehouse-ladder" or
            .name == "sample-warehouse-ladder")' \
  >/dev/null 2>&1 && emit_error "fixture remnants did not disappear before the deadline"

cd "${FIXTURE_ROOT}" || emit_error "could not enter fixture directory"
REQUEST_TIMEOUT=$(remaining 600) || emit_error "deadline exhausted before fixture download"
timeout --foreground "${REQUEST_TIMEOUT}" \
  ngc registry resource download-version nvidia/vss-developer/dev-profile-sample-data:3.2.0 \
  --org nvidia --team vss-developer >/dev/null || emit_error "fixture bundle download failed"
REQUEST_TIMEOUT=$(remaining 120) || emit_error "deadline exhausted before fixture extraction"
timeout --foreground "${REQUEST_TIMEOUT}" \
  tar -xzf dev-profile-sample-data_v3.2.0/dev-profile-sample-data.tar.gz || emit_error "fixture extraction failed"
SAMPLE_DIR=${FIXTURE_ROOT}/dev-profile-sample-data
[[ -s ${SAMPLE_DIR}/warehouse_sample.mp4 ]] || emit_error "warehouse_sample.mp4 is missing from the bundle"
[[ -s ${SAMPLE_DIR}/sample-warehouse-ladder.mp4 ]] || emit_error "sample-warehouse-ladder.mp4 is missing from the bundle"

stage_upload() {
  local file_path=$1 upload_filename=$2 request timeout response returned_url returned_path effective_url identifier
  request=$(jq -cn --arg filename "${upload_filename}" '{filename:$filename}') || return 1
  timeout=$(remaining 30) || return 1
  response=$(curl -fsS --connect-timeout 5 --max-time "${timeout}" -X POST \
    "${AGENT_URL}/api/v1/videos" -H 'Content-Type: application/json' -d "${request}" 2>/dev/null) || return 1
  returned_url=$(printf '%s' "${response}" | jq -er \
    '.url | select(type == "string" and length > 0)' 2>/dev/null) || return 1
  case ${returned_url} in
    "${VSS_ORIGIN}"/*) effective_url=${returned_url} ;;
    *)
      returned_path=$(python3 - "${returned_url}" "${VSS_ORIGIN}" <<'PY'
import ipaddress
import sys
from urllib.parse import urlsplit

returned = urlsplit(sys.argv[1])
configured = urlsplit(sys.argv[2])
try:
    configured_ip = ipaddress.ip_address(configured.hostname or "")
except ValueError:
    raise SystemExit(1)
if (
    returned.scheme not in {"http", "https"}
    or not returned.hostname
    or returned.username is not None
    or returned.password is not None
    or returned.query
    or returned.fragment
    or returned.path != "/vst/api/v1/storage/file"
    or configured.scheme not in {"http", "https"}
    or configured_ip.is_global
):
    raise SystemExit(1)
print(returned.path)
PY
      ) || return 1
      effective_url=${VSS_ORIGIN}${returned_path}
      ;;
  esac
  identifier=$(cat /proc/sys/kernel/random/uuid) || return 1
  timeout=$(remaining 300) || return 1
  curl -fsS --connect-timeout 10 --max-time "${timeout}" -X POST "${effective_url}" \
    -H 'nvstreamer-chunk-number: 1' -H 'nvstreamer-total-chunks: 1' \
    -H 'nvstreamer-is-last-chunk: true' -H "nvstreamer-identifier: ${identifier}" \
    -H "nvstreamer-file-name: ${upload_filename}" \
    -F "mediaFile=@${file_path};filename=${upload_filename}" -F "filename=${upload_filename}" \
    -F 'metadata={"timestamp":"2025-01-01T00:00:00"}' >/dev/null 2>&1 || return 1
  printf '%s' "${response}"
}

WAREHOUSE_SAMPLE_UPLOAD=$(stage_upload "${SAMPLE_DIR}/warehouse_sample.mp4" warehouse_sample.mp4) || \
  emit_error "warehouse_sample upload staging failed"
WAREHOUSE_LADDER_UPLOAD=$(stage_upload "${SAMPLE_DIR}/sample-warehouse-ladder.mp4" warehouse-ladder.mp4) || \
  emit_error "warehouse-ladder upload staging failed"
WAREHOUSE_SAMPLE_SENSOR=$(printf '%s' "${WAREHOUSE_SAMPLE_UPLOAD}" | jq -er \
  '.sensorId | select(type == "string" and length > 0)') || emit_error "warehouse_sample upload has no sensor UUID"
WAREHOUSE_LADDER_SENSOR=$(printf '%s' "${WAREHOUSE_LADDER_UPLOAD}" | jq -er \
  '.sensorId | select(type == "string" and length > 0)') || emit_error "warehouse-ladder upload has no sensor UUID"

while REQUEST_TIMEOUT=$(remaining 15); do
  SENSORS=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
    "${VST_URL}/vst/api/v1/sensor/list" 2>/dev/null) || emit_error "staged fixture listing failed"
  printf '%s' "${SENSORS}" | jq -e \
    'type == "array" and all(.[]; type == "object" and (.sensorId | type == "string" and length > 0) and (.name | type == "string" and length > 0))' \
    >/dev/null 2>&1 || emit_error "staged fixture listing was not a valid sensor array"
  if printf '%s' "${SENSORS}" | jq -e --arg sample "${WAREHOUSE_SAMPLE_SENSOR}" --arg ladder "${WAREHOUSE_LADDER_SENSOR}" \
    'any(.[]; .name == "warehouse_sample" and .sensorId == $sample) and
     any(.[]; .name == "warehouse-ladder" and .sensorId == $ladder)' >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
printf '%s' "${SENSORS}" | jq -e --arg sample "${WAREHOUSE_SAMPLE_SENSOR}" --arg ladder "${WAREHOUSE_LADDER_SENSOR}" \
  'any(.[]; .name == "warehouse_sample" and .sensorId == $sample) and
   any(.[]; .name == "warehouse-ladder" and .sensorId == $ladder)' >/dev/null 2>&1 || \
  emit_error "both staged fixtures were not listed simultaneously"

complete_upload() {
  local sensor=$1 filename=$2 upload_response=$3 response_file=$4 timeout
  timeout=$(remaining 900) || return 1
  printf '%s' "${upload_response}" | jq --arg filename "${filename}" '. + {filename:$filename}' |
    curl -fsS --connect-timeout 10 --max-time "${timeout}" -X POST \
      "${AGENT_URL}/api/v1/videos/${sensor}/complete" -H 'Content-Type: application/json' -d @- \
      >"${response_file}" 2>/dev/null
}
complete_upload "${WAREHOUSE_SAMPLE_SENSOR}" warehouse_sample.mp4 \
  "${WAREHOUSE_SAMPLE_UPLOAD}" "${COMPLETE_DIR}/sample.json" &
SAMPLE_PID=$!
complete_upload "${WAREHOUSE_LADDER_SENSOR}" warehouse-ladder.mp4 \
  "${WAREHOUSE_LADDER_UPLOAD}" "${COMPLETE_DIR}/ladder.json" &
LADDER_PID=$!
SAMPLE_STATUS=0
LADDER_STATUS=0
wait "${SAMPLE_PID}" || SAMPLE_STATUS=$?
wait "${LADDER_PID}" || LADDER_STATUS=$?
(( SAMPLE_STATUS == 0 && LADDER_STATUS == 0 )) || emit_error "one or more completion requests failed"
jq -e --arg sensor "${WAREHOUSE_SAMPLE_SENSOR}" \
  '.sensor_id == $sensor and (.chunks_processed | type == "number" and . > 0)' \
  "${COMPLETE_DIR}/sample.json" >/dev/null || emit_error "warehouse_sample completion response is invalid"
jq -e --arg sensor "${WAREHOUSE_LADDER_SENSOR}" \
  '.sensor_id == $sensor and (.chunks_processed | type == "number" and . > 0)' \
  "${COMPLETE_DIR}/ladder.json" >/dev/null || emit_error "warehouse-ladder completion response is invalid"

resolve_indexes() {
  local command_timeout
  command_timeout=$(remaining 30) || return 1
  CONFIG_JSON=$(timeout --foreground "${command_timeout}" "${VSS[@]}" configure show 2>/dev/null) || return 1
  EMBED_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(. == "mdx-embed-filtered-2025-01-01")] | first' \
    2>/dev/null) || return 1
  BEHAVIOR_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(. == "mdx-behavior-2025-01-01")] | first' \
    2>/dev/null) || return 1
  RAW_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(. == "mdx-raw-2025-01-01")] | first' \
    2>/dev/null) || return 1
  [[ ${EMBED_INDEX} != "${BEHAVIOR_INDEX}" && ${EMBED_INDEX} != "${RAW_INDEX}" && \
     ${BEHAVIOR_INDEX} != "${RAW_INDEX}" ]]
}
while REQUEST_TIMEOUT=$(remaining 30); do
  if timeout --foreground "${REQUEST_TIMEOUT}" \
     "${VSS[@]}" configure --base-url "${VSS_ORIGIN}" >/dev/null 2>&1 && \
     resolve_indexes; then
    break
  fi
  sleep 15
done
resolve_indexes || emit_error "three distinct search indexes were not discovered"

index_count() {
  local index=$1 field=$2 value=$3 timeout query response
  timeout=$(remaining 15) || return 1
  query=$(jq -cn --arg field "${field}" --arg value "${value}" \
    '{query:{term:{($field):$value}}}') || return 1
  response=$(curl -fsS --connect-timeout 5 --max-time "${timeout}" \
    -H 'Content-Type: application/json' "${ES_URL}/${index}/_count" -d "${query}" 2>/dev/null) || return 1
  printf '%s' "${response}" | jq -er \
    '.count | select(type == "number" and . >= 0 and floor == .)' 2>/dev/null
}

SAMPLE_EMBED_COUNT=0
LADDER_EMBED_COUNT=0
LADDER_BEHAVIOR_COUNT=0
LADDER_RAW_COUNT=0
while remaining 15 >/dev/null; do
  SAMPLE_EMBED_COUNT=$(index_count "${EMBED_INDEX}" sensor.id.keyword "${WAREHOUSE_SAMPLE_SENSOR}" 2>/dev/null || echo 0)
  LADDER_EMBED_COUNT=$(index_count "${EMBED_INDEX}" sensor.id.keyword "${WAREHOUSE_LADDER_SENSOR}" 2>/dev/null || echo 0)
  LADDER_BEHAVIOR_COUNT=$(index_count "${BEHAVIOR_INDEX}" sensor.id.keyword warehouse-ladder 2>/dev/null || echo 0)
  LADDER_RAW_COUNT=$(index_count "${RAW_INDEX}" sensorId.keyword warehouse-ladder 2>/dev/null || echo 0)
  if (( SAMPLE_EMBED_COUNT > 0 && LADDER_EMBED_COUNT > 0 && LADDER_BEHAVIOR_COUNT > 0 && LADDER_RAW_COUNT > 0 )); then
    break
  fi
  sleep 15
done
(( SAMPLE_EMBED_COUNT > 0 && LADDER_EMBED_COUNT > 0 && LADDER_BEHAVIOR_COUNT > 0 && LADDER_RAW_COUNT > 0 )) || \
  emit_error "search documents did not become ready before the deadline"

jq -cn --arg origin "${VSS_ORIGIN}" \
  --arg sample_id "${WAREHOUSE_SAMPLE_SENSOR}" --arg ladder_id "${WAREHOUSE_LADDER_SENSOR}" \
  --arg embed_index "${EMBED_INDEX}" --arg behavior_index "${BEHAVIOR_INDEX}" --arg raw_index "${RAW_INDEX}" \
  --argjson sample_embed "${SAMPLE_EMBED_COUNT}" --argjson ladder_embed "${LADDER_EMBED_COUNT}" \
  --argjson ladder_behavior "${LADDER_BEHAVIOR_COUNT}" --argjson ladder_raw "${LADDER_RAW_COUNT}" \
  '{origin:$origin,
    sources:{warehouse_sample:{sensor_id:$sample_id},warehouse_ladder:{sensor_id:$ladder_id}},
    indexes:{embedding:$embed_index,behavior:$behavior_index,raw:$raw_index},
    counts:{warehouse_sample_embedding:$sample_embed,warehouse_ladder_embedding:$ladder_embed,
      warehouse_ladder_behavior:$ladder_behavior,warehouse_ladder_raw:$ladder_raw}}'
