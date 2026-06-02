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
# Check 08: HTTP POST Endpoint
# Mode: HTTP
# Description: Verify Alert Bridge REST API accepts a JSON incident POST
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
source "$SCRIPT_DIR/config_p1.env"
source "$SCRIPT_DIR/lib/helpers_p1.sh"

CHECK_NAME="HTTP POST Endpoint"

if [[ -z "${AB_HOST:-}" ]]; then
    skip_check "$CHECK_NAME" "AB_HOST not set"
    exit 0
fi

REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"

if [[ ! -f "$PAYLOAD" ]]; then
    skip_check "$CHECK_NAME" "Sample payload not found: $PAYLOAD"
    exit 0
fi

status=$(curl -s -o /dev/null -w "%{http_code}" \
    --connect-timeout 5 --max-time 10 \
    -X POST "http://${AB_HOST}:${AB_PORT}/api/v1/incidents" \
    -H "Content-Type: application/json" \
    -d @"$PAYLOAD" 2>/dev/null || echo "")

if [[ -z "$status" ]]; then
    skip_check "$CHECK_NAME" "No response from ${AB_HOST}:${AB_PORT} (service may not be running)"
    exit 0
fi

if [[ "$status" == "202" ]]; then
    pass "$CHECK_NAME" "POST /api/v1/incidents returned HTTP $status (accepted)"
elif [[ "$status" =~ ^5 ]]; then
    fail "$CHECK_NAME" "POST /api/v1/incidents returned HTTP $status (server error)" || true
    exit 1
else
    fail "$CHECK_NAME" "POST /api/v1/incidents returned unexpected HTTP $status" || true
    exit 1
fi
