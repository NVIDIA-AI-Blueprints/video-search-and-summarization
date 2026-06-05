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

"""Unit tests for :class:`ConfigPublisher`."""

import json
import logging
import unittest
from unittest.mock import Mock

from mdx.analytics.core.transform.config.config_applier import ApplyResult
from mdx.analytics.core.transform.config.config_publisher import (
    EVENT_TYPE_ACK,
    EVENT_TYPE_REQUEST_CONFIG,
    NOTIFICATION_DEST_KEY,
    NOTIFICATION_MESSAGE_KEY,
    ConfigPublisher,
)


class TestConfigPublisher(unittest.TestCase):
    """Outgoing-message envelope shape and routing."""

    def setUp(self) -> None:
        self.sink = Mock()
        self.publisher = ConfigPublisher(self.sink)

    def _written_call(self) -> dict:
        """Return the kwargs passed to the last sink.write_msg call."""
        self.assertEqual(self.sink.write_msg.call_count, 1)
        return self.sink.write_msg.call_args.kwargs

    def test_request_config_envelope(self) -> None:
        """``request-config`` carries the bootstrap reference-id and a null payload."""
        ref_id = "behavior-analytics-deadbeef"
        self.publisher.publish_request_config(ref_id)

        kwargs = self._written_call()
        self.assertEqual(kwargs["dest_key"], NOTIFICATION_DEST_KEY)
        self.assertEqual(kwargs["key"], NOTIFICATION_MESSAGE_KEY.encode("utf-8"))

        # Headers carry event.type and reference-id, both UTF-8 encoded.
        self.assertEqual(kwargs["headers"]["event.type"], EVENT_TYPE_REQUEST_CONFIG.encode("utf-8"))
        self.assertEqual(kwargs["headers"]["reference-id"], ref_id.encode("utf-8"))

        # Body is JSON with all-null fields per spec.
        body = json.loads(kwargs["message"].decode("utf-8"))
        self.assertEqual(body, {"status": None, "config": None, "error": None})

    def test_ack_success_envelope(self) -> None:
        """``ack`` carries the inbound ref-id, status, merged config, and null error."""
        result = ApplyResult(
            status="success",
            config={"app": [{"name": "k", "value": "v"}], "sensors": []},
            error=None,
        )
        ref_id = "video-analytics-api-abc123"
        self.publisher.publish_ack(ref_id, result)

        kwargs = self._written_call()
        self.assertEqual(kwargs["headers"]["event.type"], EVENT_TYPE_ACK.encode("utf-8"))
        self.assertEqual(kwargs["headers"]["reference-id"], ref_id.encode("utf-8"))

        body = json.loads(kwargs["message"].decode("utf-8"))
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["config"], {"app": [{"name": "k", "value": "v"}], "sensors": []})
        self.assertIsNone(body["error"])

    def test_ack_failure_envelope(self) -> None:
        """A failure ack passes through ``config=None`` and the error."""
        result = ApplyResult(
            status="failure",
            config=None,
            error="section 'kafka' is read-only",
        )
        self.publisher.publish_ack("ref-fail", result)

        body = json.loads(self._written_call()["message"].decode("utf-8"))
        self.assertEqual(body["status"], "failure")
        self.assertIsNone(body["config"])
        self.assertEqual(body["error"], "section 'kafka' is read-only")

    def test_ack_partial_success_envelope(self) -> None:
        """Partial-success preserves the merged config and rejection details."""
        result = ApplyResult(
            status="partial-success",
            config={"app": [{"name": "ok", "value": "1"}], "sensors": []},
            error="applied 1; rejected: app[1]: 'name' must be a non-empty string",
        )
        self.publisher.publish_ack("ref-partial", result)

        body = json.loads(self._written_call()["message"].decode("utf-8"))
        self.assertEqual(body["status"], "partial-success")
        self.assertEqual(body["config"]["app"], [{"name": "ok", "value": "1"}])
        self.assertIn("rejected", body["error"])

    def test_publish_does_not_raise_on_sink_error(self) -> None:
        """A sink failure is logged and swallowed (must not crash the listener)."""
        self.sink.write_msg.side_effect = RuntimeError("broker unreachable")
        # Caplog-style: capture the error log line.
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_publisher", level=logging.ERROR
        ) as ctx:
            # Should not raise.
            self.publisher.publish_request_config("ref-x")
        self.assertTrue(any("failed to publish" in line for line in ctx.output))


if __name__ == "__main__":
    unittest.main()
