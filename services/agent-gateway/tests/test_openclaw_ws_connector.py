# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import websocket
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vss_agent_gateway.connectors.base import ConnectorError
from vss_agent_gateway.connectors.openclaw_ws import OpenClawWebSocketConnector
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
            raise websocket.WebSocketConnectionClosedException("fixture exhausted")
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
                "scopes": ["operator.read", "operator.write"],
            },
            "features": {
                "methods": ["chat.send", "chat.abort"],
                "events": ["chat", "agent", "session.tool"],
            },
        },
    }


def _hello_with_device_token(socket: FakeWebSocket) -> dict[str, object]:
    frame = _hello(socket)
    payload = frame["payload"]
    assert isinstance(payload, dict)
    auth = payload["auth"]
    assert isinstance(auth, dict)
    auth["deviceToken"] = "paired-device-token"
    return frame


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
        self.temporary = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temporary.name) / "state"
        self.config = make_config(
            backend_protocol="openclaw-ws",
            backend_url="ws://openclaw.test",
            backend_path="/",
            backend_state_dir=str(self.state_dir),
        )
        self.request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "find the delivery truck"}],
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

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

    def test_maps_native_tool_events_and_signs_the_challenge(self) -> None:
        socket = self._socket_with_tool_run()
        with mock.patch(
            "vss_agent_gateway.connectors.openclaw_ws.websocket.create_connection",
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
        device = params["device"]
        client = params["client"]
        auth = params["auth"]
        assert isinstance(device, dict)
        assert isinstance(client, dict)
        assert isinstance(auth, dict)
        payload = "|".join(
            (
                "v3",
                str(device["id"]),
                str(client["id"]),
                str(client["mode"]),
                "operator",
                "operator.read,operator.write",
                str(device["signedAt"]),
                str(auth["token"]),
                str(device["nonce"]),
                str(client["platform"]),
                str(client["deviceFamily"]),
            )
        )

        def decode(value: object) -> bytes:
            encoded = str(value)
            return base64.urlsafe_b64decode(
                encoded + "=" * ((4 - len(encoded) % 4) % 4)
            )

        Ed25519PublicKey.from_public_bytes(decode(device["publicKey"])).verify(
            decode(device["signature"]), payload.encode()
        )
        self.assertEqual(self.state_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            (self.state_dir / "openclaw-device.json").stat().st_mode & 0o777,
            0o600,
        )

    def test_service_extracts_artifact_without_exposing_raw_tool_result(self) -> None:
        socket = self._socket_with_tool_run()
        with mock.patch(
            "vss_agent_gateway.connectors.openclaw_ws.websocket.create_connection",
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

    def test_persists_and_reuses_issued_device_token_as_auth_token(self) -> None:
        first = FakeWebSocket(
            [
                _challenge(),
                _hello_with_device_token,
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
            "vss_agent_gateway.connectors.openclaw_ws.websocket.create_connection",
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

        state = json.loads(
            (self.state_dir / "openclaw-device.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["device_token"], "paired-device-token")
        first_auth = first.request("connect")["params"]["auth"]  # type: ignore[index]
        second_auth = second.request("connect")["params"]["auth"]  # type: ignore[index]
        self.assertEqual(first_auth, {"token": self.config.backend_token})
        self.assertEqual(second_auth, {"token": "paired-device-token"})

    def test_pairing_required_is_an_actionable_structured_error(self) -> None:
        def rejected(socket: FakeWebSocket) -> dict[str, object]:
            request = socket.request("connect")
            return {
                "type": "res",
                "id": request["id"],
                "ok": False,
                "error": {
                    "code": "NOT_PAIRED",
                    "message": "secret backend detail",
                    "details": {
                        "code": "PAIRING_REQUIRED",
                        "requestId": "pair-request-1",
                    },
                },
            }

        socket = FakeWebSocket([_challenge(), rejected])
        with (
            mock.patch(
                "vss_agent_gateway.connectors.openclaw_ws.websocket.create_connection",
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

        self.assertEqual(raised.exception.code, "backend_pairing_required")
        self.assertIn("pair-request-1", str(raised.exception))
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
                raise websocket.WebSocketConnectionClosedException("closed")

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
            except BaseException as error:  # pragma: no cover - asserted below
                failures.append(error)

        with mock.patch(
            "vss_agent_gateway.connectors.openclaw_ws.websocket.create_connection",
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
