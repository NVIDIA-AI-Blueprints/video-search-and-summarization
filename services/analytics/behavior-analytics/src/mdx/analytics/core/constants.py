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

import re
from datetime import datetime, timezone
from enum import Enum

# Unix epoch as a tz-aware datetime. Used by listeners as the initial value of
# ``last_insert_timestamp`` so the first notification (with any real Kafka
# record timestamp) is always strictly newer and therefore accepted.
NOTIFICATION_EPOCH_FLOOR: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Date patterns
DATE_PATTERN = re.compile(r"^__\d{1,2}_\d{1,2}_\d{4}_\d{1,2}_\d{1,2}_\d{1,2}(_\d{1,3})?_[A|P]M_UTC-\d{1,2}_\d{1,2}")

# Default coordinate values used when no specific coordinates are provided
DEFAULT_COORDINATE = {"x": 0, "y": 0}
# Default geographic location values used when no specific location is provided
DEFAULT_LOCATION = {"lng": 0, "lat": 0}
# Default camera distortion coefficients used for camera calibration
DEFAULT_DISTORTION_COEFFICIENTS = [[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]]

# Action type for updating all calibration data at once
CALIBRATION_ACTION_UPSERT_ALL = "upsert-all"
# Action type for updating specific calibration data
CALIBRATION_ACTION_UPSERT = "upsert"
# Action type for removing calibration data
CALIBRATION_ACTION_DELETE = "delete"
# Pattern used to identify calibration files in the file system
CALIBRATION_FILE_PATTERN = "-calibration-"
# Type identifier for buffer zone regions of interest (ROIs)
ROI_TYPE_BUFFER_ZONE = "buffer_zone"

# Type identifier for camera sensors
SENSOR_TYPE_CAMERA = "camera"
# Type identifier for sensor groups
SENSOR_TYPE_GROUP = "group"

# Directory path for storing calibration files
CALIBRATION_DIR = "/tmp/checkpoint/calibration"

# Number of nanoseconds in one millisecond
NANOS_PER_MILLISECOND = 1000000
# Number of nanoseconds in one second
NANOS_PER_SECOND = 1000000000

# Calibration files older than this are pruned by the listener's pruner thread.
# Generous retention -- workers should never be more than minutes behind.
CALIBRATION_RETAIN_SECONDS = 3600
# How often the pruner thread runs.
CALIBRATION_PRUNE_INTERVAL_SECONDS = 900

# Directory path for storing dynamic-config files written by the main-process
# ConfigListener and consumed by per-worker ConfigFileMonitor watchdogs.
CONFIG_DIR = "/tmp/checkpoint/config"
# Config files older than this are pruned. Same generous retention as
# calibration to keep the pruner from racing slow workers.
CONFIG_RETAIN_SECONDS = 3600
CONFIG_PRUNE_INTERVAL_SECONDS = 900

# Milvus Retry Wait Time
MILVUS_WAIT_IN_SECONDS = [5, 10, 20, 40, 80, 160]
MILVUS_LOADING_TIMEOUT = 300

# Milvus collection
RETAIL_REFERENCE = "retail_reference"
BEHAVIOR = "behavior"

# Configurations for Milvus collections, collectionName, stringFieldMaxLength, and vectorDim are to be set in the config
MILVUS_COLLECTION_MAP = {
    RETAIL_REFERENCE: {
        "collectionName": "collectionName",
        "description": "Retail reference data",
        "fields": {
            "pk": {"dtype": "INT64", "is_primary": True},
            "classId": {"dtype": "INT64"},
            "className": {"dtype": "VARCHAR", "max_length": "stringFieldMaxLength"},
            "embeddings": {"dtype": "FLOAT_VECTOR", "dim": "vectorDim", "is_vector_index": True},
            "description": {"dtype": "VARCHAR", "max_length": "stringFieldMaxLength"},
        },
    },
    BEHAVIOR: {
        "collectionName": "collectionName",
        "description": "Behavior data",
        "fields": {
            "pk": {"dtype": "INT64", "is_primary": True},
            "behaviorId": {"dtype": "VARCHAR", "max_length": "stringFieldMaxLength", "is_vector_index": False},
            "sensorId": {"dtype": "VARCHAR", "max_length": "stringFieldMaxLength", "is_vector_index": False},
            "objectId": {"dtype": "VARCHAR", "max_length": "stringFieldMaxLength", "is_vector_index": False},
            "timestamp": {"dtype": "INT64", "is_vector_index": False},
            "end": {"dtype": "INT64", "is_vector_index": False},
            "place": {"dtype": "VARCHAR", "max_length": "stringFieldMaxLength", "is_vector_index": False},
            "embeddings": {"dtype": "FLOAT_VECTOR", "dim": "vectorDim", "is_vector_index": True},
        },
    },
}


class TripwireDirection(Enum):
    """Enum representing the direction of movement through a tripwire."""

    IN = "IN"
    OUT = "OUT"


class ROIDirection(Enum):
    """Enum representing the type of tripwire event."""

    IN = "ENTRY"
    OUT = "EXIT"
