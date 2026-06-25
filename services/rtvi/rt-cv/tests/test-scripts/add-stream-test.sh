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
# Add a sample file stream via POST /api/v1/stream/add
# Exit 0 on success, 1 on failure.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_env

wait_for_rest

camera_url="$(test_camera_url)"
echo "=== Add stream test (${TEST_CAMERA_ID}) ==="

http_code="$(curl -sS -o /dev/null -w '%{http_code}' -XPOST "${REST_URL}/api/v1/stream/add" \
  -H 'Content-Type: application/json' \
  -d "{
  \"key\": \"sensor\",
  \"value\": {
      \"camera_id\": \"${TEST_CAMERA_ID}\",
      \"camera_name\": \"${TEST_CAMERA_ID}\",
      \"camera_url\": \"${camera_url}\",
      \"change\": \"camera_add\",
      \"metadata\": {
          \"resolution\": \"1920x1080\",
          \"codec\": \"h264\",
          \"framerate\": 30
      }
  },
  \"headers\": {
      \"source\": \"rtcv-test\",
      \"created_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
  }
}")"

if [[ "${http_code}" != "200" ]]; then
  test_fail "stream/add returned HTTP ${http_code} (expected 200) for ${camera_url}"
fi

test_pass "stream/add succeeded for ${TEST_CAMERA_ID} (${camera_url})"
