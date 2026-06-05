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
from mdx.analytics.core.stream.sink.sink_factory import get_sink
from mdx.analytics.core.stream.sink.sink_kafka import SinkKafka
from mdx.analytics.core.stream.sink.sink_redis_stream import SinkRedisStream
from mdx.analytics.core.stream.sink.sink_mqtt import SinkMQTT


class TestGetSink:
    """Comprehensive test suite for get_sink function covering functionality, edge cases, and error conditions."""

    def setup_method(self):
        """Set up common test fixtures."""
        self.base_config = AppConfig()

    # FUNCTIONALITY TESTS - Testing core functionality with valid configurations

    def test_get_sink_kafka_valid_configuration(self):
        """Test successful creation of Kafka sink with valid configuration."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        
        # Act
        result = get_sink(self.base_config)
        
        # Assert
        assert isinstance(result, SinkKafka)

    def test_get_sink_redis_stream_valid_configuration(self):
        """Test successful creation of Redis Stream sink with valid configuration."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="redisStream")]
        self.base_config.redisStream = AppRedisStreamConfig(host="localhost")
        
        # Act
        result = get_sink(self.base_config)
        
        # Assert
        assert isinstance(result, SinkRedisStream)

    def test_get_sink_mqtt_valid_configuration(self):
        """Test successful creation of MQTT sink with valid configuration."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="mqtt")]
        self.base_config.mqtt = AppMQTTConfig(host="localhost")
        
        # Act
        result = get_sink(self.base_config)
        
        # Assert
        assert isinstance(result, SinkMQTT)

    @pytest.mark.parametrize("sink_type,config_attr,config_class,expected_class", [
        ("kafka", "kafka", AppKafkaConfig, SinkKafka),
        ("redisStream", "redisStream", AppRedisStreamConfig, SinkRedisStream),
        ("mqtt", "mqtt", AppMQTTConfig, SinkMQTT),
    ])
    def test_get_sink_parametrized_valid_configurations(self, sink_type, config_attr, config_class, expected_class):
        """Test sink creation for all supported types using parametrized testing."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value=sink_type)]
        if sink_type == "kafka":
            setattr(self.base_config, config_attr, config_class(brokers="localhost:9092"))
        else:
            setattr(self.base_config, config_attr, config_class(host="localhost"))
        
        # Act
        result = get_sink(self.base_config)
        
        # Assert
        assert isinstance(result, expected_class)

    # EDGE CASE TESTS - Testing boundary conditions and special values

    def test_get_sink_kafka_with_missing_brokers(self):
        """Test that Kafka sink is not created when brokers is MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers=MISSING)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_redis_with_missing_host(self):
        """Test that Redis Stream sink is not created when host is MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="redisStream")]
        self.base_config.redisStream = AppRedisStreamConfig(host=MISSING)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_mqtt_with_missing_host(self):
        """Test that MQTT sink is not created when host is MISSING."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="mqtt")]
        self.base_config.mqtt = AppMQTTConfig(host=MISSING)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_kafka_with_none_config(self):
        """Test that ValueError is raised when Kafka config is None."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="kafka")]
        with patch.object(self.base_config, 'kafka', None):
            # Act & Assert
            with pytest.raises(ValueError, match="Missing a valid sink configuration"):
                get_sink(self.base_config)

    def test_get_sink_redis_with_none_config(self):
        """Test that ValueError is raised when Redis config is None."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="redisStream")]
        with patch.object(self.base_config, 'redisStream', None):
            # Act & Assert
            with pytest.raises(ValueError, match="Missing a valid sink configuration"):
                get_sink(self.base_config)

    def test_get_sink_mqtt_with_none_config(self):
        """Test that ValueError is raised when MQTT config is None."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="mqtt")]
        with patch.object(self.base_config, 'mqtt', None):
            # Act & Assert
            with pytest.raises(ValueError, match="Missing a valid sink configuration"):
                get_sink(self.base_config)

    # DIRTY TESTS - Testing error conditions and invalid inputs

    def test_get_sink_unsupported_sink_type(self):
        """Test that ValueError is raised for unsupported sink types."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="unsupported")]
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_empty_sink_type(self):
        """Test that ValueError is raised for empty sink type."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="")]
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_missing_sink_type_config(self):
        """Test that ValueError is raised when no sinkType is configured."""
        # Arrange - base_config has empty app list by default
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_none_sink_type(self):
        """Test that ValueError is raised when sink type returns None from get_app_config."""
        # Arrange - Use a mock config instead of patching the Pydantic object
        mock_config = Mock(spec=AppConfig)
        mock_config.get_app_config.return_value = None
        mock_config.kafka = Mock()
        mock_config.redisStream = Mock()
        mock_config.mqtt = Mock()
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(mock_config)

    @pytest.mark.parametrize("invalid_sink_type", [
        "KAFKA",  # Wrong case
        "redis",  # Wrong case for redisStream
        "Redis",  # Wrong case for redisStream
        "MQTT",   # Wrong case
        "invalid",
        "123",
        "kafka ",  # Extra space
        " kafka",  # Leading space
    ])
    def test_get_sink_invalid_sink_types(self, invalid_sink_type):
        """Test that various invalid sink types raise ValueError."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value=invalid_sink_type)]
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_kafka_valid_type_but_missing_required_field(self):
        """Test edge case where sink type is valid but required config field is missing."""
        # Arrange - Valid sink type but invalid configuration
        self.base_config.app = [KeyValuePair(name="sinkType", value="kafka")]
        # kafka config has default brokers="localhost:9092" but we'll override with MISSING
        self.base_config.kafka.brokers = MISSING
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_all_configs_present_but_wrong_sink_type(self):
        """Test case where all sink configs are valid but sink type doesn't match."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="nonexistent")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        self.base_config.redisStream = AppRedisStreamConfig(host="localhost")
        self.base_config.mqtt = AppMQTTConfig(host="localhost")
        
        # Act & Assert
        with pytest.raises(ValueError, match="Missing a valid sink configuration"):
            get_sink(self.base_config)

    def test_get_sink_with_mock_config_get_app_config_exception(self):
        """Test behavior when get_app_config raises an exception."""
        # Arrange - Use a mock config instead of patching the Pydantic object
        mock_config = Mock(spec=AppConfig)
        mock_config.get_app_config.side_effect = Exception("Config error")
        
        # Act & Assert
        with pytest.raises(Exception, match="Config error"):
            get_sink(mock_config)

    # BOUNDARY CONDITION TESTS

    def test_get_sink_config_with_special_characters_in_values(self):
        """Test configuration with special characters in host/broker values."""
        # Arrange
        self.base_config.app = [KeyValuePair(name="sinkType", value="kafka")]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092,host-2:9093")
        
        # Act
        result = get_sink(self.base_config)
        
        # Assert
        assert isinstance(result, SinkKafka)

    def test_get_sink_with_case_sensitive_validation(self):
        """Test that sink type matching is case-sensitive."""
        # Test cases that should work
        valid_cases = ["kafka", "redisStream", "mqtt"]
        
        for sink_type in valid_cases:
            # Arrange
            config = AppConfig()
            config.app = [KeyValuePair(name="sinkType", value=sink_type)]
            
            if sink_type == "kafka":
                config.kafka = AppKafkaConfig(brokers="localhost:9092")
                expected_type = SinkKafka
            elif sink_type == "redisStream":
                config.redisStream = AppRedisStreamConfig(host="localhost")
                expected_type = SinkRedisStream
            elif sink_type == "mqtt":
                config.mqtt = AppMQTTConfig(host="localhost")
                expected_type = SinkMQTT
            
            # Act
            result = get_sink(config)
            
            # Assert
            assert isinstance(result, expected_type)

    def test_get_sink_multiple_sink_types_in_app_config(self):
        """Test behavior when multiple sinkType entries exist in app config."""
        # Arrange - First one should win due to how get_app_config works
        self.base_config.app = [
            KeyValuePair(name="sinkType", value="kafka"),
            KeyValuePair(name="sinkType", value="redis")  # This will be ignored
        ]
        self.base_config.kafka = AppKafkaConfig(brokers="localhost:9092")
        
        # Act
        result = get_sink(self.base_config)
        
        # Assert
        assert isinstance(result, SinkKafka)