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

"""Single source of truth for loading the service YAML config.

Several components historically re-implemented ``yaml.safe_load(open(...))``
with slightly different CONFIG_PATH / error handling. This module
centralizes that so the behavior lives in one place.
"""

import logging
import os
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "config.yaml"


def resolve_config_path(config_file: Optional[str] = None) -> str:
    """Active config path; the ``CONFIG_PATH`` env var wins when set."""
    return os.getenv("CONFIG_PATH", config_file or DEFAULT_CONFIG_FILE)


def load_config(
    config_file: Optional[str] = None,
    *,
    default_on_missing: bool = False,
) -> Dict[str, Any]:
    """Load a YAML config file.

    - The ``CONFIG_PATH`` env var always wins; ``config_file`` (or
      ``config.yaml``) is only the fallback when ``CONFIG_PATH`` is unset.
      This keeps every caller consistent — a literal ``"config.yaml"`` passed
      by ``setup_logging`` / ``AlertSubmissionService`` no longer bypasses the
      ``CONFIG_PATH`` override.
    - ``default_on_missing=True`` returns ``{}`` instead of raising when the
      file is absent.
    """
    path = resolve_config_path(config_file)
    try:
        with open(path, "r") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        if default_on_missing:
            logger.warning("Config file %s not found; using defaults", path)
            return {}
        raise
