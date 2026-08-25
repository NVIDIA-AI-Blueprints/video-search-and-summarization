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

"""Waiting for the consumer group join before anything announces readiness.

subscribe() only starts the join; it finishes on a later poll. A producer that
starts in that window writes past a `latest` offset no member has reached, and
those records are never delivered.
"""

import pytest

from mdx.kafka_message_broker import KafkaMessageBroker

CONFIG = {"kafka": {"bootstrap_servers": "broker:9092", "poll_timeout": 100,
                    "max_poll_records": 10, "enable_auto_commit": False}}


class FakeMessage:
    def __init__(self, value=b"m", topic="t", partition=0, err=None):
        self._value, self._topic, self._partition, self._err = value, topic, partition, err

    def error(self):
        return self._err

    def value(self):
        return self._value

    def key(self):
        return None

    def topic(self):
        return self._topic

    def partition(self):
        return self._partition

    def timestamp(self):
        return (1, 1700000000000)


class FakePartition:
    def __init__(self, topic, partition):
        self.topic, self.partition = topic, partition


class FakeConsumer:
    """Delivers its assignment after ``assign_after`` polls, via the callback."""

    def __init__(self, assign_after=2, messages=None, partitions=(0, 1)):
        self.assign_after = assign_after
        self.polls = 0
        self._queued = list(messages or [])
        self._partitions = partitions
        self.committed = []
        self.assigned_calls = []
        self._on_assign = None
        self._on_revoke = None

    def subscribe(self, topics, on_assign=None, on_revoke=None, on_lost=None):
        self._on_assign, self._on_revoke = on_assign, on_revoke

    def poll(self, timeout=None):
        self.polls += 1
        if self.polls == self.assign_after and self._on_assign is not None:
            self._on_assign(self, [FakePartition("t", p) for p in self._partitions])
        if self.polls > self.assign_after and self._queued:
            return self._queued.pop(0)
        return None

    def assign(self, partitions):
        self.assigned_calls.append(list(partitions))

    def revoke(self):
        self._on_revoke(self, [FakePartition("t", p) for p in self._partitions])

    def commit(self, msg):
        self.committed.append(msg)


def wire(broker, consumer, on_revoke=None):
    """Attach the broker's rebalance hooks to a fake consumer."""
    broker._subscribe_with_rebalance_hooks(consumer, "t", on_revoke)
    return consumer


@pytest.fixture
def broker():
    return KafkaMessageBroker(CONFIG)


class TestAwaitAssignment:
    def test_polls_until_the_assignment_lands(self, broker):
        consumer = wire(broker, FakeConsumer(assign_after=3))
        assert broker.await_assignment(consumer, timeout=5) is True
        assert consumer.polls == 3

    def test_returns_immediately_once_decided(self, broker):
        consumer = wire(broker, FakeConsumer(assign_after=1))
        broker.await_assignment(consumer, timeout=5)
        polls = consumer.polls
        assert broker.await_assignment(consumer, timeout=5) is True
        assert consumer.polls == polls        # no further polling

    def test_reports_failure_rather_than_blocking_forever(self, broker):
        consumer = wire(broker, FakeConsumer(assign_after=10**9))
        assert broker.await_assignment(consumer, timeout=0.3) is False

    def test_a_member_assigned_nothing_is_still_decided(self, broker):
        # With more group members than partitions on a topic, a member owns
        # none of it. Requiring a non-empty assignment would hang on it.
        consumer = wire(broker, FakeConsumer(assign_after=1, partitions=()))
        assert broker.await_assignment(consumer, timeout=5) is True
        assert broker.owned_partitions(consumer) == set()

    def test_the_assignment_is_applied_explicitly(self, broker):
        # The client's behaviour when a rebalance callback does not assign
        # differs between protocols; this must not depend on it.
        consumer = wire(broker, FakeConsumer(assign_after=1))
        broker.await_assignment(consumer, timeout=5)
        assert consumer.assigned_calls


class TestJoiningLaterDoesNotStarveTheEarlierConsumers:
    """One consumer per topic, all in one group, all rebalanced together.

    Subscribing the second consumer forces a rebalance that only completes
    once every member polls. Waiting on the newcomer alone leaves the first
    consumer unpolled, the rebalance never finishes, and the wait deadlocks
    the very thing it is waiting for -- which is what took startup down on any
    deployment with two source topics.
    """

    @staticmethod
    def _pair(broker):
        """B is assigned only while A keeps being polled, as a group behaves."""
        a = wire(broker, FakeConsumer(assign_after=1), )
        b = FakeConsumer(assign_after=10**9)

        polls_at_subscribe = {}

        def b_assign_when_a_is_polled(_c, _p):
            pass

        broker._subscribe_with_rebalance_hooks(b, "t2", None)
        polls_at_subscribe["a"] = a.polls
        original_poll = b.poll

        def gated_poll(timeout=None):
            b.polls += 1
            # The coordinator can only complete the rebalance once the other
            # member has polled since this one joined.
            if a.polls > polls_at_subscribe["a"] and b._on_assign is not None:
                b._on_assign(b, [FakePartition("t2", 0)])
                b._on_assign = None
            return None

        b.poll = gated_poll
        return a, b

    def test_waiting_on_the_newcomer_alone_never_completes(self, broker):
        a, b = self._pair(broker)
        assert broker.await_assignment(b, timeout=0.4) is False

    def test_polling_every_consumer_completes_the_rebalance(self, broker):
        a, b = self._pair(broker)
        assert broker.await_assignments([a, b], timeout=5) is True
        assert broker.assignment_decided(b)

    def test_an_already_assigned_consumer_is_still_polled(self, broker):
        a, b = self._pair(broker)
        before = a.polls
        broker.await_assignments([a, b], timeout=5)
        assert a.polls > before, "the assigned consumer stopped being polled"


class TestAssignmentIsLiveState:
    """Readiness has to be able to go false again.

    A latch that can only be set reports an instance as ready after its
    partitions have moved elsewhere.
    """

    def test_owned_partitions_are_tracked(self, broker):
        consumer = wire(broker, FakeConsumer(assign_after=1, partitions=(0, 3)))
        broker.await_assignment(consumer, timeout=5)
        assert broker.owned_partitions(consumer) == {("t", 0), ("t", 3)}

    def test_a_revoke_empties_what_is_owned(self, broker):
        consumer = wire(broker, FakeConsumer(assign_after=1))
        broker.await_assignment(consumer, timeout=5)
        consumer.revoke()
        assert broker.owned_partitions(consumer) == set()

    def test_a_revoke_drops_what_was_buffered_for_that_consumer(self):
        # Buffered records were never committed, so the incoming owner reads
        # them again. Keeping them would give this member work on partitions
        # it has just lost.
        from mdx.kafka_message_broker import KafkaMessageBroker
        broker = KafkaMessageBroker(CONFIG)
        consumer = wire(broker, FakeConsumer(assign_after=2, messages=[FakeMessage()]))
        broker.await_assignment(consumer, timeout=5)
        broker._prefetched[id(consumer)] = [FakeMessage()]

        consumer.revoke()

        assert broker._prefetched.get(id(consumer)) in (None, [])

    def test_a_revoke_makes_the_assignment_undecided_again(self):
        # Readiness follows this. Leaving it decided made it a latch that
        # could only ever be set, which is what let a worker report itself
        # ready after its partitions had moved elsewhere.
        from mdx.kafka_message_broker import KafkaMessageBroker
        broker = KafkaMessageBroker(CONFIG)
        consumer = wire(broker, FakeConsumer(assign_after=1))
        broker.await_assignment(consumer, timeout=5)
        assert broker.assignment_decided(consumer)
        consumer.revoke()
        assert not broker.assignment_decided(consumer)

    def test_a_revoke_hands_the_losing_partitions_to_the_hook(self, broker):
        seen = []
        consumer = wire(broker, FakeConsumer(assign_after=1, partitions=(2, 5)),
                        on_revoke=seen.append)
        broker.await_assignment(consumer, timeout=5)
        consumer.revoke()
        assert seen == [{("t", 2), ("t", 5)}]


class TestWaitingDoesNotDropMessages:
    """Kafka only delivers to an assigned member, so anything the wait poll
    returns is real traffic that has already moved the offset."""

    def test_a_message_seen_while_waiting_reaches_the_caller(self, broker):
        wanted = FakeMessage(b"during-assign")
        consumer = wire(broker, FakeConsumer(assign_after=2, messages=[wanted]))
        broker.await_assignment(consumer, timeout=5)
        broker._prefetched[id(consumer)] = [wanted]

        batch = broker.get_consumed_messages(consumer)

        values = [value for msgs in batch.values() for _, value, _ in msgs]
        assert b"during-assign" in values

    def test_prefetched_messages_come_before_freshly_polled_ones(self, broker):
        first, second = FakeMessage(b"first"), FakeMessage(b"second")
        consumer = FakeConsumer(assign_after=0, messages=[second])
        broker._prefetched[id(consumer)] = [first]

        batch = broker.get_consumed_messages(consumer)

        values = [value for msgs in batch.values() for _, value, _ in msgs]
        assert values[:2] == [b"first", b"second"]

    def test_an_overflowing_prefetch_is_kept_for_the_next_batch(self, broker):
        held = [FakeMessage(f"m{i}".encode()) for i in range(4)]
        consumer = FakeConsumer(assign_after=0)
        broker._prefetched[id(consumer)] = list(held)

        first = broker.get_consumed_messages(consumer, batch_size=2)
        second = broker.get_consumed_messages(consumer, batch_size=2)

        seen = [v for batch in (first, second) for msgs in batch.values() for _, v, _ in msgs]
        assert seen == [b"m0", b"m1", b"m2", b"m3"]

    def test_the_buffer_is_emptied_once_drained(self, broker):
        consumer = FakeConsumer(assign_after=0)
        broker._prefetched[id(consumer)] = [FakeMessage()]
        broker.get_consumed_messages(consumer)
        assert broker._prefetched.get(id(consumer)) in (None, [])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class _FakeArray:
    """Stands in for the shared array without needing a real process."""

    def __init__(self, values):
        self._values = list(values)

    def __getitem__(self, index):
        return self._values[index]

    def get_lock(self):
        import contextlib
        return contextlib.nullcontext()


class TestHealthReflectsLiveWorkers:
    """A startup that succeeded is not the same as an instance serving.

    A rebalance can leave a running worker holding nothing, and reporting ok
    while part of the instance serves no partition hides exactly what this is
    meant to surface.
    """

    @staticmethod
    def _degraded(values, multiproc=True):
        import sys
        from unittest.mock import MagicMock, patch
        sys.modules.pop("web.main", None)
        from web import main

        samples = [MagicMock(name=n, value=v) for n, v in values.items()]
        for sample, name in zip(samples, values):
            sample.name = name
        metric = MagicMock(samples=samples)

        # The reader caches for a second; each case is a fresh read.
        main._DEGRADED_CACHE.update(at=0.0, value=None)
        env = {"PROMETHEUS_MULTIPROC_DIR": "/tmp/x"} if multiproc else {}
        with patch.dict("os.environ", env, clear=not multiproc), \
             patch("prometheus_client.CollectorRegistry") as reg, \
             patch("prometheus_client.multiprocess.MultiProcessCollector"):
            reg.return_value.collect.return_value = [metric]
            return main._degraded_workers()

    def test_a_full_fleet_is_silent(self):
        assert self._degraded({
            "alert_bridge_pipeline_processes_configured": 4.0,
            "alert_bridge_pipeline_processes_ready": 4.0,
        }) is None

    def test_a_worker_without_an_assignment_is_reported(self):
        message = self._degraded({
            "alert_bridge_pipeline_processes_configured": 4.0,
            "alert_bridge_pipeline_processes_ready": 3.0,
        })
        assert message and "3 of 4" in message

    def test_health_reads_the_shared_array_without_metrics(self):
        # The point of the change: an observability switch must not decide
        # whether this endpoint can tell a dead fleet from a whole one.
        import sys
        from unittest.mock import patch
        sys.modules.pop("web.main", None)
        from web import main
        from utils import fleet_state

        main._DEGRADED_CACHE.update(at=0.0, value=None)
        with patch.dict("os.environ", {}, clear=True):
            fleet_state.attach(_FakeArray([4, 4, 1]))
            try:
                message = main._degraded_workers()
            finally:
                fleet_state.attach(None)

        assert message and "1 of 4" in message

    def test_the_array_reports_a_whole_fleet_as_healthy(self):
        import sys
        from unittest.mock import patch
        sys.modules.pop("web.main", None)
        from web import main
        from utils import fleet_state

        main._DEGRADED_CACHE.update(at=0.0, value=None)
        with patch.dict("os.environ", {}, clear=True):
            fleet_state.attach(_FakeArray([4, 4, 4]))
            try:
                assert main._degraded_workers() is None
            finally:
                fleet_state.attach(None)

    def test_a_dead_worker_is_reported_before_an_unassigned_one(self):
        import sys
        from unittest.mock import patch
        sys.modules.pop("web.main", None)
        from web import main
        from utils import fleet_state

        main._DEGRADED_CACHE.update(at=0.0, value=None)
        with patch.dict("os.environ", {}, clear=True):
            fleet_state.attach(_FakeArray([4, 3, 3]))
            try:
                message = main._degraded_workers()
            finally:
                fleet_state.attach(None)

        assert message and "are alive" in message

    def test_an_attached_but_unpublished_array_is_not_healthy(self):
        # The startup window. Publishing after the array was created went to a
        # slot that did not exist, so it stayed unpublished and health answered
        # ok for the whole of startup -- the state this channel exists to
        # remove.
        import sys
        from unittest.mock import patch
        sys.modules.pop("web.main", None)
        from web import main
        from utils import fleet_state

        main._DEGRADED_CACHE.update(at=0.0, value=None)
        with patch.dict("os.environ", {}, clear=True):
            fleet_state.attach(_FakeArray([fleet_state.UNPUBLISHED] * 3))
            try:
                # Nothing published and no shards to fall back to: the fleet
                # is unknown, and unknown must not read as healthy once a
                # pipeline has been configured.
                assert fleet_state.read() is None
                assert main._degraded_workers() is None
            finally:
                fleet_state.attach(None)

    def test_a_configured_fleet_with_nothing_ready_is_degraded(self):
        import sys
        from unittest.mock import patch
        sys.modules.pop("web.main", None)
        from web import main
        from utils import fleet_state

        main._DEGRADED_CACHE.update(at=0.0, value=None)
        with patch.dict("os.environ", {}, clear=True):
            fleet_state.attach(_FakeArray([4, 0, 0]))
            try:
                message = main._degraded_workers()
            finally:
                fleet_state.attach(None)

        assert message and "0 of 4" in message

    def test_an_unreadable_shard_reads_as_degraded_not_healthy(self):
        # The shards are the only channel carrying assignment state to this
        # process. One that cannot be read leaves a dead fleet
        # indistinguishable from a whole one, and answering ok is the single
        # thing this endpoint must never do on a guess.
        import sys
        from unittest.mock import patch
        sys.modules.pop("web.main", None)
        from web import main

        main._DEGRADED_CACHE.update(at=0.0, value=None)
        with patch.dict("os.environ", {"PROMETHEUS_MULTIPROC_DIR": "/tmp/x"}), \
             patch("prometheus_client.CollectorRegistry",
                   side_effect=RuntimeError("corrupt shard")):
            message = main._degraded_workers()

        assert message and "could not be read" in message

    def test_metrics_switched_off_is_not_an_error(self):
        # Nothing to read is not the same as something wrong.
        assert self._degraded({}, multiproc=False) is None

    def test_missing_series_is_not_an_error(self):
        assert self._degraded({"alert_bridge_pipeline_processes_ready": 2.0}) is None

    def test_the_answer_is_cached_between_probes(self):
        # /health is polled often, and each read mmaps every metric shard.
        import sys
        from unittest.mock import patch
        sys.modules.pop("web.main", None)
        from web import main
        main._DEGRADED_CACHE.update(at=0.0, value=None)
        with patch.dict("os.environ", {"PROMETHEUS_MULTIPROC_DIR": "/tmp/x"}), \
             patch("prometheus_client.CollectorRegistry") as reg, \
             patch("prometheus_client.multiprocess.MultiProcessCollector"):
            reg.return_value.collect.return_value = []
            main._degraded_workers()
            main._degraded_workers()
            main._degraded_workers()
        assert reg.return_value.collect.call_count == 1


class TestHealthCarriesFleetState:
    """Aggregate worker assignment state must reach /health.

    Fleet state was moved onto a new /ready in response to an internal
    review, which read a rebalance as a sick container. That reversed an
    explicit lead decision: /health is what the deployment contract probes,
    so that is where the fleet has to be reported. /ready answers the same,
    for deployments that prefer the conventional name.
    """

    @staticmethod
    def _client(degraded=None, startup_ready=True):
        import sys
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        sys.modules.pop("web.main", None)
        from web import main

        # patch.object rather than assignment: a test that left
        # ``_startup_ready`` False would make every later one fail for a reason
        # that has nothing to do with what it is testing.
        patchers = [
            patch.object(main, "_startup_ready", startup_ready),
            patch.object(main, "_startup_error",
                         None if startup_ready else "store unavailable"),
            patch.object(main, "_degraded_workers", lambda: degraded),
        ]
        for patcher in patchers:
            patcher.start()

        class _Stack:
            def stop(self):
                for started in patchers:
                    started.stop()

        return TestClient(main.app, raise_server_exceptions=False), _Stack()

    def _codes(self, **kwargs):
        client, patcher = self._client(**kwargs)
        try:
            return client.get("/health").status_code, client.get("/ready").status_code
        finally:
            patcher.stop()

    def test_a_healthy_fleet_answers_both(self):
        assert self._codes(degraded=None) == (200, 200)

    def test_a_degraded_fleet_moves_both(self):
        # Aggregate worker assignment state has to reach /health -- that is the
        # endpoint the deployment contract probes. /ready is the same answer
        # under the conventional name, not a different one.
        assert self._codes(degraded="0 of 4 pipeline processes hold a "
                                    "partition assignment") == (503, 503)

    def test_a_failed_startup_fails_both(self):
        # This process cannot serve its own API either, so it is not liveness.
        assert self._codes(degraded=None, startup_ready=False) == (503, 503)

    def test_a_single_process_instance_publishes_its_own_fleet_state(self):
        # /ready gates on the fleet gauges, which only the supervisor wrote.
        # At one process nothing published them, so "nothing configured" read
        # as nothing wrong and /ready answered 200 through startup and through
        # every rebalance -- on the default configuration.
        from unittest.mock import MagicMock, patch
        import enhance_alert_with_vlm as entry

        published = []
        enhancer = MagicMock()
        enhancer._publishes_own_fleet_state = True
        enhancer.source.is_ready.return_value = False
        enhancer.source.assigned_partition_count.return_value = 0
        with patch("metrics.recorder.set_pipeline_process_counts",
                   side_effect=lambda *a: published.append(a)), \
             patch("metrics.recorder.set_assigned_partitions"):
            entry.AnomalyEnhancer._publish_assignment_state(enhancer)

        assert published == [(1, 1, 0)]

    def test_a_supervised_child_leaves_the_fleet_gauges_alone(self):
        # A child writing 1/1 would overwrite the supervisor's totals.
        from unittest.mock import MagicMock, patch
        import enhance_alert_with_vlm as entry

        published = []
        enhancer = MagicMock()
        enhancer._publishes_own_fleet_state = False
        enhancer.source.is_ready.return_value = True
        enhancer.source.assigned_partition_count.return_value = 4
        with patch("metrics.recorder.set_pipeline_process_counts",
                   side_effect=lambda *a: published.append(a)), \
             patch("metrics.recorder.set_assigned_partitions"):
            entry.AnomalyEnhancer._publish_assignment_state(enhancer)

        assert published == []

    def test_ready_names_the_degradation(self):
        client, patcher = self._client(degraded="1 of 4 pipeline processes hold a "
                                                "partition assignment")
        try:
            assert "1 of 4" in client.get("/ready").json()["message"]
        finally:
            patcher.stop()


class TestHooksSurviveRegistrationOrder:
    """A hook registered after the consumers exist must still reach them.

    Consumers were once created on first read, so any hook registered before
    that reached all of them -- a docstring said exactly this. Creating them
    up front to fix a startup deadlock silently turned a hook registered
    afterwards into a no-op: the assignment gauge froze at its startup value,
    readiness never dropped, and health never reported a degraded fleet.
    """

    @staticmethod
    def _source():
        from unittest.mock import MagicMock
        from mdx.source.source_kafka import SourceKafka

        source = SourceKafka.__new__(SourceKafka)
        source.topic_consumer_map = {}
        source.groupId = "g"
        source.source_topics = ["t"]
        source._revoke_hook = None
        source._assignment_change_hook = None
        source.kafka_message_broker = MagicMock()
        return source

    def _consumer_hooks(self, source):
        source._ensure_consumer("t")
        return source.kafka_message_broker.get_consumer.call_args.kwargs

    def test_an_assignment_hook_registered_afterwards_still_fires(self):
        source = self._source()
        hooks = self._consumer_hooks(source)      # consumer built with none set

        seen = []
        source.set_assignment_change_hook(lambda: seen.append("assigned"))
        hooks["on_assignment_change"]()

        assert seen == ["assigned"]

    def test_a_revoke_hook_registered_afterwards_still_fires(self):
        source = self._source()
        hooks = self._consumer_hooks(source)

        seen = []
        source.set_revoke_hook(seen.append)
        hooks["on_revoke"]({("t", 0)})

        assert seen == [{("t", 0)}]

    def test_no_hook_registered_is_not_an_error(self):
        source = self._source()
        hooks = self._consumer_hooks(source)
        hooks["on_assignment_change"]()
        hooks["on_revoke"]({("t", 0)})

    def test_replacing_a_hook_takes_effect_on_the_next_callback(self):
        source = self._source()
        hooks = self._consumer_hooks(source)
        source.set_assignment_change_hook(lambda: seen.append("first"))
        seen = []
        source.set_assignment_change_hook(lambda: seen.append("second"))
        hooks["on_assignment_change"]()
        assert seen == ["second"]


class _WritableFakeArray(_FakeArray):
    """A fake the publisher can write, not only read."""

    def __setitem__(self, index, value):
        self._values[index] = value


class TestTheArrayIsWhatGetsWritten:
    """Writing only the Prometheus gauges leaves /health blind.

    The gauges are the scrape channel; the array is what the endpoint reads,
    and it reads it whether or not metrics are exported -- which by default
    they are not. Every one of these went green with the publish deleted, so
    each pins a site that had no coverage: the fleet could stop being
    reported and the suite would not notice.
    """

    @staticmethod
    def _fresh():
        from utils import fleet_state
        array = _WritableFakeArray([fleet_state.UNPUBLISHED] * 3)
        fleet_state.attach(array)
        return array

    def _publish_at_one_process(self, ready, assigned):
        from unittest.mock import MagicMock, patch
        import enhance_alert_with_vlm as entry
        from utils import fleet_state

        enhancer = MagicMock()
        enhancer._publishes_own_fleet_state = True
        enhancer.source.is_ready.return_value = ready
        enhancer.source.assigned_partition_count.return_value = assigned

        try:
            self._fresh()
            with patch("metrics.recorder.set_pipeline_process_counts"), \
                 patch("metrics.recorder.set_assigned_partitions"):
                entry.AnomalyEnhancer._publish_assignment_state(enhancer)
            return fleet_state.read()
        finally:
            fleet_state.attach(None)

    def test_a_lone_pipeline_reports_itself_as_its_own_fleet(self):
        assert self._publish_at_one_process(ready=True, assigned=4) == (1, 1, 1)

    def test_a_lone_pipeline_without_an_assignment_is_not_ready(self):
        # The counts have to differ, or /health cannot tell the two apart.
        assert self._publish_at_one_process(ready=False, assigned=0) == (1, 1, 0)

    def test_a_supervised_child_leaves_the_array_to_the_parent(self):
        # A child writing 1/1 would overwrite the parent's totals with its own.
        from unittest.mock import MagicMock, patch
        import enhance_alert_with_vlm as entry
        from utils import fleet_state

        enhancer = MagicMock()
        enhancer._publishes_own_fleet_state = False
        enhancer.source.is_ready.return_value = True
        enhancer.source.assigned_partition_count.return_value = 4

        self._fresh()
        try:
            with patch("metrics.recorder.set_pipeline_process_counts"), \
                 patch("metrics.recorder.set_assigned_partitions"):
                entry.AnomalyEnhancer._publish_assignment_state(enhancer)
            assert fleet_state.read() is None
        finally:
            fleet_state.attach(None)

    def test_the_api_child_adopts_the_array_before_it_starts_serving(self):
        # Adopting it after uvicorn.run would be adopting it after the first
        # probe can already have been answered -- and answered ok, because an
        # unattached module reads as "nothing to report".
        from unittest.mock import patch
        import enhance_alert_with_vlm as entry
        from utils import fleet_state

        seen = []
        array = _WritableFakeArray([7, 7, 3])
        fleet_state.attach(None)
        try:
            with patch.object(entry, "uvicorn") as uvicorn_module:
                uvicorn_module.run.side_effect = (
                    lambda *a, **k: seen.append(fleet_state.read())
                )
                entry.start_fastapi(shared_fleet_state=array)
            assert seen == [(7, 7, 3)]
        finally:
            fleet_state.attach(None)

    def test_creating_the_array_publishes_the_configured_count(self):
        # Creating and publishing are one step because splitting them is what
        # went wrong: the publish ran first, found no array, returned early,
        # and /health answered ok through a whole startup with no pipeline.
        import multiprocessing
        from utils import fleet_state

        fleet_state.attach(None)
        try:
            fleet_state.create(multiprocessing.get_context("spawn"), 8)
            assert fleet_state.read() == (8, 0, 0)
        finally:
            fleet_state.attach(None)

    def test_a_pipeline_reports_its_own_fleet_unless_told_otherwise(self):
        # The default is what a single-process deployment gets, and every
        # shipped profile ships processes: 1. With this False and nothing
        # else publishing, a completely healthy instance answers 503 for as
        # long as it runs.
        import enhance_alert_with_vlm as entry

        assert entry.AnomalyEnhancer._publishes_own_fleet_state is True
