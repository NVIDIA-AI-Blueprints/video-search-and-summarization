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

# Run all video-analytics-api integration test profiles.
# Usage: ./test_all.sh [mode]
#   mode: dev (default) or prod

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-dev}"

if [ "$MODE" != "dev" ] && [ "$MODE" != "prod" ]; then
    echo "Invalid mode: $MODE. Must be 'dev' or 'prod'"
    exit 1
fi

echo "Running video-analytics-api integration tests (mode=$MODE)"
"$SCRIPT_DIR/test.sh" "$MODE"
echo "✅ All integration tests completed"
