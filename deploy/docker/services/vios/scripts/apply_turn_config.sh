#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

config_file="${VST_CONFIG_FILE:-/home/vst/vst_release/configs/vst_config.json}"

if [[ ! -f "${config_file}" ]]; then
  echo "[apply-turn-config] ${config_file} not found; skipping"
  exit 0
fi

explicit_turn_urls="${VST_STATIC_TURNURL_LIST:-}"
turn_url_values=()

trim() {
  local value="${1}"
  value="${value#${value%%[![:space:]]*}}"
  value="${value%${value##*[![:space:]]}}"
  printf '%s' "${value}"
}

append_turn_url() {
  local url
  url="$(trim "${1}")"
  [[ -n "${url}" ]] || return 0
  case "${url}" in
    *"<HOST_IP>"*|*'${'*)
      return 0
      ;;
  esac
  turn_url_values+=("${url}")
}

json_escape() {
  local value="${1}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "${value}"
}

wait_for_password_file() {
  local password_file="${1}"
  local wait_seconds="${TURN_PASSWORD_WAIT_SECONDS:-30}"
  case "${wait_seconds}" in
    ''|*[!0-9]*)
      wait_seconds=30
      ;;
  esac

  local elapsed=0
  while [[ ! -s "${password_file}" && "${elapsed}" -lt "${wait_seconds}" ]]; do
    sleep 1
    elapsed=$((elapsed + 1))
  done
}

if [[ -n "${explicit_turn_urls}" ]]; then
  old_ifs="${IFS}"
  IFS=','
  for raw_url in ${explicit_turn_urls}; do
    append_turn_url "${raw_url}"
  done
  IFS="${old_ifs}"
else
  turn_username="$(trim "${TURN_USERNAME:-}")"
  turn_password_file="$(trim "${TURN_PASSWORD_FILE:-}")"
  turn_public_host="$(trim "${TURN_PUBLIC_HOST:-}")"
  turn_host_port="$(trim "${TURN_HOST_PORT:-${TURN_PORT:-3478}}")"

  if [[ -z "${turn_username}" ]]; then
    echo "[apply-turn-config] TURN_USERNAME is empty; set it to configure VST TURN" >&2
    exit 1
  fi
  if [[ -z "${turn_password_file}" ]]; then
    echo "[apply-turn-config] TURN_PASSWORD_FILE is empty; ensure turnserver-init generated the mounted password file" >&2
    exit 1
  fi
  if [[ ! -s "${turn_password_file}" ]]; then
    wait_for_password_file "${turn_password_file}"
  fi
  if [[ ! -s "${turn_password_file}" ]]; then
    echo "[apply-turn-config] TURN_PASSWORD_FILE is unreadable or empty; ensure turnserver-init generated the mounted password file" >&2
    exit 1
  fi
  if [[ -z "${turn_public_host}" ]]; then
    echo "[apply-turn-config] TURN_PUBLIC_HOST is empty; set the browser-reachable TURN host" >&2
    exit 1
  fi
  case "${turn_public_host}" in
    *"<HOST_IP>"*|*'${'*)
      echo "[apply-turn-config] TURN_PUBLIC_HOST contains an unresolved placeholder" >&2
      exit 1
      ;;
  esac

  turn_password="$(tr -d '\r\n' < "${turn_password_file}")"
  if [[ -z "${turn_password}" ]]; then
    echo "[apply-turn-config] TURN password file is empty" >&2
    exit 1
  fi
  append_turn_url "${turn_username}:${turn_password}@${turn_public_host}:${turn_host_port}"
  unset turn_password
fi

if [[ "${#turn_url_values[@]}" -eq 0 ]]; then
  echo "[apply-turn-config] no usable TURN URLs configured" >&2
  exit 1
fi

json_array="["
first=1
for url in "${turn_url_values[@]}"; do
  escaped="$(json_escape "${url}")"
  if [[ "${first}" -eq 0 ]]; then
    json_array+=","
  fi
  json_array+="\"${escaped}\""
  first=0
done
json_array+="]"

tmp_file="$(mktemp)"
cleanup() {
  rm -f "${tmp_file}"
}
trap cleanup EXIT

STATIC_TURNURL_JSON="${json_array}" awk '
  BEGIN {
    static_urls = ENVIRON["STATIC_TURNURL_JSON"]
  }

  function indent_of(line) {
    match(line, /^[[:space:]]*/)
    return substr(line, RSTART, RLENGTH)
  }

  function array_ends(line) {
    return line ~ /\][[:space:]]*,?[[:space:]]*$/
  }

  function closing_array_line(line) {
    return line ~ /^[[:space:]]*\][[:space:]]*,?[[:space:]]*$/
  }

  function array_comma(line) {
    return (line ~ /\][[:space:]]*,[[:space:]]*$/) ? "," : ""
  }

  function bool_comma(line) {
    return (line ~ /(true|false)[[:space:]]*,[[:space:]]*$/) ? "," : ""
  }

  function emit_array(indent, key, value, comma) {
    print indent "\"" key "\": " value comma
  }

  function begin_array(line, key, value, patch_id,   indent) {
    indent = indent_of(line)
    if (array_ends(line)) {
      emit_array(indent, key, value, array_comma(line))
      if (patch_id == "static") {
        patched_static = 1
      } else {
        patched_secret_list = 1
      }
      return
    }

    skip_key = key
    skip_value = value
    skip_indent = indent
    skip_patch = patch_id
  }

  function finish_array(line) {
    emit_array(skip_indent, skip_key, skip_value, array_comma(line))
    if (skip_patch == "static") {
      patched_static = 1
    } else {
      patched_secret_list = 1
    }
    skip_key = ""
    skip_value = ""
    skip_indent = ""
    skip_patch = ""
  }

  {
    if (skip_key != "") {
      if (closing_array_line($0)) {
        finish_array($0)
      }
      next
    }

    if ($0 ~ /"static_turnurl_list"[[:space:]]*:/) {
      begin_array($0, "static_turnurl_list", static_urls, "static")
      next
    }

    if ($0 ~ /"use_coturn_auth_secret"[[:space:]]*:/) {
      print indent_of($0) "\"use_coturn_auth_secret\": false" bool_comma($0)
      patched_secret = 1
      next
    }

    if ($0 ~ /"coturn_turnurl_list_with_secret"[[:space:]]*:/) {
      begin_array($0, "coturn_turnurl_list_with_secret", "[]", "secret_list")
      next
    }

    print
  }

  END {
    if (skip_key != "") {
      print "[apply-turn-config] unterminated array for " skip_key > "/dev/stderr"
      exit 1
    }
    if (!patched_static) {
      print "[apply-turn-config] static_turnurl_list was not found in " FILENAME > "/dev/stderr"
      exit 1
    }
    if (!patched_secret) {
      print "[apply-turn-config] use_coturn_auth_secret was not found in " FILENAME > "/dev/stderr"
      exit 1
    }
    if (!patched_secret_list) {
      print "[apply-turn-config] coturn_turnurl_list_with_secret was not found in " FILENAME > "/dev/stderr"
      exit 1
    }
  }
' "${config_file}" > "${tmp_file}"
cat "${tmp_file}" > "${config_file}"
trap - EXIT
rm -f "${tmp_file}"

echo "[apply-turn-config] configured network.static_turnurl_list with ${#turn_url_values[@]} URL(s)"
