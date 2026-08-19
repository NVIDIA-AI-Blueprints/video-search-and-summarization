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
