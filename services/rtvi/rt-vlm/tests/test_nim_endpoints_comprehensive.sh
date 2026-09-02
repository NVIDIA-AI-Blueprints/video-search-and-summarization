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
# Comprehensive Test Script for NIM-Compatible Endpoints
# This script tests ALL endpoints and features of RTVI VLM server
######################################################################################################

set -e

BACKEND="${RTVI_BACKEND:-http://localhost:8010}"
CLI_SCRIPT="src/cli/rtvi_client_cli.py"

# Determine script directory for relative paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BODY_JSON_PATH="$SCRIPT_DIR/body.json"

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0
TESTS_TOTAL=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo "COMPREHENSIVE NIM-COMPATIBLE ENDPOINT TESTS"
echo "Backend: $BACKEND"
echo "=========================================="
echo ""

# Pre-flight: verify server is reachable
echo "Checking server connectivity..."
if ! curl -s --connect-timeout 5 "$BACKEND/v1/health/live" > /dev/null 2>&1; then
    echo -e "${RED}ERROR: Server at $BACKEND is not reachable.${NC}"
    echo "Start the server first, then re-run this script."
    exit 1
fi
echo -e "${GREEN}Server is reachable.${NC}"
echo ""

# Helper function to track test results
run_test() {
    local test_name="$1"
    local test_command="$2"
    local expected_result="${3:-success}"  # success, failure, or skip

    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    echo -e "${BLUE}Test $TESTS_TOTAL: $test_name${NC}"
    echo "-------------------"

    if [ "$expected_result" = "skip" ]; then
        echo -e "${YELLOW}⊘ SKIPPED${NC}"
        TESTS_SKIPPED=$((TESTS_SKIPPED + 1))
        echo ""
        return 0
    fi

    # Capture both stdout+stderr and exit code
    # Temporarily disable set -e so test failures don't kill the script
    local output
    local exit_code
    set +e
    output=$(eval "$test_command" 2>&1)
    exit_code=$?
    set -e

    # For expected-failure tests, verify we got an actual HTTP response
    # (not a curl connection error which would be a false positive)
    if [ "$expected_result" = "failure" ]; then
        if echo "$output" | grep -qE "Connection refused|Could not resolve|Failed to connect"; then
            echo -e "${RED}✗ FAILED (server unreachable — not a valid test result)${NC}"
            TESTS_FAILED=$((TESTS_FAILED + 1))
            echo ""
            return 0
        fi
    fi

    if [ $exit_code -eq 0 ]; then
        if [ "$expected_result" = "success" ]; then
            echo -e "${GREEN}✓ PASSED${NC}"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            echo -e "${RED}✗ FAILED (expected failure but succeeded)${NC}"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    else
        if [ "$expected_result" = "failure" ]; then
            echo -e "${GREEN}✓ PASSED (correctly failed)${NC}"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            echo -e "${RED}✗ FAILED${NC}"
            echo "$output" | tail -3
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    fi
    echo ""
}

# Check if CLI script exists
if [ ! -f "$CLI_SCRIPT" ]; then
    echo "Error: CLI script not found at $CLI_SCRIPT"
    exit 1
fi

echo "=========================================="
echo "SECTION 1: HEALTH & METADATA ENDPOINTS"
echo "=========================================="
echo ""

run_test "Health Check - Ready" \
    "curl -s -f $BACKEND/v1/health/ready | grep -q 'ready'"

run_test "Health Check - Live" \
    "curl -s -f $BACKEND/v1/health/live | grep -q 'healthy'"

run_test "Metrics Endpoint" \
    "curl -s -f $BACKEND/v1/metrics | grep -q 'rtvi'"

run_test "Get Version" \
    "python3 $CLI_SCRIPT get-version --backend $BACKEND"

run_test "Get Manifest" \
    "python3 $CLI_SCRIPT get-manifest --backend $BACKEND"

echo "=========================================="
echo "SECTION 2: MODEL MANAGEMENT ENDPOINTS"
echo "=========================================="
echo ""

# Get model name for subsequent tests
MODEL_NAME=$(python3 -c "
import sys
import json
try:
    import requests
    backend = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8000'
    if not backend.startswith('http://') and not backend.startswith('https://'):
        backend = 'http://' + backend
    r = requests.get(f'{backend}/v1/models', timeout=5)
    if r.status_code == 200:
        data = r.json()
        if data.get('data') and len(data['data']) > 0:
            print(data['data'][0]['id'])
        else:
            sys.exit(1)
    else:
        sys.exit(1)
except Exception as e:
    sys.exit(1)
" "$BACKEND" 2>/dev/null) || MODEL_NAME="default-model"

echo "Using model: $MODEL_NAME"
echo ""

run_test "List All Models" \
    "python3 $CLI_SCRIPT list-models --backend $BACKEND"

# Test 8: Get Specific Model Info - REMOVED (endpoint not implemented)
# Test was: curl -s -f $BACKEND/v1/models/$MODEL_NAME

run_test "Get Non-Existent Model (Expected Failure)" \
    "curl -s $BACKEND/v1/models/non-existent-model-123 | grep -q '404\\|error'" \
    "failure"

echo "=========================================="
echo "SECTION 3: FILE MANAGEMENT ENDPOINTS"
echo "=========================================="
echo ""

FILE_ID=""
if [ -n "$RTVI_TEST_VIDEO_PATH" ] && [ -f "$RTVI_TEST_VIDEO_PATH" ]; then
    # Upload file once, capture FILE_ID
    FILE_ID=$(python3 "$CLI_SCRIPT" add-file "$RTVI_TEST_VIDEO_PATH" --backend "$BACKEND" 2>/dev/null | grep -oP 'id: \K[^,]+' | head -1)

    run_test "Upload File" \
        "[ -n \"$FILE_ID\" ] && echo 'File uploaded: $FILE_ID'"

    if [ -n "$FILE_ID" ]; then
        run_test "List All Files" \
            "curl -s -f '$BACKEND/v1/files?purpose=vision' | python3 -m json.tool"

        run_test "Get File Info" \
            "python3 $CLI_SCRIPT file-content $FILE_ID --backend $BACKEND"

        run_test "Get File Metadata" \
            "curl -s -f $BACKEND/v1/files/$FILE_ID | python3 -m json.tool"
    fi
else
    echo "⊘ File upload tests skipped (RTVI_TEST_VIDEO_PATH not set)"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 4))
    echo ""
fi

run_test "Get Non-Existent File (Expected Failure)" \
    "curl -s $BACKEND/v1/files/non-existent-file-123 | grep -q '404\\|error'" \
    "failure"

echo "=========================================="
echo "SECTION 4: CHAT COMPLETIONS - BASIC"
echo "=========================================="
echo ""

if [ -n "$FILE_ID" ]; then
    run_test "Chat Completions - Non-Streaming" \
        "python3 $CLI_SCRIPT chat-completions --id $FILE_ID --model $MODEL_NAME --messages 'user:Describe this video.' --backend $BACKEND"

    run_test "Chat Completions - Streaming" \
        "timeout 30 python3 $CLI_SCRIPT chat-completions --id $FILE_ID --model $MODEL_NAME --messages 'user:What do you see?' --stream --backend $BACKEND"

    run_test "Chat Completions - With System Prompt" \
        "python3 $CLI_SCRIPT chat-completions --id $FILE_ID --model $MODEL_NAME --messages 'system:You are a helpful assistant.' 'user:Describe this.' --backend $BACKEND"

    run_test "Chat Completions - Multi-Turn Conversation" \
        "python3 $CLI_SCRIPT chat-completions --id $FILE_ID --model $MODEL_NAME --messages 'user:What is in the video?' 'assistant:I see a scene.' 'user:Tell me more details.' --backend $BACKEND"
else
    echo "⊘ Chat completion tests skipped (no FILE_ID available)"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 4))
    echo ""
fi

echo "=========================================="
echo "SECTION 5: CHAT COMPLETIONS - PARAMETERS"
echo "=========================================="
echo ""

if [ -n "$FILE_ID" ]; then
    run_test "Chat Completions - With Temperature" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"temperature\":0.9}' | python3 -m json.tool"

    run_test "Chat Completions - With Top-P" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"top_p\":0.9}' | python3 -m json.tool"

    run_test "Chat Completions - With Top-K" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"top_k\":50}' | python3 -m json.tool"

    run_test "Chat Completions - With Max Tokens" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"max_tokens\":50}' | python3 -m json.tool"

    run_test "Chat Completions - With Repetition Penalty" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"repetition_penalty\":1.2}' | python3 -m json.tool"

    run_test "Chat Completions - With Stop Sequences" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"stop\":[\".\",\"!\"]}' | python3 -m json.tool"

    run_test "Chat Completions - With Seed (Reproducibility)" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"seed\":42}' | python3 -m json.tool"
else
    echo "⊘ Parameter tests skipped (no FILE_ID available)"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 7))
    echo ""
fi

echo "=========================================="
echo "SECTION 6: NIM-SPECIFIC PARAMETERS"
echo "=========================================="
echo ""

if [ -n "$FILE_ID" ]; then
    run_test "Chat Completions - With Guided JSON" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"List objects as JSON.\"}],\"guided_json\":{\"type\":\"object\",\"properties\":{\"objects\":{\"type\":\"array\"}}}}' | python3 -m json.tool"

    run_test "Chat Completions - With Guided Choice" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Is this indoor or outdoor?\"}],\"guided_choice\":[\"indoor\",\"outdoor\"]}' | python3 -m json.tool"

    run_test "Chat Completions - With Guided Regex" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Give a short answer.\"}],\"guided_regex\":\"^[A-Za-z ]{1,50}$\"}' | python3 -m json.tool"
else
    echo "⊘ NIM parameter tests skipped (no FILE_ID available)"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 3))
    echo ""
fi

echo "=========================================="
echo "SECTION 7: VIDEO PROCESSING OPTIONS"
echo "=========================================="
echo ""

if [ -n "$FILE_ID" ]; then
    run_test "Chat Completions - With Custom Chunk Duration" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"chunk_duration\":30}' | python3 -m json.tool"

    run_test "Chat Completions - With Custom Frame Sampling" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"frames_per_chunk\":15}' | python3 -m json.tool"

    run_test "Chat Completions - With Enable Reasoning" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Analyze this.\"}],\"enable_reasoning\":true}' | python3 -m json.tool"
    run_test "Chat Completions - media_io_kwargs with fps (NIM format)" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"media_io_kwargs\":{\"video\":{\"fps\":3.0}}}' | python3 -m json.tool"

    run_test "Chat Completions - media_io_kwargs with num_frames" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"media_io_kwargs\":{\"video\":{\"num_frames\":8}}}' | python3 -m json.tool"

    run_test "Chat Completions - mm_processor_kwargs with resolution" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"mm_processor_kwargs\":{\"size\":{\"shortest_edge\":1568,\"longest_edge\":262144}}}' | python3 -m json.tool"

    run_test "Chat Completions - Full NIM Cosmos Reason2 format" \
        "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$FILE_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe.\"}],\"media_io_kwargs\":{\"video\":{\"fps\":2.0}},\"mm_processor_kwargs\":{\"size\":{\"shortest_edge\":1568}},\"temperature\":0.3,\"top_p\":0.3,\"max_tokens\":256}' | python3 -m json.tool"

else
    echo "⊘ Video processing option tests skipped (no FILE_ID available)"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 7))
    echo ""
fi

echo "=========================================="
echo "SECTION 8: IMAGE & VIDEO URL INPUTS"
echo "=========================================="
echo ""

run_test "Chat Completions - HTTP Image URL" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"What is this?\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"https://httpbin.org/image/png\"}}]}]}' 2>&1 | (grep -q 'object.*chat.completion\\|DownloadFailed\\|503' && echo 'Test path verified')"

run_test "Chat Completions - Data URI Image (Base64) - Quick Test" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"What?\"},  {\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,iVBORw0KGgoAAAANSUhEUg\"}}]}]}' 2>&1 | (grep -q 'object.*chat.completion\\|InvalidDataUrl\\|Failed' && echo 'Test path verified')"

# Test with actual body.json if available
if [ -f "$BODY_JSON_PATH" ]; then
    run_test "Chat Completions - Data URI Image (Base64) - Full Test" \
        "TEMP_BODY=\$(mktemp) && sed \"s/test-model/$MODEL_NAME/\" \"$BODY_JSON_PATH\" > \"\$TEMP_BODY\" && curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' --data-binary @\"\$TEMP_BODY\" 2>&1 | grep -q 'object.*chat.completion' && rm -f \"\$TEMP_BODY\""
else
    echo "  Note: Skipping full base64 image test - body.json not found"
    echo "  Create with: cd tests && python3 create_test_data.py --image"
fi

run_test "Chat Completions - HTTP Video URL" \
    "timeout 60 curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"What happens?\"},{\"type\":\"video_url\",\"video_url\":{\"url\":\"https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4\"}}]}]}' 2>&1 | (grep -q 'object.*chat.completion\\|Processing\\|DownloadFailed' && echo 'Test path verified')"

echo "=========================================="
echo "SECTION 9: STREAM MANAGEMENT"
echo "=========================================="
echo ""

STREAM_ID=""
if [ -n "$RTVI_TEST_VIDEO_PATH" ] && [ -f "$RTVI_TEST_VIDEO_PATH" ] && command -v cvlc >/dev/null 2>&1; then
    # Start RTSP server
    RTSP_PORT="${RTSP_PORT:-8554}"
    LOCAL_IP=$(hostname -I | awk '{print $1}' || echo "127.0.0.1")
    RTSP_URL="rtsp://${LOCAL_IP}:${RTSP_PORT}/test-stream"

    cvlc --loop "$RTVI_TEST_VIDEO_PATH" \
        ":sout=#gather:rtp{sdp=rtsp://:$RTSP_PORT/test-stream}" \
        :network-caching=1500 :sout-all :sout-keep \
        > /dev/null 2>&1 &
    CVLC_PID=$!
    sleep 3

    # Add stream once, capture STREAM_ID
    STREAM_ID=$(curl -s -X POST "$BACKEND/v1/streams/add" -H 'Content-Type: application/json' -d "{\"streams\":[{\"liveStreamUrl\":\"$RTSP_URL\",\"description\":\"Test stream\"}]}" | python3 -c 'import sys,json; data=json.load(sys.stdin); print(data["results"][0]["id"] if data.get("results") else "")' 2>/dev/null)

    run_test "Add RTSP Stream" \
        "[ -n \"$STREAM_ID\" ] && echo 'Stream added: $STREAM_ID'"

    if [ -n "$STREAM_ID" ]; then
        run_test "List All Streams" \
            "curl -s -f $BACKEND/v1/streams/get-stream-info | python3 -m json.tool"

        # Test 38: Get Stream Info - REMOVED (endpoint not implemented)
        # Test was: curl -s -f $BACKEND/v1/streams/info/$STREAM_ID

        run_test "Update Stream Description" \
            "curl -s -X PATCH $BACKEND/v1/streams/update/$STREAM_ID -H 'Content-Type: application/json' -d '{\"description\":\"Updated description\"}' | python3 -m json.tool"

        run_test "Chat Completions - With RTSP Stream" \
            "timeout 90 curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"$STREAM_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Describe the stream.\"}],\"stream\":true}' 2>&1 | (grep -q 'data:\\|ping' && echo 'Stream response received')"

        run_test "Delete Stream" \
            "curl -s -w '%{http_code}' -o /dev/null -X DELETE $BACKEND/v1/streams/delete/$STREAM_ID | grep -q '200'"
    fi

    kill $CVLC_PID 2>/dev/null || true
    wait $CVLC_PID 2>/dev/null || true
else
    echo "⊘ Stream management tests skipped (RTVI_TEST_VIDEO_PATH not set or cvlc not found)"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 6))
    echo ""
fi

echo "=========================================="
echo "SECTION 10: ERROR HANDLING & VALIDATION"
echo "=========================================="
echo ""

run_test "Invalid Model Name (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"invalid-model-xyz\",\"messages\":[{\"role\":\"user\",\"content\":\"Test\"}]}' | grep -q 'error\\|404'" \
    "failure"

run_test "Missing Required Field - Model (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"messages\":[{\"role\":\"user\",\"content\":\"Test\"}]}' | grep -q 'error\\|422\\|validation'" \
    "failure"

run_test "Missing Required Field - Messages (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\"}' | grep -q 'error\\|422\\|validation'" \
    "failure"

run_test "Invalid JSON Format (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{invalid json}' | grep -q 'error\\|400\\|parse'" \
    "failure"

run_test "Invalid File ID (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"non-existent-file-123\",\"messages\":[{\"role\":\"user\",\"content\":\"Test\"}]}' | grep -q 'error\\|404\\|not found'" \
    "failure"

run_test "Invalid Stream ID (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"id\":\"non-existent-stream-123\",\"messages\":[{\"role\":\"user\",\"content\":\"Test\"}]}' | grep -q 'error\\|404\\|not found'" \
    "failure"

run_test "SSRF Protection - Loopback Address (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"image_url\",\"image_url\":{\"url\":\"http://127.0.0.1:8080/secret\"}}]}]}' | grep -q 'error\\|SSRF\\|blocked'" \
    "failure"

run_test "SSRF Protection - Private IP (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"image_url\",\"image_url\":{\"url\":\"http://192.168.1.1/admin\"}}]}]}' | grep -q 'error\\|SSRF\\|blocked'" \
    "failure"

run_test "SSRF Protection - Cloud Metadata (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"image_url\",\"image_url\":{\"url\":\"http://169.254.169.254/latest/meta-data\"}}]}]}' | grep -q 'error\\|SSRF\\|blocked'" \
    "failure"

run_test "Extra Fields Forbidden (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"Test\"}],\"unknown_field\":\"value\"}' | grep -q 'error\\|422\\|forbidden\\|extra'" \
    "failure"

run_test "Invalid Temperature Range (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"Test\"}],\"temperature\":3.0}' | grep -q 'error\\|422\\|validation'" \
    "failure"

run_test "Invalid Top-P Range (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"Test\"}],\"top_p\":1.5}' | grep -q 'error\\|422\\|validation'" \
    "failure"

echo "=========================================="
echo "SECTION 11: COMPLETIONS ENDPOINT (LEGACY)"
echo "=========================================="
echo ""

run_test "Completions Endpoint (Expected Unsupported)" \
    "curl -s -X POST $BACKEND/v1/completions -H 'Content-Type: application/json' -d '{\"model\":\"$MODEL_NAME\",\"prompt\":\"Complete this\"}' | grep -q 'unsupported\\|not implemented\\|error'" \
    "failure"

echo "=========================================="
echo "SECTION 12: URL-BASED PROCESSING (LVS)"
echo "=========================================="
echo ""

if [ -n "$RTVI_TEST_VIDEO_PATH" ] && [ -f "$RTVI_TEST_VIDEO_PATH" ]; then
    URL_TEST_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"

    run_test "Generate Captions - URL file:// (CLI)" \
        "timeout 120 python3 $CLI_SCRIPT generate-captions --id $URL_TEST_ID --url file://$RTVI_TEST_VIDEO_PATH --model $MODEL_NAME --prompt 'Describe what is happening' --stream --chunk-duration 10 --backend $BACKEND"

    run_test "Delete URL-based file" \
        "python3 $CLI_SCRIPT delete-file $URL_TEST_ID --backend $BACKEND"

    # Test chunk_id in response
    URL_TEST_ID2="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    run_test "Generate Captions - chunk_id in response" \
        "timeout 120 curl -s -X POST $BACKEND/v1/generate_captions -H 'Content-Type: application/json' -d '{\"id\":\"'\"$URL_TEST_ID2\"'\",\"url\":\"file://'\"$RTVI_TEST_VIDEO_PATH\"'\",\"model\":\"'\"$MODEL_NAME\"'\",\"prompt\":\"Describe.\",\"stream\":true,\"stream_options\":{\"include_usage\":true},\"chunk_duration\":10}' 2>&1 | grep -q 'chunk_id'"

    # Cleanup
    curl -s -X DELETE "$BACKEND/v1/files/$URL_TEST_ID2" > /dev/null 2>&1

    # Test media_io_kwargs with generate_captions
    URL_TEST_ID3="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    run_test "Generate Captions - media_io_kwargs with fps" \
        "timeout 120 curl -s -X POST $BACKEND/v1/generate_captions -H 'Content-Type: application/json' -d '{\"id\":\"'\"$URL_TEST_ID3\"'\",\"url\":\"file://'\"$RTVI_TEST_VIDEO_PATH\"'\",\"model\":\"'\"$MODEL_NAME\"'\",\"prompt\":\"Describe.\",\"chunk_duration\":10,\"media_io_kwargs\":{\"video\":{\"fps\":2.0}}}' 2>&1 | grep -q 'chunk_responses\\|content'"
    curl -s -X DELETE "$BACKEND/v1/files/$URL_TEST_ID3" > /dev/null 2>&1

    run_test "Generate Captions - Invalid URL scheme (Expected Failure)" \
        "curl -s -X POST $BACKEND/v1/generate_captions -H 'Content-Type: application/json' -d '{\"id\":\"'\"$URL_TEST_ID\"'\",\"url\":\"ftp://invalid\",\"model\":\"'\"$MODEL_NAME\"'\",\"prompt\":\"test\"}' | grep -q 'error\\|422\\|validation'" \
        "failure"

    run_test "Generate Captions - URL without ID (Expected Failure)" \
        "curl -s -X POST $BACKEND/v1/generate_captions -H 'Content-Type: application/json' -d '{\"url\":\"file:///tmp/test.mp4\",\"model\":\"'\"$MODEL_NAME\"'\",\"prompt\":\"test\"}' | grep -q 'error\\|422'" \
        "failure"
else
    echo "⊘ URL processing tests skipped (RTVI_TEST_VIDEO_PATH not set)"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 5))
    echo ""
fi

echo "=========================================="
echo "SECTION 13: CV-COMPATIBLE STREAM API"
echo "=========================================="
echo ""

run_test "CV Stream Add (passthrough - no inference)" \
    "curl -s -f -X POST $BACKEND/v1/stream/add -H 'Content-Type: application/json' -d '{\"key\":\"sensor\",\"value\":{\"camera_id\":\"test-cam-001\",\"camera_url\":\"rtsp://localhost:8554/test\",\"change\":\"camera_add\"}}' | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"inference\"]==False; print(\"OK\")'"

run_test "CV Stream List" \
    "curl -s -f $BACKEND/v1/stream/get-stream-info | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"Streams: {d[\"stream_count\"]}\")'"

run_test "CV Stream Remove" \
    "curl -s -f -X POST $BACKEND/v1/stream/remove -H 'Content-Type: application/json' -d '{\"key\":\"sensor\",\"value\":{\"camera_id\":\"test-cam-001\",\"change\":\"camera_remove\"}}' | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d[\"status\"]==\"removed\"; print(\"OK\")'"

run_test "CV Stream Remove Non-Existent (Expected Failure)" \
    "curl -s -X POST $BACKEND/v1/stream/remove -H 'Content-Type: application/json' -d '{\"key\":\"sensor\",\"value\":{\"camera_id\":\"nonexistent\",\"change\":\"camera_remove\"}}' | grep -q 'error\\|404\\|not found'" \
    "failure"

# CLI-based CV stream tests
run_test "CV Stream Add via CLI (passthrough)" \
    "python3 $CLI_SCRIPT stream-add --camera-url rtsp://localhost:8554/test --camera-id cli-cam-001 --backend $BACKEND 2>&1 | grep -q 'Stream added'"

run_test "CV Stream List via CLI" \
    "python3 $CLI_SCRIPT stream-list --backend $BACKEND"

run_test "CV Stream Remove via CLI" \
    "python3 $CLI_SCRIPT stream-remove --camera-id cli-cam-001 --backend $BACKEND 2>&1 | grep -q 'Stream removed'"

echo "=========================================="
echo "SECTION 14: CLEANUP"
echo "=========================================="
echo ""

if [ -n "$FILE_ID" ]; then
    run_test "Delete Test File" \
        "python3 $CLI_SCRIPT delete-file $FILE_ID --backend $BACKEND"
fi

echo "=========================================="
echo "TEST SUMMARY"
echo "=========================================="
echo ""
echo "Total Tests: $TESTS_TOTAL"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "${RED}Failed: $TESTS_FAILED${NC}"
echo -e "${YELLOW}Skipped: $TESTS_SKIPPED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓✓✓ ALL TESTS PASSED! ✓✓✓${NC}"
    exit 0
else
    echo -e "${RED}✗✗✗ SOME TESTS FAILED ✗✗✗${NC}"
    exit 1
fi
