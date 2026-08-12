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
BDD steps for the live overlay test (features/overlay/overlay_live.feature).

Preconditions (env, not created by this test):
  * VST deployed with notifications.enable_notification_consumer = true,
    use_message_broker_consumer = kafka, kafka_server_address = the test broker,
    message_broker_topic_consumer = the topic below.
  * A Kafka broker reachable at ``brokers`` (e.g. Redpanda on 172.17.0.1:9092).

The test publishes nv.Frame protobuf with a centered box to the broker while it
captures a live overlay snapshot, then asserts a colored box outline is drawn at
the published location (direct detection -- robust to live scene motion).
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from scripts.overlay.live_publisher import LiveBoxSpec, KafkaBoxPublisher, RedisBoxPublisher
from tests.overlay.overlay_test_utils import assert_live_box_border

logger = logging.getLogger(__name__)

scenarios("../../features/overlay/overlay_live.feature")

# ``broker`` must match VST's notifications.use_message_broker_consumer:
#   kafka -> publish to Kafka (brokers); redis -> XADD to the Redis stream.
_DEFAULTS = {
    "stream_id": "warehouse_sample",
    "broker": "redis",
    "brokers": "172.17.0.1:9092",
    "redis_host": "localhost",
    "redis_port": 6379,
    "topic": "vst-overlay-test",
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "warmup_s": 3.0,
    "min_border": 300,
}


def _make_publisher(params, spec):
    broker = params["broker"].lower()
    if broker == "kafka":
        return KafkaBoxPublisher(params["brokers"], params["topic"], spec, fps=params["fps"])
    if broker == "redis":
        return RedisBoxPublisher(params["redis_host"], params["redis_port"],
                                 params["topic"], spec, fps=params["fps"])
    raise ValueError(f"unknown broker '{broker}'")
V = "/vst/api/v1"


class _Ctx:
    pass


@pytest.fixture
def params(config):
    p = dict(_DEFAULTS)
    try:
        p.update(config["tests"]["overlay_tests"]["test_parameters"])
    except (KeyError, TypeError):
        pass
    return p


@pytest.fixture
def ctx():
    c = _Ctx()
    c.publisher = None
    c.pub_thread = None
    yield c
    if c.publisher is not None:
        c.publisher.stop()
    if c.pub_thread is not None:
        c.pub_thread.join(timeout=5)


@given("a live stream with a centered box being published to the broker")
def start_publisher(ctx, params, api_config):
    ctx.params = params
    ctx.base = api_config["base_url"]
    ctx.verify = api_config.get("verify_ssl", False)
    spec = LiveBoxSpec(sensor_id=params["stream_id"],
                       width=params["width"], height=params["height"])
    ctx.spec = spec
    try:
        ctx.publisher = _make_publisher(params, spec)
    except Exception as e:  # client lib missing or broker unreachable
        pytest.skip(f"{params['broker']} publisher unavailable: {e}")
    # Publish continuously in the background; the overlay consumer connects lazily
    # when the snapshot request activates the live overlay for this sensor.
    ctx.pub_thread = threading.Thread(target=ctx.publisher.run, args=(30.0,), daemon=True)
    ctx.pub_thread.start()
    time.sleep(params["warmup_s"])
    logger.info("publisher started for sensor=%s topic=%s", params["stream_id"], params["topic"])


@when("I capture a live overlay snapshot")
def capture_snapshot(ctx, tmp_path):
    url = f"{ctx.base}{V}/live/stream/{ctx.params['stream_id']}/picture"
    overlay = json.dumps({"bbox": {"showAll": "true"}})
    # A couple of tries: the consumer connects on the first overlay request, so the
    # first snapshot may precede metadata arrival.
    jpg = tmp_path / "live_overlay.jpg"
    for attempt in range(3):
        r = requests.get(url, params={"overlay": overlay},
                         headers={"streamid": ctx.params["stream_id"]},
                         timeout=25, verify=ctx.verify)
        assert r.status_code == 200 and r.content, f"snapshot failed: {r.status_code}"
        jpg.write_bytes(r.content)
        time.sleep(1.0)
    ctx.rgb = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(jpg), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout


@then("the snapshot shows a box outline at the published location")
def assert_box(ctx):
    lx, ty, rx, by = ctx.spec.pixel_box()
    assert_live_box_border(ctx.rgb, ctx.params["width"], ctx.params["height"],
                           (lx, ty, rx, by), min_border=ctx.params["min_border"])
