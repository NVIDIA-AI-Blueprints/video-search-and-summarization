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

import os
import yaml
from its_redis.redis_handler import RedisHandler
from functools import lru_cache

def load_config():
    # Use the CONFIG_PATH environment variable, default to "config.yaml"
    config_file = os.getenv("CONFIG_PATH", "config.yaml")
    with open(config_file, 'r') as file:
        return yaml.safe_load(file)

def load_config_path():
    # Use the CONFIG_PATH environment variable, default to "config.yaml"
    return os.getenv("CONFIG_PATH", "config.yaml")

@lru_cache()
def get_redis_handler() -> RedisHandler:
    """Get or create RedisHandler instance."""
    config_path = load_config_path()  # Get the path to the configuration file
    return RedisHandler(config_path)  # Pass the config file path to RedisHandler 