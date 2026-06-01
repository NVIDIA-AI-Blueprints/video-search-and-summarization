#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# =============================================================================
# Sanity Drops Runner — sequential walker
# =============================================================================
# Each .case file declares:
#   - PROBE                       : path to probe script (relative to scenarios/)
#   - PROBE_ENV_*                 : env vars to pass to probe (prefix stripped)
#   - WAIT_TRAFFIC_SECONDS        : sleep before probe (let traffic flow)
#   - apply()  [optional]         : function that mutates AB state pre-probe
#   - revert() [optional]         : function called by EXIT trap to restore baseline
#
# Sources scenarios/lib/runner_helpers.sh + scenarios/lib/inject.sh once at start.
# Cases can call: synth_sensor_id, register_revert, inject_kafka_incident,
# inject_rest_incident, replay_last_kafka_inject, last_http_status.
#
# Env knobs (all optional):
#   SANITY_RUN_ID            override run id
#   SANITY_RUN_DIR           override per-run state dir
#   SANITY_ALLOW_RESTART     1 = let WITH_APPLY cases restart AB
#   SANITY_PER_CASE_TIMEOUT  seconds; default 300 — kills stuck cases
#   ES_HOST, AB_HOST, BOOTSTRAP, etc. — see lib/inject.sh
#
# Usage:
#   ./runner.sh                           # walk all cases/*.case
#   ./runner.sh cases/01-foo.case         # run one case
#   ./runner.sh --json                    # JSON summary
# =============================================================================

set -uo pipefail

SCENARIOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANITY_DIR="$(dirname "$SCENARIOS_DIR")"
export SCENARIOS_DIR SANITY_DIR

export SANITY_RESULTS_FILE="/tmp/sanity_drops_results_$$"
rm -f "$SANITY_RESULTS_FILE"; touch "$SANITY_RESULTS_FILE"

source "$SANITY_DIR/lib/common.sh"
# common.sh sets `set -eo pipefail` which would kill the runner on the first
# failing probe. The runner needs to keep going across cases — disable -e here.
set +e
source "$SCENARIOS_DIR/lib/runner_helpers.sh"
source "$SCENARIOS_DIR/lib/inject.sh"

init_run_id
SCRIPT_START_TIME=$(date +%s)
PER_CASE_TIMEOUT="${SANITY_PER_CASE_TIMEOUT:-300}"

# Cleanup on exit: results file + run state dir (keep RUN_DIR if SANITY_KEEP_STATE=1)
cleanup_exit() {
    rm -f "$SANITY_RESULTS_FILE"
    if [[ "${SANITY_KEEP_STATE:-0}" != "1" ]]; then
        rm -rf "$SANITY_RUN_DIR"
    fi
}
trap cleanup_exit EXIT

# -----------------------------------------------------------------------------
# Args
# -----------------------------------------------------------------------------
JSON_OUTPUT=false
CASES=()
for arg in "$@"; do
    case "$arg" in
        --json) JSON_OUTPUT=true ;;
        *)      CASES+=("$arg") ;;
    esac
done

if [[ ${#CASES[@]} -eq 0 ]]; then
    while IFS= read -r f; do CASES+=("$f"); done < <(ls "$SCENARIOS_DIR"/cases/*.case 2>/dev/null | sort)
fi
if [[ ${#CASES[@]} -eq 0 ]]; then
    echo "ERROR: no .case files found under $SCENARIOS_DIR/cases/" >&2
    exit 2
fi

log_info "[runner] RUN_ID=$SANITY_RUN_ID  state_dir=$SANITY_RUN_DIR  allow_restart=${SANITY_ALLOW_RESTART:-0}  cases=${#CASES[@]}"

# -----------------------------------------------------------------------------
# Per-case execution
# -----------------------------------------------------------------------------
run_case() {
    local case_file="$1"
    local case_id; case_id="$(basename "$case_file" .case)"

    # Reset case-scoped state
    unset PROBE WAIT_TRAFFIC_SECONDS
    unset -f apply revert 2>/dev/null
    while IFS= read -r v; do unset "$v"; done < <(compgen -v PROBE_ENV_ 2>/dev/null || true)
    SANITY_REVERT_FNS=()

    init_case_state "$case_id"
    log_info "[$case_id] starting (state: $SANITY_CASE_DIR)"

    # shellcheck disable=SC1090
    source "$case_file"

    # If the case defined revert(), register it now so apply() failure still triggers it
    if declare -F revert >/dev/null; then
        register_revert revert
    fi

    # Apply phase
    if declare -F apply >/dev/null; then
        if ! apply; then
            fail "$case_id" "apply phase failed"
            run_reverts
            return
        fi
    fi

    # Wait for traffic
    if [[ -n "${WAIT_TRAFFIC_SECONDS:-}" && "$WAIT_TRAFFIC_SECONDS" -gt 0 ]]; then
        log_info "[$case_id] waiting ${WAIT_TRAFFIC_SECONDS}s for traffic"
        sleep "$WAIT_TRAFFIC_SECONDS"
    fi

    # Probe phase
    if [[ -z "${PROBE:-}" ]]; then
        fail "$case_id" "no PROBE specified in case file"
        run_reverts
        return
    fi
    local probe_path="$SCENARIOS_DIR/$PROBE"
    if [[ ! -f "$probe_path" ]]; then
        fail "$case_id" "probe not found: $PROBE"
        run_reverts
        return
    fi

    # Build env list from PROBE_ENV_* and run probe (with timeout)
    local probe_env=()
    while IFS= read -r v; do
        probe_env+=("${v#PROBE_ENV_}=${!v}")
    done < <(compgen -v PROBE_ENV_ 2>/dev/null || true)

    timeout "$PER_CASE_TIMEOUT" env "${probe_env[@]}" \
        SANITY_RESULTS_FILE="$SANITY_RESULTS_FILE" \
        SANITY_RUN_ID="$SANITY_RUN_ID" \
        SANITY_CASE_DIR="$SANITY_CASE_DIR" \
        bash "$probe_path" || true
    local rc=$?
    if [[ $rc -eq 124 ]]; then
        fail "$case_id" "probe timed out after ${PER_CASE_TIMEOUT}s"
    fi

    run_reverts
}

# -----------------------------------------------------------------------------
# Walk cases
# -----------------------------------------------------------------------------
for cf in "${CASES[@]}"; do
    [[ -f "$cf" ]] || { fail "$(basename "$cf")" "case file not found"; continue; }
    run_case "$cf"
done

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
passed=$(count_results "PASS")
failed=$(count_results "FAIL")
skipped=$(count_results "SKIP")

if [[ "$JSON_OUTPUT" == "true" ]]; then
    results_to_json
else
    echo ""
    echo "═══════════════════════════════════════════════════"
    echo "  Sanity Drops Summary  (run $SANITY_RUN_ID)"
    echo "═══════════════════════════════════════════════════"
    echo "  Passed:  $passed"
    echo "  Failed:  $failed"
    echo "  Skipped: $skipped"
    echo "═══════════════════════════════════════════════════"
fi

[[ "$failed" -eq 0 ]] && exit 0 || exit 1
