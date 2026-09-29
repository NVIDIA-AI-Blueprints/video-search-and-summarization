#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
vss_ui_build_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
vss_ui_repo="$(cd "$vss_ui_build_dir/../../.." && pwd -P)"
vss_ui_base="${VSS_UI_BASE_IMAGE:-ghcr.io/nvidia-ai-blueprints/vss/vss-agent-ui@sha256:9f097898c769dd82ceacacd328566322f25417688c7ed009e0ea8cde370ea656}"
vss_ui_expected_revision=1f1a0b904df824fbcdcd81287547881de47d4c31
vss_ui_runtime_image="${VSS_FILLING_UI_IMAGE:-vss-ui-filling:20260922}"
vss_ui_builder_image="${VSS_FILLING_UI_BUILDER_IMAGE:-vss-ui-filling-builder:20260922}"
git -C "$vss_ui_repo" merge-base --is-ancestor "$vss_ui_expected_revision" HEAD
docker pull "$vss_ui_base"
# Install from the nightly lockfile and build its source with the additive custom tab.
docker build --target builder --build-arg "BASE_UI_IMAGE=$vss_ui_base" -f "$vss_ui_build_dir/Dockerfile"   -t "$vss_ui_builder_image" "$vss_ui_repo/services/ui"
docker build --target runtime --build-arg "BASE_UI_IMAGE=$vss_ui_base" -f "$vss_ui_build_dir/Dockerfile"   -t "$vss_ui_runtime_image" "$vss_ui_repo/services/ui"
docker image inspect "$vss_ui_runtime_image" --format '{{.Id}}'
