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

# =============================================================================
# Check 07: Kafka Consumer Lag
# Mode: SSH
# Description: Check Kafka consumer lag (requires SSH access)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Kafka Consumer Lag"

# Skip if SSH not available
if ! require_ssh; then
    skip_check "$CHECK_NAME" "SSH not available"
    exit 0
fi

# Try to get consumer lag
# Note: This assumes kafka-consumer-groups.sh is available in the kafka container
result=$(ssh_exec "docker exec ${KAFKA_CONTAINER} kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group alert-bridge-group --describe 2>/dev/null | tail -n +3" || echo "")

if [[ -z "$result" ]]; then
    # Kafka CLI might not be available, skip gracefully
    skip_check "$CHECK_NAME" "Cannot query Kafka consumer groups"
    exit 0
fi

# Parse lag from output (simplified - actual parsing depends on Kafka version)
total_lag=$(echo "$result" | awk '{sum += $6} END {print sum}' 2>/dev/null || echo "0")

if [[ "$total_lag" == "0" || -z "$total_lag" ]]; then
    pass "$CHECK_NAME" "Consumer lag: 0"
elif [[ "$total_lag" -lt 1000 ]]; then
    pass "$CHECK_NAME" "Consumer lag: $total_lag (acceptable)"
else
    fail "$CHECK_NAME" "Consumer lag: $total_lag (high)"
fi
