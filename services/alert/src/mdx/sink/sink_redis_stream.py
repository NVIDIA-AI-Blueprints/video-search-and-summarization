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
from typing import Any, Callable, Dict, List, Optional, Tuple

from mdx.redis_stream_broker import (
    RedisStreamBroker,
    require_redis_endpoint,
    resolve_redis_config,
)
from mdx.sink.sink_base import SinkBase
from mdx.stream_message import StreamMessage
from mdx.stream_routing import (
    EVENT_BRIDGE_SINK_ROUTES, require_distinct_streams, require_known_keys,
    require_stream_name,
)


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

        # Same guard as the source and the terminal sink: the endpoint check
        # belongs to the component, not to the one route that happens to run
        # validate_configuration first.
        merged = resolve_redis_config(config, 'redis_sink')
        require_redis_endpoint(merged, "event_bridge.sinkType")
        self.broker = RedisStreamBroker(merged)

        streams = section.get('streams') or {}
        # A key this sink does not read is refused rather than ignored: absent
        # means "do not publish that kind", so a misspelt one disables a route
        # while everything reports healthy.
        require_known_keys(
            streams, 'event_bridge.redis_sink.streams', EVENT_BRIDGE_SINK_ROUTES,
        )
        self.enhanced_anomaly_stream = self._route(streams, 'enhanced_anomaly')
        self.incidents_stream = self._route(streams, 'incidents')

        if not self.enhanced_anomaly_stream and not self.incidents_stream:
            raise ValueError(
                "event_bridge.redis_sink.streams must define 'enhanced_anomaly' and/or 'incidents'"
            )

        # The shared rule, rather than a fourth copy of it: two keys naming one
        # stream leaves a consumer guessing which shape an entry holds, because
        # the MDX envelope does not record it.
        require_distinct_streams(
            {
                'enhanced_anomaly': self.enhanced_anomaly_stream,
                'incidents': self.incidents_stream,
            },
            'event_bridge.redis_sink.streams',
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

    @staticmethod
    def _route(streams: dict, name: str) -> Optional[str]:
        """The stream configured for ``name``, or ``None`` if it names none.

        Reads both the ``<name>`` and legacy ``<name>_stream`` spellings.

        A key that is present but blank is rejected rather than read as absent.
        Absent means "do not publish this kind"; blank is what a rendered config
        produces for an unset variable. Treating them alike -- which an ``or``
        chain does -- let an unresolved variable disable one route silently while
        the other kept working, so the sink looked healthy and half its output
        went nowhere.
        """
        for key in (name, f"{name}_stream"):
            if key in streams:
                return require_stream_name(
                    streams[key],
                    f"event_bridge.redis_sink.streams['{key}']",
                    remedy=("Remove the key to not publish that kind, or give it "
                            "a stream name."),
                )
        return None

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

    def _publish_batch(
        self,
        stream: Optional[str],
        entries: List[Tuple[bytes, Any, str]],
    ) -> None:
        """Publish a whole write call's entries in one round trip.

        Every ``write_*`` method here is handed a list, and each was publishing
        it one XADD at a time — so a ten-event batch spent ten round trips of
        latency on the consume path, which the source cannot read past. Same
        entries, same order, same envelope; the accounting for a dropped entry
        is unchanged because the broker falls back to the individual path for
        anything the pipeline could not place.
        """
        if not entries:
            return
        if not stream:
            for _payload, _key, label in entries:
                self.logger.error(
                    "No Redis stream configured for %s; dropping message", label,
                )
            return

        entry_ids = self.broker.add_batch(
            stream, [(payload, key) for payload, key, _label in entries],
        )
        for (_payload, _key, label), entry_id in zip(entries, entry_ids):
            if entry_id is None:
                self.logger.error(
                    "Dropped %s: Redis stream '%s' rejected the write after retries",
                    label, stream,
                )
            else:
                self.logger.debug("Published %s to '%s' as %r", label, stream, entry_id)

    @staticmethod
    def _message_key(message: StreamMessage) -> str:
        return str(message.get_field('sensor_id', message.id) or '')

    def write(self, messages: List[StreamMessage]) -> None:
        """Write StreamMessage objects to the enhanced anomaly stream."""
        entries = []
        for message in messages or []:
            try:
                entries.append((
                    message.to_json().encode('utf-8'),
                    self._message_key(message),
                    f"StreamMessage {message.id}",
                ))
            except Exception as exc:
                # Serialization is per-message, so one unserializable message
                # must not cost the rest of the batch its publish.
                self.logger.error("Failed to serialize StreamMessage %s: %s", message.id, exc)
        self._publish_batch(self.enhanced_anomaly_stream, entries)

    def write_msg(self, messages: List[bytes]) -> None:
        """Write raw byte payloads to the enhanced anomaly stream."""
        self._publish_batch(self.enhanced_anomaly_stream, [
            (payload, str(index), f"raw message {index}")
            for index, payload in enumerate(messages or [])
        ])

    def write_incidents(self, messages: List[StreamMessage]) -> None:
        """Write StreamMessage objects to the incidents stream."""
        entries = []
        for message in messages or []:
            try:
                entries.append((
                    message.to_json().encode('utf-8'),
                    self._message_key(message),
                    f"incident {message.id}",
                ))
            except Exception as exc:
                self.logger.error("Failed to serialize incident %s: %s", message.id, exc)
        self._publish_batch(self.incidents_stream, entries)

    def write_data(self, data: List[dict], message_transform_func: Callable[[dict], Any] = None) -> None:
        """Publish dictionaries to the enhanced anomaly stream.

        Mirrors ``KafkaSink.write_data``: protobuf when a transform is supplied,
        JSON otherwise.
        """
        self._publish_batch(
            self.enhanced_anomaly_stream,
            self._serialize_all(data, message_transform_func, "anomaly"),
        )

    def write_incident_data(self, data: List[dict], message_transform_func: Callable = None) -> None:
        """Publish dictionaries to the incidents stream."""
        self._publish_batch(
            self.incidents_stream,
            self._serialize_all(data, message_transform_func, "incident"),
        )

    def _serialize_all(
        self,
        data: Optional[List[dict]],
        message_transform_func: Optional[Callable],
        label: str,
    ) -> List[Tuple[bytes, Any, str]]:
        """Serialize a write call's documents, skipping the ones that fail."""
        entries: List[Tuple[bytes, Any, str]] = []
        for item in data or []:
            try:
                entries.append((
                    self._serialize(item, message_transform_func),
                    self._nested_sensor_id(item),
                    label,
                ))
            except Exception as exc:
                self.logger.error("Failed to serialize %s: %s", label, exc, exc_info=True)
        return entries

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
