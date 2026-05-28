#!/usr/bin/env bash
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
#
# Run the full compose integration test suite in order.
# Exit 0 if all pass, 1 on first failure.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_env

run_test() {
  local name="$1"
  shift
  echo ""
  echo ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>"
  echo ">> ${name}"
  echo ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>"
  "$@"
}

failed=0
run_one() {
  local name="$1"
  shift
  if ! run_test "${name}" "$@"; then
    failed=1
    return 1
  fi
}

if [[ "${SKIP_DEPLOY:-0}" != "1" ]]; then
  run_one "deploy" "${SCRIPT_DIR}/deploy.sh" || exit 1
else
  echo "SKIP_DEPLOY=1: assuming stack is already up"
fi

run_one "health-test" "${SCRIPT_DIR}/health-test.sh" || exit 1
run_one "add-stream-test" "${SCRIPT_DIR}/add-stream-test.sh" || exit 1

echo ""
echo "Warming up pipeline (${PIPELINE_WARMUP_SEC}s) before Kafka consume ..."
sleep "${PIPELINE_WARMUP_SEC}"

run_one "kafka-messages-test" "${SCRIPT_DIR}/kafka-messages-test.sh" || exit 1
run_one "remove-stream-test" "${SCRIPT_DIR}/remove-stream-test.sh" || exit 1

echo ""
echo "PASS: all integration tests succeeded"
exit 0
