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

"""Redis Streams counterpart of ``produce_incident.py``.

Publishes an Incident using the MDX stream envelope that vss-behavior-analytics
writes, so Alert MS reads a real upstream payload rather than a test-only shape::

    XADD <stream> * key <sensorId> value <protobuf> headers <json>

Usage:
  python test/protobuf/produce_incident_redis_stream.py \
      --host 127.0.0.1 --port 6379 --stream mdx-incidents \
      --payload /tmp/incident.json --id-suffix "-run1"
"""

import argparse
import json
import os
import sys
from typing import Any, Dict

# Packages (mdx, ...) live under src/ after the src/ layout restructure.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
for _p in (SRC_ROOT, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import redis  # noqa: E402
from google.protobuf import json_format  # noqa: E402

from mdx.protobuf import Incident as NvIncident  # noqa: E402
from mdx.redis_stream_broker import HEADERS_FIELD, KEY_FIELD, PAYLOAD_FIELD  # noqa: E402


def load_json_payload(path: str) -> Dict[str, Any]:
    if not os.path.isabs(path):
        path = os.path.join(REPO_ROOT, path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_incident_proto(data: Dict[str, Any]) -> NvIncident:
    if "incidentType" in data and "category" not in data:
        data["category"] = data.pop("incidentType")

    msg = NvIncident()
    json_format.ParseDict(data, msg, ignore_unknown_fields=True)
    return msg


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish an Incident protobuf to a Redis Stream for E2E testing"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--db", type=int, default=0)
    parser.add_argument("--password", default=None)
    parser.add_argument("--stream", default="mdx-incidents")
    parser.add_argument(
        "--payload", default="test/protobuf/test_data/sample_incident.json"
    )
    parser.add_argument("--id-suffix", default="")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Publish the payload as JSON text instead of protobuf. The source "
             "accepts both encodings; protobuf is what behavior-analytics uses.",
    )

    args = parser.parse_args()

    try:
        data = load_json_payload(args.payload)
        if args.id_suffix and data.get("id"):
            data["id"] = f"{data['id']}{args.id_suffix}"

        if args.json:
            payload = json.dumps(data).encode("utf-8")
        else:
            payload = build_incident_proto(data).SerializeToString()

        client = redis.Redis(
            host=args.host,
            port=args.port,
            db=args.db,
            password=args.password or None,
            decode_responses=False,
        )
        entry_id = client.xadd(
            args.stream,
            {
                KEY_FIELD: str(data.get("sensorId", "")).encode("utf-8"),
                PAYLOAD_FIELD: payload,
                HEADERS_FIELD: json.dumps({}),
            },
        )
        encoding = "json" if args.json else "protobuf"
        print(
            f"Published {encoding} incident to stream '{args.stream}' "
            f"as {entry_id.decode('utf-8') if isinstance(entry_id, bytes) else entry_id} "
            f"(sensorId={data.get('sensorId')})"
        )
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
