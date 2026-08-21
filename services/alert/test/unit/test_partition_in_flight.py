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
    def test_accept_returns_a_token_carrying_the_key(self, tracker):
        assert tracker.accept(P0).key == P0

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
        assert tracker.accept(None).key is None
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
                tracker.accept(P0).release()

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


class TestAdmissionOwnership:
    """One accepted message, released exactly once by whoever ends up with it.

    Taken when the message is scheduled -- before any worker queue, because a
    queued record is already this instance's responsibility and a drain that
    ignored it would finish while work was still waiting to begin. Released by
    the stage that drops it, or by the completion callback if it was
    dispatched.
    """

    def test_accepting_counts_the_partition(self, tracker):
        admission = tracker.accept(P0)
        assert tracker.in_flight(P0) == 1
        assert admission.key == P0

    def test_releasing_is_idempotent(self, tracker):
        admission = tracker.accept(P0)
        admission.release()
        admission.release()
        assert tracker.in_flight(P0) == 0

    def test_a_source_without_partitions_counts_nothing(self, tracker):
        admission = tracker.accept(None)
        admission.release()
        assert tracker.total() == 0

    def test_transfer_marks_ownership_as_moved(self, tracker):
        admission = tracker.accept(P0)
        assert admission.transferred is False
        assert admission.transfer() is admission
        assert admission.transferred is True
        assert tracker.in_flight(P0) == 1, "transfer must not release"

    def test_a_transferred_admission_is_released_by_its_new_owner(self, tracker):
        admission = tracker.accept(P0).transfer()
        admission.release()
        assert tracker.in_flight(P0) == 0

    def test_a_queued_message_is_already_counted(self, tracker):
        # The point of taking it at scheduling time: nothing has run yet.
        tracker.accept(P0)
        assert tracker.drain([P0], timeout=0.2) is False


class TestTheDrainBudgetIsPerRebalance:
    """Every consumer in a process is revoked together and drains in turn.

    They run one after another on the consume thread, so a budget charged per
    consumer multiplies by the number of source topics while the poll interval
    it is measured against does not. Two topics at fifteen seconds each is
    thirty of a sixty-second allowance, with nothing left for a third.
    """

    @staticmethod
    def _enhancer():
        from unittest.mock import MagicMock
        from enhance_alert_with_vlm import AnomalyEnhancer

        stub = MagicMock()
        stub._partition_in_flight = PartitionInFlight()
        stub._rebalance_drain_deadline = None
        stub.source.is_ready.return_value = False
        stub._drain_revoked_partitions = (
            AnomalyEnhancer._drain_revoked_partitions.__get__(stub)
        )
        return stub

    def test_the_first_revoke_opens_a_budget(self):
        stub = self._enhancer()
        stub._drain_revoked_partitions([P0])
        assert stub._rebalance_drain_deadline is not None

    def test_a_second_consumer_shares_the_same_budget(self):
        stub = self._enhancer()
        stub._drain_revoked_partitions([P0])
        first = stub._rebalance_drain_deadline
        stub._drain_revoked_partitions([P1])
        assert stub._rebalance_drain_deadline == first, "each consumer got its own budget"

    def test_the_budget_bounds_the_total_not_each_consumer(self):
        stub = self._enhancer()
        stub._rebalance_drain_deadline = time.monotonic() + 0.15
        stub._partition_in_flight.accept(P0)
        stub._partition_in_flight.accept(P1)

        started = time.monotonic()
        stub._drain_revoked_partitions([P0])
        stub._drain_revoked_partitions([P1])
        # Two consumers, one budget: the second finds it nearly spent.
        assert time.monotonic() - started < 1.0


class TestTheBudgetIsReopenedInEveryDeployment:
    """The reset used to live only on the multi-process path.

    The revoke hook is installed for every deployment, so a single-process
    instance drains too -- and with nothing reopening the budget its first
    rebalance spent it for good, leaving every later drain to give up at once
    and report a timeout it had never waited for.
    """

    @staticmethod
    def _enhancer(ready=True, held=1):
        from unittest.mock import MagicMock
        import enhance_alert_with_vlm as entry

        enhancer = MagicMock()
        enhancer.source.is_ready.return_value = ready
        enhancer.source.assigned_partition_count.return_value = held
        enhancer._rebalance_drain_deadline = time.monotonic() + 5
        entry.AnomalyEnhancer._publish_assignment_state(enhancer)
        return enhancer

    def test_a_decided_assignment_closes_the_spent_budget(self):
        assert self._enhancer(ready=True)._rebalance_drain_deadline is None

    def test_an_undecided_assignment_leaves_it_open(self):
        # Still mid-rebalance: the budget bounds the whole of it.
        assert self._enhancer(ready=False)._rebalance_drain_deadline is not None

    def test_every_deployment_registers_the_hook(self):
        # The single-process path never called set_assignment_change_hook, so
        # the reset above was unreachable there.
        import inspect
        import enhance_alert_with_vlm as entry

        source = inspect.getsource(entry.AnomalyEnhancer.__init__)
        assert "set_assignment_change_hook" in source


class TestSplit:
    """A stage that fans one accepted message out to several.

    Handing the same admission to each of them meant the first completion
    released the whole group, so a drain could pass over work still running.
    """

    def test_each_split_is_counted_separately(self):
        tracker = PartitionInFlight()
        first = tracker.accept(P0)
        second = first.split()
        assert tracker.in_flight(P0) == 2

        first.release()
        assert tracker.in_flight(P0) == 1, "one release cleared both"
        second.release()
        assert tracker.in_flight(P0) == 0

    def test_a_drain_waits_for_the_split(self):
        tracker = PartitionInFlight()
        first = tracker.accept(P0)
        second = first.split()
        first.release()
        assert tracker.drain([P0], timeout=0.1) is False
        second.release()
        assert tracker.drain([P0], timeout=0.1) is True

    def test_splitting_an_untracked_admission_stays_untracked(self):
        # A batch with no partition key produces admissions that count
        # nothing; a split of one must not start counting.
        tracker = PartitionInFlight()
        split = tracker.accept(None).split()
        assert tracker.total() == 0
        split.release()
        assert tracker.total() == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])