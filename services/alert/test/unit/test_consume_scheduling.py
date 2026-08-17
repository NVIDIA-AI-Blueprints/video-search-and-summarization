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

"""Message scheduling: pooled in sync mode, inline in the async modes."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest

from enhance_alert_with_vlm import AnomalyEnhancer


class SchedulerStub:
    """Carries only what the scheduling helpers touch."""

    _schedule_message = AnomalyEnhancer._schedule_message

    def __init__(self, num_workers=2):
        self.config = {"alert_agent": {}}
        self.worker_queue = Queue(maxsize=num_workers)
        self.calls = []

    def process_batch_vlm(self, worker_id, messages, message_type,
                          kafka_consumed_at, kafka_published_at, worker_assigned_at):
        self.calls.append({
            "worker_id": worker_id,
            "messages": messages,
            "message_type": message_type,
            "kafka_consumed_at": kafka_consumed_at,
            "worker_assigned_at": worker_assigned_at,
        })


BATCH = {"kafka_consumed_at": "2026-01-01T00:00:00+00:00",
         "kafka_published_at": "2026-01-01T00:00:00+00:00"}


class TestInlineScheduling:
    """Async modes: no pool, no worker queue, processed on the consume thread."""

    def test_runs_inline_without_a_pool(self):
        stub = SchedulerStub()
        stub._schedule_message(None, {"id": "a"}, "Incident", BATCH)

        assert len(stub.calls) == 1
        assert stub.calls[0]["messages"] == [{"id": "a"}]
        assert stub.calls[0]["message_type"] == "Incident"
        assert stub.calls[0]["kafka_consumed_at"] == BATCH["kafka_consumed_at"]

    def test_does_not_touch_the_worker_queue(self):
        stub = SchedulerStub()
        stub._schedule_message(None, {"id": "a"}, "Behavior", BATCH)
        assert stub.worker_queue.qsize() == 0

    def test_stamps_worker_assigned_at(self):
        stub = SchedulerStub()
        stub._schedule_message(None, {"id": "a"}, "Incident", BATCH)
        assert stub.calls[0]["worker_assigned_at"]

    def test_scheduling_adds_no_error_handling_of_its_own(self):
        stub = SchedulerStub()
        stub.process_batch_vlm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        # The consume loop is protected by process_batch_vlm swallowing its own
        # errors, not by anything here. This pins that: if that ever regresses,
        # an inline schedule takes the consume loop down with it.
        with pytest.raises(RuntimeError):
            stub._schedule_message(None, {"id": "a"}, "Incident", BATCH)


class TestPooledScheduling:
    """Sync mode: a worker slot is taken before submit and returned after."""

    def test_uses_a_worker_slot_and_returns_it(self):
        stub = SchedulerStub(num_workers=2)
        stub.worker_queue.put(0)
        stub.worker_queue.put(1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            stub._schedule_message(pool, {"id": "a"}, "Incident", BATCH)
            pool.shutdown(wait=True)

        assert len(stub.calls) == 1
        assert stub.calls[0]["worker_id"] in (0, 1)
        # Taken for the submit, handed back by the done callback.
        assert stub.worker_queue.qsize() == 2

    def test_every_slot_is_returned_after_a_full_pass(self):
        stub = SchedulerStub(num_workers=2)
        stub.worker_queue.put(0)
        stub.worker_queue.put(1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            for index in range(4):
                stub._schedule_message(pool, {"id": index}, "Incident", BATCH)
            pool.shutdown(wait=True)

        assert len(stub.calls) == 4
        assert stub.worker_queue.qsize() == 2


class TestSeedingFollowsStoreSharing:
    """Seeding once per instance only works when the store is shared.

    With persistence disabled every process owns a private in-memory store, so
    a child that skipped seeding would raise on every prompt lookup for its
    partitions -- nothing falls back to the file behind the store.
    """

    @staticmethod
    def _shared(config):
        from enhance_alert_with_vlm import _alert_config_store_is_shared
        return _alert_config_store_is_shared(config)

    def test_elasticsearch_backed_is_shared(self):
        assert self._shared({"persistence": {"enabled": True}}) is True

    def test_persistence_disabled_is_per_process(self):
        assert self._shared({"persistence": {"enabled": False}}) is False

    @pytest.mark.parametrize("config", [{}, {"persistence": None}, {"persistence": {}}])
    def test_absent_configuration_is_treated_as_shared(self, config):
        # Matches the factory default, which is Elasticsearch-backed.
        assert self._shared(config) is True

    @pytest.mark.parametrize("leader,shared,expected", [
        (True, True, True),      # the one seeder for a shared store
        (False, True, False),    # peers leave the shared store alone
        (True, False, True),
        (False, False, True),    # its own store, so it must seed it itself
    ])
    def test_who_seeds(self, leader, shared, expected):
        config = {"persistence": {"enabled": shared}}
        assert (leader or not self._shared(config)) is expected


class TestWorkerPoolIsNeeded:
    """Sync mode needs the pool; so does pass-through, in every mode."""

    @staticmethod
    def _needs(mode, pass_through):
        stub = type("S", (), {})()
        stub.pipeline_mode = mode
        stub.vst_pass_through_mode = pass_through
        return AnomalyEnhancer._needs_worker_pool(stub)

    def test_sync_mode_needs_it(self):
        assert self._needs("sync", False) is True

    @pytest.mark.parametrize("mode", ["thread_bridge", "event_loop"])
    def test_async_modes_do_not(self, mode):
        assert self._needs(mode, False) is False

    @pytest.mark.parametrize("mode", ["sync", "thread_bridge", "event_loop"])
    def test_pass_through_needs_it_in_every_mode(self, mode):
        # Pass-through makes its VLM calls inline, so with no pool the async
        # modes would process one message at a time on the consume thread.
        assert self._needs(mode, True) is True


class TestRetiredConfigWarnings:
    """Retired keys must warn and be ignored, never fail the boot."""

    @staticmethod
    def _warn_text(caplog, config):
        import logging
        from enhance_alert_with_vlm import AnomalyEnhancer as AE
        stub = type("S", (), {})()
        stub.config = config
        with caplog.at_level(logging.WARNING):
            AE._warn_retired_scaling_config(stub)
        return " ".join(r.getMessage() for r in caplog.records)

    def test_chunk_size_is_reported(self, caplog):
        text = self._warn_text(caplog, {"alert_agent": {"chunk_size": 4}})
        assert "alert_agent.chunk_size" in text

    def test_per_service_switches_are_reported(self, caplog):
        text = self._warn_text(caplog, {"alert_agent": {"async_io": {
            "vst_enabled": True, "elastic_enabled": True}}})
        assert "vst_enabled" in text and "elastic_enabled" in text

    def test_async_io_enabled_is_reported_as_deprecated(self, caplog):
        text = self._warn_text(caplog, {"alert_agent": {"async_io": {"enabled": True}}})
        assert "async_io.enabled" in text and "pipeline_mode" in text

    def test_clean_config_is_silent(self, caplog):
        text = self._warn_text(caplog, {"alert_agent": {"num_workers": 4, "async_io": {
            "external_timeout_seconds": 30}}})
        assert text == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
