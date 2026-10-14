#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
export VSS_PERF_STAGE=post_deploy
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
if [[ "${VSS_PERF_BOUNDED_STAGE:-}" != "$VSS_PERF_STAGE" ]]; then
    export VSS_PERF_BOUNDED_STAGE="$VSS_PERF_STAGE"
    stage_status=0
    "$PYTHON" "$VSS_PERF_ROOT/scripts/run_stage.py" "$PERF_STAGE_TIMEOUT_SECONDS" "${BASH_SOURCE[0]}" '' || stage_status=$?
    exit "$stage_status"
fi
vss configure --base-url "$VSS_PUBLIC_URL"
vss configure check
vss vios list --type stream --sensor compose-base-perf > "$VSS_DATA_DIR/sensor-list.json"
count="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["count"])' "$VSS_DATA_DIR/sensor-list.json")"
if [[ "$count" == 0 ]]; then
    vss vios add --type stream "$VSS_PERF_STREAM_URL" --name compose-base-perf > "$VSS_DATA_DIR/sensor.json"
elif [[ "$count" == 1 ]]; then
    "$PYTHON" -c 'import json,sys; data=json.load(open(sys.argv[1]))["sensors"][0]; json.dump({"sensor_id":data["sensor_id"],"name":data["name"]},open(sys.argv[2],"w"))' "$VSS_DATA_DIR/sensor-list.json" "$VSS_DATA_DIR/sensor.json"
else
    echo 'Expected exactly one existing compose-base-perf sensor' >&2
    exit 1
fi
# Use the identifier returned by VIOS; never manufacture one from the name.
"$PYTHON" "$VSS_PERF_ROOT/scripts/prepare_runtime.py" --sensor-json "$VSS_DATA_DIR/sensor.json"
"${COMPOSE_CMD[@]}" up -d rtvi-cv
"$PYTHON" "$VSS_PERF_ROOT/scripts/wait_inference.py"
"$PYTHON" "$VSS_PERF_ROOT/scripts/capture_runtime.py"
vss configure --base-url "$VSS_PUBLIC_URL"
vss configure check
