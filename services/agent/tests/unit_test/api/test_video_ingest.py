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
"""Unit tests for the video_ingest module's three-step chat upload flow."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from vss_agents.api.video_ingest import VideoIngestResponse
from vss_agents.api.video_ingest import VideoUploadCompleteInput
from vss_agents.api.video_ingest import VideoUploadUrlInput
from vss_agents.api.video_ingest import VideoUploadUrlResponse
from vss_agents.api.video_ingest import _parse_optional_http_url
from vss_agents.api.video_ingest import _resolve_video_upload_config
from vss_agents.api.video_ingest import _run_post_upload_processing
from vss_agents.api.video_ingest import create_video_upload_complete_router
from vss_agents.api.video_ingest import create_video_upload_router
from vss_agents.api.video_ingest import register_video_upload
from vss_agents.api.video_ingest import register_video_upload_complete


class TestVideoIngestResponse:
    """Pin down the response model surface."""

    def test_response_creation(self):
        response = VideoIngestResponse(
            message="Video uploaded successfully",
            video_id="vid-001",
            filename="test_video.mp4",
            chunks_processed=5,
        )
        assert response.message == "Video uploaded successfully"
        assert response.video_id == "vid-001"
        assert response.filename == "test_video.mp4"
        assert response.chunks_processed == 5

    def test_response_default_chunks(self):
        response = VideoIngestResponse(message="Done", video_id="vid-002", filename="another_video.mp4")
        assert response.chunks_processed == 0


class TestParseOptionalHttpUrl:
    """Tests for the shared URL-guard helper."""

    def test_none_and_empty(self):
        assert _parse_optional_http_url(None) is None
        assert _parse_optional_http_url("") is None

    def test_scheme_only_forms_rejected(self):
        assert _parse_optional_http_url("http://") is None
        assert _parse_optional_http_url("http:") is None

    def test_empty_port_body_rejected(self):
        # `http://host:` parses with hostname="host" and port=None — silently
        # falls back to the scheme's default port. Treat as misconfigured.
        assert _parse_optional_http_url("http://host:") is None

    def test_explicit_host_and_port_accepted(self):
        result = _parse_optional_http_url("http://rtvi:8000")
        assert result is not None
        assert result.hostname == "rtvi"
        assert result.port == 8000

    def test_hostname_only_accepted(self):
        result = _parse_optional_http_url("http://rtvi.example.com")
        assert result is not None
        assert result.hostname == "rtvi.example.com"


class TestVideoUploadUrlModels:
    """Schema-level tests for ``POST /api/v1/videos``."""

    def test_input_requires_filename(self):
        with pytest.raises(ValidationError):
            VideoUploadUrlInput()

    def test_input_rejects_empty_filename(self):
        with pytest.raises(ValidationError):
            VideoUploadUrlInput(filename="")

    def test_input_ignores_extra_fields(self):
        # Forward-compat: the UI may forward dialog fields here too.
        model = VideoUploadUrlInput(filename="clip.mp4", extra="ignored")
        assert model.filename == "clip.mp4"

    def test_response_carries_url(self):
        response = VideoUploadUrlResponse(url="http://vst:30888/v1/storage/file")
        assert response.url == "http://vst:30888/v1/storage/file"


class TestVideoUploadCompleteInput:
    """Tests for the Pydantic model backing /complete.

    The model is intentionally permissive — the UI forwards the entire VST
    upload response, and only ``filename`` is read today.
    """

    def test_no_required_fields(self):
        # body can be empty: the path param carries the video_id.
        model = VideoUploadCompleteInput()
        assert model.filename is None
        assert model.custom_params is None

    def test_filename_optional(self):
        model = VideoUploadCompleteInput(filename="clip.mp4")
        assert model.filename == "clip.mp4"

    def test_extra_fields_from_full_vst_response_ignored(self):
        full_vst_response = {
            "sensorId": "sensor-1",
            "bytes": 1024,
            "chunkCount": "3",
            "chunkIdentifier": "abc-def",
            "filename": "clip",
            "filePath": "/home/vst/vst_release/streamer_videos/clip.mp4",
            "id": "c66efaeb-40f4-4ef0-9bbf-c06f0c3530ca",
            "streamId": "sensor-1",
            "created_at": "2026-04-23T02:53:04.498Z",
        }
        model = VideoUploadCompleteInput(**full_vst_response)
        assert model.filename == "clip"

    def test_custom_params_forwarded(self):
        model = VideoUploadCompleteInput(custom_params={"my_field": 42})
        assert model.custom_params == {"my_field": 42}


class TestRunPostUploadProcessing:
    """Tests for _run_post_upload_processing's graceful-degradation behavior."""

    @staticmethod
    def _timeline_patch(start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:10.000Z"):
        return patch(
            "vss_agents.api.video_ingest.get_timeline",
            new=AsyncMock(return_value=(start, end)),
        )

    @staticmethod
    def _mock_response(status_code=200, json_body=None, text="OK"):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body or {}
        resp.text = text
        return resp

    @pytest.mark.asyncio
    async def test_happy_path_with_cv_and_embed_configured(self):
        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})
        cv_resp = self._mock_response(200, {"ok": True})
        embed_resp = self._mock_response(200, {"usage": {"total_chunks_processed": 42}})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock(side_effect=[cv_resp, embed_resp])

        with self._timeline_patch(), patch("vss_agents.api.video_ingest.httpx.AsyncClient", return_value=client):
            result = await _run_post_upload_processing(
                camera_name="clip",
                sensor_id="sensor-abc",
                filename="clip.mp4",
                vst_url="http://vst:30888",
                rtvi_embed_base_url="http://rtvi-embed:8017",
                rtvi_cv_base_url="http://rtvi-cv:9000",
            )

        assert result.video_id == "sensor-abc"
        assert result.chunks_processed == 42
        assert "embeddings generated" in result.message

    @pytest.mark.asyncio
    async def test_rtvi_cv_unreachable_is_skipped_not_fatal(self):
        import httpx

        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})
        embed_resp = self._mock_response(200, {"usage": {"total_chunks_processed": 5}})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock(side_effect=[httpx.ConnectError("connection refused"), embed_resp])

        with self._timeline_patch(), patch("vss_agents.api.video_ingest.httpx.AsyncClient", return_value=client):
            result = await _run_post_upload_processing(
                camera_name="clip",
                sensor_id="sensor-abc",
                filename="clip.mp4",
                vst_url="http://vst:30888",
                rtvi_embed_base_url="http://rtvi-embed:8017",
                rtvi_cv_base_url="http://rtvi-cv:9000",
            )

        assert result.chunks_processed == 5

    @pytest.mark.asyncio
    async def test_embed_not_configured_skips_embeddings(self):
        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock()  # No CV or embed POSTs expected.

        with self._timeline_patch(), patch("vss_agents.api.video_ingest.httpx.AsyncClient", return_value=client):
            result = await _run_post_upload_processing(
                camera_name="clip",
                sensor_id="sensor-abc",
                filename="clip.mp4",
                vst_url="http://vst:30888",
                rtvi_embed_base_url="",
                rtvi_cv_base_url="",
            )

        assert result.chunks_processed == 0
        assert "embeddings generated" not in result.message
        assert client.post.call_count == 0

    @pytest.mark.asyncio
    async def test_storage_api_missing_video_url_is_502(self):
        storage_resp = self._mock_response(200, {"unexpected": "shape"})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock()

        with self._timeline_patch(), patch("vss_agents.api.video_ingest.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc_info:
                await _run_post_upload_processing(
                    camera_name="clip",
                    sensor_id="sensor-abc",
                    filename="clip.mp4",
                    vst_url="http://vst:30888",
                    rtvi_embed_base_url="http://rtvi-embed:8017",
                )
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_invalid_vst_url_is_500(self):
        storage_resp = self._mock_response(200, {"videoUrl": "http://vst/vst/storage/temp_files/clip.mp4"})
        cv_resp = self._mock_response(200, {"ok": True})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=storage_resp)
        client.post = AsyncMock(return_value=cv_resp)

        with self._timeline_patch(), patch("vss_agents.api.video_ingest.httpx.AsyncClient", return_value=client):
            with pytest.raises(HTTPException) as exc_info:
                await _run_post_upload_processing(
                    camera_name="clip",
                    sensor_id="sensor-abc",
                    filename="clip.mp4",
                    vst_url="",
                    rtvi_embed_base_url="http://rtvi-embed:8017",
                )
        assert exc_info.value.status_code == 500


class TestVideoUploadUrlRoute:
    """``POST /api/v1/videos`` returns the VST nvstreamer URL."""

    @staticmethod
    def _build_router(external_url: str = "http://vst.example.com:30888"):
        return create_video_upload_router(vst_external_url=external_url)

    def test_route_registered(self):
        paths = [r.path for r in self._build_router().routes]
        assert paths == ["/api/v1/videos"]

    @pytest.mark.asyncio
    async def test_returns_vst_storage_url(self):
        # Must include the `/vst/api` routing prefix so haproxy ingress forwards
        # the browser's chunked POST to VST (matches NEXT_PUBLIC_VST_API_URL).
        route = self._build_router("http://vst.example.com:30888").routes[0]
        response = await route.endpoint(VideoUploadUrlInput(filename="clip.mp4"))
        assert response.url == "http://vst.example.com:30888/vst/api/v1/storage/file"

    @pytest.mark.asyncio
    async def test_strips_trailing_slash(self):
        route = self._build_router("http://vst.example.com:30888/").routes[0]
        response = await route.endpoint(VideoUploadUrlInput(filename="clip.mp4"))
        assert response.url == "http://vst.example.com:30888/vst/api/v1/storage/file"

    @pytest.mark.asyncio
    async def test_rejects_whitespace_filename(self):
        route = self._build_router().routes[0]
        with pytest.raises(HTTPException) as exc_info:
            await route.endpoint(VideoUploadUrlInput(filename="my clip.mp4"))
        assert exc_info.value.status_code == 400
        assert "whitespace" in str(exc_info.value.detail).lower()


class TestUploadCompleteRoute:
    """The universal /complete route — single canonical completion path."""

    @staticmethod
    def _build_router():
        return create_video_upload_complete_router(
            vst_internal_url="http://vst:30888",
            rtvi_embed_base_url="",
            rtvi_cv_base_url="",
        )

    def test_complete_route_registered(self):
        paths = [r.path for r in self._build_router().routes]
        assert paths == ["/api/v1/videos/{video_id}/complete"]

    def test_complete_route_not_deprecated(self):
        route = self._build_router().routes[0]
        assert route.deprecated is not True

    @pytest.mark.asyncio
    async def test_handler_passes_video_id_as_sensor_id(self):
        """The path param is the VST stream id; body.filename drives RTVI's
        camera_name. The video_id flows straight into _run_post_upload_processing
        as sensor_id."""
        route = self._build_router().routes[0]
        body = VideoUploadCompleteInput(filename="clip.mp4")

        with patch(
            "vss_agents.api.video_ingest._run_post_upload_processing",
            new=AsyncMock(return_value=VideoIngestResponse(message="ok", video_id="sensor-xyz", filename="clip.mp4")),
        ) as mock_post:
            response = await route.endpoint(video_id="sensor-xyz", body=body)

        assert response.video_id == "sensor-xyz"
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        # filename strips its extension on the way to RTVI-CV's camera_name.
        assert kwargs["camera_name"] == "clip"
        assert kwargs["sensor_id"] == "sensor-xyz"
        assert kwargs["filename"] == "clip.mp4"

    @pytest.mark.asyncio
    async def test_filename_falls_back_to_video_id_when_body_omits_it(self):
        """If the UI doesn't forward filename, video_id at least populates
        the response message and RTVI camera_name."""
        route = self._build_router().routes[0]
        body = VideoUploadCompleteInput()  # no filename

        with patch(
            "vss_agents.api.video_ingest._run_post_upload_processing",
            new=AsyncMock(return_value=VideoIngestResponse(message="ok", video_id="sensor-xyz", filename="sensor-xyz")),
        ) as mock_post:
            await route.endpoint(video_id="sensor-xyz", body=body)

        kwargs = mock_post.call_args.kwargs
        assert kwargs["filename"] == "sensor-xyz"
        assert kwargs["camera_name"] == "sensor-xyz"


class TestResolveVideoUploadConfig:
    """Pin down config resolution: YAML wins, env-var fallback."""

    def test_streaming_ingest_config_wins(self):
        config = MagicMock()
        cfg = MagicMock()
        cfg.vst_internal_url = "http://vst:8080"
        cfg.vst_external_url = "http://vst.public:8080"
        cfg.rtvi_embed_base_url = "http://rtvi-embed:8017"
        cfg.rtvi_cv_base_url = "http://rtvi-cv:9000"
        cfg.rtvi_embed_model = "cosmos-embed1-448p"
        cfg.rtvi_embed_chunk_duration = 5
        config.general.front_end.streaming_ingest = cfg

        resolved = _resolve_video_upload_config(config)
        assert resolved is not None
        assert resolved.vst_internal_url == "http://vst:8080"
        assert resolved.vst_external_url == "http://vst.public:8080"
        assert resolved.rtvi_embed_base_url == "http://rtvi-embed:8017"

    def test_external_url_falls_back_to_internal_when_unset(self):
        config = MagicMock()
        cfg = MagicMock()
        cfg.vst_internal_url = "http://vst:8080"
        cfg.vst_external_url = ""
        cfg.rtvi_embed_base_url = ""
        cfg.rtvi_cv_base_url = ""
        cfg.rtvi_embed_model = "cosmos-embed1-448p"
        cfg.rtvi_embed_chunk_duration = 5
        config.general.front_end.streaming_ingest = cfg

        with patch.dict("os.environ", {"VST_EXTERNAL_URL": ""}, clear=False):
            resolved = _resolve_video_upload_config(config)

        assert resolved is not None
        assert resolved.vst_external_url == "http://vst:8080"

    def test_falls_back_to_env_when_streaming_ingest_missing(self):
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        env = {
            "VST_INTERNAL_URL": "http://vst:30888",
            "VST_EXTERNAL_URL": "http://vst.public:30888",
            "HOST_IP": "10.0.0.5",
            "RTVI_EMBED_PORT": "8017",
            "RTVI_CV_PORT": "9000",
        }
        with patch.dict("os.environ", env, clear=False):
            resolved = _resolve_video_upload_config(config)

        assert resolved is not None
        assert resolved.vst_internal_url == "http://vst:30888"
        assert resolved.vst_external_url == "http://vst.public:30888"
        assert resolved.rtvi_embed_base_url == "http://10.0.0.5:8017"
        assert resolved.rtvi_cv_base_url == "http://10.0.0.5:9000"

    def test_returns_none_when_vst_url_unavailable(self):
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        with patch.dict("os.environ", {"VST_INTERNAL_URL": "", "HOST_IP": ""}, clear=False):
            resolved = _resolve_video_upload_config(config)

        assert resolved is None


class TestRegisterVideoUpload:
    """Registration paths for POST /api/v1/videos."""

    def test_registers_router_when_vst_configured(self):
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = MagicMock(
            vst_internal_url="http://vst:8080",
            vst_external_url="http://vst.public:8080",
            rtvi_embed_base_url="",
            rtvi_cv_base_url="",
            rtvi_embed_model="cosmos-embed1-448p",
            rtvi_embed_chunk_duration=5,
        )

        register_video_upload(app, config)

        assert app.include_router.called

    def test_skips_when_vst_unavailable(self):
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        with patch.dict("os.environ", {"VST_INTERNAL_URL": "", "HOST_IP": ""}, clear=False):
            register_video_upload(app, config)

        assert not app.include_router.called


class TestRegisterVideoUploadComplete:
    """Registration paths for POST /api/v1/videos/{video_id}/complete."""

    def test_registers_router_when_vst_configured(self):
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = MagicMock(
            vst_internal_url="http://vst:8080",
            vst_external_url="http://vst:8080",
            rtvi_embed_base_url="http://rtvi-embed:8017",
            rtvi_cv_base_url="",
            rtvi_embed_model="cosmos-embed1-448p",
            rtvi_embed_chunk_duration=5,
        )

        register_video_upload_complete(app, config)

        assert app.include_router.called

    def test_skips_with_warning_when_vst_unavailable(self):
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        with patch.dict("os.environ", {"VST_INTERNAL_URL": "", "HOST_IP": ""}, clear=False):
            register_video_upload_complete(app, config)

        assert not app.include_router.called

    def test_register_path_does_not_require_rtvi_to_be_configured(self):
        """The upload-complete handler registers even when RTVI isn't
        available — the handler self-skips downstream calls. Locks in that
        base/alerts/lvs profiles get a working completion path."""
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
            register_video_upload_complete(app, config)

        assert app.include_router.called
