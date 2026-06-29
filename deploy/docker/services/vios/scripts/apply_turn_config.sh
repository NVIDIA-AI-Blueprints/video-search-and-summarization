#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

config_file="${VST_CONFIG_FILE:-/home/vst/vst_release/configs/vst_config.json}"

if [[ ! -f "${config_file}" ]]; then
  echo "[apply-turn-config] ${config_file} not found; skipping"
  exit 0
fi

turn_urls="${VST_STATIC_TURNURL_LIST:-}"

json_array="[]"
if [[ -n "${turn_urls}" ]]; then
  json_array="["
  old_ifs="${IFS}"
  IFS=','
  first=1
  for raw_url in ${turn_urls}; do
    url="${raw_url#${raw_url%%[![:space:]]*}}"
    url="${url%${url##*[![:space:]]}}"
    [[ -n "${url}" ]] || continue
    case "${url}" in
      *"<HOST_IP>"*|*'${'*)
        continue
        ;;
    esac
    escaped="${url//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    if [[ "${first}" -eq 0 ]]; then
      json_array+=","
    fi
    json_array+="\"${escaped}\""
    first=0
  done
  IFS="${old_ifs}"
  json_array+="]"
fi

tmp_file="$(mktemp)"
awk -v static_urls="${json_array}" '
  {
    line = $0
    sub(/"static_turnurl_list"[[:space:]]*:[[:space:]]*\[[^]]*\]/, "\"static_turnurl_list\": " static_urls, line)
    sub(/"use_coturn_auth_secret"[[:space:]]*:[[:space:]]*(true|false)/, "\"use_coturn_auth_secret\": false", line)
    sub(/"coturn_turnurl_list_with_secret"[[:space:]]*:[[:space:]]*\[[^]]*\]/, "\"coturn_turnurl_list_with_secret\": []", line)
    print line
  }
' "${config_file}" > "${tmp_file}"
cat "${tmp_file}" > "${config_file}"
rm -f "${tmp_file}"

echo "[apply-turn-config] configured network.static_turnurl_list=${json_array}"
