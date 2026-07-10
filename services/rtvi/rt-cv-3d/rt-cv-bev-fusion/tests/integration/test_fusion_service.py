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
Integration test for the built vss-rt-cv-mv3dt-bev-fusion image.

Drives the real container (via tests/integration/compose.fusion-test.yml):
inject N timestamp-aligned per-sensor Frames into mdx-mv3dt-raw and assert that
correctly-fused BEV Frames appear on mdx-bev — exercising the actual service
binary, broker I/O, and protobuf wire format end to end.
"""

import logging
import time
import uuid

import pytest

import schema_pb2  # from src/ (wired via tests/conftest.py)
import measurement_fusion as mf

from . import sample_scenario as ss

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

RAW_TOPIC = "mdx-raw"      # service default (warehouse deploy overrides to mdx-mv3dt-raw)
FUSED_TOPIC = "mdx-bev"

# One realistic scenario: REAL sensor ids from the 4-cam sample calibration +
# 20x20m world coordinates (sample-dataset-backed, no GPU). Kafka only — the
# broker the warehouse deployment uses.
SCENARIOS = {
    "warehouse-sample": (ss, ss.SENSORS),
}
NUM_INSTANTS = 60
NUM_OBJECTS = 2
BASE_TS = 1_700_000_000.0
FPS = 30.0
# Fraction of produced instants that must show up fused. The service consumes
# raw frames from offset=latest, so a few startup instants can be missed before
# its consumer group is assigned; the bulk must still fuse correctly.
MIN_FUSED_FRACTION = 0.6
COLLECT_TIMEOUT_S = 45


# --------------------------------------------------------------------------- #
# Kafka helpers
# --------------------------------------------------------------------------- #
# Topics are pre-created by the compose `kafka-init` one-shot so the service
# (auto.offset.reset=latest) is positioned at the end of an existing raw topic
# before we produce — otherwise early frames are skipped.
def _kafka_fused_consumer(bootstrap):
    from confluent_kafka import Consumer

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"itest-fused-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([FUSED_TOPIC])
    # Poll a few times so partitions get assigned before we rely on it.
    for _ in range(5):
        consumer.poll(0.2)
    return consumer


def _kafka_produce(bootstrap, records):
    from confluent_kafka import Producer

    producer = Producer({"bootstrap.servers": bootstrap, "linger.ms": 5})
    for rec in records:
        producer.produce(RAW_TOPIC, value=rec.frame.SerializeToString())
        producer.poll(0)
        time.sleep(0.005)  # pace ~ realtime-ish so buckets flush naturally
    producer.flush(10)


def _kafka_collect_fused(consumer, want, timeout_s):
    frames = []
    deadline = time.monotonic() + timeout_s
    idle = 0
    while time.monotonic() < deadline:
        msg = consumer.poll(1.0)
        if msg is None:
            idle += 1
            if frames and idle >= 5:  # stop once the stream goes quiet
                break
            continue
        if msg.error():
            continue
        idle = 0
        f = schema_pb2.Frame()
        f.ParseFromString(msg.value())
        frames.append(f)
        if len(frames) >= want:
            break
    consumer.close()
    return frames


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_fusion_service_fuses_raw_to_bev(fusion_stack, scenario):
    """Inject per-sensor raw frames and assert correctly-fused BEV output."""
    mod, SENSORS = SCENARIOS[scenario]
    records = list(
        mod.generate_stream(
            num_instants=NUM_INSTANTS,
            sensor_ids=SENSORS,
            base_ts=BASE_TS,
            fps=FPS,
            num_objects=NUM_OBJECTS,
        )
    )
    raw_count = len(records)  # NUM_INSTANTS * len(SENSORS)

    t0 = time.monotonic()
    consumer = _kafka_fused_consumer(fusion_stack.kafka_bootstrap)
    _kafka_produce(fusion_stack.kafka_bootstrap, records)
    fused = _kafka_collect_fused(consumer, want=NUM_INSTANTS, timeout_s=COLLECT_TIMEOUT_S)
    elapsed = time.monotonic() - t0

    # --- functional numbers (assertions + console traceability, no perf export) ---
    fused_count = len(fused)
    fusion_ratio = fused_count / NUM_INSTANTS if NUM_INSTANTS else 0.0
    throughput = fused_count / elapsed if elapsed else 0.0
    logger.info(
        "MV3DT_FUSION scenario=%s broker=kafka sensors=%s raw_frames=%d fused_frames=%d "
        "fusion_ratio=%.2f throughput_fps=%.1f elapsed_s=%.1f",
        scenario, SENSORS, raw_count, fused_count, fusion_ratio, throughput, elapsed,
    )

    # --- count / shape assertions ---
    assert fused_count >= int(NUM_INSTANTS * MIN_FUSED_FRACTION), (
        f"too few fused frames: {fused_count}/{NUM_INSTANTS}"
    )
    fused_ids = [f.id for f in fused]
    assert len(fused_ids) == len(set(fused_ids)), "duplicate fused frame ids (bucket republished)"

    # --- content assertions on every received fused frame ---
    # Map each produced instant to its timestamp bucket so we can recompute the
    # expected element-wise-mean coordinates for whatever frames arrived.
    bucket_to_instant = {
        str(mf._ts_bucket_key(BASE_TS + i / FPS)): i for i in range(NUM_INSTANTS)
    }
    checked = 0
    for f in fused:
        assert f.sensorId == "bev-sensor-1"
        instant = bucket_to_instant.get(f.id)
        if instant is None:
            continue  # a watermark/partial flush we didn't map; skip content check
        assert set(f.info.keys()) == set(SENSORS), f"info keys {set(f.info.keys())}"
        assert len(f.objects) == NUM_OBJECTS
        by_id = {o.id: o for o in f.objects}
        for o in range(NUM_OBJECTS):
            obj = by_id[f"obj-{o}"]
            assert obj.type == ("Person" if o % 2 == 0 else "Forklift")
            expected = mod.expected_fused_coords(instant, o, len(SENSORS))
            assert list(obj.bbox3d.coordinates) == pytest.approx(expected, abs=1e-2)
        checked += 1

    assert checked > 0, "no fused frame could be matched back to a produced instant"
    logger.info("Verified fused content for %d/%d frames", checked, fused_count)
