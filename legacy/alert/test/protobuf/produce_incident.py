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
Reliable Incident producer for local E2E testing.

Defaults:
  - bootstrap: 127.0.0.1:9092 (Redpanda/Kafka)
  - topic: mdx-incidents
  - payload: test/protobuf/test_data/sample_incident.json

Usage examples:
  python test/protobuf/produce_incident.py
  python test/protobuf/produce_incident.py --bootstrap 127.0.0.1:9092 --topic mdx-incidents \
      --payload test/protobuf/test_data/sample_incident_minimal.json --id-suffix "-run1"
"""

import argparse
import json
import os
import sys
from typing import Dict, Any

# Ensure project root is on sys.path for 'mdx' imports when invoked as a script
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from confluent_kafka import Producer
from google.protobuf import json_format
from mdx.anomaly.protobuf import Incident as NvIncident


def load_json_payload(path: str) -> Dict[str, Any]:
    if not os.path.isabs(path):
        # Resolve relative to repo root
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo_root, path)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_incident_proto(data: Dict[str, Any]) -> NvIncident:
    # Map non-schema fields if present
    if 'incidentType' in data and 'category' not in data:
        data['category'] = data.pop('incidentType')

    msg = NvIncident()
    # Be tolerant of extra fields in test payloads
    json_format.ParseDict(data, msg, ignore_unknown_fields=True)
    return msg


def produce(bootstrap: str, topic: str, msg: NvIncident, key: bytes) -> None:
    p = Producer({'bootstrap.servers': bootstrap})
    p.produce(topic, msg.SerializeToString(), key=key)
    p.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description='Produce an Incident protobuf to Kafka for E2E testing')
    parser.add_argument('--bootstrap', default='127.0.0.1:9092', help='Kafka bootstrap servers (default: 127.0.0.1:9092)')
    parser.add_argument('--topic', default='mdx-incidents', help='Kafka topic for Incident messages')
    parser.add_argument('--payload', default='test/protobuf/test_data/sample_incident.json', help='Path to Incident JSON payload')
    parser.add_argument('--id-suffix', default='', help='Optional suffix to append to incident id')

    args = parser.parse_args()

    try:
        data = load_json_payload(args.payload)

        # Ensure id present and optionally suffix it for dedup testing
        if args.id_suffix:
            if 'id' in data and data['id']:
                data['id'] = f"{data['id']}{args.id_suffix}"

        msg = build_incident_proto(data)

        # Key: prefer id; fallback to sensorId-timestamp
        raw_key = data.get('id') or f"{data.get('sensorId','')}-{data.get('timestamp','')}"
        key = str(raw_key).encode('utf-8')

        produce(args.bootstrap, args.topic, msg, key)
        print(f"Produced incident to {args.topic} with key={raw_key}")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())


