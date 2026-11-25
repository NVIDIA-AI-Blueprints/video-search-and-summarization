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
JSON File-based state manager for persistent project and calibration state.

This module provides functions to save/load state from a JSON file
"""

import json
import os
from pathlib import Path
from typing import Dict, Any
import logging
import tempfile

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class StateManager:
    """Manages persistent state using a JSON file."""

    def __init__(self, state_file: Path = Path("projects/state.json")):
        self.state_file = state_file

    def save_state(self, state: Dict[str, Any]) -> bool:
        try:
            serialized_state = json.dumps(state, indent=4)
        except Exception as e:
            logger.error(f"\tFailed to serialize state: {e}")
            return False
        return self.save_serialized_state(serialized_state)

    def save_serialized_state(self, serialized_state: str) -> bool:
        """Atomically replace state file with already-serialized JSON."""
        temp_path = None
        try:
            state_path = Path(self.state_file)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_mode = state_path.stat().st_mode & 0o777 if state_path.exists() else 0o644
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=state_path.parent,
                prefix=f".{state_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = Path(f.name)
                os.chmod(temp_path, state_mode)
                f.write(serialized_state)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, state_path)
            try:
                directory_fd = os.open(state_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as e:
                logger.warning("State file replaced but directory fsync failed: %s", e)
            return True
        except Exception as e:
            logger.error(f"\tFailed to save state: {e}")
            return False
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def load_state(self) -> Dict[str, Any]:
        try:
            with open(self.state_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"\tFailed to load state, returning empty state: {e}")
            return {
                "projects": {},
                "running_calibration": False
            }
