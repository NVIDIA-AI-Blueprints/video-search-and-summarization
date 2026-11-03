#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

ensure_host_dir "$WH_PERF_DATA_DIR"
ensure_host_dir "$VSS_DATA_DIR"
download_warehouse_app_data
prepare_data_logs
write_generated_env
load_generated_env

echo "Starting warehouse 2D Kafka industry example (project=$COMPOSE_PROJECT_NAME)"
if ! compose up -d --pull always --force-recreate --build; then
    echo "Warehouse compose up failed" >&2
    compose ps -a >&2 || true
    exit 1
fi
compose ps -a || true
