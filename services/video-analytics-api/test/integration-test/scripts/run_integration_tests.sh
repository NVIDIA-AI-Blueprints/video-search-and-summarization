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

# Run integration tests via Node (no curl). Node and npm must be available; run "npm install" in test/ first.
# Usage: ./scripts/run_integration_tests.sh [BASE_URL]
# BASE_URL defaults to http://localhost:8081

set -euo pipefail

BASE_URL="${1:-http://localhost:8081}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_TEST_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES_DIR="${INTEGRATION_TEST_DIR}/fixtures"

WEB_APIS_ROOT="${WEB_APIS_ROOT:-}"
if [ -z "$WEB_APIS_ROOT" ]; then
    WEB_APIS_ROOT="$(cd "$INTEGRATION_TEST_DIR/.." && pwd)"
    [ ! -f "$WEB_APIS_ROOT/docker/Dockerfile" ] && WEB_APIS_ROOT="$(cd "$INTEGRATION_TEST_DIR/../.." && pwd)"
fi

if ! command -v node >/dev/null 2>&1; then
    echo "Node is required. Install Node.js and run 'npm install' in the test/ directory."
    exit 1
fi

node "$SCRIPT_DIR/run_integration_tests.js" "$BASE_URL" "$FIXTURES_DIR" "$WEB_APIS_ROOT"

echo "Running warehouse_2d_app tests..."
node "$SCRIPT_DIR/run_warehouse_2d_app_tests.js" "$BASE_URL" "$WEB_APIS_ROOT" "$FIXTURES_DIR"

echo "Running warehouse_3d_app tests..."
node "$SCRIPT_DIR/run_warehouse_3d_app_tests.js" "$BASE_URL" "$WEB_APIS_ROOT" "$FIXTURES_DIR"
