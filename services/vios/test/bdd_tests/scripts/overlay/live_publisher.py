# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Live overlay metadata publisher for the VIOS live/webrtc overlay path (phase 2).

DeepStream publishes ``nv.Frame`` protobuf (ds_schema_2d) to a message broker;
VIOS's kafka/redis subscriber parses it via DsProtoParser into LiveMetadataStore,
and the live RTSP (``?bbox=1``) / WebRTC overlay draws it. This module builds the
same ``nv.Frame`` protobuf (verified wire-compatible via ``protoc --decode``) with a
centered 2D box and publishes it to Kafka at a target fps.

Because the live path matches metadata to the *current* frame PTS (wall-clock
epoch), we stamp each message with ``now`` and publish continuously while the test
reads the live stream.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

# Compiled from services/vios/src/framework/notification/ds_schema_2d.proto
sys.path.insert(0, str(Path(__file__).resolve().parent / "proto"))
import ds_schema_2d_pb2 as pb  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class LiveBoxSpec:
    sensor_id: str
    width: int = 1920
    height: int = 1080
    box_norm: Tuple[float, float, float, float] = (0.40, 0.40, 0.60, 0.60)
    obj_type: str = "Person"
    obj_id: str = "1"
    confidence: float = 0.99

    def pixel_box(self) -> Tuple[int, int, int, int]:
        l, t, r, b = self.box_norm
        return (int(round(l * self.width)), int(round(t * self.height)),
                int(round(r * self.width)), int(round(b * self.height)))


def build_nv_frame(spec: LiveBoxSpec, epoch_ms: int, frame_id: int = 0) -> bytes:
    """Serialize one nv.Frame protobuf with a centered 2D bbox at ``epoch_ms``."""
    f = pb.Frame()
    f.version = "4.0"
    f.id = str(frame_id)
    f.sensorId = spec.sensor_id
    f.timestamp.seconds = epoch_ms // 1000
    f.timestamp.nanos = (epoch_ms % 1000) * 1_000_000
    o = f.objects.add()
    o.id = spec.obj_id
    o.type = spec.obj_type
    o.confidence = spec.confidence
    lx, ty, rx, by = spec.pixel_box()
    o.bbox.leftX = float(lx)
    o.bbox.topY = float(ty)
    o.bbox.rightX = float(rx)
    o.bbox.bottomY = float(by)
    o.info["classConfidence"] = f"{spec.confidence:.6f}"
    return f.SerializeToString()


class KafkaBoxPublisher:
    """Publishes centered-box nv.Frame protobuf to a Kafka topic at a target fps.

    Runs until ``stop()``; intended to be started in a background thread while the
    test reads the live overlaid stream.
    """

    def __init__(self, brokers: str, topic: str, spec: LiveBoxSpec,
                 fps: float = 30.0, key_field: str = "sensor.id"):
        from confluent_kafka import Producer
        self._producer = Producer({"bootstrap.servers": brokers})
        self.topic = topic
        self.spec = spec
        self.fps = fps
        self._running = False
        self._sent = 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def publish_once(self, epoch_ms: Optional[int] = None) -> None:
        ts = epoch_ms if epoch_ms is not None else self._now_ms()
        payload = build_nv_frame(self.spec, ts, frame_id=self._sent)
        # Key by sensor id (message_broker_payload_key = sensor.id).
        self._producer.produce(self.topic, value=payload, key=self.spec.sensor_id)
        self._producer.poll(0)
        self._sent += 1

    def run(self, duration_s: float) -> int:
        """Publish at ``fps`` for ``duration_s`` seconds (blocking). Returns count."""
        self._running = True
        interval = 1.0 / self.fps
        end = time.monotonic() + duration_s
        next_t = time.monotonic()
        while self._running and time.monotonic() < end:
            self.publish_once()
            next_t += interval
            sleep = next_t - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
        self._producer.flush(5)
        logger.info("KafkaBoxPublisher sent %d frames to topic=%s", self._sent, self.topic)
        return self._sent

    def stop(self) -> None:
        self._running = False


class RedisBoxPublisher:
    """Publishes centered-box nv.Frame protobuf to a Redis *stream* at a target fps.

    VIOS's redis subscriber uses DeepStream's nvds_msgapi redis adaptor, which
    consumes via ``XREADGROUP`` from a stream named ``message_broker_topic_consumer``.
    The adaptor's entry schema is ``key <sensorId> value <protobuf> headers {}``, so we
    ``XADD <topic> MAXLEN ~ <cap> * key <sensorId> value <protobuf> headers {}`` and cap
    the stream (matching the adaptor's ``streamsize`` default) so a live consumer group
    reading ``>`` is never flooded with a stale backlog.
    """

    def __init__(self, host: str, port: int, topic: str, spec: LiveBoxSpec,
                 fps: float = 30.0, field: str = "value", maxlen: int = 10000):
        import redis
        self._r = redis.Redis(host=host, port=port)
        self.topic = topic
        self.spec = spec
        self.fps = fps
        self.field = field
        self.maxlen = maxlen
        self._running = False
        self._sent = 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def publish_once(self, epoch_ms: Optional[int] = None) -> None:
        ts = epoch_ms if epoch_ms is not None else self._now_ms()
        payload = build_nv_frame(self.spec, ts, frame_id=self._sent)
        # Exact nvds_msgapi redis schema the consumer parses:
        #   XADD <stream> MAXLEN ~ N * key <sensorId> value <payload> headers {}
        self._r.xadd(
            self.topic,
            {"key": self.spec.sensor_id, self.field: payload, "headers": "{}"},
            maxlen=self.maxlen, approximate=True,
        )
        self._sent += 1

    def run(self, duration_s: float) -> int:
        self._running = True
        interval = 1.0 / self.fps
        end = time.monotonic() + duration_s
        next_t = time.monotonic()
        while self._running and time.monotonic() < end:
            self.publish_once()
            next_t += interval
            sleep = next_t - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
        logger.info("RedisBoxPublisher XADDed %d frames to stream=%s", self._sent, self.topic)
        return self._sent

    def stop(self) -> None:
        self._running = False
