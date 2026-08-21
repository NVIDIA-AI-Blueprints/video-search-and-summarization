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
"""Dump mdx-raw (nv.Frame protobuf) frame timestamps + sensorId from Kafka.

Purpose: check whether per-frame timestamps are synced across cameras / perception
containers. Each perception container publishes nv.Frame messages for its cameras to
the mdx-raw topic; this prints (frame timestamp, sensorId, frame id) per message so you
can compare the same wall-clock instant / frame number across sensors and containers.

Uses confluent-kafka (kafka-python can't read the Kafka 4.0 broker) and parses each
nv.Frame IN-PROCESS via schema_pb2 — no per-message `protoc` subprocess. The old version
forked protoc once per message, which at ~840 msg/s (e.g. 28 cams x 2 instances) couldn't
keep up: a broker backlog built up and the tool kept printing long after teardown.

Driven by tools/kafka-dump.sh (sets up the venv + PYTHONPATH=tools so schema_pb2 imports).
"""
import argparse
import datetime
import sys

from confluent_kafka import Consumer
from schema_pb2 import Frame   # in-process parse; PYTHONPATH=tools set by kafka-dump.sh


def iso(sec, nanos):
    return (datetime.datetime.fromtimestamp(sec, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            + f".{nanos:09d}Z")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--topic", default="mdx-raw")
    ap.add_argument("--schema", default=None,
                    help="(deprecated/ignored — nv.Frame is now parsed in-process via schema_pb2)")
    ap.add_argument("--count", type=int, default=0, help="stop after N messages (0 = until Ctrl-C)")
    ap.add_argument("--from-beginning", action="store_true", help="read from the start of the topic")
    ap.add_argument("--raw", action="store_true", help="also print the full decoded nv.Frame text")
    a = ap.parse_args()

    c = Consumer({
        "bootstrap.servers": a.bootstrap,
        "group.id": "kafka-dump",
        "auto.offset.reset": "earliest" if a.from_beginning else "latest",
        "enable.auto.commit": False,
    })
    c.subscribe([a.topic])

    out = sys.stdout
    print(f"{'frame_timestamp (UTC)':<32} {'sensorId':<26} {'frame_id':>12} {'epoch.ns':>20}  offset",
          flush=True)
    n = 0
    try:
        while a.count == 0 or n < a.count:
            # Batch-consume (not poll-one-at-a-time) and flush once per batch rather than per
            # line — the protoc fork + per-message stdout flush were the throughput limiters.
            msgs = c.consume(num_messages=1000, timeout=1.0)
            if not msgs:
                continue
            for m in msgs:
                if m is None or m.error():
                    continue
                frame = Frame()
                try:
                    frame.ParseFromString(m.value())
                except Exception:
                    continue
                sec = frame.timestamp.seconds
                nanos = frame.timestamp.nanos
                sid = frame.sensorId or "?"
                fid = frame.id or "?"            # nv.Frame.id is the top-level frame number (string)
                if sec:
                    out.write(f"{iso(sec, nanos):<32} {sid:<26} {fid:>12} "
                              f"{sec}.{nanos:09d}  {m.offset()}\n")
                else:
                    out.write(f"{'<no timestamp>':<32} {sid:<26} {fid:>12} {'':>20}  {m.offset()}\n")
                if a.raw:
                    out.write(str(frame) + "\n")
                n += 1
                if a.count and n >= a.count:
                    break
            out.flush()
    except KeyboardInterrupt:
        pass
    finally:
        out.flush()
        c.close()
    print(f"\n[{n} messages dumped]", file=sys.stderr)


if __name__ == "__main__":
    main()
