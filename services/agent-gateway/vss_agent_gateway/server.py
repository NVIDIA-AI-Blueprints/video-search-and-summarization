# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HTTP/SSE server for the VSS agent gateway contract."""

from __future__ import annotations

import hmac
import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import GatewayConfig
from .connectors import LegacyChatConnector, ResponsesConnector
from .contract import ContractError, CreateRunRequest
from .service import GatewayService
from .store import (
    EventsExpiredError,
    IdempotencyConflictError,
    RunNotFoundError,
    StoreCapacityError,
    ThreadBusyError,
)

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 5_000_000
RUN_PATH = re.compile(r"^/v1/runs/([^/]+)$")
EVENTS_PATH = re.compile(r"^/v1/runs/([^/]+)/events$")
CANCEL_PATH = re.compile(r"^/v1/runs/([^/]+)/cancel$")
RESPOND_PATH = re.compile(r"^/v1/runs/([^/]+)/respond$")


def build_service(config: GatewayConfig) -> GatewayService:
    if config.backend_protocol == "responses":
        connector = ResponsesConnector(config)
    else:
        connector = LegacyChatConnector(config)
    return GatewayService(config, connector)


class GatewayRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VssAgentGateway/1.0"
    service: GatewayService

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.client_address[0], format % args)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(
        self,
        status: HTTPStatus | int,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self._security_headers()
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(
        self, status: HTTPStatus | int, code: str, message: str, **details: object
    ) -> None:
        error: dict[str, object] = {"code": code, "message": message}
        error.update(details)
        self._json(status, {"error": error})

    def _authorized(self) -> bool:
        expected = self.service.config.gateway_token
        if expected is None:
            return True
        header = self.headers.get("Authorization", "")
        scheme, separator, token = header.partition(" ")
        return bool(
            separator
            and scheme.lower() == "bearer"
            and hmac.compare_digest(token, expected)
        )

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._error(
            HTTPStatus.UNAUTHORIZED,
            "unauthorized",
            "a valid gateway bearer token is required",
        )
        return False

    def _body(self) -> object:
        content_type = (
            self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        )
        if content_type != "application/json":
            raise ContractError("Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ContractError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ContractError("Content-Length must be an integer") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ContractError(
                f"request body must be at most {MAX_REQUEST_BYTES} bytes"
            )
        body = self.rfile.read(length)
        try:
            return json.loads(body or b"{}")
        except json.JSONDecodeError as error:
            raise ContractError("request body must be valid JSON") from error

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if not self._require_auth():
            return
        if parsed.path == "/v1/capabilities":
            self._json(HTTPStatus.OK, self.service.capabilities())
            return

        events_match = EVENTS_PATH.fullmatch(parsed.path)
        if events_match:
            self._events(events_match.group(1), parse_qs(parsed.query))
            return
        run_match = RUN_PATH.fullmatch(parsed.path)
        if run_match:
            try:
                record = self.service.store.get(run_match.group(1))
            except RunNotFoundError:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "run_not_found",
                    "run does not exist or has expired",
                )
                return
            self._json(HTTPStatus.OK, record.snapshot())
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "endpoint does not exist")

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if not self._require_auth():
            return
        if parsed.path == "/v1/runs":
            self._create_run()
            return
        cancel_match = CANCEL_PATH.fullmatch(parsed.path)
        if cancel_match:
            self._cancel(cancel_match.group(1))
            return
        respond_match = RESPOND_PATH.fullmatch(parsed.path)
        if respond_match:
            self._error(
                HTTPStatus.CONFLICT,
                "interaction_not_supported",
                "the active connector does not support interaction responses",
            )
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "endpoint does not exist")

    def _create_run(self) -> None:
        try:
            request = CreateRunRequest.from_dict(self._body())
            record, replayed = self.service.create_run(
                request,
                idempotency_key=self.headers.get("Idempotency-Key"),
            )
        except (ContractError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(error))
            return
        except IdempotencyConflictError as error:
            self._error(HTTPStatus.CONFLICT, "idempotency_key_conflict", str(error))
            return
        except ThreadBusyError as error:
            self._error(
                HTTPStatus.CONFLICT,
                "thread_busy",
                "thread already has an active run",
                active_run_id=error.run_id,
            )
            return
        except StoreCapacityError as error:
            self._error(
                HTTPStatus.SERVICE_UNAVAILABLE, "run_capacity_reached", str(error)
            )
            return

        headers = {"Idempotency-Replayed": "true"} if replayed else None
        self._json(
            HTTPStatus.ACCEPTED,
            {
                **record.snapshot(),
                "events_url": f"/v1/runs/{record.run_id}/events",
                "cancel_url": f"/v1/runs/{record.run_id}/cancel",
            },
            headers,
        )

    def _cancel(self, run_id: str) -> None:
        try:
            record = self.service.cancel_run(run_id)
        except RunNotFoundError:
            self._error(
                HTTPStatus.NOT_FOUND,
                "run_not_found",
                "run does not exist or has expired",
            )
            return
        self._json(HTTPStatus.ACCEPTED, record.snapshot())

    def _events(self, run_id: str, query: dict[str, list[str]]) -> None:
        try:
            record = self.service.store.get(run_id)
        except RunNotFoundError:
            self._error(
                HTTPStatus.NOT_FOUND,
                "run_not_found",
                "run does not exist or has expired",
            )
            return

        raw_after = (
            self.headers.get("Last-Event-ID") or (query.get("after") or ["0"])[0]
        )
        try:
            after = int(raw_after)
            if after < 0:
                raise ValueError
            record.events_after(after)
        except ValueError:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_event_id",
                "Last-Event-ID must be a non-negative integer",
            )
            return
        except EventsExpiredError:
            self._error(
                HTTPStatus.GONE,
                "events_expired",
                "requested events are no longer retained",
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.close_connection = True

        sequence = after
        try:
            while True:
                events, terminal = record.wait_for_events(sequence, timeout=15.0)
                if not events:
                    if terminal:
                        return
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                for event in events:
                    self.wfile.write(event.to_sse())
                    sequence = event.sequence
                self.wfile.flush()
                if terminal and sequence >= record.next_sequence - 1:
                    return
        except (BrokenPipeError, ConnectionResetError):
            return


def make_handler(service: GatewayService) -> type[GatewayRequestHandler]:
    class BoundGatewayRequestHandler(GatewayRequestHandler):
        pass

    BoundGatewayRequestHandler.service = service
    return BoundGatewayRequestHandler


def create_server(config: GatewayConfig) -> ThreadingHTTPServer:
    service = build_service(config)
    server = ThreadingHTTPServer(
        (config.bind_host, config.bind_port), make_handler(service)
    )
    server.daemon_threads = True
    return server
