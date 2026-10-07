#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
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
"""Stub of the DeepStream REST server that MV3DT's add-streams.sh talks to.

The real server lives inside the `nvcr.io/nvidia/vss-core/vss-rt-cv`
perception image and is not part of the VSS repo, so client-side behavior in
`scripts/add-streams.sh` cannot otherwise be tested. This stub implements just
the endpoints that script uses, plus the failure modes the open NVBugs issues
describe.

Test-only scaffolding: it lives beside the tests deliberately and does not belong
under scripts/lib/, which is shipped production code that add-streams.sh loads at
runtime. A fake API server must never be installable on a rig.

This file exists twice, here and in the bug-fix harness at docker/mv3dt-tests/.
tests/run.sh compares them byte for byte, so edit both in the same commit.

Endpoints (mirroring DeepStream's `/api/v1`):

    GET  /api/v1/ready
    GET  /api/v1/metrics
    GET  /api/v1/proxy/configuration   (VST management API, for the SEI check)
    GET  /api/v1/stream/get-stream-info
    POST /api/v1/stream/add
    POST /api/v1/stream/remove

Modes (--mode, repeatable):

    ok               happy path
    not-ready        /api/v1/ready returns HTTP 200 with ds-ready "NO"
                     (readiness reports NO with HTTP 200)
    add-fails-200    POST add returns HTTP 200 with a failure reason in the
                     body (HTTP 200 carrying a failure)
    empty-info       get-stream-info returns an empty body (mimics a partially
                     wedged server)
    no-sei           /api/v1/proxy/configuration reports
                     enableProxyServerFrameIdSupport false, i.e. the VST proxy
                     is not emitting SEI frame IDs
    adds-inactive    streams added via POST register in get-stream-info but
                     never appear in /api/v1/metrics stream-stats, i.e. the
                     server reports STREAM_ADD_SUCCESS for a source that never
                     produces frames

To simulate the server being unavailable entirely, do not start
this stub at all -- just point the script at a closed port.

Prints the chosen port to stdout as `PORT=<n>` once listening, so shell tests
can capture it without racing.
"""

from __future__ import annotations

import argparse
import json
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler


class StubState:
    def __init__(self, modes: set[str]) -> None:
        self.modes = modes
        self.lock = threading.Lock()
        # camera_id -> url, in insertion order, mirroring source_id assignment.
        self.streams: dict[str, str] = {}
        # camera_ids registered but not producing frames: present in
        # get-stream-info, absent from metrics stream-stats.
        self.inactive: set[str] = set()
        self.frame_number = 0
        self.requests: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    state: StubState

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep test output readable
        pass

    # -- helpers ---------------------------------------------------------

    def _send(self, code: int, payload) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        st = self.state
        if self.path.startswith("/api/v1/ready"):
            # add-streams.sh greps for the literal '"ds-ready" : "[A-Z]*"' and
            # then for '"YES"', so both the spacing and the YES/NO vocabulary
            # have to match DeepStream exactly.
            ready = "NO" if "not-ready" in st.modes else "YES"
            body = ('{"ds-ready" : "%s"}' % ready).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/api/v1/proxy/configuration"):
            self._send(200, {
                "enableRtspServerFrameIdSupport": False,
                "enableProxyServerFrameIdSupport": "no-sei" not in st.modes,
                "httpPort": 30000,
            })
            return

        if self.path.startswith("/api/v1/metrics"):
            with st.lock:
                st.frame_number += 30
                active = [
                    (i, cid) for i, cid in enumerate(st.streams)
                    if cid not in st.inactive
                ]
                frame = st.frame_number
            # fps mirrors the real server, which reports 0.0 for healthy
            # streams whose frame_number is advancing -- only the first stream
            # gets a non-zero value. Tests must not gate on fps.
            stats = [
                {
                    "sensor_id": cid,
                    "sensor_name": cid,
                    "source_id": i,
                    "fps": 29.997 if n == 0 else 0.0,
                    "frame_number": frame,
                    "latency_ms": 41.62890625,
                }
                for n, (i, cid) in enumerate(active)
            ]
            reason = "GET_METRICS_INFO_SUCCESS"
            if not stats:
                reason += " - No data available"
            self._send(200, {
                "metrics-info": {
                    "stream-count": len(stats),
                    "stream-stats": stats,
                    "system-stats": {"GPU_gb": 2.49, "RAM_gb": 25.06,
                                     "cpu_util": 3.38, "gpu_util": 0.0},
                },
                "reason": reason,
            })
            return

        if self.path.startswith("/api/v1/stream/get-stream-info"):
            if "empty-info" in st.modes:
                self._send(200, b"")
                return
            with st.lock:
                items = [
                    {"source_id": i, "camera_id": cid, "camera_url": url}
                    for i, (cid, url) in enumerate(st.streams.items())
                ]
            self._send(
                200,
                {"stream-info": {"stream-count": len(items), "stream-info": items}},
            )
            return

        self._send(404, {"reason": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        st = self.state
        payload = self._read_json()
        value = payload.get("value") or {}
        cam = str(value.get("camera_id") or "")
        url = str(value.get("camera_url") or "")

        with st.lock:
            st.requests.append({"path": self.path, "payload": payload})

        if self.path.startswith("/api/v1/stream/add"):
            if "add-fails-200" in st.modes:
                # HTTP 200 carrying a failure in the body.
                self._send(
                    200,
                    {
                        "status": "fail",
                        "reason": f"failed to add stream {cam}: source not created",
                    },
                )
                return
            with st.lock:
                st.streams[cam] = url
                if "adds-inactive" in st.modes:
                    st.inactive.add(cam)
            self._send(200, {"status": "success", "reason": "STREAM_ADD_SUCCESS"})
            return

        if self.path.startswith("/api/v1/stream/remove"):
            with st.lock:
                existed = st.streams.pop(cam, None) is not None
            if not existed:
                self._send(
                    200,
                    {"status": "fail", "reason": f"stream {cam} not found"},
                )
                return
            self._send(200, {"status": "success", "reason": "STREAM_REMOVE_SUCCESS"})
            return

        self._send(404, {"reason": "not found"})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=0, help="0 picks a free port")
    ap.add_argument(
        "--mode",
        action="append",
        default=[],
        choices=["ok", "not-ready", "add-fails-200", "empty-info",
                 "adds-inactive", "no-sei"],
        help="behavior mode, repeatable",
    )
    ap.add_argument(
        "--dead-stream",
        action="append",
        default=[],
        metavar="CAM=URL",
        help="pre-register a stream that never produces frames, repeatable",
    )
    ap.add_argument(
        "--seed-stream",
        action="append",
        default=[],
        metavar="CAM=URL",
        help="pre-register a stream, repeatable",
    )
    args = ap.parse_args()

    state = StubState(set(args.mode) or {"ok"})
    for item in args.seed_stream:
        cam, _, url = item.partition("=")
        if cam:
            state.streams[cam] = url
    for item in args.dead_stream:
        cam, _, url = item.partition("=")
        if cam:
            state.streams[cam] = url
            state.inactive.add(cam)

    handler = type("BoundHandler", (Handler,), {"state": state})
    srv = Server(("127.0.0.1", args.port), handler)
    print(f"PORT={srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
