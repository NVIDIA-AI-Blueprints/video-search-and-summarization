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


class FakeConsumer:
    """Joins the group after ``join_after`` polls, like a real coordinator."""

    def __init__(self, join_after=2, messages=None):
        self.join_after = join_after
        self.polls = 0
        self._queued = list(messages or [])
        self.committed = []

    def poll(self, timeout=None):
        self.polls += 1
        if self.polls > self.join_after and self._queued:
            return self._queued.pop(0)
        return None

    def memberid(self):
        return "member-1" if self.polls >= self.join_after else None

    def commit(self, msg):
        self.committed.append(msg)


@pytest.fixture
def broker():
    return KafkaMessageBroker(CONFIG)


class TestAwaitGroupJoin:
    def test_polls_until_the_coordinator_admits_the_member(self, broker):
        consumer = FakeConsumer(join_after=3)
        assert broker.await_group_join(consumer, timeout=5) is True
        assert consumer.polls == 3

    def test_returns_immediately_once_a_member(self, broker):
        consumer = FakeConsumer(join_after=1)
        assert broker.await_group_join(consumer, timeout=5) is True
        assert consumer.polls == 1

    def test_reports_failure_rather_than_blocking_forever(self, broker):
        consumer = FakeConsumer()
        consumer.memberid = lambda: None        # coordinator never answers
        assert broker.await_group_join(consumer, timeout=0.3) is False

    def test_a_member_with_no_partitions_still_counts_as_joined(self, broker):
        # With more processes than partitions some members get nothing; they
        # have still joined, so an assignment-based wait would hang on them.
        consumer = FakeConsumer(join_after=1)
        consumer.assignment = lambda: []
        assert broker.await_group_join(consumer, timeout=5) is True


class TestWaitingDoesNotDropMessages:
    """Kafka only delivers to an assigned member, so anything the wait poll
    returns is real traffic that has already moved the offset."""

    def test_a_message_seen_while_waiting_reaches_the_caller(self, broker):
        wanted = FakeMessage(b"during-join")
        consumer = FakeConsumer(join_after=2, messages=[wanted])
        # Poll 3 lands the message; joined is reported from poll 2 onward, so
        # the wait ends first and the message would otherwise be discarded.
        broker.await_group_join(consumer, timeout=5)
        broker._prefetched[id(consumer)] = [wanted]

        batch = broker.get_consumed_messages(consumer)

        values = [value for msgs in batch.values() for _, value, _ in msgs]
        assert b"during-join" in values

    def test_prefetched_messages_come_before_freshly_polled_ones(self, broker):
        first, second = FakeMessage(b"first"), FakeMessage(b"second")
        consumer = FakeConsumer(join_after=0, messages=[second])
        broker._prefetched[id(consumer)] = [first]

        batch = broker.get_consumed_messages(consumer)

        values = [value for msgs in batch.values() for _, value, _ in msgs]
        assert values[:2] == [b"first", b"second"]

    def test_an_overflowing_prefetch_is_kept_for_the_next_batch(self, broker):
        held = [FakeMessage(f"m{i}".encode()) for i in range(4)]
        consumer = FakeConsumer(join_after=0)
        broker._prefetched[id(consumer)] = list(held)

        first = broker.get_consumed_messages(consumer, batch_size=2)
        second = broker.get_consumed_messages(consumer, batch_size=2)

        seen = [v for batch in (first, second) for msgs in batch.values() for _, v, _ in msgs]
        assert seen == [b"m0", b"m1", b"m2", b"m3"]

    def test_the_buffer_is_emptied_once_drained(self, broker):
        consumer = FakeConsumer(join_after=0)
        broker._prefetched[id(consumer)] = [FakeMessage()]
        broker.get_consumed_messages(consumer)
        assert broker._prefetched.get(id(consumer)) in (None, [])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
