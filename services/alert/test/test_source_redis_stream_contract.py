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

"""SourceRedisStream read_data contract tests."""

from datetime import datetime
import sys
from unittest.mock import Mock

import pytest

pytest.importorskip("redis")

from utils.time_utils import iso_delta_seconds

# Some lightweight enhance_alert_with_vlm tests stub ``mdx`` packages during
# collection. Purge those stubs so this contract test imports the real Redis
# source module regardless of collection order.
for _name in list(sys.modules):
    if _name == "mdx" or _name.startswith("mdx."):
        del sys.modules[_name]

from mdx.anomaly.source.source_redis_stream import SourceRedisStream  # noqa: E402


class _FakeStreamMessage:
    def __init__(self, payload: str):
        self._payload = payload

    def to_json(self) -> str:
        return self._payload


def test_redis_stream_read_data_returns_normalized_batches():
    source = SourceRedisStream.__new__(SourceRedisStream)
    source.anomaly_stream = "input-stream"
    source.logger = Mock()
    source._read_from_stream = Mock(return_value=[
        _FakeStreamMessage('{"sensorId":"cam-1"}'),
        _FakeStreamMessage('{"sensorId":"cam-2"}'),
    ])

    batches = SourceRedisStream.read_data(source)

    assert len(batches) == 1
    batch = batches[0]
    assert batch["kind"] == "anomaly"
    assert batch["messages"] == ['{"sensorId":"cam-1"}', '{"sensorId":"cam-2"}']
    assert datetime.fromisoformat(batch["kafka_consumed_at"])
    assert batch["kafka_published_at"] is None
    source._read_from_stream.assert_called_once_with("input-stream")


def test_redis_stream_read_data_returns_empty_list_when_no_messages():
    source = SourceRedisStream.__new__(SourceRedisStream)
    source.anomaly_stream = "input-stream"
    source.logger = Mock()
    source._read_from_stream = Mock(return_value=[])

    assert SourceRedisStream.read_data(source) == []


def test_redis_stream_read_data_returns_empty_list_on_source_error():
    source = SourceRedisStream.__new__(SourceRedisStream)
    source.anomaly_stream = "input-stream"
    source.logger = Mock()
    source._read_from_stream = Mock(side_effect=RuntimeError("redis unavailable"))

    assert SourceRedisStream.read_data(source) == []
    source.logger.error.assert_called_once()


def test_kafka_published_at_none_is_safe_for_downstream_latency_calculation():
    """kafka_published_at is intentionally None for Redis Stream batches
    (there is no Kafka producer timestamp). The downstream consumer,
    iso_delta_seconds, accepts None inputs and returns None — so latency
    histograms that depend on kafka_published_at are simply skipped, not
    crashed, when data arrives via Redis Stream.
    """
    source = SourceRedisStream.__new__(SourceRedisStream)
    source.anomaly_stream = "input-stream"
    source.logger = Mock()
    source._read_from_stream = Mock(return_value=[_FakeStreamMessage('{"sensorId":"cam-1"}')])

    batch = SourceRedisStream.read_data(source)[0]
    assert batch["kafka_published_at"] is None

    # Simulate what metrics/recorder.py does: compute lag between
    # kafka_published_at and kafka_consumed_at.  Must return None, not raise.
    result = iso_delta_seconds(batch["kafka_published_at"], batch["kafka_consumed_at"])
    assert result is None
