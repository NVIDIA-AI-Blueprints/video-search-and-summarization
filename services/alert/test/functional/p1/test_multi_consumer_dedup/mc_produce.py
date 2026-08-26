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

"""Incident producer for the multi-consumer dedup functional test.

Extends the plain ``produce_incident.py`` with the two controls this test
needs:

* ``--sensor-id`` / ``--timestamp`` — mint incidents with a chosen cohort
  identity so we can build both duplicates (identical dedup fingerprint) and
  distinct incidents on demand.
* ``--partition`` — force a record onto a specific Kafka partition, so the
  negative scenario can place the SAME fingerprint on two partitions (i.e.
  two consumers) and prove in-process dedup does not span containers.

Records are keyed by ``sensorId`` — the partition-key contract the in-process
dedup design relies on (see ``mdx/source/source_utils.py::classify_key_alignment``).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

# services/alert is five levels up from this file; add src/ (post src-layout) + root.
REPO_ROOT = os.path.abspath(__file__)
for _ in range(5):
    REPO_ROOT = os.path.dirname(REPO_ROOT)
SRC_ROOT = os.path.join(REPO_ROOT, "src")
for _p in (SRC_ROOT, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from confluent_kafka import Producer  # noqa: E402
from google.protobuf import json_format  # noqa: E402
from mdx.protobuf import Incident as NvIncident  # noqa: E402

BASE_PAYLOAD = os.path.join(REPO_ROOT, "test", "protobuf", "test_data", "sample_incident.json")


def build_incident_proto(data: Dict[str, Any]) -> NvIncident:
    if "incidentType" in data and "category" not in data:
        data["category"] = data.pop("incidentType")
    msg = NvIncident()
    json_format.ParseDict(data, msg, ignore_unknown_fields=True)
    return msg


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce an Incident with cohort/partition control")
    parser.add_argument("--bootstrap", default="127.0.0.1:9092")
    parser.add_argument("--topic", default="mdx-incidents-mc")
    parser.add_argument("--payload", default=BASE_PAYLOAD)
    parser.add_argument("--sensor-id", required=True, help="sensorId to stamp (also the Kafka key)")
    parser.add_argument("--timestamp", default="", help="ISO8601 timestamp; default = now (UTC)")
    parser.add_argument("--partition", type=int, default=None,
                        help="Force this partition (default: let the partitioner hash the key)")
    args = parser.parse_args()

    with open(args.payload, "r", encoding="utf-8") as f:
        data = json.load(f)

    ts = args.timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    data["sensorId"] = args.sensor_id
    data["timestamp"] = ts
    data["end"] = ts

    msg = build_incident_proto(data)

    # Partition-key contract: key by sensorId so a sensor's cohort lands on one
    # partition -> one consumer -> one in-process dedup cache.
    key = str(args.sensor_id).encode("utf-8")

    # Capture async delivery outcomes so a topic/partition delivery failure
    # surfaces here as a non-zero exit, not later as an unexplained ES timeout
    # in the functional test.
    delivery_errors: list = []

    def _on_delivery(err: Any, _msg: Any) -> None:
        if err is not None:
            delivery_errors.append(str(err))

    p = Producer({"bootstrap.servers": args.bootstrap})
    produce_kwargs: Dict[str, Any] = {"key": key, "on_delivery": _on_delivery}
    if args.partition is not None:
        produce_kwargs["partition"] = args.partition
    p.produce(args.topic, msg.SerializeToString(), **produce_kwargs)
    remaining = p.flush(10)
    if remaining > 0:
        print(f"ERROR: {remaining} message(s) not delivered within flush timeout "
              f"(topic={args.topic} sensorId={args.sensor_id})", file=sys.stderr)
        return 1
    if delivery_errors:
        print(f"ERROR: Kafka delivery failed (topic={args.topic} "
              f"sensorId={args.sensor_id}): {'; '.join(delivery_errors)}", file=sys.stderr)
        return 1

    where = f"partition={args.partition}" if args.partition is not None else "partition=auto"
    print(f"Produced incident topic={args.topic} sensorId={args.sensor_id} ts={ts} {where} key={args.sensor_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
