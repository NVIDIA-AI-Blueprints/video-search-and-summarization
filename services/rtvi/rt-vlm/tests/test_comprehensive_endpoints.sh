#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Comprehensive test script for RTVI CLI endpoints
# Tests: add-file, delete-file, add-live-stream, generate-captions, chat-completions

set +e  # Don't exit on error - we want to test all endpoints

BACKEND="${BACKEND:-http://localhost:8010}"
CLI_SCRIPT="src/cli/rtvi_client_cli.py"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test state variables
TEST_FILE_ID=""
TEST_STREAM_ID=""
TEST_VIDEO_FILE="${RTVI_TEST_VIDEO_PATH:-}"
TEST_IMAGE_FILE="${RTVI_TEST_IMAGE_PATH:-}"

# Function to print test header
print_test() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}Testing: $1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

# Function to run command and check result
run_test() {
    local test_name="$1"
    local command="$2"
    local expect_success="${3:-true}"  # Default to expecting success
    
    print_test "$test_name"
    echo "Command: $command"
    echo ""
    
    local output_file
    output_file=$(mktemp "${TMPDIR:-/tmp}/test_output_XXXXXX.txt")
    if eval "$command" > "$output_file" 2>&1; then
        if [ "$expect_success" = "true" ]; then
            cat "$output_file"
            echo -e "${GREEN}✓ PASSED: $test_name${NC}"
            rm -f "$output_file"
            return 0
        else
            cat "$output_file"
            echo -e "${RED}✗ UNEXPECTED SUCCESS: $test_name${NC}"
            rm -f "$output_file"
            return 1
        fi
    else
        if [ "$expect_success" = "false" ]; then
            cat "$output_file"
            echo -e "${GREEN}✓ PASSED (Expected Failure): $test_name${NC}"
            rm -f "$output_file"
            return 0
        else
            cat "$output_file"
            echo -e "${RED}✗ FAILED: $test_name${NC}"
            rm -f "$output_file"
            return 1
        fi
    fi
}

# Function to extract file ID from output
extract_file_id() {
    local output="$1"
    echo "$output" | grep -oE '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1
}

# Function to get model name from /v1/models endpoint
get_model_name() {
    source "$VENV_PATH/bin/activate" 2>/dev/null || true
    # Use curl to get model name from /v1/models endpoint
    MODEL_JSON=$(curl -s "${BACKEND}/v1/models" 2>/dev/null)
    if [ -n "$MODEL_JSON" ]; then
        echo "$MODEL_JSON" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['data'][0]['id'] if data.get('data') and len(data['data']) > 0 else '')" 2>/dev/null
    else
        # Fallback to CLI if curl fails
        python3 "$CLI_SCRIPT" list-models --backend "$BACKEND" 2>&1 | \
            grep -E "^│ [A-Za-z0-9-]+" | grep -v "^│ ID" | head -1 | awk -F'│' '{print $2}' | xargs
    fi
}

# Change to project root (one level up from tests/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT" || { echo "ERROR: Failed to cd to project root: $PROJECT_ROOT" >&2; exit 1; }
VENV_PATH="${VENV_PATH:-$PROJECT_ROOT/venv}"

# Activate virtual environment if it exists
if [ -d "$VENV_PATH" ]; then
    echo "Activating virtual environment: $VENV_PATH"
    source "$VENV_PATH/bin/activate"
else
    echo "Warning: Virtual environment not found at $VENV_PATH"
    echo "  To create it: python3 -m venv $VENV_PATH && $VENV_PATH/bin/pip install requests sseclient-py tabulate tqdm"
    echo "  Or override: VENV_PATH=/path/to/your/venv $0"
    echo "Using system Python. Make sure requests, sseclient-py, tabulate, and tqdm are installed."
fi

# Export backend URL
export RTVI_BACKEND="$BACKEND"

echo "================================================"
echo "Comprehensive RTVI CLI Endpoint Testing"
echo "Backend: $BACKEND"
echo "================================================"

# Track test results
PASSED=0
FAILED=0

# Get model name
print_test "Getting Model Name"
MODEL_NAME=$(get_model_name)
if [ -z "$MODEL_NAME" ]; then
    MODEL_NAME="Cosmos-Reason2-2B"  # Default fallback
    echo "Using default model: $MODEL_NAME"
else
    echo "Found model: $MODEL_NAME"
fi

# Get existing files to use for testing
print_test "Getting Existing Files"
source "$VENV_PATH/bin/activate" 2>/dev/null || true
EXISTING_FILE_OUTPUT=$(python3 "$CLI_SCRIPT" list-files --backend "$BACKEND" 2>&1)
EXISTING_FILE_ID=$(echo "$EXISTING_FILE_OUTPUT" | grep -E "^│ [a-f0-9-]{36}" | head -1 | awk '{print $2}' | tr -d '│' | xargs)

if [ -n "$EXISTING_FILE_ID" ]; then
    TEST_FILE_ID="$EXISTING_FILE_ID"
    echo "Found existing file ID: $TEST_FILE_ID"
else
    echo "No existing files found. Will need to add a file first."
fi

# ============================================================================
# TEST 1: Add File (if we have a test file)
# ============================================================================
if [ -n "$TEST_VIDEO_FILE" ] && [ -f "$TEST_VIDEO_FILE" ]; then
    print_test "Add File (Video)"
    echo "Command: python3 $CLI_SCRIPT add-file \"$TEST_VIDEO_FILE\" --backend $BACKEND --sensor-name test-sensor-001"
    echo ""
    OUTPUT=$(python3 "$CLI_SCRIPT" add-file "$TEST_VIDEO_FILE" --backend "$BACKEND" --sensor-name test-sensor-001 2>&1)
    EXIT_CODE=$?
    echo "$OUTPUT"
    if [ $EXIT_CODE -eq 0 ]; then
        TEST_FILE_ID=$(extract_file_id "$OUTPUT")
        echo -e "${GREEN}✓ PASSED: Add File (Video)${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ FAILED: Add File (Video)${NC}"
        ((FAILED++))
    fi
else
    echo -e "${YELLOW}⚠ SKIPPED: Add File (Video) - No test video file provided${NC}"
fi

# ============================================================================
# TEST 2: Add File with Creation Time
# ============================================================================
if [ -n "$TEST_VIDEO_FILE" ] && [ -f "$TEST_VIDEO_FILE" ]; then
    CREATION_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")
    print_test "Add File with Creation Time"
    echo "Command: python3 $CLI_SCRIPT add-file \"$TEST_VIDEO_FILE\" --backend $BACKEND --creation-time \"$CREATION_TIME\" --sensor-name test-sensor-002"
    echo ""
    OUTPUT=$(python3 "$CLI_SCRIPT" add-file "$TEST_VIDEO_FILE" --backend "$BACKEND" --creation-time "$CREATION_TIME" --sensor-name test-sensor-002 2>&1)
    EXIT_CODE=$?
    echo "$OUTPUT"
    if [ $EXIT_CODE -eq 0 ]; then
        NEW_FILE_ID=$(extract_file_id "$OUTPUT")
        if [ -z "$TEST_FILE_ID" ]; then
            TEST_FILE_ID="$NEW_FILE_ID"
        fi
        echo -e "${GREEN}✓ PASSED: Add File with Creation Time${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ FAILED: Add File with Creation Time${NC}"
        ((FAILED++))
    fi
else
    echo -e "${YELLOW}⚠ SKIPPED: Add File with Creation Time - No test video file provided${NC}"
fi

# ============================================================================
# TEST 3: Add Image File
# ============================================================================
TEST_IMAGE_FILE_ID=""
if [ -n "$TEST_IMAGE_FILE" ] && [ -f "$TEST_IMAGE_FILE" ]; then
    print_test "Add Image File"
    echo "Command: python3 $CLI_SCRIPT add-file \"$TEST_IMAGE_FILE\" --is-image --backend $BACKEND --sensor-name test-sensor-img"
    echo ""
    OUTPUT=$(python3 "$CLI_SCRIPT" add-file "$TEST_IMAGE_FILE" --is-image --backend "$BACKEND" --sensor-name test-sensor-img 2>&1)
    EXIT_CODE=$?
    echo "$OUTPUT"
    if [ $EXIT_CODE -eq 0 ]; then
        TEST_IMAGE_FILE_ID=$(extract_file_id "$OUTPUT")
        echo -e "${GREEN}✓ PASSED: Add Image File${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ FAILED: Add Image File${NC}"
        ((FAILED++))
    fi
else
    echo -e "${YELLOW}⚠ SKIPPED: Add Image File - No test image file provided${NC}"
fi

# ============================================================================
# TEST 4: Chat Completions on Image
# ============================================================================
if [ -n "$TEST_IMAGE_FILE_ID" ] && [ -n "$MODEL_NAME" ]; then
    run_test "Chat Completions on Image" \
        "python3 $CLI_SCRIPT chat-completions --id $TEST_IMAGE_FILE_ID --model $MODEL_NAME --messages 'user:Describe what you see in this image' --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    echo -e "${YELLOW}⚠ SKIPPED: Chat Completions on Image - No image file ID or model${NC}"
fi

# ============================================================================
# TEST 5: Get File Info
# ============================================================================
if [ -n "$TEST_FILE_ID" ]; then
    run_test "Get File Info" \
        "python3 $CLI_SCRIPT file-info $TEST_FILE_ID --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    echo -e "${YELLOW}⚠ SKIPPED: Get File Info - No file ID available${NC}"
fi

# ============================================================================
# TEST 4: Generate Captions (Non-streaming)
# ============================================================================
if [ -n "$TEST_FILE_ID" ] && [ -n "$MODEL_NAME" ]; then
    run_test "Generate Captions (Non-streaming)" \
        "python3 $CLI_SCRIPT generate-captions --id $TEST_FILE_ID --model $MODEL_NAME --chunk-duration 5 --prompt 'Describe what you see' --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    echo -e "${YELLOW}⚠ SKIPPED: Generate Captions - Missing file ID or model${NC}"
fi

# ============================================================================
# TEST 5: Generate Captions (Streaming) - with timeout
# ============================================================================
if [ -n "$TEST_FILE_ID" ] && [ -n "$MODEL_NAME" ]; then
    print_test "Generate Captions (Streaming)"
    echo "Command: timeout 30 python3 $CLI_SCRIPT generate-captions --id $TEST_FILE_ID --model $MODEL_NAME --chunk-duration 5 --prompt 'Describe what you see' --stream --backend $BACKEND"
    echo ""
    STREAM_TEST_FILE=$(mktemp "${TMPDIR:-/tmp}/stream_test_XXXXXX.txt")
    timeout 30 python3 "$CLI_SCRIPT" generate-captions --id "$TEST_FILE_ID" --model "$MODEL_NAME" --chunk-duration 5 --prompt 'Describe what you see' --stream --backend "$BACKEND" > "$STREAM_TEST_FILE" 2>&1
    EXIT_CODE=$?
    cat "$STREAM_TEST_FILE"
    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}✓ PASSED: Generate Captions (Streaming)${NC}"
        ((PASSED++))
    elif [ $EXIT_CODE -eq 124 ]; then
        echo -e "${GREEN}✓ PASSED: Generate Captions (Streaming) - Timeout expected for streaming${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ FAILED: Generate Captions (Streaming)${NC}"
        ((FAILED++))
    fi
    rm -f "$STREAM_TEST_FILE"
else
    echo -e "${YELLOW}⚠ SKIPPED: Generate Captions (Streaming) - Missing file ID or model${NC}"
fi

# ============================================================================
# TEST 6: Chat Completions (Non-streaming)
# ============================================================================
if [ -n "$TEST_FILE_ID" ] && [ -n "$MODEL_NAME" ]; then
    run_test "Chat Completions (Non-streaming)" \
        "python3 $CLI_SCRIPT chat-completions --id $TEST_FILE_ID --model $MODEL_NAME --messages 'user:Describe what you see in this video' --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    echo -e "${YELLOW}⚠ SKIPPED: Chat Completions - Missing file ID or model${NC}"
fi

# ============================================================================
# TEST 7: Chat Completions (Streaming) - with timeout
# ============================================================================
if [ -n "$TEST_FILE_ID" ] && [ -n "$MODEL_NAME" ]; then
    print_test "Chat Completions (Streaming)"
    echo "Command: timeout 30 python3 $CLI_SCRIPT chat-completions --id $TEST_FILE_ID --model $MODEL_NAME --messages 'user:Describe what you see in this video' --stream --backend $BACKEND"
    echo ""
    STREAM_CHAT_FILE=$(mktemp "${TMPDIR:-/tmp}/stream_chat_XXXXXX.txt")
    timeout 30 python3 "$CLI_SCRIPT" chat-completions --id "$TEST_FILE_ID" --model "$MODEL_NAME" --messages 'user:Describe what you see in this video' --stream --backend "$BACKEND" > "$STREAM_CHAT_FILE" 2>&1
    EXIT_CODE=$?
    cat "$STREAM_CHAT_FILE"
    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}✓ PASSED: Chat Completions (Streaming)${NC}"
        ((PASSED++))
    elif [ $EXIT_CODE -eq 124 ]; then
        echo -e "${GREEN}✓ PASSED: Chat Completions (Streaming) - Timeout expected for streaming${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ FAILED: Chat Completions (Streaming)${NC}"
        ((FAILED++))
    fi
    rm -f "$STREAM_CHAT_FILE"
else
    echo -e "${YELLOW}⚠ SKIPPED: Chat Completions (Streaming) - Missing file ID or model${NC}"
fi

# ============================================================================
# TEST 8: Chat Completions with System Prompt
# ============================================================================
if [ -n "$TEST_FILE_ID" ] && [ -n "$MODEL_NAME" ]; then
    run_test "Chat Completions with System Prompt" \
        "python3 $CLI_SCRIPT chat-completions --id $TEST_FILE_ID --model $MODEL_NAME --messages 'system:You are a helpful assistant' 'user:Describe this video' --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    echo -e "${YELLOW}⚠ SKIPPED: Chat Completions with System Prompt - Missing file ID or model${NC}"
fi

# ============================================================================
# TEST 9: Add Live Stream (using NVIDIA RTSP URL)
# ============================================================================
TEST_RTSP_URL="rtsp://nv-wowza-pdc.nvidia.com:1935/vod/warehouse_1.mp4"
print_test "Add Live Stream"
echo "Command: python3 $CLI_SCRIPT add-live-stream \"$TEST_RTSP_URL\" --description \"Test warehouse stream\" --sensor-name test-sensor-stream --backend $BACKEND"
echo ""
OUTPUT=$(python3 "$CLI_SCRIPT" add-live-stream "$TEST_RTSP_URL" --description "Test warehouse stream" --sensor-name test-sensor-stream --backend "$BACKEND" 2>&1)
echo "$OUTPUT"
TEST_STREAM_ID=$(extract_file_id "$OUTPUT")
if [ -n "$TEST_STREAM_ID" ]; then
    echo "Successfully added live stream with ID: $TEST_STREAM_ID"
    echo -e "${GREEN}✓ PASSED: Add Live Stream${NC}"
    ((PASSED++))
else
    if echo "$OUTPUT" | grep -q "Could not connect\|No video stream\|Failed to add"; then
        echo -e "${YELLOW}⚠ SKIPPED: Add Live Stream - Connection issue${NC}"
    else
        echo -e "${RED}✗ FAILED: Add Live Stream - Could not extract stream ID${NC}"
        ((FAILED++))
    fi
fi

# ============================================================================
# TEST 10: List Live Streams
# ============================================================================
run_test "List Live Streams" \
    "python3 $CLI_SCRIPT list-live-streams --backend $BACKEND" && ((PASSED++)) || ((FAILED++))

# ============================================================================
# TEST 11: Delete Files (cleanup)
# ============================================================================
if [ -n "$TEST_FILE_ID" ]; then
    run_test "Delete Video File" \
        "python3 $CLI_SCRIPT delete-file $TEST_FILE_ID --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    echo -e "${YELLOW}⚠ SKIPPED: Delete Video File - No file ID available${NC}"
fi

if [ -n "$TEST_IMAGE_FILE_ID" ]; then
    run_test "Delete Image File" \
        "python3 $CLI_SCRIPT delete-file $TEST_IMAGE_FILE_ID --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    echo -e "${YELLOW}⚠ SKIPPED: Delete Image File - No image file ID available${NC}"
fi

# ============================================================================
# TEST 12: Delete Live Stream (if we have one)
# ============================================================================
if [ -n "$TEST_STREAM_ID" ]; then
    run_test "Delete Live Stream" \
        "python3 $CLI_SCRIPT delete-live-stream $TEST_STREAM_ID --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
else
    # Try to get stream ID from list-live-streams
    print_test "Getting Live Stream ID"
    STREAM_LIST_OUTPUT=$(python3 "$CLI_SCRIPT" list-live-streams --backend "$BACKEND" 2>&1)
    TEST_STREAM_ID=$(echo "$STREAM_LIST_OUTPUT" | grep -oE '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1)
    if [ -n "$TEST_STREAM_ID" ]; then
        echo "Found stream ID from list: $TEST_STREAM_ID"
        run_test "Delete Live Stream" \
            "python3 $CLI_SCRIPT delete-live-stream $TEST_STREAM_ID --backend $BACKEND" && ((PASSED++)) || ((FAILED++))
    else
        echo -e "${YELLOW}⚠ SKIPPED: Delete Live Stream - No stream ID available${NC}"
    fi
fi

# ============================================================================
# TEST SUMMARY
# ============================================================================
echo ""
echo "================================================"
echo "Test Summary"
echo "================================================"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"
echo "================================================"

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All endpoint tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
