#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
vss_qa_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
vss_ext_dir="$(cd "$vss_qa_dir/../.." && pwd -P)"
docker build -f "$vss_ext_dir/Dockerfile.runtime" -t "${VSS_FILLING_API_IMAGE:-vss-filling-analysis:qa}" "$vss_ext_dir"
docker build -f "$vss_ext_dir/segmentation/Dockerfile" -t "${VSS_FILLING_SEGMENTATION_IMAGE:-vss-filling-segmentation:qa}" "$vss_ext_dir"
bash "$vss_ext_dir/streaming/build.sh"
VSS_FILLING_UI_IMAGE="${VSS_FILLING_UI_IMAGE:-vss-ui-filling:qa}" bash "$vss_ext_dir/native-ui/build.sh"
bash "$vss_qa_dir/patches/rt-vlm-overlap/build.sh"
