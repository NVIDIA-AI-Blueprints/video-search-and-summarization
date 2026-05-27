#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# VST BDD Tests - Docker Entrypoint

echo "==============================================="
echo "VST BDD Test Suite - Containerized"
echo "==============================================="
echo ""

# Display configuration
if [[ -f "config.json" ]]; then
    echo "Configuration loaded: config.json"
    BASE_URL=$(python3 -c "import json; print(json.load(open('config.json'))['api']['base_url'])" 2>/dev/null || echo "Not configured")
    echo "Target API: $BASE_URL"
    echo ""
    echo "Tip: Override with --base-url flag:"
    echo "  docker run ... vst-bdd-tests pytest --base-url http://your-api:30888 -v"
fi

echo ""
echo "Running tests with: poetry run $@"
echo ""

# Execute the command with poetry
exec poetry run "$@"
