#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

docker rm -f "$MONITOR_NAME" >/dev/null 2>&1 || true
if [[ -f "$GENERATED_ENV" ]]; then
    compose down -v --remove-orphans || true
fi
# Leave the extracted warehouse app data / model cache; only drop runtime logs
# that block Jenkins GPU deleteDir (uid-1000 Elasticsearch files).
if [[ -d "$VSS_DATA_DIR/data_log" ]]; then
    remove_root_tree "$VSS_DATA_DIR/data_log"
fi
