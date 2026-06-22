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

from clients.redis_handler import RedisHandler
from functools import lru_cache

from utils.config import load_config, resolve_config_path  # noqa: F401  (load_config re-exported for callers)


def load_config_path():
    """Active config path (CONFIG_PATH env, default config.yaml)."""
    return resolve_config_path()

@lru_cache()
def get_redis_handler() -> RedisHandler:
    """Get or create RedisHandler instance."""
    config_path = load_config_path()  # Get the path to the configuration file
    return RedisHandler(config_path)  # Pass the config file path to RedisHandler 