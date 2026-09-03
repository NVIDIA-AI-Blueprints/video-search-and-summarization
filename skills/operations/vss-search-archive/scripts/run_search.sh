#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_search.sh --source-scoped <true|false> -- <embed|attribute|fusion|object> [search options]
       run_search.sh --list-sources
EOF
  exit 2
}

action=search
if [[ ${1:-} == --list-sources ]]; then
  action=list-sources
  shift
  [[ $# -eq 0 ]] || usage
else
  [[ ${1:-} == --source-scoped ]] || usage
  source_scoped=${2:-}
  [[ $source_scoped == true || $source_scoped == false ]] || usage
  shift 2
  [[ ${1:-} == -- ]] || usage
  shift
  [[ $# -gt 0 ]] || usage

  search_args=("$@")
  source_count=0
  for ((index = 0; index < ${#search_args[@]}; index++)); do
    argument=${search_args[$index]}
    if [[ $argument == --video-source ]]; then
      ((index + 1 < ${#search_args[@]})) || {
        echo "--video-source requires a value" >&2
        exit 2
      }
      [[ -n ${search_args[$((index + 1))]} ]] || {
        echo "--video-source cannot be empty" >&2
        exit 2
      }
      ((source_count += 1))
      ((index += 1))
    elif [[ $argument == --video-source=* ]]; then
      [[ -n ${argument#--video-source=} ]] || {
        echo "--video-source cannot be empty" >&2
        exit 2
      }
      ((source_count += 1))
    fi
  done

  if [[ $source_scoped == true && $source_count -eq 0 ]]; then
    echo "Resolved source scope is empty; refusing an unrestricted search" >&2
    exit 2
  fi
fi

receipt=${VSS_CAPABILITY_RECEIPT:-${HOME}/.vss/agent-capabilities.json}
if [[ -z ${VSS_REPO_ROOT:-} && -f $receipt ]]; then
  VSS_REPO_ROOT=$(jq -er \
    '.runtime.repo_root | select(type == "string" and length > 0)' \
    "$receipt") || exit 1
fi
VSS_REPO_ROOT=${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}
[[ -f $VSS_REPO_ROOT/services/agent/pyproject.toml ]] || {
  echo "VSS checkout not found at $VSS_REPO_ROOT; set VSS_REPO_ROOT explicitly" >&2
  exit 1
}

vss=(
  uv run --project "$VSS_REPO_ROOT/services/agent" --no-dev --extra cli vss
)
cd "$VSS_REPO_ROOT"
"${vss[@]}" search run --help >/dev/null

if [[ -z ${VSS_ORIGIN:-} && -f $receipt ]]; then
  receipt_origin=$(jq -er \
    '(.vss_origin // "") | select(type == "string")' \
    "$receipt") || exit 1
  [[ -z $receipt_origin ]] || VSS_ORIGIN=$receipt_origin
fi
if [[ -z ${VSS_ORIGIN:-} ]]; then
  VSS_ORIGIN=$("${vss[@]}" configure show 2>/dev/null | \
    jq -er '.base_url | select(type == "string" and length > 0)') || true
fi
if [[ -z ${VSS_ORIGIN:-} && -n ${HOST_IP:-} ]]; then
  VSS_ORIGIN="http://${HOST_IP}:7777"
fi
[[ -n ${VSS_ORIGIN:-} ]] || {
  echo "Provide the Compose or Ingress origin" >&2
  exit 1
}
VSS_ORIGIN=${VSS_ORIGIN%/}
"${vss[@]}" configure --base-url "$VSS_ORIGIN" >/dev/null

if [[ $action == list-sources ]]; then
  exec "${vss[@]}" vios list
fi

if ! search_stream=$("${vss[@]}" search run "${search_args[@]}"); then
  echo "Search command failed" >&2
  exit 1
fi

mapfile -t search_documents <<<"$search_stream"
if [[ ${#search_documents[@]} -ne 2 ]]; then
  echo "Search did not return one result body and one completion marker" >&2
  exit 1
fi
search_json=${search_documents[0]}
search_completion=${search_documents[1]}
search_job_id=$(printf '%s' "$search_json" | jq -er '
  select(type == "object" and (.data | type == "array")) |
  .job_id | select(type == "string" and length > 0)
') || {
  echo "Search did not return a SearchOutput object with a data array and job_id" >&2
  exit 1
}
printf '%s' "$search_completion" | jq -e --arg job_id "$search_job_id" '
  type == "object" and
  .event == "vss_job_completed" and
  .group == "search" and
  .job_id == $job_id and
  .status == "completed" and
  .exit_hint == 0
' >/dev/null || {
  echo "Search completion marker did not validate" >&2
  exit 1
}

# Keep the validated CLI pair visible to both the agent and the OpenClaw
# connector. The connector can create the artifact directly from this pair.
printf '%s\n%s\n' "$search_json" "$search_completion"

# Responses-style harnesses can consume the same validated presentation
# contract from tool output or the agent's final-text fallback.
if [[ -f $receipt ]] &&
  jq -e '.ui_artifacts.version == "1.0"' "$receipt" >/dev/null; then
  artifact=$(jq -cn --argjson payload "$search_json" \
    '{version:"1.0",kind:"vss.search.results",payload:$payload}') || exit 1
  printf '<vss-ui-artifact>%s</vss-ui-artifact>\n' "$artifact"
fi
