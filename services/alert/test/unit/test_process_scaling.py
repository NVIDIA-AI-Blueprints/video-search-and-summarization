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

"""Resolution and validation of alert_agent.processes."""

from unittest.mock import patch

import pytest

from utils import process_scaling
from utils.process_scaling import (
    await_source_partitions,
    resolve_process_count,
    topics_short_of_processes,
)


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
        # Rendered configs substitute environment variables textually, so a
        # templated count arrives as a string. Only the spelling is relaxed.
        assert resolve_process_count({"alert_agent": {"processes": " 6 "}}) == 6

    @pytest.mark.parametrize("value", [0, -1, True, 2.5, "many", ""])
    def test_invalid_values_fail_startup(self, value):
        with pytest.raises(ValueError):
            resolve_process_count({"alert_agent": {"processes": value}})

    def test_there_is_no_upper_bound(self):
        # A ceiling was added from the spec's "1-64" wording and then removed:
        # the partition count is what bounds an instance, and a limit with no
        # product or resource basis only refuses what partitions already would.
        assert resolve_process_count({"alert_agent": {"processes": 128}}) == 128


class TestAutoIsGone:
    """The count is no longer derived from the host.

    Deriving from the CPU count read well but hid the constraint that binds:
    parallelism is capped by the partitions, and a derived value silently
    produced processes that could never receive one.
    """

    @pytest.mark.parametrize("value", ["auto", "AUTO", " auto "])
    def test_auto_is_rejected(self, value):
        with pytest.raises(ValueError, match="positive integer"):
            resolve_process_count({"alert_agent": {"processes": value}})

    def test_the_error_says_what_bounds_the_count(self):
        with pytest.raises(ValueError, match="partition"):
            resolve_process_count({"alert_agent": {"processes": "auto"}})

    def test_resolution_does_not_consult_the_host(self, monkeypatch):
        monkeypatch.setattr(process_scaling, "available_cpus",
                            lambda: (_ for _ in ()).throw(AssertionError("consulted")))
        assert resolve_process_count({"alert_agent": {"processes": 3}}) == 3


class TestSourceTopics:
    def test_reads_non_heartbeat_kafka_topics(self):
        cfg = {"event_bridge": {"sourceType": "kafka", "kafka_source": {"topics": {
            "incident": "mdx-incidents", "alert": "mdx-alerts", "heartbeat": "hb"}}}}
        assert sorted(process_scaling.source_topics(cfg)) == ["mdx-alerts", "mdx-incidents"]

    def test_an_absent_source_type_is_kafka(self):
        # The event-bridge factory defaults to kafka, so reading the key
        # strictly here reported no topics for a supported configuration and
        # left "auto" unclamped.
        cfg = {"event_bridge": {"kafka_source": {"topics": {"incident": "mdx-incidents"}}}}
        assert process_scaling.source_topics(cfg) == ["mdx-incidents"]

    def test_the_legacy_anomaly_topic_is_read(self):
        cfg = {"event_bridge": {"sourceType": "kafka"},
               "kafka": {"anomalyTopic": "mdx-raw"}}
        assert process_scaling.source_topics(cfg) == ["mdx-raw"]

    def test_the_modern_topics_win_over_the_legacy_key(self):
        cfg = {"event_bridge": {"sourceType": "kafka", "kafka_source": {"topics": {
                   "incident": "mdx-incidents"}}},
               "kafka": {"anomalyTopic": "mdx-raw"}}
        assert process_scaling.source_topics(cfg) == ["mdx-incidents"]

    def test_a_legacy_config_is_still_readable(self, monkeypatch):
        # Reading it wrongly no longer just loses a clamp: it would block boot
        # on a supported configuration whose partitions cannot be counted.
        cfg = {"kafka": {"anomalyTopic": "mdx-raw"}}
        assert process_scaling.source_topics(cfg) == ["mdx-raw"]

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

    def test_a_single_partition_companion_topic_does_not_decide(self, monkeypatch):
        # The bug: min() returned 1 here, which would now reject a nine-process
        # configuration the partitions in fact support.
        assert self._count(monkeypatch, {"mdx-incidents": 8, "mdx-alerts": 1}) == 9

    def test_a_missing_topic_reads_as_unknown_not_as_zero(self, monkeypatch):
        # A topic absent from the metadata is usually one the deployment has
        # not created yet. Summing the rest would report a number the caller
        # treats as authoritative and reject a valid configuration.
        assert self._count(monkeypatch, {"mdx-incidents": 8}) is None


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


class TestAwaitSourcePartitions:
    """Validating the count has to survive both deployment paths.

    Compose starts this process only after the topic-init container has
    completed, so the first read is already authoritative. On Kubernetes the
    topics come from a Job with no ordering against the Deployment, so a first
    install legitimately reaches here before they exist and an immediate read
    would fail a perfectly good install.
    """

    @staticmethod
    def _counts(monkeypatch, sequence):
        """Make the metadata read return each total in turn (None = not yet)."""
        remaining = list(sequence)
        seen = []

        def fake(config, timeout=10.0):
            value = remaining.pop(0) if remaining else sequence[-1]
            seen.append(value)
            return None if value is None else {"mdx-incidents": value}

        monkeypatch.setattr(process_scaling, "source_partitions_by_topic", fake)
        monkeypatch.setattr(process_scaling, "source_topics", lambda cfg: ["mdx-incidents"])
        return seen

    def test_returns_the_count_when_it_is_already_known(self, monkeypatch):
        self._counts(monkeypatch, [16])
        assert sum(await_source_partitions({}, required=4).values()) == 16

    def test_waits_out_topics_that_do_not_exist_yet(self, monkeypatch):
        seen = self._counts(monkeypatch, [None, None, 8])
        assert sum(await_source_partitions({}, required=8, interval=0.01).values()) == 8
        assert seen == [None, None, 8]

    def test_rejects_more_processes_than_partitions(self, monkeypatch):
        self._counts(monkeypatch, [4])
        with pytest.raises(RuntimeError, match="exceeds the partitions"):
            await_source_partitions({}, required=8)

    def test_rejection_is_immediate_rather_than_waited_out(self, monkeypatch):
        # An authoritative count that is too low will not improve by waiting,
        # so a misconfiguration must not cost the full timeout to surface.
        seen = self._counts(monkeypatch, [4])
        with pytest.raises(RuntimeError):
            await_source_partitions({}, required=8, timeout=30.0, interval=10.0)
        assert len(seen) == 1

    def test_equal_counts_are_allowed(self, monkeypatch):
        self._counts(monkeypatch, [8])
        assert sum(await_source_partitions({}, required=8).values()) == 8

    def test_gives_up_when_the_topics_never_appear(self, monkeypatch):
        self._counts(monkeypatch, [None])
        with pytest.raises(RuntimeError, match="within"):
            await_source_partitions({}, required=2, timeout=0.05, interval=0.01)

    def test_the_timeout_message_names_the_topics(self, monkeypatch):
        self._counts(monkeypatch, [None])
        with pytest.raises(RuntimeError, match="mdx-incidents"):
            await_source_partitions({}, required=2, timeout=0.05, interval=0.01)


class TestPerTopicIsWhatDecides:
    """The total bounds the group; the per-topic count decides who has work.

    Each process runs one consumer per topic, all in the same group, so Kafka
    assigns each topic independently. Eight plus one partitions total nine,
    which passes any check on the total, yet only one process can hold the
    one-partition topic.
    """

    def test_a_short_topic_is_named_with_its_size(self):
        short = topics_short_of_processes({"mdx-incidents": 8, "mdx-alerts": 1}, 4)
        assert short == {"mdx-alerts": 1}

    def test_topics_that_cover_every_process_are_silent(self):
        assert topics_short_of_processes({"mdx-incidents": 8, "mdx-alerts": 8}, 4) == {}

    def test_equal_partitions_and_processes_is_enough(self):
        assert topics_short_of_processes({"mdx-incidents": 4}, 4) == {}

    def test_every_short_topic_is_reported(self):
        assert topics_short_of_processes({"a": 1, "b": 2, "c": 9}, 4) == {"a": 1, "b": 2}

    def test_eight_plus_one_partitions_do_not_serve_nine_processes(self):
        # The total is nine, which any check on the total accepts. Per topic
        # neither can serve nine: one process would hold nothing on
        # mdx-incidents and eight would hold nothing on mdx-alerts.
        assert topics_short_of_processes({"mdx-incidents": 8, "mdx-alerts": 1}, 9) == {
            "mdx-alerts": 1, "mdx-incidents": 8
        }

    def test_the_same_layout_serves_four_processes_on_one_topic_only(self):
        assert topics_short_of_processes({"mdx-incidents": 8, "mdx-alerts": 1}, 4) == {
            "mdx-alerts": 1
        }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestOneStartupBudget:
    """Metadata, seeding, warmup and the group join share one bound.

    Two independent timeouts could not be reasoned about together: an instance
    could spend the metadata budget and then the readiness budget on top.
    """

    @staticmethod
    def _timeout(value=None):
        from utils.process_scaling import startup_timeout
        config = {"alert_agent": {}} if value is None else {
            "alert_agent": {"startup_timeout_seconds": value}
        }
        return startup_timeout(config)

    def test_the_default_matches_the_specified_deadline(self):
        assert self._timeout() == process_scaling.DEFAULT_STARTUP_TIMEOUT_SECONDS == 60.0

    def test_a_deployment_can_raise_it(self):
        # A topic-creation Job racing this Deployment, or several children
        # contending on Elasticsearch, legitimately need longer.
        assert self._timeout(300) == 300.0

    def test_a_numeric_string_is_accepted(self):
        assert self._timeout("120") == 120.0

    def test_a_budget_below_the_reserved_fleet_share_is_refused(self):
        # Every step before the join is given what is left after that share.
        # At or below it they get zero, so the metadata read fails instantly
        # and blames the broker for a timeout it never had.
        floor = process_scaling.MINIMUM_STARTUP_TIMEOUT_SECONDS
        # 15.1 used to pass and still gave the metadata read zero seconds.
        for value in (0, 10, floor, floor + 0.1, floor * 2 - 0.1):
            with pytest.raises(ValueError, match="must be at least"):
                self._timeout(value)

    def test_the_smallest_workable_budget_is_accepted(self):
        floor = process_scaling.MINIMUM_STARTUP_TIMEOUT_SECONDS
        assert self._timeout(floor * 2) == floor * 2

    def test_an_unsubstituted_template_is_named(self):
        with pytest.raises(ValueError, match="must be a number"):
            self._timeout("${AB_STARTUP_TIMEOUT}")

    def test_one_process_survives_unreadable_metadata(self):
        # A broker with auto-create, or one briefly unreachable, used to
        # recover on its own at N=1. The wait buys early notice there, not a
        # partition check -- one partition already satisfies one process.
        with patch.object(process_scaling, "source_partitions_by_topic", return_value=None):
            assert await_source_partitions({}, required=1, timeout=0.05, interval=0.01) == {}

    def test_more_than_one_process_still_fails_on_unreadable_metadata(self):
        with patch.object(process_scaling, "source_partitions_by_topic", return_value=None):
            with pytest.raises(RuntimeError, match="Could not read the partition count"):
                await_source_partitions({}, required=2, timeout=0.05, interval=0.01)
