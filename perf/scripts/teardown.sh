#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

docker rm -f "$MONITOR_NAME" >/dev/null 2>&1 || true
down_project || true
if ! verify_project_down; then
    down_project_by_name || true
    verify_project_down || true
fi
if [[ -f "$GENERATED_ENV" && ! -L "$GENERATED_ENV" ]]; then
    chmod 600 "$GENERATED_ENV" 2>/dev/null || true
    rm -f "$GENERATED_ENV"
fi
# Leave the extracted warehouse app data / model cache; only drop runtime logs
# that block Jenkins GPU deleteDir (uid-1000 Elasticsearch files).
# Do not run eval host-global cleanup.py or cleanup_all_datalog.sh (calibration
# / VST wipe and repo backup revert on a shared Jenkins node).
if [[ -d "$VSS_DATA_DIR/data_log" ]]; then
    remove_root_tree "$VSS_DATA_DIR/data_log"
fi
