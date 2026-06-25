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

"""Unit tests for PersistenceConfig and create_persistence_store factory."""

import pytest
from unittest.mock import MagicMock, patch

from persistence.config import PersistenceConfig
from persistence.factory import create_persistence_store
from persistence.elastic_store import ElasticPersistenceStore
from persistence.exceptions import PersistenceConfigError


class TestPersistenceConfig:

    def test_defaults(self):
        cfg = PersistenceConfig()

        assert cfg.enabled is True
        assert cfg.backend == "elasticsearch"
        assert cfg.index_prefix == "ab-"
        assert cfg.elasticsearch_hosts == tuple()
        assert cfg.auto_create_indices is True
        assert cfg.index_shards == 1
        assert cfg.index_replicas == 0

    def test_from_dict_with_defaults(self):
        cfg = PersistenceConfig.from_dict({})

        assert cfg.enabled is True
        assert cfg.backend == "elasticsearch"
        assert cfg.index_prefix == "ab-"
        assert cfg.auto_create_indices is True

    def test_from_dict_custom_values(self):
        cfg = PersistenceConfig.from_dict({
            "enabled": False,
            "backend": "elasticsearch",
            "index_prefix": "test-",
            "elasticsearch": {"hosts": ["http://es:9200"]},
        })

        assert cfg.enabled is False
        assert cfg.index_prefix == "test-"
        assert cfg.elasticsearch_hosts == ("http://es:9200",)

    def test_from_dict_auto_create_indices_off(self):
        cfg = PersistenceConfig.from_dict({"auto_create_indices": False})

        assert cfg.auto_create_indices is False

    def test_from_dict_index_settings(self):
        cfg = PersistenceConfig.from_dict({
            "index_settings": {"shards": 3, "replicas": 2},
        })

        assert cfg.index_shards == 3
        assert cfg.index_replicas == 2

    def test_from_dict_inherits_elastic_hosts(self):
        app_config = {"elastic": {"hosts": ["http://shared:9200"]}}
        cfg = PersistenceConfig.from_dict({}, app_config=app_config)

        assert cfg.elasticsearch_hosts == ("http://shared:9200",)

    def test_from_dict_override_takes_priority(self):
        app_config = {"elastic": {"hosts": ["http://shared:9200"]}}
        cfg = PersistenceConfig.from_dict(
            {"elasticsearch": {"hosts": ["http://dedicated:9200"]}},
            app_config=app_config,
        )

        assert cfg.elasticsearch_hosts == ("http://dedicated:9200",)


class TestFactory:

    def test_disabled_returns_none(self):
        result = create_persistence_store({"persistence": {"enabled": False}})

        assert result is None

    def test_unsupported_backend_raises(self):
        with pytest.raises(PersistenceConfigError, match="Unsupported persistence backend"):
            create_persistence_store({
                "persistence": {"enabled": True, "backend": "postgres"},
            })

    def test_no_hosts_raises(self):
        with pytest.raises(PersistenceConfigError, match="no Elasticsearch hosts"):
            create_persistence_store({"persistence": {"enabled": True}})

    def test_reuses_existing_es_client(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True

        app_config = {
            "persistence": {"enabled": True},
            "elastic": {"hosts": ["http://localhost:9200"]},
        }
        result = create_persistence_store(app_config, es_client=mock_client)

        assert isinstance(result, ElasticPersistenceStore)

    @patch("persistence.factory.ElasticClient")
    def test_creates_new_client_from_config(self, mock_es_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_es_cls.return_value = mock_instance

        app_config = {
            "persistence": {"enabled": True},
            "elastic": {"hosts": ["http://localhost:9200"]},
        }
        result = create_persistence_store(app_config)

        assert isinstance(result, ElasticPersistenceStore)
        mock_es_cls.assert_called_once()

    @patch("persistence.factory.ElasticClient")
    def test_connection_failure_raises(self, mock_es_cls):
        mock_es_cls.side_effect = ConnectionError("refused")

        app_config = {
            "persistence": {"enabled": True},
            "elastic": {"hosts": ["http://localhost:9200"]},
        }
        with pytest.raises(PersistenceConfigError, match="Failed to construct"):
            create_persistence_store(app_config)

    def test_empty_config_raises_because_default_enabled_has_no_hosts(self):
        # enabled defaults to True; without hosts, the factory has no way
        # to honour the implicit "enable persistence" intent, so it fails
        # loudly rather than returning None silently.
        with pytest.raises(PersistenceConfigError):
            create_persistence_store({})

    def test_factory_passes_auto_create_and_shards_through_to_store(self):
        mock_client = MagicMock()
        app_config = {
            "persistence": {
                "enabled": True,
                "auto_create_indices": False,
                "index_settings": {"shards": 3, "replicas": 2},
            },
            "elastic": {"hosts": ["http://localhost:9200"]},
        }
        store = create_persistence_store(app_config, es_client=mock_client)

        assert isinstance(store, ElasticPersistenceStore)
        assert store._auto_create_indices is False
        assert store._index_shards == 3
        assert store._index_replicas == 2
