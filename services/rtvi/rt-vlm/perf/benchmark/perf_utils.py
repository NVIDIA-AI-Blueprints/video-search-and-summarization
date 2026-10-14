######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
