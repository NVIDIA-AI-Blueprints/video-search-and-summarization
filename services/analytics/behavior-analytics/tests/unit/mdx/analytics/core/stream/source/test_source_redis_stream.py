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

import json
import pytest
from unittest.mock import Mock, patch

from mdx.analytics.core.stream.source.source_redis_stream import SourceRedisStream
from mdx.analytics.core.schema.config import AppConfig, AppRedisStreamConfig, RedisStreamConsumerConfig
from mdx.analytics.core.schema.models import StreamMessage


class TestSourceRedisStreamReadFunctionality:
    """Test suite for the SourceRedisStream read method functionality."""

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        self.mock_redis_config = Mock(spec=AppRedisStreamConfig)
        self.mock_consumer_config = Mock(spec=RedisStreamConsumerConfig)
        
        # Configure mock config
        self.mock_redis_config.host = "localhost"
        self.mock_redis_config.port = 6379
        self.mock_redis_config.db = 0
        self.mock_redis_config.group = "test-group"
        self.mock_redis_config.consumer = self.mock_consumer_config
        self.mock_consumer_config.readBlockMs = 1000
        self.mock_consumer_config.readCount = 10
        self.mock_consumer_config.mkstream = True
        self.mock_consumer_config.retryMaxAttempts = 3
        self.mock_consumer_config.retryIntervalSec = 30.0

        self.mock_config.redisStream = self.mock_redis_config
        self.mock_config.get_redis_stream = Mock(return_value="test-stream")

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_returns_stream_messages_from_redis_response(self, mock_redis_class):
        """Test that read method returns StreamMessage objects from Redis response."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        
        # Mock Redis response
        mock_redis_response = [
            ('test-stream', [
                (b'1234567890-0', {
                    b'value': b'{"test": "data"}',
                    b'key': b'test-key',
                    b'headers': b'{"content-type": "application/json"}'
                }),
                (b'1234567891-0', {
                    b'value': b'{"test": "data2"}',
                    b'key': b'test-key2',
                    b'headers': b'{"timestamp": "2023-01-01"}'
                })
            ])
        ]
        mock_redis_instance.xreadgroup.return_value = mock_redis_response
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        mock_redis_instance.xack.return_value = 2
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 2
        assert all(isinstance(msg, StreamMessage) for msg in result)
        
        # Verify first message
        assert result[0].key == b'test-key'
        assert result[0].value == b'{"test": "data"}'
        assert result[0].headers == {"content-type": b"application/json"}
        
        # Verify second message
        assert result[1].key == b'test-key2'
        assert result[1].value == b'{"test": "data2"}'
        assert result[1].headers == {"timestamp": b"2023-01-01"}

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_calls_redis_xreadgroup_with_correct_parameters(self, mock_redis_class):
        """Test that read method calls xreadgroup with correct parameters."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        source.read("test-key")
        
        # Assert
        mock_redis_instance.xreadgroup.assert_called_once_with(
            groupname="test-group - test-stream",
            consumername=source._consumer_name,
            block=1000,
            count=10,
            streams={'test-stream': '>'}
        )

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_calls_redis_xack_with_message_ids(self, mock_redis_class):
        """Test that read method calls xack with correct message IDs."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        
        mock_redis_response = [
            ('test-stream', [
                (b'1234567890-0', {b'value': b'data1', b'key': b'key1', b'headers': b'{}'}),
                (b'1234567891-0', {b'value': b'data2', b'key': b'key2', b'headers': b'{}'})
            ])
        ]
        mock_redis_instance.xreadgroup.return_value = mock_redis_response
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        mock_redis_instance.xack.return_value = 2
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        source.read("test-key")
        
        # Assert
        mock_redis_instance.xack.assert_called_once_with(
            'test-stream',
            "test-group - test-stream",
            b'1234567890-0',
            b'1234567891-0'
        )

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_creates_consumer_group_if_not_exists(self, mock_redis_class):
        """Test that read method creates consumer group if it doesn't exist."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        source.read("test-key")
        
        # Assert
        mock_redis_instance.xgroup_create.assert_called_once_with(
            'test-stream',
            "test-group - test-stream",
            mkstream=True
        )
        mock_redis_instance.xgroup_createconsumer.assert_called_once_with(
            'test-stream',
            "test-group - test-stream",
            source._consumer_name
        )

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_handles_empty_redis_response(self, mock_redis_class):
        """Test that read method handles empty Redis response correctly."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert result == []
        mock_redis_instance.xack.assert_not_called()

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_handles_none_redis_response(self, mock_redis_class):
        """Test that read method handles None Redis response correctly."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = None
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert result == []
        mock_redis_instance.xack.assert_not_called()

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_handles_missing_message_fields(self, mock_redis_class):
        """Test that read method handles missing message fields correctly."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        
        # Mock Redis response with missing fields
        mock_redis_response = [
            ('test-stream', [
                (b'1234567890-0', {b'value': b'data1'}),  # Missing key and headers
                (b'1234567891-0', {b'key': b'key2', b'headers': b'{}'}),  # Missing message
                (b'1234567892-0', {b'value': b'data3', b'key': b'key3'})  # Missing headers
            ])
        ]
        mock_redis_instance.xreadgroup.return_value = mock_redis_response
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        mock_redis_instance.xack.return_value = 3
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        result = source.read("test-key")
        
        # Assert
        assert len(result) == 2  # Only messages with 'value' field should be processed
        assert result[0].key is None
        assert result[0].value == b'data1'
        assert result[0].headers == {}
        assert result[1].key == b'key3'
        assert result[1].value == b'data3'
        assert result[1].headers == {}

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_handles_invalid_headers_json(self, mock_redis_class):
        """Test that read method handles invalid headers JSON correctly."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        
        mock_redis_response = [
            ('test-stream', [
                (b'1234567890-0', {
                    b'value': b'data1',
                    b'key': b'key1',
                    b'headers': b'invalid-json'
                })
            ])
        ]
        mock_redis_instance.xreadgroup.return_value = mock_redis_response
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        mock_redis_instance.xack.return_value = 1
        
        source = SourceRedisStream(self.mock_config)
        
        # Act & Assert
        with pytest.raises(json.JSONDecodeError):
            source.read("test-key")

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_with_group_id_suffix(self, mock_redis_class):
        """Test that read method uses group_id_suffix correctly."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        source.read("test-key", group_id_suffix="custom-suffix")
        
        # Assert
        mock_redis_instance.xreadgroup.assert_called_once_with(
            groupname="test-group - test-stream - custom-suffix",
            consumername=source._consumer_name,
            block=1000,
            count=10,
            streams={'test-stream': '>'}
        )

    def test_read_raises_error_for_invalid_src_key(self):
        """Test that read method raises ValueError for invalid src_key."""
        # Arrange
        self.mock_config.get_redis_stream.return_value = None
        source = SourceRedisStream(self.mock_config)
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a redis stream with key: invalid-key"):
            source.read("invalid-key")

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_reuses_existing_consumer_connection(self, mock_redis_class):
        """Test that read method reuses existing Redis connection and consumer group."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        source = SourceRedisStream(self.mock_config)
        
        # Act - call read twice
        source.read("test-key")
        source.read("test-key")
        
        # Assert - Redis should be instantiated only once
        mock_redis_class.assert_called_once()
        # Consumer group should be created only once
        mock_redis_instance.xgroup_create.assert_called_once()
        mock_redis_instance.xgroup_createconsumer.assert_called_once()

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_handles_redis_response_error_for_existing_group(self, mock_redis_class):
        """Test that read method handles BUSYGROUP error when consumer group already exists."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        # Mock BUSYGROUP error
        import redis
        mock_redis_instance.xgroup_create.side_effect = redis.ResponseError("BUSYGROUP Consumer Group name already exists")
        
        source = SourceRedisStream(self.mock_config)
        
        # Act - should not raise error
        result = source.read("test-key")
        
        # Assert
        assert result == []
        mock_redis_instance.xgroup_createconsumer.assert_called_once()

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_propagates_non_busygroup_redis_errors(self, mock_redis_class):
        """Test that read method propagates non-BUSYGROUP Redis errors."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        
        # Mock a different Redis error
        import redis
        mock_redis_instance.xgroup_create.side_effect = redis.ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        
        source = SourceRedisStream(self.mock_config)
        
        # Act & Assert
        with pytest.raises(redis.ResponseError, match="WRONGTYPE"):
            source.read("test-key")

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_creates_redis_connection_with_correct_parameters(self, mock_redis_class):
        """Test that read method creates Redis connection with correct parameters."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        # Update config with specific values
        self.mock_redis_config.host = "redis-host"
        self.mock_redis_config.port = 6380
        self.mock_redis_config.db = 5
        
        source = SourceRedisStream(self.mock_config)
        
        # Act
        source.read("test-key")
        
        # Assert
        mock_redis_class.assert_called_once_with(
            host="redis-host",
            port=6380,
            db=5
        )

    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_read_handles_none_db_config(self, mock_redis_class):
        """Test that read method handles None db config correctly."""
        # Arrange
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xreadgroup.return_value = []
        mock_redis_instance.xgroup_create.return_value = True
        mock_redis_instance.xgroup_createconsumer.return_value = True
        
        # Set db to None
        self.mock_redis_config.db = None

        source = SourceRedisStream(self.mock_config)

        # Act
        source.read("test-key")

        # Assert
        mock_redis_class.assert_called_once_with(
            host="localhost",
            port=6379,
            db=0  # Should default to 0 when db is None
        )


class TestSourceRedisStreamRetryBehavior:
    """Test suite for Redis consumer-group setup retry on transient errors."""

    def setup_method(self):
        self.mock_config = Mock(spec=AppConfig)
        self.mock_redis_config = Mock(spec=AppRedisStreamConfig)
        self.mock_consumer_config = Mock(spec=RedisStreamConsumerConfig)

        self.mock_redis_config.host = "localhost"
        self.mock_redis_config.port = 6379
        self.mock_redis_config.db = 0
        self.mock_redis_config.group = "test-group"
        self.mock_redis_config.consumer = self.mock_consumer_config
        self.mock_consumer_config.readBlockMs = 1000
        self.mock_consumer_config.readCount = 10
        self.mock_consumer_config.mkstream = True
        self.mock_consumer_config.retryMaxAttempts = 3
        self.mock_consumer_config.retryIntervalSec = 0.01  # tiny so tests are fast

        self.mock_config.redisStream = self.mock_redis_config
        self.mock_config.get_redis_stream = Mock(return_value="test-stream")

    @patch('mdx.analytics.core.stream.source.source_redis_stream.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_get_consumer_retries_on_connection_error_then_succeeds(self, mock_redis_class, mock_sleep):
        """Two ConnectionErrors then success → returns the consumer after retry."""
        import redis as redis_mod
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xgroup_create.side_effect = [
            redis_mod.ConnectionError("connection refused"),
            redis_mod.ConnectionError("connection refused"),
            True,  # third attempt succeeds
        ]
        mock_redis_instance.xgroup_createconsumer.return_value = True
        mock_redis_instance.xreadgroup.return_value = []

        source = SourceRedisStream(self.mock_config)

        # Should succeed without raising
        source.read("test-key")

        assert mock_redis_instance.xgroup_create.call_count == 3
        # 2 sleeps between 3 attempts
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.01)

    @patch('mdx.analytics.core.stream.source.source_redis_stream.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_get_consumer_raises_after_max_attempts(self, mock_redis_class, mock_sleep):
        """All attempts fail with ConnectionError → raises RuntimeError, no propagation of redis.ConnectionError."""
        import redis as redis_mod
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xgroup_create.side_effect = redis_mod.ConnectionError("broker down")

        source = SourceRedisStream(self.mock_config)

        with pytest.raises(RuntimeError, match="Failed to set up Redis consumer group after 3 attempts"):
            source.read("test-key")

        assert mock_redis_instance.xgroup_create.call_count == 3
        assert mock_sleep.call_count == 2  # one fewer than attempts

    @patch('mdx.analytics.core.stream.source.source_redis_stream.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_get_consumer_does_not_retry_on_busygroup(self, mock_redis_class, mock_sleep):
        """BUSYGROUP means the group already exists — proceed without retry."""
        import redis as redis_mod
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xgroup_create.side_effect = redis_mod.ResponseError("BUSYGROUP Consumer Group name already exists")
        mock_redis_instance.xgroup_createconsumer.return_value = True
        mock_redis_instance.xreadgroup.return_value = []

        source = SourceRedisStream(self.mock_config)
        source.read("test-key")

        assert mock_redis_instance.xgroup_create.call_count == 1
        mock_sleep.assert_not_called()

    @patch('mdx.analytics.core.stream.source.source_redis_stream.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_redis_stream.redis.Redis')
    def test_get_consumer_does_not_retry_on_other_response_error(self, mock_redis_class, mock_sleep):
        """Non-BUSYGROUP ResponseError is a config/permissions issue — propagate, don't retry."""
        import redis as redis_mod
        mock_redis_instance = Mock()
        mock_redis_class.return_value = mock_redis_instance
        mock_redis_instance.xgroup_create.side_effect = redis_mod.ResponseError("WRONGTYPE")

        source = SourceRedisStream(self.mock_config)

        with pytest.raises(redis_mod.ResponseError, match="WRONGTYPE"):
            source.read("test-key")

        assert mock_redis_instance.xgroup_create.call_count == 1
        mock_sleep.assert_not_called()
