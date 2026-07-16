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

"""Unit tests for utils.message_broker_factory."""
import pytest
from unittest.mock import MagicMock, patch


def test_create_kafka_broker(minimal_config):
    from utils.message_broker_factory import MessageBrokerFactory

    with patch("utils.message_broker_factory.KafkaMessageBroker") as mock_kafka:
        mock_instance = MagicMock()
        mock_kafka.return_value = mock_instance
        broker = MessageBrokerFactory.create_message_broker("kafka", minimal_config)
        assert broker is mock_instance
        mock_kafka.assert_called_once()
        call_kw = mock_kafka.call_args[1]
        assert call_kw["bootstrap_servers"] == minimal_config["WDM_KFK_BOOTSTRAP_URL"]
        assert call_kw["wl_id_field"] == minimal_config["WDM_WL_ID_FIELD"]
        assert call_kw["wl_event_field"] == minimal_config["WDM_WL_EVENT_FIELD"]


def test_create_redis_broker(minimal_config):
    from utils.message_broker_factory import MessageBrokerFactory

    with patch("utils.message_broker_factory.RedisMessageBroker") as mock_redis:
        mock_instance = MagicMock()
        mock_redis.return_value = mock_instance
        broker = MessageBrokerFactory.create_message_broker("redis", minimal_config)
        assert broker is mock_instance
        mock_redis.assert_called_once()
        call_kw = mock_redis.call_args[1]
        assert call_kw["host"] == minimal_config["WDM_REDIS_HOST"]
        assert call_kw["port"] == minimal_config["WDM_REDIS_PORT"]
        assert call_kw["db"] == minimal_config["REDIS_DB"]
        assert call_kw["wl_id_field"] == minimal_config["WDM_WL_ID_FIELD"]
        assert call_kw["wl_event_field"] == minimal_config["WDM_WL_EVENT_FIELD"]


def test_create_broker_case_insensitive(minimal_config):
    from utils.message_broker_factory import MessageBrokerFactory

    with patch("utils.message_broker_factory.KafkaMessageBroker") as mock_kafka:
        mock_kafka.return_value = MagicMock()
        broker = MessageBrokerFactory.create_message_broker("Kafka", minimal_config)
        assert broker is not None
        mock_kafka.assert_called_once()

    with patch("utils.message_broker_factory.RedisMessageBroker") as mock_redis:
        mock_redis.return_value = MagicMock()
        broker = MessageBrokerFactory.create_message_broker("REDIS", minimal_config)
        assert broker is not None
        mock_redis.assert_called_once()


def test_unsupported_broker_type_raises(minimal_config):
    from utils.message_broker_factory import MessageBrokerFactory

    with pytest.raises(ValueError) as excinfo:
        MessageBrokerFactory.create_message_broker("unknown", minimal_config)
    assert "Unsupported" in str(excinfo.value)
    assert "kafka" in str(excinfo.value).lower() or "redis" in str(excinfo.value).lower()
