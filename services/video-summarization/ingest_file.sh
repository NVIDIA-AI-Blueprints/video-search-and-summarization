#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#set -euo pipefail

CONTAINER_NAME="via-engine-$USER"
BACKEND_PORT=""
FILE_PATH=""
MODEL=""
MODEL_SEED=1
MODEL_MAX_TOKENS=512
MODEL_TOP_P=1
MODEL_TOP_K=10
CHUNK_DURATION=10
MODEL_TEMPERATURE=0.4
ENABLE_VLM_STRUCTURED_OUTPUT=true
EVENTS="accident,pedestrian crossing,vehicle crossing,traffic violation"
OBJECTS_OF_INTEREST="cars,trucks,pedestrians"
SCENARIO="traffic monitoring"
DEBUG=false
PRINT_CURL_COMMAND=false
SCHEMA='{"title":"EventExtraction","description":"Extract structured events from video captions","type":"object","properties":{"events":{"type":"array","items":{"type":"object","properties":{"start_time":{"type":"number"},"end_time":{"type":"number"},"description":{"type":"string"},"type":{"type":"string"}},"required":["start_time","end_time","description","type"]}}},"required":["events"]}'
BATCH_RESPONSE_METHOD="json_schema"
AUTO_GENERATE_PROMPT=false
TIME_METADATA_KEYS="start_pts,end_pts"
OVERRIDE_VLM_PROMPT=false
ENABLE_REASONING=false

PROMPT="You are an intelligent traffic system. You must monitor and take note of all traffic related events. Start each event description with a start and end time stamp of the event."

# Helper function to properly escape values for shell
# Uses printf %q which escapes strings for safe shell reuse
shell_escape() {
    printf '%q' "$1"
}

show_help() {
    cat << EOF
Usage: $0 --backend-port PORT --file-path PATH [OPTIONS]

Run video ingestion and summarization inside via-engine-$USER Docker container.

Required Arguments:
  --backend-port PORT              Port number where VIA backend is running
  --file-path PATH                 Path to video file to process (will be copied to container if not already there)

Optional Arguments:
  --container-name NAME            Docker container name (default: via-engine-$USER)
  --model MODEL                    Model to use (default: auto-detect from server)
  --model-seed SEED               Random seed for model (default: 1)
  --model-max-tokens TOKENS       Maximum tokens for model output (default: 512)
  --model-top-p VALUE             Top-p sampling parameter (default: 1)
  --model-top-k VALUE             Top-k sampling parameter (default: 10)
  --chunk-duration SECONDS        Duration of video chunks in seconds (default: 10)
  --model-temperature TEMP        Model temperature (default: 0.4)
  --prompt TEXT                   Custom prompt for video analysis
  --disable-vlm-structured-output Disable structured JSON output from VLM (enabled by default)
  --events TEXT                   Comma-separated list of events to focus on
  --objects-of-interest TEXT      Comma-separated list of objects to focus on
  --scenario TEXT                 Scenario description (default: "traffic monitoring")
  --schema TEXT                   Schema for structured output
  --batch-response-method TEXT    Batch response method
  --auto-generate-prompt          Enable automatic prompt generation
  --time-metadata-keys TEXT       Comma-separated list of time metadata keys
  --override-vlm-prompt           Override the VLM prompt with the user supplied prompt
  --enable-reasoning              Enable reasoning mode for the model
  --debug                         Enable debug mode to display detailed performance metrics
  --print-curl-command            Print the curl command instead of executing the request
  -h, --help                      Display this help message and exit

Examples:
  # Basic usage with required arguments
  $0 --backend-port 8000 --file-path /path/to/video.mp4

  # With custom container name
  $0 --backend-port 8000 --file-path /path/to/video.mp4 \\
     --container-name my-custom-container

  # With custom model and parameters
  $0 --backend-port 8000 --file-path /path/to/video.mp4 \\
     --model gpt-4o --model-temperature 0.7

  # With custom chunk duration
  $0 --backend-port 8000 --file-path /path/to/video.mp4 \\
     --chunk-duration 15

  # With custom events (structured output is enabled by default)
  $0 --backend-port 8000 --file-path /path/to/video.mp4 \\
     --events "traffic violation,accident,pedestrian crossing" \\
     --objects-of-interest "cars,trucks,pedestrians" \\
     --scenario "traffic monitoring"

  # With custom schema (disabling VLM structured output)
  $0 --backend-port 8000 --file-path /path/to/video.mp4 \\
     --disable-vlm-structured-output \\
     --schema '{"your":"schema"}'

  # With debug mode to see performance metrics
  $0 --backend-port 8000 --file-path /path/to/video.mp4 --debug

Notes:
  - The script expects a running Docker container (default: 'via-engine-$USER')
  - The video file will be copied to /tmp inside the container
  - All processing happens inside the container
EOF
    exit 0
}


while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help) show_help ;;
        --backend-port) BACKEND_PORT="$2"; shift ;;
        --file-path) FILE_PATH="$2"; shift ;;
        --container-name) CONTAINER_NAME="$2"; shift ;;
        --model) MODEL="$2"; shift ;;
        --model-seed) MODEL_SEED="$2"; shift ;;
        --model-max-tokens) MODEL_MAX_TOKENS="$2"; shift ;;
        --model-top-p) MODEL_TOP_P="$2"; shift ;;
        --model-top-k) MODEL_TOP_K="$2"; shift ;;
        --chunk-duration) CHUNK_DURATION="$2"; shift ;;
        --model-temperature) MODEL_TEMPERATURE="$2"; shift ;;
        --prompt) PROMPT="$2"; shift ;;
        --disable-vlm-structured-output) ENABLE_VLM_STRUCTURED_OUTPUT=false ;;
        --events) EVENTS="$2"; shift ;;
        --objects-of-interest) OBJECTS_OF_INTEREST="$2"; shift ;;
        --scenario) SCENARIO="$2"; shift ;;
        --schema) SCHEMA="$2"; shift ;;
        --batch-response-method) BATCH_RESPONSE_METHOD="$2"; shift ;;
        --auto-generate-prompt) AUTO_GENERATE_PROMPT=true ;;
        --time-metadata-keys) TIME_METADATA_KEYS="$2"; shift ;;
        --override-vlm-prompt) OVERRIDE_VLM_PROMPT=true ;;
        --enable-reasoning) ENABLE_REASONING=true ;;
        --debug) DEBUG=true ;;
        --print-curl-command) PRINT_CURL_COMMAND=true ;;
        *)
            echo "Unknown argument: $1"
            echo "Use -h or --help for usage information"
            exit 1 ;;
    esac
    shift
done

if [[ -z "$BACKEND_PORT" ]]; then
    echo "ERROR: --backend-port is required"
    echo "Use -h or --help for usage information"
    exit 1
fi

if [[ -z "$FILE_PATH" ]]; then
    echo "ERROR: --file-path is required"
    echo "Use -h or --help for usage information"
    exit 1
fi

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "ERROR: Container '${CONTAINER_NAME}' is not running"
    exit 1
fi

echo "[INFO] Using container: $CONTAINER_NAME"

BACKEND="http://localhost:${BACKEND_PORT}"
export VIA_BACKEND="$BACKEND"
echo "[INFO] Using backend: $VIA_BACKEND"

# Check if FILE_PATH is a URL (http, https, s3, minio, etc.)
if [[ "$FILE_PATH" =~ ^(https?|s3|minio|ftp|ftps):// ]]; then
    echo "[INFO] Detected URL: $FILE_PATH"
    CONTAINER_FILE_PATH="$FILE_PATH"
else
    # Copy file into container if it's not already there
    FILE_NAME=$(basename "$FILE_PATH")
    CONTAINER_FILE_PATH="/tmp/${FILE_NAME}"

    if docker exec "$CONTAINER_NAME" test -f "$CONTAINER_FILE_PATH" 2>/dev/null; then
        echo "[INFO] File already exists in container: $CONTAINER_FILE_PATH"
    else
        echo "[INFO] Copying file to container: $FILE_PATH -> $CONTAINER_FILE_PATH"
        docker cp "$FILE_PATH" "${CONTAINER_NAME}:${CONTAINER_FILE_PATH}"
    fi
fi

# Fetch model from server if not provided via command line
if [[ -z "$MODEL" ]]; then
    echo "[INFO] Fetching available models from server..."
    MODEL=$(docker exec -e VIA_BACKEND="$BACKEND" "$CONTAINER_NAME" \
        python3 /opt/nvidia/via/via-engine/via_client_cli.py list-models --backend "$BACKEND" 2>/dev/null | \
        grep -E '^\│' | \
        tail -n +2 | \
        head -n 1 | \
        awk -F'│' '{print $2}' | \
        xargs)

    if [[ -z "$MODEL" ]]; then
        echo "ERROR: Failed to retrieve model from server"
        exit 1
    fi
    echo "[INFO] Using model: $MODEL"
else
    echo "[INFO] Using model from arguments: $MODEL"
fi

echo "[INFO] Using file: $CONTAINER_FILE_PATH"

# Build optional parameters
OPTIONAL_PARAMS=""

# Pass prompt in case of overriding VLM prompt
if [[ -n "$PROMPT" ]]; then
    echo "[INFO] Using prompt: $PROMPT"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --prompt $(shell_escape "$PROMPT")"
fi

# Always pass events and scenario regardless of VLM structured output mode
if [[ -n "$EVENTS" ]]; then
    echo "[INFO] Events: $EVENTS"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --events $(shell_escape "$EVENTS")"
fi

if [[ -n "$SCENARIO" ]]; then
    echo "[INFO] Scenario: $SCENARIO"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --scenario $(shell_escape "$SCENARIO")"
fi

# Handle VLM structured output mode
if [[ "$ENABLE_VLM_STRUCTURED_OUTPUT" == "true" ]]; then
    echo "[INFO] Using VLM structured output mode"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --enable-vlm-structured-output"

    # Objects of interest only when VLM structured output is enabled
    if [[ -n "$OBJECTS_OF_INTEREST" ]]; then
        echo "[INFO] Objects of interest: $OBJECTS_OF_INTEREST"
        OPTIONAL_PARAMS="$OPTIONAL_PARAMS --objects-of-interest $(shell_escape "$OBJECTS_OF_INTEREST")"
    fi
else
    # Explicitly disable VLM structured output
    echo "[INFO] Disabling VLM structured output mode"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --disable-vlm-structured-output"

    # Pass schema and related parameters when VLM structured output is disabled
    if [[ -n "$SCHEMA" ]]; then
        echo "[INFO] Schema: $SCHEMA"
        OPTIONAL_PARAMS="$OPTIONAL_PARAMS --schema $(shell_escape "$SCHEMA")"
    fi

    if [[ -n "$BATCH_RESPONSE_METHOD" ]]; then
        echo "[INFO] Batch response method: $BATCH_RESPONSE_METHOD"
        OPTIONAL_PARAMS="$OPTIONAL_PARAMS --batch-response-method $(shell_escape "$BATCH_RESPONSE_METHOD")"
    fi

    if [[ "$AUTO_GENERATE_PROMPT" == "true" ]]; then
        echo "[INFO] Auto-generate prompt enabled"
        OPTIONAL_PARAMS="$OPTIONAL_PARAMS --auto-generate-prompt"
    fi

    if [[ -n "$TIME_METADATA_KEYS" ]]; then
        echo "[INFO] Time metadata keys: $TIME_METADATA_KEYS"
        OPTIONAL_PARAMS="$OPTIONAL_PARAMS --time-metadata-keys $(shell_escape "$TIME_METADATA_KEYS")"
    fi
fi

if [[ "$OVERRIDE_VLM_PROMPT" == "true" ]]; then
    echo "[INFO] Override VLM prompt enabled"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --override-vlm-prompt"
fi

if [[ "$ENABLE_REASONING" == "true" ]]; then
    echo "[INFO] Reasoning mode enabled"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --enable-reasoning"
fi

if [[ "$PRINT_CURL_COMMAND" == "true" ]]; then
    echo "[INFO] Printing curl command only"
    OPTIONAL_PARAMS="$OPTIONAL_PARAMS --print-curl-command"
fi

echo "[INFO] Optional parameters: $OPTIONAL_PARAMS"

# Capture the summarize output to extract request ID
SUMMARIZE_CMD="python3 /opt/nvidia/via/via-engine/via_client_cli.py summarize \
    --backend $(shell_escape "$BACKEND") \
    --url $(shell_escape "$CONTAINER_FILE_PATH") \
    --source-type file \
    --model $(shell_escape "$MODEL") \
    --chunk-duration $(shell_escape "$CHUNK_DURATION") \
    --model-temperature $(shell_escape "$MODEL_TEMPERATURE") \
    --model-seed $(shell_escape "$MODEL_SEED") \
    --model-max-tokens $(shell_escape "$MODEL_MAX_TOKENS") \
    --model-top-p $(shell_escape "$MODEL_TOP_P") \
    --model-top-k $(shell_escape "$MODEL_TOP_K") \
    --stream \
    $OPTIONAL_PARAMS"

SUMMARIZE_OUTPUT=$(docker exec -e VIA_BACKEND="$BACKEND" "$CONTAINER_NAME" bash -c "$SUMMARIZE_CMD" 2>&1)

# Display the output
echo "$SUMMARIZE_OUTPUT"

# Only extract performance metrics if debug mode is enabled and not just printing curl command
if [[ "$DEBUG" == "true" && "$PRINT_CURL_COMMAND" == "false" ]]; then
    # Extract the request ID from the output
    REQUEST_ID=$(echo "$SUMMARIZE_OUTPUT" | grep -i "request id:" | head -1 | grep -oE "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")

    if [[ -z "$REQUEST_ID" ]]; then
        echo "[WARNING] Could not extract request ID from output"
    else
        echo ""
        echo "[INFO] Request ID: $REQUEST_ID"
        echo "[INFO] Waiting for request to complete..."

        # Wait a moment for the health summary file to be written
        sleep 2

        # Read the health summary JSON file
        HEALTH_SUMMARY_FILE="/tmp/via-logs/via_health_summary_${REQUEST_ID}.json"

        if docker exec "$CONTAINER_NAME" test -f "$HEALTH_SUMMARY_FILE" 2>/dev/null; then
            echo "[INFO] Reading health summary from: $HEALTH_SUMMARY_FILE"

            # Extract metrics using Python
            docker exec "$CONTAINER_NAME" python3 -c "
import json

try:
    with open('$HEALTH_SUMMARY_FILE') as f:
        data = json.load(f)

    # Calculate average VLM latency per chunk from all_times
    all_times = data.get('all_times', [])
    vlm_latencies = []
    for chunk in all_times:
        if chunk.get('vlm_start') and chunk.get('vlm_end'):
            latency = chunk['vlm_end'] - chunk['vlm_start']
            vlm_latencies.append(latency)

    avg_vlm_latency = sum(vlm_latencies) / len(vlm_latencies) if vlm_latencies else 0

    decode_latency = data.get('decode_latency', 0)
    e2e_latency = data.get('e2e_latency', 0)
    ca_rag_latency = data.get('ca_rag_latency', 0)
    input_tokens = data.get('total_vlm_input_tokens', 0)
    output_tokens = data.get('total_vlm_output_tokens', 0)
    num_chunks = len(vlm_latencies)

    print()
    print('=' * 50)
    print(f'Average VLM Latency per Chunk: {avg_vlm_latency:.4f} seconds')
    print('=' * 50)
    print()
    print('Additional Metrics:')
    print(f'  Number of Chunks:    {num_chunks}')
    print(f'  Decode Latency:      {decode_latency:.4f} seconds')
    print(f'  E2E Latency:         {e2e_latency:.4f} seconds')
    print(f'  CA-RAG Latency:      {ca_rag_latency:.4f} seconds')
    print(f'  Total Input Tokens:  {input_tokens}')
    print(f'  Total Output Tokens: {output_tokens}')
except Exception as e:
    print(f'Error reading health summary: {e}')
"
        else
            echo "[WARNING] Health summary file not found: $HEALTH_SUMMARY_FILE"
            echo "[INFO] Health evaluation may not be enabled on the server"
        fi
    fi
fi
