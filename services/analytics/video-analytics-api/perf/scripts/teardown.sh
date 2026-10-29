#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [[ -f "$ENV_FILE" ]]; then
    load_compose_env || true
    cleanup_docker_environment || true
fi
docker rm -f "$APP_CONTAINER" "$ES_CONTAINER" "$ES_INIT_CONTAINER" >/dev/null 2>&1 || true
remove_root_tree "$VA_API_PERF_DATA_DIR"
