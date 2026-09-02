# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from tests.helpers import FakeResponse, make_config
from vss_agent_gateway.connectors.legacy_chat import LegacyChatConnector
from vss_agent_gateway.contract import CreateRunRequest


class LegacyChatConnectorTest(unittest.TestCase):
    def test_maps_existing_vss_stream_and_keeps_session_opaque(self) -> None:
        connector = LegacyChatConnector(
            make_config(backend_protocol="legacy-chat", backend_path="/chat/stream")
        )
        body = (
            b'intermediate_data: {"id":"step_1","name":"search","status":"in_progress","payload":"query"}\n'
            b'data: {"choices":[{"delta":{"content":"found"}}]}\n\n'
            b'intermediate_data: {"id":"step_1","name":"search","status":"complete","payload":"done"}\n'
            b"data: [DONE]\n\n"
        )
        captured: dict[str, object] = {}

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            captured["payload"] = json.loads(request.data)  # type: ignore[attr-defined]
            captured["headers"] = dict(request.header_items())  # type: ignore[attr-defined]
            return FakeResponse(body)

        request = CreateRunRequest.from_dict(
            {
                "thread_id": "private-thread-id",
                "input": [{"role": "user", "content": "next"}],
                "history": [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "answer"},
                    {"role": "user", "content": "next"},
                ],
            }
        )
        with patch("urllib.request.urlopen", side_effect=open_response):
            events = list(
                connector.run(request, run_id="run_1", cancel_event=threading.Event())
            )

        self.assertEqual(
            [event.type for event in events],
            ["tool.started", "message.delta", "tool.completed"],
        )
        self.assertEqual(events[1].data, {"delta": "found"})
        self.assertEqual(len(captured["payload"]["messages"]), 3)  # type: ignore[index]
        headers = captured["headers"]
        self.assertNotEqual(headers["Conversation-id"], "private-thread-id")  # type: ignore[index]
        self.assertEqual(headers["Authorization"], "Bearer backend-secret")  # type: ignore[index]

    def test_history_without_current_input_is_combined(self) -> None:
        connector = LegacyChatConnector(
            make_config(backend_protocol="legacy-chat", backend_path="/chat/stream")
        )
        captured: dict[str, object] = {}

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            captured.update(json.loads(request.data))  # type: ignore[attr-defined]
            return FakeResponse(b"data: [DONE]\n\n")

        request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "next"}],
                "history": [{"role": "assistant", "content": "answer"}],
            }
        )
        with patch("urllib.request.urlopen", side_effect=open_response):
            list(connector.run(request, run_id="run_1", cancel_event=threading.Event()))

        self.assertEqual(
            captured["messages"],
            [
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "next"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
