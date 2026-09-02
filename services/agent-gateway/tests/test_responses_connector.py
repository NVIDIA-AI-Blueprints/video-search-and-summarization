# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from vss_agent_gateway.capabilities import CapabilityReceipt
from vss_agent_gateway.connectors.responses import ResponsesConnector
from vss_agent_gateway.contract import CreateRunRequest

from tests.helpers import FakeResponse, make_config
from tests.test_capabilities import valid_receipt


def response_stream(response_id: str, text: str) -> FakeResponse:
    events = [
        (
            "response.created",
            {"type": "response.created", "response": {"id": response_id}},
        ),
        (
            "response.output_text.delta",
            {"type": "response.output_text.delta", "delta": text},
        ),
        (
            "response.completed",
            {"type": "response.completed", "response": {"id": response_id}},
        ),
    ]
    body = (
        b"".join(
            f"event: {event_type}\ndata: {json.dumps(payload)}\n\n".encode()
            for event_type, payload in events
        )
        + b"data: [DONE]\n\n"
    )
    return FakeResponse(body)


class ResponsesConnectorTest(unittest.TestCase):
    def test_publishes_vss_artifact_with_a_responses_client_tool(self) -> None:
        connector = ResponsesConnector(
            make_config(
                vss_capabilities=CapabilityReceipt.from_payload(valid_receipt())
            )
        )
        requests: list[dict[str, object]] = []
        artifact_arguments = json.dumps(
            {
                "version": "1.0",
                "kind": "vss.search.results",
                "payload": {"data": [], "job_id": "job-1"},
            }
        )
        tool_events = [
            (
                "response.created",
                {"type": "response.created", "response": {"id": "resp_tool"}},
            ),
            (
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "id": "item_publish",
                        "call_id": "call_publish",
                        "name": "vss_ui_publish_artifact",
                    },
                },
            ),
            (
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "id": "item_publish",
                        "call_id": "call_publish",
                        "name": "vss_ui_publish_artifact",
                        "status": "completed",
                        "arguments": artifact_arguments,
                    },
                },
            ),
            (
                "response.completed",
                {"type": "response.completed", "response": {"id": "resp_tool"}},
            ),
        ]
        first_response = FakeResponse(
            b"".join(
                f"event: {event_type}\ndata: {json.dumps(payload)}\n\n".encode()
                for event_type, payload in tool_events
            )
            + b"data: [DONE]\n\n"
        )
        responses = iter([first_response, response_stream("resp_final", "Done")])

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            requests.append(json.loads(request.data))  # type: ignore[attr-defined]
            return next(responses)

        request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "search"}],
            }
        )
        with patch("urllib.request.urlopen", side_effect=open_response):
            events = list(
                connector.run(request, run_id="run_1", cancel_event=threading.Event())
            )

        self.assertEqual(
            [event.type for event in events],
            ["tool.started", "tool.requested", "tool.completed", "message.delta"],
        )
        self.assertIn(
            "<vss-ui-artifact>",
            str(events[2].data["output"]),
        )
        self.assertEqual(events[-1].data, {"delta": "Done"})
        self.assertEqual(
            requests[0]["tools"][0]["name"],  # type: ignore[index]
            "vss_ui_publish_artifact",
        )
        self.assertIn(
            "must call that tool exactly once",
            str(requests[0]["instructions"]),
        )
        self.assertEqual(requests[1]["previous_response_id"], "resp_tool")
        follow_up = requests[1]["input"][0]  # type: ignore[index]
        self.assertEqual(follow_up["type"], "function_call_output")
        self.assertEqual(follow_up["call_id"], "call_publish")
        self.assertNotIn("vss-ui-artifact", str(follow_up["output"]))

    def test_uses_history_once_then_previous_response_for_matching_transcript(
        self,
    ) -> None:
        connector = ResponsesConnector(
            make_config(backend_session_header="X-Session-Key")
        )
        requests: list[dict[str, object]] = []
        headers: list[dict[str, str]] = []

        responses = iter(
            [response_stream("resp_1", "Hello"), response_stream("resp_2", "Again")]
        )

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            requests.append(json.loads(request.data))  # type: ignore[attr-defined]
            headers.append(dict(request.header_items()))  # type: ignore[attr-defined]
            return next(responses)

        first = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "Hi"}],
                "history": [{"role": "user", "content": "Hi"}],
            },
        )
        second = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "Next"}],
                "history": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello"},
                    {"role": "user", "content": "Next"},
                ],
            },
        )

        with patch("urllib.request.urlopen", side_effect=open_response):
            first_events = list(
                connector.run(first, run_id="run_1", cancel_event=threading.Event())
            )
            second_events = list(
                connector.run(second, run_id="run_2", cancel_event=threading.Event())
            )

        self.assertEqual(first_events[0].data, {"delta": "Hello"})
        self.assertNotIn("previous_response_id", requests[0])
        self.assertEqual(len(requests[0]["input"]), 1)  # type: ignore[arg-type]
        self.assertNotIn("tools", requests[0])
        self.assertNotIn("instructions", requests[0])
        self.assertEqual(requests[1]["previous_response_id"], "resp_1")
        self.assertEqual(len(requests[1]["input"]), 1)  # type: ignore[arg-type]
        self.assertEqual(requests[1]["input"][0]["content"][0]["text"], "Next")  # type: ignore[index]
        self.assertEqual(second_events[0].data, {"delta": "Again"})
        self.assertEqual(headers[0]["Authorization"], "Bearer backend-secret")
        self.assertTrue(headers[0]["X-session-key"].startswith("vss-ui:"))

    def test_changed_history_starts_a_new_upstream_chain(self) -> None:
        connector = ResponsesConnector(make_config())
        requests: list[dict[str, object]] = []

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            requests.append(json.loads(request.data))  # type: ignore[attr-defined]
            return response_stream(f"resp_{len(requests)}", "answer")

        first = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "original"}],
                "history": [{"role": "user", "content": "original"}],
            },
        )
        changed = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "next"}],
                "history": [
                    {"role": "user", "content": "edited"},
                    {"role": "assistant", "content": "answer"},
                    {"role": "user", "content": "next"},
                ],
            },
        )
        with patch("urllib.request.urlopen", side_effect=open_response):
            list(connector.run(first, run_id="run_1", cancel_event=threading.Event()))
            list(connector.run(changed, run_id="run_2", cancel_event=threading.Event()))

        self.assertNotIn("previous_response_id", requests[1])
        self.assertEqual(len(requests[1]["input"]), 3)  # type: ignore[arg-type]

    def test_continues_saved_chain_when_client_history_is_disabled(self) -> None:
        connector = ResponsesConnector(make_config())
        requests: list[dict[str, object]] = []

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            requests.append(json.loads(request.data))  # type: ignore[attr-defined]
            return response_stream(f"resp_{len(requests)}", "answer")

        first = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "first"}]},
        )
        second = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "second"}]},
        )
        with patch("urllib.request.urlopen", side_effect=open_response):
            list(connector.run(first, run_id="run_1", cancel_event=threading.Event()))
            list(connector.run(second, run_id="run_2", cancel_event=threading.Event()))

        self.assertEqual(requests[1]["previous_response_id"], "resp_1")
        self.assertEqual(requests[1]["input"][0]["content"][0]["text"], "second")  # type: ignore[index]

    def test_recovery_history_may_exclude_current_input(self) -> None:
        connector = ResponsesConnector(make_config())
        captured: dict[str, object] = {}

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            captured.update(json.loads(request.data))  # type: ignore[attr-defined]
            return response_stream("resp_1", "answer")

        request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "next"}],
                "history": [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "answer"},
                ],
            },
        )
        with patch("urllib.request.urlopen", side_effect=open_response):
            list(connector.run(request, run_id="run_1", cancel_event=threading.Event()))

        texts = [item["content"][0]["text"] for item in captured["input"]]  # type: ignore[index]
        self.assertEqual(texts, ["first", "answer", "next"])

    def test_bounds_saved_thread_transcripts_with_lru_eviction(self) -> None:
        connector = ResponsesConnector(
            make_config(max_runs=10, max_thread_state_chars=20)
        )
        requests: list[dict[str, object]] = []

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            requests.append(json.loads(request.data))  # type: ignore[attr-defined]
            return response_stream(f"resp_{len(requests)}", "answer")

        def turn(thread_id: str, content: str, run_id: str) -> None:
            request = CreateRunRequest.from_dict(
                {
                    "thread_id": thread_id,
                    "input": [{"role": "user", "content": content}],
                },
            )
            list(
                connector.run(
                    request,
                    run_id=run_id,
                    cancel_event=threading.Event(),
                )
            )

        with patch("urllib.request.urlopen", side_effect=open_response):
            turn("thread-1", "first-one", "run_1")
            turn("thread-2", "second-one", "run_2")
            turn("thread-1", "first-two", "run_3")

        self.assertNotIn("previous_response_id", requests[2])

    def test_does_not_retain_an_oversized_response_for_continuity(self) -> None:
        connector = ResponsesConnector(make_config(max_thread_state_chars=10))
        requests: list[dict[str, object]] = []

        def open_response(request: object, **_kwargs: object) -> FakeResponse:
            requests.append(json.loads(request.data))  # type: ignore[attr-defined]
            return response_stream(f"resp_{len(requests)}", "long-response")

        first = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "one"}]}
        )
        second = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "two"}]}
        )
        with patch("urllib.request.urlopen", side_effect=open_response):
            list(connector.run(first, run_id="run_1", cancel_event=threading.Event()))
            list(connector.run(second, run_id="run_2", cancel_event=threading.Event()))

        self.assertNotIn("previous_response_id", requests[1])

    def test_maps_backend_executed_function_call_and_output(self) -> None:
        connector = ResponsesConnector(make_config())
        payloads = [
            (
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "id": "item_1",
                        "call_id": "call_1",
                        "name": "lookup",
                    },
                },
            ),
            (
                "response.function_call_arguments.delta",
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "item_1",
                    "delta": '{"q":"x"}',
                },
            ),
            (
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "id": "item_1",
                        "call_id": "call_1",
                        "name": "lookup",
                        "status": "completed",
                        "arguments": '{"q":"x"}',
                    },
                },
            ),
            (
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "status": "completed",
                        "output": "result",
                    },
                },
            ),
            (
                "response.completed",
                {"type": "response.completed", "response": {"id": "resp_1"}},
            ),
        ]
        body = (
            b"".join(
                f"event: {event_type}\ndata: {json.dumps(payload)}\n\n".encode()
                for event_type, payload in payloads
            )
            + b"data: [DONE]\n\n"
        )
        request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "call it"}],
            },
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            events = list(
                connector.run(request, run_id="run_1", cancel_event=threading.Event())
            )

        self.assertEqual(
            [event.type for event in events],
            [
                "tool.started",
                "tool.arguments.delta",
                "tool.completed",
                "tool.completed",
            ],
        )
        self.assertEqual(events[-2].data["arguments"], '{"q":"x"}')
        self.assertEqual(events[-1].data["output"], "result")


if __name__ == "__main__":
    unittest.main()
