#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

docker rm -f "$MONITOR_NAME" >/dev/null 2>&1 || true

if [[ -f "$INTEGRATION_DIR/docker_compose/infra/.env" ]]; then
    load_compose_env || true
    cd "$COMPOSE_DIR"
    compose down --remove-orphans -v || true
fi

remove_root_tree "$BA_PERF_DATA_DIR"
# Older runs wrote kafka/ES into the git checkout. Jenkins deleteDir on the
# next GPU job cannot remove those uid-1000 files (Jenkins #62).
remove_root_tree "$CHECKOUT_DATA_LOG"
