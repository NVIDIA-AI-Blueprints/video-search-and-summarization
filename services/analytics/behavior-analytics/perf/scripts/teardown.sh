#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

docker rm -f "$MONITOR_NAME" >/dev/null 2>&1 || true

if [[ -f "$INTEGRATION_DIR/docker_compose/infra/.env" ]]; then
    load_compose_env || true
    cd "$SERVICE_DIR/docker_compose"
    compose down --remove-orphans || true
fi
