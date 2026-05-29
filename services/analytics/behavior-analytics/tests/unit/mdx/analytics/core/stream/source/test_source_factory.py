# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import pytest
from unittest.mock import Mock, patch

from mdx.analytics.core.schema.config import AppConfig, AppKafkaConfig, AppRedisStreamConfig, AppMQTTConfig, MISSING, KeyValuePair
from mdx.analytics.core.stream.source.source_factory import get_source
from mdx.analytics.core.stream.source.source_kafka import SourceKafka
from mdx.analytics.core.stream.source.source_redis_stream import SourceRedisStream
from mdx.analytics.core.stream.source.source_mqtt import SourceMQTT


class TestGetSource:
    """Comprehensive test suite for get_source function covering functionality, edge cases, and error conditions."""

    def setup_method(self):
        """Set up common test fixtures."""
        self.base_config = AppConfig()

    # FUNCTIONALITY TESTS - Testing core functionality with valid configurations

    def test_get_source_kafka_valid_configuration(self):
        """Test successful creation of Kafka source with valid configuration."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceKafka)

    def test_get_source_redis_stream_valid_configuration(self):
        """Test successful creation of Redis Stream source with valid configuration."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        self.base_config.redisStream = AppRedisStreamConfig(host="localhost")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceRedisStream)

    def test_get_source_mqtt_valid_configuration(self):
        """Test successful creation of MQTT source with valid configuration."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="mqtt")]
        self.base_config.mqtt = AppMQTTConfig(host="localhost")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceMQTT)

    @pytest.mark.parametrize("source_type,config_attr,config_class,expected_class", [
        ("kafka", "kafka", AppKafkaConfig, SourceKafka),
        ("redisStream", "redisStream", AppRedisStreamConfig, SourceRedisStream),
        ("mqtt", "mqtt", AppMQTTConfig, SourceMQTT),
    ])
    def test_get_source_parametrized_valid_configurations(self, source_type, config_attr, config_class, expected_class):
        """Test source creation for all supported types using parametrized testing."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value=source_type)]
        if source_type == "kafka":
            setattr(self.base_config, config_attr, config_class(brokers="localhost:9092"))
        else:
            setattr(self.base_config, config_attr, config_class(host="localhost"))
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, expected_class)

    @pytest.mark.parametrize("host_value", [
        "localhost",
        "127.0.0.1",
        "redis.example.com",
        "redis-cluster.local:6379",
    ])
    def test_get_source_redis_stream_various_hosts(self, host_value):
        """Test Redis Stream source creation with various valid host values."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        self.base_config.redisStream = AppRedisStreamConfig(host=host_value)
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceRedisStream)

    @pytest.mark.parametrize("brokers_value", [
        "localhost:9092",
        "kafka1:9092,kafka2:9092",
        "kafka.example.com:9092",
    ])
    def test_get_source_kafka_various_brokers(self, brokers_value):
        """Test Kafka source creation with various valid broker configurations."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers=brokers_value)
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceKafka)

    # EDGE CASE TESTS - Testing boundary conditions and special values

    def test_get_source_kafka_with_missing_brokers(self):
        """Test that Kafka source is not created when brokers is MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers=MISSING)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_redis_with_missing_host(self):
        """Test that Redis Stream source is not created when host is MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        self.base_config.redisStream = AppRedisStreamConfig(host=MISSING)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_mqtt_with_missing_host(self):
        """Test that MQTT source is not created when host is MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="mqtt")]
        self.base_config.mqtt = AppMQTTConfig(host=MISSING)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_kafka_with_none_config(self):
        """Test that ValueError is raised when Kafka config is None."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        with patch.object(self.base_config, 'kafka', None):
            # Act & Assert
            with pytest.raises(ValueError, match="Missing a valid source configuration"):
                get_source(self.base_config)

    def test_get_source_redis_with_none_config(self):
        """Test that ValueError is raised when Redis config is None."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        with patch.object(self.base_config, 'redisStream', None):
            # Act & Assert
            with pytest.raises(ValueError, match="Missing a valid source configuration"):
                get_source(self.base_config)

    def test_get_source_mqtt_with_none_config(self):
        """Test that ValueError is raised when MQTT config is None."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="mqtt")]
        with patch.object(self.base_config, 'mqtt', None):
            # Act & Assert
            with pytest.raises(ValueError, match="Missing a valid source configuration"):
                get_source(self.base_config)

    @pytest.mark.parametrize("source_type,config_attr", [
        ("kafka", "kafka"),
        ("redisStream", "redisStream"),
        ("mqtt", "mqtt"),
    ])
    def test_get_source_with_none_configs_parametrized(self, source_type, config_attr):
        """Test that ValueError is raised for all source types when their config is None."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value=source_type)]
        with patch.object(self.base_config, config_attr, None):
            # Act & Assert
            with pytest.raises(ValueError, match="Missing a valid source configuration"):
                get_source(self.base_config)

    # DIRTY TESTS - Testing error conditions and invalid inputs

    def test_get_source_unsupported_source_type(self):
        """Test that ValueError is raised for unsupported source types."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="unsupported")]
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_empty_source_type(self):
        """Test that ValueError is raised when source type is empty."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="")]
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_none_source_type(self):
        """Test that ValueError is raised when source type is None."""
        # Arrange - get_app_config returns None when key doesn't exist and no default provided
        # The function calls config.get_app_config("sourceType") with no default, so it returns None
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_missing_source_type_key(self):
        """Test that ValueError is raised when sourceType key is missing from config."""
        # Arrange - don't set sourceType at all, get_app_config will return None
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    @pytest.mark.parametrize("invalid_source_type", [
        "KAFKA",  # wrong case
        "redis",  # wrong name
        "mqtt_client",  # wrong name
        "file",  # unsupported type
        "database",  # unsupported type
        "stream",  # generic name
        123,  # number instead of string
        True,  # boolean instead of string
    ])
    def test_get_source_invalid_source_types(self, invalid_source_type):
        """Test that ValueError is raised for various invalid source type values."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value=str(invalid_source_type))]
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_kafka_correct_type_but_missing_brokers(self):
        """Test that Kafka source type with missing brokers raises ValueError."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        # Kafka config has default valid brokers, so this should succeed
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceKafka)

    def test_get_source_redis_correct_type_but_missing_host(self):
        """Test that Redis Stream source type with missing host raises ValueError."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        # Redis config has default valid host, so this should succeed
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceRedisStream)

    def test_get_source_mqtt_correct_type_but_missing_host(self):
        """Test that MQTT source type with missing host raises ValueError."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="mqtt")]
        # Don't set mqtt config at all, so it will be default (empty)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    def test_get_source_kafka_config_exists_but_brokers_missing(self):
        """Test Kafka with config object present but brokers set to MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig()  # Default constructor has brokers="localhost:9092"
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceKafka)

    def test_get_source_redis_config_exists_but_host_missing(self):
        """Test Redis Stream with config object present but host set to MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        self.base_config.redisStream = AppRedisStreamConfig()  # Default constructor has host="localhost"
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceRedisStream)

    def test_get_source_mqtt_config_exists_but_host_missing(self):
        """Test MQTT with config object present but host set to MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="mqtt")]
        self.base_config.mqtt = AppMQTTConfig()  # Default constructor has host=MISSING
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid source configuration"):
            get_source(self.base_config)

    # INTEGRATION TESTS - Testing combinations and complex scenarios

    def test_get_source_multiple_configs_kafka_selected(self):
        """Test that Kafka source is selected when multiple configs are present."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        self.base_config.redisStream = AppRedisStreamConfig(host="localhost")
        self.base_config.mqtt = AppMQTTConfig(host="localhost")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceKafka)

    def test_get_source_multiple_configs_redis_selected(self):
        """Test that Redis Stream source is selected when multiple configs are present."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        self.base_config.redisStream = AppRedisStreamConfig(host="localhost")
        self.base_config.mqtt = AppMQTTConfig(host="localhost")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceRedisStream)

    def test_get_source_multiple_configs_mqtt_selected(self):
        """Test that MQTT source is selected when multiple configs are present."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="mqtt")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        self.base_config.redisStream = AppRedisStreamConfig(host="localhost")
        self.base_config.mqtt = AppMQTTConfig(host="localhost")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceMQTT)

    def test_get_source_config_with_direct_sourceType_setting(self):
        """Test get_source by directly setting sourceType in app config."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        assert isinstance(result, SourceKafka)
        # Verify the config was used by checking the sourceType is set correctly
        assert self.base_config.get_app_config("sourceType") == "kafka"

    @patch('mdx.analytics.core.stream.source.source_factory.SourceKafka')
    def test_get_source_kafka_constructor_called_with_config(self, mock_kafka_class):
        """Test that SourceKafka constructor is called with the correct config."""
        # Arrange
        mock_instance = Mock()
        mock_kafka_class.return_value = mock_instance
        self.base_config.app = [KeyValuePair(name="sourceType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        mock_kafka_class.assert_called_once_with(self.base_config)
        assert result == mock_instance

    @patch('mdx.analytics.core.stream.source.source_factory.SourceRedisStream')
    def test_get_source_redis_constructor_called_with_config(self, mock_redis_class):
        """Test that SourceRedisStream constructor is called with the correct config."""
        # Arrange
        mock_instance = Mock()
        mock_redis_class.return_value = mock_instance
        self.base_config.app = [KeyValuePair(name="sourceType", value="redisStream")]
        self.base_config.redisStream = AppRedisStreamConfig(host="localhost")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        mock_redis_class.assert_called_once_with(self.base_config)
        assert result == mock_instance

    @patch('mdx.analytics.core.stream.source.source_factory.SourceMQTT')
    def test_get_source_mqtt_constructor_called_with_config(self, mock_mqtt_class):
        """Test that SourceMQTT constructor is called with the correct config."""
        # Arrange
        mock_instance = Mock()
        mock_mqtt_class.return_value = mock_instance
        self.base_config.app = [KeyValuePair(name="sourceType", value="mqtt")]
        self.base_config.mqtt = AppMQTTConfig(host="localhost")
        
        # Act
        result = get_source(self.base_config)
        
        # Assert
        mock_mqtt_class.assert_called_once_with(self.base_config)
        assert result == mock_instance