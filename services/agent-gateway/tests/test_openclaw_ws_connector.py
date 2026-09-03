# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Callable
from unittest import mock

from vss_agent_gateway.connectors.base import ConnectorError
from vss_agent_gateway.connectors.openclaw_ws import OpenClawWebSocketConnector
from vss_agent_gateway.connectors.websocket_client import (
    WebSocketConnectionClosedError,
)
from vss_agent_gateway.contract import CreateRunRequest
from vss_agent_gateway.service import GatewayService

from tests.helpers import make_config
from tests.test_service_and_server import wait_terminal

FrameFactory = Callable[["FakeWebSocket"], dict[str, object]]


def _encoded(value: dict[str, object]) -> str:
    return json.dumps(value, separators=(",", ":"))


class FakeWebSocket:
    def __init__(self, frames: list[dict[str, object] | FrameFactory]) -> None:
        self.frames = list(frames)
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def recv(self) -> str:
        if not self.frames:
            raise WebSocketConnectionClosedError("fixture exhausted")
        frame = self.frames.pop(0)
        if callable(frame):
            frame = frame(self)
        return _encoded(frame)

    def close(self) -> None:
        self.closed = True

    def request(self, method: str) -> dict[str, object]:
        return next(
            item for item in reversed(self.sent) if item.get("method") == method
        )


def _challenge() -> dict[str, object]:
    return {
        "type": "event",
        "event": "connect.challenge",
        "payload": {"nonce": "challenge-nonce", "ts": 1_777_777_777_777},
    }


def _hello(socket: FakeWebSocket) -> dict[str, object]:
    request = socket.request("connect")
    return {
        "type": "res",
        "id": request["id"],
        "ok": True,
        "payload": {
            "type": "hello-ok",
            "protocol": 4,
            "auth": {
                "role": "operator",
                # Shared-token backend clients are authorized by their token;
                # current OpenClaw clears unbound device scopes in hello-ok.
                "scopes": [],
            },
            "features": {
                "methods": ["chat.send", "chat.abort"],
                "events": ["chat", "agent", "session.tool"],
            },
        },
    }


def _chat_values(socket: FakeWebSocket) -> tuple[str, str]:
    request = socket.request("chat.send")
    params = request["params"]
    assert isinstance(params, dict)
    return str(params["sessionKey"]), str(params["idempotencyKey"])


def _chat_ack(socket: FakeWebSocket) -> dict[str, object]:
    request = socket.request("chat.send")
    _, run_id = _chat_values(socket)
    return {
        "type": "res",
        "id": request["id"],
        "ok": True,
        "payload": {"runId": run_id, "status": "started"},
    }


def _event(
    socket: FakeWebSocket,
    event: str,
    payload: dict[str, object],
) -> dict[str, object]:
    session_key, run_id = _chat_values(socket)
    return {
        "type": "event",
        "event": event,
        "payload": {"sessionKey": session_key, "runId": run_id, **payload},
    }


class OpenClawWebSocketConnectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = make_config(
            backend_protocol="openclaw-ws",
            backend_url="ws://openclaw.test",
            backend_path="/",
        )
        self.request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "find the delivery truck"}],
            }
        )

    def _socket_with_tool_run(self) -> FakeWebSocket:
        artifact = (
            '<vss-ui-artifact>{"version":"1.0",'
            '"kind":"vss.search.results","payload":{"data":[]}}'
            "</vss-ui-artifact>"
        )
        return FakeWebSocket(
            [
                _challenge(),
                _hello,
                # The Gateway may emit events before acknowledging chat.send.
                lambda ws: _event(
                    ws,
                    "agent",
                    {
                        "stream": "tool",
                        "seq": 1,
                        "data": {
                            "phase": "start",
                            "name": "exec",
                            "toolCallId": "call-1",
                            "args": {"command": "must-not-reach-the-browser"},
                        },
                    },
                ),
                _chat_ack,
                lambda ws: _event(
                    ws,
                    "agent",
                    {
                        "stream": "tool",
                        "seq": 2,
                        "data": {
                            "phase": "update",
                            "name": "exec",
                            "toolCallId": "call-1",
                            "partialResult": "private command output",
                        },
                    },
                ),
                # An unrelated run on the same Gateway must not bleed into this UI run.
                lambda ws: {
                    **_event(
                        ws,
                        "agent",
                        {
                            "stream": "tool",
                            "data": {
                                "phase": "start",
                                "name": "other",
                                "toolCallId": "other-call",
                            },
                        },
                    ),
                    "payload": {
                        **_event(ws, "agent", {})["payload"],
                        "runId": "another-run",
                        "stream": "tool",
                        "data": {
                            "phase": "start",
                            "name": "other",
                            "toolCallId": "other-call",
                        },
                    },
                },
                lambda ws: _event(
                    ws,
                    "agent",
                    {
                        "stream": "tool",
                        "seq": 3,
                        "data": {
                            "phase": "result",
                            "name": "exec",
                            "toolCallId": "call-1",
                            "result": {"content": [{"type": "text", "text": artifact}]},
                            "isError": False,
                        },
                    },
                ),
                # Newer Gateways can also project the same result as session.tool.
                lambda ws: _event(
                    ws,
                    "session.tool",
                    {
                        "data": {
                            "phase": "result",
                            "name": "exec",
                            "toolCallId": "call-1",
                            "result": "duplicate private result",
                        }
                    },
                ),
                lambda ws: _event(
                    ws,
                    "session.tool",
                    {
                        "data": {
                            "phase": "result",
                            "name": "search",
                            "toolCallId": "call-2",
                            "result": "private failure",
                            "isError": True,
                        }
                    },
                ),
                lambda ws: _event(
                    ws, "chat", {"state": "delta", "deltaText": "Found it."}
                ),
                lambda ws: _event(ws, "chat", {"state": "final"}),
            ]
        )

    def test_maps_native_tool_events_with_backend_token_auth(self) -> None:
        socket = self._socket_with_tool_run()
        with mock.patch(
            "vss_agent_gateway.connectors.openclaw_ws.connect_websocket",
            return_value=socket,
        ):
            connector = OpenClawWebSocketConnector(self.config)
            events = list(
                connector.run(
                    self.request,
                    run_id="run-1",
                    cancel_event=threading.Event(),
                )
            )

        self.assertEqual(
            [event.type for event in events],
            [
                "tool.started",
                "tool.completed",
                "tool.started",
                "tool.failed",
                "message.delta",
            ],
        )
        self.assertEqual(events[0].data["name"], "exec")
        self.assertNotIn("args", events[0].data)
        self.assertNotIn("partialResult", str(events))
        self.assertEqual(events[-1].data["delta"], "Found it.")

        connect = socket.request("connect")
        params = connect["params"]
        assert isinstance(params, dict)
        self.assertEqual(params["caps"], ["tool-events", "session-scoped-events"])
        client = params["client"]
        auth = params["auth"]
        assert isinstance(client, dict)
        assert isinstance(auth, dict)
        self.assertEqual(client["id"], "gateway-client")
        self.assertEqual(client["mode"], "backend")
        self.assertEqual(auth, {"token": self.config.backend_token})
        self.assertNotIn("device", params)

    def test_service_extracts_artifact_without_exposing_raw_tool_result(self) -> None:
        socket = self._socket_with_tool_run()
        with mock.patch(
            "vss_agent_gateway.connectors.openclaw_ws.connect_websocket",
            return_value=socket,
        ):
            service = GatewayService(
                self.config, OpenClawWebSocketConnector(self.config)
            )
            record, _ = service.create_run(self.request, idempotency_key=None)
            wait_terminal(service, record.run_id)

        types = [event.type for event in record.events]
        self.assertIn("artifact.created", types)
        completed = next(
            event
            for event in record.events
            if event.type == "tool.completed"
            and event.data.get("tool_call_id") == "call-1"
        )
        self.assertNotIn("_artifact_source", completed.data)
        self.assertNotIn("content", completed.data)
        self.assertNotIn("must-not-reach-the-browser", str(record.events))

    def test_uses_configured_backend_token_on_every_connection(self) -> None:
        first = FakeWebSocket(
            [
                _challenge(),
                _hello,
                _chat_ack,
                lambda ws: _event(ws, "chat", {"state": "final"}),
            ]
        )
        second = FakeWebSocket(
            [
                _challenge(),
                _hello,
                _chat_ack,
                lambda ws: _event(ws, "chat", {"state": "final"}),
            ]
        )
        with mock.patch(
            "vss_agent_gateway.connectors.openclaw_ws.connect_websocket",
            side_effect=[first, second],
        ):
            connector = OpenClawWebSocketConnector(self.config)
            list(
                connector.run(
                    self.request,
                    run_id="run-1",
                    cancel_event=threading.Event(),
                )
            )
            reloaded = OpenClawWebSocketConnector(self.config)
            list(
                reloaded.run(
                    self.request,
                    run_id="run-2",
                    cancel_event=threading.Event(),
                )
            )

        first_auth = first.request("connect")["params"]["auth"]  # type: ignore[index]
        second_auth = second.request("connect")["params"]["auth"]  # type: ignore[index]
        self.assertEqual(first_auth, {"token": self.config.backend_token})
        self.assertEqual(second_auth, {"token": self.config.backend_token})

    def test_device_identity_requirement_explains_local_backend_mode(self) -> None:
        def rejected(socket: FakeWebSocket) -> dict[str, object]:
            request = socket.request("connect")
            return {
                "type": "res",
                "id": request["id"],
                "ok": False,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "secret backend detail",
                    "details": {
                        "code": "DEVICE_IDENTITY_REQUIRED",
                    },
                },
            }

        socket = FakeWebSocket([_challenge(), rejected])
        with (
            mock.patch(
                "vss_agent_gateway.connectors.openclaw_ws.connect_websocket",
                return_value=socket,
            ),
            self.assertRaises(ConnectorError) as raised,
        ):
            list(
                OpenClawWebSocketConnector(self.config).run(
                    self.request,
                    run_id="run-1",
                    cancel_event=threading.Event(),
                )
            )

        self.assertEqual(raised.exception.code, "backend_auth_error")
        self.assertIn("trusted local or loopback route", str(raised.exception))
        self.assertNotIn("secret backend detail", str(raised.exception))

    def test_cancel_sends_native_chat_abort_and_closes_socket(self) -> None:
        waiting = threading.Event()

        class BlockingSocket(FakeWebSocket):
            def recv(self) -> str:
                if self.frames:
                    return super().recv()
                waiting.set()
                while not self.closed:
                    threading.Event().wait(0.01)
                raise WebSocketConnectionClosedError("closed")

        socket = BlockingSocket([_challenge(), _hello, _chat_ack])
        connector = OpenClawWebSocketConnector(self.config)
        cancel_event = threading.Event()
        failures: list[BaseException] = []

        def consume() -> None:
            try:
                list(
                    connector.run(
                        self.request,
                        run_id="run-1",
                        cancel_event=cancel_event,
                    )
                )
            except Exception as error:  # noqa: BLE001  # pragma: no cover
                failures.append(error)

        with mock.patch(
            "vss_agent_gateway.connectors.openclaw_ws.connect_websocket",
            return_value=socket,
        ):
            worker = threading.Thread(target=consume)
            worker.start()
            self.assertTrue(waiting.wait(timeout=1))
            cancel_event.set()
            connector.cancel("run-1")
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        abort = socket.request("chat.abort")
        self.assertEqual(abort["params"]["runId"], "run-1")  # type: ignore[index]
        self.assertTrue(socket.closed)


if __name__ == "__main__":
    unittest.main()
