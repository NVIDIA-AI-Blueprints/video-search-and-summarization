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
Shared pytest fixtures for VSS Configurator unit tests.

Run tests from the repository root with: uv run pytest tests/ -v
"""
import os
import sys
import json
from unittest.mock import MagicMock
import pytest

# Ensure app/ is on path so we can import utils, profile_configurator, sensor_config_manager
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(REPO_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
# Profile config manager uses "from profile_configurator_utils import utils" which resolves
# when profile_configurator directory is on path
PROFILE_CONFIGURATOR_DIR = os.path.join(APP_DIR, "profile_configurator")
if PROFILE_CONFIGURATOR_DIR not in sys.path:
    sys.path.insert(0, PROFILE_CONFIGURATOR_DIR)

# Mock kafka, redis, and spatialai_data_utils so sensor_config_manager can be imported
# without real brokers or optional BEV dependency
for _mod in ("kafka", "redis"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
if "kafka" in sys.modules:
    sys.modules["kafka"].KafkaProducer = MagicMock()

# spatialai_data_utils is used by utils.recompute_bev_centers (optional dependency)
_spatialai = MagicMock()
sys.modules["spatialai_data_utils"] = _spatialai
sys.modules["spatialai_data_utils.core"] = MagicMock()
sys.modules["spatialai_data_utils.core.cameras"] = MagicMock()
sys.modules["spatialai_data_utils.core.cameras.bev"] = MagicMock()
sys.modules["spatialai_data_utils.core.cameras.bev"].calculate_group_origins_from_calibration = MagicMock(return_value="/tmp/calibration.json")

from utils.sensor_mapping import SensorMapping, Sensor


@pytest.fixture
def fixtures_dir():
    """Path to tests/fixtures directory."""
    return os.path.join(TESTS_DIR, "fixtures")


@pytest.fixture
def sample_msb_output(fixtures_dir):
    """Load sample MSB response (groups with rtspURLs)."""
    path = os.path.join(fixtures_dir, "msb_output_sample.json")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def sample_calibration(fixtures_dir):
    """Load sample calibration payload."""
    path = os.path.join(fixtures_dir, "calibration_sample.json")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def sample_nvstreamer_streams(fixtures_dir):
    """Load sample VST-style stream list."""
    path = os.path.join(fixtures_dir, "nvstreamer_streams_sample.json")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def mock_logger():
    """Minimal logger mock with debug/info/warning/error that do nothing."""
    class Logger:
        def debug(self, msg, *args, **kwargs): pass
        def info(self, msg, *args, **kwargs): pass
        def warning(self, msg, *args, **kwargs): pass
        def error(self, msg, *args, **kwargs): pass
        def exception(self, msg, *args, **kwargs): pass
    return Logger()


@pytest.fixture
def sample_sensor_mapping(mock_logger):
    """SensorMapping with a few sensors for API tests."""
    mapping = SensorMapping()
    mapping.sensors["cam-1"] = Sensor(
        name="cam-1",
        url="rtsp://host/1",
        group_id="g1",
        region="r1",
    )
    mapping.sensors["cam-2"] = Sensor(
        name="cam-2",
        url="rtsp://host/2",
        group_id="g1",
        region="r2",
    )
    return mapping


@pytest.fixture
def minimal_config():
    """Minimal CONFIG dict for endpoints and factory tests."""
    return {
        "ENABLE_CALIBRATION_PROCESS": True,
        "CALIBRATION_MODE": "upload",
        "CALIBRATION_FILE_PATH": "/tmp/calibration.json",
        "CALIBRATION_FILE_NAME": "calibration.json",
        "CALIBRATION_API_ENDPOINT": "http://example.com/cal",
        "SENSOR_MAPPING_FILE_PATH": "/tmp/sensor_mapping.json",
        "WDM_KFK_BOOTSTRAP_URL": "kafka:9092",
        "WDM_WL_ID_FIELD": "camera_id",
        "WDM_WL_EVENT_FIELD": "event",
        "WDM_REDIS_HOST": "localhost",
        "WDM_REDIS_PORT": 6379,
        "REDIS_DB": 0,
        "WDM_REDIS_STREAM_NAME": "sensor",
        "WDM_REDIS_MSG_KEY": "sensor.id",
        "MESSAGE_BROKER_TYPE": "kafka",
        "WDM_KFK_TOPIC": "sensor.config",
        "WDM_KFK_MSG_KEY": "sensor",
    }
