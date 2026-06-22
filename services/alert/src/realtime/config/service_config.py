#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Shared configuration helpers for Alert Bridge services.
"""

import logging
import os
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)


def load_config(config_file: str = "config.yaml") -> Dict[str, Any]:
    """Load configuration from YAML file."""
    config_path = os.getenv("CONFIG_PATH", config_file)
    try:
        with open(config_path, "r") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("Config file %s not found; using defaults", config_path)
        return {}
