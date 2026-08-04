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

import json
import os
import pathlib


class Config(object):
    PORT = os.environ["PORT"] if "PORT" in os.environ else 9002

    DS_CONFIG_PATH = (
        os.environ["DS_CONFIG_PATH"]
        if "DS_CONFIG_PATH" in os.environ
        and os.environ["DS_CONFIG_PATH"].strip() != ""
        else "/tmp/data/vss-rt-config-adaptor/config.csv"
    )

    DS_CONFIG_YAML_SOURCE_PATH = (
        os.environ["DS_CONFIG_YAML_SOURCE_PATH"]
        if "DS_CONFIG_YAML_SOURCE_PATH" in os.environ
        and os.environ["DS_CONFIG_YAML_SOURCE_PATH"].strip() != ""
        else "/ds-config/config.yaml"
    )

    DS_CONFIG_YAML_TARGET_PATH = (
        os.environ["DS_CONFIG_YAML_TARGET_PATH"]
        if "DS_CONFIG_YAML_TARGET_PATH" in os.environ
        and os.environ["DS_CONFIG_YAML_TARGET_PATH"].strip() != ""
        else "/tmp/data/vss-rt-config-adaptor/config.yaml"
    )

    CALIB_FILE_PATH = (
        os.environ["CALIB_FILE_PATH"]
        if "CALIB_FILE_PATH" in os.environ
        and os.environ["CALIB_FILE_PATH"].strip() != ""
        else "/tmp/data/vss-rt-config-adaptor/calibration_grouped.json"
    )

    EVENT_OBJECT_FIELD = (
        os.environ["EVENT_OBJECT_FIELD"]
        if "EVENT_OBJECT_FIELD" in os.environ
        and os.environ["EVENT_OBJECT_FIELD"].strip() != ""
        else "event"
    )

    METADATA_OBJECT_FIELD = (
        os.environ["METADATA_OBJECT_FIELD"]
        if "METADATA_OBJECT_FIELD" in os.environ
        and os.environ["METADATA_OBJECT_FIELD"].strip() != ""
        else "metadata"
    )

