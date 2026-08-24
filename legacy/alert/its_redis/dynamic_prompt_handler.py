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

"""Thin wrapper around the project's Redis connection.

The legacy ``prompts:*`` CRUD has been removed in favour of
``handlers/alert_config/store.py:AlertConfigStore`` which is now the
single source of truth for alert prompts and VLM configuration. This
module remains as a small bootstrap helper that loads
``event_bridge.redis_source`` from ``config.yaml`` and exposes the
underlying ``redis.Redis`` client for everyone else to compose on top.

The class name ``DynamicPromptHandler`` is preserved as a backward
compatible alias so callers (and tests) can keep importing it during
the migration period. New code should depend on ``RedisClient``.
"""

import logging
import yaml

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry


class RedisClient:
    """Initialize and expose a project-wide Redis client.

    Anything else that needs Redis access should compose this client
    instead of inheriting prompt-handling behaviour.
    """

    def __init__(self, config_file: str = "config.yaml"):
        self.logger = logging.getLogger(self.__class__.__name__)

        with open(config_file, "r") as fh:
            config = yaml.safe_load(fh) or {}

        redis_config = config.get("event_bridge", {}).get("redis_source", {})
        self.logger.info(
            "Connecting to Redis using event_bridge.redis_source: %s", redis_config
        )

        # See ``its_redis/redis_handler.py`` for why these timeouts and
        # the empty ``Retry`` are required: ``redis-py`` defaults rely on
        # OS TCP retries and a backoff loop that turn a Redis blip into a
        # multi-second API hang on the alert-config CRUD hot path.
        self._redis_client = redis.Redis(
            host=redis_config["host"],
            port=redis_config["port"],
            db=redis_config.get("db", 0),
            decode_responses=True,
            socket_connect_timeout=redis_config.get("socket_connect_timeout", 2.0),
            socket_timeout=redis_config.get("socket_timeout", 2.0),
            health_check_interval=redis_config.get("health_check_interval", 30),
            retry=Retry(NoBackoff(), 0),
        )
        self.logger.info("RedisClient initialized successfully.")

    @property
    def redis_client(self) -> redis.Redis:
        """Expose the underlying ``redis.Redis`` instance."""
        return self._redis_client

    def health_check(self) -> bool:
        """Return ``True`` when the Redis backend responds to PING."""
        try:
            return bool(self._redis_client.ping())
        except Exception as exc:
            self.logger.error("Redis health check failed: %s", exc)
            return False


# Backward-compatible alias — to be removed once all callers stop
# importing the historical name. Module-level so existing
# ``from its_redis.dynamic_prompt_handler import DynamicPromptHandler``
# imports keep working.
DynamicPromptHandler = RedisClient
