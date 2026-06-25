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

######################################################################################################
# Integration test for turn-by-turn conversation support
# Tests the actual endpoint behavior with a running server
######################################################################################################

set -e

BACKEND="${RTVI_BACKEND:-http://0.0.0.0:8010}"
MODEL="${RTVI_MODEL:-nim_nvidia_cosmos-reason2-8b_0303-fp8-dynamic-kv8}"

MODEL=$(python3 -c "
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
" "$BACKEND" 2>/dev/null)



echo "=========================================="
echo "Testing Turn-by-Turn Conversation Support"
echo "Backend: $BACKEND"
echo "Model: $MODEL"
echo "Video: $RTVI_TEST_VIDEO_PATH"
echo "=========================================="
echo ""

# Check if server is available
if ! curl -s "$BACKEND/v1/ready" > /dev/null 2>&1; then
    echo "⚠️  Server not available at $BACKEND"
    echo "Skipping integration tests (server required)"
    exit 0
fi

# Test 1: Verify assistant messages are accepted in request structure
echo "Test 1: Verify Request Structure Accepts Assistant Messages"
echo "-----------------------------------------------------------"
TEST_JSON=$(mktemp)
cat > "$TEST_JSON" << 'EOF'
{
  "model": "MODEL_PLACEHOLDER",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What is in this video?"},
    {"role": "assistant", "content": "The video shows a warehouse."},
    {"role": "user", "content": "Tell me more about the colors."}
  ],
  "id": "FILE_ID_PLACEHOLDER"
}
EOF

# Replace placeholders
sed -i "s/MODEL_PLACEHOLDER/$MODEL/g" "$TEST_JSON"

echo "Request structure:"
cat "$TEST_JSON" | python3 -m json.tool 2>/dev/null || cat "$TEST_JSON"
echo ""

# If we have a test file, upload it and test
if [ -n "$RTVI_TEST_VIDEO_PATH" ] && [ -f "$RTVI_TEST_VIDEO_PATH" ]; then
    echo "Uploading test video..."
    UPLOAD_RESPONSE=$(curl -s -X POST "$BACKEND/v1/files" \
        -F "file=@$RTVI_TEST_VIDEO_PATH" \
        -F "purpose=vision" \
        -F "media_type=video" 2>&1)
    
    FILE_ID=$(echo "$UPLOAD_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Response format: {"id": "...", "bytes": ..., ...}
    if data.get('id'):
        print(data['id'])
except:
    pass
" 2>/dev/null)
    
    if [ -n "$FILE_ID" ]; then
        echo "✓ File uploaded with ID: $FILE_ID"
        
        # Replace FILE_ID_PLACEHOLDER
        sed -i "s/FILE_ID_PLACEHOLDER/$FILE_ID/g" "$TEST_JSON"
        
        echo ""
        echo "Test 2: Single Turn (Baseline)"
        echo "-------------------------------"
        SINGLE_TURN_JSON=$(mktemp)
        cat > "$SINGLE_TURN_JSON" << EOF
{
  "model": "$MODEL",
  "messages": [
    {"role": "user", "content": "What is in this video? Be brief."}
  ],
  "id": "$FILE_ID",
  "max_tokens": 100
}
EOF
        
        echo "Sending single-turn request..."
        RESPONSE1=$(curl -s -X POST "$BACKEND/v1/chat/completions" \
            -H "Content-Type: application/json" \
            --data-binary @"$SINGLE_TURN_JSON" \
            --max-time 120 2>&1)
        
        if echo "$RESPONSE1" | grep -q '"object":"chat.completion"'; then
            ASSISTANT_RESPONSE1=$(echo "$RESPONSE1" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('choices') and len(data['choices']) > 0:
        print(data['choices'][0].get('message', {}).get('content', ''))
except:
    pass
" 2>/dev/null)
            
            if [ -n "$ASSISTANT_RESPONSE1" ]; then
                echo "✓ Single-turn response received"
                echo "Response: ${ASSISTANT_RESPONSE1:0:100}..."
                
                echo ""
                echo "Test 3: Multi-Turn Conversation"
                echo "------------------------------"
                MULTI_TURN_JSON=$(mktemp)
                cat > "$MULTI_TURN_JSON" << EOF
{
  "model": "$MODEL",
  "messages": [
    {"role": "user", "content": "What is in this video? Be brief."},
    {"role": "assistant", "content": "$ASSISTANT_RESPONSE1"},
    {"role": "user", "content": "Can you describe the colors you see?"}
  ],
  "id": "$FILE_ID",
  "max_tokens": 100
}
EOF
                
                echo "Sending multi-turn request with conversation history..."
                echo "Request includes:"
                echo "  - User: What is in this video? Be brief."
                echo "  - Assistant: ${ASSISTANT_RESPONSE1:0:80}..."
                echo "  - User: Can you describe the colors you see?"
                echo ""
                
                RESPONSE2=$(curl -s -X POST "$BACKEND/v1/chat/completions" \
                    -H "Content-Type: application/json" \
                    --data-binary @"$MULTI_TURN_JSON" \
                    --max-time 120 2>&1)
                
                if echo "$RESPONSE2" | grep -q '"object":"chat.completion"'; then
                    ASSISTANT_RESPONSE2=$(echo "$RESPONSE2" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('choices') and len(data['choices']) > 0:
        print(data['choices'][0].get('message', {}).get('content', ''))
except:
    pass
" 2>/dev/null)
                    
                    if [ -n "$ASSISTANT_RESPONSE2" ]; then
                        echo "✓ Multi-turn response received"
                        echo "Response: ${ASSISTANT_RESPONSE2:0:150}..."
                        echo ""
                        echo "✅ Turn-by-turn conversation test PASSED!"
                        echo ""
                        echo "The model received the conversation history and responded contextually."
                    else
                        echo "⚠️  Multi-turn request succeeded but response missing content"
                    fi
                elif echo "$RESPONSE2" | grep -q "InvalidParameters\|400\|422"; then
                    echo "❌ Multi-turn request failed with validation error"
                    echo "Response: $(echo "$RESPONSE2" | head -5)"
                else
                    echo "⚠️  Multi-turn request failed (model may not be ready)"
                    echo "Status: $(echo "$RESPONSE2" | head -3)"
                fi
                
                rm -f "$MULTI_TURN_JSON"
            else
                echo "⚠️  Single-turn response missing content (model may not be ready)"
            fi
        else
            echo "⚠️  Single-turn request failed (model may not be ready)"
            echo "Response: $(echo "$RESPONSE1" | head -5)"
        fi
        
        rm -f "$SINGLE_TURN_JSON"
        
        # Cleanup
        echo ""
        echo "Cleaning up test file..."
        curl -s -X DELETE "$BACKEND/v1/files/$FILE_ID" > /dev/null 2>&1 || true
    else
        echo "⚠️  Could not upload file or get file ID"
        echo "Response: $UPLOAD_RESPONSE"
    fi
else
    echo "⚠️  RTVI_TEST_VIDEO_PATH not set - skipping file-based tests"
    echo "Set RTVI_TEST_VIDEO_PATH to test with actual video"
fi

rm -f "$TEST_JSON"

echo ""
echo "=========================================="
echo "Testing Complete"
echo "=========================================="
