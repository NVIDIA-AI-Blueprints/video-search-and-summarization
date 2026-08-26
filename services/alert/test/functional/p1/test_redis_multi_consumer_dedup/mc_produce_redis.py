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

"""Incident producer for the Redis multi-consumer dedup functional test.

The Redis analogue of ``test_multi_consumer_dedup/mc_produce.py``: same
``--sensor-id`` / ``--timestamp`` cohort controls, so the caller can mint
duplicates (identical dedup fingerprint) or distinct incidents on demand.

There is deliberately no ``--partition`` equivalent, and that absence is the
whole point of the test. Kafka lets the producer place a record on a chosen
partition, and therefore on a chosen consumer; a Redis Streams producer has no
such control. ``XADD`` appends to one log and ``XREADGROUP`` hands the entry to
whichever consumer in the group asks first, so the placement the Kafka test
relies on cannot be expressed here.

Entries use the MDX envelope (``key`` / ``value`` / ``headers``) with the key set
to ``sensorId``, matching what behavior-analytics publishes. Note that the key is
carried for parity and dedup-alignment reporting only: unlike a Kafka record key
it does not influence which consumer receives the entry.
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

import redis  # noqa: E402
from google.protobuf import json_format  # noqa: E402
from mdx.protobuf import Incident as NvIncident  # noqa: E402
from mdx.redis_stream_broker import HEADERS_FIELD, KEY_FIELD, PAYLOAD_FIELD  # noqa: E402

BASE_PAYLOAD = os.path.join(REPO_ROOT, "test", "protobuf", "test_data", "sample_incident.json")


def build_incident_proto(data: Dict[str, Any]) -> NvIncident:
    if "incidentType" in data and "category" not in data:
        data["category"] = data.pop("incidentType")
    msg = NvIncident()
    json_format.ParseDict(data, msg, ignore_unknown_fields=True)
    return msg


def main() -> int:
    parser = argparse.ArgumentParser(description="XADD an Incident with cohort control")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--db", type=int, default=0)
    parser.add_argument("--password", default=None)
    parser.add_argument("--stream", default="mdx-incidents-mc")
    parser.add_argument("--payload", default=BASE_PAYLOAD)
    parser.add_argument("--sensor-id", required=True, help="sensorId to stamp (also the envelope key)")
    parser.add_argument("--timestamp", default="", help="ISO8601 timestamp; default = now (UTC)")
    parser.add_argument("--maxlen", type=int, default=1000)
    args = parser.parse_args()

    with open(args.payload, "r", encoding="utf-8") as f:
        data = json.load(f)

    ts = args.timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    data["sensorId"] = args.sensor_id
    data["timestamp"] = ts
    data["end"] = ts

    msg = build_incident_proto(data)

    client = redis.Redis(
        host=args.host,
        port=args.port,
        db=args.db,
        password=args.password or None,
        decode_responses=False,
    )
    try:
        entry_id = client.xadd(
            args.stream,
            {
                KEY_FIELD: str(args.sensor_id).encode("utf-8"),
                PAYLOAD_FIELD: msg.SerializeToString(),
                HEADERS_FIELD: json.dumps({}),
            },
            maxlen=args.maxlen,
            approximate=True,
        )
    except redis.exceptions.RedisError as exc:
        print(f"ERROR: XADD failed (stream={args.stream} sensorId={args.sensor_id}): {exc}",
              file=sys.stderr)
        return 1
    finally:
        client.close()

    print(f"Produced incident stream={args.stream} sensorId={args.sensor_id} ts={ts} id={entry_id!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
