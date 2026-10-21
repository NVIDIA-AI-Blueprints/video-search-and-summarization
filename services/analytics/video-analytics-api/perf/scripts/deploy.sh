#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

write_compose_env
load_compose_env
docker pull "$VIDEO_ANALYTICS_API_IMAGE"

compose up -d --build --force-recreate elasticsearch elasticsearch-init-container video-analytics-api
