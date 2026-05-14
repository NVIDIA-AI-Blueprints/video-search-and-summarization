#!/bin/sh
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

set -e

MAX_RETRIES="${MAX_RETRIES:-30}"
RETRY_INTERVAL="${RETRY_INTERVAL:-2}"

check_port() {
    host=$1
    port=$2
    i=0
    while [ "$i" -lt "$MAX_RETRIES" ]; do
        if nc -z "$host" "$port" 2>/dev/null; then
            echo "$host:$port is healthy"
            return 0
        fi
        i=$((i + 1))
        echo "Waiting for $host:$port ... ($i/$MAX_RETRIES)"
        sleep "$RETRY_INTERVAL"
    done
    echo "$host:$port is not reachable after $MAX_RETRIES attempts"
    return 1
}

if [ "$BP_PROFILE" = "auto_calib" ]; then
    echo "BP_PROFILE is auto_calib, skipping broker health checks as auto_calib does not use broker"
    exit 0
fi

if [ "$STREAM_TYPE" = "kafka" ]; then
    check_port localhost 9092
elif [ "$STREAM_TYPE" = "redis" ]; then
    check_port localhost 6379
else
    echo "Invalid STREAM_TYPE: '$STREAM_TYPE'. Expected 'kafka' or 'redis'."
    exit 1
fi

echo "Broker health checks passed"
