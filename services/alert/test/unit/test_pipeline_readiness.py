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

"""Readiness has to cover the sink, not only the source.

A pipeline whose terminal sink cannot be written to still consumes, still
verifies, and then discards the verdict. Nothing about that is visible in
throughput -- a sink dropping everything moves no counter a dashboard watches --
so if readiness only asks the source, the deployment reports healthy for as long
as it runs.

The republish timer is here for the same reason. Kafka refreshes readiness from
its rebalance callbacks, but Redis has no rebalance: without a timer the state
published at startup is the only one ever published, and a broker that goes away
afterwards is never reported.

Which is why the timer is opt-in per transport rather than on for everyone.
Republishing closes the rebalance drain budget, and that budget is meant to be
spent once per rebalance -- so a timer on the Kafka path would quietly restore
the per-consumer allowance it was written to remove.
"""

import time
from unittest.mock import MagicMock, patch

import enhance_alert_with_vlm as entry


def make_enhancer(source_ready=True, sink=None):
    enhancer = MagicMock()
    enhancer.source.is_ready.return_value = source_ready
    enhancer.vlm_enhanced_event_sink = sink
    return enhancer


def make_sink(healthy=True, transport="redis_stream"):
    sink = MagicMock()
    sink.is_healthy.return_value = healthy
    sink.transport_label = transport
    return sink


class TestTerminalSinkReadiness:
    def test_a_writable_sink_is_ready(self):
        assert entry._terminal_sink_ready(make_enhancer(sink=make_sink())) is True

    def test_a_sink_that_cannot_publish_is_not_ready(self):
        assert entry._terminal_sink_ready(make_enhancer(sink=make_sink(healthy=False))) is False

    def test_no_sink_configured_is_not_held_against_readiness(self):
        assert entry._terminal_sink_ready(make_enhancer(sink=None)) is True

    def test_a_sink_whose_health_check_raises_is_assumed_healthy(self):
        """A sink that cannot answer must not be able to fail readiness by
        omission: that would make a health check a liveness hazard."""
        sink = make_sink()
        sink.is_healthy.side_effect = RuntimeError("boom")
        assert entry._terminal_sink_ready(make_enhancer(sink=sink)) is True

    def test_sink_health_is_exported_under_its_transport(self):
        with patch("metrics.recorder.set_sink_ready") as gauge:
            entry._terminal_sink_ready(make_enhancer(sink=make_sink(healthy=False)))
        gauge.assert_called_once_with("redis_stream", False)

    def test_an_elastic_sink_without_a_label_still_reports(self):
        sink = MagicMock(spec=["is_healthy"])
        sink.is_healthy.return_value = True
        with patch("metrics.recorder.set_sink_ready") as gauge:
            entry._terminal_sink_ready(make_enhancer(sink=sink))
        gauge.assert_called_once_with("elastic", True)


class TestPipelineReadinessNeedsBothHalves:
    """Either half failing leaves the process running and serving nothing."""

    def test_a_readable_source_and_writable_sink_is_ready(self):
        assert entry._pipeline_ready(make_enhancer(True, make_sink())) is True

    def test_an_unreadable_source_is_not_ready(self):
        assert entry._pipeline_ready(make_enhancer(False, make_sink())) is False

    def test_an_unwritable_sink_is_not_ready_even_while_consuming_fine(self):
        """The case the review flagged: the source is happy, the verdicts are
        going nowhere, and nothing else in the pipeline would report it."""
        assert entry._pipeline_ready(make_enhancer(True, make_sink(healthy=False))) is False

    def test_neither_half_working_is_not_ready(self):
        assert entry._pipeline_ready(make_enhancer(False, make_sink(healthy=False))) is False

    def test_the_published_state_reflects_an_unwritable_sink(self):
        published = []
        enhancer = make_enhancer(True, make_sink(healthy=False))
        enhancer._publishes_own_fleet_state = True
        enhancer.source.assigned_partition_count.return_value = 4
        with patch("metrics.recorder.set_pipeline_process_counts",
                   side_effect=lambda *a: published.append(a)), \
             patch("metrics.recorder.set_assigned_partitions"):
            entry.AnomalyEnhancer._publish_assignment_state(enhancer)
        assert published == [(1, 1, 0)]


class TestWhoNeedsAReadinessTimer:
    """The timer is not free, so only a transport that asks for it gets one.

    Republishing goes through ``_publish_assignment_state``, which closes the
    rebalance drain budget. That budget is spent once per rebalance by design
    and not reopened on expiry, so a timer running it between revokes of the
    same rebalance would hand each consumer a fresh allowance -- exactly the
    per-consumer bound it was written to replace. Kafka reports its own state
    changes and must therefore stay off the timer entirely.
    """

    @staticmethod
    def _pipeline(source, sink=None):
        enhancer = MagicMock()
        enhancer.source = source
        enhancer.vlm_enhanced_event_sink = sink
        return entry.AnomalyEnhancer._readiness_needs_polling(enhancer)

    def test_a_source_that_reports_its_own_changes_gets_no_timer(self):
        source = MagicMock(spec=["is_ready"])
        assert self._pipeline(source) is False

    def test_a_source_that_asks_for_one_gets_one(self):
        source = MagicMock()
        source.needs_readiness_polling = True
        assert self._pipeline(source) is True

    def test_a_sink_can_ask_even_when_the_source_does_not(self):
        """Redis sink behind a Kafka source: the publish side is the half whose
        health changes with nothing to announce it."""
        source = MagicMock(spec=["is_ready"])
        sink = MagicMock()
        sink.needs_readiness_polling = True
        assert self._pipeline(source, sink) is True

    def test_no_sink_configured_asks_for_nothing(self):
        assert self._pipeline(MagicMock(spec=["is_ready"]), None) is False

    def test_the_kafka_source_does_not_ask_for_a_timer(self):
        """Guard on the pre-existing transport: a default that flipped here
        would put every Kafka deployment's drain budget on a 5 second reset."""
        from mdx.source.source_kafka import SourceKafka

        assert getattr(SourceKafka, "needs_readiness_polling", False) is False

    def test_the_redis_source_does_ask_for_a_timer(self):
        from mdx.source.source_redis_stream import SourceRedisStream

        assert SourceRedisStream.needs_readiness_polling is True

    def test_the_default_sink_does_not_ask_for_a_timer(self):
        from mdx.sink.vlm_enhanced_sink.sink_base import VLMEnhancedSink

        assert VLMEnhancedSink.needs_readiness_polling is False


class TestOnlyAnAuthoritativeSourceStampsTheKind:
    """Stamping rewrites where a verdict is published, so it is gated on the
    source claiming its own kind is the authoritative one.

    Redis Streams claims it: the kind comes from the configured stream. Kafka
    does not, and must not -- its kind has always reached routing through the
    payload, and stamping would move events in an existing deployment.
    """

    def test_the_kafka_source_does_not_claim_authority(self):
        from mdx.source.source_kafka import SourceKafka

        assert getattr(SourceKafka, "kind_is_authoritative", False) is False

    def test_the_redis_source_claims_authority(self):
        from mdx.source.source_redis_stream import SourceRedisStream

        assert SourceRedisStream.kind_is_authoritative is True


class TestReadinessRepublishTimer:
    """Redis has no rebalance callback, so this timer is the only thing that
    would report a broker lost after startup.
    """

    @staticmethod
    def _enhancer(needs_polling=True):
        enhancer = MagicMock()
        enhancer._last_readiness_publish_at = 0.0
        enhancer._readiness_hook = None
        enhancer._readiness_needs_polling.return_value = needs_polling
        return enhancer

    def test_a_transport_that_did_not_ask_is_never_published_for(self):
        """The Kafka path: the loop still calls this every iteration, and it has
        to come to nothing."""
        enhancer = self._enhancer(needs_polling=False)
        hook = MagicMock()
        enhancer._readiness_hook = hook
        for _ in range(3):
            # Past the interval each time, so nothing is being credited to the
            # rate limit rather than to the gate.
            enhancer._last_readiness_publish_at = (
                time.monotonic() - entry.READINESS_REPUBLISH_SECONDS - 1
            )
            entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)
        enhancer._publish_assignment_state.assert_not_called()
        hook.assert_not_called()

    def test_the_first_pass_publishes(self):
        enhancer = self._enhancer()
        entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)
        enhancer._publish_assignment_state.assert_called_once()

    def test_a_second_pass_inside_the_interval_does_not(self):
        """The consume loop calls this every iteration; publishing each time
        would mmap the metric shards on every poll."""
        enhancer = self._enhancer()
        entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)
        entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)
        assert enhancer._publish_assignment_state.call_count == 1

    def test_it_publishes_again_once_the_interval_has_passed(self):
        enhancer = self._enhancer()
        entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)
        enhancer._last_readiness_publish_at = (
            time.monotonic() - entry.READINESS_REPUBLISH_SECONDS - 1
        )
        entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)
        assert enhancer._publish_assignment_state.call_count == 2

    def test_a_supervised_child_publishes_through_its_own_hook(self):
        """Under a supervisor the fleet counts belong to the parent and the
        child reports through its ready event, so moving the gauge alone would
        leave the signal the endpoint reads untouched."""
        enhancer = self._enhancer()
        hook = MagicMock()
        enhancer._readiness_hook = hook
        entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)
        hook.assert_called_once()
        enhancer._publish_assignment_state.assert_not_called()

    def test_a_failing_publish_does_not_break_the_consume_loop(self):
        enhancer = self._enhancer()
        enhancer._publish_assignment_state.side_effect = RuntimeError("registry gone")
        entry.AnomalyEnhancer._republish_readiness_periodically(enhancer)

    def test_the_interval_is_not_finer_than_what_reads_it(self):
        """The supervisor polls at one second; resolving readiness finer than
        that buys nothing and costs a shard read per poll."""
        assert entry.READINESS_REPUBLISH_SECONDS >= 1.0
