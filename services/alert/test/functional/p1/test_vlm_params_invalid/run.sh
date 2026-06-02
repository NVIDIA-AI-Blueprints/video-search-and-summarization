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

# Test: VLM Params Invalid Config
# Description: A typo or invalid field in vlm_params should cause a startup
#              validation error (Pydantic rejects unknown/mistyped fields).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P1_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$P1_ROOT/../../.." && pwd)"
source "$P1_ROOT/shared/helpers.sh"

PID_DIR="${PID_DIR:-/tmp/alert_agent_p1_functional}"
TEST_NAME="vlm_params_invalid"

echo "=== P1: VLM Params Invalid Config ==="

mkdir -p "$PID_DIR"

# 1. Create a bad alert_type_config.json with typo in vlm_params
BAD_CONFIG="$PID_DIR/bad_alert_type_config.json"
cat > "$BAD_CONFIG" << 'JSONEOF'
{
  "version": "1.1",
  "alerts": [
    {
      "alert_type": "collision",
      "prompts": {
        "system": "You are a helpful assistant.",
        "user": "Analyze the scene."
      },
      "vlm_params": {
        "num_framez": 10,
        "max_tokenz": 2048
      }
    }
  ]
}
JSONEOF

# 2. Try to load the config with AlertTypeConfigLoader
LOAD_RESULT=$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigLoader

try:
    loader = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
    import json
    with open('$BAD_CONFIG') as f:
        config_data = json.load(f)
    from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigFile
    parsed = AlertTypeConfigFile.model_validate(config_data)
    # If we get here, check if typo fields were silently ignored
    cfg = parsed.alerts[0]
    if cfg.vlm_params is None:
        print('REJECTED_NO_PARAMS')
    else:
        dumped = cfg.vlm_params.model_dump(exclude_none=True)
        if dumped:
            print('ACCEPTED_WITH_DATA')
        else:
            print('ACCEPTED_EMPTY')
except Exception as e:
    print(f'ERROR:{e}')
" 2>&1)

echo "Load result: $LOAD_RESULT"

case "$LOAD_RESULT" in
    ERROR:*)
        print_status "ok" "PASS: Invalid vlm_params rejected at load time: ${LOAD_RESULT#ERROR:}"
        exit 0
        ;;
    ACCEPTED_EMPTY|REJECTED_NO_PARAMS|ACCEPTED_WITH_DATA)
        print_status "fail" "FAIL: Typo fields were not rejected (got: $LOAD_RESULT) — VlmParams must use extra='forbid'"
        exit 1
        ;;
    *)
        print_status "fail" "FAIL: Unexpected result: $LOAD_RESULT"
        exit 1
        ;;
esac
