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

import asyncio
import json

import httpx
import pytest

from vst.its_vst_handler import ITS_VST_HANDLER
from vst.exceptions import (
    VSTError,
    VSTClientError,
    VSTOverloadedError,
    VSTRecordingNotFoundError,
    VSTTimeoutError,
    VSTUnavailableError,
)


def _make_handler():
    handler = ITS_VST_HANDLER({
        "vst_config": {
            "base_url": "http://vst.test:30888",
            "segment_anchor": "end",
            "segment_duration_seconds": 10,
            "timeout": 5,
        },
    })
    handler._get_stream_id_from_name = lambda name: name
    return handler


def _run_with_transport(handler, transport_handler, **kwargs):
    async def _run():
        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await handler.get_video_stream_url_async(
                client,
                "cam-1",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:10.000Z",
                **kwargs,
            )

    return asyncio.run(_run())


class TestAsyncVSTErrorTaxonomy:
    def test_success_returns_video_url_and_window(self):
        handler = _make_handler()

        def respond(request):
            assert "/vst/api/v1/storage/file/cam-1/url" in str(request.url)
            return httpx.Response(200, json={"videoUrl": "http://vst.test/clip.mp4"})

        video_url, eff_start, eff_end = _run_with_transport(handler, respond)
        assert video_url == "http://vst.test/clip.mp4"
        assert eff_start and eff_end

    def test_missing_video_url_maps_to_vst_error(self):
        handler = _make_handler()

        def respond(request):
            return httpx.Response(200, json={"unexpected": True})

        with pytest.raises(VSTError) as exc_info:
            _run_with_transport(handler, respond)
        assert exc_info.value.category == "missing_video_url"

    @pytest.mark.parametrize("status,expected,category", [
        (404, VSTRecordingNotFoundError, "recording_not_found"),
        (429, VSTOverloadedError, "overloaded"),
        (503, VSTOverloadedError, "overloaded"),
        (400, VSTClientError, "client_error"),
        (500, VSTUnavailableError, "server_error"),
    ])
    def test_http_status_mapping_matches_sync_taxonomy(self, status, expected, category):
        handler = _make_handler()

        def respond(request):
            return httpx.Response(status, text="boom")

        with pytest.raises(expected) as exc_info:
            _run_with_transport(handler, respond)
        assert exc_info.value.category == category
        assert exc_info.value.status_code == status

    def test_timeout_maps_to_vst_timeout_error(self):
        handler = _make_handler()

        def respond(request):
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(VSTTimeoutError) as exc_info:
            _run_with_transport(handler, respond)
        assert exc_info.value.category == "timeout"

    def test_connect_error_maps_to_vst_unavailable(self):
        handler = _make_handler()

        def respond(request):
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(VSTUnavailableError) as exc_info:
            _run_with_transport(handler, respond)
        assert exc_info.value.category == "connection_failed"

    def test_request_params_match_sync_builder(self):
        handler = _make_handler()
        captured = {}

        def respond(request):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"videoUrl": "http://vst.test/clip.mp4"})

        _run_with_transport(handler, respond)

        url, headers, params, timeout, eff_s, eff_e = handler._build_video_url_request(
            "cam-1", "2026-01-01T00:00:00.000Z", "2026-01-01T00:00:10.000Z",
        )
        assert captured["params"]["streamId"] == params["streamId"]
        assert captured["params"]["container"] == params["container"]
        assert json.loads(captured["params"]["configuration"]) == json.loads(params["configuration"])
