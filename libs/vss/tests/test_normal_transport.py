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

"""Serialized requests, incremental delivery, errors and owned-client closure."""

import json
from types import SimpleNamespace

import httpx
import pytest

from benchmarks.workflow import normal_transport as adapter


def payload():
    return {
        "id": "asset",
        "prompt": "PRIVATE_PROMPT",
        "model": "model",
        "stream": True,
        "chunk_duration": 30,
        "chunk_overlap_duration": 0,
        "enable_reasoning": False,
        "enable_audio": False,
        "max_tokens": 1024,
        "temperature": 0,
        "seed": 1729,
        "system_prompt": "PRIVATE_SYSTEM",
        "response_format": {"type": "json_object"},
        "media_info": {"start_offset": 0},
        "num_frames_per_second_or_fixed_frames_chunk": 8.0,
        "use_fps_for_chunking": False,
    }


@pytest.mark.parametrize("fault", ["url", "not_streaming", "empty_id"])
def test_unsupported_modes_reject_before_dispatch(fault):
    body = payload()
    body.update(
        {"url": "https://unused.invalid/video"}
        if fault == "url"
        else {"stream": False}
        if fault == "not_streaming"
        else {"id": ""}
    )
    with pytest.raises(ValueError):
        adapter.arguments(body)


@pytest.mark.parametrize("mode", ["complete", "early_close", "malformed", "http_error"])
def test_actual_library_transport_preserves_stock_request_and_closes(monkeypatch, mode):
    body = payload()
    seen = []
    closed = []
    delivered = []

    class Wire(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(3):
                delivered.append(i)
                yield (
                    b"data: invalid-json\n\n"
                    if mode == "malformed"
                    else b"data: " + json.dumps({"chunk": i}).encode() + b"\n\n"
                )
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.append("wire")

    def respond(request):
        seen.append(request)
        return httpx.Response(
            503 if mode == "http_error" else 200,
            stream=Wire(),
            headers={"content-type": "text/event-stream"},
        )

    real_client = httpx.AsyncClient

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

        async def aclose(self):
            closed.append("client")
            await super().aclose()

        async def __aexit__(self, *args):
            closed.append("client_context")
            await super().__aexit__(*args)

    monkeypatch.setattr(adapter.httpx, "AsyncClient", Client)

    class Stock:
        _base_url = "http://127.0.0.1:8018"

        def _build_generate_captions_request(self, **kwargs):
            assert kwargs == {"file_id": "asset"}
            return SimpleNamespace(model_dump=lambda **kw: dict(body))

        def _raise_rtvi_error(self, operation, response):
            assert operation == "generate_captions" and response.status_code == 503
            raise RuntimeError("stock HTTP error")

    class RtviError(Exception):
        pass

    source = adapter.client_class(Stock, RtviError)().generate_captions_stream(
        file_id="asset"
    )
    if mode == "complete":
        assert list(source) == [{"chunk": i} for i in range(3)]
    elif mode == "early_close":
        assert next(source) == {"chunk": 0}
        assert delivered == [0]
        source.close()
    else:
        with pytest.raises(RuntimeError if mode == "http_error" else RtviError):
            list(source)
    assert len(seen) == 1
    assert json.loads(seen[0].content) == body
    assert seen[0].headers["x-stream-id"] == "asset"
    assert "wire" in closed and "client_context" in closed
