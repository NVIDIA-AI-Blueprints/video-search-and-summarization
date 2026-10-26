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

"""Caption work identities reject narrowing, truncation and private-text leaks."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest

from benchmarks.workflow import normal_witness
from benchmarks.workflow.normal_witness import request_identity, response_identity


def request(asset, **extra):
    return json.dumps(
        {
            "id": asset,
            "model": "cosmos3-nano",
            "stream": True,
            "prompt": "PRIVATE_PROMPT",
            "system_prompt": "PRIVATE_SYSTEM",
            "max_tokens": 1024,
            "temperature": 0,
            "seed": 1729,
            **extra,
        }
    ).encode()


def wire(*, done=True, order=(1, 0), **extra):
    events = []
    for i in order:
        body = {
            "model": "cosmos3-nano",
            "chunk_responses": [
                {
                    "chunk_id": i,
                    "start_time": i * 30,
                    "end_time": (i + 1) * 30,
                    "content": "PRIVATE_CAPTION",
                    "frame_count": 8,
                    "input_tokens": 800,
                    "output_tokens": 100,
                    **extra,
                }
            ],
        }
        events.append(b"data: " + json.dumps(body).encode() + b"\n\n")
    if done:
        events.append(b"data: [DONE]\n\n")
    return b"".join(events)


def test_request_normalizes_only_asset_id_and_keeps_full_other_work():
    first, second = str(uuid4()), str(uuid4())
    _, a = request_identity(request(first), first)
    _, b = request_identity(request(second), second)
    assert a["canonical_request_sha256"] == b["canonical_request_sha256"]
    assert a["raw_request_sha256"] != b["raw_request_sha256"]
    for key, value in [
        ("prompt", "changed"),
        ("temperature", 0.1),
        ("seed", 42),
        ("system_prompt", "changed"),
    ]:
        _, changed = request_identity(request(first, **{key: value}), first)
        assert changed["canonical_request_sha256"] != a["canonical_request_sha256"]
    assert "PRIVATE_" not in json.dumps(a) and first not in json.dumps(a)


@pytest.mark.parametrize(
    "fault", ["header", "url", "stream", "model", "large", "duplicate"]
)
def test_invalid_request_is_never_accepted(fault):
    asset = str(uuid4())
    raw = request(
        asset,
        **{
            "url": {"url": "https://unused.invalid"},
            "stream": {"stream": False},
            "model": {"model": "other"},
            "large": {"prompt": "x" * 65536},
        }.get(fault, {}),
    )
    if fault == "duplicate":
        raw = raw[:-1] + b',"stream":true}'
    with pytest.raises(ValueError):
        request_identity(raw, str(uuid4()) if fault == "header" else asset)


def test_unordered_complete_captions_are_hashed_without_publishing_text():
    a = response_identity(wire(), 2, 60)
    b = response_identity(wire(order=(0, 1)), 2, 60)
    assert a["caption_outputs_sha256"] == b["caption_outputs_sha256"]
    assert not a["native_completion_observed"]
    native = response_identity(wire(finish_reason="stop"), 2, 60)
    assert native["native_completion_observed"]
    assert "PRIVATE_" not in json.dumps(a)
    changed = response_identity(wire(content="DIFFERENT_PRIVATE_CAPTION"), 2, 60)
    assert changed["caption_outputs_sha256"] != a["caption_outputs_sha256"]


@pytest.mark.parametrize(
    "fault",
    [
        "no_done",
        "duplicate",
        "missing",
        "after_done",
        "length",
        "frame_count",
        "bool_usage",
        "window",
        "large",
    ],
)
def test_invalid_or_truncated_caption_evidence_fails(fault):
    raw = wire(
        **{
            "no_done": {"done": False},
            "duplicate": {"order": (0, 0)},
            "missing": {"order": (0,)},
            "length": {"finish_reason": "length"},
            "frame_count": {"frame_count": 7},
            "bool_usage": {"output_tokens": True},
            "window": {"start_time": 0},
            "large": {"content": "x" * 1024**2},
        }.get(fault, {})
    )
    if fault == "after_done":
        raw += wire(done=False)
    with pytest.raises(ValueError):
        response_identity(raw, 2, 60)


@pytest.mark.parametrize("fragment_size", [None, 17])
def test_real_proxy_forwards_before_upstream_finishes_and_retains_receipt(
    monkeypatch, fragment_size
):
    if fragment_size is not None:
        original = httpx.Response.iter_raw

        def fragmented(response, *args, **kwargs):
            for block in original(response, *args, **kwargs):
                for offset in range(0, len(block), fragment_size):
                    yield block[offset : offset + fragment_size]

        monkeypatch.setattr(httpx.Response, "iter_raw", fragmented)
    release = threading.Event()
    first = wire(done=False, order=(0,))
    later = wire(order=(1,))
    asset, token = str(uuid4()), str(uuid4())

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            assert self.path == "/v1/generate_captions"
            assert raw == request(asset) and self.headers["x-stream-id"] == asset
            assert self.headers["Accept-Encoding"] == "identity"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            assert release.wait(3)
            self.wfile.write(later)
            self.wfile.flush()

    with ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as upstream:
        monkeypatch.setattr(normal_witness, "UPSTREAM_PORT", upstream.server_port)
        with normal_witness.Witness(0, token, 2, 60) as proxy:
            threads = [
                threading.Thread(target=s.serve_forever) for s in (upstream, proxy)
            ]
            for thread in threads:
                thread.start()
            try:
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{proxy.server_port}",
                    trust_env=False,
                    timeout=3,
                ) as client:
                    with client.stream(
                        "POST",
                        "/v1/generate_captions",
                        content=request(asset),
                        headers={"x-stream-id": asset},
                    ) as response:
                        response.raise_for_status()
                        blocks = response.iter_raw()
                        initial = b""
                        while len(initial) < len(first):
                            initial += next(blocks)
                        assert initial == first and not release.is_set()
                        release.set()
                        assert initial + b"".join(blocks) == first + later
                    receipt = client.get("/control/" + token).json()
                    assert receipt["record"]["status"] == "completed"
                    assert (
                        receipt["calls"] == 1
                        and not receipt["active"]
                        and not receipt["rejected"]
                    )
                    assert "PRIVATE_" not in json.dumps(
                        receipt
                    ) and asset not in json.dumps(receipt)
                    replay = client.post(
                        "/v1/generate_captions",
                        content=request(asset),
                        headers={"x-stream-id": asset},
                    )
                    assert replay.status_code == 502
                    assert client.get("/control/" + token).json()["rejected"] == 1
            finally:
                release.set()
                proxy.shutdown()
                upstream.shutdown()
                for thread in threads:
                    thread.join(3)


def test_deployed_string_times_accept_only_ten_occupied_five_minute_windows():
    raw = b""
    for i in range(10):
        raw += wire(
            done=False, order=(i,), start_time=str(i * 30), end_time=str((i + 1) * 30)
        )
    raw += b"data: [DONE]\n\n"
    result = response_identity(raw, 10, 300.033067)
    assert len(result["chunks"]) == 10
    assert result["chunks"][-1]["end_time"] == "300"
    with pytest.raises(ValueError, match="coverage"):
        response_identity(raw, 11, 300.033067)
    for invalid in (True, "NaN", "inf", "-1", {}, " 0"):
        with pytest.raises(ValueError):
            response_identity(wire(start_time=invalid), 2, 60)


@pytest.mark.parametrize("reason,tokens", [(None, 1024), (None, 1025), ("stop", 1025)])
def test_missing_native_finish_at_cap_or_over_cap_is_rejected(reason, tokens):
    with pytest.raises(ValueError, match="native completion"):
        response_identity(wire(finish_reason=reason, output_tokens=tokens), 2, 60)
