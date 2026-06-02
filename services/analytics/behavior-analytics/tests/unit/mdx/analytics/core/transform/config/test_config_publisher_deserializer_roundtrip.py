# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Wire-format round-trip between :class:`ConfigPublisher` and
:func:`deserialize_config_message`.

Both sides are independently unit-tested against fixtures (a frozen wire shape
the implementation is presumed to honor). This file proves the two actually
agree: bytes produced by the publisher decode back to the expected
:class:`ConfigMessage`. A drift in either direction (a key rename, a header
encoding change, a body-shape tweak) breaks these tests where the per-side
unit tests would still pass.

We don't need a real Kafka broker -- a ``Mock`` sink captures the bytes the
publisher hands to ``write_msg``, and we feed those bytes back in through a
synthetic :class:`StreamMessage`.
"""

import time
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from mdx.analytics.core.schema.models import StreamMessage
from mdx.analytics.core.transform.config.config_applier import ApplyResult
from mdx.analytics.core.transform.config.config_listener import deserialize_config_message
from mdx.analytics.core.transform.config.config_publisher import (
    EVENT_TYPE_ACK,
    EVENT_TYPE_REQUEST_CONFIG,
    NOTIFICATION_MESSAGE_KEY,
    ConfigPublisher,
)


class TestPublisherDeserializerRoundtrip(unittest.TestCase):
    """Publisher writes bytes; deserializer reads them; assert equivalence."""

    def setUp(self) -> None:
        self.sink = Mock()
        self.publisher = ConfigPublisher(self.sink)

    def _capture_and_decode(self, kafka_timestamp_ms: int | None = None):
        """
        Pull the bytes the publisher just wrote, wrap them in a StreamMessage
        with the same key+headers the publisher used, and run them through
        :func:`deserialize_config_message`.

        :param int | None kafka_timestamp_ms: Synthetic Kafka record timestamp
            (in ms) to attach to the StreamMessage. ``None`` defaults to "now"
            in milliseconds, mirroring real broker behavior.
        :return: The decoded envelope dict (per
            :func:`deserialize_config_message`'s new contract).
        """
        self.assertEqual(self.sink.write_msg.call_count, 1)
        kwargs = self.sink.write_msg.call_args.kwargs
        ts_ms = kafka_timestamp_ms if kafka_timestamp_ms is not None else int(time.time() * 1000)
        sm = StreamMessage(
            key=kwargs["key"],
            value=kwargs["message"],
            headers=dict(kwargs["headers"]),
            timestamp=ts_ms,
        )
        return deserialize_config_message(sm)

    def test_request_config_roundtrip(self) -> None:
        """``request-config`` envelope decodes back to a ConfigMessage with null body fields."""
        ref_id = "behavior-analytics-cafef00d"
        self.publisher.publish_request_config(ref_id)

        cm = self._capture_and_decode()
        self.assertIsNotNone(cm)
        self.assertEqual(cm["event_type"], EVENT_TYPE_REQUEST_CONFIG)
        self.assertEqual(cm["reference_id"], ref_id)
        self.assertIsNone(cm["config"])
        self.assertIsNone(cm["status"])
        self.assertIsNone(cm["error"])

    def test_ack_success_roundtrip(self) -> None:
        """A success ack with a merged config snapshot survives the round-trip."""
        result = ApplyResult(
            status="success",
            config={
                "app": [{"name": "k", "value": "v"}],
                "sensors": [{"id": "cam1", "configs": [{"name": "c", "value": "1"}]}],
            },
            error=None,
        )
        self.publisher.publish_ack("ref-OK", result)

        cm = self._capture_and_decode()
        self.assertIsNotNone(cm)
        self.assertEqual(cm["event_type"], EVENT_TYPE_ACK)
        self.assertEqual(cm["reference_id"], "ref-OK")
        self.assertEqual(cm["status"], "success")
        self.assertEqual(cm["config"], result.config)
        self.assertIsNone(cm["error"])

    def test_ack_failure_roundtrip(self) -> None:
        """A failure ack passes both the null config and the error through."""
        result = ApplyResult(
            status="failure",
            config=None,
            error="section 'kafka' is read-only",
        )
        self.publisher.publish_ack("ref-FAIL", result)

        cm = self._capture_and_decode()
        self.assertIsNotNone(cm)
        self.assertEqual(cm["status"], "failure")
        self.assertIsNone(cm["config"])
        self.assertEqual(cm["error"], "section 'kafka' is read-only")

    def test_ack_partial_success_roundtrip(self) -> None:
        """Partial-success carries both the merged config and the rejections summary."""
        result = ApplyResult(
            status="partial-success",
            config={"app": [{"name": "ok", "value": "1"}], "sensors": []},
            error="applied 1; rejected: app[1]: 'name' must be a non-empty string",
        )
        self.publisher.publish_ack("ref-PART", result)

        cm = self._capture_and_decode()
        self.assertIsNotNone(cm)
        self.assertEqual(cm["status"], "partial-success")
        self.assertEqual(cm["config"], result.config)
        self.assertIn("rejected", cm["error"])

    def test_published_key_matches_listener_filter(self) -> None:
        """The publisher's key is exactly what the deserializer filters on."""
        self.publisher.publish_request_config("ref-X")
        self.assertEqual(
            self.sink.write_msg.call_args.kwargs["key"],
            NOTIFICATION_MESSAGE_KEY.encode("utf-8"),
        )

    def test_kafka_record_timestamp_decoded_as_utc_datetime(self) -> None:
        """The deserializer's Kafka timestamp -> datetime conversion is UTC and ms-precise."""
        # Pick a known epoch-ms (2026-04-29T10:30:15.123Z) and assert decode.
        known_ms = int(datetime(2026, 4, 29, 10, 30, 15, 123_000, tzinfo=timezone.utc).timestamp() * 1000)
        self.publisher.publish_request_config("ref-T")

        cm = self._capture_and_decode(kafka_timestamp_ms=known_ms)
        self.assertIsNotNone(cm)
        # Round-trip equality on the datetime (within ms precision).
        self.assertEqual(cm["timestamp"], datetime.fromtimestamp(known_ms / 1000.0, tz=timezone.utc))


if __name__ == "__main__":
    unittest.main()
