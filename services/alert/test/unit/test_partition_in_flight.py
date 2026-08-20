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

"""Per-partition in-flight accounting, and the drain that waits on it.

A leaked count is the failure that matters: the drain then waits out its whole
timeout on every rebalance, which is exactly the stall it exists to avoid.
"""

import threading
import time

import pytest

from utils.partition_in_flight import PartitionInFlight

P0, P1 = ("mdx-incidents", 0), ("mdx-incidents", 1)


@pytest.fixture
def tracker():
    return PartitionInFlight()


class TestCounting:
    def test_accept_returns_the_key_to_release_with(self, tracker):
        assert tracker.accept(P0) == P0

    def test_counts_are_per_partition(self, tracker):
        tracker.accept(P0)
        tracker.accept(P0)
        tracker.accept(P1)
        assert tracker.in_flight(P0) == 2
        assert tracker.in_flight(P1) == 1

    def test_release_brings_it_back_down(self, tracker):
        tracker.accept(P0)
        tracker.release(P0)
        assert tracker.in_flight(P0) == 0

    def test_a_source_without_partitions_is_not_counted(self, tracker):
        assert tracker.accept(None) is None
        tracker.release(None)
        assert tracker.total() == 0

    def test_releasing_more_than_was_accepted_does_not_go_negative(self, tracker):
        tracker.release(P0)
        tracker.release(P0)
        assert tracker.in_flight(P0) == 0
        tracker.accept(P0)
        assert tracker.in_flight(P0) == 1


class TestDrain:
    def test_returns_at_once_when_nothing_is_owed(self, tracker):
        assert tracker.drain([P0], timeout=5) is True

    def test_no_partitions_is_not_a_wait(self, tracker):
        assert tracker.drain([], timeout=5) is True

    def test_waits_until_the_last_message_is_released(self, tracker):
        tracker.accept(P0)
        tracker.accept(P0)

        def finish():
            time.sleep(0.05)
            tracker.release(P0)
            time.sleep(0.05)
            tracker.release(P0)

        threading.Thread(target=finish, daemon=True).start()
        assert tracker.drain([P0], timeout=5) is True
        assert tracker.in_flight(P0) == 0

    def test_gives_up_rather_than_holding_up_the_rebalance(self, tracker):
        # Overrunning the poll interval costs the member its place and starts
        # another rebalance, which is worse than the overlap left behind.
        tracker.accept(P0)
        started = time.monotonic()
        assert tracker.drain([P0], timeout=0.2) is False
        assert time.monotonic() - started < 2

    def test_only_the_revoked_partitions_are_waited_on(self, tracker):
        tracker.accept(P1)          # keeps running, and must not block P0
        assert tracker.drain([P0], timeout=0.5) is True

    def test_drains_several_partitions_together(self, tracker):
        tracker.accept(P0)
        tracker.accept(P1)

        def finish():
            time.sleep(0.05)
            tracker.release(P0)
            tracker.release(P1)

        threading.Thread(target=finish, daemon=True).start()
        assert tracker.drain([P0, P1], timeout=5) is True


class TestConcurrentUse:
    def test_counts_survive_parallel_accept_and_release(self, tracker):
        def churn():
            for _ in range(200):
                tracker.release(tracker.accept(P0))

        threads = [threading.Thread(target=churn) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert tracker.total() == 0

    def test_a_drain_wakes_on_the_final_release(self, tracker):
        tracker.accept(P0)
        result = {}

        def drainer():
            result["drained"] = tracker.drain([P0], timeout=5)

        thread = threading.Thread(target=drainer)
        thread.start()
        time.sleep(0.05)
        tracker.release(P0)
        thread.join(timeout=5)
        assert result["drained"] is True


class TestTheCountIsNeverLeaked:
    """Taken and released in one place, so no path can leak it.

    Counting where messages are scheduled would leak on every message dropped
    by dedup or the rate limiter before it reaches dispatch, and a leaked
    count makes the drain wait out its whole timeout on every rebalance.
    """

    @staticmethod
    def _stub(mode="event_loop"):
        from unittest.mock import MagicMock
        from handlers.async_dispatch_mixin import AsyncDispatchMixin

        stub = MagicMock()
        stub.pipeline_mode = mode
        stub._partition_in_flight = PartitionInFlight()
        stub._process_single_message_with_mode = (
            AsyncDispatchMixin._process_single_message_with_mode.__get__(stub)
        )
        return stub

    def test_sync_mode_releases_after_processing(self):
        stub = self._stub(mode="sync")
        stub._process_single_message_with_mode(0, {"id": "a"}, source_partition=P0)
        assert stub._partition_in_flight.in_flight(P0) == 0

    def test_sync_mode_releases_even_when_processing_raises(self):
        stub = self._stub(mode="sync")
        stub._process_single_message.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            stub._process_single_message_with_mode(0, {"id": "a"}, source_partition=P0)
        assert stub._partition_in_flight.in_flight(P0) == 0

    def test_a_missing_dispatch_target_releases_on_the_inline_fallback(self):
        stub = self._stub()
        stub.async_vlm_runtime = None
        stub._process_single_message_with_mode(0, {"id": "a"}, source_partition=P0)
        assert stub._partition_in_flight.in_flight(P0) == 0

    def test_a_failed_submit_releases_on_the_inline_fallback(self):
        stub = self._stub()
        stub.async_vlm_runtime.submit_coroutine.side_effect = RuntimeError("no")
        stub._process_single_message_with_mode(0, {"id": "a"}, source_partition=P0)
        assert stub._partition_in_flight.in_flight(P0) == 0

    def test_a_dispatched_message_stays_counted_until_it_completes(self):
        # It outlives the call that dispatched it, so the count has to too.
        stub = self._stub()
        stub._process_single_message_with_mode(0, {"id": "a"}, source_partition=P0)
        assert stub._partition_in_flight.in_flight(P0) == 1

    def test_the_done_callback_carries_the_partition(self):
        stub = self._stub()
        stub._process_single_message_with_mode(0, {"id": "a"}, source_partition=P0)
        assert stub._track_dispatched_future.call_args.kwargs["partition_key"] == P0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
