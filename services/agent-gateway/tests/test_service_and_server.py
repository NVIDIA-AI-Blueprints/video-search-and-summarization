# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

from tests.helpers import make_config
from vss_agent_gateway.connectors.base import Connector
from vss_agent_gateway.contract import ConnectorEvent, CreateRunRequest
from vss_agent_gateway.server import make_handler
from vss_agent_gateway.service import GatewayService
from vss_agent_gateway.store import IdempotencyConflictError


class FakeConnector(Connector):
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    @property
    def protocol(self) -> str:
        return "fake"

    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: threading.Event,
    ) -> Iterator[ConnectorEvent]:
        del request, run_id, cancel_event
        yield ConnectorEvent(
            "tool.started", {"tool_call_id": "tool_1", "name": "search"}
        )
        yield ConnectorEvent("message.delta", {"delta": "done"})

    def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)


class BlockingConnector(FakeConnector):
    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: threading.Event,
    ) -> Iterator[ConnectorEvent]:
        del request, run_id
        cancel_event.wait(timeout=2)
        if False:
            yield ConnectorEvent("message.delta", {"delta": "unreachable"})


def wait_terminal(service: GatewayService, run_id: str) -> None:
    record = service.store.get(run_id)
    with record.condition:
        if not record.terminal:
            record.condition.wait_for(lambda: record.terminal, timeout=2)


class GatewayServiceTest(unittest.TestCase):
    def test_cancel_interrupts_connector_and_ends_with_cancelled_event(self) -> None:
        connector = BlockingConnector()
        service = GatewayService(make_config(), connector)
        request = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "wait"}]},
        )
        record, _ = service.create_run(request, idempotency_key=None)

        service.cancel_run(record.run_id)
        wait_terminal(service, record.run_id)

        self.assertIn(record.run_id, connector.cancelled)
        self.assertEqual(record.status, "cancelled")
        self.assertEqual(record.events[-1].type, "run.cancelled")

    def test_replays_idempotent_creation_and_records_terminal_events(self) -> None:
        service = GatewayService(make_config(), FakeConnector())
        request = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "hi"}]},
        )
        record, replayed = service.create_run(request, idempotency_key="message-1")
        wait_terminal(service, record.run_id)
        replay, was_replayed = service.create_run(request, idempotency_key="message-1")

        self.assertFalse(replayed)
        self.assertTrue(was_replayed)
        self.assertIs(replay, record)
        self.assertEqual(
            [event.type for event in record.events],
            ["run.started", "tool.started", "message.delta", "run.completed"],
        )

        changed = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "different"}],
            },
        )
        with self.assertRaises(IdempotencyConflictError):
            service.create_run(changed, idempotency_key="message-1")

    def test_http_surface_authenticates_and_replays_sse(self) -> None:
        config = make_config(gateway_token="gateway-secret")
        service = GatewayService(config, FakeConnector())
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                urllib.request.urlopen(f"{base}/v1/capabilities")
            self.assertEqual(unauthorized.exception.code, 401)

            body = json.dumps(
                {"thread_id": "thread-1", "input": [{"role": "user", "content": "hi"}]},
            ).encode()
            create = urllib.request.Request(
                f"{base}/v1/runs",
                data=body,
                method="POST",
                headers={
                    "Authorization": "Bearer gateway-secret",
                    "Content-Type": "application/json",
                    "Idempotency-Key": "message-1",
                },
            )
            with urllib.request.urlopen(create) as response:
                self.assertEqual(response.status, 202)
                created = json.load(response)
            wait_terminal(service, created["run_id"])

            events = urllib.request.Request(
                f"{base}{created['events_url']}",
                headers={
                    "Authorization": "Bearer gateway-secret",
                    "Last-Event-ID": "1",
                },
            )
            with urllib.request.urlopen(events) as response:
                stream = response.read().decode()
            self.assertNotIn("event: run.started", stream)
            self.assertIn("event: message.delta", stream)
            self.assertIn("event: run.completed", stream)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
