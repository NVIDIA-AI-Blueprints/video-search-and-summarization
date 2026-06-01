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
# Check 06: Container Running
# Mode: SSH
# Description: Verify alert-bridge container is running (requires SSH access)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

CHECK_NAME="Container Running"

# Skip if SSH not available
if ! require_ssh; then
    skip_check "$CHECK_NAME" "SSH not available"
    exit 0
fi

# Check container status
result=$(ssh_exec "docker ps --filter name=${ALERT_BRIDGE_CONTAINER} --format '{{.Status}}' 2>/dev/null" || echo "")

if [[ -z "$result" ]]; then
    fail "$CHECK_NAME" "Container '${ALERT_BRIDGE_CONTAINER}' not found"
    exit 1
fi

if [[ "$result" == *"Up"* ]]; then
    pass "$CHECK_NAME" "Status: $result"
else
    fail "$CHECK_NAME" "Container status: $result (expected 'Up')"
fi
