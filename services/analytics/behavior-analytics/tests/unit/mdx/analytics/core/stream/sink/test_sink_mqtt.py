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

from mdx.analytics.core.stream.sink.sink_mqtt import SinkMQTT
from mdx.analytics.core.schema.config import AppConfig, AppMQTTConfig, MQTTProducerConfig
from paho.mqtt.client import Client, ConnectFlags, DisconnectFlags
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode


class TestSinkMQTTFunctionality:
    """
    Functionality tests for SinkMQTT class.
    
    This test suite covers the core functionality of the write and write_msg methods
    including message publishing, key handling, headers processing, and client interactions.
    """

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        self.mock_mqtt_config = Mock(spec=AppMQTTConfig)
        self.mock_producer_config = Mock(spec=MQTTProducerConfig)
        
        # Configure mock config
        self.mock_config.mqtt = self.mock_mqtt_config
        self.mock_mqtt_config.host = "localhost"
        self.mock_mqtt_config.port = 1883
        self.mock_mqtt_config.keepAliveSec = 60
        self.mock_mqtt_config.producer = self.mock_producer_config
        self.mock_producer_config.qos = 1
        self.mock_producer_config.retain = False
        
        # Mock get_mqtt_topic method
        self.mock_config.get_mqtt_topic = Mock(return_value="test/topic")
        
        # Create SinkMQTT instance
        self.sink = SinkMQTT(self.mock_config)

    @pytest.fixture
    def mock_client(self):
        """Fixture providing a mocked paho-mqtt Client instance."""
        client = Mock(spec=Client)
        client.publish.return_value = Mock()
        client.connect.return_value = None
        client.loop_start.return_value = None
        client.is_connected.return_value = True
        return client

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_single_message_basic_functionality(self, mock_client_class, mock_client):
        """Test write method with a single message and basic parameters."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [{"id": 1, "data": "test_message"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        self.mock_config.get_mqtt_topic.assert_called_once_with(dest_key)
        mock_client_class.assert_called_once()
        mock_client.connect.assert_called_once_with(
            host="localhost",
            port=1883,
            keepalive=60,
            clean_start=True
        )
        mock_client.loop_start.assert_called_once()
        mock_client.publish.assert_called_once()
        
        # Check publish call arguments
        publish_call = mock_client.publish.call_args
        assert publish_call.kwargs['topic'] == "test/topic"
        assert publish_call.kwargs['qos'] == 1
        assert publish_call.kwargs['retain'] == False

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_multiple_messages_functionality(self, mock_client_class, mock_client):
        """Test write method with multiple messages."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [
            {"id": 1, "data": "message_1"},
            {"id": 2, "data": "message_2"},
            {"id": 3, "data": "message_3"}
        ]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        assert mock_client.publish.call_count == 3
        
        # Verify each message was published with correct payload
        publish_calls = mock_client.publish.call_args_list
        for i, call in enumerate(publish_calls):
            expected_payload = str(messages[i]).encode('utf-8')
            assert call.kwargs['payload'] == expected_payload

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_with_key_extraction_and_serialization(self, mock_client_class, mock_client):
        """Test write method with key extraction and serialization."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [{"id": 1, "user_id": "user123", "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        key_extractor = lambda x: x["user_id"]
        key_serializer = lambda x: x.encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, key_extractor, key_serializer)
        
        # Assert
        mock_client.publish.assert_called_once()
        # Key should be included in properties (tested via _serialize_properties)

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_with_headers(self, mock_client_class, mock_client):
        """Test write method with custom headers."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [{"id": 1, "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        headers = {"source": "test_app", "version": "1.0"}
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, headers=headers)
        
        # Assert
        mock_client.publish.assert_called_once()
        # Headers should be included in properties

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_reuses_existing_client(self, mock_client_class, mock_client):
        """Test that write method reuses existing client connection."""
        # Arrange
        mock_client_class.return_value = mock_client
        self.sink._client = mock_client  # Set existing client
        dest_key = "test_key"
        messages = [{"id": 1, "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        # Should not create new client or call connect
        mock_client_class.assert_not_called()
        mock_client.connect.assert_not_called()
        mock_client.loop_start.assert_not_called()
        mock_client.publish.assert_called_once()

    def test_write_raises_value_error_for_invalid_topic(self):
        """Test write method raises ValueError when topic cannot be found."""
        # Arrange
        self.mock_config.get_mqtt_topic.return_value = None
        dest_key = "invalid_key"
        messages = [{"id": 1, "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a topic with key: invalid_key"):
            self.sink.write(dest_key, messages, value_serializer)

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_msg_basic_functionality(self, mock_client_class, mock_client):
        """Test write_msg method with basic parameters."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        message = b"test message payload"
        key = b"test_key"
        
        # Act
        self.sink.write_msg(dest_key, message, key)
        
        # Assert
        self.mock_config.get_mqtt_topic.assert_called_once_with(dest_key)
        mock_client_class.assert_called_once()
        mock_client.connect.assert_called_once()
        mock_client.loop_start.assert_called_once()
        mock_client.publish.assert_called_once()
        
        # Check publish call arguments
        publish_call = mock_client.publish.call_args
        assert publish_call.kwargs['topic'] == "test/topic"
        assert publish_call.kwargs['payload'] == message
        assert publish_call.kwargs['qos'] == 1
        assert publish_call.kwargs['retain'] == False

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_msg_with_headers(self, mock_client_class, mock_client):
        """Test write_msg method with custom headers."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        message = b"test message"
        key = b"test_key"
        headers = {"timestamp": "2025-01-01T00:00:00Z", "source": "device_123"}
        
        # Act
        self.sink.write_msg(dest_key, message, key, headers)
        
        # Assert
        mock_client.publish.assert_called_once()
        # Headers should be processed via _serialize_properties

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_msg_without_key(self, mock_client_class, mock_client):
        """Test write_msg method without message key."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        message = b"test message payload"
        
        # Act
        self.sink.write_msg(dest_key, message, None)
        
        # Assert
        mock_client.publish.assert_called_once()
        publish_call = mock_client.publish.call_args
        assert publish_call.kwargs['payload'] == message

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_msg_reuses_existing_client(self, mock_client_class, mock_client):
        """Test that write_msg method reuses existing client connection."""
        # Arrange
        mock_client_class.return_value = mock_client
        self.sink._client = mock_client  # Set existing client
        dest_key = "test_key"
        message = b"test message"
        key = b"test_key"
        
        # Act
        self.sink.write_msg(dest_key, message, key)
        
        # Assert
        # Should not create new client or call connect
        mock_client_class.assert_not_called()
        mock_client.connect.assert_not_called()
        mock_client.loop_start.assert_not_called()
        mock_client.publish.assert_called_once()

    def test_write_msg_raises_value_error_for_invalid_topic(self):
        """Test write_msg method raises ValueError when topic cannot be found."""
        # Arrange
        self.mock_config.get_mqtt_topic.return_value = None
        dest_key = "invalid_key"
        message = b"test message"
        key = b"test_key"
        
        # Act & Assert
        with pytest.raises(ValueError, match="Could not find a topic with key: invalid_key"):
            self.sink.write_msg(dest_key, message, key)

    @pytest.mark.parametrize("qos,retain", [
        (0, False),
        (1, True),
        (2, False),
        (2, True)
    ])
    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_respects_producer_config_settings(self, mock_client_class, mock_client, qos, retain):
        """Test that write methods respect QoS and retain settings from producer config."""
        # Arrange
        mock_client_class.return_value = mock_client
        self.mock_producer_config.qos = qos
        self.mock_producer_config.retain = retain
        
        dest_key = "test_key"
        messages = [{"id": 1, "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        publish_call = mock_client.publish.call_args
        assert publish_call.kwargs['qos'] == qos
        assert publish_call.kwargs['retain'] == retain

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_handles_empty_message_list(self, mock_client_class, mock_client):
        """Test write method with empty message list."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = []
        value_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer)
        
        # Assert
        # Should still initialize client but not publish any messages
        mock_client_class.assert_called_once()
        mock_client.publish.assert_not_called()

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_serialize_properties_with_key_and_headers(self, mock_client_class, mock_client):
        """Test that _serialize_properties correctly handles keys and headers."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        message = b"test message"
        key = b"test_key"
        headers = {"source": "test", "timestamp": b"2025-01-01"}
        
        # Act
        self.sink.write_msg(dest_key, message, key, headers)
        
        # Assert
        mock_client.publish.assert_called_once()
        publish_call = mock_client.publish.call_args
        properties = publish_call.kwargs['properties']
        # Properties should be created by _serialize_properties method
        assert properties is not None


class TestSinkMQTTEdgeCases:
    """
    Edge case tests for SinkMQTT class.
    
    This test suite covers edge cases and boundary conditions including
    key extraction without serialization, empty inputs, and various configurations.
    """

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        self.mock_mqtt_config = Mock(spec=AppMQTTConfig)
        self.mock_producer_config = Mock(spec=MQTTProducerConfig)
        
        # Configure mock config
        self.mock_config.mqtt = self.mock_mqtt_config
        self.mock_mqtt_config.host = "localhost"
        self.mock_mqtt_config.port = 1883
        self.mock_mqtt_config.keepAliveSec = 60
        self.mock_mqtt_config.producer = self.mock_producer_config
        self.mock_producer_config.qos = 1
        self.mock_producer_config.retain = False
        
        # Mock get_mqtt_topic method
        self.mock_config.get_mqtt_topic = Mock(return_value="test/topic")
        
        # Create SinkMQTT instance
        self.sink = SinkMQTT(self.mock_config)

    @pytest.fixture
    def mock_client(self):
        """Fixture providing a mocked paho-mqtt Client instance."""
        client = Mock(spec=Client)
        client.publish.return_value = Mock()
        client.connect.return_value = None
        client.loop_start.return_value = None
        client.is_connected.return_value = True
        return client

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_with_key_extractor_but_no_key_serializer(self, mock_client_class, mock_client):
        """Test write method with key extraction but no key serialization raises error."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [{"id": 1, "user_id": "user123", "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        key_extractor = lambda x: x["user_id"]
        # No key_serializer provided - extracted key stays as string
        
        # Act & Assert
        with pytest.raises(ValueError, match="Message key must be of type `bytes`"):
            self.sink.write(dest_key, messages, value_serializer, key_extractor)

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_with_key_extractor_returning_none(self, mock_client_class, mock_client):
        """Test write method when key extractor returns None."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [{"id": 1, "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        key_extractor = lambda x: None  # Returns None
        key_serializer = lambda x: str(x).encode('utf-8')
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, key_extractor, key_serializer)
        
        # Assert
        mock_client.publish.assert_called_once()
        # Should handle None key gracefully

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_with_key_extractor_no_serializer_none_key(self, mock_client_class, mock_client):
        """Test write method with key extraction but no serialization when key is None - partial branch coverage."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [{"id": 1, "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        key_extractor = lambda x: None  # Returns None
        # No key_serializer provided - this tests the partial branch where key_serializer is None
        
        # Act
        self.sink.write(dest_key, messages, value_serializer, key_extractor)
        
        # Assert
        mock_client.publish.assert_called_once()
        # Should handle None key gracefully and not try to serialize

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_serialize_properties_with_no_key_no_headers(self, mock_client_class, mock_client):
        """Test _serialize_properties method with no key and no headers."""
        # Arrange
        mock_client_class.return_value = mock_client
        
        # Act
        props = self.sink._serialize_properties()
        
        # Assert
        assert props is not None
        assert isinstance(props, Properties)

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_serialize_properties_with_bytes_headers(self, mock_client_class, mock_client):
        """Test _serialize_properties method with bytes headers."""
        # Arrange
        mock_client_class.return_value = mock_client
        headers = {"source": b"test_app", "version": "1.0"}
        key = b"test_key"
        
        # Act
        props = self.sink._serialize_properties(headers, key)
        
        # Assert
        assert props is not None
        assert isinstance(props, Properties)

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_serialize_properties_with_string_headers(self, mock_client_class, mock_client):
        """Test _serialize_properties method with string headers."""
        # Arrange
        mock_client_class.return_value = mock_client
        headers = {"source": "test_app", "version": "1.0"}
        
        # Act
        props = self.sink._serialize_properties(headers)
        
        # Assert
        assert props is not None
        assert isinstance(props, Properties)


class TestSinkMQTTDirtyTests:
    """
    Dirty tests for SinkMQTT class - testing error conditions and failure scenarios.
    
    This test suite ensures the code fails gracefully with proper error handling
    when invalid inputs or error conditions are encountered.
    """

    def setup_method(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.mock_config = Mock(spec=AppConfig)
        self.mock_mqtt_config = Mock(spec=AppMQTTConfig)
        self.mock_producer_config = Mock(spec=MQTTProducerConfig)
        
        # Configure mock config
        self.mock_config.mqtt = self.mock_mqtt_config
        self.mock_mqtt_config.host = "localhost"
        self.mock_mqtt_config.port = 1883
        self.mock_mqtt_config.keepAliveSec = 60
        self.mock_mqtt_config.producer = self.mock_producer_config
        self.mock_producer_config.qos = 1
        self.mock_producer_config.retain = False
        
        # Mock get_mqtt_topic method
        self.mock_config.get_mqtt_topic = Mock(return_value="test/topic")
        
        # Create SinkMQTT instance
        self.sink = SinkMQTT(self.mock_config)

    @pytest.fixture
    def mock_client(self):
        """Fixture providing a mocked paho-mqtt Client instance."""
        client = Mock(spec=Client)
        client.publish.return_value = Mock()
        client.connect.return_value = None
        client.loop_start.return_value = None
        client.is_connected.return_value = True
        client.disconnect.return_value = None
        client.loop_stop.return_value = None
        return client

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_write_raises_error_for_non_bytes_key(self, mock_client_class, mock_client):
        """Test write method raises ValueError when key is not bytes type."""
        # Arrange
        mock_client_class.return_value = mock_client
        dest_key = "test_key"
        messages = [{"id": 1, "user_id": "user123", "data": "test"}]
        value_serializer = lambda x: str(x).encode('utf-8')
        key_extractor = lambda x: x["user_id"]
        key_serializer = lambda x: x  # Returns string instead of bytes
        
        # Act & Assert
        with pytest.raises(ValueError, match="Message key must be of type `bytes`"):
            self.sink.write(dest_key, messages, value_serializer, key_extractor, key_serializer)

    def test_on_publish_success(self):
        """Test _on_publish callback with successful publish."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.__eq__ = Mock(return_value=True)  # rc == MQTT_ERR_SUCCESS
        
        # Act
        with patch('mdx.analytics.core.stream.sink.sink_mqtt.logger') as mock_logger:
            self.sink._on_publish(mock_client, None, 123, mock_rc, None)
        
        # Assert
        mock_logger.debug.assert_called_once_with('Published message with id 123 successfully')

    def test_on_publish_no_subscribers(self):
        """Test _on_publish callback when no subscribers are active."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.__eq__ = Mock(return_value=False)  # rc != MQTT_ERR_SUCCESS
        mock_rc.value = 16
        
        # Act
        with patch('mdx.analytics.core.stream.sink.sink_mqtt.logger') as mock_logger:
            self.sink._on_publish(mock_client, None, 123, mock_rc, None)
        
        # Assert
        mock_logger.debug.assert_called_once_with('Msg with id 123 published to the broker but no matching subscribers are active.')

    def test_on_publish_error(self):
        """Test _on_publish callback with publish error."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.__eq__ = Mock(return_value=False)  # rc != MQTT_ERR_SUCCESS
        mock_rc.value = 5
        mock_rc.__str__ = Mock(return_value="Connection refused")
        
        # Act
        with patch('mdx.analytics.core.stream.sink.sink_mqtt.logger') as mock_logger:
            self.sink._on_publish(mock_client, None, 123, mock_rc, None)
        
        # Assert
        mock_logger.error.assert_called_once_with('Failed to publish message with id 123 - [code:5] Connection refused')

    def test_on_connect_success(self):
        """Test _on_connect callback with successful connection."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_conn_flags = Mock(spec=ConnectFlags)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.__eq__ = Mock(return_value=True)  # rc == CONNACK_ACCEPTED
        
        # Act
        with patch('mdx.analytics.core.stream.sink.sink_mqtt.logger') as mock_logger:
            self.sink._on_connect(mock_client, None, mock_conn_flags, mock_rc, None)
        
        # Assert
        mock_logger.info.assert_called_once_with('Producer connected to MQTT broker.')

    def test_on_connect_failure(self):
        """Test _on_connect callback with connection failure."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_conn_flags = Mock(spec=ConnectFlags)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.__eq__ = Mock(return_value=False)  # rc != CONNACK_ACCEPTED
        mock_rc.value = 5
        mock_rc.__str__ = Mock(return_value="Connection refused")
        
        # Act & Assert
        with pytest.raises(Exception, match="MQTT Connection Error in producer - \\[code:5\\] Connection refused"):
            self.sink._on_connect(mock_client, None, mock_conn_flags, mock_rc, None)

    def test_on_disconnect_success_when_connected(self):
        """Test _on_disconnect callback with successful disconnection when client is connected."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_client.is_connected.return_value = True
        self.sink._client = mock_client
        
        mock_disc_flags = Mock(spec=DisconnectFlags)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.__eq__ = Mock(return_value=True)  # rc == MQTT_ERR_SUCCESS
        
        # Act
        with patch('mdx.analytics.core.stream.sink.sink_mqtt.logger') as mock_logger:
            self.sink._on_disconnect(mock_client, None, mock_disc_flags, mock_rc, None)
        
        # Assert
        mock_logger.info.assert_called_once_with('Producer disconnected from MQTT broker.')

    def test_on_disconnect_error_when_connected(self):
        """Test _on_disconnect callback with disconnection error when client is connected."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_client.is_connected.return_value = True
        self.sink._client = mock_client
        
        mock_disc_flags = Mock(spec=DisconnectFlags)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.__eq__ = Mock(return_value=False)  # rc != MQTT_ERR_SUCCESS
        mock_rc.value = 7
        mock_rc.__str__ = Mock(return_value="Unexpected disconnect")
        
        # Act & Assert
        with pytest.raises(Exception, match="MQTT Error while disconnecting producer - \\[code:7\\] Unexpected disconnect"):
            self.sink._on_disconnect(mock_client, None, mock_disc_flags, mock_rc, None)

    def test_on_disconnect_when_not_connected(self):
        """Test _on_disconnect callback when client is not connected."""
        # Arrange
        mock_client = Mock(spec=Client)
        mock_client.is_connected.return_value = False
        self.sink._client = mock_client
        
        mock_disc_flags = Mock(spec=DisconnectFlags)
        mock_rc = Mock(spec=ReasonCode)
        mock_rc.value = 0
        
        # Act
        with patch('mdx.analytics.core.stream.sink.sink_mqtt.logger') as mock_logger:
            self.sink._on_disconnect(mock_client, None, mock_disc_flags, mock_rc, None)
        
        # Assert
        # Should not log anything or raise exception when not connected
        mock_logger.info.assert_not_called()
        mock_logger.error.assert_not_called()

    def test_on_disconnect_when_no_client(self):
        """Test _on_disconnect callback when client is None."""
        # Arrange
        self.sink._client = None
        mock_client = Mock(spec=Client)
        mock_disc_flags = Mock(spec=DisconnectFlags)
        mock_rc = Mock(spec=ReasonCode)
        
        # Act
        with patch('mdx.analytics.core.stream.sink.sink_mqtt.logger') as mock_logger:
            self.sink._on_disconnect(mock_client, None, mock_disc_flags, mock_rc, None)
        
        # Assert
        # Should not log anything or raise exception when client is None
        mock_logger.info.assert_not_called()
        mock_logger.error.assert_not_called()

    @patch('mdx.analytics.core.stream.sink.sink_mqtt.Client')
    def test_close_with_existing_client(self, mock_client_class, mock_client):
        """Test close method when client exists."""
        # Arrange
        mock_client_class.return_value = mock_client
        self.sink._client = mock_client
        
        # Act
        self.sink.close()
        
        # Assert
        mock_client.disconnect.assert_called_once()
        mock_client.loop_stop.assert_called_once()

    def test_close_with_no_client(self):
        """Test close method when no client exists."""
        # Arrange
        self.sink._client = None
        
        # Act
        self.sink.close()
        
        # Assert
        # Should not raise any exception when client is None
