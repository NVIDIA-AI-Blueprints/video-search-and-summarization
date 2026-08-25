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

"""The startup budget is enforced once, for whatever is running.

Threading the remaining budget into each step was tried first and left a hole
every round: the metadata read got it, then warmup was found outside it, then
the cap written for warmup went to a key nothing read. Two steps cannot take a
timeout at all -- the prompt store and the single-process pipeline are built by
constructors that reach Elasticsearch with no parameter to bound them.
"""

import threading
import time

import pytest

from utils.startup_deadline import StartupDeadline


class TestTheBudgetIsHeldWhateverIsRunning:
    def test_a_step_that_takes_no_timeout_is_still_bounded(self):
        # The whole point: this stands in for PromptManager and the enhancer
        # constructor, neither of which accepts a deadline.
        expired = []
        with StartupDeadline(0.1, on_expiry=expired.append) as deadline:
            deadline.step("seeding the prompt store")
            time.sleep(0.4)  # a constructor blocked on Elasticsearch

        assert expired == ["seeding the prompt store"]

    def test_startup_that_finishes_in_time_is_left_alone(self):
        expired = []
        with StartupDeadline(0.5, on_expiry=expired.append) as deadline:
            deadline.step("reading source topic metadata")

        time.sleep(0.7)
        assert expired == []

    def test_the_expiry_names_the_step_that_was_running(self):
        # "startup timed out" sends an operator to read the whole log;
        # "timed out warming up the VLM" sends them to the backend.
        expired = []
        with StartupDeadline(0.1, on_expiry=expired.append) as deadline:
            deadline.step("reading source topic metadata")
            deadline.step("warming up the VLM")
            time.sleep(0.3)

        assert expired == ["warming up the VLM"]

    def test_leaving_the_block_stops_the_watchdog(self):
        # Readiness ends the startup phase; a timer still counting would
        # signal a process that is serving traffic.
        expired = []
        deadline = StartupDeadline(0.2, on_expiry=expired.append)
        with deadline:
            deadline.step("running")
        time.sleep(0.4)
        assert expired == []

    def test_the_step_is_readable_from_another_thread(self):
        # The watchdog reads it from its own thread while startup writes it.
        deadline = StartupDeadline(10.0, on_expiry=lambda step: None)
        seen = []

        def reader():
            for _ in range(50):
                seen.append(deadline.current_step)
                time.sleep(0.001)

        watcher = threading.Thread(target=reader)
        watcher.start()
        for name in ("one", "two", "three"):
            deadline.step(name)
            time.sleep(0.01)
        watcher.join()

        assert "three" in seen
        assert all(isinstance(step, str) for step in seen)


class TestAGrantedExtensionIsHonoured:
    """A floor the watchdog never hears about is not a floor.

    The fleet join is granted a reserved window when the steps before it
    overran. Without telling the watchdog, it still fired at the original
    budget and took the window back seconds later -- the grant was logged and
    then revoked, which is the same shape as a cap written where nothing
    reads it.
    """

    def test_an_extension_pushes_the_deadline_out(self):
        expired = []
        with StartupDeadline(0.15, on_expiry=expired.append) as deadline:
            deadline.step("waiting for the pipeline processes to join")
            deadline.extend(0.4)
            time.sleep(0.3)
            assert expired == [], "fired inside the window it granted"
            time.sleep(0.35)

        assert expired == ["waiting for the pipeline processes to join"]

    def test_an_extension_after_expiry_is_ignored(self):
        expired = []
        deadline = StartupDeadline(0.05, on_expiry=expired.append)
        with deadline:
            time.sleep(0.25)
            deadline.extend(5.0)
        assert expired == ["starting"]

    def test_expiry_happens_once(self):
        expired = []
        with StartupDeadline(0.05, on_expiry=expired.append):
            time.sleep(0.4)
        assert len(expired) == 1


@pytest.fixture(autouse=True)
def _clean_expiry_record():
    """Reset the module global around every test in this file.

    One test here called ``_terminate`` and left the reason set, so a later
    test could observe a timeout that never happened -- depending on file
    order. That is the ambient-state dependence this suite has already been
    bitten by once.
    """
    import utils.startup_deadline as module
    module._expired_step = None
    yield
    module._expired_step = None


class TestTheDefaultExpiryIsAnOrderlyShutdown:
    def test_the_reason_is_recorded_so_the_exit_can_be_non_zero(self):
        # The entry point's handler exits 0. Without the recorded reason a
        # startup that ran out of budget is indistinguishable from an operator
        # stopping the container -- a failure reported as a success.
        from unittest.mock import patch
        import utils.startup_deadline as module

        with patch.object(module.os, "kill"):
            StartupDeadline._terminate("seeding the prompt store")

        assert module.expired_step() == "seeding the prompt store"

    def test_nothing_is_recorded_when_startup_finishes(self):
        import utils.startup_deadline as module
        assert module.expired_step() is None

    def test_it_signals_rather_than_exiting_outright(self):
        # os._exit would leave a pipeline child holding its consumer-group
        # slot, which is the thing the supervisor exists to prevent. SIGTERM
        # goes through the entry point's own teardown.
        from unittest.mock import patch
        import utils.startup_deadline as module

        with patch.object(module.os, "kill") as kill:
            StartupDeadline._terminate("seeding the prompt store")

        assert kill.call_args.args[1] == module.signal.SIGTERM


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestTheExitCodeTellsFailureFromRequest:
    """Both arrive as SIGTERM; only the recorded reason separates them.

    This is the line the whole non-zero-exit fix exists for, and it shipped a
    round with no test at all while the conformance table claimed otherwise.
    """

    @staticmethod
    def _code(expired):
        import enhance_alert_with_vlm as entry
        import utils.startup_deadline as module

        module._expired_step = expired
        return entry.shutdown_exit_code()

    def test_an_operator_shutdown_exits_zero(self):
        assert self._code(None) == 0

    def test_a_startup_timeout_exits_one(self):
        assert self._code("seeding the prompt store") == 1

    def test_the_failing_step_is_logged(self):
        from unittest.mock import patch
        import enhance_alert_with_vlm as entry
        import utils.startup_deadline as module

        module._expired_step = "warming up the VLM"
        with patch.object(entry.logger, "error") as error:
            entry.shutdown_exit_code()

        assert any("warming up the VLM" in str(call.args) for call in error.call_args_list)


class TestTheHandlerDisarmsTheWatchdogFirst:
    def test_on_shutdown_runs_before_anything_is_torn_down(self):
        # A teardown can outlast the startup budget; a watchdog firing inside
        # the handler re-enters it and abandons the escalation half-done.
        from unittest.mock import MagicMock, patch
        import enhance_alert_with_vlm as entry

        order = []
        child = MagicMock()
        child.is_alive.return_value = True
        child.terminate.side_effect = lambda: order.append("api")
        supervisor = MagicMock()
        supervisor.request_shutdown.side_effect = lambda: order.append("supervisor")

        with patch.object(entry.signal, "signal"), \
             patch.object(entry, "_pipeline_supervisor", supervisor), \
             patch.object(entry.sys, "exit", side_effect=SystemExit):
            entry.setup_signal_handlers(child, on_shutdown=lambda: order.append("disarmed"))
            handler = entry.signal.signal.call_args_list[0].args[1]
            try:
                handler(entry.signal.SIGTERM, None)
            except SystemExit:
                pass

        # Everything the handler touches records into one list, so moving the
        # callback later fails this. Asserting only that it ran could not.
        assert order[0] == "disarmed", f"disarmed late: {order}"
        assert "supervisor" in order and "api" in order, order


class TestTheWatchdogOutlastsTheWindowsItGrants:
    """Widening a window without telling the watchdog widens nothing.

    The parent's announce window and the child's floored join both run past
    the plain remainder of the budget. The watchdog was extended only when the
    budget had already run short, so in the ordinary case it fired exactly one
    reserved share before the window it had been widened for -- the readiness
    timeout could never run, and its per-child diagnostic was unreachable.
    Nothing pinned the relationship, which is why the fix could be inert and
    the suite stay green.
    """

    def test_an_extension_covers_a_window_widened_after_it(self):
        budget, extra = 0.30, 0.30
        expired = []
        with StartupDeadline(budget, on_expiry=expired.append) as deadline:
            deadline.step("waiting for the pipeline processes to join")
            deadline.extend(extra)
            # The widened window: the watchdog must still be alive here.
            time.sleep(budget + 0.1)
            assert expired == [], "fired inside the window it had been widened for"
            time.sleep(extra)

        assert expired == ["waiting for the pipeline processes to join"]

    def test_startup_stays_inside_its_configured_budget(self):
        # Padding the announce window past the remainder pushed the whole of
        # startup beyond the deadline it is measured against. The budget is
        # the contract; a budget too small for the work is refused up front.
        import enhance_alert_with_vlm as entry

        for remaining in (1.0, 15.0, 45.0):
            announce, extension = entry.readiness_windows(remaining)
            assert announce <= remaining, (
                f"announce window {announce}s exceeds the {remaining}s left"
            )
            assert extension <= entry.WATCHDOG_MARGIN_SECONDS

    def test_the_watchdog_ends_after_the_announce_window(self):
        # The arithmetic, not the source text. A grep between two markers
        # could not see a re-added condition, nor a change to the other side
        # of the relationship it claimed to pin.
        import enhance_alert_with_vlm as entry

        for fleet_wait in (0.0, 1.0, 15.0, 40.0, 600.0):
            announce, extension = entry.readiness_windows(fleet_wait)
            # Both clocks start together at the fleet step: the announce
            # thread runs for `announce`, the watchdog for what remains of the
            # budget (>= fleet_wait) plus `extension`.
            watchdog_end = fleet_wait + extension
            assert watchdog_end > announce, (
                f"watchdog ends {announce - watchdog_end:.3f}s before the "
                f"window it must cover, at fleet_wait={fleet_wait}"
            )


class TestAReadinessTimeoutDisarmsTheWatchdogFirst:
    """The teardown a readiness timeout starts runs the shutdown timeline.

    A watchdog firing partway through it exits the process from the signal
    handler, abandoning terminate/kill/reap -- the children would then die
    only by PR_SET_PDEATHSIG, with no exit accounting and no metric shards
    retired.
    """

    def test_the_disarm_runs_before_the_shutdown_is_requested(self):
        from unittest.mock import MagicMock, patch
        import enhance_alert_with_vlm as entry

        order = []
        supervisor = MagicMock()
        supervisor.request_shutdown.side_effect = lambda: order.append("shutdown")
        captured = {}

        with patch.object(entry, "ProcessSupervisor", return_value=supervisor), \
             patch.object(entry, "_start_pipeline_process"), \
             patch.object(entry, "_pipeline_mp_context"), \
             patch.object(entry, "_announce_when_all_ready",
                          side_effect=lambda *a, **k: captured.update(k)):
            entry.run_multi_process_pipeline(
                "config.yaml", 2, on_ready=lambda: None,
                readiness_timeout=1.0,
                disarm_watchdog=lambda: order.append("disarmed"),
            )

        captured["on_timeout"]()
        assert order[0] == "disarmed", f"the watchdog was still armed: {order}"
