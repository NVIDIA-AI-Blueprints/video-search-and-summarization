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
import random
import time

from collections import deque
from paho.mqtt.client import Client, ConnectFlags, DisconnectFlags, MQTTMessage, CONNACK_ACCEPTED, MQTT_ERR_SUCCESS
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from typing import Any

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import StreamMessage
from mdx.analytics.core.stream.source.source_base import Source

logger = logging.getLogger(__name__)


class SourceMQTT(Source):

    def __init__(self, config: AppConfig) -> None:
        """Initialize MQTT source with configuration.

        :param AppConfig config: Application configuration containing MQTT settings
        """

        self._config = config.mqtt
        self._consumer_config = self._config.consumer
        self._get_topic_pattern = config.get_mqtt_topic

        self._buffers: dict[str, _MessageBuffer] = {}
        self._client: Client | None = None


    def read(
        self,
        src_key: str,
        group_id_suffix: str | None = None
    ) -> list[StreamMessage]:
        """Read messages from MQTT topic associated with the source key.

        :param str src_key: Source key to identify the MQTT topic
        :param str | None group_id_suffix: Optional suffix for client group ID
        :return list[StreamMessage]: List of stream messages from the topic
        :raises ValueError: If no topic/pattern found for the given source key
        """

        topic = self._get_topic_pattern(src_key)
        if not topic:
            raise ValueError(f"Could not find a topic/pattern with key: {src_key}")

        client_id = self._get_group_id(topic, self._config.clientId, group_id_suffix)

        if not self._client:
            self._init_client(client_id)

        self._subscribe(topic, client_id)

        if (buffer := self._buffers.get(topic)):
            return buffer.poll()

        logger.warn(f'No messages available for {topic} yet.')
        return []


    def _on_message(
        self,
        client: Client,  # noqa: F841
        userdata: Any,  # noqa: F841
        msg: MQTTMessage
    ) -> None:
        """Callback function called when a message is received from MQTT broker.

        :param Client client: MQTT client instance
        :param Any userdata: User data passed to callbacks
        :param MQTTMessage msg: MQTT message received
        """

        logger.debug(f'Received message with id {msg.mid} on topic {msg.topic}, retained={msg.retain}')

        if not msg.retain:
            if (buffer := self._buffers.get(msg.topic)):
                buffer.append(msg)
            else:
                raise Exception(f'[FATAL] Unexpected :: msg received before subscribing to topic {msg.topic}.')


    def _on_connect(
        self,
        client: Client,  # noqa: F841
        userdata: Any,  # noqa: F841
        conn_flags: ConnectFlags,  # noqa: F841
        rc: ReasonCode,
        props: Properties | None  # noqa: F841
    ) -> None:
        """Callback function called when MQTT client connects to broker.

        :param Client client: MQTT client instance
        :param Any userdata: User data passed to callbacks
        :param ConnectFlags conn_flags: Connection flags
        :param ReasonCode rc: Reason code for connection result
        :param Properties | None props: Connection properties
        :raises Exception: If connection fails (rc != CONNACK_ACCEPTED)
        """

        if rc == CONNACK_ACCEPTED:
            logger.info('Consumer connected to MQTT broker.')

        else:
            raise Exception(f'MQTT Connection Error in consumer - [code:{rc.value}] {rc!s}')


    def _on_disconnect(
        self,
        client: Client,  # noqa: F841
        userdata: Any,  # noqa: F841
        conn_flags: DisconnectFlags,  # noqa: F841
        rc: ReasonCode,
        props: Properties | None  # noqa: F841
    ) -> None:
        """Callback function called when MQTT client disconnects from broker.

        :param Client client: MQTT client instance
        :param Any userdata: User data passed to callbacks
        :param DisconnectFlags conn_flags: Disconnection flags
        :param ReasonCode rc: Reason code for disconnection
        :param Properties | None props: Disconnection properties
        :raises Exception: If disconnection fails (rc != MQTT_ERR_SUCCESS)
        """

        if self._client and self._client.is_connected():

            if rc == MQTT_ERR_SUCCESS:
                logger.info('Consumer disconnected from MQTT broker.')

            else:
                raise Exception(f'MQTT Error while disconnecting consumer - [code:{rc.value}] {rc!s}')


    def _subscribe(self, topic: str, client_id: str) -> None:
        """Subscribe MQTT client to a specific topic.

        :param str topic: MQTT topic to subscribe to
        :param str client_id: Client identifier for logging purposes
        :raises Exception: If attempting to subscribe before client is connected
        """

        if topic in self._buffers:
            return

        if self._client:

            max_attempts = 3
            attempts = 0

            while not self._client.is_connected() and attempts < max_attempts:
                time.sleep(0.1 + (random.random() * 0.4))
                attempts += 1

            if self._client.is_connected():

                rc, _ = self._client.subscribe(topic, qos = self._consumer_config.qos)

                if rc == MQTT_ERR_SUCCESS:
                    self._buffers[topic] = _MessageBuffer(self._consumer_config.maxPollCount, self._consumer_config.pollTimeoutSec)
                    logger.info(f'Subscribed client {client_id} to MQTT topic {topic}')

                else:
                    logger.error(f'Failed to subscribe client {client_id} to topic {topic} - [code:{rc}]')

            else:
                raise Exception(f'[FATAL] Failed attempt to subscribe to {topic} before MQTT client {client_id} is connected.')

        else:
            raise Exception(f'[FATAL] MQTT consumer `{client_id}` does not exist.')


    def _init_client(self, client_id: str):
        """Initialize and configure MQTT client with callbacks and connection.

        Retries the connect on transient socket errors (``OSError``,
        ``TimeoutError``) using ``retryMaxAttempts`` and ``retryIntervalSec``
        from the consumer config. Raised exceptions inside ``_on_connect``
        (e.g., ``CONNACK_REFUSED_*``) propagate without retry — those are
        config/auth issues, not transient outages.

        :param str client_id: Unique identifier for the MQTT client
        :raises RuntimeError: If the broker cannot be reached after
            ``retryMaxAttempts`` attempts.
        """

        max_attempts = self._consumer_config.retryMaxAttempts
        wait_between = self._consumer_config.retryIntervalSec
        last_err: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                self._client = Client(
                    callback_api_version = CallbackAPIVersion.VERSION2,
                    client_id = client_id,
                    protocol = MQTTProtocolVersion.MQTTv5
                )

                self._client.on_connect = self._on_connect
                self._client.on_message = self._on_message
                self._client.on_disconnect = self._on_disconnect

                self._client.connect(
                    host = self._config.host,
                    port = self._config.port,
                    keepalive = self._config.keepAliveSec,
                    clean_start = True
                )

                self._client.loop_start()
                return

            except OSError as e:
                last_err = e
                logger.warning(
                    f"MQTT connect failed on attempt {attempt} of {max_attempts}: {e}",
                    exc_info=True,
                )
                # Tear down the partially-created client so the next attempt
                # starts fresh. paho-mqtt's loop_stop() returns an error code
                # rather than raising if the loop was never started, so this is
                # safe regardless of whether connect() got far enough to start
                # the network thread.
                if self._client is not None:
                    self._client.loop_stop()
                    self._client = None

                if attempt < max_attempts:
                    logger.info(f"Waiting {wait_between} seconds before retrying...")
                    time.sleep(wait_between)

        raise RuntimeError(
            f"FATAL - Failed to connect to MQTT broker after {max_attempts} attempts\n"
            f"  Host: {self._config.host}:{self._config.port}\n"
            f"  Client ID: {client_id}\n"
            f"  Last error: {last_err}"
        ) from last_err


    def close(self) -> None:
        """Close MQTT client connection and stop the network loop."""

        if self._client:
            self._client.disconnect()
            self._client.loop_stop()


class _MessageBuffer:

    _BUFFER_MAX_LEN: int = 100000

    def __init__(self, max_poll_count: int, poll_timeout_sec: float) -> None:
        """Initialize message buffer with polling configuration.

        :param int max_poll_count: Maximum number of messages to return in a single poll
        :param float poll_timeout_sec: Timeout in seconds for polling operations
        """

        self._max_poll_count = max_poll_count
        self._poll_timeout_sec = poll_timeout_sec

        self._buffer: deque[StreamMessage] = deque(maxlen = _MessageBuffer._BUFFER_MAX_LEN)


    def append(self, msg: MQTTMessage) -> None:
        """Add MQTT message to the buffer as a StreamMessage.

        :param MQTTMessage msg: MQTT message to add to buffer
        """

        msg_headers = self._deserialize_headers(msg.properties)

        if (msg_key := msg_headers.get('key')):
            del msg_headers['key']
        
        # Try to extract timestamp from headers first, otherwise use default value -1
        timestamp = -1
        if 'timestamp' in msg_headers:
            try:
                # Assume timestamp in headers is in milliseconds as bytes
                timestamp = int(msg_headers['timestamp'].decode('utf-8'))
                del msg_headers['timestamp']  # Remove from headers since we're storing it separately
            except Exception as e:
                logger.warning(f'Failed to extract timestamp from mqtt message headers: {msg_headers} - {e}')

        self._buffer.append(StreamMessage(key = msg_key, value = msg.payload, headers = msg_headers, timestamp = timestamp))


    def poll(self) -> list[StreamMessage]:
        """Poll messages from buffer up to max count or timeout.

        :return list[StreamMessage]: List of stream messages from the buffer
        """

        batch = []
        start_time = time.time()

        while len(batch) < self._max_poll_count:

            try:
                batch.append(self._buffer.popleft())

            except IndexError:

                if ((time.time() - start_time) >= self._poll_timeout_sec):
                    logger.debug(f'Poll timeout before batch complete, returning current batch')
                    return batch

                time.sleep(0.001)

        return batch


    def _deserialize_headers(self, properties: Properties | None = None) -> dict[str, bytes]:
        """Deserialize MQTT message properties to header dictionary.

        :param Properties | None properties: MQTT message properties containing user properties
        :return dict[str, bytes]: Dictionary of headers with string keys and bytes values
        """

        headers: dict[str, bytes] = {}

        if properties and (user_props := properties.json().get('UserProperty')):    # UserProperty: list[tuple[str, str]]

            headers = { k: v.encode('utf-8') for k, v in user_props }

        return headers
