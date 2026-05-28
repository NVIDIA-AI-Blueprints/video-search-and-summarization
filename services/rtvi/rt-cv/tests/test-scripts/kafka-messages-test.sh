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
# Consume the compose Kafka topic; pass if at least MIN_MESSAGES are received.
# Exit 0 on success, 1 on failure.
# Prerequisite: stack running with streams added (add-stream-test.sh).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_env

if ! compose ps --status running kafka 2>/dev/null | grep -q kafka; then
  test_fail "kafka service is not running (run ./deploy.sh first)"
fi

msg_file="$(mktemp "${TMPDIR:-/tmp}/rtcv-kafka-msgs.XXXXXX")"
trap 'rm -f "${msg_file}"' EXIT

echo "=== Kafka messages test (topic ${KAFKA_TOPIC}, timeout ${CONSUME_TIMEOUT_SEC}s) ==="

timeout_ms=$((CONSUME_TIMEOUT_SEC * 1000))
set +e
compose exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server "${KAFKA_BOOTSTRAP}" \
  --topic "${KAFKA_TOPIC}" \
  --from-beginning \
  --timeout-ms "${timeout_ms}" \
  >"${msg_file}" 2>/dev/null
set -e

line_count="$(grep -cve '^[[:space:]]*$' "${msg_file}" || true)"
echo "Received ${line_count} message(s)."

if [[ "${line_count}" -lt "${MIN_MESSAGES}" ]]; then
  test_fail "expected at least ${MIN_MESSAGES} message(s), got ${line_count} (add streams and check rt-cv logs)"
fi

test_pass "received ${line_count} Kafka message(s) on topic '${KAFKA_TOPIC}'"
