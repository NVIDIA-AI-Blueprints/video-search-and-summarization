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

    def is_alive(self):
        return self._alive

    def die(self, exitcode=1):
        self._alive = False
        self.exitcode = exitcode

    def join(self, timeout=None):
        self.joined = True

    def terminate(self):
        self.terminated = True
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
    kwargs.setdefault("restart_backoff", 0.0)
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
        supervisor = _supervisor(Spawner(), count=2, on_exit=seen.append)
        supervisor.start()
        supervisor.stop()
        assert len(seen) == 2

    def test_count_must_be_positive(self):
        with pytest.raises(ValueError):
            ProcessSupervisor(count=0, spawn=Spawner())


class TestRestart:
    def test_dead_child_is_replaced_in_place(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2)
        supervisor.start()
        supervisor.processes[1].die(exitcode=9)

        supervisor._reap_and_restart()

        assert len(spawn.spawned) == 3
        assert supervisor.processes[1] is spawn.spawned[2]
        assert supervisor.processes[1].index == 1
        assert supervisor.processes[0] is spawn.spawned[0]

    def test_live_children_are_left_alone(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=2)
        supervisor.start()
        supervisor._reap_and_restart()
        assert len(spawn.spawned) == 2

    def test_dead_child_is_reported_to_the_exit_hook_before_replacement(self):
        seen = []
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1, on_exit=seen.append)
        supervisor.start()
        original = supervisor.processes[0]
        original.die()

        supervisor._reap_and_restart()

        assert seen == [original]

    def test_no_restart_once_shutdown_requested(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1)
        supervisor.start()
        supervisor.processes[0].die(exitcode=0)
        supervisor.request_shutdown()

        supervisor._reap_and_restart()

        assert len(spawn.spawned) == 1

    def test_gives_up_after_repeated_immediate_failures(self):
        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1, rapid_restart_window=60.0, max_rapid_restarts=2)
        supervisor.start()

        with pytest.raises(SupervisedProcessError):
            for _ in range(4):
                supervisor.processes[0].die()
                supervisor._reap_and_restart()

    def test_healthy_uptime_resets_the_failure_counter(self, monkeypatch):
        import utils.process_supervisor as module

        clock = {"now": 0.0}
        monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])

        spawn = Spawner()
        supervisor = _supervisor(spawn, count=1, rapid_restart_window=60.0, max_rapid_restarts=1)
        supervisor.start()

        for _ in range(4):
            clock["now"] += 3600.0
            supervisor.processes[0].die()
            supervisor._reap_and_restart()

        assert len(spawn.spawned) == 5


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

    def test_killed_child_is_replaced_and_all_children_are_reaped(self):
        supervisor = ProcessSupervisor(
            count=2,
            spawn=self._spawn,
            poll_interval=0.05,
            restart_backoff=0.0,
            stop_timeout=5.0,
        )
        supervisor.start()
        try:
            original = supervisor.processes[0]
            os.kill(original.pid, signal.SIGKILL)
            deadline = time.monotonic() + 10
            while original.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)

            supervisor._reap_and_restart()

            replacement = supervisor.processes[0]
            assert replacement.pid != original.pid
            assert replacement.is_alive()
            pids = [p.pid for p in supervisor.processes]
        finally:
            supervisor.stop()

        for pid in pids:
            with pytest.raises(OSError):
                os.kill(pid, 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
