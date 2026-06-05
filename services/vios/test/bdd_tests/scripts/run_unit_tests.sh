#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Run unit tests (API tests) grouped to match the VST containers the default
# stream-processing docker-compose deployment exposes (sensor-ms +
# streamprocessing-ms), producing one CSV and one JUnit XML per group under
# reports/unit_tests/:
#
#   vst-sensor.xml          <- sensor_management
#   vst-streamprocessing.xml <- live_stream, replay_stream, rtsp_proxy,
#                               storage_management, stream_recorder
#   vst-mcp.xml             <- mcp_gateway (kept for dashboard continuity;
#                              tests are gated by the mcp_gateway pytest
#                              marker and skipped by default in this flow
#                              because vst-mcp is not part of the
#                              stream-processing compose stack)
#
# Usage:
#   ./scripts/run_unit_tests.sh [--base-url http://host:30888]
#
# Any extra arguments are forwarded to every pytest invocation.
#
# -e is intentionally omitted so the summary block runs even when pytest
# reports test failures.  The worst exit code is propagated via OVERALL_EXIT.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

TEST_DIR="tests/unit_tests"
REPORTS_DIR="${PROJECT_DIR}/reports/unit_tests"

# Detect container environment
if [[ -d "/app/reports" ]]; then
    REPORTS_DIR="/app/reports/unit_tests"
fi

mkdir -p "${REPORTS_DIR}"

EXTRA_ARGS=("$@")

echo "=============================================="
echo "VST Unit Tests (per deployed container)"
echo "=============================================="
echo "Reports directory: ${REPORTS_DIR}"
echo ""

OVERALL_EXIT=0
GROUP_RESULTS=()

# Container groups: <junit-name>|<csv-name>|<space-separated test subdirs>
# Update this list if the deployed container topology changes.
GROUPS=(
    "vst-sensor|sensor_management|sensor_management"
    "vst-mcp|mcp_gateway|mcp_gateway"
    "vst-streamprocessing|streamprocessing|live_stream replay_stream rtsp_proxy storage_management stream_recorder"
)

for GROUP in "${GROUPS[@]}"; do
    JUNIT_NAME="${GROUP%%|*}"
    REST="${GROUP#*|}"
    CSV_NAME="${REST%%|*}"
    SUBDIRS="${REST#*|}"

    # Resolve subdirs that actually contain test_*.py files; skip the group
    # entirely if none are present (keeps things robust against module
    # removal or rename).
    TEST_PATHS=()
    for SUB in ${SUBDIRS}; do
        MODULE_DIR="${PROJECT_DIR}/${TEST_DIR}/${SUB}"
        if [[ -d "${MODULE_DIR}" ]] && ls "${MODULE_DIR}"/test_*.py 1>/dev/null 2>&1; then
            TEST_PATHS+=("${MODULE_DIR}")
        fi
    done

    if [[ ${#TEST_PATHS[@]} -eq 0 ]]; then
        echo "Skipping ${JUNIT_NAME}: no test files found in (${SUBDIRS})"
        echo ""
        continue
    fi

    CSV_FILE="${REPORTS_DIR}/${CSV_NAME}.csv"
    JUNIT_FILE="${REPORTS_DIR}/${JUNIT_NAME}.xml"

    echo "----------------------------------------------"
    echo "Container: ${JUNIT_NAME}"
    echo "  Sources: ${TEST_PATHS[*]##*/}"
    echo "  CSV:     ${CSV_FILE}"
    echo "  JUnit:   ${JUNIT_FILE}"
    echo "----------------------------------------------"

    poetry run pytest "${TEST_PATHS[@]}" \
        --csv="${CSV_FILE}" \
        --junitxml="${JUNIT_FILE}" \
        --override-ini="addopts=" \
        -v --tb=short --color=yes \
        --disable-container-monitor \
        ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    GROUP_EXIT=$?

    GROUP_RESULTS+=("${JUNIT_NAME}|${CSV_NAME}|${GROUP_EXIT}")

    if [[ "${GROUP_EXIT}" -ne 0 && "${OVERALL_EXIT}" -eq 0 ]]; then
        OVERALL_EXIT=${GROUP_EXIT}
    fi

    echo ""
done

echo "=============================================="
echo "Unit Test Summary"
echo "=============================================="
for RESULT in ${GROUP_RESULTS[@]+"${GROUP_RESULTS[@]}"}; do
    JUNIT_NAME="${RESULT%%|*}"
    REST="${RESULT#*|}"
    CSV_NAME="${REST%%|*}"
    GROUP_EXIT="${REST#*|}"

    CSV_FILE="${REPORTS_DIR}/${CSV_NAME}.csv"

    if [[ "${GROUP_EXIT}" -eq 0 ]]; then
        STATUS="PASS"
    else
        STATUS="FAIL (exit ${GROUP_EXIT})"
    fi

    if [[ -f "${CSV_FILE}" ]]; then
        LINE_COUNT=$(wc -l < "${CSV_FILE}")
        if [[ "${LINE_COUNT}" -le 1 ]]; then
            TEST_COUNT=0
        else
            TEST_COUNT=$((LINE_COUNT - 1))
        fi
        echo "  [${STATUS}] ${JUNIT_NAME}: ${TEST_COUNT} test(s)"
    else
        echo "  [${STATUS}] ${JUNIT_NAME}: NO CSV generated"
    fi
done

echo ""
echo "Overall exit code: ${OVERALL_EXIT}"

exit ${OVERALL_EXIT}
