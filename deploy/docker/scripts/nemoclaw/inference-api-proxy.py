#!/usr/bin/env python3
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

"""Reverse proxy: LISTEN -> https://inference-api.nvidia.com (for NemoClaw SSRF bypass).

On hosts where inference-api.nvidia.com resolves to a private IP (e.g. corp/DGX
internal DNS), NemoClaw onboard rejects the endpoint during SSRF preflight.
Point NEMOCLAW_ENDPOINT_URL at this proxy instead, e.g.:

  http://host.openshell.internal:18080/v1

Bind on all interfaces (0.0.0.0) so the OpenShell gateway container can reach
the host via host.openshell.internal (typically 172.18.0.1). Loopback-only
(127.0.0.1) works for host-side onboard probes but breaks inference.local
inside the sandbox (503).

Environment variables:
  INFERENCE_API_UPSTREAM      upstream host (default: inference-api.nvidia.com)
  INFERENCE_API_PROXY_HOST    bind address (default: 0.0.0.0)
  INFERENCE_API_PROXY_PORT    listen port (default: 18080)
"""

from __future__ import annotations

import http.client
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_HOST = os.environ.get("INFERENCE_API_UPSTREAM", "inference-api.nvidia.com")
LISTEN_HOST = os.environ.get("INFERENCE_API_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("INFERENCE_API_PROXY_PORT", "18080"))
SKIP_HEADERS = {"host", "connection", "transfer-encoding", "proxy-connection"}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[inference-api-proxy] {self.address_string()} - {fmt % args}\n")

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        conn = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=120, context=ssl.create_default_context())
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_HEADERS}
            headers["Host"] = UPSTREAM_HOST
            conn.request(self.command, self.path, body=body, headers=headers)
            upstream = conn.getresponse()
            self.send_response(upstream.status, upstream.reason)
            has_length = upstream.getheader("Content-Length") is not None
            for key, value in upstream.getheaders():
                if key.lower() not in SKIP_HEADERS:
                    self.send_header(key, value)
            # Streaming (SSE) replies come back chunked with no Content-Length.
            # We strip Transfer-Encoding above and don't re-chunk, so on a keep-alive
            # HTTP/1.1 socket the client would never see end-of-message and hang.
            # Signal end-of-body via connection close instead.
            if not has_length:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()  # push SSE tokens to the client promptly
        finally:
            conn.close()

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_PATCH(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def do_OPTIONS(self) -> None:
        self._proxy()


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print(f"Listening http://{LISTEN_HOST}:{LISTEN_PORT} -> https://{UPSTREAM_HOST}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
