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
from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

load_dotenv: Callable[..., Any] | None = None
with contextlib.suppress(Exception):
    from dotenv import load_dotenv


def _load_env_file(env_path: Path) -> None:
    """Attempt to load environment variables from ``env_path`` if available."""
    if load_dotenv is None:
        logger.warning("python-dotenv not installed; skipping env file load for %s", env_path)
        return

    if env_path.is_file():
        load_dotenv(env_path, override=False)
        logger.info("Loaded environment variables from %s", env_path)
    else:
        logger.warning("Env file %s not found; skipping", env_path)


def _auto_load_env_files() -> None:
    project_root = Path(__file__).resolve().parent.parent

    env_pointer = project_root / ".env_file"
    if env_pointer.is_file():
        try:
            target_path = env_pointer.read_text().strip()
            if target_path:
                env_path = Path(target_path).expanduser()
                if not env_path.is_absolute():
                    env_path = project_root / env_path

                if env_path.is_file():
                    logger.info("Loading environment variables from %s", env_path)
                    _load_env_file(env_path)
                else:
                    logger.warning("Env file %s not found; skipping", env_path)
            else:
                logger.warning(".env_file at %s is empty", env_pointer)
        except Exception:
            logger.exception("Error reading %s", env_pointer)
    else:
        logger.info(".env_file not found at %s", env_pointer)


try:
    _auto_load_env_files()
except Exception:
    logger.exception("Unhandled error during env auto-load")
