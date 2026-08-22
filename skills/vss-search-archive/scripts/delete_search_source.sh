#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Resolve, Agent-delete, and verify one uploaded-file search source. Uploaded
# files use the Agent's fixed 2025-01-01 index partition; RTSP registrations
# have a separate Agent deletion route and must not use this operation.
set -uo pipefail

SOURCE_NAME=${1:-}
TIMEOUT_SECONDS=${2:-600}
VSS_REPO_ROOT=${VSS_REPO_ROOT:-${HOME}/video-search-and-summarization}

usage() {
  echo "usage: $0 SOURCE_NAME [TIMEOUT_SECONDS]" >&2
  exit 2
}

emit_error() {
  local message=$1
  jq -cn --arg error "${message}" '{error:$error}' >&2
  exit 1
}

[[ -n ${SOURCE_NAME} ]] || usage
[[ ${TIMEOUT_SECONDS} =~ ^[1-9][0-9]*$ ]] || usage
[[ -f ${VSS_REPO_ROOT}/services/agent/pyproject.toml ]] || emit_error "VSS checkout is unavailable"
command -v curl >/dev/null || emit_error "curl is unavailable"
command -v jq >/dev/null || emit_error "jq is unavailable"
command -v uv >/dev/null || emit_error "uv is unavailable"
command -v timeout >/dev/null || emit_error "timeout is unavailable"

VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)
DEADLINE=$(($(date +%s) + TIMEOUT_SECONDS))

remaining() {
  local cap=$1 available
  available=$((DEADLINE - $(date +%s)))
  (( available > 0 )) || return 1
  (( cap < available )) && printf '%s\n' "${cap}" || printf '%s\n' "${available}"
}

REQUEST_TIMEOUT=$(remaining 60) || emit_error "deletion deadline exhausted before configuration lookup"
CONFIG_JSON=$(timeout --foreground "${REQUEST_TIMEOUT}" "${VSS[@]}" configure show 2>/dev/null) || \
  emit_error "vss configure show failed"
VSS_ORIGIN=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '.base_url | select(type == "string" and length > 0)' 2>/dev/null) || emit_error "configured origin is missing"
VSS_ORIGIN=${VSS_ORIGIN%/}
ES_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '.services.elasticsearch.url | select(type == "string" and length > 0)' 2>/dev/null) || \
  emit_error "configured Elasticsearch URL is missing"
ES_URL=${ES_URL%/}
EMBED_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '[.services.elasticsearch.indices[] | select(. == "mdx-embed-filtered-2025-01-01")] | first' \
  2>/dev/null) || \
  emit_error "embedding index is missing"
BEHAVIOR_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '[.services.elasticsearch.indices[] | select(. == "mdx-behavior-2025-01-01")] | first' \
  2>/dev/null) || \
  emit_error "behavior index is missing"
RAW_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '[.services.elasticsearch.indices[] | select(. == "mdx-raw-2025-01-01")] | first' \
  2>/dev/null) || \
  emit_error "raw index is missing"
[[ ${EMBED_INDEX} != "${BEHAVIOR_INDEX}" && ${EMBED_INDEX} != "${RAW_INDEX}" && \
   ${BEHAVIOR_INDEX} != "${RAW_INDEX}" ]] || emit_error "search indexes are not distinct"

REQUEST_TIMEOUT=$(remaining 15) || emit_error "deletion deadline exhausted before source lookup"
SENSORS=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
  "${VSS_ORIGIN}/vst/api/v1/sensor/list" 2>/dev/null) || emit_error "VST source listing failed"
printf '%s' "${SENSORS}" | jq -e \
  'type == "array" and all(.[]; type == "object" and (.sensorId | type == "string" and length > 0) and (.name | type == "string" and length > 0))' \
  >/dev/null 2>&1 || emit_error "VST source listing was not a valid sensor array"
MATCHES=$(printf '%s' "${SENSORS}" | jq -c --arg name "${SOURCE_NAME}" \
  '[.[] | select(.name == $name)]' 2>/dev/null) || emit_error "VST source listing was not valid JSON"
MATCH_COUNT=$(printf '%s' "${MATCHES}" | jq -r 'length') || emit_error "could not count matching sources"
(( MATCH_COUNT == 1 )) || emit_error "expected exactly one source named ${SOURCE_NAME}; found ${MATCH_COUNT}"
SENSOR_ID=$(printf '%s' "${MATCHES}" | jq -er \
  '.[0].sensorId | select(type == "string" and length > 0)') || emit_error "matching source has no sensor UUID"
CANONICAL_NAME=$(printf '%s' "${MATCHES}" | jq -er \
  '.[0].name | select(type == "string" and length > 0)') || emit_error "matching source has no canonical name"

TMP_DIR=$(mktemp -d /tmp/vss-search-delete.XXXXXX) || emit_error "could not create temporary directory"
trap 'rm -rf -- "${TMP_DIR}"' EXIT
REQUEST_TIMEOUT=$(remaining 300) || emit_error "deletion deadline exhausted before Agent DELETE"
DELETE_HTTP_CODE=$(curl -sS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
  -o "${TMP_DIR}/delete.json" -w '%{http_code}' -X DELETE \
  "${VSS_ORIGIN}/api/v1/videos/${SENSOR_ID}") || emit_error "Agent DELETE request failed"
[[ ${DELETE_HTTP_CODE} =~ ^2[0-9][0-9]$ ]] || \
  emit_error "Agent DELETE returned HTTP ${DELETE_HTTP_CODE}"
DELETE_STATUS=$(jq -er '.status | select(. == "success" or . == "partial" or . == "failure")' \
  "${TMP_DIR}/delete.json" 2>/dev/null) || emit_error "Agent DELETE response has no valid status"
DELETE_MESSAGE=$(jq -r '.message // ""' "${TMP_DIR}/delete.json" 2>/dev/null) || DELETE_MESSAGE=
DELETE_WARNINGS=$(jq -c '.warnings // []' "${TMP_DIR}/delete.json" 2>/dev/null) || DELETE_WARNINGS='[]'

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

VST_PRESENT=true
EMBED_COUNT=-1
BEHAVIOR_COUNT=-1
RAW_COUNT=-1
LAST_ERROR=
while REQUEST_TIMEOUT=$(remaining 15); do
  if ! SENSORS=$(curl -fsS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" \
      "${VSS_ORIGIN}/vst/api/v1/sensor/list" 2>/dev/null); then
    LAST_ERROR="VST source listing failed during cleanup verification"
    break
  fi
  if ! printf '%s' "${SENSORS}" | jq -e \
      'type == "array" and all(.[]; type == "object" and (.sensorId | type == "string" and length > 0) and (.name | type == "string" and length > 0))' \
      >/dev/null 2>&1; then
    LAST_ERROR="VST source listing was not a valid sensor array during cleanup verification"
    break
  fi
  if ! VST_PRESENT=$(printf '%s' "${SENSORS}" | jq -r --arg id "${SENSOR_ID}" --arg name "${CANONICAL_NAME}" \
      'any(.[]; .sensorId == $id or .name == $name)' 2>/dev/null); then
    LAST_ERROR="VST source listing was invalid during cleanup verification"
    break
  fi
  case ${VST_PRESENT} in
    true|false) ;;
    *) LAST_ERROR="VST source listing returned an invalid presence value"; break ;;
  esac
  EMBED_COUNT=$(index_count "${EMBED_INDEX}" sensor.id.keyword "${SENSOR_ID}") || {
    LAST_ERROR="embedding cleanup count failed"
    break
  }
  BEHAVIOR_COUNT=$(index_count "${BEHAVIOR_INDEX}" sensor.id.keyword "${CANONICAL_NAME}") || {
    LAST_ERROR="behavior cleanup count failed"
    break
  }
  RAW_COUNT=$(index_count "${RAW_INDEX}" sensorId.keyword "${CANONICAL_NAME}") || {
    LAST_ERROR="raw cleanup count failed"
    break
  }
  if [[ ${VST_PRESENT} == false ]] && (( EMBED_COUNT == 0 && BEHAVIOR_COUNT == 0 && RAW_COUNT == 0 )); then
    break
  fi
  sleep 5
done

RESULT=$(jq -cn \
  --arg status "${DELETE_STATUS}" --arg message "${DELETE_MESSAGE}" --argjson warnings "${DELETE_WARNINGS}" \
  --arg source "${CANONICAL_NAME}" --arg sensor_id "${SENSOR_ID}" --argjson vst_present "${VST_PRESENT}" \
  --arg embed_index "${EMBED_INDEX}" --arg embed_field sensor.id.keyword \
  --arg embed_value "${SENSOR_ID}" --argjson embed_count "${EMBED_COUNT}" \
  --arg behavior_index "${BEHAVIOR_INDEX}" --arg behavior_field sensor.id.keyword \
  --arg behavior_value "${CANONICAL_NAME}" --argjson behavior_count "${BEHAVIOR_COUNT}" \
  --arg raw_index "${RAW_INDEX}" --arg raw_field sensorId.keyword \
  --arg raw_value "${CANONICAL_NAME}" --argjson raw_count "${RAW_COUNT}" \
  '{delete:{status:$status,message:$message,warnings:$warnings},source:$source,sensor_id:$sensor_id,
    vst_present:$vst_present,
    embedding:{index:$embed_index,field:$embed_field,value:$embed_value,count:$embed_count},
    behavior:{index:$behavior_index,field:$behavior_field,value:$behavior_value,count:$behavior_count},
    raw:{index:$raw_index,field:$raw_field,value:$raw_value,count:$raw_count}}') || emit_error "could not encode deletion result"

if [[ -n ${LAST_ERROR} ]]; then
  printf '%s' "${RESULT}" | jq -c --arg error "${LAST_ERROR}" '. + {error:$error}' >&2
  exit 1
fi
if [[ ${DELETE_STATUS} != success || ${VST_PRESENT} != false ]] || \
   (( EMBED_COUNT != 0 || BEHAVIOR_COUNT != 0 || RAW_COUNT != 0 )); then
  printf '%s' "${RESULT}" | jq -c '. + {error:"source cleanup did not complete before the deadline"}' >&2
  exit 1
fi
printf '%s\n' "${RESULT}"
