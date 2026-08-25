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

"""Unit tests for ``mdx.kafka_message_broker``.

``get_consumed_messages`` owns manual offset commits, so its loop control is
what keeps the Alert Bridge from either stalling or silently losing events:

* poll returns ``None`` -> stop early (no busy-wait on an idle topic)
* partition EOF -> keep polling (not a real error)
* any other broker error -> abandon the batch but return what was collected
* a failed commit must not drop the message already collected

The Confluent ``Consumer``/``Producer`` constructors are patched out; nothing
here touches a real broker.
"""

from unittest.mock import MagicMock, patch

import pytest
from confluent_kafka import KafkaError, KafkaException

from mdx.kafka_message_broker import KafkaMessageBroker

CONFIG = {
    "kafka": {
        "bootstrap_servers": "kafka:9092",
        "auto_offset_reset": "latest",
        "enable_auto_commit": False,
        "max_poll_interval_ms": 600000,
        "poll_timeout": 1000,
        "max_poll_records": 10,
    }
}


def make_message(topic="alerts", partition=0, key=b"k", value=b"v", ts=(1, 1700000000000)):
    msg = MagicMock()
    msg.error.return_value = None
    msg.topic.return_value = topic
    msg.partition.return_value = partition
    msg.key.return_value = key
    msg.value.return_value = value
    msg.timestamp.return_value = ts
    return msg


def make_error_message(code):
    err = MagicMock()
    err.code.return_value = code
    msg = MagicMock()
    msg.error.return_value = err
    return msg


@pytest.fixture
def broker():
    return KafkaMessageBroker(CONFIG)


class TestGetConsumer:
    def test_builds_consumer_config_from_the_kafka_section(self, broker):
        with patch("mdx.kafka_message_broker.Consumer") as consumer_cls:
            broker.get_consumer("alerts", "group-1")

        config = consumer_cls.call_args[0][0]
        assert config["bootstrap.servers"] == "kafka:9092"
        assert config["group.id"] == "group-1"
        assert config["auto.offset.reset"] == "latest"
        assert config["enable.auto.commit"] is False
        assert config["max.poll.interval.ms"] == 600000

    def test_session_and_heartbeat_default_when_unset(self, broker):
        with patch("mdx.kafka_message_broker.Consumer") as consumer_cls:
            broker.get_consumer("alerts", "group-1")

        config = consumer_cls.call_args[0][0]
        assert config["session.timeout.ms"] == 300000
        assert config["heartbeat.interval.ms"] == 300000

    def test_configured_session_and_heartbeat_win(self):
        config = {"kafka": dict(CONFIG["kafka"], session_timeout_ms=45000, heartbeat_interval_ms=3000)}
        with patch("mdx.kafka_message_broker.Consumer") as consumer_cls:
            KafkaMessageBroker(config).get_consumer("alerts", "group-1")

        built = consumer_cls.call_args[0][0]
        assert built["session.timeout.ms"] == 45000
        assert built["heartbeat.interval.ms"] == 3000

    def test_subscribes_to_the_requested_topic(self, broker):
        with patch("mdx.kafka_message_broker.Consumer") as consumer_cls:
            consumer = broker.get_consumer("alerts", "group-1")

        topics, hooks = consumer.subscribe.call_args
        assert topics == (["alerts"],)
        assert consumer is consumer_cls.return_value

    def test_the_rebalance_hooks_are_registered(self, broker):
        # Readiness and the revoke drain both hang off these; subscribing
        # without them leaves the assignment unobservable.
        with patch("mdx.kafka_message_broker.Consumer") as consumer_cls:
            consumer = broker.get_consumer("alerts", "group-1")

        hooks = consumer.subscribe.call_args.kwargs
        assert set(hooks) == {"on_assign", "on_revoke", "on_lost"}
        assert all(callable(hook) for hook in hooks.values())

    def test_a_lost_assignment_is_treated_as_a_revoke(self, broker):
        # Partitions may already be owned elsewhere, so the same stop-and-
        # drain path has to run.
        with patch("mdx.kafka_message_broker.Consumer") as consumer_cls:
            consumer = broker.get_consumer("alerts", "group-1")

        hooks = consumer.subscribe.call_args.kwargs
        assert hooks["on_lost"] is hooks["on_revoke"]


class TestGetProducer:
    def test_producer_only_needs_bootstrap_servers(self, broker):
        with patch("mdx.kafka_message_broker.Producer") as producer_cls:
            producer = broker.get_producer()

        assert producer_cls.call_args[0][0] == {"bootstrap.servers": "kafka:9092"}
        assert producer is producer_cls.return_value


class TestGetConsumedMessages:
    def test_groups_messages_by_topic_and_partition(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [
            make_message(partition=0, key=b"k1", value=b"v1"),
            make_message(partition=1, key=b"k2", value=b"v2"),
            None,
        ]

        result = broker.get_consumed_messages(consumer)

        assert set(result) == {"alerts-0", "alerts-1"}
        assert result["alerts-0"] == [(b"k1", b"v1", 1700000000000)]
        assert result["alerts-1"] == [(b"k2", b"v2", 1700000000000)]

    def test_accumulates_multiple_messages_per_partition(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [
            make_message(key=b"k1"),
            make_message(key=b"k2"),
            None,
        ]

        result = broker.get_consumed_messages(consumer)

        assert len(result["alerts-0"]) == 2

    def test_stops_polling_on_the_first_empty_poll(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [make_message(), None, make_message()]

        result = broker.get_consumed_messages(consumer)

        assert len(result["alerts-0"]) == 1
        assert consumer.poll.call_count == 2

    def test_poll_timeout_is_converted_to_seconds(self, broker):
        consumer = MagicMock()
        consumer.poll.return_value = None

        broker.get_consumed_messages(consumer)

        assert consumer.poll.call_args.kwargs["timeout"] == 1.0

    def test_batch_size_argument_caps_the_loop(self, broker):
        consumer = MagicMock()
        consumer.poll.return_value = make_message()

        result = broker.get_consumed_messages(consumer, batch_size=3)

        assert consumer.poll.call_count == 3
        assert len(result["alerts-0"]) == 3

    def test_batch_size_falls_back_to_max_poll_records(self, broker):
        consumer = MagicMock()
        consumer.poll.return_value = make_message()

        broker.get_consumed_messages(consumer)

        assert consumer.poll.call_count == 10

    def test_max_poll_records_defaults_when_unset(self):
        config = {"kafka": {k: v for k, v in CONFIG["kafka"].items() if k != "max_poll_records"}}
        consumer = MagicMock()
        consumer.poll.return_value = make_message()

        KafkaMessageBroker(config).get_consumed_messages(consumer)

        assert consumer.poll.call_count == 10

    def test_partition_eof_keeps_polling(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [
            make_error_message(KafkaError._PARTITION_EOF),
            make_message(),
            None,
        ]

        result = broker.get_consumed_messages(consumer)

        assert len(result["alerts-0"]) == 1

    def test_other_broker_errors_abandon_the_batch(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [
            make_message(key=b"k1"),
            make_error_message(KafkaError.BROKER_NOT_AVAILABLE),
            make_message(key=b"k2"),
        ]

        result = broker.get_consumed_messages(consumer)

        # The message collected before the error is still returned.
        assert result["alerts-0"] == [(b"k1", b"v", 1700000000000)]

    def test_missing_timestamp_type_yields_none(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [make_message(ts=(0, -1)), None]

        result = broker.get_consumed_messages(consumer)

        assert result["alerts-0"][0][2] is None

    def test_log_append_timestamp_type_is_kept(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [make_message(ts=(2, 1700000000999)), None]

        result = broker.get_consumed_messages(consumer)

        assert result["alerts-0"][0][2] == 1700000000999

    def test_each_message_offset_is_committed(self, broker):
        consumer = MagicMock()
        first, second = make_message(key=b"k1"), make_message(key=b"k2")
        consumer.poll.side_effect = [first, second, None]

        broker.get_consumed_messages(consumer)

        assert consumer.commit.call_count == 2
        consumer.commit.assert_any_call(first)

    def test_commit_failure_does_not_drop_the_message(self, broker):
        consumer = MagicMock()
        consumer.poll.side_effect = [make_message(), None]
        consumer.commit.side_effect = KafkaException("commit failed")

        result = broker.get_consumed_messages(consumer)

        assert len(result["alerts-0"]) == 1

    def test_empty_topic_returns_an_empty_dict(self, broker):
        consumer = MagicMock()
        consumer.poll.return_value = None

        assert broker.get_consumed_messages(consumer) == {}
