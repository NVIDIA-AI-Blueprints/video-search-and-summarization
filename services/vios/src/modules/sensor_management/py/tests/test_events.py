# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Event-payload parity tests against golden strings captured from a live C++ deployment.

Captured 2026-06-09 from the `vst_events` Redis stream (field "sensor.id") of the stream-processing
stack, by adding then deleting an RTSP sensor (password "Sup3rSecret!23"). These golden strings are
the exact wire bytes; serialize_event() must reproduce them byte-for-byte (alphabetical keys,
compact separators).
"""
from __future__ import annotations

import pytest

from sensor_ms.config import Config
from sensor_ms.events.publisher import ChangeEvent, EventPublisher, build_payload, serialize_event

CREATED = "2026-06-09T07:49:54Z"

# --- exact strings captured from Redis (created_at normalized to CREATED) ---
GOLDEN_ADD = (
    '{"alert_type":"camera_status_change","created_at":"2026-06-09T07:49:54Z",'
    '"event":{"camera_id":"8b678171-57ef-40bc-8626-66f5883aa5f6",'
    '"camera_name":"crypto-parity-test","camera_url":"","change":"camera_add","tags":""},'
    '"source":"vst"}'
)
GOLDEN_PROXY = (
    '{"alert_type":"camera_status_change","created_at":"2026-06-09T07:49:54Z",'
    '"event":{"camera_id":"8b678171-57ef-40bc-8626-66f5883aa5f6",'
    '"camera_name":"crypto-parity-test",'
    '"camera_url":"rtsp://admin:Sup3rSecret!23@192.0.2.55:554/stream1",'
    '"change":"camera_proxy","metadata":{"codec":"h264","framerate":"","resolution":""},'
    '"tags":""},"source":"vst"}'
)
GOLDEN_REMOVE = (
    '{"alert_type":"camera_status_change","created_at":"2026-06-09T07:49:54Z",'
    '"event":{"camera_id":"8b678171-57ef-40bc-8626-66f5883aa5f6",'
    '"camera_name":"crypto-parity-test","camera_url":"","change":"camera_remove","tags":""},'
    '"source":"vst"}'
)

SID = "8b678171-57ef-40bc-8626-66f5883aa5f6"
NAME = "crypto-parity-test"


def test_camera_add_payload_matches_golden():
    p = build_payload(change=ChangeEvent.camera_add, camera_id=SID, camera_name=NAME,
                      camera_url="", tags="", created_at=CREATED, metadata=None)
    assert serialize_event(p) == GOLDEN_ADD


def test_camera_proxy_payload_matches_golden():
    p = build_payload(change=ChangeEvent.camera_proxy, camera_id=SID, camera_name=NAME,
                      camera_url="rtsp://admin:Sup3rSecret!23@192.0.2.55:554/stream1", tags="",
                      created_at=CREATED,
                      metadata={"codec": "h264", "framerate": "", "resolution": ""})
    assert serialize_event(p) == GOLDEN_PROXY


def test_camera_remove_payload_matches_golden():
    # metadata is dropped for camera_remove even if supplied.
    p = build_payload(change=ChangeEvent.camera_remove, camera_id=SID, camera_name=NAME,
                      camera_url="", tags="", created_at=CREATED,
                      metadata={"codec": "h264"})
    assert serialize_event(p) == GOLDEN_REMOVE


# --- backend dispatch (kafka / mqtt / unknown / best-effort) ------------------------
def _payload():
    return build_payload(change=ChangeEvent.camera_add, camera_id=SID, camera_name=NAME,
                         camera_url="", tags="", created_at=CREATED, metadata=None)


async def test_publish_kafka_backend(monkeypatch):
    cfg = Config(enable_notification=True, use_message_broker="kafka",
                 message_broker_topic="vst_events", message_broker_payload_key="sensor.id",
                 kafka_server_address="localhost:9092")
    pub = EventPublisher(cfg)
    sent: dict = {}

    class _FakeProducer:
        def produce(self, topic, value=None, key=None):
            sent.update(topic=topic, value=value, key=key)

        def poll(self, _t):
            sent["polled"] = True

    monkeypatch.setattr(pub, "_kafka_producer", lambda: _FakeProducer())
    await pub.publish(_payload())
    assert sent["topic"] == "vst_events"
    assert sent["key"] == b"sensor.id"
    assert sent["value"] == serialize_event(_payload()).encode("utf-8")
    assert sent.get("polled") is True


async def test_publish_mqtt_backend(monkeypatch):
    cfg = Config(enable_notification=True, use_message_broker="mqtt",
                 message_broker_topic="vst_events", message_broker_payload_key="sensor.id",
                 mqtt_broker_address="tcp://localhost:1883")
    pub = EventPublisher(cfg)
    sent: dict = {}

    class _FakeClient:
        def publish(self, topic, payload, qos=0, retain=False):
            sent.update(topic=topic, payload=payload, qos=qos, retain=retain)

    monkeypatch.setattr(pub, "_mqtt_client", lambda: _FakeClient())
    await pub.publish(_payload())
    assert sent["topic"] == "vst_events" and sent["qos"] == 1 and sent["retain"] is True
    assert sent["payload"] == serialize_event(_payload())


async def test_publish_unknown_backend_raises():
    cfg = Config(enable_notification=True, use_message_broker="rabbitmq", message_broker_topic="t")
    with pytest.raises(NotImplementedError):
        await EventPublisher(cfg).publish(_payload())


async def test_publish_is_best_effort_on_broker_error(monkeypatch):
    cfg = Config(enable_notification=True, use_message_broker="kafka", message_broker_topic="t")
    pub = EventPublisher(cfg)

    class _BadProducer:
        def produce(self, *a, **k):
            raise RuntimeError("broker down")

        def poll(self, _t):
            pass

    monkeypatch.setattr(pub, "_kafka_producer", lambda: _BadProducer())
    await pub.publish(_payload())  # must NOT raise (best-effort, C++ parity)
