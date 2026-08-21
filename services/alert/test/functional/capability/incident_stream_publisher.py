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
Sustained-rate incident stream publisher for capability/load tests.

Produces Incident protobuf messages to Kafka at a paced aggregate rate across
N sensors, in one of two modes:

- ``--unique`` (default off): every message is a fresh cohort (unique id and
  timestamps) so it passes dedup — survivor rate equals the injection rate.
  Use for concurrency/knee tests where the load must be exact.
- BA-style (default): one live incident per sensor with a FIXED cohort where
  only the ``end`` timestamp advances per publish; ``info.isComplete`` flips
  true on the final message of each cycle, then the incident recycles. This
  mirrors the behavior-analytics write pattern and is collapsed by dedup.

Start the stream only AFTER Alert Bridge is up: the consumer uses
``auto_offset_reset=latest`` and skips anything produced before it joins.
"""

import argparse
import copy
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
for _p in (SRC_ROOT, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from confluent_kafka import Producer
from google.protobuf import json_format
from mdx.protobuf import Incident as NvIncident

DEFAULT_PAYLOAD = os.path.join(REPO_ROOT, "test", "protobuf", "test_data", "sample_incident.json")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _build_proto(data: dict) -> bytes:
    if 'incidentType' in data and 'category' not in data:
        data['category'] = data.pop('incidentType')
    msg = NvIncident()
    json_format.ParseDict(data, msg, ignore_unknown_fields=True)
    return msg.SerializeToString()


class SensorState:
    """BA-style live incident: fixed cohort, advancing end, recycle."""

    def __init__(self, template: dict, sensor_id: str, recycle_seconds: float):
        self.template = template
        self.sensor_id = sensor_id
        self.recycle_seconds = recycle_seconds
        self._new_cohort()

    def _new_cohort(self):
        self.cohort_start = datetime.now(timezone.utc)
        self.cohort_id = f"{self.sensor_id}-{uuid.uuid4().hex[:12]}"

    def next_message(self) -> dict:
        now = datetime.now(timezone.utc)
        complete = (now - self.cohort_start).total_seconds() >= self.recycle_seconds
        data = copy.deepcopy(self.template)
        data["id"] = self.cohort_id
        data["sensorId"] = self.sensor_id
        data["timestamp"] = _iso(self.cohort_start)
        data["end"] = _iso(now)
        # Incident.info is a string map on the protobuf schema
        data.setdefault("info", {})
        data["info"]["isComplete"] = "true" if complete else "false"
        if complete:
            self._new_cohort()
        return data


def _unique_message(template: dict, sensor_id: str, seq: int) -> dict:
    now = datetime.now(timezone.utc)
    data = copy.deepcopy(template)
    data["id"] = f"{sensor_id}-{seq}-{uuid.uuid4().hex[:8]}"
    data["sensorId"] = sensor_id
    data["timestamp"] = _iso(now - timedelta(seconds=10))
    data["end"] = _iso(now)
    data.setdefault("info", {})
    data["info"]["isComplete"] = "true"
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bootstrap', default='127.0.0.1:9092')
    parser.add_argument('--topic', default='mdx-incidents')
    parser.add_argument('--payload', default=DEFAULT_PAYLOAD, help='Incident JSON template')
    parser.add_argument('--num-sensors', type=int, default=4)
    parser.add_argument('--rate', type=float, help='Aggregate messages/sec')
    parser.add_argument('--duration', type=float, help='Seconds to run')
    parser.add_argument('--sensor-prefix', default='CAP_SENSOR')
    parser.add_argument('--unique', action='store_true',
                        help='Fresh cohort per message (survivors == rate)')
    parser.add_argument('--recycle-seconds', type=float, default=30.0,
                        help='BA-style mode: incident lifetime before isComplete+recycle')
    parser.add_argument('--identical-burst', type=int, default=0,
                        help='Fire N byte-identical messages (same fingerprint) '
                             'back-to-back and exit — dedup atomicity tests')
    args = parser.parse_args()

    with open(args.payload, 'r', encoding='utf-8') as f:
        template = json.load(f)

    if args.identical_burst > 0:
        sensor = f"{args.sensor_prefix}_001"
        data = _unique_message(template, sensor, 0)
        blob = _build_proto(data)
        producer = Producer({'bootstrap.servers': args.bootstrap, 'linger.ms': 0})
        for _ in range(args.identical_burst):
            producer.produce(args.topic, blob, key=sensor.encode())
        producer.flush(10)
        print(f"DONE identical_burst={args.identical_burst} sensor={sensor} id={data['id']}", flush=True)
        return 0

    if args.rate is None or args.duration is None:
        parser.error("--rate and --duration are required unless --identical-burst is used")

    sensors = [f"{args.sensor_prefix}_{i:03d}" for i in range(1, args.num_sensors + 1)]
    states = {
        s: SensorState(template, s, args.recycle_seconds) for s in sensors
    }

    producer = Producer({'bootstrap.servers': args.bootstrap, 'linger.ms': 5})
    interval = 1.0 / args.rate
    deadline = time.monotonic() + args.duration
    next_send = time.monotonic()
    sent = 0
    started_at = time.monotonic()

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_send:
                time.sleep(min(next_send - now, 0.05))
                continue
            sensor = sensors[sent % len(sensors)]
            if args.unique:
                data = _unique_message(template, sensor, sent)
            else:
                data = states[sensor].next_message()
            producer.produce(args.topic, _build_proto(data), key=sensor.encode())
            sent += 1
            next_send += interval
            if sent % 50 == 0:
                producer.poll(0)
                elapsed = time.monotonic() - started_at
                print(f"sent={sent} elapsed={elapsed:.1f}s achieved_rate={sent/elapsed:.2f}/s", flush=True)
    finally:
        producer.flush(10)

    elapsed = time.monotonic() - started_at
    print(f"DONE sent={sent} elapsed={elapsed:.1f}s achieved_rate={sent/elapsed:.2f}/s", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
