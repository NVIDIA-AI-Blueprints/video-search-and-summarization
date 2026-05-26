#! /bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Fix permissions for mounted volumes
# `os.walk` yields each directory exactly once as `cur` (including the root),
# so chmod-ing `cur` here matches `find $dir -type d -exec chmod 755 {} +`.
python3 -c '
import os, sys
for d in sys.argv[1:]:
    if not os.path.isdir(d):
        continue
    for cur, dirs, files in os.walk(d):
        try: os.chmod(cur, 0o755)
        except OSError: pass
        for x in files:
            try: os.chmod(os.path.join(cur, x), 0o644)
            except OSError: pass
' /home/lvsuser/.via /home/lvsuser/.milvus.io 2>/dev/null || true

# Source .env file if it exists and SKIP_ENV_FILE is not set
if [ -z "$SKIP_ENV_FILE" ] && [ -f .env ]; then
    source .env
fi

# Configuration defaults
CA_RAG_CONFIG="${CA_RAG_CONFIG:-/opt/nvidia/via/default_config.yaml}"
DISABLE_CA_RAG=${DISABLE_CA_RAG:-false}
MODE="${MODE:-release}"
export VSS_LOG_LEVEL=$VSS_LOG_LEVEL

python3 -c 'import os; os.makedirs("/tmp/via-logs/", exist_ok=True)'

PID_FILE="/tmp/pids.txt"

if [ "$MODE" == "release" ]; then
    export PYTHONWARNINGS=ignore
fi

kill_processes() {
    while read pid; do
        # Existence check + PGID lookup via Python (avoids shipping procps).
        # os.getpgid raises ProcessLookupError if the PID is gone.
        pgid=$(python3 -c '
import os, sys
try:
    print(os.getpgid(int(sys.argv[1])))
except (ValueError, ProcessLookupError, PermissionError):
    pass
' "$pid" 2>/dev/null)
        if [ -n "$pgid" ]; then
            kill -9 "-$pgid" 2>/dev/null
            echo "Killed process with PID $pid"
        fi
    done < "$PID_FILE"
    > "$PID_FILE"
}

check_via_process_status() {
    process_pid=$!
    if [ $? -eq 0 ]; then
        echo $process_pid >> "$PID_FILE"
    else
        echo "Failed to start via_server"
        exit 1
    fi

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

start_via_server() {
    EXTRA_ARGS="$VSS_EXTRA_ARGS"
    if [ "$ENABLE_AUDIO" = true ]; then
        EXTRA_ARGS+=" --enable-audio"
    fi
    if [ $DISABLE_CA_RAG = true ]; then
        EXTRA_ARGS+=" --disable-ca-rag"
    fi

    if [ "$MODE" = "release" ]; then
        echo "Starting VIA server in release mode"
        EXE="python3 -Wignore via-engine/via_server.py"
    else
        echo "Starting VIA server in development mode"
        EXE="python3 -Wignore src/via_server.py"
    fi

    # Remove any stale logs from previous runs
    if [[ -n "${VIA_LOG_DIR}" && -d "${VIA_LOG_DIR}" ]]; then
        python3 -c '
import os, shutil, sys, glob
for p in glob.glob(os.path.join(sys.argv[1], "*")):
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)
    except OSError:
        pass
' "${VIA_LOG_DIR}"
    fi

    # Start via_server
    TRANSFORMERS_VERBOSITY=error $EXE --port $BACKEND_PORT \
        --ca-rag-config $CA_RAG_CONFIG $EXTRA_ARGS &
    check_via_process_status
}

start_processes() {
    if [ -z "${BACKEND_PORT}" ]; then
        echo "Please set BACKEND_PORT env variable"
        exit 1
    fi

    if [ -z "${RTVI_VLM_URL}" ]; then
        echo "Warning: RTVI_VLM_URL not set, defaulting to http://localhost:8000"
    fi

    start_via_server
}

# Check if PID file exists
if [ -f "$PID_FILE" ]; then
    kill_processes 9
fi

trap kill_processes 9 EXIT

start_processes
echo "***********************************************************"
echo "VIA Server loaded"
echo "Backend is running at http://0.0.0.0:$BACKEND_PORT"
echo "Press ctrl+C to stop"
echo "***********************************************************"
wait
