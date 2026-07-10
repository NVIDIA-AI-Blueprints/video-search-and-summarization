# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""In-process HTTP receiver used by webhook notification BDD tests."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, cast
from urllib.parse import parse_qs, urlsplit

MAX_WEBHOOK_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CapturedWebhookRequest:
    """One HTTP request received from VIOS."""

    sequence: int
    received_at: float
    method: str
    raw_target: str
    path: str
    query: Dict[str, List[str]]
    headers: Dict[str, str]
    body: bytes
    json_body: Optional[Any]

    def header(self, name: str) -> Optional[str]:
        """Return a request header using case-insensitive name matching."""
        expected = name.lower()
        for header_name, value in self.headers.items():
            if header_name.lower() == expected:
                return value
        return None

    def summary(self) -> str:
        """Return a compact representation suitable for assertion failures."""
        event = self.json_body.get("event", {}) if isinstance(self.json_body, dict) else {}
        return (
            f"#{self.sequence} {self.method} {self.raw_target} "
            f"camera_id={event.get('camera_id')!r} change={event.get('change')!r}"
        )


class _WebhookHttpServer(ThreadingHTTPServer):
    """Threading HTTP server carrying a reference to its receiver."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], receiver: "WebhookReceiver") -> None:
        self.receiver = receiver
        super().__init__(address, _WebhookRequestHandler)


class _WebhookRequestHandler(BaseHTTPRequestHandler):
    """Capture the webhook request and acknowledge it with HTTP 200."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._capture()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._capture()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._capture()

    def _capture(self) -> None:
        content_length = self.headers.get("Content-Length", "0")
        try:
            length = int(content_length)
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if length < 0:
            self.send_error(400, "Invalid Content-Length")
            return
        if length > MAX_WEBHOOK_BODY_BYTES:
            self.send_error(413, "Webhook payload too large")
            return

        body = self.rfile.read(length) if length else b""
        server = cast(_WebhookHttpServer, self.server)
        server.receiver.record(
            method=self.command,
            raw_target=self.path,
            headers=dict(self.headers.items()),
            body=body,
        )

        response = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        """Suppress the default stderr access log; pytest logs assertions."""


class WebhookReceiver:
    """Background HTTP receiver with lossless wait-for-request semantics."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._condition = threading.Condition()
        self._requests: List[CapturedWebhookRequest] = []
        self._next_sequence = 0
        self._server: Optional[_WebhookHttpServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Bind the configured address and run the HTTP server in a daemon thread."""
        if self._server is not None:
            return
        self._server = _WebhookHttpServer((self.host, self.port), self)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="bdd-webhook-receiver",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop accepting requests and wait for the server thread to exit."""
        server = self._server
        thread = self._thread
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._server = None
        self._thread = None

    def record(
        self,
        method: str,
        raw_target: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> CapturedWebhookRequest:
        """Store one request and wake every waiter."""
        target = urlsplit(raw_target)
        parsed_body: Optional[Any] = None
        if body:
            try:
                parsed_body = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_body = None

        with self._condition:
            captured = CapturedWebhookRequest(
                sequence=self._next_sequence,
                received_at=time.monotonic(),
                method=method,
                raw_target=raw_target,
                path=target.path,
                query=parse_qs(target.query, keep_blank_values=True),
                headers=headers,
                body=body,
                json_body=parsed_body,
            )
            self._next_sequence += 1
            self._requests.append(captured)
            self._condition.notify_all()
            return captured

    def next_sequence(self) -> int:
        """Return a cursor that excludes every request captured so far."""
        with self._condition:
            return self._next_sequence

    def wait_for(
        self,
        predicate: Callable[[CapturedWebhookRequest], bool],
        start_sequence: int,
        timeout: float,
    ) -> CapturedWebhookRequest:
        """Return the first matching request, including one that arrived early."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for request in self._requests:
                    if request.sequence >= start_sequence and predicate(request):
                        return request

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    recent = ", ".join(
                        request.summary()
                        for request in self._requests
                        if request.sequence >= start_sequence
                    )
                    raise TimeoutError(
                        f"No matching webhook within {timeout:.1f}s. "
                        f"Captured after cursor: [{recent}]"
                    )
                self._condition.wait(remaining)

    def requests_since(self, start_sequence: int) -> List[CapturedWebhookRequest]:
        """Return a snapshot of requests at or after the supplied cursor."""
        with self._condition:
            return [
                request for request in self._requests
                if request.sequence >= start_sequence
            ]
