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

"""Redis Streams sink for VLM-enhanced Alert and Incident results.

Selected with ``vlm_enhanced_sink.type: redisStream``. Elasticsearch remains
the default and Kafka the alternative broker.

Payloads are the same protobuf messages :class:`VLMEnhancedKafkaSink` produces,
wrapped in the MDX stream envelope, so the Logstash ``redis_stream`` input can
decode ``mdx-vlm-incidents`` and ``mdx-vlm-alerts`` in Redis mode exactly as it
does in Kafka mode.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from mdx.redis_stream_broker import (
    RedisStreamBroker,
    require_redis_endpoint,
    resolve_redis_config,
)
from mdx.stream_routing import require_stream_name
from utils.schema_util import (
    convert_behavior_to_protobuf_behavior,
    convert_incident_to_protobuf_incident,
    get_nested_field,
)

from .sink_base import VLMEnhancedSink, document_id, log_enriched_event

DEFAULT_INCIDENT_STREAM = "mdx-vlm-incidents"
DEFAULT_ALERT_STREAM = "mdx-vlm-alerts"

#: Payload encodings :meth:`VLMEnhancedRedisStreamSink._serialize` can produce.
#: Validated at construction because the alternative is discovering a typo when
#: the first verdict is already in hand: an unrecognized value used to fall
#: through to protobuf, so ``payload_format: JSON `` with a stray space
#: published protobuf to a consumer expecting JSON.
SUPPORTED_PAYLOAD_FORMATS = ("protobuf", "json")

#: Shortest gap between two recovery probes in :meth:`is_healthy`.
#:
#: Sized against the readiness timer that is the intended caller -- one probe per
#: poll, unchanged -- while bounding what any other caller can cost. A second is
#: also short enough that a broker which has come back is reported ready on the
#: next read of readiness rather than the one after.
PROBE_INTERVAL_SECONDS = 1.0


def _build_route(
    kind: str,
    route_cfg: Dict[str, Any],
    connection: Dict[str, Any],
    shipped_stream: str,
) -> Dict[str, Any]:
    """Resolve and validate one output route.

    Four things are checked here that used to be accepted and then misbehave
    at publish time or, worse, not at all:

    * ``stream`` must be **present**. There is no default: where a verdict is
      published is not something to infer. A config that names no stream is one
      whose author either meant a stream and lost it to an unresolved variable,
      or never knew the key existed — and inferring ``mdx-vlm-incidents`` for
      either of them produces a deployment that publishes, reports healthy, and
      is read by nobody. ``shipped_stream`` is what the shipped configs use, so
      it is named in the message as the likely intent rather than applied as one.
    * a **present but blank** ``stream`` is rejected for the same reason: an
      unresolved variable in a rendered config renders as ``""``.
    * ``message_type`` must agree with the route it is on. It is what selects
      the protobuf schema, so ``incident`` on the alert route serializes a
      Behavior document as an Incident — which succeeds, publishes, and is
      undecodable downstream.
    * ``payload_format`` must be one this sink can produce, instead of anything
      unrecognized meaning protobuf.
    """
    setting = f"vlm_enhanced_sink.{kind}.redisStream.stream"

    if "stream" not in route_cfg:
        raise ValueError(
            f"{setting} is not set. Every {kind} verdict this sink produces goes "
            f"to that stream, and there is no default to fall back to — one "
            f"would send them somewhere the deployment never named while "
            f"everything reported healthy. Set it to '{shipped_stream}', which is "
            f"what the shipped configs use, or to whatever your consumer reads."
        )

    stream = require_stream_name(
        route_cfg.get("stream"),
        setting,
        remedy=(f"Give it a stream name — '{shipped_stream}' is what the shipped "
                f"configs use. Removing the key is not an alternative: this sink "
                f"does not guess where {kind} verdicts go."),
    )

    message_type = str(route_cfg.get("message_type", kind)).strip().lower()
    if message_type != kind:
        raise ValueError(
            f"vlm_enhanced_sink.{kind}.redisStream.message_type is "
            f"'{message_type}' on the {kind} route. message_type selects the "
            f"protobuf schema, so anything other than '{kind}' here serializes "
            f"the document with the wrong schema and publishes it anyway."
        )

    payload_format = str(
        route_cfg.get("payload_format") or connection.get("payload_format") or "protobuf"
    ).strip().lower()
    if payload_format not in SUPPORTED_PAYLOAD_FORMATS:
        raise ValueError(
            f"vlm_enhanced_sink.{kind}.redisStream.payload_format is "
            f"'{payload_format}'; supported values are "
            f"{', '.join(SUPPORTED_PAYLOAD_FORMATS)}"
        )

    return {
        "stream": stream,
        "key_field": route_cfg.get("key_field"),
        "message_type": message_type,
        "payload_format": payload_format,
    }


def resolve_routes(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The two output routes this sink would publish on, or raise.

    Everything here is a predicate over configuration -- no connection, no
    client, nothing to clean up on the way out -- which is what lets startup
    validation run it before any of this is built. It is called from
    :meth:`VLMEnhancedRedisStreamSink.from_config` as well, so the answer
    validation gives and the one the sink is built from cannot drift.

    Separated because of where the sink is constructed: inside a forked pipeline
    child, well after the API and the metrics port are up. A route with no stream
    raised there, which crash-looped the child rather than failing the container,
    and left an operator reading a traceback about a sink class instead of the
    name of the key they had misspelt.
    """
    sink_root = config.get("vlm_enhanced_sink", {}) or {}
    connection = sink_root.get("redisStream") or {}
    incident_cfg = (sink_root.get("incident") or {}).get("redisStream", {}) or {}
    alert_cfg = (sink_root.get("alert") or {}).get("redisStream", {}) or {}

    routes = {
        "incident": _build_route(
            "incident", incident_cfg, connection, DEFAULT_INCIDENT_STREAM,
        ),
        "alert": _build_route(
            "alert", alert_cfg, connection, DEFAULT_ALERT_STREAM,
        ),
    }

    if routes["incident"]["stream"] == routes["alert"]["stream"]:
        # Both kinds on one stream means a consumer reading it cannot tell
        # which schema to decode an entry with: the envelope carries no kind
        # and the two protobuf messages are not distinguishable by parsing.
        raise ValueError(
            f"vlm_enhanced_sink.incident.redisStream.stream and "
            f"vlm_enhanced_sink.alert.redisStream.stream are both "
            f"'{routes['incident']['stream']}'. The kinds serialize to different "
            f"protobuf schemas and the MDX envelope does not record which, "
            f"so a single stream cannot carry both."
        )

    return routes


class VLMEnhancedRedisStreamSink(VLMEnhancedSink):
    """Publishes VLM-verified events to per-kind Redis Streams."""

    transport_label = "redis_stream"

    #: A broker lost after startup is an ordinary runtime condition here, and
    #: nothing raises to announce it: publishes fail and are counted. Readiness
    #: only learns about it if something re-reads :meth:`is_healthy`.
    needs_readiness_polling = True

    def __init__(
        self,
        broker: RedisStreamBroker,
        incident_route: Dict[str, Any],
        alert_route: Dict[str, Any],
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> None:
        super().__init__(
            alert_config_store=alert_config_store,
            category_mapping=category_mapping,
        )
        self._broker = broker
        self._incident_route = incident_route
        self._alert_route = alert_route
        #: When the recovery probe in :meth:`is_healthy` last ran, so a caller
        #: that is not the readiness timer cannot turn one probe per interval
        #: into one per call.
        self._last_probe_at = 0.0

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> "VLMEnhancedRedisStreamSink":
        """Construct the sink and its Redis connection from configuration."""
        routes = resolve_routes(config)
        connection = (config.get("vlm_enhanced_sink", {}) or {}).get("redisStream") or {}

        merged = resolve_redis_config(config, override=connection)
        # Before the reachability check below, so a deployment that never set a
        # host is told that rather than being shown a failed connection to
        # localhost, which reads as "Redis is down" and sends the operator to
        # the wrong machine.
        require_redis_endpoint(merged, "vlm_enhanced_sink.type")

        broker = RedisStreamBroker(merged)

        # Fail fast, the same way the event-bridge Redis sink does. Per-write
        # retries are bounded and cannot ride out a wrong host or a Redis that
        # is not running, so without this check the service starts cleanly and
        # then discards every verdict it produces until Redis appears — a
        # deployment that looks healthy and delivers nothing.
        if not broker.ping():
            raise ConnectionError(
                f"Unable to reach Redis at {broker.host}:{broker.port} for the "
                f"VLM-enhanced sink; check the redis connection settings or "
                f"select a different vlm_enhanced_sink.type"
            )

        return cls(
            broker=broker,
            incident_route=routes["incident"],
            alert_route=routes["alert"],
            category_mapping=category_mapping,
            alert_config_store=alert_config_store,
        )

    def is_healthy(self) -> bool:
        """Whether Redis is reachable, re-probing once it has stopped being.

        ``None`` (nothing attempted yet) counts as healthy: the constructor
        already pinged, and reporting unready before the first publish would
        make an idle deployment look broken.

        The ping is what makes the unhealthy state recoverable. A sink is
        passive -- the flag only changes on a command, and the only commands it
        issues are publishes -- so after a broker blip it stayed unhealthy until
        the next verdict happened to be published. With no traffic that is
        never: a deployment whose Redis came back a second later reported 503
        indefinitely and had to be restarted.

        Rate-limited to one probe per :data:`PROBE_INTERVAL_SECONDS` because
        this is not only called by the readiness timer. The consumer-group
        rebalance callback reads it too, through the assignment-state publish,
        and a PING against a host that is not answering costs a socket timeout
        there -- inside the window the group allows a member before it is
        evicted. Between probes the answer is the flag, which is what it was
        before this method pinged at all.
        """
        if self._broker.connection_healthy is not False:
            return True
        now = time.monotonic()
        if now - self._last_probe_at < PROBE_INTERVAL_SECONDS:
            return False
        self._last_probe_at = now
        return self._broker.ping()

    def _store_success(
        self,
        event_kind: str,
        document: Dict[str, Any],
        raw_vlm_response: Any,
        user_prompt: str,
    ) -> None:
        self._publish(event_kind, document)

    def _store_error(
        self,
        event_kind: str,
        document: Dict[str, Any],
        error_payload: Dict[str, Any],
    ) -> None:
        self._publish(event_kind, document)

    def _resolve_key(self, route: Dict[str, Any], document: Dict[str, Any]) -> str:
        key_field = route.get("key_field")
        if key_field:
            key_value = get_nested_field(document, key_field)
            if key_value is not None:
                return str(key_value)
        # Prefer the sensor id so cohorts stay co-located, mirroring the
        # partition-key contract the Kafka transport relies on.
        sensor_id = document.get("sensorId") or (document.get("sensor") or {}).get("id")
        return str(sensor_id or document.get("id") or document.get("incidentId") or "")

    @staticmethod
    def _serialize(route: Dict[str, Any], document: Dict[str, Any]) -> bytes:
        if route.get("payload_format") == "json":
            return json.dumps(document).encode("utf-8")

        message_type = (route.get("message_type") or "incident").lower()
        if message_type == "incident":
            proto_msg = convert_incident_to_protobuf_incident(document)
        elif message_type == "alert":
            proto_msg = convert_behavior_to_protobuf_behavior(document)
        else:
            raise ValueError(f"Unsupported message_type for Redis Stream route: {message_type}")
        return proto_msg.SerializeToString()

    def _publish(self, event_kind: str, document: Dict[str, Any]) -> None:
        route = self._alert_route if event_kind == 'alert' else self._incident_route
        stream = route.get("stream")
        if not stream:
            raise ValueError("Redis Stream route requires a stream name")

        key = self._resolve_key(route, document)

        # Apply the operator-configured output category before serialization.
        # Read through ``_resolve_output_category`` so live PUT API edits are
        # picked up. Written in place because ``document`` is this sink's own
        # event: ``build_vlm_enriched_event`` deep-copies the pipeline's message,
        # so nothing outside this publish shares it, and the dedup fingerprint
        # was computed upstream of the copy.
        if 'category' in document:
            original_category = document['category']
            resolved = self._resolve_output_category(original_category)
            if resolved and resolved != original_category:
                document['category'] = resolved
                self._logger.debug(
                    "Category mapped for output: %s -> %s", original_category, resolved
                )

        try:
            self._logger.info(
                "Publishing VLM-enhanced event to Redis Stream event_type=%s stream=%s",
                event_kind,
                stream,
            )
            entry_id = self._broker.add(stream, self._serialize(route, document), key=key)
            if entry_id is None:
                # Redis is the only destination for a redisStream sink, so this
                # discards a verdict the VLM already paid to produce. Counted as
                # a lost verdict rather than only a failed write, and the sink's
                # health now reads unhealthy so pipeline readiness reports the
                # degradation instead of leaving it to whoever reads the logs.
                self._record_drop(event_kind)
                self._logger.error(
                    "Dropped VLM-enhanced %s: Redis stream write failed after retries",
                    event_kind,
                    extra={"incident_id": document_id(document), "stream": stream},
                )
                return
            log_enriched_event(
                self._logger, "RedisStream", document_id(document), document,
            )
        except Exception:
            self._record_drop(event_kind)
            self._logger.error(
                "Failed to publish VLM-enhanced event to Redis Stream",
                extra={"incident_id": document_id(document), "stream": stream},
                exc_info=True,
            )
            return

    def _record_drop(self, event_kind: str) -> None:
        """Count a verdict that never reached its stream. Never raises."""
        try:
            from metrics.recorder import inc_terminal_publish_dropped
            inc_terminal_publish_dropped(self.transport_label, event_kind)
        except Exception:  # pragma: no cover - metrics must never break publish
            pass
