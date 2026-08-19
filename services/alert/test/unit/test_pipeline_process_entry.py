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

import pytest

import enhance_alert_with_vlm as entry


@pytest.fixture
def built():
    """Capture the instance_leader each child would construct itself with."""
    seen = []

    def fake_enhancer(config_path, instance_leader=True, seed_shared_store=True):
        seen.append(instance_leader)
        return MagicMock()

    with patch.object(entry, "AnomalyEnhancer", side_effect=fake_enhancer), \
         patch.object(entry, "_exit_when_parent_dies"), \
         patch.object(entry, "_log_instance_concurrency"):
        yield seen


@pytest.fixture
def enhancer():
    """A child whose every startup step records itself in call order."""
    built = MagicMock()
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
        enhancer.process_anomalies = order.process_anomalies
        ready = MagicMock()
        ready.set = order.set

        entry._run_pipeline_process("config.yaml", 0, os.getpid(), 1, ready)

        assert order.mock_calls == [call.await_ready(), call.set(),
                                    call.process_anomalies()]

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
