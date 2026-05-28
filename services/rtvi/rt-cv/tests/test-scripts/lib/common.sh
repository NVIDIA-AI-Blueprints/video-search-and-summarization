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
# Shared helpers for RT-CV compose integration tests.
# Source from test scripts:  source "$(dirname "$0")/lib/common.sh"

if [[ -n "${RTCV_TEST_COMMON_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
RTCV_TEST_COMMON_LOADED=1

set -euo pipefail

_caller="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
if [[ "$(basename "${_caller}")" == "lib" ]]; then
  TEST_SCRIPTS_DIR="$(cd "${_caller}/.." && pwd)"
else
  TEST_SCRIPTS_DIR="${_caller}"
fi
COMPOSE_DIR="$(cd "${TEST_SCRIPTS_DIR}/../docker-compose" && pwd)"

load_env() {
  if [[ -f "${COMPOSE_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${COMPOSE_DIR}/.env"
    set +a
  fi
  REST_URL="${REST_URL:-http://localhost:9000}"
  STREAMS_DIR="${STREAMS_DIR:-/opt/nvidia/deepstream/deepstream/samples/streams}"
  KAFKA_TOPIC="${KAFKA_TOPIC:-ds-perception}"
  KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
  CONSUME_TIMEOUT_SEC="${CONSUME_TIMEOUT_SEC:-90}"
  MIN_MESSAGES="${MIN_MESSAGES:-1}"
  PIPELINE_WARMUP_SEC="${PIPELINE_WARMUP_SEC:-45}"
  REST_TIMEOUT_SEC="${REST_TIMEOUT_SEC:-300}"
  KAFKA_HEALTH_TIMEOUT_SEC="${KAFKA_HEALTH_TIMEOUT_SEC:-120}"
  TEST_CAMERA_ID="${TEST_CAMERA_ID:-camera_0}"
  TEST_STREAM_FILE="${TEST_STREAM_FILE:-sample_1080p_h264.mp4}"
}

compose() {
  (cd "${COMPOSE_DIR}" && docker compose "$@")
}

test_pass() {
  echo "PASS: $*"
  exit 0
}

test_fail() {
  echo "FAIL: $*" >&2
  exit 1
}

wait_for_kafka_healthy() {
  local deadline=$((SECONDS + KAFKA_HEALTH_TIMEOUT_SEC))
  echo "Waiting for Kafka (container rtcv-test-kafka) ..."
  until [[ "$(docker inspect --format='{{.State.Health.Status}}' rtcv-test-kafka 2>/dev/null || echo none)" == "healthy" ]]; do
    if (( SECONDS >= deadline )); then
      compose logs kafka | tail -20 >&2 || true
      test_fail "Kafka did not become healthy within ${KAFKA_HEALTH_TIMEOUT_SEC}s"
    fi
    sleep 2
  done
}

wait_for_rest() {
  local deadline=$((SECONDS + REST_TIMEOUT_SEC))
  echo "Waiting for REST at ${REST_URL} ..."
  until curl -sS --connect-timeout 2 "${REST_URL}" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      test_fail "REST server not reachable at ${REST_URL} within ${REST_TIMEOUT_SEC}s"
    fi
    sleep 3
  done
}

test_camera_url() {
  printf 'file://%s/%s' "${STREAMS_DIR}" "${TEST_STREAM_FILE}"
}
