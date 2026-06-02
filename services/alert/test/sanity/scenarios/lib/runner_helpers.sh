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
# scenarios/lib/runner_helpers.sh
#
# Shared runner-side helpers:
#   - RUN_ID generation + per-run / per-case state dirs
#   - synthetic sensor-id factory
#   - revert hook registration + EXIT trap firing
#
# Sourced by runner.sh exactly once per invocation. NOT sourced by .case files.
# =============================================================================

# ---- RUN_ID ----------------------------------------------------------------
init_run_id() {
    if [[ -z "${SANITY_RUN_ID:-}" ]]; then
        SANITY_RUN_ID="$(date +%Y%m%d%H%M%S)-$$"
    fi
    SANITY_RUN_DIR="${SANITY_RUN_DIR:-/tmp/sanity-drops/${SANITY_RUN_ID}}"
    mkdir -p "$SANITY_RUN_DIR"
    export SANITY_RUN_ID SANITY_RUN_DIR
}

# Per-case state dir, created and exposed each iteration.
init_case_state() {
    local case_id="$1"
    SANITY_CASE_DIR="${SANITY_RUN_DIR}/${case_id}"
    mkdir -p "$SANITY_CASE_DIR"
    export SANITY_CASE_DIR
}

# ---- Synthetic sensor IDs --------------------------------------------------
# Cases call: synth_sensor_id "<test-tag>" "<index>"
#   -> "sanity-<RUN_ID>-<test-tag>-<index>"
# E.g. synth_sensor_id "iscomplete" "1" -> sanity-20260430143012-12345-iscomplete-1
synth_sensor_id() {
    local tag="$1" idx="${2:-1}"
    echo "sanity-${SANITY_RUN_ID}-${tag}-${idx}"
}

# ---- Revert hook registration ---------------------------------------------
# Cases optionally define `revert()` (a function). The runner registers it
# *before* calling apply(), so a partial apply() failure still triggers revert.
# Multiple cases register sequentially; runner clears between cases.
SANITY_REVERT_FNS=()

register_revert() {
    SANITY_REVERT_FNS+=("$1")
}

run_reverts() {
    local fn
    for ((i=${#SANITY_REVERT_FNS[@]}-1; i>=0; i--)); do
        fn="${SANITY_REVERT_FNS[$i]}"
        log_info "[revert] running $fn"
        "$fn" || log_warn "[revert] $fn returned non-zero (continuing)"
    done
    SANITY_REVERT_FNS=()
}

