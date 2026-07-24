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
"""Unit tests for the deprecated PUT /api/v1/videos-for-search/{filename} shim."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi import FastAPI
import pytest

from vss_agents.api.video_ingest import VideoIngestResponse
from vss_agents.api.video_search_ingest import create_video_search_ingest_router
from vss_agents.api.video_search_ingest import register_video_search_ingest_routes


class TestDeprecatedSearchIngestRouter:
    """The legacy PUT must register and stay flagged as deprecated."""

    @staticmethod
    def _build_router():
        return create_video_search_ingest_router(
            vst_internal_url="http://vst:30888",
            rtvi_embed_base_url="",
            rtvi_cv_base_url="",
        )

    def test_only_legacy_put_route_registered(self):
        paths_methods = sorted((r.path, sorted(r.methods)) for r in self._build_router().routes)
        assert paths_methods == [("/api/v1/videos-for-search/{filename}", ["PUT"])]

    def test_route_is_deprecated_in_openapi(self):
        """The PUT route must be flagged ``deprecated=True`` so the OpenAPI
        schema warns clients off; this is a compat shim for the CI test
        fixture, not a supported API surface."""
        route = self._build_router().routes[0]
        assert route.deprecated is True


class TestRegisterVideoSearchIngestRoutes:
    """Registration paths for the deprecated /videos-for-search/* shim."""

    def test_registers_when_vst_configured(self):
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = MagicMock(
            vst_internal_url="http://vst:8080",
            vst_external_url="http://vst.public:8080",
            rtvi_embed_base_url="http://rtvi-embed:8017",
            rtvi_cv_base_url="",
            rtvi_embed_model="cosmos-embed1-448p",
            rtvi_embed_chunk_duration=5,
        )

        register_video_search_ingest_routes(app, config)

        assert app.include_router.called

    def test_skips_when_vst_unavailable(self):
        """Without VST_INTERNAL_URL the shim must skip (with a warning) rather
        than crash boot — mirrors the universal /videos route's policy."""
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        with patch.dict("os.environ", {"VST_INTERNAL_URL": "", "HOST_IP": ""}, clear=False):
            register_video_search_ingest_routes(app, config)

        assert not app.include_router.called

    def test_register_path_does_not_require_rtvi_to_be_configured(self):
        """Registration succeeds even when RTVI isn't configured — the
        ``_run_post_upload_processing`` helper this shim delegates to
        self-skips RTVI calls in that case."""
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        env = {
            "VST_INTERNAL_URL": "http://vst:30888",
            "HOST_IP": "10.0.0.5",
            "RTVI_EMBED_PORT": "",
            "RTVI_CV_PORT": "",
        }
        with patch.dict("os.environ", env, clear=False):
            register_video_search_ingest_routes(app, config)

        assert app.include_router.called


class TestUploadVideoToVstHeaderValidation:
    """Header validation on the deprecated PUT — covers the synchronous
    checks before any VST call (no need to mock the streaming upload)."""

    @staticmethod
    def _route():
        router = create_video_search_ingest_router(
            vst_internal_url="http://vst:30888",
            rtvi_embed_base_url="",
        )
        return router.routes[0]

    @staticmethod
    def _request(headers):
        req = MagicMock()
        req.headers.get.side_effect = lambda k: headers.get(k.lower())
        return req

    @pytest.mark.asyncio
    async def test_missing_content_type_400(self):
        from fastapi import HTTPException

        route = self._route()
        with pytest.raises(HTTPException) as exc:
            await route.endpoint(filename="clip.mp4", request=self._request({"content-length": "10"}))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unsupported_content_type_415(self):
        from fastapi import HTTPException

        route = self._route()
        with pytest.raises(HTTPException) as exc:
            await route.endpoint(
                filename="clip.avi",
                request=self._request({"content-type": "video/avi", "content-length": "10"}),
            )
        assert exc.value.status_code == 415

    @pytest.mark.asyncio
    async def test_missing_content_length_400(self):
        from fastapi import HTTPException

        route = self._route()
        with pytest.raises(HTTPException) as exc:
            await route.endpoint(filename="clip.mp4", request=self._request({"content-type": "video/mp4"}))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_zero_content_length_400(self):
        from fastapi import HTTPException

        route = self._route()
        with pytest.raises(HTTPException) as exc:
            await route.endpoint(
                filename="clip.mp4",
                request=self._request({"content-type": "video/mp4", "content-length": "0"}),
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_disable_audio_threaded_to_post_upload_processing(self):
        """``disable_audio=False`` on the router (audio-aware VLM) must
        propagate to ``_run_post_upload_processing`` so VST keeps audio
        through the deprecated PUT shim too."""
        route = create_video_search_ingest_router(
            vst_internal_url="http://vst:30888",
            rtvi_embed_base_url="",
            disable_audio=False,
        ).routes[0]

        response = MagicMock()
        response.status_code = 201
        response.json.return_value = {"sensorId": "sensor-abc", "filename": "clip.mp4"}
        response.text = "OK"

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.put = AsyncMock(return_value=response)

        request = self._request({"content-type": "video/mp4", "content-length": "10"})
        request.stream.return_value = "stream-body"

        with (
            patch("vss_agents.api.video_search_ingest.httpx.AsyncClient", return_value=client),
            patch(
                "vss_agents.api.video_search_ingest._run_post_upload_processing",
                new=AsyncMock(
                    return_value=VideoIngestResponse(message="ok", sensor_id="sensor-abc", filename="clip.mp4")
                ),
            ) as mock_post,
        ):
            await route.endpoint(filename="clip.mp4", request=request)

        assert mock_post.call_args.kwargs["disable_audio"] is False

    @pytest.mark.asyncio
    async def test_custom_timeouts_apply_to_upload_and_completion(self):
        route = create_video_search_ingest_router(
            vst_internal_url="http://vst:30888",
            rtvi_embed_base_url="",
            vst_upload_timeout_seconds=123.0,
            rtvi_cv_timeout_seconds=124.0,
            rtvi_embed_timeout_seconds=125.0,
            vst_storage_timeout_seconds=126.0,
        ).routes[0]

        response = MagicMock()
        response.status_code = 201
        response.json.return_value = {"sensorId": "sensor-abc", "filename": "clip.mp4"}
        response.text = "OK"

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.put = AsyncMock(return_value=response)

        request = self._request({"content-type": "video/mp4", "content-length": "10"})
        request.stream.return_value = "stream-body"

        with (
            patch("vss_agents.api.video_search_ingest.httpx.AsyncClient", return_value=client) as async_client,
            patch(
                "vss_agents.api.video_search_ingest._run_post_upload_processing",
                new=AsyncMock(
                    return_value=VideoIngestResponse(message="ok", sensor_id="sensor-abc", filename="clip.mp4")
                ),
            ) as mock_post,
        ):
            result = await route.endpoint(filename="clip.mp4", request=request)

        assert result.sensor_id == "sensor-abc"
        async_client.assert_called_once_with(timeout=123.0)
        kwargs = mock_post.call_args.kwargs
        assert kwargs["rtvi_cv_timeout_seconds"] == 124.0
        assert kwargs["rtvi_embed_timeout_seconds"] == 125.0
        assert kwargs["vst_storage_timeout_seconds"] == 126.0
