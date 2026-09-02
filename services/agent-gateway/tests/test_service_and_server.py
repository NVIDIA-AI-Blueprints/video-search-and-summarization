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
from vss_agent_gateway.store import (
    EventsExpiredError,
    IdempotencyConflictError,
    RunStore,
)


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


class ArtifactConnector(FakeConnector):
    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: threading.Event,
    ) -> Iterator[ConnectorEvent]:
        del request, run_id, cancel_event
        yield ConnectorEvent("message.delta", {"delta": "Found "})
        yield ConnectorEvent("message.delta", {"delta": "<vss-ui-art"})
        yield ConnectorEvent(
            "message.delta",
            {
                "delta": 'ifact>{"version":"1.0","kind":"vss.search.results",'
                '"payload":{"data":[]}}</vss-ui-artifact> results'
            },
        )


class ToolArtifactConnector(FakeConnector):
    def run(
        self,
        request: CreateRunRequest,
        *,
        run_id: str,
        cancel_event: threading.Event,
    ) -> Iterator[ConnectorEvent]:
        del request, run_id, cancel_event
        artifact = (
            '<vss-ui-artifact>{"version":"1.0",'
            '"kind":"vss.alert.incidents","payload":{"incidents":[]}}'
            "</vss-ui-artifact>"
        )
        yield ConnectorEvent(
            "tool.completed",
            {"tool_call_id": "tool_1", "name": "exec", "output": artifact},
        )
        # Some harnesses expose the tool output and also copy it into final text.
        yield ConnectorEvent("message.delta", {"delta": artifact})


def wait_terminal(service: GatewayService, run_id: str) -> None:
    record = service.store.get(run_id)
    with record.condition:
        if not record.terminal:
            record.condition.wait_for(lambda: record.terminal, timeout=2)


class GatewayServiceTest(unittest.TestCase):
    def test_capabilities_advertise_connector_neutral_artifacts(self) -> None:
        capabilities = GatewayService(make_config(), FakeConnector()).capabilities()

        self.assertTrue(capabilities["features"]["artifacts"])
        self.assertTrue(capabilities["connector"]["artifacts"])
        self.assertEqual(
            capabilities["artifact_protocol"]["kinds"],
            ["vss.search.results", "vss.alert.incidents"],
        )

    def test_run_store_bounds_replay_by_serialized_event_size(self) -> None:
        store = RunStore(
            retention_seconds=60,
            max_runs=10,
            max_events_per_run=100,
            max_event_chars_per_run=30,
        )
        request = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "hi"}]},
        )
        record, _ = store.create(request, idempotency_key=None)
        record.append("message.delta", {"delta": "1234567890"})
        record.append("message.delta", {"delta": "abcdefghij"})

        with self.assertRaises(EventsExpiredError):
            record.events_after(0)
        self.assertEqual(record.events_after(1)[0].data["delta"], "abcdefghij")

    def test_normalizes_agent_text_artifacts_for_every_connector(self) -> None:
        service = GatewayService(make_config(), ArtifactConnector())
        request = CreateRunRequest.from_dict(
            {"thread_id": "thread-1", "input": [{"role": "user", "content": "find"}]},
        )
        record, _ = service.create_run(request, idempotency_key=None)
        wait_terminal(service, record.run_id)

        self.assertEqual(
            [event.type for event in record.events],
            [
                "run.started",
                "message.delta",
                "artifact.created",
                "message.delta",
                "run.completed",
            ],
        )
        self.assertEqual(record.events[1].data["delta"], "Found ")
        self.assertEqual(record.events[2].data["kind"], "vss.search.results")
        self.assertEqual(record.events[3].data["delta"], " results")

    def test_extracts_tool_output_artifacts_without_duplicate_final_event(self) -> None:
        service = GatewayService(make_config(), ToolArtifactConnector())
        request = CreateRunRequest.from_dict(
            {
                "thread_id": "thread-1",
                "input": [{"role": "user", "content": "show alerts"}],
            },
        )
        record, _ = service.create_run(request, idempotency_key=None)
        wait_terminal(service, record.run_id)

        self.assertEqual(
            [event.type for event in record.events],
            [
                "run.started",
                "tool.completed",
                "artifact.created",
                "run.completed",
            ],
        )
        self.assertEqual(record.events[2].data["kind"], "vss.alert.incidents")

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
