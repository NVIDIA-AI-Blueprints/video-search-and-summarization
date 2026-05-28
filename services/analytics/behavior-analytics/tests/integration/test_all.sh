#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Usage: ./test_all.sh [mode]
# This script runs all the integration tests for all the profiles
# mode: dev (default) - no cleanup on failure, prod - cleanup even on failure

set -euo pipefail

# Get mode parameter
MODE=${1:-dev}  # Use first argument or default to dev

# Validate mode parameter
if [ "$MODE" != "dev" ] && [ "$MODE" != "prod" ]; then
    echo "Invalid mode: $MODE. Must be 'dev' or 'prod'"
    exit 1
fi

echo "Running all tests in $MODE mode"
echo ""

# Function to run a test and check its result
run_test() {
    local profile1=$1
    local profile2=$2
    
    echo "================================================"
    echo "Running $profile1 $profile2 in $MODE mode"
    echo "================================================"
    
    ./test.sh "$profile1" "$profile2" "$MODE"
    TEST_EXIT_CODE=$?
    
    if [ $TEST_EXIT_CODE -ne 0 ]; then
        echo ""
        echo "❌ Test FAILED, skipping other tests..."
        exit 1
    fi
    
    sleep 10s
}

# Run all tests
run_test "warehouse_2d" "kafka"
echo ""

run_test "warehouse_2d" "redis"
echo ""

run_test "warehouse_2d" "mqtt"
echo ""

run_test "warehouse_3d" "kafka"
echo ""

run_test "warehouse_3d" "redis"
echo ""

run_test "smart_city" "kafka"
echo ""

echo ""
echo "================================================"
echo "✅ All tests completed successfully!"
echo "================================================"
