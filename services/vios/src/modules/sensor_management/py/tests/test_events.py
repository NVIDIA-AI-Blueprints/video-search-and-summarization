# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Event-payload parity tests against golden strings captured from a live C++ deployment.

Captured 2026-06-09 from the `vst_events` Redis stream (field "sensor.id") of the stream-processing
stack, by adding then deleting an RTSP sensor (password "Sup3rSecret!23"). These golden strings are
the exact wire bytes; serialize_event() must reproduce them byte-for-byte (alphabetical keys,
compact separators).
"""
from __future__ import annotations

from sensor_ms.events.publisher import ChangeEvent, build_payload, serialize_event

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
