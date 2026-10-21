#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/scripts/common.sh"

cleanup() {
    "$PERF_DIR/scripts/teardown.sh" || true
}
trap cleanup EXIT

"$PERF_DIR/scripts/pre_flight.sh"
"$PERF_DIR/scripts/deploy.sh"
"$PERF_DIR/scripts/post_deploy.sh"
"$PERF_DIR/scripts/measure.sh"
