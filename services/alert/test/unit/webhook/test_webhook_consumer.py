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

"""Unit tests for ``webhook.consumer``.

``WebhookKafkaForwarder`` is polled from the main enhancer loop, so every
failure mode — broker unavailable, poll error, undecodable message — has to
degrade to a log line rather than an exception that would stall ingestion.

``_decode_kafka_value`` accepts both Protobuf binary and JSON text and always
round-trips through the nvSchema ``Incident`` message, so the payload handed
to the webhook uses camelCase keys.
"""

from unittest.mock import MagicMock, patch

import pytest

from mdx.protobuf import Incident as nvSchemaIncident
from webhook.consumer import WebhookKafkaForwarder, _decode_kafka_value


class TestDecodeKafkaValue:
    """Both wire formats must produce the same camelCase dict."""

    def test_decodes_json_text(self):
        result = _decode_kafka_value('{"sensorId": "cam-1", "category": "intrusion"}')
        assert result == {"sensorId": "cam-1", "category": "intrusion"}

    def test_decodes_json_bytes(self):
        result = _decode_kafka_value(b'{"sensorId": "cam-1"}')
        assert result == {"sensorId": "cam-1"}

    def test_decodes_protobuf_binary(self):
        payload = nvSchemaIncident(sensorId="cam-1", category="intrusion").SerializeToString()
        result = _decode_kafka_value(payload)
        assert result == {"sensorId": "cam-1", "category": "intrusion"}

    def test_json_and_protobuf_agree(self):
        """The two wire formats are interchangeable for the same incident."""
        from_json = _decode_kafka_value('{"sensorId": "cam-9", "isAnomaly": true}')
        from_proto = _decode_kafka_value(
            nvSchemaIncident(sensorId="cam-9", isAnomaly=True).SerializeToString()
        )
        assert from_json == from_proto

    def test_snake_case_json_keys_are_rejected(self):
        """nvSchema declares ``sensorId`` literally, so ``sensor_id`` is unknown."""
        with pytest.raises(Exception, match="no field named"):
            _decode_kafka_value('{"sensor_id": "cam-1"}')

    def test_empty_payload_raises_value_error(self):
        """Garbage that ParseFromString silently accepts must still be rejected."""
        with pytest.raises(ValueError, match="decoded message is empty"):
            _decode_kafka_value(b"")

    def test_json_with_only_default_values_raises_value_error(self):
        with pytest.raises(ValueError, match="decoded message is empty"):
            _decode_kafka_value('{"sensorId": ""}')

    def test_unknown_json_field_propagates_parse_error(self):
        with pytest.raises(Exception):
            _decode_kafka_value('{"definitelyNotAField": 1}')

    def test_undecodable_binary_propagates_error(self):
        with pytest.raises(Exception):
            _decode_kafka_value(b"\xff\xfe\xfd\xfc")


def make_notifier(enabled=True, topic="incidents"):
    notifier = MagicMock()
    notifier.enabled = enabled
    notifier.topic = topic
    return notifier


@pytest.fixture
def mock_broker_cls():
    """Patch ``KafkaMessageBroker`` where the forwarder imports it."""
    with patch("webhook.consumer.KafkaMessageBroker") as cls:
        cls.return_value.get_consumer.return_value = MagicMock(name="consumer")
        yield cls


class TestForwarderConstruction:
    def test_disabled_notifier_creates_no_consumer(self, mock_broker_cls):
        forwarder = WebhookKafkaForwarder({}, make_notifier(enabled=False))
        assert forwarder._consumer is None
        mock_broker_cls.assert_not_called()

    def test_missing_topic_creates_no_consumer(self, mock_broker_cls):
        forwarder = WebhookKafkaForwarder({}, make_notifier(topic=""))
        assert forwarder._consumer is None
        mock_broker_cls.assert_not_called()

    def test_creates_consumer_for_notifier_topic(self, mock_broker_cls):
        forwarder = WebhookKafkaForwarder({}, make_notifier())
        assert forwarder._consumer is not None
        mock_broker_cls.return_value.get_consumer.assert_called_once_with(
            "incidents", "openclaw-webhook-incidents"
        )

    def test_group_id_defaults_to_topic_suffix(self, mock_broker_cls):
        WebhookKafkaForwarder({}, make_notifier(topic="alerts"))
        _topic, group_id = mock_broker_cls.return_value.get_consumer.call_args[0]
        assert group_id == "openclaw-webhook-alerts"

    def test_configured_group_id_overrides_default(self, mock_broker_cls):
        config = {"webhook": {"openclaw": {"group_id": "custom-group"}}}
        WebhookKafkaForwarder(config, make_notifier())
        _topic, group_id = mock_broker_cls.return_value.get_consumer.call_args[0]
        assert group_id == "custom-group"

    def test_null_webhook_section_falls_back_to_default_group(self, mock_broker_cls):
        WebhookKafkaForwarder({"webhook": None}, make_notifier())
        _topic, group_id = mock_broker_cls.return_value.get_consumer.call_args[0]
        assert group_id == "openclaw-webhook-incidents"

    def test_consumer_creation_failure_is_swallowed(self, mock_broker_cls):
        mock_broker_cls.return_value.get_consumer.side_effect = RuntimeError("no brokers")
        forwarder = WebhookKafkaForwarder({}, make_notifier())
        assert forwarder._consumer is None

    def test_broker_receives_full_config(self, mock_broker_cls):
        config = {"kafka": {"bootstrap_servers": "kafka:9092"}}
        WebhookKafkaForwarder(config, make_notifier())
        mock_broker_cls.assert_called_once_with(config)


class TestPollAndForward:
    def test_noop_when_forwarder_is_disabled(self, mock_broker_cls):
        notifier = make_notifier(enabled=False)
        forwarder = WebhookKafkaForwarder({}, notifier)
        forwarder.poll_and_forward()
        notifier.notify.assert_not_called()

    def test_forwards_each_decoded_message(self, mock_broker_cls):
        notifier = make_notifier()
        forwarder = WebhookKafkaForwarder({}, notifier)
        mock_broker_cls.return_value.get_consumed_messages.return_value = {
            "incidents-0": [
                (b"k1", b'{"sensorId": "cam-1"}', 1700000000000),
                (b"k2", b'{"sensorId": "cam-2"}', 1700000000001),
            ]
        }

        forwarder.poll_and_forward()

        assert notifier.notify.call_count == 2
        assert notifier.notify.call_args_list[0][0][0] == {"sensorId": "cam-1"}
        assert notifier.notify.call_args_list[1][0][0] == {"sensorId": "cam-2"}

    def test_forwards_across_multiple_partitions(self, mock_broker_cls):
        notifier = make_notifier()
        forwarder = WebhookKafkaForwarder({}, notifier)
        mock_broker_cls.return_value.get_consumed_messages.return_value = {
            "incidents-0": [(b"k1", b'{"sensorId": "cam-1"}', None)],
            "incidents-1": [(b"k2", b'{"sensorId": "cam-2"}', None)],
        }

        forwarder.poll_and_forward()

        assert notifier.notify.call_count == 2

    def test_empty_poll_forwards_nothing(self, mock_broker_cls):
        notifier = make_notifier()
        forwarder = WebhookKafkaForwarder({}, notifier)
        mock_broker_cls.return_value.get_consumed_messages.return_value = {}

        forwarder.poll_and_forward()

        notifier.notify.assert_not_called()

    def test_poll_error_is_swallowed(self, mock_broker_cls):
        notifier = make_notifier()
        forwarder = WebhookKafkaForwarder({}, notifier)
        mock_broker_cls.return_value.get_consumed_messages.side_effect = RuntimeError("kaboom")

        forwarder.poll_and_forward()

        notifier.notify.assert_not_called()

    def test_undecodable_message_is_skipped_without_dropping_the_batch(self, mock_broker_cls):
        notifier = make_notifier()
        forwarder = WebhookKafkaForwarder({}, notifier)
        mock_broker_cls.return_value.get_consumed_messages.return_value = {
            "incidents-0": [
                (b"k1", b"", None),                        # empty -> ValueError
                (b"k2", b'{"sensorId": "cam-2"}', None),   # still delivered
            ]
        }

        forwarder.poll_and_forward()

        notifier.notify.assert_called_once_with({"sensorId": "cam-2"})

    def test_protobuf_messages_are_forwarded(self, mock_broker_cls):
        notifier = make_notifier()
        forwarder = WebhookKafkaForwarder({}, notifier)
        payload = nvSchemaIncident(sensorId="cam-7").SerializeToString()
        mock_broker_cls.return_value.get_consumed_messages.return_value = {
            "incidents-0": [(b"k1", payload, None)]
        }

        forwarder.poll_and_forward()

        notifier.notify.assert_called_once_with({"sensorId": "cam-7"})


class TestClose:
    def test_closes_consumer_and_clears_it(self, mock_broker_cls):
        forwarder = WebhookKafkaForwarder({}, make_notifier())
        consumer = forwarder._consumer

        forwarder.close()

        consumer.close.assert_called_once()
        assert forwarder._consumer is None

    def test_consumer_close_error_is_swallowed(self, mock_broker_cls):
        forwarder = WebhookKafkaForwarder({}, make_notifier())
        forwarder._consumer.close.side_effect = RuntimeError("already closed")

        forwarder.close()

        assert forwarder._consumer is None

    def test_close_is_idempotent(self, mock_broker_cls):
        forwarder = WebhookKafkaForwarder({}, make_notifier())
        forwarder.close()
        forwarder.close()
        assert forwarder._consumer is None

    def test_close_on_disabled_forwarder_is_noop(self, mock_broker_cls):
        forwarder = WebhookKafkaForwarder({}, make_notifier(enabled=False))
        forwarder.close()
        assert forwarder._consumer is None
