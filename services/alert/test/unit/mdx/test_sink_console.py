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

"""Unit tests for ``mdx.sink.sink_console``.

The console sink exists so the event bridge can run with no broker at all, so
the contract is narrow but strict: it must satisfy the full ``SinkBase``
interface and it must never raise. Anything it cannot render has to degrade to a
placeholder, because the whole point is to keep a local run alive while
inspecting payloads.
"""

from datetime import datetime

import pytest

from mdx.sink.sink_base import SinkBase
from mdx.sink.sink_console import ConsoleSink
from mdx.stream_message import StreamMessage


def make_stream_message(data=None, message_id="evt-1"):
    return StreamMessage(
        id=message_id,
        timestamp=datetime(2026, 1, 1),
        data=data if data is not None else {"id": message_id},
        metadata={},
    )


class TestInterface:
    def test_it_is_a_usable_sink(self):
        """Every abstract method must be implemented or the factory cannot
        instantiate it."""
        assert isinstance(ConsoleSink({}), SinkBase)

    def test_it_needs_no_configuration(self):
        ConsoleSink({})

    def test_construction_warns_that_output_is_not_durable(self, caplog):
        with caplog.at_level("WARNING"):
            ConsoleSink({})
        assert "not durable" in caplog.text

    def test_close_is_a_no_op(self):
        assert ConsoleSink({}).close() is None


class TestRendering:
    def test_stream_messages_are_rendered(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write([make_stream_message({"id": "evt-1", "verdict": "confirmed"})])
        assert "confirmed" in caplog.text

    def test_incidents_are_labelled_separately_from_anomalies(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write_incidents([make_stream_message({"id": "inc-1"})])
        assert "incident" in caplog.text

    def test_raw_json_bytes_are_pretty_printed(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write_msg([b'{"id": "evt-1"}'])
        assert '"id": "evt-1"' in caplog.text

    def test_non_json_bytes_fall_back_to_text(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write_msg([b"not json at all"])
        assert "not json at all" in caplog.text

    def test_undecodable_bytes_do_not_raise(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write_msg([b"\xff\xfe\x00"])

    def test_dict_payloads_are_rendered(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write_data([{"id": "evt-1"}])
            sink.write_incident_data([{"id": "inc-1"}])
        assert "evt-1" in caplog.text
        assert "inc-1" in caplog.text

    def test_unserialisable_payloads_do_not_raise(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write_data([{"id": "evt-1", "bad": object()}])

    def test_output_is_truncated_when_configured(self, caplog):
        sink = ConsoleSink({"event_bridge": {"console_sink": {"max_chars": 10}}})
        with caplog.at_level("INFO"):
            sink.write([make_stream_message({"id": "evt-1", "detail": "x" * 200})])
        assert "truncated" in caplog.text

    def test_truncation_is_off_by_default(self, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write([make_stream_message({"id": "evt-1", "detail": "x" * 200})])
        assert "truncated" not in caplog.text

    def test_compact_rendering_is_configurable(self, caplog):
        sink = ConsoleSink({"event_bridge": {"console_sink": {"pretty": False}}})
        with caplog.at_level("INFO"):
            sink.write([make_stream_message({"id": "evt-1", "a": 1})])
        assert '{"id": "evt-1", "a": 1}' in caplog.text

    @pytest.mark.parametrize("batch", [[], None])
    def test_empty_batches_are_no_ops(self, batch, caplog):
        sink = ConsoleSink({})
        with caplog.at_level("INFO"):
            sink.write(batch)
            sink.write_msg(batch)
            sink.write_incidents(batch)
            sink.write_data(batch)
            sink.write_incident_data(batch)
        assert "console-sink" not in caplog.text
