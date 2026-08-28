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
"""

import time
from unittest.mock import MagicMock, patch

import pytest

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

    def test_the_public_method_and_the_publish_path_agree(self):
        """They disagreed once already, which is how a lone pipeline came to
        answer 200 through its whole startup."""
        enhancer = make_enhancer(True, make_sink(healthy=False))
        assert entry.AnomalyEnhancer.is_ready(enhancer) == entry._pipeline_ready(enhancer)

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


class TestReadinessRepublishTimer:
    """Redis has no rebalance callback, so this timer is the only thing that
    would report a broker lost after startup.
    """

    @staticmethod
    def _enhancer():
        enhancer = MagicMock()
        enhancer._last_readiness_publish_at = 0.0
        enhancer._readiness_hook = None
        return enhancer

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
