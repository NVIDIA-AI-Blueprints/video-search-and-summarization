######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
"""Shared utilities for perf benchmark reporting tools."""

import json
import math
from pathlib import Path
from typing import Dict, Optional


def calc_vision_tokens(w: int, h: int, num_frames: int, model_preset: str = "") -> int:
    """Estimate merged vision tokens using the configured model's temporal patching."""
    spatial = math.ceil(w / 32) * math.ceil(h / 32)
    return spatial * (
        num_frames if model_preset == "cosmos3-edge-bf16" else num_frames // 2
    )


def load_json(path: Path, verbose: bool = True) -> Optional[Dict]:
    """Load a JSON file, returning None on error.

    Args:
        path: Path to the JSON file.
        verbose: If True, print a warning on error.

    Returns:
        Parsed dict or None if the file is missing or malformed.
    """
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        if verbose:
            print(f"  Warning: {e}")
        return None
