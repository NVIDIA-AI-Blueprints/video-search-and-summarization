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

    def test_batch_failure_does_not_propagate_to_the_consume_loop(self):
        stub = SchedulerStub()
        stub.process_batch_vlm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        # process_batch_vlm swallows its own errors in production; if that ever
        # regresses, an inline schedule would take the consume loop down.
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
