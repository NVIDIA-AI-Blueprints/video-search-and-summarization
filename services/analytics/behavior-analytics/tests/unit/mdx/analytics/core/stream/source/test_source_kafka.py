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
from confluent_kafka import Consumer

from mdx.analytics.core.stream.source.source_kafka import SourceKafka
from mdx.analytics.core.schema.config import AppConfig, AppKafkaConfig, KafkaConsumerConfig
from mdx.analytics.core.schema.models import StreamMessage


class TestSourceKafkaReadFunctionality:
    """Test suite for the SourceKafka read method functionality."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        self.mock_kafka_config = Mock(spec=AppKafkaConfig)
        self.mock_consumer_config = Mock(spec=KafkaConsumerConfig)

        # Configure mock config
        self.mock_kafka_config.brokers = "localhost:9092"
        self.mock_kafka_config.group = "test-group"
        self.mock_kafka_config.consumer = self.mock_consumer_config
        self.mock_consumer_config.maxPollRecords = 100
        self.mock_consumer_config.timeout = 0.1
        self.mock_consumer_config.autoOffsetReset = "latest"
        self.mock_consumer_config.enableAutoCommit = False
        self.mock_consumer_config.maxPollIntervalMs = 900000
        self.mock_consumer_config.fetchMaxBytes = 52428800
        self.mock_consumer_config.maxPartitionFetchBytes = 10485760
        self.mock_consumer_config.retryMaxAttempts = 3
        self.mock_consumer_config.retryIntervalSec = 30.0

        self.mock_config.kafka = self.mock_kafka_config
        self.mock_config.get_kafka_topic = Mock(return_value="test-topic")

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_returns_stream_messages_from_kafka_response(self, mock_consumer_class):
        """Test that read method returns StreamMessage objects from Kafka response."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        
        # Mock Kafka messages
        mock_msg1 = Mock()
        mock_msg1.error.return_value = None
        mock_msg1.key.return_value = b"test-key-1"
        mock_msg1.value.return_value = b"test-value-1"
        mock_msg1.headers.return_value = [("content-type", b"application/json")]
        mock_msg1.timestamp.return_value = (0, 1234567890000)  # (timestamp_type, timestamp_value)
        
        mock_msg2 = Mock()
        mock_msg2.error.return_value = None
        mock_msg2.key.return_value = b"test-key-2"
        mock_msg2.value.return_value = b"test-value-2"
        mock_msg2.headers.return_value = [("timestamp", b"2023-01-01")]
        mock_msg2.timestamp.return_value = (0, 1234567891000)  # (timestamp_type, timestamp_value)
        
        mock_consumer.consume.return_value = [mock_msg1, mock_msg2]
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 2
        assert all(isinstance(msg, StreamMessage) for msg in result)
        
        # Verify first message
        assert result[0].key == b"test-key-1"
        assert result[0].value == b"test-value-1"
        assert result[0].headers == {"content-type": b"application/json"}
        assert result[0].timestamp == 1234567890000
        
        # Verify second message
        assert result[1].key == b"test-key-2"
        assert result[1].value == b"test-value-2"
        assert result[1].headers == {"timestamp": b"2023-01-01"}
        assert result[1].timestamp == 1234567891000

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_calls_consume_with_correct_parameters(self, mock_consumer_class):
        """Test that read method calls consume with correct parameters."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        mock_consumer.consume.return_value = []
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        source.read("test-key")
        
        # Assert
        mock_consumer.consume.assert_called_once_with(
            num_messages=100,
            timeout=0.1
        )

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_creates_consumer_with_correct_configuration(self, mock_consumer_class):
        """Test that read method creates consumer with correct configuration."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        mock_consumer.consume.return_value = []
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        source.read("test-key")
        
        # Assert
        mock_consumer_class.assert_called_once_with({
            'bootstrap.servers': "localhost:9092",
            'group.id': "test-group - test-topic",
            'auto.offset.reset': "latest",
            'enable.auto.commit': False,
            'max.poll.interval.ms': 900000,
            'max.partition.fetch.bytes': 10485760,
            'fetch.max.bytes': 52428800  # From mock config
        })

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_subscribes_to_topic_and_waits_for_assignment(self, mock_consumer_class):
        """Test that read method subscribes to topic and waits for assignment."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        mock_consumer.consume.return_value = []
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        source.read("test-key")
        
        # Assert
        # The on_assign parameter should be a callable returned by _on_assign
        mock_consumer.subscribe.assert_called_once()
        call_args = mock_consumer.subscribe.call_args
        assert call_args[0][0] == ["test-topic"]
        assert 'on_assign' in call_args[1]
        assert callable(call_args[1]['on_assign'])

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_handles_empty_kafka_response(self, mock_consumer_class):
        """Test that read method handles empty Kafka response correctly."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        mock_consumer.consume.return_value = []
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert result == []

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_handles_none_messages_in_response(self, mock_consumer_class):
        """Test that read method handles None messages in Kafka response."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        
        mock_valid_msg = Mock()
        mock_valid_msg.error.return_value = None
        mock_valid_msg.key.return_value = b"test-key"
        mock_valid_msg.value.return_value = b"test-value"
        mock_valid_msg.headers.return_value = []
        mock_valid_msg.timestamp.return_value = (0, 1234567890000)  # (timestamp_type, timestamp_value)
        
        mock_consumer.consume.return_value = [None, mock_valid_msg, None]
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 1
        assert result[0].key == b"test-key"
        assert result[0].value == b"test-value"
        assert result[0].timestamp == 1234567890000

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_read_handles_kafka_message_errors(self, mock_logger, mock_consumer_class):
        """Test that read method handles Kafka message errors correctly."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        
        mock_error_msg = Mock()
        mock_error_msg.error.return_value = RuntimeError()  #KafkaError() doesnt work - TypeError: function missing required argument 'error' (pos 1)
        
        mock_valid_msg = Mock()
        mock_valid_msg.error.return_value = None
        mock_valid_msg.key.return_value = b"test-key"
        mock_valid_msg.value.return_value = b"test-value"
        mock_valid_msg.headers.return_value = []
        mock_valid_msg.timestamp.return_value = (0, 1234567890000)  # (timestamp_type, timestamp_value)
        
        mock_consumer.consume.return_value = [mock_error_msg, mock_valid_msg]
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 1
        assert result[0].key == b"test-key"
        assert result[0].timestamp == 1234567890000
        mock_logger.error.assert_called_once()

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_with_group_id_suffix(self, mock_consumer_class):
        """Test that read method correctly uses group_id_suffix."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        mock_consumer.consume.return_value = []
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        source.read("test-key", group_id_suffix="custom-suffix")
        
        # Assert
        mock_consumer_class.assert_called_once_with({
            'bootstrap.servers': "localhost:9092",
            'group.id': "test-group - test-topic - custom-suffix",
            'auto.offset.reset': "latest",
            'enable.auto.commit': False,
            'max.poll.interval.ms': 900000,
            'max.partition.fetch.bytes': 10485760,
            'fetch.max.bytes': 52428800  # From mock config
        })

    def test_read_raises_error_for_missing_topic(self):
        """Test that read method raises ValueError for missing topic."""
        # Arrange
        self.mock_config.get_kafka_topic.return_value = None
        source = SourceKafka(self.mock_config)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a kafka topic with key: invalid-key"):
            source.read("invalid-key")

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_reuses_existing_consumer_connection(self, mock_consumer_class):
        """Test that read method reuses existing consumer for same group_id."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        mock_consumer.consume.return_value = []
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        
        # Mock the _wait_for_assignment to properly set up the consumer
        def mock_wait_for_assignment(consumer, group_id, *args, **kwargs):
            # Simulate what happens in the real _wait_for_assignment
            source._consumers[group_id] = consumer
            return True
        
        source._wait_for_assignment = Mock(side_effect=mock_wait_for_assignment)
        
        # Act
        source.read("test-key")
        source.read("test-key")  # Second call should reuse consumer
        
        # Assert
        mock_consumer_class.assert_called_once()  # Consumer created only once

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    @patch('mdx.analytics.core.stream.source.source_kafka.time.sleep')
    def test_read_raises_exception_when_no_partitions_assigned(self, mock_sleep, mock_consumer_class):
        """Test that read method raises exception when no partitions are assigned."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        mock_consumer.assignment.return_value = []  # Empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return False (no assignment)
        source._wait_for_assignment = Mock(return_value=False)
        
        # Act & Assert
        with pytest.raises(Exception, match=r"Consumer initialization failed"):
            source.read("test-key")

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_handles_complex_message_headers(self, mock_consumer_class):
        """Test that read method handles complex message headers correctly."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        
        mock_msg = Mock()
        mock_msg.error.return_value = None
        mock_msg.key.return_value = b"test-key"
        mock_msg.value.return_value = b"test-value"
        mock_msg.headers.return_value = [
            ("content-type", b"application/json"),
            ("timestamp", b"2023-01-01T00:00:00Z"),
            ("source", b"test-source"),
            ("empty-header", b"")
        ]
        mock_msg.timestamp.return_value = (0, 1234567890000)  # (timestamp_type, timestamp_value)
        
        mock_consumer.consume.return_value = [mock_msg]
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 1
        expected_headers = {
            "content-type": b"application/json",
            "timestamp": b"2023-01-01T00:00:00Z",
            "source": b"test-source",
            "empty-header": b""
        }
        assert result[0].headers == expected_headers
        assert result[0].timestamp == 1234567890000

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_handles_messages_with_no_headers(self, mock_consumer_class):
        """Test that read method handles messages with no headers."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        
        mock_msg = Mock()
        mock_msg.error.return_value = None
        mock_msg.key.return_value = b"test-key"
        mock_msg.value.return_value = b"test-value"
        mock_msg.headers.return_value = None
        mock_msg.timestamp.return_value = (0, 1234567890000)  # (timestamp_type, timestamp_value)
        
        mock_consumer.consume.return_value = [mock_msg]
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 1
        assert result[0].headers == {}
        assert result[0].timestamp == 1234567890000

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_handles_large_message_batch(self, mock_consumer_class):
        """Test that read method handles large batches of messages correctly."""
        # Arrange
        mock_consumer = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer
        
        # Create 1000 mock messages
        mock_messages = []
        for i in range(1000):
            mock_msg = Mock()
            mock_msg.error.return_value = None
            mock_msg.key.return_value = f"key-{i}".encode()
            mock_msg.value.return_value = f"value-{i}".encode()
            mock_msg.headers.return_value = []
            mock_msg.timestamp.return_value = (0, 1234567890000 + i)  # (timestamp_type, timestamp_value)
            mock_messages.append(mock_msg)
        
        mock_consumer.consume.return_value = mock_messages
        mock_consumer.assignment.return_value = [Mock()]  # Non-empty assignment
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 1000
        for i, msg in enumerate(result):
            assert msg.key == f"key-{i}".encode()
            assert msg.value == f"value-{i}".encode()
            assert msg.headers == {}
            assert msg.timestamp == 1234567890000 + i

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_handles_different_topics_with_separate_consumers(self, mock_consumer_class):
        """Test that read method creates separate consumers for different topics."""
        # Arrange
        mock_consumer1 = Mock(spec=Consumer)
        mock_consumer2 = Mock(spec=Consumer)
        mock_consumer_class.side_effect = [mock_consumer1, mock_consumer2]
        
        mock_consumer1.consume.return_value = []
        mock_consumer1.assignment.return_value = [Mock()]
        mock_consumer2.consume.return_value = []
        mock_consumer2.assignment.return_value = [Mock()]
        
        # Mock different topics for different keys
        def mock_get_topic(key):
            return f"topic-{key}"
        
        self.mock_config.get_kafka_topic.side_effect = mock_get_topic
        
        source = SourceKafka(self.mock_config)
        # Mock the _wait_for_assignment method to return True
        source._wait_for_assignment = Mock(return_value=True)
        
        # Act
        source.read("key1")
        source.read("key2")
        
        # Assert
        assert mock_consumer_class.call_count == 2
        
        # Verify first consumer config
        first_call_args = mock_consumer_class.call_args_list[0][0][0]
        assert first_call_args['group.id'] == "test-group - topic-key1"
        
        # Verify second consumer config  
        second_call_args = mock_consumer_class.call_args_list[1][0][0]
        assert second_call_args['group.id'] == "test-group - topic-key2"

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    def test_read_close_functionality(self, mock_consumer_class):
        """Test that close method properly closes all consumers."""
        # Arrange
        mock_consumer1 = Mock(spec=Consumer)
        mock_consumer2 = Mock(spec=Consumer)
        mock_consumer_class.side_effect = [mock_consumer1, mock_consumer2]
        
        mock_consumer1.consume.return_value = []
        mock_consumer1.assignment.return_value = [Mock()]
        mock_consumer2.consume.return_value = []
        mock_consumer2.assignment.return_value = [Mock()]
        
        def mock_get_topic(key):
            return f"topic-{key}"
        
        self.mock_config.get_kafka_topic.side_effect = mock_get_topic
        
        source = SourceKafka(self.mock_config)
        
        # Mock the _wait_for_assignment to properly set up the consumers
        consumers_to_store = [mock_consumer1, mock_consumer2]
        call_count = 0
        
        def mock_wait_for_assignment(consumer, group_id, *args, **kwargs):
            nonlocal call_count
            # Store the consumer in _consumers as the real method would
            source._consumers[group_id] = consumers_to_store[call_count]
            call_count += 1
            return True
        
        source._wait_for_assignment = Mock(side_effect=mock_wait_for_assignment)
        
        # Create two consumers
        source.read("key1")
        source.read("key2")
        
        # Act
        source.close()
        
        # Assert
        mock_consumer1.close.assert_called_once()
        mock_consumer2.close.assert_called_once()


class TestSourceKafkaCloseConsumerTimeout:
    """Test suite for _close_consumer_with_timeout method edge cases."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_kafka_config = Mock(spec=AppKafkaConfig)
        self.mock_consumer_config = Mock(spec=KafkaConsumerConfig)

        self.mock_kafka_config.brokers = "localhost:9092"
        self.mock_kafka_config.group = "test-group"
        self.mock_kafka_config.consumer = self.mock_consumer_config

        self.mock_config.kafka = self.mock_kafka_config
        self.mock_config.get_kafka_topic = Mock(return_value="test-topic")

    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_close_consumer_returns_true_on_success(self, mock_logger):
        """A clean close returns True so the caller can proceed with retry."""
        mock_consumer = Mock(spec=Consumer)
        source = SourceKafka(self.mock_config)

        result = source._close_consumer_with_timeout(mock_consumer)

        assert result is True
        mock_consumer.unsubscribe.assert_called_once()
        mock_consumer.close.assert_called_once()

    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_close_consumer_with_exception_during_close(self, mock_logger):
        """Inner-thread exceptions are caught and logged; the caller still sees True
        because the worker terminated within the timeout."""
        mock_consumer = Mock(spec=Consumer)
        mock_consumer.unsubscribe.side_effect = Exception("Unsubscribe failed")

        source = SourceKafka(self.mock_config)

        result = source._close_consumer_with_timeout(mock_consumer)

        assert result is True
        mock_consumer.unsubscribe.assert_called_once()
        mock_logger.warning.assert_called()
        assert "Failed to close consumer" in str(mock_logger.warning.call_args)

    @patch('mdx.analytics.core.stream.source.source_kafka.ThreadPoolExecutor')
    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_close_consumer_returns_false_on_timeout(self, mock_logger, mock_executor_class):
        """When close hangs past the timeout the wrapper returns False, signalling
        callers to abort retry rather than leak more threads."""
        from concurrent.futures import TimeoutError as FutureTimeoutError

        mock_consumer = Mock(spec=Consumer)
        mock_executor = Mock()
        mock_future = Mock()

        mock_executor_class.return_value = mock_executor
        mock_executor.submit.return_value = mock_future
        mock_future.result.side_effect = FutureTimeoutError()

        source = SourceKafka(self.mock_config)

        result = source._close_consumer_with_timeout(mock_consumer, timeout=2.0)

        assert result is False
        mock_executor.shutdown.assert_called_once_with(wait=False)
        mock_logger.warning.assert_called()
        assert "close timed out after 2.0 seconds" in str(mock_logger.warning.call_args)


class TestSourceKafkaOnAssignCallback:
    """Test suite for _on_assign callback functionality."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_kafka_config = Mock(spec=AppKafkaConfig)
        self.mock_consumer_config = Mock(spec=KafkaConsumerConfig)
        
        self.mock_kafka_config.brokers = "localhost:9092"
        self.mock_kafka_config.group = "test-group"
        self.mock_kafka_config.consumer = self.mock_consumer_config
        
        self.mock_config.kafka = self.mock_kafka_config
        self.mock_config.get_kafka_topic = Mock(return_value="test-topic")

    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_on_assign_callback_stores_consumer_and_logs(self, mock_logger):
        """Test that _on_assign callback stores consumer and logs assignment."""
        # Arrange
        source = SourceKafka(self.mock_config)
        mock_consumer = Mock(spec=Consumer)
        mock_partitions = [Mock(), Mock()]
        group_id = "test-group-123"
        
        # Get the callback function
        callback = source._on_assign(group_id)
        
        # Act
        callback(mock_consumer, mock_partitions)
        
        # Assert
        assert source._consumers[group_id] == mock_consumer
        mock_logger.info.assert_called_once()
        log_call_args = str(mock_logger.info.call_args)
        assert "Kafka consumer created and partitions assigned" in log_call_args
        assert group_id in log_call_args


class TestSourceKafkaWaitForAssignmentEdgeCases:
    """Test suite for _wait_for_assignment method edge cases."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_kafka_config = Mock(spec=AppKafkaConfig)
        self.mock_consumer_config = Mock(spec=KafkaConsumerConfig)
        
        self.mock_kafka_config.brokers = "localhost:9092"
        self.mock_kafka_config.group = "test-group"
        self.mock_kafka_config.consumer = self.mock_consumer_config
        
        self.mock_config.kafka = self.mock_kafka_config
        self.mock_config.get_kafka_topic = Mock(return_value="test-topic")

    def test_wait_for_assignment_timeout_after_max_attempts(self):
        """Test _wait_for_assignment returns False after max attempts."""
        # Arrange
        source = SourceKafka(self.mock_config)
        mock_consumer = Mock(spec=Consumer)
        group_id = "test-group-456"
        
        # Act
        result = source._wait_for_assignment(mock_consumer, group_id, max_attempts=3, interval_sec=0.01)
        
        # Assert
        assert result is False
        assert mock_consumer.poll.call_count == 3
        assert group_id not in source._consumers

    def test_wait_for_assignment_success_after_multiple_polls(self):
        """Test _wait_for_assignment succeeds after multiple polling attempts."""
        # Arrange
        source = SourceKafka(self.mock_config)
        mock_consumer = Mock(spec=Consumer)
        group_id = "test-group-789"
        
        # Simulate consumer assignment after 2nd poll
        def side_effect_poll(*args):
            if mock_consumer.poll.call_count == 2:
                source._consumers[group_id] = mock_consumer
                
        mock_consumer.poll.side_effect = side_effect_poll
        
        # Act
        result = source._wait_for_assignment(mock_consumer, group_id, max_attempts=5, interval_sec=0.01)
        
        # Assert
        assert result is True
        assert mock_consumer.poll.call_count == 2
        assert source._consumers[group_id] == mock_consumer


class TestSourceKafkaGetConsumerExceptionHandling:
    """Test suite for _get_consumer method exception handling."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.mock_config = Mock(spec=AppConfig)
        self.mock_kafka_config = Mock(spec=AppKafkaConfig)
        self.mock_consumer_config = Mock(spec=KafkaConsumerConfig)

        self.mock_kafka_config.brokers = "localhost:9092"
        self.mock_kafka_config.group = "test-group"
        self.mock_kafka_config.consumer = self.mock_consumer_config
        self.mock_consumer_config.autoOffsetReset = "latest"
        self.mock_consumer_config.enableAutoCommit = False
        self.mock_consumer_config.maxPollIntervalMs = 900000
        self.mock_consumer_config.fetchMaxBytes = 52428800
        self.mock_consumer_config.maxPartitionFetchBytes = 10485760
        self.mock_consumer_config.retryMaxAttempts = 3
        self.mock_consumer_config.retryIntervalSec = 30.0

        self.mock_config.kafka = self.mock_kafka_config
        self.mock_config.get_kafka_topic = Mock(return_value="test-topic")

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    @patch('mdx.analytics.core.stream.source.source_kafka.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_get_consumer_exception_during_creation_with_retry(self, mock_logger, mock_sleep, mock_consumer_class):
        """Test exception handling during consumer creation with retry."""
        # Arrange
        mock_consumer1 = Mock(spec=Consumer)
        mock_consumer2 = Mock(spec=Consumer)
        mock_consumer3 = Mock(spec=Consumer)  # In case more attempts are needed

        # First consumer creation fails, second succeeds
        mock_consumer_class.side_effect = [mock_consumer1, mock_consumer2, mock_consumer3]
        mock_consumer1.subscribe.side_effect = Exception("Subscribe failed")
        mock_consumer2.subscribe.return_value = None

        source = SourceKafka(self.mock_config)
        source._wait_for_assignment = Mock(side_effect=[False, True])
        # Close succeeds (broker reachable) so retry proceeds.
        source._close_consumer_with_timeout = Mock(return_value=True)

        # Act
        result = source._get_consumer("test-topic", "test-group-id")

        # Assert
        assert result is not None  # Should return a consumer
        assert mock_consumer_class.call_count >= 2  # At least 2 attempts
        source._close_consumer_with_timeout.assert_called()  # Should be called for the failed consumer
        mock_logger.warning.assert_called()
        assert "Error during consumer creation attempt" in str(mock_logger.warning.call_args)
        mock_sleep.assert_called_with(30.0)

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    @patch('mdx.analytics.core.stream.source.source_kafka.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_get_consumer_aborts_retry_when_close_hangs(self, mock_logger, mock_sleep, mock_consumer_class):
        """If close() hangs past the timeout the broker is unreachable; abort retry
        immediately rather than leaking another thread on the next attempt."""
        mock_consumer1 = Mock(spec=Consumer)
        mock_consumer_class.return_value = mock_consumer1

        source = SourceKafka(self.mock_config)
        source._wait_for_assignment = Mock(return_value=False)
        # First close hangs → wrapper returns False → must raise without retrying.
        source._close_consumer_with_timeout = Mock(return_value=False)

        with pytest.raises(RuntimeError, match="close hung on attempt 1"):
            source._get_consumer("test-topic", "test-group-id")

        # Only one attempt — no sleep, no second consumer creation.
        assert mock_consumer_class.call_count == 1
        source._close_consumer_with_timeout.assert_called_once_with(mock_consumer1)
        mock_sleep.assert_not_called()

    @patch('mdx.analytics.core.stream.source.source_kafka.Consumer')
    @patch('mdx.analytics.core.stream.source.source_kafka.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_kafka.logger')
    def test_get_consumer_uses_configurable_retry_settings(self, mock_logger, mock_sleep, mock_consumer_class):
        """retryMaxAttempts and retryIntervalSec drive
        the loop instead of the previous hardcoded 3 / 30."""
        self.mock_consumer_config.retryMaxAttempts = 5
        self.mock_consumer_config.retryIntervalSec = 1.5

        mock_consumers = [Mock(spec=Consumer) for _ in range(5)]
        mock_consumer_class.side_effect = mock_consumers

        source = SourceKafka(self.mock_config)
        source._wait_for_assignment = Mock(return_value=False)
        source._close_consumer_with_timeout = Mock(return_value=True)

        with pytest.raises(RuntimeError, match="Consumer initialization failed"):
            source._get_consumer("test-topic", "test-group-id")

        assert mock_consumer_class.call_count == 5
        # Sleep happens between attempts (4 times for 5 attempts), each at 1.5s.
        assert mock_sleep.call_count == 4
        mock_sleep.assert_called_with(1.5)
