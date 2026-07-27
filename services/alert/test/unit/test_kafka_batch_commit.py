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

"""Per-message vs batched offset commit in the Kafka consume loop."""

import pytest

from confluent_kafka import KafkaException

from mdx.kafka_message_broker import KafkaMessageBroker


class FakeMessage:
    def __init__(self, topic, partition, offset, value=b"payload", key=b"cam-1"):
        self._topic = topic
        self._partition = partition
        self._offset = offset
        self._value = value
        self._key = key

    def topic(self):
        return self._topic

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def key(self):
        return self._key

    def value(self):
        return self._value

    def error(self):
        return None

    def timestamp(self):
        return (1, 1700000000000)


class FakeConsumer:
    def __init__(self, messages, commit_error=False):
        self._messages = list(messages)
        self._commit_error = commit_error
        self.commits = []

    def poll(self, timeout=None):
        return self._messages.pop(0) if self._messages else None

    def commit(self, msg):
        if self._commit_error:
            raise KafkaException("commit rejected")
        self.commits.append((msg.topic(), msg.partition(), msg.offset()))


def _broker(batch_commit):
    return KafkaMessageBroker({
        'kafka': {
            'poll_timeout': 10,
            'max_poll_records': 10,
            'batch_commit': batch_commit,
        }
    })


def _batch():
    return [
        FakeMessage("mdx-incidents", 0, 10),
        FakeMessage("mdx-incidents", 0, 11),
        FakeMessage("mdx-incidents", 1, 5),
        FakeMessage("mdx-incidents", 0, 12),
        FakeMessage("mdx-incidents", 1, 6),
    ]


class TestCommitModes:
    def test_default_commits_every_message(self):
        consumer = FakeConsumer(_batch())
        messages = _broker(False).get_consumed_messages(consumer)

        assert len(consumer.commits) == 5
        assert len(messages["mdx-incidents-0"]) == 3
        assert len(messages["mdx-incidents-1"]) == 2

    def test_batch_commits_highest_offset_per_partition(self):
        consumer = FakeConsumer(_batch())
        messages = _broker(True).get_consumed_messages(consumer)

        assert sorted(consumer.commits) == [
            ("mdx-incidents", 0, 12),
            ("mdx-incidents", 1, 6),
        ]
        assert len(messages["mdx-incidents-0"]) == 3
        assert len(messages["mdx-incidents-1"]) == 2

    def test_batch_returns_the_same_messages_as_per_message_commit(self):
        per_message = _broker(False).get_consumed_messages(FakeConsumer(_batch()))
        batched = _broker(True).get_consumed_messages(FakeConsumer(_batch()))
        assert per_message == batched

    def test_empty_poll_commits_nothing(self):
        consumer = FakeConsumer([])
        assert _broker(True).get_consumed_messages(consumer) == {}
        assert consumer.commits == []

    def test_commit_failure_does_not_break_the_consume_loop(self):
        consumer = FakeConsumer(_batch(), commit_error=True)
        messages = _broker(True).get_consumed_messages(consumer)
        assert len(messages["mdx-incidents-0"]) == 3

    def test_flag_defaults_to_off(self):
        assert KafkaMessageBroker({'kafka': {}}).batch_commit is False


class RaisingConsumer(FakeConsumer):
    def __init__(self, messages, raise_after):
        super().__init__(messages)
        self._raise_after = raise_after
        self._polls = 0

    def poll(self, timeout=None):
        self._polls += 1
        if self._polls > self._raise_after:
            raise KafkaException("broker gone")
        return super().poll(timeout)


class TestCommitBoundary:
    """The redelivery window batching opens is the poll loop, nothing wider."""

    def test_no_partition_is_returned_without_having_been_committed(self):
        consumer = FakeConsumer(_batch())
        messages = _broker(True).get_consumed_messages(consumer)

        returned = set()
        for key in messages:
            topic, partition = key.rsplit("-", 1)
            returned.add((topic, int(partition)))
        assert returned == {(topic, partition) for topic, partition, _ in consumer.commits}

    def test_batch_commits_what_was_read_when_the_poll_loop_raises(self):
        consumer = RaisingConsumer(_batch(), raise_after=3)
        messages = _broker(True).get_consumed_messages(consumer)

        assert sorted(consumer.commits) == [
            ("mdx-incidents", 0, 11),
            ("mdx-incidents", 1, 5),
        ]
        assert len(messages["mdx-incidents-0"]) == 2
        assert len(messages["mdx-incidents-1"]) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
