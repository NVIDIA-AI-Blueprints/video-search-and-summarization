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

"""Bounded loopback wire oracle, not RTVI inference or full normal VSS."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .media import MediaManifest
from .normal_results import ChunkFingerprint, NormalConfiguration, WireWitness
from .video_results import selected_chunks

MAX_MEDIA = 16 * 1024**2


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def settings(config: NormalConfiguration) -> dict:
    return {
        "prompt": config.prompt,
        "model": config.model,
        "stream": True,
        "chunk_duration": 30,
        "chunk_overlap_duration": 0,
        "num_frames_per_second_or_fixed_frames_chunk": 8.0,
        "use_fps_for_chunking": False,
        "enable_audio": False,
        "enable_reasoning": False,
        "max_tokens": 64,
        "temperature": 0.0,
        "seed": config.seed,
        "vlm_input_width": 0,
        "vlm_input_height": 0,
    }


def expected_payloads(media: MediaManifest) -> tuple[dict, ...]:
    return tuple(
        {
            "chunk_id": index,
            "start_time": str(index * 30),
            "end_time": f"{min((index + 1) * 30, media.duration_s):.6f}",
            "content": json.dumps(
                {
                    "events": [
                        {
                            "start_time": index * 30,
                            "end_time": index * 30 + 1,
                            "description": f"controlled event {index}",
                        }
                    ]
                },
                separators=(",", ":"),
            ),
            "frame_count": len(frames),
            "input_tokens": 0,
            "output_tokens": 0,
        }
        for index, frames in selected_chunks(media).items()
    )


def fingerprint(payload: dict) -> ChunkFingerprint:
    # No text or asset identifier is retained in evidence.
    return ChunkFingerprint(
        index=payload["chunk_id"],
        start_ns=round(float(payload["start_time"]) * 1e9),
        end_ns=round(float(payload["end_time"]) * 1e9),
        content_sha256=digest(payload["content"].encode()),
        frames=payload["frame_count"],
        input_tokens=payload.get("input_tokens", -1),
        output_tokens=payload.get("output_tokens", -1),
    )


def wire_valid(
    wire: bytes, expected: tuple[dict, ...], model: str, asset: str
) -> tuple[bool, int, bool]:
    if not wire or len(wire) > 65536:
        return False, 0, False
    actual, done = [], False
    try:
        for line in wire.splitlines():
            if len(line) > 8192:
                return False, len(actual), done
            if not line:
                continue
            if not line.startswith(b"data: ") or done:
                return False, len(actual), done
            data = line[6:]
            if data == b"[DONE]":
                done = True
                continue
            body = json.loads(data)
            if (
                body["id"] != asset
                or body["model"] != model
                or len(body["chunk_responses"]) != 1
            ):
                return False, len(actual), done
            actual.append(body["chunk_responses"][0])
            if len(actual) > 64:
                return False, 64, done
    except (ValueError, KeyError, TypeError):
        return False, len(actual), done
    return done and tuple(actual) == expected, len(actual), done


class Fixture:
    """One server thread, two-second sockets, owned uploads, bounded wire ledger."""

    def __init__(self, source: Path, media: MediaManifest, config: NormalConfiguration):
        self.media, self.config = media, config
        self.payloads = expected_payloads(media)
        self.expected = tuple(fingerprint(p) for p in self.payloads)
        self.uploads: dict[str, str] = {}
        self.witnesses: dict[str, dict] = {}
        self.closed = False
        self.requests = 0
        if source.stat().st_size > MAX_MEDIA or len(self.payloads) > 64:
            raise ValueError("fixture admission")
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                return

            def respond(self, status, body: bytes, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                written = self.wfile.write(body)
                self.wfile.flush()
                return written == len(body)

            def document(self, status, data):
                self.respond(status, json.dumps(data).encode())

            def admitted_body(self, limit):
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= limit:
                    raise ValueError("request admission")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("truncated request")
                fixture.requests += 1
                if fixture.requests > 256:
                    raise ValueError("request count")
                return data

            def do_GET(self):
                if self.path == "/v1/health/ready":
                    self.document(200, {})
                elif self.path == "/v1/models":
                    self.document(
                        200,
                        {
                            "data": [
                                {
                                    "id": config.model,
                                    "created": 1,
                                    "owned_by": "fixture",
                                    "api_type": "internal",
                                }
                            ]
                        },
                    )
                else:
                    self.document(404, {})

            def do_POST(self):
                try:
                    if self.path == "/v1/files":
                        body = self.admitted_body(MAX_MEDIA + 65536)
                        content_type = self.headers.get("Content-Type", "")
                        if (
                            len(content_type) > 512
                            or "multipart/form-data" not in content_type
                        ):
                            raise ValueError("multipart type")
                        message = BytesParser(policy=default).parsebytes(
                            (
                                f"Content-Type: {content_type}\r\n"
                                "MIME-Version: 1.0\r\n\r\n"
                            ).encode()
                            + body
                        )
                        parts = list(message.iter_parts())
                        if len(parts) != 3:
                            raise ValueError("multipart count")
                        fields = {
                            p.get_param(
                                "name", header="content-disposition"
                            ): p.get_payload(decode=True)
                            for p in parts
                        }
                        valid = (
                            set(fields) == {"purpose", "media_type", "file"}
                            and fields["purpose"] == b"vision"
                            and fields["media_type"] == b"video"
                            and len(fields["file"]) == media.bytes
                            and digest(fields["file"]) == media.sha256
                        )
                        if not valid or len(fixture.uploads) >= 2:
                            raise ValueError("upload mismatch")
                        asset = str(uuid.uuid4())
                        fixture.uploads[asset] = digest(fields["file"])
                        self.document(200, {"id": asset})
                    elif self.path == "/v1/generate_captions":
                        body = json.loads(self.admitted_body(8192))
                        asset = body["id"]
                        if (
                            asset not in fixture.uploads
                            or self.headers.get("x-stream-id") != asset
                        ):
                            raise ValueError("asset identity")
                        matched = {
                            k: v for k, v in body.items() if k != "id"
                        } == settings(config)
                        if not matched:
                            raise ValueError("request settings")
                        records = list(fixture.payloads)
                        if config.fault == "duplicate":
                            records.insert(0, records[0])
                        if config.fault == "wrong_content":
                            records[0] = records[0] | {"content": "wrong fixture"}
                        if config.fault == "truncated":
                            records = records[:-1]
                        model = (
                            "different"
                            if config.fault == "wrong_model"
                            else config.model
                        )
                        wire = b"".join(
                            b"data: "
                            + json.dumps(
                                {"id": asset, "model": model, "chunk_responses": [p]},
                                separators=(",", ":"),
                            ).encode()
                            + b"\n\n"
                            for p in records
                        )
                        if config.fault == "malformed":
                            wire = b"data: invalid json\n\n" + wire
                        if config.fault != "missing_done":
                            wire += b"data: [DONE]\n\n"
                        valid, messages, done = wire_valid(
                            wire, fixture.payloads, config.model, asset
                        )
                        fixture.witnesses[asset] = {
                            "upload_sha256": fixture.uploads[asset],
                            "settings_sha256": digest(
                                json.dumps(
                                    {k: v for k, v in body.items() if k != "id"},
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode()
                            ),
                            "chunks": tuple(fingerprint(p) for p in records),
                            "bytes": len(wire),
                            "sha256": digest(wire),
                            "messages": messages,
                            "done": done,
                            "sent_complete": False,
                            "valid": valid,
                            "request_matched": matched,
                            "upload_matched": fixture.uploads[asset] == media.sha256,
                            "deleted": False,
                        }
                        if len(wire) > 65536:
                            raise ValueError("response admission")
                        time.sleep(config.fixture_delay_ms / 1000)
                        fixture.witnesses[asset]["sent_complete"] = self.respond(
                            200, wire, "text/event-stream; charset=utf-8"
                        )
                    else:
                        self.document(404, {})
                except (ValueError, KeyError, TypeError):
                    self.document(422, {})

            def do_DELETE(self):
                asset = self.path.removeprefix("/v1/files/")
                if (
                    asset not in fixture.uploads
                    or self.headers.get("x-stream-id", asset) != asset
                ):
                    self.document(404, {})
                    return
                if config.fault == "delete_failure":
                    self.document(500, {})
                    return
                if config.fault == "delete_conflict":
                    self.document(409, {})
                    return
                fixture.uploads.pop(asset)
                if asset in fixture.witnesses:
                    fixture.witnesses[asset]["deleted"] = True
                self.document(200, {})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 2
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def witness(self, asset: str) -> WireWitness | None:
        data = self.witnesses.get(asset)
        if data is None:
            return None
        return WireWitness(**data, residual_uploads=len(self.uploads))

    def reset(self):
        # Only warmup/failed diagnostic fixture state, never a remote cleanup claim.
        if self.uploads:
            raise ValueError("owned fixture upload remains")
        self.witnesses.clear()
        self.requests = 0

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.closed = not self.thread.is_alive()
        self.uploads.clear()
        self.witnesses.clear()
