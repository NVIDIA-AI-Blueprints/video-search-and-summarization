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

import logging
import time

from paho.mqtt.client import Client, ConnectFlags, DisconnectFlags, CONNACK_ACCEPTED, MQTT_ERR_SUCCESS
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from typing import Any
from collections.abc import Callable, Mapping

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.sink.sink_base import Sink

logger = logging.getLogger(__name__)


class SinkMQTT(Sink):

    def __init__(self, config: AppConfig) -> None:
        """Initialize the MQTT sink with application configuration.
        
        :param AppConfig config: Application configuration containing MQTT settings
        """

        self._config = config.mqtt
        self._producer_config = self._config.producer
        self._get_topic = config.get_mqtt_topic

        self._client: Client | None = None


    def write(
        self,
        dest_key: str,
        messages: list[Any],
        value_serializer: Callable,
        key_extractor: Callable | None = None,
        key_serializer: Callable | None = None,
        headers: Mapping[str, str | bytes] | None = None
    ) -> None:
        """Write multiple messages to an MQTT topic.
        
        :param str dest_key: Key used to determine the destination topic
        :param list[Any] messages: List of messages to publish
        :param Callable value_serializer: Function to serialize message values
        :param Callable | None key_extractor: Optional function to extract keys from messages
        :param Callable | None key_serializer: Optional function to serialize message keys
        :param Mapping[str, str | bytes] | None headers: Optional headers to include with messages
        :raises ValueError: If no topic can be found for the given dest_key
        """

        topic = self._get_topic(dest_key)
        if not topic:
            raise ValueError(f"Could not find a topic with key: {dest_key}")

        if not self._client:
            self._init_client()

        for message in messages:

            key = None

            if key_extractor:
                key = key_extractor(message)
                if key_serializer:
                    key = key_serializer(key)

            if key and not isinstance(key, bytes):
                raise ValueError(f"Message key must be of type `bytes`, incorrect type received - {type(key)}")

            self._client.publish( # type: ignore 
                topic = topic,
                payload = value_serializer(message),
                properties = self._serialize_properties(headers, key),
                qos = self._producer_config.qos,
                retain = self._producer_config.retain
            )


    def write_msg(
        self,
        dest_key: str,
        message: bytes,
        key: bytes | None,
        headers: Mapping[str, str | bytes] | None = None
    ) -> None:
        """Write a single serialized message to an MQTT topic.
        
        :param str dest_key: Key used to determine the destination topic
        :param bytes message: Serialized message payload
        :param bytes | None key: Optional message key
        :param Mapping[str, str | bytes] | None headers: Optional headers to include with the message
        :raises ValueError: If no topic can be found for the given dest_key
        """

        topic = self._get_topic(dest_key)
        if not topic:
            raise ValueError(f"Could not find a topic with key: {dest_key}")

        if not self._client:
            self._init_client()

        self._client.publish( # type: ignore
            topic = topic,
            payload = message,
            properties = self._serialize_properties(headers, key),
            qos = self._producer_config.qos,
            retain = self._producer_config.retain
        )


    def _on_publish(
        self,
        client: Client,  # noqa: F841
        userdata: Any,  # noqa: F841
        mid: int,
        rc: ReasonCode,
        props: Properties | None  # noqa: F841
    ) -> None:
        """Callback function called when a message is published to the broker.
        
        :param Client client: The MQTT client instance
        :param Any userdata: User data passed to the client
        :param int mid: Message ID of the published message
        :param ReasonCode rc: Reason code indicating publish result
        :param Properties | None props: MQTT properties associated with the publish
        """

        if rc == MQTT_ERR_SUCCESS:
            logger.debug(f'Published message with id {mid} successfully')
        else:
            if rc.value == 16:
                logger.debug(f'Msg with id {mid} published to the broker but no matching subscribers are active.')
            else:
                logger.error(f'Failed to publish message with id {mid} - [code:{rc.value}] {rc!s}')


    def _on_connect(
        self,
        client: Client,  # noqa: F841
        userdata: Any,  # noqa: F841
        conn_flags: ConnectFlags,  # noqa: F841
        rc: ReasonCode,
        props: Properties | None  # noqa: F841
    ) -> None:
        """Callback function called when the client connects to the broker.
        
        :param Client client: The MQTT client instance
        :param Any userdata: User data passed to the client
        :param ConnectFlags conn_flags: Connection flags returned by the broker
        :param ReasonCode rc: Reason code indicating connection result
        :param Properties | None props: MQTT properties associated with the connection
        :raises Exception: If connection to MQTT broker fails
        """

        if rc == CONNACK_ACCEPTED:
            logger.info('Producer connected to MQTT broker.')

        else:
            raise Exception(f'MQTT Connection Error in producer - [code:{rc.value}] {rc!s}')


    def _on_disconnect(
        self,
        client: Client,  # noqa: F841
        userdata: Any,  # noqa: F841
        conn_flags: DisconnectFlags,  # noqa: F841
        rc: ReasonCode,
        props: Properties | None  # noqa: F841
    ) -> None:
        """Callback function called when the client disconnects from the broker.
        
        :param Client client: The MQTT client instance
        :param Any userdata: User data passed to the client
        :param DisconnectFlags conn_flags: Disconnect flags
        :param ReasonCode rc: Reason code indicating disconnection result
        :param Properties | None props: MQTT properties associated with the disconnection
        :raises Exception: If disconnection from MQTT broker encounters an error
        """

        if self._client and self._client.is_connected():

            if rc == MQTT_ERR_SUCCESS:
                logger.info(f'Producer disconnected from MQTT broker.')

            else:
                raise Exception(f'MQTT Error while disconnecting producer - [code:{rc.value}] {rc!s}')


    def _init_client(self):
        """Initialize and configure the MQTT client with connection settings."""

        self._client = Client(
            callback_api_version = CallbackAPIVersion.VERSION2,
            protocol = MQTTProtocolVersion.MQTTv5
        )

        self._client.on_connect = self._on_connect
        self._client.on_publish = self._on_publish
        self._client.on_disconnect = self._on_disconnect

        self._client.connect(
            host = self._config.host,
            port = self._config.port,
            keepalive = self._config.keepAliveSec,
            clean_start = True
        )

        self._client.loop_start()


    def _serialize_properties(self, headers: Mapping[str, str | bytes] | None = None, key: bytes | None = None) -> Properties:
        """Serialize headers and optional key into MQTT properties.
        
        :param Mapping[str, str | bytes] | None headers: Optional headers to include as user properties
        :param bytes | None key: Optional message key to include as user property
        :return Properties: MQTT properties object with serialized headers and key
        """

        props = Properties(PacketTypes.PUBLISH)

        # Add timestamp in milliseconds as a user property
        timestamp = str(headers.get('timestamp', int(time.time() * 1000))) if headers else str(int(time.time() * 1000))
        props.UserProperty = ('timestamp', timestamp)

        if key:
            props.UserProperty = ('key', key.decode('utf-8'))

        if headers:
            for k, v in headers.items():
                # Skip timestamp since we already added it to the user property
                if k == 'timestamp':
                    continue
                props.UserProperty = (k, v.decode('utf-8') if isinstance(v, bytes) else v)

        return props


    def close(self) -> None:
        """Close the MQTT client connection and stop the network loop."""

        if self._client:
            self._client.disconnect()
            self._client.loop_stop()