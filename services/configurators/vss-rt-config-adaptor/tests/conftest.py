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
Shared pytest fixtures for vss-rt-config-adaptor unit tests.

Run tests from the repository root with: uv run pytest tests/ -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure root app/ is on path so we can import app and config
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(os.path.dirname(TESTS_DIR), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


@pytest.fixture
def fixtures_dir():
    """Path to tests/fixtures directory."""
    return os.path.join(TESTS_DIR, "fixtures")


@pytest.fixture
def sample_config_payload():
    """Minimal JSON payload for POST /config (event with metadata)."""
    return {
        "event": {
            "metadata": {
                "region": "r1",
                "group": "g1",
                "topic-prefix": "tp1",
            }
        }
    }


@pytest.fixture
def client(tmp_path):
    """Flask test client with app config pointed at tmp_path and os._exit mocked."""
    import app

    # Minimal source YAML so POST /config can read it
    source_yaml = tmp_path / "config.yaml"
    source_yaml.write_text("calib_file_path: /old\nbev_group_name: old\n")

    app.app.config["TESTING"] = True
    app.app.config["DS_CONFIG_PATH"] = str(tmp_path / "config.csv")
    app.app.config["DS_CONFIG_YAML_SOURCE_PATH"] = str(source_yaml)
    app.app.config["DS_CONFIG_YAML_TARGET_PATH"] = str(tmp_path / "target.yaml")
    app.app.config["CALIB_FILE_PATH"] = str(tmp_path / "calib.json")
    app.app.config["EVENT_OBJECT_FIELD"] = "event"
    app.app.config["METADATA_OBJECT_FIELD"] = "metadata"

    with patch.object(os, "_exit", MagicMock()):
        with app.app.test_client() as c:
            yield c
