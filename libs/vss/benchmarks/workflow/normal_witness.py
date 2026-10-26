# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Bounded, loopback-only caption witness shared by matched normal-app arms.

The proxy forwards incrementally. Only hashes and bounded numeric metadata
survive; generated text and asset identities never enter its public snapshot.
"""

import argparse
import hashlib
import http.client
import json
import math
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

from benchmarks.e2e.config import fingerprint
from benchmarks.workflow.file_witness import unique_object

LIMIT = 1024**2
UPSTREAM_PORT = 18018


def request_identity(raw, stream_id):
    if len(raw) > 65536:
        raise ValueError("caption request budget")
    body = json.loads(raw, object_pairs_hook=unique_object)
    UUID(body["id"])
    if (
        body["id"] != stream_id
        or body.get("url") is not None
        or body.get("stream") is not True
        or body.get("model") != "cosmos3-nano"
    ):
        raise ValueError("caption request identity")
    canonical = {k: v for k, v in body.items() if k != "id"}
    return body, {
        "raw_request_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_request_sha256": fingerprint(canonical),
        "asset_identity_removed_fields": ["id"],
        "source_header_matches_request": True,
    }


def timestamp(value):
    if (
        type(value) not in {int, float, str}
        or isinstance(value, str)
        and (len(value) > 64 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value))
    ):
        raise ValueError("caption timestamp")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("caption timestamp")
    return number


def response_identity(raw, expected, duration):
    if len(raw) > LIMIT:
        raise ValueError("caption response budget")
    chunks = {}
    done = False
    for line in raw.splitlines():
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data in {b"ping", b": ping"}:
            continue
        if data == b"[DONE]":
            if done:
                raise ValueError("duplicate DONE")
            done = True
            continue
        if done:
            raise ValueError("caption after DONE")
        body = json.loads(data, object_pairs_hook=unique_object)
        if body.get("error") or body.get("model") != "cosmos3-nano":
            raise ValueError("caption model or error")
        for chunk in body["chunk_responses"]:
            index = chunk["chunk_id"]
            if type(index) is not int or not 0 <= index < expected or index in chunks:
                raise ValueError("caption index")
            start, end = timestamp(chunk["start_time"]), timestamp(chunk["end_time"])
            if (
                any(
                    type(v) not in {int, float} or not math.isfinite(v)
                    for v in (start, end)
                )
                or not 0 <= start < end <= duration + 0.1
                or not isinstance(chunk.get("content"), str)
            ):
                raise ValueError("caption interval or content")
            if (
                abs(start - index * 30) > 0.1
                or abs(end - min((index + 1) * 30, duration)) > 0.1
            ):
                raise ValueError("caption window")
            for key in ("frame_count", "input_tokens", "output_tokens"):
                if type(chunk.get(key)) is not int or not 0 <= chunk[key] <= 10000000:
                    raise ValueError("caption usage")
            if chunk["frame_count"] != 8:
                raise ValueError("caption frame count")
            reason = chunk.get("finish_reason")
            if (
                reason not in (None, "stop")
                or chunk["output_tokens"] > 1024
                or reason is None
                and chunk["output_tokens"] >= 1024
            ):
                raise ValueError("caption native completion")
            chunks[index] = {
                k: chunk[k]
                for k in (
                    "chunk_id",
                    "start_time",
                    "end_time",
                    "content",
                    "frame_count",
                    "input_tokens",
                    "output_tokens",
                )
            } | {"finish_reason": reason}
    if not done or set(chunks) != set(range(expected)):
        raise ValueError("caption coverage")
    ordered = [chunks[i] for i in range(expected)]
    if (
        timestamp(ordered[0]["start_time"]) > 0.1
        or abs(timestamp(ordered[-1]["end_time"]) - duration) > 0.1
    ):
        raise ValueError("caption source coverage")
    return {
        "raw_wire_sha256": hashlib.sha256(raw).hexdigest(),
        "caption_outputs_sha256": fingerprint(ordered),
        "done": True,
        "native_completion_observed": all(
            c["finish_reason"] == "stop" for c in ordered
        ),
        "chunks": [
            {k: v for k, v in c.items() if k != "content"}
            | {"content_sha256": fingerprint(c["content"])}
            for c in ordered
        ],
    }


class Witness(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, token, expected, duration):
        UUID(token)
        if not 1 <= expected <= 64 or not 0 < duration <= 600:
            raise ValueError("witness bounds")
        self.token, self.expected, self.duration = token, expected, duration
        self.slots = threading.BoundedSemaphore(8)
        self.lock = threading.Lock()
        self.record, self.calls, self.active, self.rejected = {}, 0, False, 0
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def snapshot(self):
        with self.lock:
            return {
                "schema_version": "normal_caption_transport_witness.1",
                "upstream": f"http://127.0.0.1:{UPSTREAM_PORT}",
                "upstream_sha256": fingerprint(f"http://127.0.0.1:{UPSTREAM_PORT}"),
                "calls": self.calls,
                "active": self.active,
                "rejected": self.rejected,
                "record": dict(self.record),
            }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)
        self.header_timer = threading.Timer(10, self.abort_socket, (self.connection,))
        self.header_timer.daemon = True
        self.header_timer.start()

    @staticmethod
    def abort_socket(connection):
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self):
        self.header_timer.cancel()
        super().finish()

    def do_GET(self):
        self.header_timer.cancel()
        if self.path == "/control/" + self.server.token:
            raw = json.dumps(self.server.snapshot(), allow_nan=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path in {"/v1/health/ready", "/v1/models"} or re.fullmatch(
            r"/v1/files/[a-f0-9-]{36}", self.path
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", UPSTREAM_PORT, timeout=3
            )
            try:
                connection.request(
                    "GET", self.path, headers={"Accept-Encoding": "identity"}
                )
                response = connection.getresponse()
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("metadata budget")
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                self.send_error(502)
            finally:
                connection.close()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/v1/generate_captions":
            self.send_error(404)
            return
        connection = None
        headers_sent = False
        admitted = False
        transfer_timer = None
        try:
            length = self.headers.get("Content-Length", "")
            if (
                self.headers.get("Transfer-Encoding") is not None
                or len(self.headers.get_all("Content-Length", [])) != 1
                or not length.isdecimal()
                or not 0 < int(length) <= 65536
            ):
                raise ValueError("request framing")
            raw = self.rfile.read(int(length))
            self.header_timer.cancel()
            body, request = request_identity(raw, self.headers.get("x-stream-id"))
            with self.server.lock:
                if self.server.calls:
                    raise ValueError("caption replay")
                self.server.calls += 1
                self.server.active = True
                admitted = True
                self.server.record = {"status": "active", **request}
            connection = http.client.HTTPConnection(
                "127.0.0.1", UPSTREAM_PORT, timeout=180
            )
            connection.connect()
            transfer_timer = threading.Timer(180, self.abort_socket, (connection.sock,))
            transfer_timer.daemon = True
            transfer_timer.start()
            connection.request(
                "POST",
                self.path,
                body=raw,
                headers={
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                    "x-stream-id": body["id"],
                },
            )
            response = connection.getresponse()
            if (
                response.status != 200
                or "text/event-stream" not in response.getheader("Content-Type", "")
                or response.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("upstream response")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            headers_sent = True
            captured = bytearray()
            deadline = time.monotonic() + 180
            while block := response.read1(4096):
                if time.monotonic() > deadline or len(captured) + len(block) > LIMIT:
                    raise ValueError("caption response budget")
                captured.extend(block)
                self.wfile.write(block)
                self.wfile.flush()
            observation = response_identity(
                bytes(captured), self.server.expected, self.server.duration
            )
            with self.server.lock:
                self.server.record = {"status": "completed", **request, **observation}
        except Exception as error:
            with self.server.lock:
                self.server.rejected += 1
                self.server.record = {
                    **self.server.record,
                    "status": "failed",
                    "failure": type(error).__name__,
                }
            if not headers_sent:
                self.send_error(502)
        finally:
            if transfer_timer is not None:
                transfer_timer.cancel()
            if connection:
                connection.close()
            if admitted:
                with self.server.lock:
                    self.server.active = False
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args()
    with Witness(args.port, args.token, args.expected, args.duration) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
