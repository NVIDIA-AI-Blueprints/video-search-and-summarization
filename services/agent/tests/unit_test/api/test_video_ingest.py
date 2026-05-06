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
"""Unit tests for the universal videos.py /complete route."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from vss_agents.api.video_ingest import VideoIngestResponse
from vss_agents.api.video_ingest import VideoUploadCompleteInput
from vss_agents.api.video_ingest import _parse_optional_http_url
from vss_agents.api.video_ingest import _resolve_video_upload_config
from vss_agents.api.video_ingest import _run_post_upload_processing
from vss_agents.api.video_ingest import create_video_upload_complete_router
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
        # No hostname to connect to — can't be used as a service URL.
        assert _parse_optional_http_url("http://") is None
        assert _parse_optional_http_url("http:") is None

    def test_empty_port_body_rejected(self):
        # `http://host:` (trailing colon, empty port body) parses with
        # hostname="host" and port=None — Python urlparse silently leaves the
        # netloc as `host:`, so callers would fall back to the scheme's default
        # port (80) and connect to nothing. Treat it as misconfigured.
        assert _parse_optional_http_url("http://host:") is None

    def test_explicit_host_and_port_accepted(self):
        result = _parse_optional_http_url("http://rtvi:8000")
        assert result is not None
        assert result.hostname == "rtvi"
        assert result.port == 8000

    def test_hostname_only_accepted(self):
        # URL relying on scheme's default port — must not be mis-classified
        # as "not configured".
        result = _parse_optional_http_url("http://rtvi.example.com")
        assert result is not None
        assert result.hostname == "rtvi.example.com"


class TestVideoUploadCompleteInput:
    """Tests for the Pydantic model backing /complete.

    Pin down the three flags that define the contract:
      - alias="sensorId" so VST's camelCase field name is accepted
      - populate_by_name=True so snake_case sensor_id still validates
      - extra="ignore" so forwarding the full VST upload response works
    """

    def test_camelcase_sensor_id_accepted(self):
        model = VideoUploadCompleteInput(**{"sensorId": "sensor-abc"})
        assert model.sensor_id == "sensor-abc"

    def test_snake_case_sensor_id_accepted(self):
        model = VideoUploadCompleteInput(sensor_id="sensor-abc")
        assert model.sensor_id == "sensor-abc"

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
        assert model.sensor_id == "sensor-1"

    def test_missing_sensor_id_rejected(self):
        with pytest.raises(ValidationError):
            VideoUploadCompleteInput()

    def test_empty_sensor_id_rejected_by_min_length(self):
        with pytest.raises(ValidationError, match=r"min_length|at least 1"):
            VideoUploadCompleteInput(**{"sensorId": ""})


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
        # First POST (CV) raises ConnectError; second POST (embed) succeeds.
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
                    vst_url="",  # wraps to "http://" → urlparse hostname=None → 500
                    rtvi_embed_base_url="http://rtvi-embed:8017",
                )
        assert exc_info.value.status_code == 500


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
        assert paths == ["/api/v1/videos/{filename}/complete"]

    def test_complete_route_not_deprecated(self):
        # The /complete path is the canonical universal endpoint.
        route = self._build_router().routes[0]
        assert route.deprecated is not True

    @pytest.mark.asyncio
    async def test_handler_invokes_post_processing(self):
        """Endpoint delegates to _run_post_upload_processing with the
        filename-without-extension as camera_name and the body's sensor_id."""
        route = self._build_router().routes[0]

        body = VideoUploadCompleteInput(**{"sensorId": "sensor-xyz"})

        with patch(
            "vss_agents.api.video_ingest._run_post_upload_processing",
            new=AsyncMock(return_value=VideoIngestResponse(message="ok", video_id="sensor-xyz", filename="clip.mp4")),
        ) as mock_post:
            response = await route.endpoint(filename="clip.mp4", body=body)

        assert response.video_id == "sensor-xyz"
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        # Filename strips its extension on the way to RTVI-CV's camera_name.
        assert kwargs["camera_name"] == "clip"
        assert kwargs["sensor_id"] == "sensor-xyz"


class TestResolveVideoUploadConfig:
    """Pin down config resolution: YAML wins, env-var fallback."""

    def test_streaming_ingest_config_wins(self):
        config = MagicMock()
        cfg = MagicMock()
        cfg.vst_internal_url = "http://vst:8080"
        cfg.rtvi_embed_base_url = "http://rtvi-embed:8017"
        cfg.rtvi_cv_base_url = "http://rtvi-cv:9000"
        cfg.rtvi_embed_model = "cosmos-embed1-448p"
        cfg.rtvi_embed_chunk_duration = 5
        config.general.front_end.streaming_ingest = cfg

        resolved = _resolve_video_upload_config(config)
        assert resolved is not None
        assert resolved.vst_internal_url == "http://vst:8080"
        assert resolved.rtvi_embed_base_url == "http://rtvi-embed:8017"

    def test_falls_back_to_env_when_streaming_ingest_missing(self):
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        env = {
            "VST_INTERNAL_URL": "http://vst:30888",
            "HOST_IP": "10.0.0.5",
            "RTVI_EMBED_PORT": "8017",
            "RTVI_CV_PORT": "9000",
        }
        with patch.dict("os.environ", env, clear=False):
            resolved = _resolve_video_upload_config(config)

        assert resolved is not None
        assert resolved.vst_internal_url == "http://vst:30888"
        assert resolved.rtvi_embed_base_url == "http://10.0.0.5:8017"
        assert resolved.rtvi_cv_base_url == "http://10.0.0.5:9000"

    def test_returns_none_when_vst_url_unavailable(self):
        config = MagicMock()
        config.general.front_end.streaming_ingest = None

        # Make sure the env vars we care about are unset.
        with patch.dict("os.environ", {"VST_INTERNAL_URL": "", "HOST_IP": ""}, clear=False):
            resolved = _resolve_video_upload_config(config)

        assert resolved is None


class TestRegisterVideoUploadComplete:
    """Registration paths for POST /api/v1/videos/{filename}/complete."""

    def test_registers_router_when_vst_configured(self):
        app = MagicMock(spec=FastAPI)
        config = MagicMock()
        config.general.front_end.streaming_ingest = MagicMock(
            vst_internal_url="http://vst:8080",
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
            "RTVI_EMBED_PORT": "",  # RTVI not deployed
            "RTVI_CV_PORT": "",
        }
        with patch.dict("os.environ", env, clear=False):
            register_video_upload_complete(app, config)

        assert app.include_router.called
