#! /bin/bash
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

ASSET_STORAGE_DIR="${ASSET_STORAGE_DIR:-/tmp/assets}"

CA_RAG_CONFIG="${CA_RAG_CONFIG:-/opt/nvidia/via/default_config.yaml}"
GRAPH_RAG_PROMPT_CONFIG="${GRAPH_RAG_PROMPT_CONFIG:-/opt/nvidia/via/warehouse_graph_rag_config.yaml}"
CV_PIPELINE_TRACKER_CONFIG="${CV_PIPELINE_TRACKER_CONFIG:-/opt/nvidia/via/config/default_tracker_config.yml}"

DISABLE_CA_RAG=${DISABLE_CA_RAG:-false}
DISABLE_FRONTEND=${DISABLE_FRONTEND:-false}
DISABLE_GUARDRAILS=${DISABLE_GUARDRAILS:-false}
DISABLE_CV_PIPELINE=${DISABLE_CV_PIPELINE:-true}

MILVUS_DB_HOST="${MILVUS_DB_HOST:-127.0.0.1}"

if [ -z $MILVUS_DB_PORT ]; then
MILVUS_DB_PORT=$((19530 + RANDOM % 100))
fi
# Assigning to itself for sake of completion.
MILVUS_DATA_DIR=${MILVUS_DATA_DIR}

MODE="${MODE:-release}"

MODEL_PATH="${MODEL_PATH:-/opt/models/vila-llama-3-8b-lita-im-se-didemo-charades-warehouse-medical-short-e031/}"
NUM_GPUS="${NUM_GPUS:-`nvidia-smi --query-gpu=name --format=csv,noheader | wc -l`}"
TRT_LLM_MODE=${TRT_LLM_MODE:-int4_awq}

EXAMPLE_STREAMS_DIR="${EXAMPLE_STREAMS_DIR:-/opt/nvidia/via/streams}"

VLM_MODEL_TO_USE="${VLM_MODEL_TO_USE:-openai-compat}"
export VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME="${VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME:-gpt-4o}"
export VSS_LOG_LEVEL=$VSS_LOG_LEVEL

ENABLE_NSYS_PROFILER="${ENABLE_NSYS_PROFILER:-false}"

if [[ $NUM_GPUS -eq 0 ]]; then
    echo "Error: No GPUs were found"
    exit 1
fi

NUM_NVDEC_ENGINES=$(nvdec_get_count)
echo "GPU has $NUM_NVDEC_ENGINES decode engines"

export CUDA_MPS_PIPE_DIRECTORY=/tmp/nvidia-mps-via

pip3 install /opt/nvidia/via/3rdparty_sources/annoy-1.17.3.tar.gz --no-deps --force-reinstall >/dev/null 2>&1

# Hide gstreamer failed to load warnings
python3 via-engine/utils.py 2>/dev/null
python3 src/utils.py 2>/dev/null

if [ -z $VLM_BATCH_SIZE ]; then
    GPU_MEM=0
    if [[ $NUM_GPUS -gt 0 ]]; then
        GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader -i 0 | awk '{print $1}')
    fi
    echo "Total GPU memory is $GPU_MEM MiB per GPU"

    if [[ $TRT_LLM_MODE == "fp16" ]]; then
        if [[ $GPU_MEM -gt 80000 ]]; then
            VLM_BATCH_SIZE=16
        elif [[ $GPU_MEM -gt 46000 ]]; then
            VLM_BATCH_SIZE=2
        else
            VLM_BATCH_SIZE=1
        fi
    else
        if [[ $GPU_MEM -gt 80000 ]]; then
            VLM_BATCH_SIZE=128
        elif [[ $GPU_MEM -gt 46000 ]]; then
            VLM_BATCH_SIZE=16
        else
            VLM_BATCH_SIZE=3
        fi
    fi
    echo "Auto-selecting VLM Batch Size to $VLM_BATCH_SIZE"
else
    echo "Using VLM Batch Size $VLM_BATCH_SIZE"
fi

mkdir -p /tmp/via-logs/

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

start_demo_client() {
    # Start via_demo_client
    if [ "$MODE" = "release" ]; then
        EXE="python3 via-engine/via_demo_client.py"
    else
        EXE="python3 src/via_demo_client.py"
    fi
    $EXE --backend http://localhost:$BACKEND_PORT --port $FRONTEND_PORT --examples-streams-directory $EXAMPLE_STREAMS_DIR  &
    process_pid=$!
    if [ $? -eq 0 ]; then
        echo $process_pid >> "$PID_FILE"
    else
        echo "Failed to start via_demo_client"
        exit 1
    fi
    # Wait for via_server to come up
    while true; do
        response=$(curl -s "http://localhost:$FRONTEND_PORT/")
        if [ $? -eq 0 ]; then
            break
        fi
    done
}

check_milvus() {
    while true; do
        python3 << END_PYTHON
from pymilvus import connections
import sys
try:
    connections.connect("default", host="$MILVUS_DB_HOST", port="$MILVUS_DB_PORT")
except:
    sys.exit(-1)
END_PYTHON
        if [ $? -eq 0 ]; then
            break
        fi
        echo "Waiting for milvus server to start..."
        sleep 1
    done
    echo "Milvus server started."
}

start_milvus() {
    # Stop milvus if already running
    PROCESS=$(ps -e | grep milvus | grep -v grep | awk '{print $1}')
    if [ -n "$PROCESS" ]; then
        echo "Stopping milvus server..."
        kill -9 $PROCESS
        echo "Milvus server stopped"
    fi
    if [ $DISABLE_CA_RAG = false ] && [ $MILVUS_DB_HOST == "127.0.0.1" ]; then
        echo "Running milvus server"
        # Start milvus_server
        if [[ -z "$MILVUS_DATA_DIR" ]]; then
            milvus-server --proxy-port $MILVUS_DB_PORT &
        else
            echo "Milvus data dir moved to " $MILVUS_DATA_DIR
            milvus-server --proxy-port $MILVUS_DB_PORT --data $MILVUS_DATA_DIR &
        fi
        process_pid=$!
        if [ $? -eq 0 ]; then
            echo $process_pid >> "$PID_FILE"
            check_milvus
        else
            echo "Failed to start milvus-server"
            exit 1
        fi
    fi
}

check_via_process_status() {
    process_pid=$!
    if [ $? -eq 0 ]; then
        echo $process_pid >> "$PID_FILE"
    else
        echo "Failed to start via_server"
        exit 1
    fi

    # Wait for via_server to come up
    while true; do
        response=$(curl -s "http://localhost:$BACKEND_PORT/health/ready")
        if [ $? -eq 0 ]; then
            break
        fi
        if ! kill -0 $process_pid 2>/dev/null; then
            exit 1
        fi
    done
}

start_cuda_mps_server() {
    nvidia-cuda-mps-control -f >/dev/null 2>&1 &
    echo $! >> "$PID_FILE"
    sleep 2
}

configure_riva_asr_service() {
    if [ -z "${RIVA_ASR_SERVER_URI}" ]; then
        echo "Please set RIVA_ASR_SERVER_URI env variable"
        exit 1
    fi
    if [ -z "${RIVA_ASR_GRPC_PORT}" ]; then
        echo "Please set RIVA_ASR_GRPC_PORT env variable"
        exit 1
    fi

    echo "Audio transcription enabled."
    mkdir -p /tmp/via
    cp /opt/nvidia/via/riva_asr_grpc_conf.yaml /tmp/via/
    RIVA_CONFIG_FILE="/tmp/via/riva_asr_grpc_conf.yaml"
    sed -i -e "s|server_uri.*|server_uri: \"$RIVA_ASR_SERVER_URI:$RIVA_ASR_GRPC_PORT\"|" ${RIVA_CONFIG_FILE}
    sed -i -e "s|is_nim.*|is_nim: $RIVA_ASR_SERVER_IS_NIM|" ${RIVA_CONFIG_FILE}
    if [ ! -z "${RIVA_ASR_MODEL_NAME}" ]; then
        sed -i -e "s|model_name.*|model_name: \"$RIVA_ASR_MODEL_NAME\"|" ${RIVA_CONFIG_FILE}
    fi
    if [ ! -z "${RIVA_ASR_SERVER_USE_SSL}" ]; then
        sed -i -e "s|use_ssl.*|use_ssl: $RIVA_ASR_SERVER_USE_SSL|" ${RIVA_CONFIG_FILE}
    fi
    if [ ! -z "${RIVA_ASR_SERVER_FUNC_ID}" ]; then
        sed -i -e "s|function-id.*|function-id: $RIVA_ASR_SERVER_FUNC_ID|" ${RIVA_CONFIG_FILE}
    fi
    if [ ! -z "${RIVA_ASR_SERVER_API_KEY}" ]; then
        sed -i -e "s|authorization.*|authorization: \"Bearer $RIVA_ASR_SERVER_API_KEY\"|" ${RIVA_CONFIG_FILE}
    fi

    if [ ! -z "$RIVA_ASR_HTTP_PORT" ] && [ "$ENABLE_RIVA_SERVER_READINESS_CHECK" = "true" ]; then
        while true; do
            response=$(curl -s -X 'GET' "$RIVA_ASR_SERVER_URI:$RIVA_ASR_HTTP_PORT/v1/health/ready")
            ready_status="\"status\":[[:space:]]*\"ready\""
            if [[ $response =~ $ready_status ]]; then
                break
            fi
            sleep 3
            echo "Waiting for Riva ASR server to be ready at $RIVA_ASR_SERVER_URI:$RIVA_ASR_HTTP_PORT/v1/health/ready"
        done
        echo "Riva ASR server is ready."
    fi

}

start_via_server() {
    EXTRA_ARGS="$VSS_EXTRA_ARGS"
    if [ $DISABLE_GUARDRAILS = true ]; then
        EXTRA_ARGS+=" --disable-guardrails"
    fi
    if [ $DISABLE_CV_PIPELINE = true ]; then
        EXTRA_ARGS+=" --disable-cv-pipeline"
    fi
    if [ "$ENABLE_AUDIO" = true ]; then
        EXTRA_ARGS+=" --enable-audio"
    fi
    if [ $DISABLE_CA_RAG = true ]; then
        EXTRA_ARGS+=" --disable-ca-rag"
    else
        # Start via_server
        EXTRA_ARGS+=" --milvus-db-port $MILVUS_DB_PORT --milvus-db-host $MILVUS_DB_HOST"
    fi
    if [ $ENABLE_NSYS_PROFILER = true ]; then
	    echo "Profiling with  nsys"
	    PROFILE_GPU_IDS=$(nvidia-smi --query-gpu=index --format=csv,noheader | paste -sd "," -)
	    EXE_PREFIX="nsys profile -t cuda,nvtx,osrt --python-backtrace=cuda --show-output=true --force-overwrite=true  --output=via_nsys_logs --gpu-metrics-device=$PROFILE_GPU_IDS --capture-range=cudaProfilerApi --capture-range-end=stop"
    fi

    if [ "$MODE" = "release" ]; then
	    echo "Starting VIA server in release mode"
	    EXE="python3 -Wignore via-engine/via_server.py"
    else
	    echo "Starting VIA server in development mode"
	    EXE="python3 -Wignore src/via_server.py"
    fi
    if [ ! -z $TRT_ENGINE_PATH ]; then
        EXTRA_ARGS+=" --trt-engine-dir $TRT_ENGINE_PATH"
    fi
    if [ $VLM_MODEL_TO_USE != "custom" ]; then
        EXTRA_ARGS+=" --vlm-model-type $VLM_MODEL_TO_USE"
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

    # Remove any stale logs from previous runs
    if [[ -n "${VIA_LOG_DIR}" && -d "${VIA_LOG_DIR}" ]]; then
        rm -rf "${VIA_LOG_DIR}"/*
    fi

    # Start via_server
    TRANSFORMERS_VERBOSITY=error $EXE_PREFIX $EXE --port $BACKEND_PORT \
        --model-path "$MODEL_PATH" --num-gpus $NUM_GPUS \
        --vlm-batch-size $VLM_BATCH_SIZE --ca-rag-config $CA_RAG_CONFIG \
        --graph-rag-prompt-config $GRAPH_RAG_PROMPT_CONFIG \
        --asset-dir $ASSET_STORAGE_DIR --num-decoders-per-gpu $(( NUM_NVDEC_ENGINES + 1)) \
        --trt-llm-mode $TRT_LLM_MODE $EXTRA_ARGS &
    check_via_process_status
}

start_processes() {

    if [ -z "${FRONTEND_PORT}" ]; then
        echo "Please set FRONTEND_PORT env variable"
        exit 1
    fi
    if [ -z "${BACKEND_PORT}" ]; then
        echo "Please set BACKEND_PORT env variable"
        exit 1
    fi

    if [ $DISABLE_CA_RAG = true ]; then
        echo "Disabling CA RAG, Also disabling milvus"
        ENABLE_MILVUS=false
    fi

    if [ "$INSTALL_PROPRIETARY_CODECS" = true ]; then
        if ! command -v ffmpeg_for_overlay_video >/dev/null 2>&1; then
            echo "Installing additional multimedia packages"
            bash user_additional_install.sh
        fi
    fi

    if [ "$ENABLE_AUDIO" = true ]; then
        configure_riva_asr_service
    fi

    start_milvus

    start_cuda_mps_server

    echo "Using $VLM_MODEL_TO_USE"
    start_via_server

    if [ $DISABLE_FRONTEND = false ]; then
        start_demo_client
    fi
}

# Check if PID file exists
if [ -f "$PID_FILE" ]; then
    # Kill existing processes
    kill_processes 9
fi

trap kill_processes 9 EXIT

start_processes
echo "***********************************************************"
echo "VIA Server loaded"
echo "Backend is running at http://0.0.0.0:$BACKEND_PORT"
if [ $DISABLE_FRONTEND = false ]; then
echo "Frontend is running at http://0.0.0.0:$FRONTEND_PORT"
else
echo "Frontend is disabled"
fi
echo "Press ctrl+C to stop"
echo "***********************************************************"
wait
