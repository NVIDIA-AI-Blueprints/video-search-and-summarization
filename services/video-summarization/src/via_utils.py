# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""GPU-independent utility functions extracted from utils.py.

This module contains utilities needed by via_server.py and via_stream_handler.py
that have zero dependency on GStreamer, CUDA, or any GPU libraries.
"""

import json
import logging
import os
import re

import yaml


def round_up(s):
    """
    Rounds up a string representation of a number to an integer.

    Example:
    >>> round_up("7.9s")
    8
    """
    # Strip any non-numeric characters from the string
    num_str = re.sub(r"[a-zA-Z]+", "", s)

    # Convert the string to a float and round up to the nearest integer
    num = float(num_str)
    return -(-num // 1)  # equivalent to math.ceil(num) in Python 3.x


def get_avg_time_per_chunk(GPU_in_use, Model_ID, yaml_file_path):
    """
    Returns the average time per query for a given GPU and Model ID
    from a VIA_runtime_stats YAML file.

    Args:
        GPU_in_use (str): The GPU in use (e.g. A100, H100)
        Model_ID (str): The Model ID (e.g. VILA)
        yaml_file_path (str): The path to the VIA_runtime_stats YAML file

    Returns:
        str: The average time per chunk (e.g. 2.5s, 1.8s)
    """

    def is_subset_s1_in_s2(string1, string2):
        # Returns True if string1 is a subset of string2, ignoring case
        pattern = re.compile(re.escape(string1), re.IGNORECASE)
        return bool(pattern.search(string2))

    def is_subset(string1, string2):
        return is_subset_s1_in_s2(string1, string2) or is_subset_s1_in_s2(string2, string1)

    with open(yaml_file_path, "r") as f:
        yaml_data = yaml.safe_load(f)

    max_atpc = 0.0
    max_atpc_as_is = "0"

    for entry in yaml_data["VIA_runtime_stats"]:
        if round_up(entry["average_time_per_chunk"]) > max_atpc:
            max_atpc = round_up(entry["average_time_per_chunk"])
            max_atpc_as_is = entry["average_time_per_chunk"]
        if is_subset(GPU_in_use, entry["GPU_in_use"]) and is_subset(Model_ID, entry["Model_ID"]):
            return entry["average_time_per_chunk"]

    # If no matching entry is found, return max of all
    return max_atpc_as_is


class StreamSettingsCache:
    def __init__(
        self,
        stream_settings_fp: str = "/tmp/.stream_settings_cache.json",
        logger: logging.Logger = None,
    ):
        self.stream_settings_fp = stream_settings_fp
        self.logger = logger

    def update_stream_settings(self, video_id: str, stream_settings: dict):
        """
        Save/Update stream settings to a json file
        """
        try:
            existing_settings = self.load_stream_settings()
            # Update with new settings
            existing_settings.update({video_id: stream_settings})

            # Save updated settings
            with open(self.stream_settings_fp, "w") as f:
                json.dump(existing_settings, f, indent=4)
            self.logger.debug(f"Stream settings updated: {self.stream_settings_fp}")
        except Exception as e:
            self.logger.error(f"Failed to save stream settings: {str(e)}")

    def load_stream_settings(self, video_id: str = None):
        """
        Load stream settings from a json file
        """
        if os.path.exists(self.stream_settings_fp):
            with open(self.stream_settings_fp, "r") as f:
                existing_settings = json.load(f)
        else:
            existing_settings = {}

        if video_id:
            existing_stream_settings = existing_settings.get(video_id, {})
            self.logger.debug(f"Stream settings for {video_id}: {existing_stream_settings}")
            return existing_stream_settings
        else:
            self.logger.debug(f"ALL Streams settings: {existing_settings}")
            return existing_settings

    def transform_query(self, query_dict: dict) -> dict:
        """
        Transform the query string into a dictionary
        """
        if not query_dict:
            self.logger.error("Empty Query params!")
            return {}

        # Define the fields we want to keep
        required_fields = {
            "id",
            "model",
            "chunk_duration",
            "temperature",
            "seed",
            "max_tokens",
            "top_p",
            "top_k",
            "stream",
            "stream_options",
            "vlm_input_width",
            "vlm_input_height",
            "enable_audio",
            "prompt",
            "enable_vlm_structured_output",
            "objects_of_interest",
            "summarize",
            "camera_id",
            "schema",
            "batch_response_method",
            "scenario",
            "events",
            "auto_generate_prompt",
            "time_metadata_keys",
        }

        # Extract only the required fields and remove None values
        filtered_dict = {
            k: v for k, v in query_dict.items() if k in required_fields and v is not None
        }

        return filtered_dict
