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

# Test: VST Video URL
# Description: Verify output document contains a non-empty videoUrl pointing to VST simulator
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
ES_HOST="${ES_HOST:-http://127.0.0.1:9200}"
BOOTSTRAP="${BOOTSTRAP:-127.0.0.1:9092}"
TOPIC="${TOPIC:-mdx-incidents}"
PAYLOAD="${REPO_ROOT}/test/protobuf/test_data/sample_incident.json"
TEST_NAME="vst_video_url"
ID_SUFFIX="p1_${TEST_NAME}_$(date +%H%M%S)"
VST_HOST="127.0.0.1:30888"

echo "=== P1: VST Video URL ==="

mkdir -p "$PID_DIR"

# 1. Patch timestamps + produce
PATCHED="$PID_DIR/patched_${TEST_NAME}.json"
patch_timestamps "$PAYLOAD" "$PATCHED"
produce_incident "$REPO_ROOT" "$BOOTSTRAP" "$TOPIC" "$PATCHED" "$ID_SUFFIX"

# 2. Wait + poll
sleep 10
DOC=$(poll_es_sim "$ES_HOST" 60 5) || { print_status "fail" "FAIL: No doc in ES within 60s"; exit 1; }

# 3. Extract video URL from info.videoUrl or info.videoSource
VIDEO_URL=$(echo "$DOC" | python3 -c "
import sys, json
data = json.load(sys.stdin)
info = data.get('info', {})
url = info.get('videoUrl', info.get('videoSource', data.get('videoUrl', data.get('video_url', ''))))
print(url or '')
" 2>/dev/null || echo "")

if [ -z "$VIDEO_URL" ]; then
    print_status "fail" "FAIL: videoUrl/videoSource is missing or empty in output document"
    exit 1
fi

print_status "info" "Found video URL: $VIDEO_URL"

# 4. Assert URL contains VST simulator host
if echo "$VIDEO_URL" | grep -q "$VST_HOST"; then
    print_status "ok" "PASS: videoUrl is non-empty and references VST sim ($VST_HOST)"
    exit 0
else
    print_status "fail" "FAIL: videoUrl does not contain $VST_HOST — got: $VIDEO_URL"
    exit 1
fi
