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

"""Restart / teardown behavior of the pipeline process supervisor."""

import multiprocessing
import os
import signal
import threading
import time

import pytest

from utils import process_supervisor
from utils.process_supervisor import ProcessSupervisor, SupervisedProcessError


class FakeProcess:
    """Stand-in for multiprocessing.Process with scriptable liveness."""

    def __init__(self, index, alive=True, exitcode=None):
        self.index = index
        self.pid = 1000 + index
        self._alive = alive
        self.exitcode = exitcode
        self.terminated = False
        self.killed = False
        self.joined = False
        # A child that installed its own handler and is still draining does
        # not die on terminate; the timeline has to escalate past it.
        self.ignore_terminate = False
        self.join_timeouts = []

    def is_alive(self):
        return self._alive

    def die(self, exitcode=1):
        self._alive = False
        self.exitcode = exitcode

    def join(self, timeout=None):
        self.joined = True
        self.join_timeouts.append(timeout)

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False


class Spawner:
    def __init__(self):
        self.spawned = []

    def __call__(self, index):
        process = FakeProcess(index)
        self.spawned.append(process)
        return process


def _supervisor(spawn, count=2, **kwargs):
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("stop_timeout", 0.0)
    return ProcessSupervisor(count=count, spawn=spawn, **kwargs)


class TestStartAndStop:
    def test_start_spawns_one_process_per_slot(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=3)
        supervisor.start()
        assert [p.index for p in supervisor.processes] == [0, 1, 2]

    def test_stop_terminates_every_live_child(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2)
        supervisor.start()
        supervisor.stop()
        assert all(p.terminated for p in spawn.spawned)
        assert supervisor.processes == []

    def test_stop_kills_children_that_ignore_terminate(self):
        class StubbornProcess(FakeProcess):
            def terminate(self):
                self.terminated = True

        spawn_calls = []

        def spawn(index):
            process = StubbornProcess(index)
            spawn_calls.append(process)
            return process

        supervisor = _supervisor(spawn, count=1)
        supervisor.start()
        supervisor.stop()
        assert spawn_calls[0].killed

    def test_stop_reports_each_child_to_the_exit_hook(self):
        seen = []
        supervisor = _supervisor(Spawner(), count=2,
                                 on_exit=lambda p, expected: seen.append(p))
        supervisor.start()
        supervisor.stop()
        assert len(seen) == 2

    def test_count_must_be_positive(self):
        with pytest.raises(ValueError):
            ProcessSupervisor(count=0, spawn=Spawner())


class TestAnyExitFailsTheInstance:
    """A child that exits on its own takes the instance down with it.

    Replacing it in place kept the container alive around a partially rebuilt
    instance: the replacement rejoins the group and forces a rebalance, the
    survivors keep whatever work they had, and the cause of the exit is still
    there. Failing whole makes the orchestrator's restart the recovery path.
    """

    def test_a_dead_child_raises(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2)
        supervisor.start()
        supervisor.processes[1].die(exitcode=9)

        with pytest.raises(SupervisedProcessError, match="pipeline process 1"):
            supervisor._fail_on_any_exit()

    def test_the_exit_code_is_carried_into_the_error(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1)
        supervisor.start()
        supervisor.processes[0].die(exitcode=137)

        with pytest.raises(SupervisedProcessError, match="exitcode=137"):
            supervisor._fail_on_any_exit()

    def test_nothing_is_replaced(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2)
        supervisor.start()
        supervisor.processes[0].die()

        with pytest.raises(SupervisedProcessError):
            supervisor._fail_on_any_exit()
        assert len(spawn.spawned) == 2

    def test_live_children_are_left_alone(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2)
        supervisor.start()
        supervisor._fail_on_any_exit()
        assert len(spawn.spawned) == 2

    def test_an_exit_during_shutdown_is_not_a_failure(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1)
        supervisor.start()
        supervisor.processes[0].die(exitcode=0)
        supervisor.request_shutdown()

        supervisor._fail_on_any_exit()      # expected, so it must not raise

    def test_the_run_loop_tears_the_rest_down_on_the_way_out(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=3, poll_interval=0.01)
        supervisor.start()
        supervisor.processes[1].die(exitcode=1)

        with pytest.raises(SupervisedProcessError):
            supervisor.run()

        # Survivors must not be left holding consumer-group slots.
        assert spawn.spawned[0].terminated or spawn.spawned[0].killed
        assert spawn.spawned[2].terminated or spawn.spawned[2].killed

    def test_every_child_is_reported_to_the_exit_hook_exactly_once(self):
        seen = []
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=3, poll_interval=0.01,
                                 on_exit=lambda p, expected: seen.append(p))
        supervisor.start()
        supervisor.processes[1].die(exitcode=1)

        with pytest.raises(SupervisedProcessError):
            supervisor.run()

        assert len(seen) == 3
        assert len(set(id(p) for p in seen)) == 3


class TestRunLoop:
    def test_run_exits_and_tears_down_on_shutdown(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2, poll_interval=0.01)
        thread = threading.Thread(target=supervisor.run)
        thread.start()
        supervisor.request_shutdown()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert all(p.terminated or p.killed for p in spawn.spawned)


class TestRealProcesses:
    """Same contract against multiprocessing.Process rather than a fake."""

    @staticmethod
    def _spawn(index):
        process = multiprocessing.Process(target=time.sleep, args=(120,))
        process.start()
        return process

    def test_a_killed_child_takes_the_others_down_with_it(self):
        supervisor = ProcessSupervisor(
            count=2,
            spawn=self._spawn,
            poll_interval=0.05,
            stop_timeout=5.0,
        )
        supervisor.start()
        pids = [p.pid for p in supervisor.processes]
        try:
            os.kill(pids[0], signal.SIGKILL)
            deadline = time.monotonic() + 10
            while supervisor.processes[0].is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)

            with pytest.raises(SupervisedProcessError):
                supervisor.run()
        finally:
            supervisor.stop()

        # Including the survivor: an orphan keeps its consumer-group slot.
        for pid in pids:
            with pytest.raises(OSError):
                os.kill(pid, 0)


class TestFleetStateIsReported:
    """Degraded capacity is invisible from throughput alone.

    An instance short of a process keeps serving whatever the survivors still
    own, at a lower ceiling and with no signal that anything is wrong.
    """

    def test_the_hook_runs_while_supervising(self):
        calls = []
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2, poll_interval=0.01,
                                 on_poll=lambda: calls.append(1))
        supervisor.start()
        thread = threading.Thread(target=supervisor.run)
        thread.start()
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        supervisor.request_shutdown()
        thread.join(timeout=5)
        assert calls, "fleet state was never published"

    def test_a_failing_hook_does_not_take_the_supervisor_down(self):
        # Reporting is not the job; supervising is.
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1, poll_interval=0.01,
                                 on_poll=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        supervisor.start()
        thread = threading.Thread(target=supervisor.run)
        thread.start()
        time.sleep(0.05)
        supervisor.request_shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()

    def test_shutdown_requested_distinguishes_expected_exits(self):
        supervisor = _supervisor(Spawner(), count=1)
        assert supervisor.shutdown_requested is False
        supervisor.request_shutdown()
        assert supervisor.shutdown_requested is True


class TestExitReasonSurvivesTeardown:
    """A crash must not be reported as a shutdown.

    Tearing down sets the shutdown flag before reaping, so anything reading it
    afterwards saw every exit as one that had been asked for -- including the
    crash that started the teardown, which is the one case the distinction
    exists for.
    """

    @staticmethod
    def _reasons(request_shutdown: bool):
        seen = []
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2, poll_interval=0.01,
                                 on_exit=lambda p, expected: seen.append(expected))
        supervisor.start()
        if request_shutdown:
            supervisor.request_shutdown()
            supervisor.run()
        else:
            supervisor.processes[0].die(exitcode=1)
            with pytest.raises(SupervisedProcessError):
                supervisor.run()
        return seen

    def test_only_the_process_that_crashed_is_reported_as_unexpected(self):
        # The survivor stopped because teardown terminated it, so its exit was
        # asked for. Applying the one flag to both reported a single crash as
        # ``count`` unexpected exits, which is what the metric is read for.
        assert self._reasons(request_shutdown=False) == [False, True]

    def test_an_asked_for_stop_is_reported_as_expected(self):
        assert self._reasons(request_shutdown=True) == [True, True]

    def test_every_crash_is_counted_when_several_die_together(self):
        seen = []
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=3, poll_interval=0.01,
                                 on_exit=lambda p, expected: seen.append(expected))
        supervisor.start()
        supervisor.processes[0].die(exitcode=1)
        supervisor.processes[1].die(exitcode=1)
        with pytest.raises(SupervisedProcessError):
            supervisor.run()
        # Two exited on their own and only the third was terminated by the
        # teardown: crediting the survivor's exit to the crash would overcount,
        # and crediting the two crashes to the shutdown would lose them.
        assert seen == [False, False, True]


class TestAFailedSpawnLeavesNothingRunning:
    """A child started before the failure must not be left behind.

    It would keep its consumer-group slot and stall the partitions it holds
    until the group next rebalances, with nothing supervising it.
    """

    @staticmethod
    def _failing_spawner(fail_at):
        spawn = Spawner()
        original = spawn.__call__

        def spawning(index):
            if index == fail_at:
                raise RuntimeError("no more processes")
            return original(index)
        return spawn, spawning

    def test_the_earlier_children_are_reaped(self):
        spawn, spawning = self._failing_spawner(fail_at=2)
        supervisor = _supervisor(spawning, count=4, stop_timeout=0.0)

        with pytest.raises(RuntimeError, match="no more processes"):
            supervisor.start()

        assert len(spawn.spawned) == 2
        assert all(p.terminated or p.killed for p in spawn.spawned)

    def test_the_failure_is_not_swallowed(self):
        _, spawning = self._failing_spawner(fail_at=0)
        supervisor = _supervisor(spawning, count=2, stop_timeout=0.0)
        with pytest.raises(RuntimeError):
            supervisor.run()


class TestWatchedProcessesShareTheLifecycle:
    """Processes that belong to the instance without being pipeline slots.

    The API child is one. An instance still consuming but with no health
    endpoint is not healthy, and one left running after the instance is gone
    orphans the port it holds.
    """

    def test_a_watched_exit_stops_the_instance(self):
        spawn = Spawner()
        api = FakeProcess(index=99)
        supervisor = _supervisor(spawn, count=2, watch=[api])
        supervisor.start()
        api.die(exitcode=1)

        with pytest.raises(SupervisedProcessError, match="exited unexpectedly"):
            supervisor._fail_on_any_exit()

    def test_a_live_watched_process_is_left_alone(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1, watch=[FakeProcess(index=99)])
        supervisor.start()
        supervisor._fail_on_any_exit()

    def test_it_is_not_counted_as_a_pipeline_slot(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2, watch=[FakeProcess(index=99)])
        supervisor.start()
        assert len(supervisor.processes) == 2
        assert len(spawn.spawned) == 2

    def test_it_is_never_replaced(self):
        spawn = Spawner()
        api = FakeProcess(index=99)
        supervisor = _supervisor(spawn, count=1, watch=[api])
        supervisor.start()
        api.die(exitcode=1)
        with pytest.raises(SupervisedProcessError):
            supervisor._fail_on_any_exit()
        assert len(spawn.spawned) == 1

    def test_it_is_torn_down_with_the_rest(self):
        api = FakeProcess(index=99)
        supervisor = _supervisor(Spawner(), count=1, watch=[api])
        supervisor.start()
        supervisor.stop()
        assert api.terminated or api.killed

    def test_a_watched_exit_during_shutdown_is_not_a_failure(self):
        api = FakeProcess(index=99)
        supervisor = _supervisor(Spawner(), count=1, watch=[api])
        supervisor.start()
        api.die(exitcode=0)
        supervisor.request_shutdown()
        supervisor._fail_on_any_exit()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestShutdownFollowsTheTimeline:
    """T0 terminate, drain, terminate again, kill, reap.

    A single join budget was tried first and was shorter than the drain a
    child is allowed to take, which made SIGKILL the normal path rather than
    the exception. The phases scale with the configured budget so a caller can
    shorten them without changing their proportions.
    """

    def test_a_child_that_stops_in_time_is_never_killed(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2, stop_timeout=0.2)
        supervisor.start()
        for process in supervisor.processes:
            process.die(exitcode=0)
        supervisor.stop()
        assert not any(p.killed for p in spawn.spawned), "killed a child that had stopped"

    def test_a_child_that_ignores_terminate_is_killed(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1, stop_timeout=0.2)
        supervisor.start()
        supervisor.processes[0].ignore_terminate = True
        supervisor.stop()
        assert spawn.spawned[0].killed, "a child that ignored terminate survived"

    def test_a_clean_stop_does_not_wait_out_the_timeline(self):
        # The phases are deadlines, not delays. Sleeping to each one before
        # checking liveness made every clean shutdown -- and every non-zero
        # exit after a crash -- take most of the budget.
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=3, stop_timeout=20.0)
        supervisor.start()
        started = time.monotonic()
        supervisor.stop()
        assert time.monotonic() - started < 1.0

    def test_a_clean_stop_at_the_default_budget_is_immediate(self):
        # At the default budget, not a scaled-down one. Every existing test
        # here passes stop_timeout=0.2, which is exactly why a 19s
        # unconditional sleep survived the suite.
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=3, stop_timeout=None)
        supervisor._stop_timeout = process_supervisor.DEFAULT_STOP_TIMEOUT
        supervisor.start()
        for process in supervisor.processes:
            process.die(exitcode=0)
        started = time.monotonic()
        supervisor.stop()
        assert time.monotonic() - started < 2.0, "burned the timeline with nothing alive"

    def test_an_empty_stop_at_the_default_budget_is_immediate(self):
        supervisor = _supervisor(Spawner(), count=1, stop_timeout=None)
        supervisor._stop_timeout = process_supervisor.DEFAULT_STOP_TIMEOUT
        started = time.monotonic()
        supervisor.stop()
        assert time.monotonic() - started < 2.0

    def test_a_killed_child_still_gets_time_to_be_collected(self):
        # join(0) does not collect a process that was just killed; it would be
        # left with exitcode None for the exit hook to read.
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1, stop_timeout=0.0)
        supervisor.start()
        supervisor.processes[0].ignore_terminate = True
        supervisor.stop()
        assert max(t for t in spawn.spawned[0].join_timeouts if t is not None) > 0

    def test_the_phases_are_ordered_at_the_default_budget(self):
        from utils import process_supervisor as ps
        assert ps.DRAIN_SECONDS < ps.TERMINATE_AT < ps.KILL_AT < ps.DEADLINE
        assert (ps.DRAIN_SECONDS, ps.TERMINATE_AT, ps.KILL_AT, ps.DEADLINE) == (
            15.0, 18.0, 19.0, 20.0
        )
