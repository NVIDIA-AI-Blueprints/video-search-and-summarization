#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Persist one source-setup deadline across independent agent shell calls.
set -euo pipefail

usage() {
  echo "usage: source_setup_budget.sh start <seconds> | remaining <request-cap> | deadline" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
COMMAND=$1
shift

STATE_DIR=${VSS_CONFIG_HOME:-${HOME}/.vss}
STATE_FILE=${VSS_SEARCH_BUDGET_FILE:-${STATE_DIR}/search-source-setup.deadline}

read_deadline() {
  [[ -r "${STATE_FILE}" ]] || {
    echo "Search source-setup budget is not initialized" >&2
    return 1
  }
  IFS= read -r SEARCH_READINESS_DEADLINE <"${STATE_FILE}"
  [[ "${SEARCH_READINESS_DEADLINE}" =~ ^[0-9]+$ ]] || {
    echo "Search source-setup deadline state is invalid" >&2
    return 1
  }
}

case "${COMMAND}" in
  start)
    [[ $# -eq 1 && $1 =~ ^[1-9][0-9]*$ ]] || usage
    BUDGET_SECONDS=$1
    mkdir -p -- "${STATE_DIR}"
    umask 077
    SEARCH_READINESS_DEADLINE=$(($(date +%s) + BUDGET_SECONDS))
    printf '%s\n' "${SEARCH_READINESS_DEADLINE}" >"${STATE_FILE}"
    ;;
  remaining)
    [[ $# -eq 1 && $1 =~ ^[1-9][0-9]*$ ]] || usage
    REQUEST_CAP=$1
    read_deadline
    CURRENT_EPOCH=$(date +%s)
    REMAINING=$((SEARCH_READINESS_DEADLINE - CURRENT_EPOCH))
    (( REMAINING > 0 )) || {
      echo "Search source-setup deadline exhausted" >&2
      exit 1
    }
    (( REQUEST_CAP < REMAINING )) && printf '%s\n' "${REQUEST_CAP}" || printf '%s\n' "${REMAINING}"
    ;;
  deadline)
    [[ $# -eq 0 ]] || usage
    read_deadline
    printf '%s\n' "${SEARCH_READINESS_DEADLINE}"
    ;;
  *) usage ;;
esac
