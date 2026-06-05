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

from mdx.analytics.core.stream.sink.sink_kafka import SinkKafka
from mdx.analytics.core.schema.config import AppConfig, AppKafkaConfig, KafkaProducerConfig


class TestSinkKafkaFunctionality:
    """
    Functionality tests for SinkKafka class.
    
    This test suite covers the core functionality of the write and write_msg methods
    including message serialization, key extraction, headers processing, and producer interactions.
    """

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        self.mock_kafka_config = Mock(spec=AppKafkaConfig)
        self.mock_producer_config = Mock(spec=KafkaProducerConfig)
        
        # Configure mock config
        self.mock_config.kafka = self.mock_kafka_config
        self.mock_kafka_config.brokers = "localhost:9092"
        self.mock_kafka_config.producer = self.mock_producer_config
        self.mock_producer_config.lingerMs = 100
        self.mock_producer_config.batchSize = 16384
        self.mock_producer_config.messageMaxBytes = 1048576
        
        # Mock get_kafka_topic method
        self.mock_config.get_kafka_topic = Mock(return_value="test-topic")
        
        # Create SinkKafka instance
        self.sink = SinkKafka(self.mock_config)

    @pytest.fixture
    def mock_producer(self):
        """Fixture providing a mocked Producer instance."""
        producer = Mock()
        producer.produce = Mock()
        producer.poll = Mock()
        producer.flush = Mock()
        return producer

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_basic_functionality(self, mock_producer_class, mock_producer):
        """Test basic write functionality with simple messages."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        messages = [{"id": 1, "name": "test1"}, {"id": 2, "name": "test2"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        self.mock_config.get_kafka_topic.assert_called_once_with(dest_key)
        assert mock_producer.produce.call_count == 2
        
        # Check first message
        first_call = mock_producer.produce.call_args_list[0]
        assert first_call[1]['topic'] == 'test-topic'
        assert first_call[1]['key'] is None
        assert first_call[1]['value'] == b"{'id': 1, 'name': 'test1'}"
        assert first_call[1]['headers'] == []
        
        # Check second message
        second_call = mock_producer.produce.call_args_list[1]
        assert second_call[1]['topic'] == 'test-topic'
        assert second_call[1]['key'] is None
        assert second_call[1]['value'] == b"{'id': 2, 'name': 'test2'}"
        
        mock_producer.poll.assert_called_once_with(0)

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_with_key_extractor_and_serializer(self, mock_producer_class, mock_producer):
        """Test write functionality with key extraction and serialization."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        messages = [{"id": 1, "name": "test1"}, {"id": 2, "name": "test2"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        key_extractor = lambda x: x["id"]
        key_serializer = lambda x: f"key_{x}".encode()
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, key_extractor, key_serializer)
        
        # Assert
        assert mock_producer.produce.call_count == 2
        
        # Check first message key
        first_call = mock_producer.produce.call_args_list[0]
        assert first_call[1]['key'] == b"key_1"
        
        # Check second message key
        second_call = mock_producer.produce.call_args_list[1]
        assert second_call[1]['key'] == b"key_2"

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_with_headers(self, mock_producer_class, mock_producer):
        """Test write functionality with custom headers."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        messages = [{"id": 1, "name": "test1"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        headers = {"content-type": "application/json", "version": b"1.0"}
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, headers=headers)
        
        # Assert
        call_args = mock_producer.produce.call_args_list[0]
        expected_headers = [("content-type", "application/json"), ("version", b"1.0")]
        assert call_args[1]['headers'] == expected_headers

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_msg_basic_functionality(self, mock_producer_class, mock_producer):
        """Test basic write_msg functionality."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        message = b"test message"
        key = b"test_key"
        
        # Act
        self.sink.write_msg(dest_key, message, key)
        
        # Assert
        self.mock_config.get_kafka_topic.assert_called_once_with(dest_key)
        mock_producer.produce.assert_called_once_with(
            topic='test-topic',
            key=key,
            value=message,
            on_delivery=self.sink._delivery_callback,
            headers=[]
        )
        mock_producer.poll.assert_called_once_with(0)

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_msg_with_headers(self, mock_producer_class, mock_producer):
        """Test write_msg functionality with custom headers."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        message = b"test message"
        key = b"test_key"
        headers = {"content-type": "application/json", "version": b"1.0"}
        
        # Act
        self.sink.write_msg(dest_key, message, key, headers)
        
        # Assert
        call_args = mock_producer.produce.call_args
        expected_headers = [("content-type", "application/json"), ("version", b"1.0")]
        assert call_args[1]['headers'] == expected_headers

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_msg_with_none_key(self, mock_producer_class, mock_producer):
        """Test write_msg functionality with None key."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        message = b"test message"
        key = None
        
        # Act
        self.sink.write_msg(dest_key, message, key)
        
        # Assert
        call_args = mock_producer.produce.call_args
        assert call_args[1]['key'] is None

    @pytest.mark.parametrize("messages", [
        ([{"id": 1}]),
        ([{"id": 1}, {"id": 2}]),
        ([{"id": 1}, {"id": 2}, {"id": 3}]),
    ])
    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_with_multiple_messages(self, mock_producer_class, mock_producer, messages):
        """Test write functionality with different numbers of messages."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        assert mock_producer.produce.call_count == len(messages)

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_topic_not_found_error(self, mock_producer_class, mock_producer):
        """Test write raises ValueError when topic is not found."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        self.mock_config.get_kafka_topic.return_value = None
        
        dest_key = "nonexistent"
        messages = [{"id": 1}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a kafka topic with key: nonexistent"):
            self.sink.write(dest_key, messages, value_serializer)

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_write_msg_topic_not_found_error(self, mock_producer_class, mock_producer):
        """Test write_msg raises ValueError when topic is not found."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        self.mock_config.get_kafka_topic.return_value = None
        
        dest_key = "nonexistent"
        message = b"test message"
        key = b"test_key"
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a kafka topic with key: nonexistent"):
            self.sink.write_msg(dest_key, message, key)

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_producer_configuration(self, mock_producer_class, mock_producer):
        """Test that producer is configured correctly."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        messages = [{"id": 1}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        expected_config = {
            'bootstrap.servers': 'localhost:9092',
            'linger.ms': 100,
            'batch.size': 16384,
            'message.max.bytes': 1048576
        }
        mock_producer_class.assert_called_once_with(expected_config)

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_producer_reuse(self, mock_producer_class, mock_producer):
        """Test that producer instance is reused across multiple calls."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        dest_key = "output"
        messages = [{"id": 1}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        mock_producer_class.assert_called_once()  # Producer created only once

    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_close_functionality(self, mock_producer_class, mock_producer):
        """Test close method flushes the producer."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        
        # Initialize producer by calling write
        dest_key = "output"
        messages = [{"id": 1}]
        value_serializer = lambda x: str(x).encode('utf-8')
        self.sink.write(dest_key, messages, value_serializer)
        
        # Act
        self.sink.close()
        
        # Assert
        mock_producer.flush.assert_called_once()

    @patch('mdx.analytics.core.stream.sink.sink_kafka.logger')
    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_delivery_callback_success(self, mock_producer_class, mock_logger, mock_producer):
        """Test delivery callback logs success message."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        mock_msg = Mock()
        mock_msg.topic.return_value = "test-topic"
        mock_msg.partition.return_value = 0
        
        # Act
        self.sink._delivery_callback(None, mock_msg)
        
        # Assert
        mock_logger.debug.assert_called_once_with("Message delivered to test-topic [0]")

    @patch('mdx.analytics.core.stream.sink.sink_kafka.logger')
    @patch('mdx.analytics.core.stream.sink.sink_kafka.Producer')
    def test_delivery_callback_error(self, mock_producer_class, mock_logger, mock_producer):
        """Test delivery callback logs error message."""
        # Arrange
        mock_producer_class.return_value = mock_producer
        error = "Connection timeout"
        
        # Act
        self.sink._delivery_callback(error, None)
        
        # Assert
        mock_logger.warning.assert_called_once_with(f"Message delivery failed: {error} for message: None")
