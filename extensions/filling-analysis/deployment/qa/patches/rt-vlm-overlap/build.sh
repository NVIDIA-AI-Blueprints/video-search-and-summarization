#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
vss_patch_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
vss_repo_dir="$(cd "$vss_patch_dir/../../../../../.." && pwd -P)"
vss_patch_tmp="$(mktemp -d)"
trap 'rm -rf "$vss_patch_tmp"' EXIT
mkdir -p "$vss_patch_tmp/services/rtvi/rt-vlm/src/"{vlm_pipeline,server}
cp "$vss_repo_dir/services/rtvi/rt-vlm/src/vlm_pipeline/vlm_pipeline.py" "$vss_patch_tmp/services/rtvi/rt-vlm/src/vlm_pipeline/"
cp "$vss_repo_dir/services/rtvi/rt-vlm/src/server/rtvi_stream_handler.py" "$vss_patch_tmp/services/rtvi/rt-vlm/src/server/"
cp "$vss_patch_dir/test_overlap.py" "$vss_patch_dir/Dockerfile" "$vss_patch_tmp/"
git -C "$vss_patch_tmp" apply --check "$vss_patch_dir/overlap.patch"
git -C "$vss_patch_tmp" apply "$vss_patch_dir/overlap.patch"
python3 "$vss_patch_tmp/test_overlap.py"
if [[ "${1:-}" == "--check" ]]; then exit 0; fi
docker build -f "$vss_patch_tmp/Dockerfile" -t "${VSS_FILLING_RTVLM_IMAGE:-vss-livestream/rt-vlm:qa}" "$vss_patch_tmp"
