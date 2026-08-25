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

"""Child entry point: which pipeline process owns the instance-wide work."""

import os
from unittest.mock import MagicMock, call, patch

import time

import pytest

import enhance_alert_with_vlm as entry


@pytest.fixture
def built():
    """Capture the instance_leader each child would construct itself with."""
    seen = []

    def fake_enhancer(config_path, instance_leader=True, seed_shared_store=True):
        seen.append(instance_leader)
        built = MagicMock()
        built.source.assigned_partition_count.return_value = 2
        built.source.is_ready.return_value = True
        return built

    with patch.object(entry, "AnomalyEnhancer", side_effect=fake_enhancer), \
         patch.object(entry, "_exit_when_parent_dies"), \
         patch.object(entry, "_log_instance_concurrency"):
        yield seen


@pytest.fixture
def enhancer():
    """A child whose every startup step records itself in call order."""
    built = MagicMock()
    built.source.assigned_partition_count.return_value = 2
    built.source.is_ready.return_value = True
    with patch.object(entry, "AnomalyEnhancer", return_value=built), \
         patch.object(entry, "_exit_when_parent_dies"), \
         patch.object(entry, "_log_instance_concurrency"):
        yield built


class TestInstanceLeaderElection:
    """The verdict reaper is per instance, not per pipeline.

    Running it in every child defeats the reaper's own request-rate throttle.
    """

    def test_child_zero_leads(self, built):
        entry._run_pipeline_process("config.yaml", 0, os.getpid(), process_count=4)
        assert built == [True]

    @pytest.mark.parametrize("index", [1, 2, 7])
    def test_every_other_child_follows(self, built, index):
        entry._run_pipeline_process("config.yaml", index, os.getpid(), process_count=8)
        assert built == [False]

    def test_exactly_one_leader_across_the_instance(self, built):
        for index in range(6):
            entry._run_pipeline_process("config.yaml", index, os.getpid(), process_count=6)
        assert built.count(True) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestChildReadiness:
    """A child counts as ready only once its consumers have joined the group.

    Announcing earlier lets a producer write past a `latest` offset that no
    member has reached yet, and those records are never delivered.
    """

    def test_waits_for_the_source_before_signalling(self, enhancer):
        order = MagicMock()
        order.await_ready.return_value = True
        enhancer.source.await_ready = order.await_ready
        # The child raises when the loop reports failure, so the stub has to
        # report success or this test fails for the wrong reason.
        order.process_anomalies.return_value = True
        enhancer.process_anomalies = order.process_anomalies
        ready = MagicMock()
        ready.set = order.set

        # An explicit non-default: asserting the default would pass even if
        # the entry point stopped forwarding the budget at all.
        entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1, ready,
                                    join_deadline=time.time() + 42.0)

        assert order.mock_calls == [call.await_ready(timeout=pytest.approx(42.0, abs=1.0)), call.set(),
                                    call.process_anomalies()], order.mock_calls

    def test_never_signals_when_the_join_failed(self, enhancer):
        # Reporting ready here is the exact failure this path exists to stop:
        # a producer would publish past an offset no member is reading.
        enhancer.source.await_ready.return_value = False
        ready = MagicMock()

        with pytest.raises(RuntimeError, match="consumer group"):
            entry._run_pipeline_process("config.yaml", 1, os.getpid(), 2, ready)

        ready.set.assert_not_called()

    def test_a_failed_join_does_not_start_processing(self, enhancer):
        enhancer.source.await_ready.return_value = False

        with pytest.raises(RuntimeError):
            entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1, None)

        enhancer.process_anomalies.assert_not_called()

    def test_a_child_without_an_event_still_starts(self, enhancer):
        enhancer.source.await_ready.return_value = True
        entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1, None)
        enhancer.process_anomalies.assert_called_once()


class TestAFailedConsumeLoopFailsTheChild:
    """A child whose loop stopped on an error must not exit 0.

    The supervisor fails the instance on any child exit, so the outcome was
    already right -- but it logged exitcode=0 for a crash, and the
    single-process path raises. Both report the same way now.
    """

    @staticmethod
    def _run(loop_result):
        from unittest.mock import MagicMock, patch

        enhancer = MagicMock()
        enhancer.process_anomalies.return_value = loop_result
        enhancer.source.await_ready.return_value = True
        with patch.object(entry, "AnomalyEnhancer", return_value=enhancer), \
             patch.object(entry, "_exit_when_parent_dies"), \
             patch.object(entry, "signal"), \
             patch.object(entry, "_publish_readiness"):
            return entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1)

    def test_a_failed_loop_raises(self):
        with pytest.raises(RuntimeError, match="stopped on an error"):
            self._run(loop_result=False)

    def test_a_clean_loop_returns(self):
        self._run(loop_result=True)


class TestOrphanProtection:
    """An orphan keeps its consumer-group slot, which stalls its partitions.

    The parent-PID recheck is the half that works everywhere: PR_SET_PDEATHSIG
    is Linux-only and can be refused by seccomp, and behind those two failure
    paths the recheck used to be skipped exactly when it was all that was left.
    """

    @staticmethod
    def _run(prctl_result, ppid):
        from unittest.mock import MagicMock, patch

        libc = MagicMock()
        libc.prctl.return_value = prctl_result
        with patch("ctypes.CDLL", return_value=libc), \
             patch.object(entry.os, "getppid", return_value=ppid), \
             patch.object(entry.os, "_exit", side_effect=SystemExit) as exit_now:
            try:
                entry._exit_when_parent_dies(4321)
            except SystemExit:
                pass
        return exit_now.called

    def test_an_already_dead_parent_exits_the_child(self):
        assert self._run(prctl_result=0, ppid=1) is True

    def test_the_recheck_happens_even_when_prctl_is_refused(self):
        # seccomp refuses it; the recheck is then the only protection there is.
        assert self._run(prctl_result=-1, ppid=1) is True

    def test_a_live_parent_leaves_the_child_running(self):
        assert self._run(prctl_result=0, ppid=4321) is False


class TestTheStartupBudgetReachesTheChildren:
    """The budget threading has been rewritten twice and pinned by nothing.

    Reviewers had to re-derive from source each round that the group join
    actually draws from the same budget the parent's watchdog counts down.
    """

    def test_the_spawn_helper_passes_the_deadline_to_the_child(self):
        from unittest.mock import MagicMock, patch

        when = time.time() + 42.0
        with patch.object(entry, "_pipeline_mp_context") as ctx:
            ctx.return_value.Process = MagicMock()
            entry._start_pipeline_process("config.yaml", 2, 4, None, join_deadline=when)
            args = ctx.return_value.Process.call_args.kwargs["args"]

        assert args[-1] == when, "the child was not given the deadline"

    def test_the_child_shares_the_parents_deadline_not_its_duration(self):
        # Handed the same duration, the child began counting after its spawn
        # and its constructor, so its window always ended after the parent's
        # and the parent killed children a second from joining.
        from unittest.mock import MagicMock, patch

        seen = {}
        with patch.object(entry, "ProcessSupervisor") as supervisor, \
             patch.object(entry, "_start_pipeline_process",
                          side_effect=lambda *a: seen.setdefault("args", a)), \
             patch.object(entry, "_announce_when_all_ready"):
            supervisor.return_value.run = MagicMock()
            before = time.time()
            entry.run_multi_process_pipeline("config.yaml", 2, readiness_timeout=42.0)
            supervisor.call_args.kwargs["spawn"](0)

        # One instant for parent, watchdog and child, and it is the budget's
        # own remainder -- padding it past the budget was what pushed the
        # whole of startup beyond the deadline it is measured against.
        deadline = seen["args"][-1]
        expected = 42.0
        assert before + expected - 1 <= deadline <= before + expected + 1, (
            f"not the instance's final readiness instant: {deadline - before}"
        )


class TestInstanceReadiness:
    """The parent announces readiness once, after every child has signalled."""

    def test_announces_only_after_the_last_child(self):
        import threading
        events = [threading.Event() for _ in range(3)]
        announced = threading.Event()

        entry._announce_when_all_ready(events, announced.set)

        for event in events[:-1]:
            event.set()
        assert not announced.wait(0.1), "announced before the last child"

        events[-1].set()
        assert announced.wait(2), "never announced"

    def test_a_child_that_never_arrives_leaves_the_instance_unready(self):
        import threading
        events = [threading.Event(), threading.Event()]
        announced = threading.Event()
        events[0].set()

        entry._announce_when_all_ready(events, announced.set, timeout=0.2)

        assert not announced.wait(1), "announced with a partially joined group"

    def test_the_wait_is_bounded(self, caplog):
        import logging
        import threading
        entry._announce_when_all_ready([threading.Event()], lambda: None, timeout=0.1)
        with caplog.at_level(logging.ERROR):
            threading.Event().wait(0.5)
        assert any("not ready within" in r.getMessage() for r in caplog.records)

    def test_expiry_ends_the_run(self):
        # A permanently unready instance leaves the missing child's partitions
        # unowned, and only a restart reassigns them -- so the wait expiring
        # has to bring the container down rather than log and carry on.
        import threading
        expired = threading.Event()
        entry._announce_when_all_ready([threading.Event()], lambda: None,
                                       on_timeout=expired.set, timeout=0.1)
        assert expired.wait(2), "the readiness timeout did not end the run"


class TestReadinessNeedsEveryWorkerAtOnce:
    """Each worker ready at some point is not the fleet being ready.

    The wait ran through the events in order. An early worker could be
    revoked while the wait was still blocked on a later one, and the
    announcement -- which the harness and producers gate on -- went out with
    part of the fleet holding nothing.
    """

    def test_a_worker_that_drops_out_delays_the_announcement(self):
        import threading
        first, second = threading.Event(), threading.Event()
        announced = threading.Event()

        entry._announce_when_all_ready([first, second], announced.set, timeout=5)

        first.set()
        # It is now blocked on the second; the first goes away meanwhile.
        second.set()
        first.clear()
        assert not announced.wait(0.3), "announced with a worker no longer ready"

        first.set()
        assert announced.wait(2), "never announced once the fleet was whole"

    def test_the_whole_fleet_ready_announces(self):
        import threading
        events = [threading.Event() for _ in range(3)]
        announced = threading.Event()
        entry._announce_when_all_ready(events, announced.set, timeout=5)
        for event in events:
            event.set()
        assert announced.wait(2)


class TestSeedingHappensBeforeAnyChild:
    """The prompt store is written by the supervisor, not by a child.

    Only one child used to seed, and it seeded while building its own
    pipeline. The children that skipped that write finished building first, so
    they could start reading a store nobody had filled yet and fail every
    lookup as having no prompt.
    """

    def test_the_supervisor_seeds(self):
        with patch("handlers.prompt_handler.prompt_manager.PromptManager") as manager:
            entry.seed_prompt_store("config.yaml")
        manager.assert_called_once_with("config.yaml", seed_prompts=True)

    def test_a_failure_to_seed_is_fatal(self):
        # Serving traffic against a store that may be empty drops events
        # silently, which is worse than refusing to start.
        with patch("handlers.prompt_handler.prompt_manager.PromptManager",
                   side_effect=RuntimeError("ES unreachable")):
            with pytest.raises(RuntimeError, match="ES unreachable"):
                entry.seed_prompt_store("config.yaml")

    def test_children_do_not_seed_the_shared_store(self, built):
        entry._run_pipeline_process("config.yaml", 0, os.getpid(), 2)
        # `built` records instance_leader; the seeding flag is asserted here
        # against the constructor call itself.
        assert entry.AnomalyEnhancer.call_args.kwargs["seed_shared_store"] is False


class TestChildrenStartInAFreshInterpreter:
    """Children are spawned, not forked.

    By the time the pipeline processes start, this process is running a
    Prometheus HTTP server thread, a readiness thread and a FastAPI child of
    its own. Forking copies the address space but only the calling thread, so
    a child would inherit locks, sockets and logging handlers mid-use with
    nobody left to release them.
    """

    def test_the_context_is_spawn(self):
        assert entry._pipeline_mp_context().get_start_method() == "spawn"

    def test_children_are_created_from_it(self):
        with patch.object(entry, "_pipeline_mp_context") as ctx:
            entry._start_pipeline_process("config.yaml", 0, 1, None)
        ctx.return_value.Process.assert_called_once()
        assert ctx.return_value.Process.return_value.start.called

    def test_the_child_entry_point_and_its_plain_arguments_pickle(self):
        # Spawn re-imports and unpickles instead of inheriting memory, so a
        # target defined anywhere but module level fails at start(). The
        # readiness Event is deliberately not included: multiprocessing
        # refuses to pickle a synchronisation primitive on its own and hands
        # it over through the Process constructor instead, which is what
        # test_a_readiness_event_crosses_a_spawned_process exercises.
        import pickle
        payload = (entry._run_pipeline_process, ("config.yaml", 0, os.getpid(), 2))
        assert pickle.loads(pickle.dumps(payload))[0] is entry._run_pipeline_process

    def test_a_readiness_event_crosses_a_spawned_process(self):
        ctx = entry._pipeline_mp_context()
        event = ctx.Event()
        process = ctx.Process(target=_set_event, args=(event,))
        process.start()
        process.join(timeout=30)
        assert event.is_set(), "the child never signalled through the event"
        assert process.exitcode == 0


def _set_event(event):
    """Module level so spawn can import it in the child."""
    event.set()


class TestReadinessTracksTheLiveAssignment:
    """Readiness is not a milestone a process passes once.

    A rebalance can take every partition from a worker that is still running.
    Until it is given some back it is serving nothing, and both the readiness
    signal and the partition gauge have to say so.
    """

    @staticmethod
    def _child(held, ready=True):
        enhancer = MagicMock()
        enhancer.source.assigned_partition_count.return_value = held
        enhancer.source.is_ready.return_value = ready
        # Bound for real: the gauge and the drain budget live on the enhancer
        # so a single-process instance gets them too, and a mock here would
        # let that wiring rot unnoticed.
        enhancer._publish_assignment_state = (
            lambda: entry.AnomalyEnhancer._publish_assignment_state(enhancer)
        )
        return enhancer

    def test_holding_partitions_is_ready(self):
        event = entry._pipeline_mp_context().Event()
        assert entry._publish_readiness(self._child(held=2), 0, event) is True
        assert event.is_set()

    def test_a_revoke_clears_readiness(self):
        # A revoke makes the assignment undecided again, which is what
        # readiness follows. Holding zero partitions with a decided assignment
        # is a supported state and stays ready.
        event = entry._pipeline_mp_context().Event()
        entry._publish_readiness(self._child(held=2), 0, event)
        entry._publish_readiness(self._child(held=0, ready=False), 0, event)
        assert not event.is_set()

    def test_a_decided_but_empty_assignment_is_still_ready(self):
        # replicas x processes > partitions leaves members legitimately empty;
        # they are serving correctly and must not report otherwise.
        event = entry._pipeline_mp_context().Event()
        entry._publish_readiness(self._child(held=0, ready=True), 0, event)
        assert event.is_set()

    def test_being_reassigned_raises_it_again(self):
        event = entry._pipeline_mp_context().Event()
        entry._publish_readiness(self._child(held=0), 0, event)
        entry._publish_readiness(self._child(held=3), 0, event)
        assert event.is_set()

    def test_an_undecided_assignment_is_not_ready(self):
        event = entry._pipeline_mp_context().Event()
        assert entry._publish_readiness(self._child(held=2, ready=False), 0, event) is False
        assert not event.is_set()

    def test_the_gauge_reports_the_count_even_when_empty(self):
        # Zero partitions is worth seeing; it is just not unreadiness.
        child = self._child(held=0, ready=True)
        entry._publish_readiness(child, 0, None)
        child.source.assigned_partition_count.assert_called()

    def test_the_hook_is_registered_so_a_revoke_reaches_it(self, enhancer):
        enhancer.source.await_ready.return_value = True
        enhancer.source.assigned_partition_count.return_value = 2
        enhancer.source.is_ready.return_value = True
        entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1, None)
        enhancer.source.set_assignment_change_hook.assert_called_once()


class TestReadinessWaitsForStartupToFinish:
    """The first assignment arrives while this process is still starting.

    It comes from inside the group join, before the dispatcher is built.
    Raising readiness there tells the supervisor to announce the instance, and
    a producer gated on that announcement starts sending to a pipeline that
    cannot yet take the work.
    """

    @staticmethod
    def _child(held=2, ready=True):
        enhancer = MagicMock()
        enhancer.source.assigned_partition_count.return_value = held
        enhancer.source.is_ready.return_value = ready
        # Bound for real: the gauge and the drain budget live on the enhancer
        # so a single-process instance gets them too, and a mock here would
        # let that wiring rot unnoticed.
        enhancer._publish_assignment_state = (
            lambda: entry.AnomalyEnhancer._publish_assignment_state(enhancer)
        )
        return enhancer

    def test_an_assignment_before_startup_finishes_does_not_raise_it(self):
        event = entry._pipeline_mp_context().Event()
        entry._publish_readiness(self._child(), 0, event, started=False)
        assert not event.is_set()

    def test_the_gauge_is_still_published_while_starting(self):
        # Live assignment state is useful before this process is ready.
        child = self._child(held=3)
        entry._publish_readiness(child, 0, None, started=False)
        child.source.assigned_partition_count.assert_called()

    def test_finishing_startup_raises_it(self):
        event = entry._pipeline_mp_context().Event()
        entry._publish_readiness(self._child(), 0, event, started=False)
        entry._publish_readiness(self._child(), 0, event, started=True)
        assert event.is_set()

    def test_a_revoke_lowers_it_whatever_the_startup_state(self):
        # Reported at once; there is nothing to wait for in that direction.
        event = entry._pipeline_mp_context().Event()
        entry._publish_readiness(self._child(), 0, event, started=True)
        entry._publish_readiness(self._child(held=0, ready=False), 0, event, started=False)
        assert not event.is_set()

    def test_the_hook_cannot_raise_readiness_before_the_child_logs_ready(self, enhancer):
        enhancer.source.await_ready.return_value = True
        enhancer.source.assigned_partition_count.return_value = 2
        enhancer.source.is_ready.return_value = True
        event = entry._pipeline_mp_context().Event()

        # Fire the hook the way the join does, from inside await_ready.
        def fire_hook_during_join(timeout=None):
            hook = enhancer.source.set_assignment_change_hook.call_args.args[0]
            hook()
            assert not event.is_set(), "readiness raised before startup finished"
            return True

        enhancer.source.await_ready.side_effect = fire_hook_during_join
        entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1, event)
        assert event.is_set(), "readiness never raised after startup"

    def test_the_child_logs_ready_before_it_signals_the_supervisor(self, enhancer, caplog):
        # Setting the event wakes the supervisor, which announces the instance
        # by writing to the same log. Signalling first left the two racing, and
        # the order held only because a futex wake is slower than the next
        # line -- a scheduler hiccup would have reversed it.
        import logging
        enhancer.source.await_ready.return_value = True
        enhancer.source.assigned_partition_count.return_value = 2
        enhancer.source.is_ready.return_value = True

        order = []
        event = MagicMock()
        event.set.side_effect = lambda: order.append("signalled")

        class Recorder(logging.Handler):
            def emit(self, record):
                if "ready (pid=" in record.getMessage():
                    order.append("logged")

        handler = Recorder()
        logging.getLogger("enhance_alert_with_vlm").addHandler(handler)
        logging.getLogger("enhance_alert_with_vlm").setLevel(logging.INFO)
        try:
            entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1, event)
        finally:
            logging.getLogger("enhance_alert_with_vlm").removeHandler(handler)

        assert order == ["logged", "signalled"], order
