######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

set -e

if [ "$#" -ne 2 ]; then
    echo "Error: Exactly two arguments required."
    echo "Usage: $(basename $0) <via-configs-dir> <out-dir>"
    exit 1
fi

CONFIGS_DIR="$1"
OUT_DIR="$2"

cp "$CONFIGS_DIR/runtime_stats.yaml" "$OUT_DIR/default_runtime_stats.yaml"
cp -r "$CONFIGS_DIR/guardrails" "$OUT_DIR/guardrails_config"
cp "$CONFIGS_DIR/riva_asr_grpc_conf.yaml" "$OUT_DIR/riva_asr_grpc_conf.yaml"
