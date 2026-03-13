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

set -euo pipefail

echo "Redis health check service started..."

# Configuration with defaults
MAX_RETRIES=${MAX_RETRIES:-60}                        # Max retries for Redis connection
RETRY_INTERVAL=${RETRY_INTERVAL:-2}                   # Seconds between retries
REDIS_HOST=${BOOTSTRAP_HOST:-localhost}
REDIS_PORT=${REDIS_PORT:-6379}

echo "Configuration:"
echo "  MAX_RETRIES: $MAX_RETRIES ($(($MAX_RETRIES * $RETRY_INTERVAL))s timeout)"
echo "  RETRY_INTERVAL: ${RETRY_INTERVAL}s"
echo "  REDIS_HOST: $REDIS_HOST"
echo "  REDIS_PORT: $REDIS_PORT"

echo "Waiting for Redis to be ready..."

# Wait for Redis to be reachable
redis_retry_count=0
echo "Waiting for Redis at $REDIS_HOST:$REDIS_PORT (max ${MAX_RETRIES} retries)..."

while [ $redis_retry_count -lt $MAX_RETRIES ]; do
    if nc -z $REDIS_HOST $REDIS_PORT 2>/dev/null; then
        echo "✓ Redis is reachable after $redis_retry_count retries"
        break
    fi
    
    redis_retry_count=$((redis_retry_count + 1))
    echo "[$redis_retry_count/$MAX_RETRIES] Waiting for Redis..."
    sleep $RETRY_INTERVAL
done

if [ $redis_retry_count -eq $MAX_RETRIES ]; then
    echo "❌ ERROR: Redis at $REDIS_HOST:$REDIS_PORT is not reachable after $MAX_RETRIES retries with $RETRY_INTERVAL seconds interval"
    exit 1
fi

echo "✅ Redis health check completed successfully"
exit 0

