#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

command -v docker >/dev/null
docker info >/dev/null
resolve_image >/dev/null
test -f "$INTEGRATION_DIR/docker_compose/apps_data/playback/warehouse_2d_playback_data.txt"
mkdir -p "$OUTPUT_DIR"
chmod 0777 "$OUTPUT_DIR"
echo "Behavior Analytics playback pre-flight passed."
