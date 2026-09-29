#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
vss_live_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
docker build -f "$vss_live_dir/Dockerfile" -t "${VSS_LIVE_IMAGE:-vss-filling-live:qa}" "$vss_live_dir/.."
