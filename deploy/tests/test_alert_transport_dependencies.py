# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Alert MS transport-dependent startup wait.

The Redis Streams and console transports are optional, so the chart only waits
on the brokers the selected transports actually use: a Redis-only deployment
must not block on an absent Kafka, and a Kafka deployment must not block on an
absent Redis.

That decision is made by matching the transport names in values.yaml, which is
only safe while the chart canonicalizes them exactly like the application's
_normalize_transport(). These tests pin both halves of that contract, because a
divergence is silent: the pod comes up talking to a broker the init container
never waited for, which is the crash-loop the wait exists to prevent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALERT_CHART = REPO_ROOT / "helm" / "services" / "alert"

ES_ENDPOINT = "elasticsearch:9200"
KAFKA_ENDPOINT = "kafka-kafka:9092"
REDIS_ENDPOINT = "redis:6379"

_ENDPOINTS_RE = re.compile(r'ENDPOINTS="([^"]*)"')


def _wait_endpoints(**values: str) -> list[str]:
    """Render the chart and return the endpoints the init container waits on."""
    cmd = [
        "helm",
        "template",
        "alert",
        str(ALERT_CHART),
        "--set",
        "enabled=true",
        "--set",
        "waitForDependencies.enabled=true",
    ]
    for key, value in values.items():
        cmd.extend(["--set", f"{key}={value}"])

    rendered = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    match = _ENDPOINTS_RE.search(rendered)
    if match is None:
        raise AssertionError("chart rendered no wait-for-dependencies endpoints")
    return match.group(1).split()


@unittest.skipIf(shutil.which("helm") is None, "helm is not installed")
class AlertTransportDependencyTests(unittest.TestCase):
    """The startup wait covers exactly the brokers the transports need."""

    def test_defaults_wait_for_kafka_and_not_redis(self):
        endpoints = _wait_endpoints()
        self.assertIn(ES_ENDPOINT, endpoints)
        self.assertIn(KAFKA_ENDPOINT, endpoints)
        self.assertNotIn(REDIS_ENDPOINT, endpoints)

    def test_redis_source_adds_redis_and_keeps_kafka_for_the_sink(self):
        endpoints = _wait_endpoints(eventSourceType="redisStream")
        self.assertIn(REDIS_ENDPOINT, endpoints)
        # The sink is still Kafka, so Kafka has to stay in the wait.
        self.assertIn(KAFKA_ENDPOINT, endpoints)

    def test_redis_only_deployment_does_not_wait_for_kafka(self):
        endpoints = _wait_endpoints(
            eventSourceType="redisStream",
            eventSinkType="redisStream",
            vlmSinkType="redisStream",
        )
        self.assertIn(REDIS_ENDPOINT, endpoints)
        self.assertNotIn(KAFKA_ENDPOINT, endpoints)

    def test_vlm_sink_alone_pulls_redis_into_the_wait(self):
        """Each transport is selected independently, including the VLM sink."""
        endpoints = _wait_endpoints(vlmSinkType="redisStream")
        self.assertIn(REDIS_ENDPOINT, endpoints)
        self.assertIn(KAFKA_ENDPOINT, endpoints)

    def test_console_transports_need_no_broker(self):
        endpoints = _wait_endpoints(
            eventSourceType="redisStream",
            eventSinkType="console",
            vlmSinkType="console",
        )
        self.assertEqual([ES_ENDPOINT, REDIS_ENDPOINT], endpoints)

    def test_transport_names_are_matched_the_way_the_application_matches_them(self):
        """Every spelling _normalize_transport() accepts has to reach the wait.

        The application lowercases the value and strips "_" and "-" before
        resolving it, and it accepts "redis" as an alias of "redisStream". A
        chart that compared the raw string would render a Kafka pod that never
        waits for Kafka on a value as ordinary as "Kafka".
        """
        for spelling in ("redisStream", "redisstream", "redis_stream", "redis-stream", "redis", "REDIS"):
            with self.subTest(transport=spelling):
                endpoints = _wait_endpoints(
                    eventSourceType=spelling,
                    eventSinkType=spelling,
                    vlmSinkType="elastic",
                )
                self.assertIn(REDIS_ENDPOINT, endpoints)
                self.assertNotIn(KAFKA_ENDPOINT, endpoints)

        for spelling in ("kafka", "Kafka", "KAFKA"):
            with self.subTest(transport=spelling):
                endpoints = _wait_endpoints(
                    eventSourceType=spelling,
                    eventSinkType=spelling,
                    vlmSinkType="elastic",
                )
                self.assertIn(KAFKA_ENDPOINT, endpoints)
                self.assertNotIn(REDIS_ENDPOINT, endpoints)


if __name__ == "__main__":
    unittest.main()
