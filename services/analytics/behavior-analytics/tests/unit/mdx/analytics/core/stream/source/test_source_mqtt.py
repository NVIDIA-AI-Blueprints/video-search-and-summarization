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
import time
from unittest.mock import Mock, patch

from paho.mqtt.client import Client, MQTTMessage, MQTT_ERR_SUCCESS
from paho.mqtt.properties import Properties
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import StreamMessage
from mdx.analytics.core.stream.source.source_mqtt import SourceMQTT, _MessageBuffer


class TestSourceMQTTRead:
    """Test suite for SourceMQTT.read functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock AppConfig for testing."""
        config = Mock(spec=AppConfig)
        config.mqtt = Mock()
        config.mqtt.consumer = Mock()
        config.mqtt.consumer.maxPollCount = 10
        config.mqtt.consumer.pollTimeoutSec = 1
        config.mqtt.consumer.qos = 1
        config.mqtt.consumer.retryMaxAttempts = 3
        config.mqtt.consumer.retryIntervalSec = 30.0
        config.mqtt.clientId = "test-client"
        config.mqtt.host = "localhost"
        config.mqtt.port = 1883
        config.mqtt.keepAliveSec = 60
        config.get_mqtt_topic = Mock()
        return config

    @pytest.fixture
    def source_mqtt(self, mock_config):
        """Create SourceMQTT instance for testing."""
        return SourceMQTT(mock_config)

    @pytest.fixture
    def mock_mqtt_client(self):
        """Create a mock MQTT client."""
        client = Mock(spec=Client)
        client.is_connected.return_value = True
        client.subscribe.return_value = (MQTT_ERR_SUCCESS, 1)
        return client

    def test_read_successful_with_messages(self, source_mqtt, mock_config, mock_mqtt_client):
        """Test successful read operation when messages are available in buffer."""
        # Setup
        test_topic = "test/topic"
        test_src_key = "test_key"
        mock_config.get_mqtt_topic.return_value = test_topic
        
        # Create mock messages
        mock_messages = [
            StreamMessage(key="key1", value=b"message1", headers={}),
            StreamMessage(key="key2", value=b"message2", headers={})
        ]
        
        # Mock buffer with messages
        mock_buffer = Mock()
        mock_buffer.poll.return_value = mock_messages
        source_mqtt._buffers[test_topic] = mock_buffer
        
        with patch.object(source_mqtt, '_get_group_id', return_value="test-client-id"), \
             patch.object(source_mqtt, '_init_client'), \
             patch.object(source_mqtt, '_subscribe'):
            
            source_mqtt._client = mock_mqtt_client
            
            # Execute
            result = source_mqtt.read(test_src_key)
            
            # Assert
            assert result == mock_messages
            assert len(result) == 2
            mock_buffer.poll.assert_called_once()

    def test_read_no_messages_available(self, source_mqtt, mock_config, mock_mqtt_client):
        """Test read operation when no messages are available in buffer."""
        # Setup
        test_topic = "test/topic"
        test_src_key = "test_key"
        mock_config.get_mqtt_topic.return_value = test_topic
        
        # No buffer exists for the topic
        assert test_topic not in source_mqtt._buffers
        
        with patch.object(source_mqtt, '_get_group_id', return_value="test-client-id"), \
             patch.object(source_mqtt, '_init_client'), \
             patch.object(source_mqtt, '_subscribe'):
            
            source_mqtt._client = mock_mqtt_client
            
            # Execute
            result = source_mqtt.read(test_src_key)
            
            # Assert
            assert result == []

    def test_read_topic_not_found_raises_value_error(self, source_mqtt, mock_config):
        """Test that ValueError is raised when no topic is found for the given source key."""
        # Setup
        test_src_key = "invalid_key"
        mock_config.get_mqtt_topic.return_value = None
        
        # Execute & Assert
        with pytest.raises(ValueError, match=f"Could not find a topic/pattern with key: {test_src_key}"):
            source_mqtt.read(test_src_key)

    def test_read_initializes_client_when_none_exists(self, source_mqtt, mock_config, mock_mqtt_client):
        """Test that client is initialized when it doesn't exist."""
        # Setup
        test_topic = "test/topic"
        test_src_key = "test_key"
        mock_config.get_mqtt_topic.return_value = test_topic
        
        # Ensure no client exists
        source_mqtt._client = None
        
        with patch.object(source_mqtt, '_get_group_id', return_value="test-client-id") as mock_get_group, \
             patch.object(source_mqtt, '_init_client') as mock_init_client, \
             patch.object(source_mqtt, '_subscribe') as mock_subscribe:
            
            # Execute
            source_mqtt.read(test_src_key)
            
            # Assert
            mock_get_group.assert_called_once_with(test_topic, "test-client", None)
            mock_init_client.assert_called_once_with("test-client-id")
            mock_subscribe.assert_called_once_with(test_topic, "test-client-id")

    def test_read_with_group_id_suffix(self, source_mqtt, mock_config, mock_mqtt_client):
        """Test read operation with group_id_suffix parameter."""
        # Setup
        test_topic = "test/topic"
        test_src_key = "test_key"
        group_id_suffix = "suffix"
        mock_config.get_mqtt_topic.return_value = test_topic
        
        with patch.object(source_mqtt, '_get_group_id', return_value="test-client-id-suffix") as mock_get_group, \
             patch.object(source_mqtt, '_init_client'), \
             patch.object(source_mqtt, '_subscribe'):
            
            source_mqtt._client = mock_mqtt_client
            
            # Execute
            source_mqtt.read(test_src_key, group_id_suffix)
            
            # Assert
            mock_get_group.assert_called_once_with(test_topic, "test-client", group_id_suffix)

    def test_read_subscribes_to_topic(self, source_mqtt, mock_config, mock_mqtt_client):
        """Test that read operation subscribes to the topic."""
        # Setup
        test_topic = "test/topic"
        test_src_key = "test_key"
        mock_config.get_mqtt_topic.return_value = test_topic
        
        with patch.object(source_mqtt, '_get_group_id', return_value="test-client-id"), \
             patch.object(source_mqtt, '_init_client'), \
             patch.object(source_mqtt, '_subscribe') as mock_subscribe:
            
            source_mqtt._client = mock_mqtt_client
            
            # Execute
            source_mqtt.read(test_src_key)
            
            # Assert
            mock_subscribe.assert_called_once_with(test_topic, "test-client-id")


class TestSourceMQTTCallbacks:
    """Test suite for SourceMQTT callback methods."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock AppConfig for testing."""
        config = Mock(spec=AppConfig)
        config.mqtt = Mock()
        config.mqtt.consumer = Mock()
        config.mqtt.consumer.maxPollCount = 10
        config.mqtt.consumer.pollTimeoutSec = 1
        config.mqtt.consumer.qos = 1
        config.mqtt.consumer.retryMaxAttempts = 3
        config.mqtt.consumer.retryIntervalSec = 30.0
        config.mqtt.clientId = "test-client"
        config.mqtt.host = "localhost"
        config.mqtt.port = 1883
        config.mqtt.keepAliveSec = 60
        config.get_mqtt_topic = Mock()
        return config

    @pytest.fixture
    def source_mqtt(self, mock_config):
        """Create SourceMQTT instance for testing."""
        return SourceMQTT(mock_config)

    @pytest.fixture
    def mock_mqtt_message(self):
        """Create a mock MQTT message."""
        msg = Mock(spec=MQTTMessage)
        msg.topic = "test/topic"
        msg.payload = b"test payload"
        msg.mid = 123
        msg.retain = False
        msg.properties = None
        return msg

    @pytest.fixture
    def mock_mqtt_message_retained(self):
        """Create a mock retained MQTT message."""
        msg = Mock(spec=MQTTMessage)
        msg.topic = "test/topic"
        msg.payload = b"test payload"
        msg.mid = 123
        msg.retain = True
        msg.properties = None
        return msg

    def test_on_message_non_retained_with_buffer(self, source_mqtt, mock_mqtt_message):
        """Test _on_message callback with non-retained message and existing buffer."""
        # Setup
        mock_buffer = Mock()
        source_mqtt._buffers["test/topic"] = mock_buffer
        mock_client = Mock()
        mock_userdata = Mock()
        
        # Execute
        source_mqtt._on_message(mock_client, mock_userdata, mock_mqtt_message)
        
        # Assert
        mock_buffer.append.assert_called_once_with(mock_mqtt_message)

    def test_on_message_non_retained_without_buffer_raises_exception(self, source_mqtt, mock_mqtt_message):
        """Test _on_message callback with non-retained message but no buffer raises exception."""
        # Setup
        mock_client = Mock()
        mock_userdata = Mock()
        # Ensure no buffer exists for the topic
        assert "test/topic" not in source_mqtt._buffers
        
        # Execute & Assert
        with pytest.raises(Exception, match=r"\[FATAL\] Unexpected :: msg received before subscribing to topic test/topic\."):
            source_mqtt._on_message(mock_client, mock_userdata, mock_mqtt_message)

    def test_on_message_retained_message_ignored(self, source_mqtt, mock_mqtt_message_retained):
        """Test _on_message callback ignores retained messages."""
        # Setup
        mock_client = Mock()
        mock_userdata = Mock()
        # Don't set up any buffer - retained messages should be ignored anyway
        
        # Execute - should not raise exception
        source_mqtt._on_message(mock_client, mock_userdata, mock_mqtt_message_retained)
        
        # Assert - no exception should be raised and no buffer operations should occur

    def test_on_connect_success(self, source_mqtt):
        """Test _on_connect callback with successful connection."""
        # Setup
        mock_client = Mock()
        mock_userdata = Mock()
        mock_conn_flags = Mock()
        # Mock rc that will equal CONNACK_ACCEPTED
        mock_rc = Mock()
        mock_rc.__eq__ = Mock(return_value=True)  # Returns True when compared to CONNACK_ACCEPTED
        mock_props = Mock()
        
        # Execute - should not raise exception
        source_mqtt._on_connect(mock_client, mock_userdata, mock_conn_flags, mock_rc, mock_props)

    def test_on_connect_failure_raises_exception(self, source_mqtt):
        """Test _on_connect callback with connection failure raises exception."""
        # Setup
        mock_client = Mock()
        mock_userdata = Mock()
        mock_conn_flags = Mock()
        # Mock rc that will NOT equal CONNACK_ACCEPTED
        mock_rc = Mock()
        mock_rc.value = 5
        mock_rc.__eq__ = Mock(return_value=False)  # Returns False when compared to CONNACK_ACCEPTED
        mock_rc.__str__ = Mock(return_value="Connection refused")
        mock_props = Mock()
        
        # Execute & Assert
        with pytest.raises(Exception, match=r"MQTT Connection Error in consumer - \[code:5\] Connection refused"):
            source_mqtt._on_connect(mock_client, mock_userdata, mock_conn_flags, mock_rc, mock_props)

    def test_on_disconnect_success_when_connected(self, source_mqtt):
        """Test _on_disconnect callback with successful disconnection when client is connected."""
        # Setup
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        source_mqtt._client = mock_client
        
        mock_userdata = Mock()
        mock_conn_flags = Mock()
        # Mock rc that will equal MQTT_ERR_SUCCESS
        mock_rc = Mock()
        mock_rc.__eq__ = Mock(return_value=True)  # Returns True when compared to MQTT_ERR_SUCCESS
        mock_props = Mock()
        
        # Execute - should not raise exception
        source_mqtt._on_disconnect(mock_client, mock_userdata, mock_conn_flags, mock_rc, mock_props)

    def test_on_disconnect_failure_when_connected_raises_exception(self, source_mqtt):
        """Test _on_disconnect callback with disconnection failure when client is connected raises exception."""
        # Setup
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        source_mqtt._client = mock_client
        
        mock_userdata = Mock()
        mock_conn_flags = Mock()
        # Mock rc that will NOT equal MQTT_ERR_SUCCESS
        mock_rc = Mock()
        mock_rc.value = 7
        mock_rc.__eq__ = Mock(return_value=False)  # Returns False when compared to MQTT_ERR_SUCCESS
        mock_rc.__str__ = Mock(return_value="Network error")
        mock_props = Mock()
        
        # Execute & Assert
        with pytest.raises(Exception, match=r"MQTT Error while disconnecting consumer - \[code:7\] Network error"):
            source_mqtt._on_disconnect(mock_client, mock_userdata, mock_conn_flags, mock_rc, mock_props)

    def test_on_disconnect_when_not_connected(self, source_mqtt):
        """Test _on_disconnect callback when client is not connected (no action taken)."""
        # Setup
        mock_client = Mock()
        mock_client.is_connected.return_value = False
        source_mqtt._client = mock_client
        
        mock_userdata = Mock()
        mock_conn_flags = Mock()
        mock_rc = Mock()
        mock_props = Mock()
        
        # Execute - should not raise exception or do anything
        source_mqtt._on_disconnect(mock_client, mock_userdata, mock_conn_flags, mock_rc, mock_props)

    def test_on_disconnect_when_no_client(self, source_mqtt):
        """Test _on_disconnect callback when no client exists (no action taken)."""
        # Setup
        source_mqtt._client = None
        
        mock_client = Mock()
        mock_userdata = Mock()
        mock_conn_flags = Mock()
        mock_rc = Mock()
        mock_props = Mock()
        
        # Execute - should not raise exception or do anything
        source_mqtt._on_disconnect(mock_client, mock_userdata, mock_conn_flags, mock_rc, mock_props)


class TestSourceMQTTSubscribe:
    """Test suite for SourceMQTT._subscribe method."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock AppConfig for testing."""
        config = Mock(spec=AppConfig)
        config.mqtt = Mock()
        config.mqtt.consumer = Mock()
        config.mqtt.consumer.maxPollCount = 10
        config.mqtt.consumer.pollTimeoutSec = 1
        config.mqtt.consumer.qos = 1
        config.mqtt.consumer.retryMaxAttempts = 3
        config.mqtt.consumer.retryIntervalSec = 30.0
        config.mqtt.clientId = "test-client"
        config.mqtt.host = "localhost"
        config.mqtt.port = 1883
        config.mqtt.keepAliveSec = 60
        config.get_mqtt_topic = Mock()
        return config

    @pytest.fixture
    def source_mqtt(self, mock_config):
        """Create SourceMQTT instance for testing."""
        return SourceMQTT(mock_config)

    def test_subscribe_topic_already_in_buffers(self, source_mqtt):
        """Test _subscribe returns early when topic already exists in buffers."""
        # Setup
        topic = "test/topic"
        client_id = "test-client-id"
        source_mqtt._buffers[topic] = Mock()  # Topic already exists
        
        # Execute
        source_mqtt._subscribe(topic, client_id)
        
        # Assert - method should return early, no client operations

    def test_subscribe_successful_when_client_connected(self, source_mqtt, mock_config):
        """Test successful subscription when client is connected."""
        # Setup
        topic = "test/topic"
        client_id = "test-client-id"
        
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.subscribe.return_value = (MQTT_ERR_SUCCESS, 1)
        source_mqtt._client = mock_client
        
        # Execute
        source_mqtt._subscribe(topic, client_id)
        
        # Assert
        mock_client.subscribe.assert_called_once_with(topic, qos=mock_config.mqtt.consumer.qos)
        assert topic in source_mqtt._buffers
        assert isinstance(source_mqtt._buffers[topic], _MessageBuffer)

    def test_subscribe_failure_when_subscribe_returns_error(self, source_mqtt, mock_config):
        """Test subscription failure when MQTT subscribe returns error code."""
        # Setup
        topic = "test/topic"
        client_id = "test-client-id"
        
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.subscribe.return_value = (7, 1)  # Error code
        source_mqtt._client = mock_client
        
        # Execute
        source_mqtt._subscribe(topic, client_id)
        
        # Assert
        mock_client.subscribe.assert_called_once_with(topic, qos=mock_config.mqtt.consumer.qos)
        assert topic not in source_mqtt._buffers  # Buffer should not be created

    @patch('time.sleep')
    @patch('random.random')
    def test_subscribe_waits_for_connection_then_succeeds(self, mock_random, mock_sleep, source_mqtt, mock_config):
        """Test subscription waits for client connection then succeeds."""
        # Setup
        topic = "test/topic"
        client_id = "test-client-id"
        mock_random.return_value = 0.3
        
        mock_client = Mock()
        # Loop checks: while not connected (3 times), then final if connected check
        # For 2 attempts before success: False, False, True, True
        mock_client.is_connected.side_effect = [False, False, True, True]
        mock_client.subscribe.return_value = (MQTT_ERR_SUCCESS, 1)
        source_mqtt._client = mock_client
        
        # Execute
        source_mqtt._subscribe(topic, client_id)
        
        # Assert
        assert mock_client.is_connected.call_count == 4
        assert mock_sleep.call_count == 2  # Should sleep twice while waiting
        mock_client.subscribe.assert_called_once_with(topic, qos=mock_config.mqtt.consumer.qos)
        assert topic in source_mqtt._buffers

    @patch('time.sleep')
    @patch('random.random')
    def test_subscribe_max_attempts_exceeded_raises_exception(self, mock_random, mock_sleep, source_mqtt):
        """Test subscription raises exception when max connection attempts exceeded."""
        # Setup
        topic = "test/topic"
        client_id = "test-client-id"
        mock_random.return_value = 0.3
        
        mock_client = Mock()
        mock_client.is_connected.return_value = False  # Never connects
        source_mqtt._client = mock_client
        
        # Execute & Assert
        with pytest.raises(Exception, match=r"\[FATAL\] Failed attempt to subscribe to test/topic before MQTT client test-client-id is connected\."):
            source_mqtt._subscribe(topic, client_id)
        
        # The loop checks connection initially, then sleeps and checks again up to 3 attempts
        # So it will call is_connected: initial + 3 attempts = 4 times minimum
        assert mock_client.is_connected.call_count >= 3  # At least max attempts
        assert mock_sleep.call_count == 3  # Sleep after each attempt

    def test_subscribe_no_client_raises_exception(self, source_mqtt):
        """Test subscription raises exception when no client exists."""
        # Setup
        topic = "test/topic"
        client_id = "test-client-id"
        source_mqtt._client = None
        
        # Execute & Assert
        with pytest.raises(Exception, match=r"\[FATAL\] MQTT consumer `test-client-id` does not exist\."):
            source_mqtt._subscribe(topic, client_id)


class TestSourceMQTTInitClient:
    """Test suite for SourceMQTT._init_client method."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock AppConfig for testing."""
        config = Mock(spec=AppConfig)
        config.mqtt = Mock()
        config.mqtt.consumer = Mock()
        config.mqtt.consumer.maxPollCount = 10
        config.mqtt.consumer.pollTimeoutSec = 1
        config.mqtt.consumer.qos = 1
        config.mqtt.consumer.retryMaxAttempts = 3
        config.mqtt.consumer.retryIntervalSec = 30.0
        config.mqtt.clientId = "test-client"
        config.mqtt.host = "localhost"
        config.mqtt.port = 1883
        config.mqtt.keepAliveSec = 60
        config.get_mqtt_topic = Mock()
        return config

    @pytest.fixture
    def source_mqtt(self, mock_config):
        """Create SourceMQTT instance for testing."""
        return SourceMQTT(mock_config)

    @patch('mdx.analytics.core.stream.source.source_mqtt.Client')
    def test_init_client_creates_and_configures_client(self, mock_client_class, source_mqtt, mock_config):
        """Test _init_client creates and properly configures MQTT client."""
        # Setup
        client_id = "test-client-id"
        mock_client_instance = Mock()
        mock_client_class.return_value = mock_client_instance
        
        # Execute
        source_mqtt._init_client(client_id)
        
        # Assert
        # Client creation
        mock_client_class.assert_called_once_with(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=MQTTProtocolVersion.MQTTv5
        )
        
        # Callback assignment
        assert mock_client_instance.on_connect == source_mqtt._on_connect
        assert mock_client_instance.on_message == source_mqtt._on_message
        assert mock_client_instance.on_disconnect == source_mqtt._on_disconnect
        
        # Connection call
        mock_client_instance.connect.assert_called_once_with(
            host=mock_config.mqtt.host,
            port=mock_config.mqtt.port,
            keepalive=mock_config.mqtt.keepAliveSec,
            clean_start=True
        )
        
        # Loop start
        mock_client_instance.loop_start.assert_called_once()
        
        # Client assignment
        assert source_mqtt._client == mock_client_instance


class TestSourceMQTTClose:
    """Test suite for SourceMQTT.close method."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock AppConfig for testing."""
        config = Mock(spec=AppConfig)
        config.mqtt = Mock()
        config.mqtt.consumer = Mock()
        config.get_mqtt_topic = Mock()
        return config

    @pytest.fixture
    def source_mqtt(self, mock_config):
        """Create SourceMQTT instance for testing."""
        return SourceMQTT(mock_config)

    def test_close_with_existing_client(self, source_mqtt):
        """Test close method when client exists."""
        # Setup
        mock_client = Mock()
        source_mqtt._client = mock_client
        
        # Execute
        source_mqtt.close()
        
        # Assert
        mock_client.disconnect.assert_called_once()
        mock_client.loop_stop.assert_called_once()

    def test_close_with_no_client(self, source_mqtt):
        """Test close method when no client exists (should not raise exception)."""
        # Setup
        source_mqtt._client = None

        # Execute - should not raise exception
        source_mqtt.close()


class TestSourceMQTTInitClientRetry:
    """Test suite for _init_client retry on transient connect failures."""

    @pytest.fixture
    def mock_config(self):
        config = Mock(spec=AppConfig)
        config.mqtt = Mock()
        config.mqtt.consumer = Mock()
        config.mqtt.consumer.maxPollCount = 10
        config.mqtt.consumer.pollTimeoutSec = 1
        config.mqtt.consumer.qos = 1
        config.mqtt.consumer.retryMaxAttempts = 3
        config.mqtt.consumer.retryIntervalSec = 0.01  # tiny so tests are fast
        config.mqtt.clientId = "test-client"
        config.mqtt.host = "localhost"
        config.mqtt.port = 1883
        config.mqtt.keepAliveSec = 60
        config.get_mqtt_topic = Mock()
        return config

    @pytest.fixture
    def source_mqtt(self, mock_config):
        return SourceMQTT(mock_config)

    @patch('mdx.analytics.core.stream.source.source_mqtt.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_mqtt.Client')
    def test_init_client_retries_on_oserror_then_succeeds(self, mock_client_class, mock_sleep, source_mqtt):
        """Two ConnectionRefusedErrors then success → succeeds after retry."""
        bad_client_1 = Mock()
        bad_client_1.connect.side_effect = ConnectionRefusedError("refused")
        bad_client_2 = Mock()
        bad_client_2.connect.side_effect = ConnectionRefusedError("refused")
        good_client = Mock()
        mock_client_class.side_effect = [bad_client_1, bad_client_2, good_client]

        source_mqtt._init_client("test-client")

        # Three Client() instances created — third one wins
        assert mock_client_class.call_count == 3
        good_client.connect.assert_called_once()
        good_client.loop_start.assert_called_once()
        # 2 sleeps between 3 attempts
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.01)

    @patch('mdx.analytics.core.stream.source.source_mqtt.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_mqtt.Client')
    def test_init_client_raises_runtime_error_after_max_attempts(self, mock_client_class, mock_sleep, source_mqtt):
        """All attempts fail → RuntimeError, not raw OSError."""
        bad_client = Mock()
        bad_client.connect.side_effect = ConnectionRefusedError("refused")
        mock_client_class.return_value = bad_client

        with pytest.raises(RuntimeError, match="Failed to connect to MQTT broker after 3 attempts"):
            source_mqtt._init_client("test-client")

        assert mock_client_class.call_count == 3
        assert mock_sleep.call_count == 2

    @patch('mdx.analytics.core.stream.source.source_mqtt.time.sleep')
    @patch('mdx.analytics.core.stream.source.source_mqtt.Client')
    def test_init_client_does_not_retry_on_value_error(self, mock_client_class, mock_sleep, source_mqtt):
        """A ValueError (bad config) is not a transient failure — propagate immediately."""
        bad_client = Mock()
        bad_client.connect.side_effect = ValueError("bad host")
        mock_client_class.return_value = bad_client

        with pytest.raises(ValueError, match="bad host"):
            source_mqtt._init_client("test-client")

        assert mock_client_class.call_count == 1
        mock_sleep.assert_not_called()


class TestMessageBuffer:
    """Test suite for _MessageBuffer functionality."""

    @pytest.fixture
    def message_buffer(self):
        """Create _MessageBuffer instance for testing."""
        return _MessageBuffer(max_poll_count=5, poll_timeout_sec=1)

    @pytest.fixture
    def mock_mqtt_message(self):
        """Create a mock MQTT message."""
        msg = Mock(spec=MQTTMessage)
        msg.topic = "test/topic"
        msg.payload = b"test payload"
        msg.mid = 123
        msg.retain = False
        msg.properties = None
        return msg

    @pytest.fixture
    def mock_mqtt_message_with_properties(self):
        """Create a mock MQTT message with properties."""
        msg = Mock(spec=MQTTMessage)
        msg.topic = "test/topic"
        msg.payload = b"test payload"
        msg.mid = 123
        msg.retain = False
        
        # Mock properties with UserProperty
        properties = Mock(spec=Properties)
        properties.json.return_value = {
            'UserProperty': [('key', 'test-key'), ('header1', 'value1')]
        }
        msg.properties = properties
        return msg

    def test_append_message_without_properties(self, message_buffer, mock_mqtt_message):
        """Test appending MQTT message without properties to buffer."""
        # Execute
        message_buffer.append(mock_mqtt_message)
        
        # Assert
        assert len(message_buffer._buffer) == 1
        stream_msg = message_buffer._buffer[0]
        assert isinstance(stream_msg, StreamMessage)
        assert stream_msg.key is None
        assert stream_msg.value == b"test payload"
        assert stream_msg.headers == {}

    def test_append_message_with_properties_and_key(self, message_buffer, mock_mqtt_message_with_properties):
        """Test appending MQTT message with properties including key."""
        # Execute
        message_buffer.append(mock_mqtt_message_with_properties)
        
        # Assert
        assert len(message_buffer._buffer) == 1
        stream_msg = message_buffer._buffer[0]
        assert isinstance(stream_msg, StreamMessage)
        assert stream_msg.key_bytes == b"test-key"
        assert stream_msg.value_bytes == b"test payload"
        assert stream_msg.headers == {'header1': b'value1'}

    def test_append_multiple_messages(self, message_buffer, mock_mqtt_message):
        """Test appending multiple messages to buffer."""
        # Execute
        for i in range(3):
            mock_mqtt_message.payload = f"payload{i}".encode()
            message_buffer.append(mock_mqtt_message)
        
        # Assert
        assert len(message_buffer._buffer) == 3
        for i, stream_msg in enumerate(message_buffer._buffer):
            assert stream_msg.value == f"payload{i}".encode()

    def test_poll_returns_all_messages_when_under_max_count(self, message_buffer):
        """Test polling when buffer has fewer messages than max_poll_count."""
        # Setup - add 3 messages (less than max_poll_count of 5)
        for i in range(3):
            stream_msg = StreamMessage(key=f"key{i}", value=f"value{i}".encode(), headers={})
            message_buffer._buffer.append(stream_msg)
        
        # Execute
        result = message_buffer.poll()
        
        # Assert
        assert len(result) == 3
        assert len(message_buffer._buffer) == 0  # Buffer should be empty after polling
        for i, msg in enumerate(result):
            assert msg.key == f"key{i}"
            assert msg.value == f"value{i}".encode()

    def test_poll_returns_max_count_messages(self, message_buffer):
        """Test polling when buffer has more messages than max_poll_count."""
        # Setup - add 8 messages (more than max_poll_count of 5)
        for i in range(8):
            stream_msg = StreamMessage(key=f"key{i}", value=f"value{i}".encode(), headers={})
            message_buffer._buffer.append(stream_msg)
        
        # Execute
        result = message_buffer.poll()
        
        # Assert
        assert len(result) == 5  # Should return max_poll_count
        assert len(message_buffer._buffer) == 3  # 3 messages should remain
        for i, msg in enumerate(result):
            assert msg.key == f"key{i}"

    def test_poll_empty_buffer_times_out(self, message_buffer):
        """Test polling empty buffer times out and returns empty list."""
        # Execute
        start_time = time.time()
        result = message_buffer.poll()
        end_time = time.time()
        
        # Assert
        assert result == []
        assert (end_time - start_time) >= message_buffer._poll_timeout_sec

    @patch('time.sleep')  # Mock sleep to speed up test
    def test_poll_partial_timeout_returns_available_messages(self, mock_sleep, message_buffer):
        """Test polling that times out while waiting for more messages."""
        # Setup - add 2 messages (less than max_poll_count)
        for i in range(2):
            stream_msg = StreamMessage(key=f"key{i}", value=f"value{i}".encode(), headers={})
            message_buffer._buffer.append(stream_msg)
        
        # Mock time to simulate timeout
        with patch('time.time') as mock_time:
            mock_time.side_effect = [0, 0.5, 1.5]  # Start, during poll, timeout
            
            # Execute
            result = message_buffer.poll()
        
        # Assert
        assert len(result) == 2
        assert len(message_buffer._buffer) == 0

    def test_deserialize_headers_without_properties(self, message_buffer):
        """Test header deserialization when properties is None."""
        # Execute
        result = message_buffer._deserialize_headers(None)
        
        # Assert
        assert result == {}

    def test_deserialize_headers_with_user_properties(self, message_buffer):
        """Test header deserialization with UserProperty."""
        # Setup
        properties = Mock(spec=Properties)
        properties.json.return_value = {
            'UserProperty': [('header1', 'value1'), ('header2', 'value2')]
        }
        
        # Execute
        result = message_buffer._deserialize_headers(properties)
        
        # Assert
        expected = {'header1': b'value1', 'header2': b'value2'}
        assert result == expected

    def test_buffer_max_length_enforcement(self):
        """Test that buffer enforces maximum length."""
        # Setup - create buffer and fill beyond max length
        buffer = _MessageBuffer(max_poll_count=5, poll_timeout_sec=1)
        max_len = _MessageBuffer._BUFFER_MAX_LEN
        
        # Add more messages than max length
        for i in range(max_len + 10):
            stream_msg = StreamMessage(key=f"key{i}", value=f"value{i}".encode(), headers={})
            buffer._buffer.append(stream_msg)
        
        # Assert
        assert len(buffer._buffer) == max_len
        # First 10 messages should be dropped, last messages should remain
        assert buffer._buffer[0].key == "key10"
        assert buffer._buffer[-1].key == f"key{max_len + 9}"
