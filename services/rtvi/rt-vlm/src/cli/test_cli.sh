#!/usr/bin/env bash
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

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  test_cli.sh --live-stream RTSP_URL --prompt "PROMPT" --system-prompt "SYSTEM_PROMPT" \
    [--backend URL] [--model MODEL_ID]

Examples:
  ./test_cli.sh --live-stream rtsp://10.63.144.151:9006/warehouse_sample.mp4 \
    --prompt "Describe events" \
    --system-prompt "Answer correctly" \
    --backend http://10.63.144.151:8010
USAGE
}

LIVE_STREAM="rtsp://nv-wowza-pdc.nvidia.com:1935/vod/warehouse_1.mp4"
PROMPT="Explain what is happening?"
SYSTEM_PROMPT="Answer Users questions correctly"
BACKEND="http://localhost:8010"
MODEL=""
CHUNK_DURATION=30

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live-stream) LIVE_STREAM="$2"; shift 2 ;;
    --prompt) PROMPT="$2"; shift 2 ;;
    --system-prompt) SYSTEM_PROMPT="$2"; shift 2 ;;
    --backend) BACKEND="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --chunk-duration) CHUNK_DURATION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done



echo "----------------------------------------------------------"
echo "--live-stream=$LIVE_STREAM"
echo "--prompt=$PROMPT"
echo "--system-prompt=$SYSTEM_PROMPT"
echo "--chunk-duration=$CHUNK_DURATION"
echo "----------------------------------------------------------"

if [[ -z "${LIVE_STREAM}" || -z "${PROMPT}" || -z "${SYSTEM_PROMPT}" ]]; then
  echo "Error: --live-stream, --prompt, and --system-prompt are required." >&2
  usage
  exit 1
fi

if [[ -z "${MODEL}" ]]; then
  if ! command -v jq >/dev/null 2>&1; then
    echo "Error: jq is required to auto-select a model. Provide --model or install jq." >&2
    exit 1
  fi
  MODEL=$(curl -fsS "$BACKEND/v1/models" | jq -r '.data[0].id')
  if [[ -z "${MODEL}" || "${MODEL}" == "null" ]]; then
    echo "Error: Failed to resolve model from $BACKEND/v1/models." >&2
    exit 1
  fi
fi

# Add live stream with sensor name
STREAM_ID=$(python3 rtvi_client_cli.py add-live-stream \
   $LIVE_STREAM \
  --description "Camera 1" \
  --place-name "Main Warehouse Entrance" \
  --place-type "warehouse-bay" \
  --place-lat 37.3706 \
  --place-lon -121.9672 \
  --place-alt 10.5 \
  --place-coordinate-x 25.0 \
  --place-coordinate-y 8.5 \
  --sensor-name "Camera_123" \
  --backend $BACKEND | grep -oP 'id: \K[^,]+') &&\
   [ -n "$STREAM_ID" ] && echo "Stream added successfully. Stream ID: $STREAM_ID" ||\
   { echo "Error: Failed to add live stream. Please check the stream URL and backend connection.";  }

# Trigger VLM generation request for video stream
python3 rtvi_client_cli.py generate-captions \
    --chunk-duration $CHUNK_DURATION --chunk-overlap-duration 0  \
    --prompt "$PROMPT" \
    --system-prompt "$SYSTEM_PROMPT" \
     --file-start-offset 0 \
     --model-temperature 0.4 \
     --model-top-p 1 \
     --model-top-k 100 \
     --model-max-tokens 512 \
     --model-seed 1 \
     --response-format json_object \
     --num-frames-per-second-or-fixed-frames-chunk 1  \
     --vlm-input-width 0 \
     --vlm-input-height 0 \
     --model  $MODEL \
     --backend $BACKEND \
     --stream \
     --id  $STREAM_ID
	 

# To stop the generate-captions request from another terminal, set the appropriate stream ID
python3 rtvi_client_cli.py stop-live-stream-processing $STREAM_ID \
    --backend $BACKEND

# To delete the above live stream
python3 rtvi_client_cli.py delete-live-stream $STREAM_ID --backend $BACKEND

