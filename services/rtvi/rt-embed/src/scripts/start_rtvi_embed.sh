#! /bin/bash
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

BACKEND_PORT="${BACKEND_PORT:-8000}"

ASSET_STORAGE_DIR="${ASSET_STORAGE_DIR:-/tmp/assets}"

# Validate MAX_ASSET_STORAGE_SIZE_GB against actual storage at startup.
# Without this, an unset or oversized limit silently allows unlimited storage,
# which causes OOM kills when using tmpfs or unexpectedly fills a bind-mounted disk.
if [ -d "$ASSET_STORAGE_DIR" ]; then
    _asset_fstype=$(findmnt -n -o FSTYPE --target "$ASSET_STORAGE_DIR" 2>/dev/null)
    if [ "$_asset_fstype" = "tmpfs" ]; then
        _tmpfs_kb=$(df -k "$ASSET_STORAGE_DIR" 2>/dev/null | awk 'NR==2{print $2}')
        _tmpfs_kb="${_tmpfs_kb:-8388608}"
        _tmpfs_gb=$(( _tmpfs_kb / 1048576 ))
        if [ -z "$MAX_ASSET_STORAGE_SIZE_GB" ]; then
            echo "WARNING: MAX_ASSET_STORAGE_SIZE_GB is unset; asset storage is unbounded but tmpfs at $ASSET_STORAGE_DIR is ${_tmpfs_gb}GB. Set MAX_ASSET_STORAGE_SIZE_GB<=${_tmpfs_gb} to enable eviction before tmpfs fills."
        elif [ "$(( MAX_ASSET_STORAGE_SIZE_GB * 1048576 ))" -gt "$_tmpfs_kb" ]; then
            echo "WARNING: MAX_ASSET_STORAGE_SIZE_GB=${MAX_ASSET_STORAGE_SIZE_GB} exceeds tmpfs size (${_tmpfs_gb}GB) at $ASSET_STORAGE_DIR. Set MAX_ASSET_STORAGE_SIZE_GB<=${_tmpfs_gb} to avoid out-of-space errors."
        fi
    else
        if [ -z "$MAX_ASSET_STORAGE_SIZE_GB" ]; then
            echo "WARNING: ASSET_STORAGE_DIR is a bind mount ($ASSET_STORAGE_DIR) but MAX_ASSET_STORAGE_SIZE_GB is unset — asset storage is unbounded. Set MAX_ASSET_STORAGE_SIZE_GB to enable eviction."
        fi
    fi
    unset _asset_fstype _tmpfs_kb _tmpfs_gb
fi

export RTVI_DISABLE_LIVESTREAM_PREVIEW=${RTVI_DISABLE_LIVESTREAM_PREVIEW:-true}

MODE="${MODE:-release}"

NUM_GPUS="${NUM_GPUS:-`nvidia-smi --query-gpu=name --format=csv,noheader | wc -l`}"
EXAMPLE_STREAMS_DIR="${EXAMPLE_STREAMS_DIR:-/opt/nvidia/rtvi/streams}"

VLM_MODEL_TO_USE="${VLM_MODEL_TO_USE:-custom}"
MODEL_IMPLEMENTATION_PATH="${MODEL_IMPLEMENTATION_PATH:-/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1}"
MODEL_PATH="${MODEL_PATH:-git:https://huggingface.co/nvidia/Cosmos-Embed1-448p}"

export VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME="${VLM_OPENAI_MODEL_DEPLOYMENT_NAME:-gpt-4o}"
export LOG_LEVEL=$LOG_LEVEL
export VLM_INPUT_WIDTH=
export VLM_INPUT_HEIGHT=

ENABLE_NSYS_PROFILER="${ENABLE_NSYS_PROFILER:-false}"

SM_ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader -i 0)

export VLLM_WORKER_MULTIPROC_METHOD=spawn
export GST_ENABLE_CUSTOM_PARSER_MODIFICATIONS="${GST_ENABLE_CUSTOM_PARSER_MODIFICATIONS:-1}"

if [[ $NUM_GPUS -eq 0 ]]; then
    echo "Error: No GPUs were found"
    exit 1
fi


NUM_NVDEC_ENGINES=$(nvdec_get_count)
echo "GPU has $NUM_NVDEC_ENGINES decode engines"

export CUDA_MPS_PIPE_DIRECTORY=/tmp/nvidia-mps-rtvi

# Hide gstreamer failed to load warnings
python3 rtvi/utils.py 2>/dev/null
python3 src/utils.py 2>/dev/null

FREE_GPU_MEM=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader -i 0 | awk '{print $1}')
echo "Free GPU memory is $FREE_GPU_MEM MiB"

if [ $FREE_GPU_MEM -lt 40000 ]; then
    export DISABLE_DECODER_REUSE="${DISABLE_DECODER_REUSE:-true}"
else
    export DISABLE_DECODER_REUSE="${DISABLE_DECODER_REUSE:-false}"
fi

if [ "$DISABLE_DECODER_REUSE" == "true" ]; then
    echo "Disabling decoder reuse"
fi

if [ -f /etc/nv_tegra_release ]; then
    if grep -q "R38 (release), REVISION: 2.0" /etc/nv_tegra_release; then
        export LD_LIBRARY_PATH="/opt/nvidia/via/lib:${LD_LIBRARY_PATH}"
    fi
fi

GPU_MEM=0
if [[ $NUM_GPUS -gt 0 ]]; then
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader -i 0 | awk '{print $1}')
fi
if [[ $GPU_MEM == *"N/A"* ]]; then
    # Get total system memory in MiB if GPU memory is N/A
    GPU_MEM=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
fi
echo "Total GPU memory is $GPU_MEM MiB per GPU"

if [[ $GPU_MEM -le 50000 ]]; then
    if [[ -z "${VLLM_GPU_MEMORY_UTILIZATION}" ]]; then
        export VLLM_GPU_MEMORY_UTILIZATION=0.7
        echo "Setting VLLM_GPU_MEMORY_UTILIZATION to 0.7 (GPU mem <= 50GB)"
    fi
fi


if [ -z $VLM_BATCH_SIZE ]; then
    if [[ "$GPU_MEM" == *"N/A"* || $GPU_MEM -gt 80000 ]]; then
        VLM_BATCH_SIZE=64
    elif [[ $GPU_MEM -gt 46000 ]]; then
        VLM_BATCH_SIZE=16
    else
        VLM_BATCH_SIZE=4
    fi
    echo "Auto-selecting VLM Batch Size to $VLM_BATCH_SIZE"
else
    echo "Using VLM Batch Size $VLM_BATCH_SIZE"
fi

mkdir -p /tmp/rtvi-logs/

# File to store PIDs
PID_FILE="/tmp/pids.txt"

if [ "$MODE" == "release" ]; then
    export PYTHONWARNINGS=ignore
fi

# Function to kill processes
kill_processes() {
    # Read PIDs from file
    while read pid; do
        # Check if process is running
        if ps -p $pid > /dev/null; then
            # Kill the process
            kill -9 -$(ps -o pgid= $pid | grep -o '[0-9]*') 2>/dev/null
            echo "Killed process with PID $pid"
        fi
    done < "$PID_FILE"

    # Clear the PID file
    > "$PID_FILE"
}

check_rtvi_process_status() {
    process_pid=$!
    if [ -z "$process_pid" ]; then
        echo "Failed to start rtvi_server"
        exit 1
    fi
    echo $process_pid >> "$PID_FILE"

    # Wait for rtvi_server to come up
    while true; do
        response=$(curl -s "http://localhost:$BACKEND_PORT/v1/ready")
        if [ $? -eq 0 ]; then
            break
        fi
        if ! kill -0 $process_pid 2>/dev/null; then
            exit 1
        fi
    done
}

start_cuda_mps_server() {
    if [ "$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader)" = "12.1" ]; then
        return
    fi
    nvidia-cuda-mps-control -f >/dev/null 2>&1 &
    echo $! >> "$PID_FILE"
    sleep 2
}

start_rtvi_server() {
    if [ $VLM_MODEL_TO_USE == "custom" ] && [ -f "$MODEL_IMPLEMENTATION_PATH/install_prerequisites.sh" ]; then
        echo "Found prerequisites script for custom model. Installing dependencies..."
        bash "$MODEL_IMPLEMENTATION_PATH/install_prerequisites.sh"
    fi

    EXTRA_ARGS="$RTVI_EXTRA_ARGS"
    if [ "$ENABLE_AUDIO" = true ]; then
        EXTRA_ARGS+=" --enable-audio"
    fi
    if [ $ENABLE_NSYS_PROFILER = true ]; then
	    echo "Profiling with  nsys"
	    PROFILE_GPU_IDS=$(nvidia-smi --query-gpu=index --format=csv,noheader | paste -sd "," -)
	    EXE_PREFIX="nsys profile -t cuda,nvtx,osrt --python-backtrace=cuda --show-output=true --force-overwrite=true  --output=rtvi_nsys_logs --gpu-metrics-devices=$PROFILE_GPU_IDS --capture-range=cudaProfilerApi --capture-range-end=stop"
    fi

    EXE="python3 -Wignore -m server.rtvi_embed_server"
    if [ "$MODE" = "release" ]; then
        echo "Starting RTVI server in release mode"
        RUN_DIR="rtvi"
    else
        echo "Starting RTVI server in development mode"
        RUN_DIR="src"
    fi
    if [ ! -z "$MAX_ASSET_STORAGE_SIZE_GB" ]; then
        EXTRA_ARGS+=" --max-asset-storage-size $MAX_ASSET_STORAGE_SIZE_GB"
    fi
    if [ $VLM_MODEL_TO_USE == "openai-compat" ]; then
        if [ ! -z $NUM_VLM_PROCS ]; then
            EXTRA_ARGS+=" --num-vlm-procs $NUM_VLM_PROCS"
        else
            EXTRA_ARGS+=" --num-vlm-procs 10"
        fi
    fi
    if [ ! -z $VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK ]; then
        EXTRA_ARGS+=" --num-frames-per-second-or-fixed-frames-chunk $VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK"
    fi
    if [ ! -z "$MODEL_IMPLEMENTATION_PATH" ]; then
        EXTRA_ARGS+=" --model-implementation-path $MODEL_IMPLEMENTATION_PATH"
    fi
    if [ ! -z "$MODEL_REPOSITORY_SCRIPT_PATH" ]; then
        EXTRA_ARGS+=" --model-repository-script-path $MODEL_REPOSITORY_SCRIPT_PATH"
    fi

    # Kafka configuration - export environment variables for Kafka integration
    if [ "$KAFKA_ENABLED" = "true" ]; then
        EXTRA_ARGS+=" --kafka-enabled"
        if [ ! -z "$KAFKA_TOPIC" ]; then
            EXTRA_ARGS+=" --kafka-topic $KAFKA_TOPIC"
        fi
        if [ ! -z "$KAFKA_BOOTSTRAP_SERVERS" ]; then
            EXTRA_ARGS+=" --kafka-bootstrap-servers $KAFKA_BOOTSTRAP_SERVERS"
        fi
    fi


    # Start rtvi_server
    cd $RUN_DIR && TRANSFORMERS_VERBOSITY=error $EXE_PREFIX $EXE --port $BACKEND_PORT \
        --model-path "$MODEL_PATH" --num-gpus $NUM_GPUS \
        --vlm-model-type $VLM_MODEL_TO_USE \
        --vlm-batch-size $VLM_BATCH_SIZE \
        --asset-dir $ASSET_STORAGE_DIR --num-decoders-per-gpu $(( NUM_NVDEC_ENGINES + 1)) \
        $EXTRA_ARGS &
    check_rtvi_process_status
}

start_processes() {

    if [ -z "${BACKEND_PORT}" ]; then
        echo "Please set BACKEND_PORT env variable"
        exit 1
    fi

    # Handle INSTALL_PROPRIETARY_CODECS environment variable
    # When set to true/True/TRUE/1, downloads and extracts patent-encumbered codec packages
    # using apt-get download + dpkg -x (no root/sudo required).
    # Env vars (GST_PLUGIN_PATH, LD_LIBRARY_PATH, PATH) are sourced after extraction.
    install_codecs=$(echo "$INSTALL_PROPRIETARY_CODECS" | tr '[:upper:]' '[:lower:]')
    if [ "$install_codecs" = "true" ] || [ "$install_codecs" = "1" ] || [ "$install_codecs" = "yes" ]; then
        if [ ! -f "/opt/nvidia/rtvi/codecs/.installed" ]; then
            echo "INSTALL_PROPRIETARY_CODECS enabled: Downloading and extracting multimedia packages"
            bash /opt/nvidia/rtvi/install_codecs_nonroot.sh
        else
            echo "INSTALL_PROPRIETARY_CODECS enabled: Proprietary codecs already installed"
        fi
        # shellcheck source=/dev/null
        source /opt/nvidia/rtvi/codecs/codec_env.sh
    fi

    start_cuda_mps_server

    echo "Using $VLM_MODEL_TO_USE"
    start_rtvi_server
}

# Check if PID file exists
if [ -f "$PID_FILE" ]; then
    # Kill existing processes
    kill_processes 9
fi

trap kill_processes 9 EXIT

start_processes
echo "***********************************************************"
echo "RTVI Server loaded"
echo "Backend is running at http://0.0.0.0:$BACKEND_PORT"
echo "Press ctrl+C to stop"
echo "***********************************************************"
wait
