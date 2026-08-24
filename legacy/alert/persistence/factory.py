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
from typing import Any, Dict, Optional

from elastic.elastic import ElasticClient, ElasticConfig
from persistence.config import PersistenceConfig
from persistence.elastic_store import ElasticPersistenceStore
from persistence.base import PersistenceStore
from persistence.exceptions import PersistenceConfigError

logger = logging.getLogger(__name__)


def create_persistence_store(
    app_config: Dict[str, Any],
    es_client: Optional[ElasticClient] = None,
) -> Optional[PersistenceStore]:
    """Build a PersistenceStore from application config.

    Returns ``None`` only when persistence is explicitly disabled
    (``persistence.enabled: false``). Any other failure path — unsupported
    backend, missing hosts, client construction error — raises
    ``PersistenceConfigError`` so the caller sees a loud startup failure
    instead of a silently degraded service that looks healthy but will
    never persist writes.

    Args:
        app_config: Full application config dict (parsed config.yaml).
        es_client: Optional pre-existing ElasticClient to reuse.
                   When None, a new client is created from config.

    Returns:
        A PersistenceStore instance, or ``None`` when disabled in config.

    Raises:
        PersistenceConfigError: Persistence is enabled but cannot be
            initialised (bad backend / missing hosts / ES client init fail).
    """
    persistence_cfg_raw = app_config.get("persistence", {})
    cfg = PersistenceConfig.from_dict(persistence_cfg_raw, app_config)

    if not cfg.enabled:
        logger.info("Persistence layer is disabled")
        return None

    if cfg.backend != "elasticsearch":
        raise PersistenceConfigError(
            f"Unsupported persistence backend: {cfg.backend!r}; "
            "only 'elasticsearch' is implemented."
        )

    if es_client is None:
        if not cfg.elasticsearch_hosts:
            raise PersistenceConfigError(
                "Persistence is enabled but no Elasticsearch hosts are "
                "configured. Set persistence.elasticsearch.hosts or the "
                "top-level elastic.hosts in config.yaml."
            )
        try:
            es_config = ElasticConfig(hosts=cfg.elasticsearch_hosts)
            es_client = ElasticClient(config=es_config)
        except Exception as exc:
            raise PersistenceConfigError(
                "Failed to construct Elasticsearch client for persistence; "
                f"hosts={list(cfg.elasticsearch_hosts)}"
            ) from exc

    store = ElasticPersistenceStore(
        es_client,
        index_prefix=cfg.index_prefix,
        auto_create_indices=cfg.auto_create_indices,
        index_shards=cfg.index_shards,
        index_replicas=cfg.index_replicas,
    )
    logger.info(
        "Persistence layer initialised (backend=%s index_prefix=%s "
        "auto_create_indices=%s shards=%d replicas=%d)",
        cfg.backend, cfg.index_prefix, cfg.auto_create_indices,
        cfg.index_shards, cfg.index_replicas,
    )
    return store
