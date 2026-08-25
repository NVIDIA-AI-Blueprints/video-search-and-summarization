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

from unittest.mock import patch

import pytest

from enhance_alert_with_vlm import AnomalyEnhancer


class SchedulerStub:
    """Carries only what the scheduling helpers touch."""

    _schedule_message = AnomalyEnhancer._schedule_message

    def __init__(self, num_workers=2):
        from utils.partition_in_flight import PartitionInFlight
        self._partition_in_flight = PartitionInFlight()
        self.config = {"alert_agent": {}}
        self.worker_queue = Queue(maxsize=num_workers)
        self.calls = []

    def process_batch_vlm(self, worker_id, messages, message_type,
                          kafka_consumed_at, kafka_published_at, worker_assigned_at,
                          admission=None):
        self.calls.append({
            "admission": admission,
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


class TestMultiProcessRequiresEventLoop:
    """The other modes hold their concurrency in threads.

    Several processes then multiply the load offered to the VLM and VST
    backends by the process count, without the per-process caps that event
    loop mode applies to bound it.
    """

    @staticmethod
    def _mode(config):
        from enhance_alert_with_vlm import pipeline_mode_from_config
        return pipeline_mode_from_config(config)

    def test_reads_the_top_level_spelling(self):
        assert self._mode({"alert_agent": {"pipeline_mode": "event_loop"}}) == "event_loop"

    def test_reads_the_nested_spelling(self):
        assert self._mode({"alert_agent": {"async_io": {
            "pipeline_mode": "event_loop"}}}) == "event_loop"

    def test_conflicting_spellings_fail_startup(self):
        with pytest.raises(ValueError, match="Conflicting"):
            self._mode({"alert_agent": {"pipeline_mode": "sync",
                                        "async_io": {"pipeline_mode": "event_loop"}}})

    def test_the_legacy_flag_still_decides_when_unset(self):
        assert self._mode({"alert_agent": {"async_io": {"enabled": True}}}) == "thread_bridge"
        assert self._mode({"alert_agent": {}}) == "sync"

    def test_an_invalid_mode_fails_startup(self):
        with pytest.raises(ValueError, match="Invalid pipeline_mode"):
            self._mode({"alert_agent": {"pipeline_mode": "turbo"}})


class TestSeedingFollowsStoreSharing:
    """Seeding ahead of a process only works when the store is shared.

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

    @pytest.mark.parametrize("seed_shared,shared,expected", [
        (True, True, True),      # single process: nobody seeded ahead of it
        (False, True, False),    # a supervisor already wrote the shared store
        (True, False, True),
        (False, False, True),    # private store, so it must write its own
    ])
    def test_who_seeds(self, seed_shared, shared, expected):
        config = {"persistence": {"enabled": shared}}
        assert (seed_shared or not self._shared(config)) is expected


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


class TestQueuedWorkIsCountedBeforeItRuns:
    """A queued record is already this instance's responsibility.

    Admission used to be taken where the work finally ran, which for the
    pooled paths is on the worker thread. A rebalance could therefore drain
    successfully while records sat in the pool queue, and they would start
    against a partition another member had already taken over.
    """

    def test_scheduling_counts_before_the_pool_runs_anything(self):
        stub = SchedulerStub(num_workers=2)
        stub.worker_queue.put(0)
        stub.worker_queue.put(1)
        batch = dict(BATCH, topic="mdx-incidents", partition=3)

        # A pool that never runs anything: the count must exist regardless.
        class Idle:
            def submit(self, fn, *a, **k):
                from concurrent.futures import Future
                return Future()

        stub._schedule_message(Idle(), {"id": "a"}, "Incident", batch)
        assert stub._partition_in_flight.in_flight(("mdx-incidents", 3)) == 1

    def test_a_drain_will_not_finish_while_work_is_queued(self):
        stub = SchedulerStub(num_workers=1)
        stub.worker_queue.put(0)
        batch = dict(BATCH, topic="mdx-incidents", partition=3)

        class Idle:
            def submit(self, fn, *a, **k):
                from concurrent.futures import Future
                return Future()

        stub._schedule_message(Idle(), {"id": "a"}, "Incident", batch)
        assert stub._partition_in_flight.drain([("mdx-incidents", 3)], timeout=0.2) is False

    def test_a_source_without_partitions_is_not_counted(self):
        stub = SchedulerStub()
        stub._schedule_message(None, {"id": "a"}, "Incident", BATCH)
        assert stub._partition_in_flight.total() == 0


class TestProcessLocalStorageRejectsMultipleProcesses:
    """A store private to each process cannot be initialised by a supervisor.

    With persistence disabled every process builds its own copy, so the parent
    would seed one nobody reads and each worker another, and they drift. The
    accepted topology is parent-owned initialisation, so the configuration is
    refused rather than silently run as N independent stores.
    """

    @staticmethod
    def _shared(enabled):
        from enhance_alert_with_vlm import _alert_config_store_is_shared
        return _alert_config_store_is_shared({"persistence": {"enabled": enabled}})

    def test_elasticsearch_backed_storage_can_be_shared(self):
        assert self._shared(True) is True

    def test_persistence_disabled_storage_cannot(self):
        assert self._shared(False) is False

    @staticmethod
    def _validate(shared, mode="event_loop", processes=4, source_type=None,
                  max_poll_interval_ms=None):
        from enhance_alert_with_vlm import validate_multi_process_config
        config = {"persistence": {"enabled": shared},
                  "alert_agent": {"pipeline_mode": mode}}
        if source_type is not None:
            config["event_bridge"] = {"sourceType": source_type}
        if max_poll_interval_ms is not None:
            config["kafka"] = {"max_poll_interval_ms": max_poll_interval_ms}
        validate_multi_process_config(config, processes)

    def test_a_shared_store_is_accepted(self):
        self._validate(shared=True)

    def test_a_private_store_is_refused(self):
        with pytest.raises(ValueError, match="persistence.enabled is false"):
            self._validate(shared=False)

    def test_the_refusal_names_the_way_out(self):
        with pytest.raises(ValueError, match="Enable persistence, or run a single process"):
            self._validate(shared=False)

    def test_a_cooperative_assignor_is_refused_in_the_parent(self):
        # Judged before any resource starts. Reached only where the consumer
        # is built, it fired inside a child -- after the API, the metrics
        # port, the metadata wait, seeding, warmup and the fork.
        from mdx.kafka_message_broker import _require_eager_assignor
        with pytest.raises(ValueError, match="cooperative-sticky"):
            _require_eager_assignor("cooperative-sticky")

    def test_metrics_being_off_does_not_refuse_multiple_processes(self):
        # An observability switch is not a precondition for serving. The
        # requirement was invented here to work around health reading fleet
        # state through the metric shards; that state now travels on its own.
        from enhance_alert_with_vlm import validate_multi_process_config
        with patch("enhance_alert_with_vlm.PROMETHEUS_ENABLED", False):
            validate_multi_process_config(
                {"persistence": {"enabled": True},
                 "alert_agent": {"pipeline_mode": "event_loop"}}, 4,
            )

    def test_the_mode_is_checked_too(self):
        with pytest.raises(ValueError, match="requires pipeline_mode"):
            self._validate(shared=True, mode="sync")

    def test_a_non_kafka_source_is_refused(self):
        # Otherwise the partition wait spends its whole budget and then fails
        # with a message about Kafka topics the deployment does not have.
        with pytest.raises(ValueError, match="sourceType='kafka'"):
            self._validate(shared=True, source_type="redis_stream")

    def test_an_absent_source_type_still_means_kafka(self):
        self._validate(shared=True, source_type=None)


class TestTheDrainMustFitThePollInterval:
    """Checked for every deployment, not only multi-process ones.

    The revoke hook that runs the drain is installed in the constructor for
    every deployment, so a single-process instance evicts itself on the same
    configuration a multi-process one refuses to start on. The check lived
    inside the multi-process validation and so never ran at one process.
    """

    @staticmethod
    def _validate(max_poll_interval_ms):
        from enhance_alert_with_vlm import validate_drain_fits_poll_interval
        validate_drain_fits_poll_interval(
            {"kafka": {"max_poll_interval_ms": max_poll_interval_ms}}
        )

    def test_a_poll_interval_shorter_than_the_drain_is_refused(self):
        with pytest.raises(ValueError, match="max_poll_interval_ms"):
            self._validate(10000)

    def test_the_drain_bound_itself_is_refused(self):
        # Equal is not enough: the drain would consume the whole interval.
        from utils.partition_in_flight import DEFAULT_DRAIN_TIMEOUT
        with pytest.raises(ValueError, match="max_poll_interval_ms"):
            self._validate(DEFAULT_DRAIN_TIMEOUT * 1000)

    def test_the_default_poll_interval_leaves_room(self):
        self._validate(60000)

    def test_an_absent_setting_is_accepted(self):
        from enhance_alert_with_vlm import validate_drain_fits_poll_interval
        validate_drain_fits_poll_interval({})

    def test_a_numeric_string_is_accepted(self):
        # Rendered configs substitute environment variables textually.
        self._validate("60000")

    def test_an_unsubstituted_template_is_named(self):
        # Otherwise a bare float() conversion error reaches the entry point's
        # catch-all and is logged as an unexpected error.
        with pytest.raises(ValueError, match="must be a number"):
            self._validate("${AB_MAX_POLL_INTERVAL_MS}")
