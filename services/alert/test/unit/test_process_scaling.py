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

"""Resolution of alert_agent.processes."""

import pytest

from utils import process_scaling
from utils.process_scaling import resolve_process_count


class TestResolveProcessCount:
    def test_absent_key_defaults_to_single_process(self):
        assert resolve_process_count({}) == 1
        assert resolve_process_count({"alert_agent": {}}) == 1
        assert resolve_process_count(None) == 1

    def test_explicit_null_defaults_to_single_process(self):
        assert resolve_process_count({"alert_agent": {"processes": None}}) == 1

    def test_integer_value(self):
        assert resolve_process_count({"alert_agent": {"processes": 4}}) == 4

    def test_numeric_string_value(self):
        assert resolve_process_count({"alert_agent": {"processes": " 6 "}}) == 6

    def test_auto_uses_available_cpus(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 12)
        assert resolve_process_count({"alert_agent": {"processes": "AUTO"}}) == 12

    def test_auto_never_returns_zero(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 0)
        assert resolve_process_count({"alert_agent": {"processes": "auto"}}) == 1

    @pytest.mark.parametrize("value", [0, -1, True, 2.5, "many", ""])
    def test_invalid_values_fail_startup(self, value):
        with pytest.raises(ValueError):
            resolve_process_count({"alert_agent": {"processes": value}})


class TestAutoClampedToPartitions:
    """Processes beyond the partition count idle, so auto must not create them."""

    def test_auto_clamps_to_partition_count(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 256)
        cfg = {"alert_agent": {"processes": "auto"}}
        assert resolve_process_count(cfg, partition_count=8) == 8

    def test_auto_keeps_cpu_count_when_partitions_are_plentiful(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 4)
        cfg = {"alert_agent": {"processes": "auto"}}
        assert resolve_process_count(cfg, partition_count=64) == 4

    @pytest.mark.parametrize("partitions", [None, 0])
    def test_auto_unclamped_when_partition_count_is_unknown(self, monkeypatch, partitions):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 6)
        cfg = {"alert_agent": {"processes": "auto"}}
        assert resolve_process_count(cfg, partition_count=partitions) == 6

    def test_explicit_count_is_an_instruction_and_is_not_clamped(self):
        cfg = {"alert_agent": {"processes": 16}}
        assert resolve_process_count(cfg, partition_count=2) == 16


class TestSourceTopics:
    def test_reads_non_heartbeat_kafka_topics(self):
        cfg = {"event_bridge": {"sourceType": "kafka", "kafka_source": {"topics": {
            "incident": "mdx-incidents", "alert": "mdx-alerts", "heartbeat": "hb"}}}}
        assert sorted(process_scaling.source_topics(cfg)) == ["mdx-alerts", "mdx-incidents"]

    def test_non_kafka_source_has_no_topics(self):
        cfg = {"event_bridge": {"sourceType": "elasticsearch", "kafka_source": {"topics": {"incident": "x"}}}}
        assert process_scaling.source_topics(cfg) == []

    def test_missing_sections_are_tolerated(self):
        assert process_scaling.source_topics({}) == []
        assert process_scaling.source_topics(None) == []


class TestPartitionCountIsSummed:
    """A low-traffic companion topic must not decide the answer."""

    @staticmethod
    def _metadata(sizes):
        from types import SimpleNamespace
        return SimpleNamespace(topics={
            name: SimpleNamespace(error=None, partitions={i: None for i in range(n)})
            for name, n in sizes.items()
        })

    def _count(self, monkeypatch, sizes):
        import types
        admin = types.ModuleType("confluent_kafka.admin")
        admin.AdminClient = lambda cfg: types.SimpleNamespace(
            list_topics=lambda timeout: self._metadata(sizes)
        )
        monkeypatch.setitem(__import__("sys").modules, "confluent_kafka.admin", admin)
        cfg = {
            "event_bridge": {"sourceType": "kafka", "kafka_source": {"topics": {
                "incident": "mdx-incidents", "alert": "mdx-alerts"}}},
            "kafka": {"bootstrap_servers": "broker:9092"},
        }
        return process_scaling.source_partition_count(cfg)

    def test_totals_the_topics(self, monkeypatch):
        assert self._count(monkeypatch, {"mdx-incidents": 8, "mdx-alerts": 1}) == 9

    def test_a_single_partition_topic_does_not_clamp_auto(self, monkeypatch):
        # The bug: min() returned 1 here, so "auto" resolved to one process on
        # any deployment carrying a one-partition companion topic.
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 16)
        partitions = self._count(monkeypatch, {"mdx-incidents": 8, "mdx-alerts": 1})
        assert resolve_process_count({"alert_agent": {"processes": "auto"}}, partitions) == 9

    def test_still_clamps_when_partitions_are_genuinely_few(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus", lambda: 256)
        partitions = self._count(monkeypatch, {"mdx-incidents": 2, "mdx-alerts": 1})
        assert resolve_process_count({"alert_agent": {"processes": "auto"}}, partitions) == 3


class TestSourcePartitionCount:
    def test_returns_none_for_non_kafka_source(self):
        assert process_scaling.source_partition_count({"event_bridge": {"sourceType": "redis_stream"}}) is None

    def test_returns_none_without_bootstrap_servers(self):
        cfg = {"event_bridge": {"sourceType": "kafka", "kafka_source": {"topics": {"incident": "t"}}}}
        assert process_scaling.source_partition_count(cfg) is None

    def test_unreachable_broker_does_not_raise(self):
        cfg = {
            "event_bridge": {"sourceType": "kafka", "kafka_source": {"topics": {"incident": "t"}}},
            "kafka": {"bootstrap_servers": "127.0.0.1:1"},
        }
        assert process_scaling.source_partition_count(cfg, timeout=0.2) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
