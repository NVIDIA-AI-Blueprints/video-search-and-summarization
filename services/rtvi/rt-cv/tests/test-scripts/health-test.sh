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
# Health probe test: GET /api/v1/live, /api/v1/ready, /api/v1/startup
# Exit 0 if all return HTTP 200, else 1.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_env

wait_for_rest

check_probe() {
  local name="$1"
  local path="$2"
  local url="${REST_URL}${path}"
  local http_code body

  body="$(mktemp)"
  http_code="$(curl -sS -o "${body}" -w '%{http_code}' --connect-timeout 5 "${url}" || echo "000")"

  if [[ "${http_code}" != "200" ]]; then
    rm -f "${body}"
    test_fail "${name} ${path} returned HTTP ${http_code} (expected 200)"
  fi

  if ! grep -q '"status"' "${body}" 2>/dev/null; then
    rm -f "${body}"
    test_fail "${name} ${path} response missing JSON status field"
  fi

  rm -f "${body}"
  echo "  OK ${path} (HTTP 200)"
}

echo "=== Health probe test ==="
check_probe "Liveness" "/api/v1/live"
check_probe "Readiness" "/api/v1/ready"
check_probe "Startup" "/api/v1/startup"

test_pass "All health probes responded with HTTP 200"
