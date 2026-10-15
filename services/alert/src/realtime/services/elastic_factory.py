#!/usr/bin/env python3
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
Elasticsearch client construction for the realtime surfaces.

Lives here rather than in the web layer because two processes need it: the API
child serves reads, and the pipeline process folds events. Building it twice
from two copies of the same configuration is how the two drift apart.
"""

import logging
from typing import Optional

from ..config import load_config

logger = logging.getLogger(__name__)


def build_elastic_client(config: Optional[dict] = None):
    """Build a client from config, or None when Elasticsearch is not usable.

    None rather than an exception: a caller answers 503 or skips a cycle, and
    both are better than refusing to start.
    """
    config = config if config is not None else load_config()
    es_cfg = (config.get("elastic") or {})

    if not es_cfg.get("enabled", False):
        return None

    hosts_config = es_cfg.get("hosts", [])
    if isinstance(hosts_config, str):
        hosts = (hosts_config,)
    elif isinstance(hosts_config, (list, tuple)):
        hosts = tuple(str(h).strip() for h in hosts_config if h)
    else:
        hosts = tuple()

    if not hosts:
        logger.warning("Elasticsearch enabled but no hosts configured")
        return None

    try:
        from clients.elastic import ElasticClient, ElasticConfig

        return ElasticClient(
            config=ElasticConfig(
                hosts=hosts,
                username=es_cfg.get("username"),
                password=es_cfg.get("password"),
                api_key=es_cfg.get("api_key"),
                verify_certs=es_cfg.get("verify_certs", False),
                ca_certs=es_cfg.get("ca_certs"),
                request_timeout=es_cfg.get("request_timeout", 10),
            )
        )
    except ConnectionError as exc:
        logger.error("Elasticsearch cluster unreachable at %s: %s", hosts, exc)
        return None
    except (ValueError, TypeError) as exc:
        logger.error("Invalid Elasticsearch configuration: %s", exc)
        return None
    except Exception:
        # Deliberately broad, and restored after a refactor narrowed it: this
        # is a FastAPI dependency, so anything escaping here becomes a 500 on
        # routes that are supposed to degrade to 503. An import error from the
        # transitive elastic stack is the realistic case.
        logger.error("Could not build the Elasticsearch client", exc_info=True)
        return None
