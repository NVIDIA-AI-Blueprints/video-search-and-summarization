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
from unittest.mock import Mock
import google.protobuf.message as pbm

from mdx.analytics.core.stream.source.source_base import (
    Source,
    BytesStrDeserializer,
    BytesMessageProtoDeserializer,
    StreamMessageProtoDeserializer
)
from mdx.analytics.core.schema.models import StreamMessage


class MockSource(Source):
    """Mock concrete implementation of Source for testing purposes."""
    
    def __init__(self, mock_messages=None):
        self.mock_messages = mock_messages or []
        self.read_calls = []
    
    def read(self, src_key: str, group_id_suffix: str | None = None) -> list[StreamMessage]:
        """Mock implementation that returns predefined messages."""
        self.read_calls.append((src_key, group_id_suffix))
        return self.mock_messages


class MockProtobufMessage(pbm.Message):
    """Mock protobuf message for testing."""
    
    def __init__(self, data="test_data"):
        super().__init__()
        self.data = data
    
    @classmethod
    def FromString(cls, data: bytes | str):
        """Mock FromString method."""
        return cls(data.decode('utf-8') if isinstance(data, bytes) else data)  # type: ignore


class TestPollFunctionality:
    """Test suite for the poll method functionality."""
    
    def test_poll_calls_read_with_correct_parameters(self):
        """Test that poll calls read with the correct parameters."""
        source = MockSource()
        deserializer = Mock(return_value="deserialized")
        
        result = source.poll("test_key", deserializer, "test_suffix")
        
        # Verify read was called with correct parameters
        assert len(source.read_calls) == 1
        assert source.read_calls[0] == ("test_key", "test_suffix")
        assert result == []
    
    def test_poll_calls_read_without_group_suffix(self):
        """Test that poll calls read without group_id_suffix when not provided."""
        source = MockSource()
        deserializer = Mock(return_value="deserialized")
        
        result = source.poll("test_key", deserializer)
        
        # Verify read was called with None for group_id_suffix
        assert len(source.read_calls) == 1
        assert source.read_calls[0] == ("test_key", None)
    
    def test_poll_applies_deserializer_to_single_message(self):
        """Test that poll applies deserializer to a single message."""
        test_message = StreamMessage(key="test_key", value="test_value")
        source = MockSource([test_message])
        deserializer = Mock(return_value="deserialized_message")
        
        result = source.poll("test_key", deserializer)
        
        # Verify deserializer was called with the message
        deserializer.assert_called_once_with(test_message)
        assert result == ["deserialized_message"]
    
    def test_poll_applies_deserializer_to_multiple_messages(self):
        """Test that poll applies deserializer to multiple messages."""
        test_messages = [
            StreamMessage(key="key1", value="value1"),
            StreamMessage(key="key2", value="value2"),
            StreamMessage(key="key3", value="value3")
        ]
        source = MockSource(test_messages)
        deserializer = Mock(side_effect=lambda x: f"deserialized_{x.value}")
        
        result = source.poll("test_key", deserializer)
        
        # Verify deserializer was called for each message
        assert deserializer.call_count == 3
        assert result == ["deserialized_value1", "deserialized_value2", "deserialized_value3"]
    
    def test_poll_handles_empty_message_list(self):
        """Test that poll handles empty message list correctly."""
        source = MockSource([])
        deserializer = Mock()
        
        result = source.poll("test_key", deserializer)
        
        # Verify deserializer was never called and result is empty
        deserializer.assert_not_called()
        assert result == []
    
    def test_poll_with_bytes_str_deserializer(self):
        """Test poll with BytesStrDeserializer."""
        test_message = b"test_bytes"
        source = MockSource([test_message])
        
        result = source.poll("test_key", BytesStrDeserializer)
        
        # BytesStrDeserializer should decode bytes to string
        assert result == ["test_bytes"]
    
    def test_poll_with_bytes_message_proto_deserializer(self):
        """Test poll with BytesMessageProtoDeserializer."""
        test_message = b"test_proto_data"
        source = MockSource([test_message])
        
        # Create deserializer for mock protobuf type
        deserializer = BytesMessageProtoDeserializer(MockProtobufMessage)
        
        result = source.poll("test_key", deserializer)
        
        # Should return protobuf message object
        assert len(result) == 1
        assert isinstance(result[0], MockProtobufMessage)
        assert result[0].data == "test_proto_data"
    
    def test_poll_with_stream_message_proto_deserializer(self):
        """Test poll with StreamMessageProtoDeserializer."""
        test_message = StreamMessage(key="test_key", value=b"test_stream_data")
        source = MockSource([test_message])
        
        # Create deserializer for mock protobuf type
        deserializer = StreamMessageProtoDeserializer(MockProtobufMessage)
        
        result = source.poll("test_key", deserializer)
        
        # Should return protobuf message object
        assert len(result) == 1
        assert isinstance(result[0], MockProtobufMessage)
        assert result[0].data == "test_stream_data"
    
    def test_poll_with_stream_message_proto_deserializer_none_value(self):
        """Test poll with StreamMessageProtoDeserializer when value is None."""
        test_message = StreamMessage(key="test_key", value=None)
        source = MockSource([test_message])
        
        deserializer = StreamMessageProtoDeserializer(MockProtobufMessage)
        
        result = source.poll("test_key", deserializer)
        
        # Should return None for None value
        assert result == [None]
    
    def test_poll_with_stream_message_proto_deserializer_string_value(self):
        """Test poll with StreamMessageProtoDeserializer when value is string."""
        test_message = StreamMessage(key="test_key", value="test_string_data")
        source = MockSource([test_message])
        
        deserializer = StreamMessageProtoDeserializer(MockProtobufMessage)
        
        result = source.poll("test_key", deserializer)
        
        # Should handle string values by encoding them
        assert len(result) == 1
        assert isinstance(result[0], MockProtobufMessage)
        assert result[0].data == "test_string_data"
    
    def test_poll_with_custom_deserializer(self):
        """Test poll with custom deserializer function."""
        test_messages = [
            StreamMessage(key="key1", value="value1"),
            StreamMessage(key="key2", value="value2")
        ]
        source = MockSource(test_messages)
        
        # Custom deserializer that extracts key and value
        def custom_deserializer(msg):
            return {"key": msg.key, "value": msg.value}
        
        result = source.poll("test_key", custom_deserializer)
        
        expected = [
            {"key": "key1", "value": "value1"},
            {"key": "key2", "value": "value2"}
        ]
        assert result == expected
    
    def test_poll_deserializer_exception_propagates(self):
        """Test that exceptions from deserializer are propagated."""
        test_message = StreamMessage(key="test_key", value="test_value")
        source = MockSource([test_message])
        
        def failing_deserializer(msg):
            raise ValueError("Deserialization failed")
        
        with pytest.raises(ValueError, match="Deserialization failed"):
            source.poll("test_key", failing_deserializer)
    
    def test_poll_with_lambda_deserializer(self):
        """Test poll with lambda deserializer."""
        test_messages = [
            StreamMessage(key="key1", value="hello"),
            StreamMessage(key="key2", value="world")
        ]
        source = MockSource(test_messages)
        
        result = source.poll("test_key", lambda msg: msg.value.upper())
        
        assert result == ["HELLO", "WORLD"]
    
    def test_poll_with_large_message_batch(self):
        """Test poll with a large number of messages."""
        # Create 1000 test messages
        test_messages = [
            StreamMessage(key=f"key{i}", value=f"value{i}") 
            for i in range(1000)
        ]
        source = MockSource(test_messages)
        
        result = source.poll("test_key", lambda msg: msg.value)
        
        # Verify all messages were processed
        assert len(result) == 1000
        assert result[0] == "value0"
        assert result[999] == "value999"
    
    def test_bytes_message_proto_deserializer_with_empty_bytes(self):
        """Test BytesMessageProtoDeserializer with empty bytes."""
        deserializer = BytesMessageProtoDeserializer(MockProtobufMessage)
        
        result = deserializer(b"")
        
        assert result is None
