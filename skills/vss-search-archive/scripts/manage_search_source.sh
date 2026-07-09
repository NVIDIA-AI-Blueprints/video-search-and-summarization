#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Required examples (set exactly one ACTION before running this block):
# ACTION=file-ingest FILE_PATH=/data/clip.mp4 FILENAME=clip.mp4 DEPLOYMENT=docker PROFILE=search
# ACTION=rtsp-ingest RTSP_URL=rtsp://camera/live SOURCE_NAME=loading-dock DEPLOYMENT=docker PROFILE=search
# ACTION=file-delete VIDEO_ID=<uuid> SOURCE_NAME=clip DEPLOYMENT=docker PROFILE=search
# ACTION=rtsp-delete VIDEO_ID=<uuid> SOURCE_NAME=loading-dock DEPLOYMENT=kubernetes \
#   NAMESPACE=vss RELEASE=search

# Never enable xtrace around credentials or request bodies.
set +x
set -euo pipefail

: "${ACTION:?set ACTION to file-ingest, rtsp-ingest, file-delete, or rtsp-delete}"

# Capture possible caller-exported RTSP inputs before the first child process
# (`mktemp`, deployment discovery, readiness curls) and keep the copies
# non-exported. do_rtsp_ingest also accepts values assigned after sourcing so
# the source-only unit harness remains useful.
CAPTURED_RTSP_URL="${RTSP_URL:-}"
CAPTURED_RTSP_USERNAME="${RTSP_USERNAME:-}"
CAPTURED_RTSP_PASSWORD=''
CAPTURED_RTSP_PASSWORD_FILE="${RTSP_PASSWORD_FILE:-}"
CAPTURED_RTSP_PASSWORD_SET=false
if [[ -n "${RTSP_PASSWORD+x}" ]]; then
  CAPTURED_RTSP_PASSWORD="${RTSP_PASSWORD}"
  CAPTURED_RTSP_PASSWORD_SET=true
fi
unset RTSP_URL RTSP_USERNAME RTSP_PASSWORD RTSP_PASSWORD_FILE
export -n \
  CAPTURED_RTSP_URL CAPTURED_RTSP_USERNAME CAPTURED_RTSP_PASSWORD \
  CAPTURED_RTSP_PASSWORD_FILE CAPTURED_RTSP_PASSWORD_SET 2>/dev/null || true

DEPLOYMENT="${DEPLOYMENT:-docker}"
VIDEO_INDEX="${VIDEO_INDEX:-}"
VIDEO_INDEX_WILDCARD="${VIDEO_INDEX_WILDCARD:-}"
BEHAVIOR_INDEX="${BEHAVIOR_INDEX:-}"
BEHAVIOR_INDEX_WILDCARD="${BEHAVIOR_INDEX_WILDCARD:-}"
RAW_INDEX="${RAW_INDEX:-}"
RAW_INDEX_WILDCARD="${RAW_INDEX_WILDCARD:-}"
ES_URL="${ES_URL:-}"
BEHAVIOR_ES_URL="${BEHAVIOR_ES_URL:-}"
RAW_ES_URL="${RAW_ES_URL:-}"
RTVI_CV_URL="${RTVI_CV_URL:-}"
RTVI_CV_404_SAFE=false
INGEST_WAIT_SECONDS="${INGEST_WAIT_SECONDS:-300}"
DELETE_WAIT_SECONDS="${DELETE_WAIT_SECONDS:-120}"
RTSP_ROLLBACK_DISCOVERY_SECONDS="${RTSP_ROLLBACK_DISCOVERY_SECONDS:-30}"
CHUNK_SIZE_BYTES="${CHUNK_SIZE_BYTES:-10485760}"
CHUNK_TIMEOUT_SECONDS="${CHUNK_TIMEOUT_SECONDS:-300}"

WORK_DIR="$(mktemp -d)"
PF_PIDS=()
PARTIAL_FILE_SENSOR=""
PARTIAL_FILE_NAME=""
PARTIAL_UPLOAD_IDENTIFIER=""
FILE_INGEST_DONE=false
FILE_COMPLETE_REQUEST_IN_FLIGHT=false
PARTIAL_RTSP_SENSOR=""
PARTIAL_RTSP_NAME=""
RTSP_INGEST_DONE=false
RTSP_ADD_CONFIRMED=false

cleanup() {
  local rc=$?
  local cleanup_response cleanup_status file_segment rollback_ok rtsp_segment sensor_candidate
  trap - EXIT INT TERM
  set +e
  if [[ -n "${PARTIAL_FILE_SENSOR}" ]] && ! is_safe_video_id "${PARTIAL_FILE_SENSOR}"; then
    printf 'warning: refusing rollback for an unsafe file sensor identifier\n' >&2
    PARTIAL_FILE_SENSOR=''
  fi
  if [[ -n "${PARTIAL_FILE_NAME}" ]] && ! is_safe_source_name "${PARTIAL_FILE_NAME}"; then
    printf 'warning: refusing rollback for an unsafe file source name\n' >&2
    PARTIAL_FILE_SENSOR=''
    PARTIAL_FILE_NAME=''
  fi
  if [[ -n "${PARTIAL_RTSP_NAME}" ]] && ! is_safe_source_name "${PARTIAL_RTSP_NAME}"; then
    printf 'warning: refusing rollback for an unsafe RTSP source name\n' >&2
    PARTIAL_RTSP_NAME=''
  fi
  if [[ -n "${PARTIAL_FILE_SENSOR}" && "${FILE_INGEST_DONE}" != true && -n "${VSS_AGENT_URL:-}" ]]; then
    file_segment="$(printf '%s' "${PARTIAL_FILE_SENSOR}" | jq -sRr @uri)"
    printf 'File ingestion did not complete; requesting agent-backed cleanup for %s\n' \
      "${PARTIAL_FILE_SENSOR}" >&2
    cleanup_response="$(
      curl -fsS --connect-timeout 5 --max-time 120 -X DELETE \
        -- "${VSS_AGENT_URL%/}/api/v1/videos/${file_segment}"
    )"
    cleanup_status="$(
      printf '%s' "${cleanup_response}" |
        jq -r --arg id "${PARTIAL_FILE_SENSOR}" \
          'if .video_id == $id then (.status // "unparseable") else "identity-mismatch" end'
    )"
    if [[ "${FILE_COMPLETE_REQUEST_IN_FLIGHT}" == true ]]; then
      printf 'warning: the file completion request ended without a response; server-side embedding may still be running, so rollback cannot be certified\n' >&2
    elif [[ "${cleanup_status}" != success && "${cleanup_status}" != partial ]]; then
      printf 'warning: rollback cleanup status=%s; operator verification is required\n' \
        "${cleanup_status}" >&2
    else
      rollback_ok=true
      reconcile_file_delete_state "${PARTIAL_FILE_SENSOR}" "${PARTIAL_FILE_NAME}" ||
        rollback_ok=false
      if [[ "${rollback_ok}" == true ]] &&
        wait_deleted_state video_file "${PARTIAL_FILE_SENSOR}" "${PARTIAL_FILE_NAME}"; then
        printf 'File-ingest rollback reconciled and verified for sensor=%s\n' \
          "${PARTIAL_FILE_SENSOR}" >&2
      else
        printf 'warning: file-ingest rollback could not verify all VST/CV/index state for sensor=%s\n' \
          "${PARTIAL_FILE_SENSOR}" >&2
      fi
    fi
  elif [[ -n "${PARTIAL_RTSP_NAME}" && "${RTSP_INGEST_DONE}" != true && -n "${VSS_AGENT_URL:-}" ]]; then
    if [[ "${RTSP_ADD_CONFIRMED}" != true ]]; then
      printf 'warning: RTSP add ownership was not confirmed; no name-addressed rollback delete was sent\n' >&2
      PARTIAL_RTSP_SENSOR=''
      PARTIAL_RTSP_NAME=''
    elif [[ -n "${PARTIAL_RTSP_SENSOR}" ]] && ! is_safe_video_id "${PARTIAL_RTSP_SENSOR}"; then
      printf 'warning: refusing RTSP rollback for an unsafe sensor identifier\n' >&2
      PARTIAL_RTSP_SENSOR=''
      PARTIAL_RTSP_NAME=''
    fi
    if [[ -n "${PARTIAL_RTSP_NAME}" ]] && sensor_candidate="$(
      resolve_rtsp_rollback_sensor "${PARTIAL_RTSP_NAME}" "${PARTIAL_RTSP_SENSOR}"
    )"; then
      PARTIAL_RTSP_SENSOR="${sensor_candidate}"
    elif [[ -n "${PARTIAL_RTSP_NAME}" ]]; then
      printf 'warning: RTSP rollback could not resolve one unambiguous post-mutation sensor; no name-addressed delete was sent\n' >&2
      PARTIAL_RTSP_SENSOR=''
      PARTIAL_RTSP_NAME=''
    fi
  fi
  if [[ -n "${PARTIAL_RTSP_NAME}" && "${RTSP_INGEST_DONE}" != true && -n "${VSS_AGENT_URL:-}" ]]; then
    rtsp_segment="$(printf '%s' "${PARTIAL_RTSP_NAME}" | jq -sRr @uri)"
    printf 'RTSP ingestion did not become searchable; rolling back sensor=%s name=%s\n' \
      "${PARTIAL_RTSP_SENSOR:-unresolved}" "${PARTIAL_RTSP_NAME}" >&2
    cleanup_response="$(
      curl -fsS --connect-timeout 5 --max-time 120 -X DELETE \
        -- "${VSS_AGENT_URL%/}/api/v1/rtsp-streams/delete/${rtsp_segment}"
    )"
    cleanup_status="$(
      printf '%s' "${cleanup_response}" |
        jq -r --arg name "${PARTIAL_RTSP_NAME}" \
          'if .name == $name then (.status // "unparseable") else "identity-mismatch" end'
    )"
    if [[ "${cleanup_status}" != success ]]; then
      printf 'warning: RTSP rollback cleanup status=%s for sensor=%s name=%s; operator verification is required\n' \
        "${cleanup_status}" "${PARTIAL_RTSP_SENSOR:-unresolved}" "${PARTIAL_RTSP_NAME}" >&2
    fi
    if [[ "${cleanup_status}" == success && -n "${PARTIAL_RTSP_SENSOR}" ]]; then
      rollback_ok=true
      delete_indexed_history rtsp "${PARTIAL_RTSP_SENSOR}" "${PARTIAL_RTSP_NAME}" ||
        rollback_ok=false
      if [[ "${rollback_ok}" == true ]] &&
        wait_deleted_state rtsp "${PARTIAL_RTSP_SENSOR}" "${PARTIAL_RTSP_NAME}"; then
        printf 'RTSP-ingest rollback exact history and VST state verified for sensor=%s name=%s\n' \
          "${PARTIAL_RTSP_SENSOR}" "${PARTIAL_RTSP_NAME}" >&2
      else
        printf 'warning: RTSP-ingest rollback could not verify all VST/index state for sensor=%s name=%s\n' \
          "${PARTIAL_RTSP_SENSOR}" "${PARTIAL_RTSP_NAME}" >&2
      fi
    fi
  elif [[ -z "${PARTIAL_FILE_SENSOR}" && -n "${PARTIAL_UPLOAD_IDENTIFIER}" && "${FILE_INGEST_DONE}" != true ]]; then
    printf 'warning: upload failed before VST returned a sensor ID; inspect chunk identifier %s\n' \
      "${PARTIAL_UPLOAD_IDENTIFIER}" >&2
  fi
  local pid
  for pid in "${PF_PIDS[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
  for pid in "${PF_PIDS[@]}"; do
    wait "${pid}" 2>/dev/null || true
  done
  unset RTSP_PASSWORD
  rm -rf "${WORK_DIR}"
  exit "${rc}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf 'error: %s\n' "$*" >&2
  return 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_value() {
  local name="$1" value="$2"
  [[ -n "${value}" && "${value}" != *'<'* && "${value}" != *'>'* ]] ||
    fail "set ${name} to a real, non-placeholder value"
}

validate_source_name() {
  local value="$1"
  require_value SOURCE_NAME "${value}"
  is_safe_source_name "${value}" ||
    fail 'SOURCE_NAME must be one non-dot path segment without slashes or control characters'
}

validate_video_id() {
  is_safe_video_id "$1" || fail 'VIDEO_ID has an invalid format'
}

is_safe_source_name() {
  local value="$1"
  [[ -n "${value}" && "${value}" != '.' && "${value}" != '..' && "${value}" != */* ]] &&
    [[ ! "${value}" =~ [[:cntrl:]] ]]
}

is_safe_video_id() {
  local value="$1"
  [[ "${value}" != '.' && "${value}" != '..' && "${value}" =~ ^[A-Za-z0-9._-]{1,128}$ ]]
}

validate_http_url() {
  local name="$1" value="$2" allow_query="${3:-false}"
  local remainder authority host port
  require_value "${name}" "${value}"
  case "${value}" in
    http://*|https://*) ;;
    *) fail "${name} must use http:// or https://"; return ;;
  esac
  [[ ! "${value}" =~ [[:space:][:cntrl:]] && "${value}" != *\\* ]] ||
    { fail "${name} contains unsafe URL characters"; return; }
  [[ "${value}" != *'#'* ]] || { fail "${name} must not contain a URL fragment"; return; }
  if [[ "${allow_query}" != true && "${value}" == *'?'* ]]; then
    fail "${name} must not contain query credentials or parameters"
    return
  fi
  remainder="${value#*://}"
  authority="${remainder%%/*}"
  authority="${authority%%\?*}"
  [[ -n "${authority}" && "${authority}" != *'@'* ]] ||
    { fail "${name} must contain a hostname and must not contain userinfo"; return; }
  if [[ "${authority}" == \[* ]]; then
    [[ "${authority}" =~ ^\[[^][]+\](:[0-9]+)?$ ]] ||
      { fail "${name} has an invalid IPv6 authority"; return; }
    [[ "${authority}" == *']:'* ]] && port="${authority##*:}" || port=''
  else
    [[ "${authority}" != *:*:* ]] || { fail "${name} must bracket an IPv6 hostname"; return; }
    host="${authority%%:*}"
    [[ -n "${host}" ]] || { fail "${name} must contain a hostname"; return; }
    [[ "${authority}" == *:* ]] && port="${authority##*:}" || port=''
  fi
  if [[ -n "${port}" ]]; then
    [[ "${port}" =~ ^[0-9]+$ ]] && ((10#${port} >= 1 && 10#${port} <= 65535)) ||
      { fail "${name} has an invalid port"; return; }
  fi
}

validate_positive_integer() {
  local name="$1" value="$2"
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || fail "${name} must be a positive integer"
}

for command_name in curl jq dd wc mktemp rm sleep uv; do
  require_command "${command_name}"
done
validate_positive_integer INGEST_WAIT_SECONDS "${INGEST_WAIT_SECONDS}"
validate_positive_integer DELETE_WAIT_SECONDS "${DELETE_WAIT_SECONDS}"
validate_positive_integer RTSP_ROLLBACK_DISCOVERY_SECONDS "${RTSP_ROLLBACK_DISCOVERY_SECONDS}"
validate_positive_integer CHUNK_SIZE_BYTES "${CHUNK_SIZE_BYTES}"
validate_positive_integer CHUNK_TIMEOUT_SECONDS "${CHUNK_TIMEOUT_SECONDS}"

wait_http() {
  local url="$1" process_id="${2:-}" label="${3:-service endpoint}" deadline=$((SECONDS + 45))
  validate_http_url "${label}" "${url}"
  while ((SECONDS < deadline)); do
    if [[ -n "${process_id}" ]] && ! kill -0 "${process_id}" 2>/dev/null; then
      fail "port-forward exited before ${label} became ready"
      return
    fi
    if curl -fsS --connect-timeout 2 --max-time 5 -- "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  fail "timed out waiting for ${label}"
}

free_port() {
  uv run --project libs/vss-cli python - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

select_service() {
  local app_name="$1" services_json
  local -a names=()
  services_json="$(
    "${KUBECTL[@]}" get services \
      -l "app.kubernetes.io/name=${app_name},app.kubernetes.io/instance=${RELEASE}" \
      -o json
  )"
  mapfile -t names < <(
    printf '%s' "${services_json}" |
      jq -r '.items[] | select(.spec.clusterIP != "None") | .metadata.name'
  )
  ((${#names[@]} == 1)) ||
    fail "expected one non-headless ${app_name} Service for release ${RELEASE}; found ${#names[@]}"
  printf '%s' "${names[0]}"
}

start_forward() {
  local app_name="$1" output_name="$2" health_path="$3"
  local service_name local_port log_file process_id
  service_name="$(select_service "${app_name}")"
  local_port="$(free_port)"
  log_file="${WORK_DIR}/${app_name}-port-forward.log"
  "${KUBECTL[@]}" port-forward --address 127.0.0.1 \
    "service/${service_name}" "${local_port}:http" >"${log_file}" 2>&1 &
  process_id=$!
  PF_PIDS+=("${process_id}")
  printf -v "${output_name}" 'http://127.0.0.1:%s' "${local_port}"
  wait_http "http://127.0.0.1:${local_port}${health_path}" "${process_id}" "${app_name} port-forward"
}

discover_runtime_es_urls() {
  uv run --project libs/vss-cli python - \
    "${DEPLOYMENT}" "${PROFILE:-}" "${NAMESPACE:-}" "${RELEASE:-}" "${KUBE_CONTEXT:-}" <<'PY'
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from lib.cli.deployment import discover_docker, discover_kubernetes
from lib.search_core.runtime import RuntimeSnapshot, _interpolate
import yaml

deployment_name, profile, namespace, release, context = sys.argv[1:]
deployment = None
try:
    if deployment_name == "docker":
        deployment = discover_docker(profile)
    elif deployment_name == "kubernetes":
        deployment = discover_kubernetes(namespace=namespace, release=release, context=context or None)
    else:
        raise RuntimeError(f"unsupported deployment {deployment_name!r}")
    runtime = RuntimeSnapshot.from_config_file(deployment.config_path, env=deployment.env).runtime
    rendered = _interpolate(Path(deployment.config_path).read_text(), deployment.env)
    document = yaml.safe_load(rendered) or {}
    general = document.get("general", {}) if isinstance(document, Mapping) else {}
    front_end = general.get("front_end", {}) if isinstance(general, Mapping) else {}
    streaming = front_end.get("streaming_ingest", {}) if isinstance(front_end, Mapping) else {}
    if not isinstance(streaming, Mapping):
        streaming = {}

    def configured(name):
        value = streaming.get(name)
        return value.strip() if isinstance(value, str) else ""

    explicit_embed = configured("elasticsearch_url")
    explicit_behavior = configured("behavior_elasticsearch_url")
    explicit_raw = configured("raw_elasticsearch_url")
    explicit_rtvi_cv = configured("rtvi_cv_base_url")
    embed = explicit_embed or runtime.es_endpoint or explicit_behavior or runtime.behavior_es_endpoint or explicit_raw
    behavior = explicit_behavior or runtime.behavior_es_endpoint or explicit_embed or embed
    raw = explicit_raw or explicit_behavior or runtime.behavior_es_endpoint or explicit_embed or embed
    print(json.dumps({
        "embed": embed,
        "behavior": behavior,
        "raw": raw,
        "rtvi_cv": explicit_rtvi_cv or runtime.rtvi_cv_endpoint,
    }))
finally:
    if deployment is not None:
        deployment.close()
PY
}

start_service_forward() {
  local service_name="$1" service_namespace="$2" remote_port="$3"
  local original_url="$4" output_name="$5" health_path="$6"
  local local_port log_file process_id rewritten
  local -a command=(kubectl)
  if [[ -n "${KUBE_CONTEXT:-}" ]]; then
    command+=(--context "${KUBE_CONTEXT}")
  fi
  command+=(--namespace "${service_namespace}")
  local_port="$(free_port)"
  log_file="${WORK_DIR}/${service_namespace}-${service_name}-${remote_port}-port-forward.log"
  "${command[@]}" port-forward --address 127.0.0.1 \
    "service/${service_name}" "${local_port}:${remote_port}" >"${log_file}" 2>&1 &
  process_id=$!
  PF_PIDS+=("${process_id}")
  rewritten="$(
    uv run --project libs/vss-cli python - "${original_url}" "${local_port}" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

url, port = sys.argv[1:]
parsed = urlsplit(url)
print(urlunsplit((parsed.scheme, f"127.0.0.1:{port}", parsed.path, parsed.query, parsed.fragment)))
PY
  )"
  printf -v "${output_name}" '%s' "${rewritten}"
  wait_http "${rewritten%/}${health_path}" "${process_id}" "${service_name} port-forward"
}

ensure_kubernetes_service_endpoint() {
  local variable_name="$1" endpoint="${!1}" endpoint_label="$2" health_path="$3"
  local target service namespace port certainty path service_json service_app
  local endpoints_json ready_backends ingress_json affinity_backends affinity_matches
  local affinity_backend backend_service_json backend_app backend_instance
  target="$(
    uv run --project libs/vss-cli python - "${endpoint}" "${NAMESPACE}" <<'PY'
import json
import sys
from urllib.parse import urlsplit

endpoint, default_namespace = sys.argv[1:]
parsed = urlsplit(endpoint)
host = (parsed.hostname or "").rstrip(".").lower()
labels = host.split(".") if host else []
target = {"certainty": "external", "service": "", "namespace": "", "port": 0,
          "path": parsed.path or ""}
if len(labels) == 1 and labels[0] not in {"localhost", "127.0.0.1", "::1"}:
    target = {"certainty": "internal", "service": labels[0], "namespace": default_namespace,
              "port": parsed.port or (443 if parsed.scheme == "https" else 80)}
elif len(labels) >= 3 and labels[2] == "svc":
    target = {"certainty": "internal", "service": labels[0], "namespace": labels[1],
              "port": parsed.port or (443 if parsed.scheme == "https" else 80)}
elif len(labels) == 2:
    target = {"certainty": "ambiguous", "service": labels[0], "namespace": labels[1],
              "port": parsed.port or (443 if parsed.scheme == "https" else 80)}
target["path"] = parsed.path or ""
print(json.dumps(target))
PY
  )"
  certainty="$(printf '%s' "${target}" | jq -er '.certainty')"
  path="$(printf '%s' "${target}" | jq -er '.path | strings')"
  if [[ "${certainty}" == external ]]; then
    return 0
  fi
  service="$(printf '%s' "${target}" | jq -er '.service')"
  namespace="$(printf '%s' "${target}" | jq -er '.namespace')"
  port="$(printf '%s' "${target}" | jq -er '.port')"
  local -a get_service=(kubectl)
  if [[ -n "${KUBE_CONTEXT:-}" ]]; then
    get_service+=(--context "${KUBE_CONTEXT}")
  fi
  get_service+=(--namespace "${namespace}" get service "${service}" -o json)
  if ! service_json="$("${get_service[@]}" 2>/dev/null)"; then
    [[ "${certainty}" == ambiguous ]] && return 0
    fail "configured ${endpoint_label} Service ${service}.${namespace} is unavailable"
    return
  fi
  if [[ "${variable_name}" == RTVI_CV_URL ]]; then
    service_app="$(
      printf '%s' "${service_json}" |
        jq -r '.metadata.labels["app.kubernetes.io/name"] // ""'
    )"
    if [[ "${service_app}" == vss-rtvi-cv || -z "${path}" || "${path}" == / ]]; then
      local -a get_endpoints=(kubectl)
      if [[ -n "${KUBE_CONTEXT:-}" ]]; then
        get_endpoints+=(--context "${KUBE_CONTEXT}")
      fi
      get_endpoints+=(--namespace "${namespace}" get endpoints "${service}" -o json)
      endpoints_json="$("${get_endpoints[@]}" 2>/dev/null)" || {
        fail "cannot verify RTVI-CV Service ${service}.${namespace} affinity; configure the live HAProxy endpoint or grant read access to Endpoints"
        return
      }
      ready_backends="$(
        printf '%s' "${endpoints_json}" |
          jq -er '[.subsets[]?.addresses[]?] | length'
      )"
      [[ "${ready_backends}" == 1 ]] || {
        fail "direct RTVI-CV Service ${service}.${namespace} has ${ready_backends} ready backends; configure the affinity-routed HAProxy endpoint before host cleanup"
        return
      }
    else
      local -a get_ingress=(kubectl)
      if [[ -n "${KUBE_CONTEXT:-}" ]]; then
        get_ingress+=(--context "${KUBE_CONTEXT}")
      fi
      get_ingress+=(
        --namespace "${NAMESPACE}" get ingress
        -l "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=rtvi-internal-ingress"
        -o json
      )
      ingress_json="$("${get_ingress[@]}" 2>/dev/null)" || {
        fail 'cannot verify the live RTVI-CV affinity Ingress; grant Ingress read access or use a verified singleton Service'
        return
      }
      affinity_backends="$(
        printf '%s' "${ingress_json}" |
          jq -cer --arg path "${path}" '
            def covers($request; $route):
              ($request == $route) or
              ($request | startswith((($route | rtrimstr("/")) + "/")));
            [
              .items[]
              | select(.metadata.annotations["haproxy.org/load-balance"] == "hdr(x-stream-id)")
              | select(.metadata.annotations["haproxy.org/hash-type"] == "consistent")
              | .spec.rules[]?.http.paths[]?
              | select(covers($path; .path))
              | .backend.service.name
            ] | unique
          '
      )"
      affinity_matches="$(printf '%s' "${affinity_backends}" | jq -er 'length')"
      [[ "${affinity_matches}" == 1 ]] || {
        fail "no unique live HAProxy Ingress proves x-stream-id affinity for RTVI-CV path ${path}"
        return
      }
      affinity_backend="$(printf '%s' "${affinity_backends}" | jq -er '.[0]')"
      local -a get_backend_service=(kubectl)
      if [[ -n "${KUBE_CONTEXT:-}" ]]; then
        get_backend_service+=(--context "${KUBE_CONTEXT}")
      fi
      get_backend_service+=(--namespace "${NAMESPACE}" get service "${affinity_backend}" -o json)
      backend_service_json="$("${get_backend_service[@]}" 2>/dev/null)" || {
        fail "cannot verify RTVI-CV Ingress backend Service ${affinity_backend}.${NAMESPACE}"
        return
      }
      backend_app="$(
        printf '%s' "${backend_service_json}" |
          jq -r '.metadata.labels["app.kubernetes.io/name"] // ""'
      )"
      backend_instance="$(
        printf '%s' "${backend_service_json}" |
          jq -r '.metadata.labels["app.kubernetes.io/instance"] // ""'
      )"
      [[ "${backend_app}" == vss-rtvi-cv && "${backend_instance}" == "${RELEASE}" ]] || {
        fail "verified affinity path ${path} does not target the live release's RTVI-CV Service"
        return
      }
    fi
    RTVI_CV_404_SAFE=true
  fi
  start_service_forward \
    "${service}" "${namespace}" "${port}" "${endpoint}" "${variable_name}" "${health_path}"
}

setup_access() {
  local discovered_urls runtime_es_urls='{}'
  local embed_endpoint behavior_endpoint raw_endpoint
  case "${DEPLOYMENT}" in
    docker)
      PROFILE="${PROFILE:-search}"
      if [[ -z "${ES_URL}" || -z "${BEHAVIOR_ES_URL}" || -z "${RAW_ES_URL}" || \
            ( ( "${ACTION}" == file-delete || "${ACTION}" == file-ingest ) && -z "${RTVI_CV_URL}" ) ]]; then
        runtime_es_urls="$(discover_runtime_es_urls)"
      fi
      discovered_urls="$(
        uv run --project libs/vss-cli python - "${PROFILE}" <<'PY'
import json
import sys

from lib.cli.deployment import discover_docker_host_endpoints

print(json.dumps(discover_docker_host_endpoints(sys.argv[1])))
PY
      )"
      VSS_AGENT_URL="${VSS_AGENT_URL:-$(printf '%s' "${discovered_urls}" | jq -er '.agent_url')}"
      VST_URL="${VST_URL:-$(printf '%s' "${discovered_urls}" | jq -er '.vst_url')}"
      ES_URL="${ES_URL:-$(printf '%s' "${runtime_es_urls}" | jq -er '.embed // empty')}"
      ES_URL="${ES_URL:-$(printf '%s' "${discovered_urls}" | jq -er '.es_url')}"
      BEHAVIOR_ES_URL="${BEHAVIOR_ES_URL:-$(printf '%s' "${runtime_es_urls}" | jq -er '.behavior // empty')}"
      BEHAVIOR_ES_URL="${BEHAVIOR_ES_URL:-${ES_URL}}"
      RAW_ES_URL="${RAW_ES_URL:-$(printf '%s' "${runtime_es_urls}" | jq -er '.raw // empty')}"
      RAW_ES_URL="${RAW_ES_URL:-${BEHAVIOR_ES_URL}}"
      RTVI_CV_URL="${RTVI_CV_URL:-$(printf '%s' "${runtime_es_urls}" | jq -r '.rtvi_cv // empty')}"
      if [[ "${ACTION}" == file-delete || "${ACTION}" == file-ingest ]]; then
        case "${RTVI_CV_URL}" in
          http://127.0.0.1:*|https://127.0.0.1:*|http://localhost:*|https://localhost:*)
            RTVI_CV_404_SAFE=true
            ;;
          *) RTVI_CV_404_SAFE=false ;;
        esac
      fi
      VST_FORWARD_URL="${VST_FORWARD_URL:-}"
      ;;
    kubernetes)
      : "${NAMESPACE:?set NAMESPACE for Kubernetes}"
      : "${RELEASE:?set RELEASE for Kubernetes}"
      require_command kubectl
      require_command uv
      KUBECTL=(kubectl --namespace "${NAMESPACE}")
      if [[ -n "${KUBE_CONTEXT:-}" ]]; then
        KUBECTL+=(--context "${KUBE_CONTEXT}")
      fi
      if [[ -z "${ES_URL}" || -z "${BEHAVIOR_ES_URL}" || -z "${RAW_ES_URL}" || \
            ( ( "${ACTION}" == file-delete || "${ACTION}" == file-ingest ) && -z "${RTVI_CV_URL}" ) ]]; then
        runtime_es_urls="$(discover_runtime_es_urls)"
      fi
      if [[ -z "${VSS_AGENT_URL:-}" ]]; then
        start_forward vss-agent VSS_AGENT_URL /docs
      fi
      if [[ -z "${VST_URL:-}" ]]; then
        start_forward vss-vios-ingress VST_URL /vst/api/v1/sensor/version
        VST_FORWARD_URL="${VST_URL}"
      else
        VST_FORWARD_URL="${VST_FORWARD_URL:-}"
      fi
      ES_URL="${ES_URL:-$(printf '%s' "${runtime_es_urls}" | jq -er '.embed | strings | select(length > 0)')}"
      BEHAVIOR_ES_URL="${BEHAVIOR_ES_URL:-$(printf '%s' "${runtime_es_urls}" | jq -er '.behavior | strings | select(length > 0)')}"
      RAW_ES_URL="${RAW_ES_URL:-$(printf '%s' "${runtime_es_urls}" | jq -er '.raw | strings | select(length > 0)')}"
      if [[ "${ACTION}" == file-delete || "${ACTION}" == file-ingest ]]; then
        RTVI_CV_URL="${RTVI_CV_URL:-$(
          printf '%s' "${runtime_es_urls}" | jq -er '.rtvi_cv | strings | select(length > 0)'
        )}"
      fi
      embed_endpoint="${ES_URL}"
      behavior_endpoint="${BEHAVIOR_ES_URL}"
      raw_endpoint="${RAW_ES_URL}"
      validate_http_url ES_URL "${ES_URL}"
      validate_http_url BEHAVIOR_ES_URL "${BEHAVIOR_ES_URL}"
      validate_http_url RAW_ES_URL "${RAW_ES_URL}"
      if [[ "${behavior_endpoint}" == "${embed_endpoint}" ]]; then
        ensure_kubernetes_service_endpoint ES_URL Elasticsearch /
        BEHAVIOR_ES_URL="${ES_URL}"
      else
        ensure_kubernetes_service_endpoint ES_URL Elasticsearch /
        ensure_kubernetes_service_endpoint BEHAVIOR_ES_URL 'behavior Elasticsearch' /
      fi
      if [[ "${raw_endpoint}" == "${behavior_endpoint}" ]]; then
        RAW_ES_URL="${BEHAVIOR_ES_URL}"
      elif [[ "${raw_endpoint}" == "${embed_endpoint}" ]]; then
        RAW_ES_URL="${ES_URL}"
      else
        ensure_kubernetes_service_endpoint RAW_ES_URL 'raw Elasticsearch' /
      fi
      if [[ "${ACTION}" == file-delete || "${ACTION}" == file-ingest ]]; then
        validate_http_url RTVI_CV_URL "${RTVI_CV_URL}"
        ensure_kubernetes_service_endpoint RTVI_CV_URL RTVI-CV /docs
      fi
      ;;
    *) fail "unsupported DEPLOYMENT=${DEPLOYMENT}; use docker or kubernetes" ;;
  esac

  VSS_AGENT_URL="${VSS_AGENT_URL%/}"
  VST_URL="${VST_URL%/}"
  ES_URL="${ES_URL%/}"
  BEHAVIOR_ES_URL="${BEHAVIOR_ES_URL%/}"
  RAW_ES_URL="${RAW_ES_URL%/}"
  RTVI_CV_URL="${RTVI_CV_URL%/}"
  VST_FORWARD_URL="${VST_FORWARD_URL:-${VST_URL}}"
  validate_http_url VSS_AGENT_URL "${VSS_AGENT_URL}"
  validate_http_url VST_URL "${VST_URL}"
  validate_http_url ES_URL "${ES_URL}"
  validate_http_url BEHAVIOR_ES_URL "${BEHAVIOR_ES_URL}"
  validate_http_url RAW_ES_URL "${RAW_ES_URL}"
  if [[ -n "${RTVI_CV_URL}" ]]; then
    validate_http_url RTVI_CV_URL "${RTVI_CV_URL}"
  fi
  validate_http_url VST_FORWARD_URL "${VST_FORWARD_URL}"
  wait_http "${VSS_AGENT_URL}/docs" '' 'VSS agent'
  wait_http "${VST_URL}/vst/api/v1/sensor/version" '' 'VST'
  wait_http "${ES_URL}/" '' 'embedding Elasticsearch'
  [[ "${BEHAVIOR_ES_URL}" == "${ES_URL}" ]] ||
    wait_http "${BEHAVIOR_ES_URL}/" '' 'behavior Elasticsearch'
  [[ "${RAW_ES_URL}" == "${ES_URL}" || "${RAW_ES_URL}" == "${BEHAVIOR_ES_URL}" ]] ||
    wait_http "${RAW_ES_URL}/" '' 'raw Elasticsearch'
  if [[ "${ACTION}" == file-delete || "${ACTION}" == file-ingest ]]; then
    require_value RTVI_CV_URL "${RTVI_CV_URL}"
    wait_http "${RTVI_CV_URL}/docs" '' 'RTVI-CV'
  fi
}

vst_sensor_present() {
  local video_id="$1" source_name="$2"
  curl -fsS --connect-timeout 5 --max-time 15 \
    -- "${VST_URL}/vst/api/v1/sensor/list" |
    jq -e --arg id "${video_id}" --arg name "${source_name}" \
      'any(.[]; (($id | length) > 0 and .sensorId == $id) or
                (($name | length) > 0 and .name == $name))' >/dev/null
}

vst_sensor_absent() {
  local video_id="$1" source_name="$2"
  curl -fsS --connect-timeout 5 --max-time 15 \
    -- "${VST_URL}/vst/api/v1/sensor/list" |
    jq -e --arg id "${video_id}" --arg name "${source_name}" \
      'all(.[]; (($id | length) == 0 or .sensorId != $id) and
                (($name | length) == 0 or .name != $name))' >/dev/null
}

vst_file_storage_absent() {
  local video_id="$1"
  curl -fsS --connect-timeout 5 --max-time 15 \
    -- "${VST_URL}/vst/api/v1/storage/timelines" |
    jq -e --arg id "${video_id}" '
      (.[$id] // []) as $entries |
      ($entries | type) == "array" and ($entries | length) == 0
    ' >/dev/null
}

vst_file_media_absent() {
  local video_id="$1"
  curl -fsS --connect-timeout 5 --max-time 15 \
    -- "${VST_URL}/vst/api/v1/storage/file/list" |
    jq -e --arg id "${video_id}" '
      type == "object" and
      ((.[$id] // []) | type) == "array" and
      ((.[$id] // []) | length) == 0
    ' >/dev/null
}

delete_vst_file_storage() {
  local video_id="$1" timelines range start_time end_time
  local video_segment start_segment end_segment status_code response_file
  if ! timelines="$(
      curl -fsS --connect-timeout 5 --max-time 30 \
        -- "${VST_URL}/vst/api/v1/storage/timelines"
    )"; then
    fail 'could not read VST storage timeline for reconciliation'
    return 1
  fi
  if ! range="$(
    printf '%s' "${timelines}" |
      jq -er --arg id "${video_id}" '
        (.[$id] // []) as $entries |
        if ($entries | type) != "array" then
          error("timeline entry is not an array")
        else
          [$entries[]? | .startTime? | strings | select(length > 0)] as $starts |
          [$entries[]? | .endTime? | strings | select(length > 0)] as $ends |
          if ($entries | length) == 0 then
            {empty: true}
          elif ($starts | length) == 0 or ($ends | length) == 0 then
            error("timeline entry has no complete range")
          else
            {empty: false, start: ($starts | min), end: ($ends | max)}
          end
        end
      '
    )"; then
    fail 'VST storage timeline returned an invalid range'
    return 1
  fi
  if [[ "$(printf '%s' "${range}" | jq -er '.empty')" == true ]]; then
    return 0
  fi
  start_time="$(printf '%s' "${range}" | jq -er '.start | strings | select(length > 0)')"
  end_time="$(printf '%s' "${range}" | jq -er '.end | strings | select(length > 0)')"
  video_segment="$(printf '%s' "${video_id}" | jq -sRr @uri)"
  start_segment="$(printf '%s' "${start_time}" | jq -sRr @uri)"
  end_segment="$(printf '%s' "${end_time}" | jq -sRr @uri)"
  response_file="${WORK_DIR}/vst-storage-delete.json"
  status_code="$(
    curl -sS --connect-timeout 5 --max-time 120 -o "${response_file}" -w '%{http_code}' \
      -X DELETE -- \
      "${VST_URL}/vst/api/v1/storage/file/${video_segment}?startTime=${start_segment}&endTime=${end_segment}"
  )"
  case "${status_code}" in
    200|204|404) ;;
    *) fail "VST storage reconciliation returned HTTP ${status_code}" ;;
  esac
}

delete_vst_sensor_direct() {
  local video_id="$1" video_segment status_code response_file
  video_segment="$(printf '%s' "${video_id}" | jq -sRr @uri)"
  response_file="${WORK_DIR}/vst-sensor-delete.json"
  status_code="$(
    curl -sS --connect-timeout 5 --max-time 120 -o "${response_file}" -w '%{http_code}' \
      -X DELETE -- "${VST_URL}/vst/api/v1/sensor/${video_segment}"
  )"
  case "${status_code}" in
    200|204|404) ;;
    *) fail "VST sensor reconciliation returned HTTP ${status_code}" ;;
  esac
}

vst_source_pair_matches() {
  local expected_type="$1" video_id="$2" source_name="$3" sensor_type
  case "${expected_type}" in
    video_file) sensor_type=sensor_file ;;
    rtsp) sensor_type=sensor_rtsp ;;
    *) return 1 ;;
  esac
  curl -fsS --connect-timeout 5 --max-time 15 \
    -- "${VST_URL}/vst/api/v1/sensor/list" |
    jq -e --arg id "${video_id}" --arg name "${source_name}" --arg type "${sensor_type}" \
      '[.[] | select(.sensorId == $id and .name == $name and .type == $type and
                     ((.state // "") != "removed"))] |
       length == 1' >/dev/null || return 1
}

wait_vst_present() {
  local video_id="$1" source_name="$2" deadline=$((SECONDS + 60))
  while ((SECONDS < deadline)); do
    if vst_sensor_present "${video_id}" "${source_name}"; then
      return 0
    fi
    sleep 2
  done
  fail 'source did not appear in the VST listing within 60 seconds'
}

resolve_video_id_by_name() {
  local source_name="$1" response
  response="$(
    curl -fsS --connect-timeout 5 --max-time 15 \
      -- "${VST_URL}/vst/api/v1/sensor/list"
  )"
  printf '%s' "${response}" |
    jq -er --arg name "${source_name}" \
      '[.[] | select(.name == $name) | .sensorId] |
       if length == 1 then .[0]
       elif length == 0 then error("source not found")
       else error("source name is ambiguous") end'
}

resolve_rtsp_rollback_sensor() {
  local source_name="$1" expected_id="${2:-}" response ids count candidate
  local deadline=$((SECONDS + RTSP_ROLLBACK_DISCOVERY_SECONDS))
  while :; do
    if response="$(
      curl -fsS --connect-timeout 5 --max-time 15 \
        -- "${VST_URL}/vst/api/v1/sensor/list"
    )" && ids="$(
      printf '%s' "${response}" |
        jq -cer --arg name "${source_name}" \
          '[.[] | select(.name == $name) | .sensorId | strings | select(length > 0)]'
    )"; then
      count="$(printf '%s' "${ids}" | jq -er 'length')"
      if ((count > 1)); then
        fail "RTSP rollback source name ${source_name} is ambiguous; refusing name-addressed deletion"
        return 1
      fi
      if ((count == 1)); then
        candidate="$(printf '%s' "${ids}" | jq -er '.[0]')"
        is_safe_video_id "${candidate}" || {
          fail 'RTSP rollback resolved an unsafe sensor identifier'
          return 1
        }
        if [[ -n "${expected_id}" && "${candidate}" != "${expected_id}" ]]; then
          fail 'RTSP rollback sensor identity changed after the mutating request'
          return 1
        fi
        printf '%s' "${candidate}"
        return 0
      fi
    fi
    ((SECONDS < deadline)) || break
    sleep 2
  done
  fail "RTSP rollback source ${source_name} did not resolve after the in-flight add settled"
  return 1
}

es_count() {
  local endpoint="$1" index_expression="$2" field="$3" value="$4" allow_missing="${5:-false}"
  local query_options='ignore_unavailable=false&allow_no_indices=false'
  [[ "${allow_missing}" == true ]] &&
    query_options='ignore_unavailable=true&allow_no_indices=true'
  jq -n --arg field "${field}" --arg value "${value}" \
    '{query: {term: {($field): $value}}}' |
    curl -fsS --connect-timeout 5 --max-time 15 \
      -H 'Content-Type: application/json' --data-binary @- \
      -- "${endpoint}/${index_expression}/_count?${query_options}" |
    jq -er '.count | numbers'
}

es_delete_exact() {
  local endpoint="$1" index_expression="$2" field="$3" value="$4" allow_missing="${5:-false}"
  local query_options='ignore_unavailable=false&allow_no_indices=false' response
  [[ "${allow_missing}" == true ]] &&
    query_options='ignore_unavailable=true&allow_no_indices=true'
  response="$(
    jq -n --arg field "${field}" --arg value "${value}" \
      '{query: {term: {($field): $value}}}' |
      curl -fsS --connect-timeout 5 --max-time 120 -X POST \
        -H 'Content-Type: application/json' --data-binary @- \
        -- "${endpoint}/${index_expression}/_delete_by_query?refresh=true&conflicts=proceed&${query_options}"
  )"
  printf '%s' "${response}" |
    jq -e '(.timed_out // false) == false and ((.failures // []) | length == 0)' >/dev/null ||
    fail 'Elasticsearch exact-history cleanup reported failures'
}

delete_indexed_history() {
  local source_type="$1" video_id="$2" source_name="$3"
  local video_expression behavior_expression raw_expression video_value failed=false
  if [[ "${source_type}" == rtsp ]]; then
    video_expression="${VIDEO_INDEX_WILDCARD},-${VIDEO_INDEX}"
    behavior_expression="${BEHAVIOR_INDEX_WILDCARD},-${BEHAVIOR_INDEX}"
    raw_expression="${RAW_INDEX_WILDCARD},-${RAW_INDEX}"
    video_value="${source_name}"
  else
    video_expression="${VIDEO_INDEX}"
    behavior_expression="${BEHAVIOR_INDEX}"
    raw_expression="${RAW_INDEX}"
    video_value="${video_id}"
  fi
  es_delete_exact "${ES_URL}" "${video_expression}" 'sensor.id.keyword' "${video_value}" false || failed=true
  es_delete_exact "${BEHAVIOR_ES_URL}" "${behavior_expression}" 'sensor.id.keyword' "${source_name}" true || failed=true
  es_delete_exact "${RAW_ES_URL}" "${raw_expression}" 'sensorId.keyword' "${source_name}" true || failed=true
  [[ "${failed}" == false ]]
}

remove_file_from_rtvi_cv() {
  local video_id="$1" source_name="$2" payload status_code response_file
  require_value RTVI_CV_URL "${RTVI_CV_URL}"
  payload="$(
    jq -n --arg id "${video_id}" --arg name "${source_name}" '
      {
        key: "sensor",
        value: {
          camera_id: $id, camera_name: $name, camera_url: "", change: "camera_remove",
          metadata: {resolution: "1920x1080", codec: "h264", framerate: 30}
        },
        headers: {source: "vst"}
      }'
  )"
  response_file="${WORK_DIR}/rtvi-cv-remove.json"
  status_code="$(
    curl -sS --connect-timeout 5 --max-time 120 -o "${response_file}" -w '%{http_code}' -X POST \
      -H 'Content-Type: application/json' -H "x-stream-id: ${video_id}" \
      --data-binary "${payload}" -- "${RTVI_CV_URL}/api/v1/stream/remove"
  )"
  case "${status_code}" in
    200|201|204) ;;
    404)
      if [[ "${RTVI_CV_404_SAFE}" != true ]] ||
        ! jq -e --arg id "${video_id}" '
          type == "object" and
          .code == "NotFound" and
          (.message | type) == "string" and
          (.message | contains($id))
        ' "${response_file}" >/dev/null 2>&1; then
        fail 'RTVI-CV returned an unverified 404 instead of an exact missing-camera response'
      fi
      ;;
    *) fail "RTVI-CV routed removal returned HTTP ${status_code}" ;;
  esac
}

reconcile_file_delete_state() {
  local video_id="$1" source_name="$2" failed=false
  require_value SOURCE_NAME "${source_name}" || failed=true
  delete_vst_file_storage "${video_id}" || failed=true
  delete_vst_sensor_direct "${video_id}" || failed=true
  remove_file_from_rtvi_cv "${video_id}" "${source_name}" || failed=true
  delete_indexed_history video_file "${video_id}" "${source_name}" || failed=true
  [[ "${failed}" == false ]]
}

discover_indexes() {
  local discovered raw_discovered index_expression
  if [[ -n "${VIDEO_INDEX}" && -n "${VIDEO_INDEX_WILDCARD}" && \
        -n "${BEHAVIOR_INDEX}" && -n "${BEHAVIOR_INDEX_WILDCARD}" && \
        -n "${RAW_INDEX}" && -n "${RAW_INDEX_WILDCARD}" ]]; then
    # A complete operator-supplied contract is self-contained. In particular,
    # do not make an unnecessary Kubernetes ConfigMap lookup defeat the
    # documented explicit-override recovery path.
    discovered='{}'
  else
    raw_discovered="$(
      uv run --project libs/vss-cli python - \
        "${DEPLOYMENT}" "${PROFILE:-}" "${NAMESPACE:-}" "${RELEASE:-}" "${KUBE_CONTEXT:-}" <<'PY'
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from lib.cli.deployment import discover_docker, discover_kubernetes
from lib.search_core.runtime import RuntimeSnapshot, _interpolate
import yaml

deployment_name, profile, namespace, release, context = sys.argv[1:]
deployment = None
try:
    if deployment_name == "docker":
        deployment = discover_docker(profile)
    elif deployment_name == "kubernetes":
        deployment = discover_kubernetes(
            namespace=namespace,
            release=release,
            context=context or None,
        )
    else:
        raise RuntimeError(f"unsupported deployment {deployment_name!r}")
    runtime = RuntimeSnapshot.from_config_file(
        deployment.config_path,
        env=deployment.env,
    ).runtime
    rendered = _interpolate(Path(deployment.config_path).read_text(), deployment.env)
    document = yaml.safe_load(rendered) or {}
    general = document.get("general", {}) if isinstance(document, Mapping) else {}
    front_end = general.get("front_end", {}) if isinstance(general, Mapping) else {}
    streaming = front_end.get("streaming_ingest", {}) if isinstance(front_end, Mapping) else {}
    if not isinstance(streaming, Mapping):
        streaming = {}

    def configured(name):
        value = streaming.get(name)
        return value.strip() if isinstance(value, str) else ""

    print(json.dumps({
        "runtime": {
            "video": runtime.video_embed_index,
            "video_wildcard": runtime.video_embed_index_wildcard,
            "behavior": runtime.behavior_index,
            "behavior_wildcard": runtime.behavior_index_wildcard,
            "raw": runtime.frames_index,
            "raw_wildcard": runtime.frames_index_wildcard,
        },
        "streaming": {
            "video": configured("rtvi_embed_es_index"),
            "video_wildcard": configured("rtsp_embed_es_index_pattern"),
            "behavior": configured("behavior_es_index"),
            "behavior_wildcard": configured("rtsp_behavior_es_index_pattern"),
            "raw": configured("raw_es_index"),
            "raw_wildcard": configured("rtsp_raw_es_index_pattern"),
        },
    }))
finally:
    if deployment is not None:
        deployment.close()
PY
    )"
    discovered="$(printf '%s' "${raw_discovered}" | select_index_contract)"
  fi

  VIDEO_INDEX="${VIDEO_INDEX:-$(
    printf '%s' "${discovered}" | jq -er '.video | strings | select(length > 0)'
  )}"
  VIDEO_INDEX_WILDCARD="${VIDEO_INDEX_WILDCARD:-$(
    printf '%s' "${discovered}" | jq -er '.video_wildcard | strings | select(length > 0)'
  )}"
  BEHAVIOR_INDEX="${BEHAVIOR_INDEX:-$(
    printf '%s' "${discovered}" | jq -er '.behavior | strings | select(length > 0)'
  )}"
  BEHAVIOR_INDEX_WILDCARD="${BEHAVIOR_INDEX_WILDCARD:-$(
    printf '%s' "${discovered}" | jq -er '.behavior_wildcard | strings | select(length > 0)'
  )}"
  RAW_INDEX="${RAW_INDEX:-$(
    printf '%s' "${discovered}" | jq -er '.raw | strings | select(length > 0)'
  )}"
  RAW_INDEX_WILDCARD="${RAW_INDEX_WILDCARD:-$(
    printf '%s' "${discovered}" | jq -er '.raw_wildcard | strings | select(length > 0)'
  )}"

  for index_expression in \
    "${VIDEO_INDEX}" "${VIDEO_INDEX_WILDCARD}" \
    "${BEHAVIOR_INDEX}" "${BEHAVIOR_INDEX_WILDCARD}" \
    "${RAW_INDEX}" "${RAW_INDEX_WILDCARD}"; do
    [[ "${index_expression}" =~ ^[-A-Za-z0-9._*,]+$ ]] ||
      fail "unsafe or missing Elasticsearch index expression: ${index_expression}"
  done
}

select_index_contract() {
  jq -c '
    def first_nonempty:
      map(select(type == "string" and length > 0)) | .[0];
    {
      video: ([.streaming.video, .runtime.video] | first_nonempty),
      video_wildcard: ([.streaming.video_wildcard, .runtime.video_wildcard] | first_nonempty),
      behavior: ([.streaming.behavior, .runtime.behavior] | first_nonempty),
      behavior_wildcard: ([.streaming.behavior_wildcard, .runtime.behavior_wildcard] | first_nonempty),
      raw: ([.streaming.raw, .runtime.raw] | first_nonempty),
      raw_wildcard: ([.streaming.raw_wildcard, .runtime.raw_wildcard] | first_nonempty)
    }'
}

wait_rtsp_searchable() {
  local source_name="$1" count
  local index_expression="${VIDEO_INDEX_WILDCARD},-${VIDEO_INDEX}"
  local deadline=$((SECONDS + INGEST_WAIT_SECONDS))
  while ((SECONDS < deadline)); do
    if count="$(es_count "${ES_URL}" "${index_expression}" 'sensor.id.keyword' "${source_name}")"; then
      if ((count > 0)); then
        return 0
      fi
    fi
    sleep 5
  done
  fail "no exact RTSP embedding for ${source_name} after ${INGEST_WAIT_SECONDS} seconds"
}

deleted_state_is_clean() {
  local source_type="$1" video_id="$2" source_name="$3"
  local video_expression behavior_expression raw_expression
  local video_value video_count behavior_count raw_count

  vst_sensor_absent "${video_id}" "${source_name}" || return 1
  if [[ "${source_type}" == video_file ]]; then
    vst_file_storage_absent "${video_id}" || return 1
    vst_file_media_absent "${video_id}" || return 1
  fi
  if [[ "${source_type}" == rtsp ]]; then
    video_expression="${VIDEO_INDEX_WILDCARD},-${VIDEO_INDEX}"
    behavior_expression="${BEHAVIOR_INDEX_WILDCARD},-${BEHAVIOR_INDEX}"
    raw_expression="${RAW_INDEX_WILDCARD},-${RAW_INDEX}"
  else
    video_expression="${VIDEO_INDEX}"
    behavior_expression="${BEHAVIOR_INDEX}"
    raw_expression="${RAW_INDEX}"
  fi
  video_value="${video_id}"
  [[ "${source_type}" == rtsp ]] && video_value="${source_name}"

  if ! video_count="$(es_count "${ES_URL}" "${video_expression}" 'sensor.id.keyword' "${video_value}" false)"; then
    return 1
  fi
  if ! behavior_count="$(es_count "${BEHAVIOR_ES_URL}" "${behavior_expression}" 'sensor.id.keyword' "${source_name}" true)"; then
    return 1
  fi
  if ! raw_count="$(es_count "${RAW_ES_URL}" "${raw_expression}" 'sensorId.keyword' "${source_name}" true)"; then
    return 1
  fi
  ((video_count == 0 && behavior_count == 0 && raw_count == 0))
}

wait_deleted_state() {
  local source_type="$1" video_id="$2" source_name="$3"
  local deadline=$((SECONDS + DELETE_WAIT_SECONDS))
  while ((SECONDS < deadline)); do
    if deleted_state_is_clean "${source_type}" "${video_id}" "${source_name}"; then
      return 0
    fi
    sleep 3
  done
  fail "source or exact-match index documents remain after ${DELETE_WAIT_SECONDS} seconds"
}

new_identifier() {
  local identifier
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen
  elif [[ -r /proc/sys/kernel/random/uuid ]]; then
    IFS= read -r identifier </proc/sys/kernel/random/uuid
    printf '%s' "${identifier}"
  else
    printf '%s-%s-%s' "${SECONDS}" "$$" "${RANDOM}"
  fi
}

upload_chunk() {
  local upload_url="$1" file_path="$2" filename="$3" identifier="$4"
  local chunk_number="$5" total_chunks="$6" is_last="$7"
  local chunk_file="${WORK_DIR}/upload.chunk" response attempt
  dd if="${file_path}" of="${chunk_file}" bs="${CHUNK_SIZE_BYTES}" \
    skip="$((chunk_number - 1))" count=1 status=none

  for attempt in 1 2 3 4; do
    if response="$(
      curl -fsS --connect-timeout 10 --max-time "${CHUNK_TIMEOUT_SECONDS}" \
        -X POST \
        -H "nvstreamer-chunk-number: ${chunk_number}" \
        -H "nvstreamer-total-chunks: ${total_chunks}" \
        -H "nvstreamer-is-last-chunk: ${is_last}" \
        -H "nvstreamer-identifier: ${identifier}" \
        -H "nvstreamer-file-name: ${filename}" \
        -F "mediaFile=@${chunk_file};filename=${filename};type=application/octet-stream" \
        -F "filename=${filename}" \
        -F 'metadata={"timestamp":"2025-01-01T00:00:00"}' \
        -- "${upload_url}"
    )"; then
      printf '%s' "${response}"
      return 0
    fi
    if ((attempt < 4)); then
      sleep "$((1 << (attempt - 1)))"
    fi
  done
  fail "chunk ${chunk_number}/${total_chunks} failed after four attempts"
}

do_file_ingest() {
  local file_path="${FILE_PATH:-}" filename="${FILENAME:-}"
  local upload_url upload_path file_size total_chunks
  local chunk_number is_last upload_response complete_response chunks_processed
  require_value FILE_PATH "${file_path}"
  require_value FILENAME "${filename}"
  [[ -f "${file_path}" && -s "${file_path}" ]] ||
    fail "FILE_PATH is not a non-empty regular file: ${file_path}"
  [[ "${filename}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$ ]] ||
    fail 'FILENAME must be whitespace-free and contain only letters, digits, dot, underscore, or dash'
  PARTIAL_FILE_NAME="${filename%.*}"

  upload_url="$(
    jq -n --arg filename "${filename}" '{filename: $filename}' |
      curl -fsS --connect-timeout 5 --max-time 30 -X POST \
        -H 'Content-Type: application/json' --data-binary @- \
        -- "${VSS_AGENT_URL}/api/v1/videos" |
      jq -er '.url | strings | select(length > 0)'
  )"
  validate_http_url 'agent VST upload URL' "${upload_url}" true
  upload_path="${upload_url#*://}"
  upload_path="/${upload_path#*/}"
  [[ "${upload_path%%\?*}" == '/vst/api/v1/storage/file' ]] ||
    fail "agent returned an unexpected VST upload path: ${upload_path%%\?*}"
  if [[ -n "${VST_FORWARD_URL:-}" ]]; then
    upload_url="${VST_FORWARD_URL%/}${upload_path}"
  fi
  validate_http_url 'host VST upload URL' "${upload_url}" true
  curl -sS --connect-timeout 3 --max-time 5 -X OPTIONS \
    -o /dev/null -- "${upload_url}" ||
    fail 'returned VST upload URL is not host-reachable; configure VST_FORWARD_URL'

  file_size="$(wc -c <"${file_path}")"
  ((file_size > 0)) || fail 'video file is empty'
  total_chunks=$(((file_size + CHUNK_SIZE_BYTES - 1) / CHUNK_SIZE_BYTES))
  PARTIAL_UPLOAD_IDENTIFIER="$(new_identifier)"
  upload_response=''
  for ((chunk_number = 1; chunk_number <= total_chunks; chunk_number++)); do
    is_last=false
    ((chunk_number == total_chunks)) && is_last=true
    upload_response="$(
      upload_chunk "${upload_url}" "${file_path}" "${filename}" "${PARTIAL_UPLOAD_IDENTIFIER}" \
        "${chunk_number}" "${total_chunks}" "${is_last}"
    )"
  done

  local sensor_candidate
  sensor_candidate="$(printf '%s' "${upload_response}" | jq -er '.sensorId | strings | select(length > 0)')"
  validate_video_id "${sensor_candidate}"
  PARTIAL_FILE_SENSOR="${sensor_candidate}"
  FILE_COMPLETE_REQUEST_IN_FLIGHT=true
  if ! complete_response="$(
      printf '%s' "${upload_response}" |
        jq --arg filename "${filename}" '. + {filename: $filename}' |
        curl -fsS --connect-timeout 5 --max-time 900 -X POST \
          -H 'Content-Type: application/json' --data-binary @- \
          -- "${VSS_AGENT_URL}/api/v1/videos/${PARTIAL_FILE_SENSOR}/complete"
    )"; then
    fail 'agent completion request ended without a usable response; server-side completion outcome is unknown'
    return 1
  fi
  FILE_COMPLETE_REQUEST_IN_FLIGHT=false
  if ! chunks_processed="$(
      printf '%s' "${complete_response}" |
        jq -er --arg id "${PARTIAL_FILE_SENSOR}" \
          'select(.sensor_id == $id) | .chunks_processed | numbers'
    )"; then
    fail 'agent completion returned an invalid or mismatched sensor identity'
    return 1
  fi
  ((chunks_processed > 0)) || fail 'agent completion returned zero searchable chunks'
  wait_vst_present "${PARTIAL_FILE_SENSOR}" ''
  FILE_INGEST_DONE=true
  printf 'File ingestion complete: sensor=%s chunks=%s\n' \
    "${PARTIAL_FILE_SENSOR}" "${chunks_processed}"
}

do_rtsp_ingest() {
  local rtsp_url="${RTSP_URL:-${CAPTURED_RTSP_URL}}" source_name="${SOURCE_NAME:-}"
  local rtsp_username="${RTSP_USERNAME:-${CAPTURED_RTSP_USERNAME}}"
  local rtsp_password_file="${RTSP_PASSWORD_FILE:-${CAPTURED_RTSP_PASSWORD_FILE}}"
  local rtsp_password='' password_was_set=false authority
  local response video_id deadline=$((SECONDS + 60))
  if [[ -n "${RTSP_PASSWORD+x}" ]]; then
    rtsp_password="${RTSP_PASSWORD}"
    password_was_set=true
  elif [[ "${CAPTURED_RTSP_PASSWORD_SET}" == true ]]; then
    rtsp_password="${CAPTURED_RTSP_PASSWORD}"
    password_was_set=true
  fi
  # Do not leave caller-exported credentials in the environment inherited by
  # jq, curl, stat, or any other child process.
  unset RTSP_URL RTSP_USERNAME RTSP_PASSWORD RTSP_PASSWORD_FILE
  require_value RTSP_URL "${rtsp_url}"
  validate_source_name "${source_name}"
  [[ "${rtsp_url}" == rtsp://* || "${rtsp_url}" == rtsps://* ]] ||
    fail 'RTSP_URL must use rtsp:// or rtsps://'
  [[ ! "${rtsp_url}" =~ [[:space:][:cntrl:]] ]] || fail 'RTSP_URL contains unsafe characters'
  authority="${rtsp_url#*://}"
  authority="${authority%%/*}"
  [[ "${authority}" != *'@'* ]] || fail 'RTSP_URL must not embed userinfo; use RTSP_USERNAME and the password input'
  vst_sensor_absent '' "${source_name}" ||
    fail 'SOURCE_NAME is already registered in VST; choose a unique name before ingesting'
  if [[ -n "${rtsp_password_file}" ]]; then
    [[ -r "${rtsp_password_file}" ]] || fail 'RTSP_PASSWORD_FILE is not readable'
    require_command stat
    local password_mode
    password_mode="$(stat -c '%a' -- "${rtsp_password_file}")"
    (( (8#${password_mode} & 8#077) == 0 )) ||
      fail 'RTSP_PASSWORD_FILE must not grant group or other permissions'
    rtsp_password="$(<"${rtsp_password_file}")"
  elif [[ "${password_was_set}" != true ]]; then
    if [[ -t 0 ]]; then
      IFS= read -r -s -p 'RTSP password (empty allowed): ' rtsp_password
      printf '\n' >&2
    fi
  fi

  # Arm the name for failure diagnostics before mutation, but never issue a
  # name-addressed rollback unless this POST later returns success and thereby
  # establishes ownership. Failure/transport-unknown responses expose no ID.
  PARTIAL_RTSP_NAME="${source_name}"
  response="$(
    printf '%s\0%s\0%s\0%s\0' \
      "${rtsp_url}" "${source_name}" "${rtsp_username}" "${rtsp_password}" |
      jq -Rs '
        split("\u0000") |
        {sensorUrl: .[0], name: .[1], username: .[2], password: .[3],
         location: "", tags: ""}
      ' |
      curl -fsS --connect-timeout 5 --max-time 600 -X POST \
        -H 'Content-Type: application/json' --data-binary @- \
        -- "${VSS_AGENT_URL}/api/v1/rtsp-streams/add"
  )"
  if ! printf '%s' "${response}" | jq -e '.status == "success"' >/dev/null; then
    fail 'agent reported RTSP registration failure'
  fi
  RTSP_ADD_CONFIRMED=true
  rtsp_password=''

  video_id=''
  while ((SECONDS < deadline)); do
    if video_id="$(resolve_video_id_by_name "${source_name}" 2>/dev/null)"; then
      break
    fi
    sleep 2
  done
  [[ -n "${video_id}" ]] || fail 'registered RTSP source did not resolve to one VST sensor ID'
  validate_video_id "${video_id}"
  PARTIAL_RTSP_SENSOR="${video_id}"
  wait_rtsp_searchable "${source_name}"
  RTSP_INGEST_DONE=true
  printf 'RTSP ingestion searchable: sensor=%s name=%s\n' "${video_id}" "${source_name}"
}

delete_status_and_verify() {
  local source_type="$1" video_id="$2" source_name="$3" response="$4"
  local allow_reconciled="${5:-false}" status api_ok=false verify_ok=false
  status="$(printf '%s' "${response}" | jq -er '.status | strings')"
  if [[ "${status}" == success ]] ||
    [[ "${allow_reconciled}" == true && "${status}" == partial ]]; then
    api_ok=true
  fi
  if wait_deleted_state "${source_type}" "${video_id}" "${source_name}"; then
    verify_ok=true
  fi
  if [[ "${api_ok}" == true && "${verify_ok}" == true ]]; then
    printf '%s deletion complete and verified: sensor=%s name=%s agent_status=%s\n' \
      "${source_type}" "${video_id}" "${source_name}" "${status}"
    return 0
  fi
  fail "agent deletion status=${status}; post-delete verification=${verify_ok}"
}

do_file_delete() {
  local video_id="${VIDEO_ID:-}" source_name="${SOURCE_NAME:-}" response status
  require_value VIDEO_ID "${video_id}"
  validate_video_id "${video_id}"
  validate_source_name "${source_name}"
  vst_source_pair_matches video_file "${video_id}" "${source_name}" ||
    fail 'VIDEO_ID and SOURCE_NAME do not identify the same unique live uploaded-file source; refusing deletion'
  response="$(
    curl -fsS --connect-timeout 5 --max-time 600 -X DELETE \
      -- "${VSS_AGENT_URL}/api/v1/videos/${video_id}"
  )"
  status="$(
    printf '%s' "${response}" |
      jq -er --arg id "${video_id}" \
        'select(.video_id == $id) | .status | strings'
  )" || fail 'agent file deletion returned an invalid or mismatched response'
  case "${status}" in
    success|partial) ;;
    failure) fail 'agent file deletion failed before state could be safely reconciled' ;;
    *) fail "agent file deletion returned unsupported status=${status}" ;;
  esac
  reconcile_file_delete_state "${video_id}" "${source_name}" ||
    fail "agent file deletion status=${status}; independent reconciliation failed"
  delete_status_and_verify video_file "${video_id}" "${source_name}" "${response}" true
}

do_rtsp_delete() {
  local video_id="${VIDEO_ID:-}" source_name="${SOURCE_NAME:-}"
  local source_segment response status
  require_value VIDEO_ID "${video_id}"
  validate_video_id "${video_id}"
  validate_source_name "${source_name}"
  vst_source_pair_matches rtsp "${video_id}" "${source_name}" ||
    fail 'VIDEO_ID and SOURCE_NAME do not identify the same unique live RTSP source; refusing deletion'
  source_segment="$(printf '%s' "${source_name}" | jq -sRr @uri)"
  response="$(
    curl -fsS --connect-timeout 5 --max-time 600 -X DELETE \
      -- "${VSS_AGENT_URL}/api/v1/rtsp-streams/delete/${source_segment}"
  )"
  status="$(
    printf '%s' "${response}" |
      jq -er --arg name "${source_name}" \
        'select(.name == $name) | .status | strings'
  )" || fail 'agent RTSP deletion returned an invalid or mismatched response'
  [[ "${status}" == success ]] || fail "agent RTSP deletion status=${status}; refusing to mask incomplete cleanup"
  delete_indexed_history rtsp "${video_id}" "${source_name}"
  delete_status_and_verify rtsp "${video_id}" "${source_name}" "${response}"
}

main() {
  setup_access
  discover_indexes
  case "${ACTION}" in
    file-ingest) do_file_ingest ;;
    rtsp-ingest) do_rtsp_ingest ;;
    file-delete) do_file_delete ;;
    rtsp-delete) do_rtsp_delete ;;
    *) fail "unsupported ACTION=${ACTION}" ;;
  esac
}

if [[ "${VSS_SEARCH_ARCHIVE_SOURCE_ONLY:-false}" != true ]]; then
  main
fi
