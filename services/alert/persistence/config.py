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

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PersistenceConfig:
    """Configuration for the persistence layer."""
    enabled: bool = True
    backend: str = "elasticsearch"
    index_prefix: str = "ab-"
    # ES-specific overrides; when empty, falls back to the top-level elastic config.
    elasticsearch_hosts: Tuple[str, ...] = tuple()
    # When True (default, convenient for dev/test), ``ElasticPersistenceStore``
    # creates indices on first write. Production deployments should set this
    # to False and pre-declare indices via ES bootstrap templates so shard /
    # replica counts stay under ops control.
    auto_create_indices: bool = True
    index_shards: int = 1
    index_replicas: int = 0

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any], app_config: Optional[Dict[str, Any]] = None) -> "PersistenceConfig":
        """Build from the ``persistence:`` section of config.yaml.

        If ``persistence.elasticsearch.hosts`` is not set, inherits from
        the top-level ``elastic.hosts``.
        """
        hosts = tuple(cfg.get("elasticsearch", {}).get("hosts", []))
        if not hosts and app_config:
            hosts = tuple(app_config.get("elastic", {}).get("hosts", []))

        index_settings = cfg.get("index_settings", {}) or {}

        return cls(
            enabled=cfg.get("enabled", True),
            backend=cfg.get("backend", "elasticsearch"),
            index_prefix=cfg.get("index_prefix", "ab-"),
            elasticsearch_hosts=hosts,
            auto_create_indices=bool(cfg.get("auto_create_indices", True)),
            index_shards=int(index_settings.get("shards", 1)),
            index_replicas=int(index_settings.get("replicas", 0)),
        )
