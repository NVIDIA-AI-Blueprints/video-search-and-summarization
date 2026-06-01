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
import json

from mdx.analytics.core.stream.sink.sink_redis_stream import SinkRedisStream
from mdx.analytics.core.schema.config import AppConfig, AppRedisStreamConfig, RedisStreamProducerConfig


class TestSinkRedisStreamFunctionality:
    """
    Functionality tests for SinkRedisStream class.
    
    This test suite covers the core functionality of the write and write_msg methods
    including message serialization, key extraction, headers processing, and Redis interactions.
    """

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        self.mock_redis_config = Mock(spec=AppRedisStreamConfig)
        self.mock_producer_config = Mock(spec=RedisStreamProducerConfig)
        
        # Configure mock config
        self.mock_config.redisStream = self.mock_redis_config
        self.mock_redis_config.host = "localhost"
        self.mock_redis_config.port = 6379
        self.mock_redis_config.db = 0
        self.mock_redis_config.producer = self.mock_producer_config
        self.mock_producer_config.maxLen = 10000
        
        # Mock get_redis_stream method
        self.mock_config.get_redis_stream = Mock(return_value="test-stream")
        
        # Create SinkRedisStream instance
        self.sink = SinkRedisStream(self.mock_config)

    @pytest.fixture
    def mock_redis_client(self):
        """Fixture providing a mocked Redis client instance."""
        redis_client = Mock()
        redis_client.xadd = Mock()
        redis_client.close = Mock()
        return redis_client

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_basic_functionality(self, mock_redis_class, mock_redis_client):
        """Test basic write functionality with simple messages."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        messages = [{"id": 1, "name": "test1"}, {"id": 2, "name": "test2"}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        self.mock_config.get_redis_stream.assert_called_once_with(dest_key)
        assert mock_redis_client.xadd.call_count == 2
        
        # Check first message
        first_call = mock_redis_client.xadd.call_args_list[0]
        assert first_call[1]['name'] == 'test-stream'
        assert first_call[1]['fields']['key'] == b''
        assert first_call[1]['fields']['value'] == b'{"id": 1, "name": "test1"}'
        assert first_call[1]['fields']['headers'] == '{}'
        assert first_call[1]['maxlen'] == 10000
        assert first_call[1]['approximate'] == True
        
        # Check second message
        second_call = mock_redis_client.xadd.call_args_list[1]
        assert second_call[1]['name'] == 'test-stream'
        assert second_call[1]['fields']['key'] == b''
        assert second_call[1]['fields']['value'] == b'{"id": 2, "name": "test2"}'

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_with_key_extractor_and_serializer(self, mock_redis_class, mock_redis_client):
        """Test write functionality with key extraction and serialization."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        messages = [{"id": 1, "name": "test1"}, {"id": 2, "name": "test2"}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        key_extractor = lambda x: x["id"]
        key_serializer = lambda x: f"key_{x}".encode()
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, key_extractor, key_serializer)
        
        # Assert
        assert mock_redis_client.xadd.call_count == 2
        
        # Check first message key
        first_call = mock_redis_client.xadd.call_args_list[0]
        assert first_call[1]['fields']['key'] == b"key_1"
        
        # Check second message key
        second_call = mock_redis_client.xadd.call_args_list[1]
        assert second_call[1]['fields']['key'] == b"key_2"

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_with_headers(self, mock_redis_class, mock_redis_client):
        """Test write functionality with custom headers."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        messages = [{"id": 1, "name": "test1"}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        headers = {"content-type": "application/json", "version": b"1.0"}
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, headers=headers)
        
        # Assert
        call_args = mock_redis_client.xadd.call_args_list[0]
        expected_headers = {"content-type": "application/json", "version": "1.0"}
        assert call_args[1]['fields']['headers'] == json.dumps(expected_headers)

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_msg_basic_functionality(self, mock_redis_class, mock_redis_client):
        """Test basic write_msg functionality."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        message = b"test message"
        key = b"test_key"
        
        # Act
        self.sink.write_msg(dest_key, message, key)
        
        # Assert
        self.mock_config.get_redis_stream.assert_called_once_with(dest_key)
        mock_redis_client.xadd.assert_called_once_with(
            name='test-stream',
            fields={
                'key': key,
                'value': message,
                'headers': '{}'
            },
            maxlen=10000,
            approximate=True
        )

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_msg_with_headers(self, mock_redis_class, mock_redis_client):
        """Test write_msg functionality with custom headers."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        message = b"test message"
        key = b"test_key"
        headers = {"content-type": "application/json", "version": b"1.0"}
        
        # Act
        self.sink.write_msg(dest_key, message, key, headers)
        
        # Assert
        call_args = mock_redis_client.xadd.call_args
        expected_headers = {"content-type": "application/json", "version": "1.0"}
        assert call_args[1]['fields']['headers'] == json.dumps(expected_headers)

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_msg_with_none_key(self, mock_redis_class, mock_redis_client):
        """Test write_msg functionality with None key."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        message = b"test message"
        key = None
        
        # Act
        self.sink.write_msg(dest_key, message, key)
        
        # Assert
        call_args = mock_redis_client.xadd.call_args
        assert call_args[1]['fields']['key'] == b''

    @pytest.mark.parametrize("messages", [
        ([{"id": 1}]),
        ([{"id": 1}, {"id": 2}]),
        ([{"id": 1}, {"id": 2}, {"id": 3}]),
        ([{"id": i} for i in range(1, 11)])  # Test with 10 messages
    ])
    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_with_multiple_messages(self, mock_redis_class, mock_redis_client, messages):
        """Test write functionality with varying numbers of messages."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        assert mock_redis_client.xadd.call_count == len(messages)

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_stream_not_found_error(self, mock_redis_class, mock_redis_client):
        """Test write functionality when stream is not found."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        self.mock_config.get_redis_stream.return_value = None
        
        dest_key = "nonexistent"
        messages = [{"id": 1}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a redis stream with key: nonexistent"):
            self.sink.write(dest_key, messages, value_serializer)

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_msg_stream_not_found_error(self, mock_redis_class, mock_redis_client):
        """Test write_msg functionality when stream is not found."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        self.mock_config.get_redis_stream.return_value = None
        
        dest_key = "nonexistent"
        message = b"test message"
        key = b"test_key"
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a redis stream with key: nonexistent"):
            self.sink.write_msg(dest_key, message, key)

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_redis_connection_configuration(self, mock_redis_class, mock_redis_client):
        """Test that Redis connection is configured correctly."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        messages = [{"id": 1}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        mock_redis_class.assert_called_once_with(
            host="localhost",
            port=6379,
            db=0
        )

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_redis_connection_reuse(self, mock_redis_class, mock_redis_client):
        """Test that Redis connection is reused across multiple calls."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        dest_key = "output"
        messages = [{"id": 1}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        mock_redis_class.assert_called_once()  # Connection created only once

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_close_functionality(self, mock_redis_class, mock_redis_client):
        """Test close functionality properly closes Redis connection."""
        # Arrange
        mock_redis_class.return_value = mock_redis_client
        
        # Create connection first
        dest_key = "output"
        messages = [{"id": 1}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        self.sink.write(dest_key, messages, value_serializer)
        
        # Act
        self.sink.close()
        
        # Assert
        mock_redis_client.close.assert_called_once()

    def test_serialize_headers_with_mixed_types(self):
        """Test header serialization with mixed string and bytes types."""
        # Arrange
        headers = {
            "string_header": "value1",
            "bytes_header": b"value2",
            "mixed": "value3"
        }
        
        # Act
        result = self.sink._serialize_headers(headers)
        
        # Assert
        expected = {"string_header": "value1", "bytes_header": "value2", "mixed": "value3"}
        assert result == json.dumps(expected)

    def test_serialize_headers_empty(self):
        """Test header serialization with empty headers."""
        # Arrange
        headers = {}
        
        # Act
        result = self.sink._serialize_headers(headers)
        
        # Assert
        assert result == json.dumps({})

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_redis_xadd_error(self, mock_redis_class):
        """Test write functionality when Redis xadd fails."""
        # Arrange
        mock_redis_client = Mock()
        mock_redis_client.xadd.side_effect = Exception("Redis xadd failed")
        mock_redis_class.return_value = mock_redis_client
        self.mock_config.get_redis_stream = Mock(return_value="test-stream")
        
        dest_key = "output"
        messages = [{"id": 1}]
        value_serializer = lambda x: json.dumps(x).encode('utf-8')
        
        # Act & Assert
        with pytest.raises(Exception, match="Redis xadd failed"):
            self.sink.write(dest_key, messages, value_serializer)

    @patch('mdx.analytics.core.stream.sink.sink_redis_stream.redis.Redis')
    def test_write_msg_redis_xadd_error(self, mock_redis_class):
        """Test write_msg functionality when Redis xadd fails."""
        # Arrange
        mock_redis_client = Mock()
        mock_redis_client.xadd.side_effect = Exception("Redis xadd failed")
        mock_redis_class.return_value = mock_redis_client
        self.mock_config.get_redis_stream = Mock(return_value="test-stream")
        
        dest_key = "output"
        message = b"test message"
        key = b"test_key"
        
        # Act & Assert
        with pytest.raises(Exception, match="Redis xadd failed"):
            self.sink.write_msg(dest_key, message, key)
