#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
    analytics_state="$(docker inspect -f '{{.State.Status}}' mdx-analytics 2>/dev/null || true)"
    if [[ "$analytics_state" == "running" ]]; then
        echo "Behavior Analytics is running."
        exit 0
    fi
    sleep 5
done

echo "Timed out waiting for mdx-analytics." >&2
docker ps -a >&2 || true
exit 1
