#!/usr/bin/env python3
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

"""
Reliable behavior (alert) producer for local Kafka end-to-end testing.

Usage examples:
  python test/protobuf/produce_alert.py
  python test/protobuf/produce_alert.py --bootstrap 127.0.0.1:9092 --topic mdx-alerts \
      --payload test/protobuf/test_data/sample_alert.json --id-suffix "-run1"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from confluent_kafka import Producer
from google.protobuf import json_format

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mdx.anomaly.protobuf import Behavior as NvBehavior  # noqa: E402


DEFAULT_PAYLOAD = os.path.join(
    REPO_ROOT,
    "test",
    "protobuf",
    "test_data",
    "sample_alert.json",
)


def _load_json_payload(path: str) -> Dict[str, Any]:
    if not os.path.isabs(path):
        path = os.path.join(REPO_ROOT, path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _ensure_timestamp(value: str | None = None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise_geolocation(geo: Dict[str, Any]) -> None:
    coords: Iterable[Any] | None = geo.get("coordinates")
    if not coords:
        return
    sample = next(iter(coords), None)
    if isinstance(sample, dict) and "point" in sample:
        return  # already normalised
    geo["coordinates"] = [{"point": list(coord)} for coord in coords]


def _stringify_map_fields(mapping: Dict[str, Any]) -> Dict[str, str]:
    normalised: Dict[str, str] = {}
    for key, value in mapping.items():
        if isinstance(value, str):
            normalised[key] = value
        else:
            normalised[key] = json.dumps(value)
    return normalised


def _prepare_behavior_payload(data: Dict[str, Any], *, sensor_id: str | None = None) -> Dict[str, Any]:
    payload = deepcopy(data)

    if not payload.get("id"):
        payload["id"] = f"alert-{uuid.uuid4()}"

    payload["timestamp"] = _ensure_timestamp(payload.get("timestamp"))
    if not payload.get("end"):
        payload["end"] = payload["timestamp"]

    existing_sensor = payload.get("sensor") or {}
    derived_sensor_id = sensor_id or existing_sensor.get("id") or payload.get("sensorId")
    resolved_sensor_id = derived_sensor_id or "sensor-alert-demo"
    payload["sensor"] = {
        "id": resolved_sensor_id,
        "type": existing_sensor.get("type", "traffic_camera"),
        "description": existing_sensor.get("description", "Demo alert sensor"),
    }

    analytics = payload.get("analyticsModule") or {}
    analytics.setdefault("id", "Alert Module")
    if analytics.get("info"):
        analytics["info"] = _stringify_map_fields(analytics["info"])
    payload["analyticsModule"] = analytics

    info_map = payload.get("info", {})
    if info_map:
        payload["info"] = _stringify_map_fields(info_map)

    payload.setdefault("videoPath", "alert_demo.mp4")

    if "smoothLocations" in payload and isinstance(payload["smoothLocations"], dict):
        _normalise_geolocation(payload["smoothLocations"])

    if "locations" in payload and isinstance(payload["locations"], dict):
        _normalise_geolocation(payload["locations"])

    event = payload.get("event") or {}
    event.setdefault("id", payload["id"])
    event.setdefault("type", "alert_event")
    payload["event"] = event

    return payload


def _build_behavior_proto(data: Dict[str, Any]) -> NvBehavior:
    msg = NvBehavior()
    json_format.ParseDict(data, msg, ignore_unknown_fields=True)
    return msg


def _produce(bootstrap: str, topic: str, message: NvBehavior, key: str) -> None:
    producer = Producer({"bootstrap.servers": bootstrap})
    producer.produce(topic, message.SerializeToString(), key=key.encode("utf-8"))
    producer.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce a Behavior protobuf to Kafka for E2E testing")
    parser.add_argument("--bootstrap", default="127.0.0.1:9092", help="Kafka bootstrap servers")
    parser.add_argument("--topic", default="mdx-alerts", help="Kafka topic for Behavior messages")
    parser.add_argument("--payload", default=DEFAULT_PAYLOAD, help="Path to Behavior JSON payload")
    parser.add_argument("--id-suffix", default="", help="Optional suffix appended to alert id")
    parser.add_argument("--sensor-id", default=None, help="Override sensor id in payload")

    args = parser.parse_args()

    try:
        raw_data = _load_json_payload(args.payload)
    except FileNotFoundError as exc:
        parser.error(f"Payload file not found: {exc}")

    payload = _prepare_behavior_payload(raw_data, sensor_id=args.sensor_id)

    if args.id_suffix:
        payload["id"] = f"{payload['id']}{args.id_suffix}"
        payload.setdefault("event", {})["id"] = payload["id"]

    try:
        behavior_message = _build_behavior_proto(payload)
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        parser.error(f"Failed to build Behavior protobuf: {exc}")

    key = payload.get("id") or payload["sensor"]["id"]

    try:
        _produce(args.bootstrap, args.topic, behavior_message, key)
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        parser.error(f"Failed to publish alert to Kafka: {exc}")

    print(f"Produced alert to {args.topic} with key={key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


