# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Redis Streams sink for the Alert event bridge.

Optional alternative to :class:`mdx.sink.sink_kafka.KafkaSink`, selected with
``event_bridge.sinkType: redisStream``. Kafka remains the default.

This is the event-bridge sink, which carries validation-error responses and the
legacy enhanced-anomaly path. VLM-enhanced results are published by the
separate ``vlm_enhanced_sink`` (see
:mod:`mdx.sink.vlm_enhanced_sink.sink_redis_stream`).
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from mdx.redis_stream_broker import RedisStreamBroker, resolve_redis_config
from mdx.sink.sink_base import SinkBase
from mdx.stream_message import StreamMessage


class SinkRedisStream(SinkBase):
    """Redis Streams sink implementation."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)

        section = (config.get('event_bridge') or {}).get('redis_sink') or {}
        if not section:
            raise ValueError(
                "event_bridge.redis_sink must be configured when sinkType is 'redisStream'"
            )

        self.broker = RedisStreamBroker(resolve_redis_config(config, 'redis_sink'))

        streams = section.get('streams') or {}
        # Accept both the ``<name>`` and legacy ``<name>_stream`` spellings.
        self.enhanced_anomaly_stream = (
            streams.get('enhanced_anomaly') or streams.get('enhanced_anomaly_stream')
        )
        self.incidents_stream = streams.get('incidents') or streams.get('incidents_stream')

        if not self.enhanced_anomaly_stream and not self.incidents_stream:
            raise ValueError(
                "event_bridge.redis_sink.streams must define 'enhanced_anomaly' and/or 'incidents'"
            )

        # Fail fast on an unreachable broker: per-write retries are bounded and
        # cannot ride out a misconfigured host, so surface it at boot instead.
        if not self.broker.ping():
            raise ConnectionError(
                f"Unable to reach Redis at {self.broker.host}:{self.broker.port} for the event bridge sink"
            )

        self.logger.info(
            "Redis Streams sink publishing to enhanced_anomaly='%s' incidents='%s'",
            self.enhanced_anomaly_stream, self.incidents_stream,
        )

    def _publish(self, stream: Optional[str], payload: bytes, key: Any, label: str) -> None:
        if not stream:
            self.logger.error("No Redis stream configured for %s; dropping message", label)
            return
        entry_id = self.broker.add(stream, payload, key=key)
        if entry_id is None:
            # The broker already retried and counted the drop; log here too so
            # the loss is visible against the stream and message it belongs to
            # rather than only as a metric.
            self.logger.error(
                "Dropped %s: Redis stream '%s' rejected the write after retries", label, stream
            )
            return
        self.logger.debug("Published %s to '%s' as %r", label, stream, entry_id)

    @staticmethod
    def _message_key(message: StreamMessage) -> str:
        return str(message.get_field('sensor_id', message.id) or '')

    def write(self, messages: List[StreamMessage]) -> None:
        """Write StreamMessage objects to the enhanced anomaly stream."""
        for message in messages or []:
            try:
                self._publish(
                    self.enhanced_anomaly_stream,
                    message.to_json().encode('utf-8'),
                    self._message_key(message),
                    f"StreamMessage {message.id}",
                )
            except Exception as exc:
                self.logger.error("Failed to publish StreamMessage %s: %s", message.id, exc)

    def write_msg(self, messages: List[bytes]) -> None:
        """Write raw byte payloads to the enhanced anomaly stream."""
        for index, payload in enumerate(messages or []):
            try:
                self._publish(self.enhanced_anomaly_stream, payload, str(index), f"raw message {index}")
            except Exception as exc:
                self.logger.error("Failed to publish raw message %s: %s", index, exc)

    def write_incidents(self, messages: List[StreamMessage]) -> None:
        """Write StreamMessage objects to the incidents stream."""
        for message in messages or []:
            try:
                self._publish(
                    self.incidents_stream,
                    message.to_json().encode('utf-8'),
                    self._message_key(message),
                    f"incident {message.id}",
                )
            except Exception as exc:
                self.logger.error("Failed to publish incident %s: %s", message.id, exc)

    def write_data(self, data: List[dict], message_transform_func: Callable[[dict], Any] = None) -> None:
        """Publish dictionaries to the enhanced anomaly stream.

        Mirrors ``KafkaSink.write_data``: protobuf when a transform is supplied,
        JSON otherwise.
        """
        for item in data or []:
            try:
                self._publish(
                    self.enhanced_anomaly_stream,
                    self._serialize(item, message_transform_func),
                    self._nested_sensor_id(item),
                    "anomaly",
                )
            except Exception as exc:
                self.logger.error("Failed to publish anomaly: %s", exc, exc_info=True)

    def write_incident_data(self, data: List[dict], message_transform_func: Callable = None) -> None:
        """Publish dictionaries to the incidents stream."""
        for item in data or []:
            try:
                self._publish(
                    self.incidents_stream,
                    self._serialize(item, message_transform_func),
                    self._nested_sensor_id(item),
                    "incident",
                )
            except Exception as exc:
                self.logger.error("Failed to publish incident: %s", exc, exc_info=True)

    @staticmethod
    def _serialize(item: dict, message_transform_func: Optional[Callable]) -> bytes:
        if message_transform_func:
            return message_transform_func(item).SerializeToString()
        return json.dumps(item).encode('utf-8')

    @staticmethod
    def _nested_sensor_id(item: Dict[str, Any]) -> str:
        return str(item.get('sensorId') or (item.get('sensor') or {}).get('id') or '')

    def close(self) -> None:
        """Release the Redis connection."""
        self.broker.close()
