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
"""Unit tests for CV-compatible stream API models (Bug: 5881430).

Tests the Pydantic models in api_models.live_stream that support the
/v1/stream/add, /v1/stream/remove, and /v1/stream/get-stream-info endpoints.

These tests validate model construction, field validation, and property logic
without requiring a running server.
"""

import pytest
from pydantic import ValidationError

live_stream = pytest.importorskip(
    "api_models.live_stream",
    reason="api_models.live_stream not importable (run inside container with PYTHONPATH=../src)",
)

StreamMetadata = live_stream.StreamMetadata
MAX_GENERATION_TOKENS = live_stream.MAX_GENERATION_TOKENS
StreamAddValue = live_stream.StreamAddValue
StreamAddHeaders = live_stream.StreamAddHeaders
StreamAddRequest = live_stream.StreamAddRequest
StreamAddResponse = live_stream.StreamAddResponse
StreamRemoveValue = live_stream.StreamRemoveValue
StreamRemoveRequest = live_stream.StreamRemoveRequest
StreamRemoveResponse = live_stream.StreamRemoveResponse
StreamInfo = live_stream.StreamInfo
StreamInfoResponse = live_stream.StreamInfoResponse


# =============================================================================
# StreamMetadata.has_inference_params
# =============================================================================


class TestStreamMetadataHasInferenceParams:
    """Test the has_inference_params property on StreamMetadata."""

    def test_default_metadata_has_no_inference_params(self):
        meta = StreamMetadata()
        assert meta.has_inference_params is False

    def test_metadata_with_prompt_has_inference_params(self):
        meta = StreamMetadata(prompt="Describe this scene")
        assert meta.has_inference_params is True

    def test_metadata_with_model_but_no_prompt(self):
        meta = StreamMetadata(model="cosmos-reason1")
        assert meta.has_inference_params is True  # model triggers embed inference
        assert meta.has_vlm_inference_params is False
        assert meta.has_embed_inference_params is True

    def test_metadata_with_empty_prompt_has_inference_params(self):
        """An explicit empty string prompt counts as 'provided'."""
        meta = StreamMetadata(prompt="")
        assert meta.has_inference_params is True

    def test_metadata_with_all_inference_fields(self):
        meta = StreamMetadata(
            prompt="test",
            system_prompt="sys",
            model="m",
            max_tokens=128,
            temperature=0.5,
            top_p=0.9,
        )
        assert meta.has_inference_params is True

    def test_metadata_with_cv_fields_only(self):
        meta = StreamMetadata(resolution="1920x1080", codec="H264", framerate=30.0)
        assert meta.has_inference_params is False


# =============================================================================
# StreamMetadata field validation
# =============================================================================


class TestStreamMetadataValidation:
    """Test field-level validation on StreamMetadata."""

    def test_valid_resolution_pattern(self):
        meta = StreamMetadata(resolution="3840x2160")
        assert meta.resolution == "3840x2160"

    def test_invalid_resolution_pattern(self):
        with pytest.raises(ValidationError):
            StreamMetadata(resolution="1080p")

    def test_framerate_lower_bound(self):
        with pytest.raises(ValidationError):
            StreamMetadata(framerate=0.0)

    def test_framerate_upper_bound(self):
        with pytest.raises(ValidationError):
            StreamMetadata(framerate=300.0)

    def test_max_tokens_bounds(self):
        with pytest.raises(ValidationError):
            StreamMetadata(max_tokens=0)
        meta = StreamMetadata(max_tokens=5000)
        assert meta.max_tokens == 5000
        meta = StreamMetadata(max_tokens=MAX_GENERATION_TOKENS)
        assert meta.max_tokens == MAX_GENERATION_TOKENS
        with pytest.raises(ValidationError):
            StreamMetadata(max_tokens=MAX_GENERATION_TOKENS + 1)

    def test_temperature_bounds(self):
        with pytest.raises(ValidationError):
            StreamMetadata(temperature=-0.1)
        with pytest.raises(ValidationError):
            StreamMetadata(temperature=1.1)

    def test_prompt_accepts_10240_chars(self):
        """StreamMetadata.prompt should mirror VlmQuery.prompt — 10,240 default."""
        meta = StreamMetadata(prompt="x" * 10240)
        assert len(meta.prompt) == 10240

    def test_prompt_rejects_above_10240_chars(self):
        """StreamMetadata.prompt should reject above the documented limit."""
        with pytest.raises(ValidationError, match="10240"):
            StreamMetadata(prompt="x" * 10241)

    def test_prompt_limit_can_be_raised_with_env(self, monkeypatch):
        """VLM_PROMPT_MAX_LENGTH should also raise the StreamMetadata limit."""
        monkeypatch.setenv("VLM_PROMPT_MAX_LENGTH", "10241")

        meta = StreamMetadata(prompt="x" * 10241)

        assert len(meta.prompt) == 10241

    def test_prompt_limit_error_mentions_env_var(self, monkeypatch):
        """Oversized StreamMetadata.prompt errors should name the env override."""
        monkeypatch.delenv("VLM_PROMPT_MAX_LENGTH", raising=False)

        with pytest.raises(ValidationError) as exc_info:
            StreamMetadata(prompt="x" * 10241)

        message = str(exc_info.value)
        assert "10240" in message
        assert "VLM_PROMPT_MAX_LENGTH" in message

    def test_system_prompt_accepts_10240_chars(self):
        meta = StreamMetadata(system_prompt="x" * 10240)
        assert len(meta.system_prompt) == 10240

    def test_system_prompt_rejects_above_10240_chars(self):
        with pytest.raises(ValidationError, match="10240"):
            StreamMetadata(system_prompt="x" * 10241)

    def test_system_prompt_limit_can_be_raised_with_env(self, monkeypatch):
        monkeypatch.setenv("VLM_SYSTEM_PROMPT_MAX_LENGTH", "10241")

        meta = StreamMetadata(system_prompt="x" * 10241)

        assert len(meta.system_prompt) == 10241

    def test_system_prompt_limit_error_mentions_env_var(self, monkeypatch):
        monkeypatch.delenv("VLM_SYSTEM_PROMPT_MAX_LENGTH", raising=False)

        with pytest.raises(ValidationError) as exc_info:
            StreamMetadata(system_prompt="x" * 10241)

        message = str(exc_info.value)
        assert "10240" in message
        assert "VLM_SYSTEM_PROMPT_MAX_LENGTH" in message

    def test_prompt_none_still_allowed(self):
        """Passthrough mode: no inference params, no prompt validation."""
        meta = StreamMetadata()
        assert meta.prompt is None
        assert meta.system_prompt is None

    def test_response_format_type_valid(self):
        meta = StreamMetadata(response_format_type="json_object")
        assert meta.response_format_type == "json_object"

    def test_response_format_type_invalid(self):
        with pytest.raises(ValidationError):
            StreamMetadata(response_format_type="xml")

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            StreamMetadata(unknown_field="value")


# =============================================================================
# StreamAddRequest
# =============================================================================


class TestStreamAddRequest:
    """Test StreamAddRequest construction and validation."""

    def _make_value(self, **overrides):
        defaults = {
            "camera_id": "camera-001",
            "camera_url": "rtsp://host:554/live/video",
            "change": "camera_add",
        }
        defaults.update(overrides)
        return defaults

    def test_valid_request_all_fields(self):
        req = StreamAddRequest(
            key="sensor",
            value=self._make_value(
                camera_name="Front Entrance",
                creation_time="2025-01-01T00:00:00Z",
                metadata={"prompt": "Describe", "resolution": "1920x1080"},
            ),
            headers={"source": "vst"},
        )
        assert req.value.camera_id == "camera-001"
        assert req.value.metadata.has_inference_params is True
        assert req.headers.source == "vst"

    def test_valid_request_no_metadata(self):
        req = StreamAddRequest(key="sensor", value=self._make_value())
        assert req.value.metadata is None

    def test_valid_request_metadata_without_prompt(self):
        req = StreamAddRequest(
            key="sensor",
            value=self._make_value(metadata={"resolution": "1280x720"}),
        )
        assert req.value.metadata is not None
        assert req.value.metadata.has_inference_params is False

    def test_missing_camera_id(self):
        with pytest.raises(ValidationError, match="camera_id"):
            StreamAddRequest(
                key="sensor",
                value={"camera_url": "rtsp://host/video", "change": "camera_add"},
            )

    def test_missing_camera_url(self):
        with pytest.raises(ValidationError, match="camera_url"):
            StreamAddRequest(
                key="sensor",
                value={"camera_id": "cam1", "change": "camera_add"},
            )

    def test_invalid_url_scheme_ftp(self):
        with pytest.raises(ValidationError):
            StreamAddRequest(
                key="sensor",
                value=self._make_value(camera_url="ftp://host/file.mp4"),
            )

    def test_valid_url_scheme_file(self):
        req = StreamAddRequest(
            key="sensor",
            value=self._make_value(camera_url="file:///data/video.mp4"),
        )
        assert req.value.camera_url == "file:///data/video.mp4"

    def test_valid_url_scheme_http(self):
        req = StreamAddRequest(
            key="sensor",
            value=self._make_value(camera_url="http://host/stream.m3u8"),
        )
        assert req.value.camera_url == "http://host/stream.m3u8"

    def test_valid_url_scheme_https(self):
        req = StreamAddRequest(
            key="sensor",
            value=self._make_value(camera_url="https://host/stream.m3u8"),
        )
        assert req.value.camera_url == "https://host/stream.m3u8"


# =============================================================================
# StreamRemoveRequest
# =============================================================================


class TestStreamRemoveRequest:
    """Test StreamRemoveRequest construction and validation."""

    def test_valid_remove_request(self):
        req = StreamRemoveRequest(
            key="sensor",
            value={"camera_id": "camera-001", "change": "camera_remove"},
        )
        assert req.value.camera_id == "camera-001"

    def test_missing_camera_id(self):
        with pytest.raises(ValidationError, match="camera_id"):
            StreamRemoveRequest(
                key="sensor",
                value={"change": "camera_remove"},
            )

    def test_optional_camera_url(self):
        req = StreamRemoveRequest(
            key="sensor",
            value={
                "camera_id": "cam-1",
                "change": "camera_remove",
                "camera_url": "rtsp://host/video",
            },
        )
        assert req.value.camera_url == "rtsp://host/video"

    def test_remove_with_headers(self):
        req = StreamRemoveRequest(
            key="sensor",
            value={"camera_id": "cam-1", "change": "camera_remove"},
            headers={"source": "vst", "created_at": "2025-01-01T00:00:00Z"},
        )
        assert req.headers.source == "vst"


# =============================================================================
# StreamAddResponse
# =============================================================================


class TestStreamAddResponse:
    """Test StreamAddResponse construction."""

    def test_processing_with_inference(self):
        resp = StreamAddResponse(
            camera_id="cam-1",
            asset_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            status="processing",
            inference=True,
        )
        assert resp.status == "processing"
        assert resp.inference is True

    def test_added_without_inference(self):
        resp = StreamAddResponse(
            camera_id="cam-2",
            asset_id="11111111-2222-3333-4444-555555555555",
            status="added",
            inference=False,
        )
        assert resp.status == "added"
        assert resp.inference is False


# =============================================================================
# StreamRemoveResponse
# =============================================================================


class TestStreamRemoveResponse:
    """Test StreamRemoveResponse construction."""

    def test_default_status(self):
        resp = StreamRemoveResponse(
            camera_id="cam-1",
            asset_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        assert resp.status == "removed"

    def test_custom_status(self):
        resp = StreamRemoveResponse(
            camera_id="cam-1",
            asset_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            status="not_found",
        )
        assert resp.status == "not_found"


# =============================================================================
# StreamInfo / StreamInfoResponse
# =============================================================================


class TestStreamInfo:
    """Test StreamInfo model construction."""

    def test_all_fields(self):
        info = StreamInfo(
            camera_id="cam-1",
            camera_name="Front Door",
            camera_url="rtsp://host:554/live/video",
            asset_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            source_id=42,
            sensor_id="sensor-abc",
            inference_active=True,
            chunk_duration=60,
            chunk_overlap_duration=5,
        )
        assert info.camera_id == "cam-1"
        assert info.camera_name == "Front Door"
        assert info.source_id == 42
        assert info.inference_active is True

    def test_optional_fields_omitted(self):
        info = StreamInfo(
            camera_id="cam-2",
            camera_url="rtsp://host/video",
            asset_id="11111111-2222-3333-4444-555555555555",
            inference_active=False,
        )
        assert info.camera_name is None
        assert info.source_id is None
        assert info.sensor_id is None
        assert info.chunk_duration == 0
        assert info.chunk_overlap_duration == 0


class TestStreamInfoResponse:
    """Test StreamInfoResponse model construction."""

    def test_empty_stream_list(self):
        resp = StreamInfoResponse(stream_count=0)
        assert resp.status == "ok"
        assert resp.stream_count == 0
        assert resp.stream_list == []

    def test_with_streams(self):
        stream = StreamInfo(
            camera_id="cam-1",
            camera_url="rtsp://host/video",
            asset_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            inference_active=True,
        )
        resp = StreamInfoResponse(stream_count=1, stream_list=[stream])
        assert resp.stream_count == 1
        assert len(resp.stream_list) == 1
        assert resp.stream_list[0].camera_id == "cam-1"
