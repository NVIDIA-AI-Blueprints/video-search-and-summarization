#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

command -v docker >/dev/null
docker info >/dev/null
command -v python3 >/dev/null
command -v nvidia-smi >/dev/null
nvidia-smi >/dev/null
test -f "$PERF_DIR/configs/kpis.yaml"
test -f "$WAREHOUSE_DIR/compose.yml"
test -f "$WAREHOUSE_DIR/overrides.env"
test -f "$VSS_APPS_DIR/compose.yml"
test -f "$VSS_APPS_DIR/containers.env"
ensure_host_dir "$OUTPUT_DIR"
echo "Warehouse industry-example pre-flight passed."
echo "  VSS_APPS_DIR=$VSS_APPS_DIR"
echo "  VSS_DATA_DIR=$VSS_DATA_DIR"
echo "  COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME"
