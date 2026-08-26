################################################################################
#  SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES.
#  All rights reserved.
#  SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
#  NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
#  property and proprietary rights in and to this material, related
#  documentation and any modifications thereto. Any use, reproduction,
#  disclosure or distribution of this material and related documentation
#  without an express license agreement from NVIDIA CORPORATION or
#  its affiliates is strictly prohibited.
################################################################################

"""Unit tests for features migrated from via-engine into openai_compat_model.py:
  - strip_thinking_tags()
  - video_embeds_to_mp4_base64()
  - REMOTE_VIDEO_INPUT / REMOTE_VIDEO_AS_IMAGES logic
  - extra_body (media_io_kwargs, min_tokens, ignore_eos)
  - _nim_start/_nim_end timing
  - reasoning_description in VlmModelOutput

Run with:
  PYTHONPATH=src pytest tests/model/openai_compat/test_migrated_features.py -v
"""

import io
import os
import sys
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch

from common.chunk_info import ChunkInfo
from models.base_vlm_model import VlmGenerationConfig, VlmModelOutput
from models.openai_compat.openai_compat_model import (
    CompOpenAIModel,
    _decode_jpegs_cpu,
    _decode_jpegs_pyav,
    _encode_h264_cpu,
    _encode_h264_nvenc,
    _encode_h264_pyav,
    _pynvcodec_nvenc_available,
    _rgb_to_nv12,
    _rgb_to_nv12_tensor,
    strip_thinking_tags,
    video_embeds_to_mp4_base64,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(extra_env=None):
    """Instantiate CompOpenAIModel with a fake OpenAI client (no real API calls)."""
    env = {
        "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME": "test-model",
        "OPENAI_API_KEY": "fake-key-for-tests",
        "VIA_VLM_ENDPOINT": "http://localhost:9999/v1/",
        "VIA_VLM_API_KEY": "fake-api-key",
        "AZURE_OPENAI_ENDPOINT": "",
        "NV_LLMG_CLIENT_SECRET": "",
    }
    if extra_env:
        env.update(extra_env)

    with patch.dict(os.environ, env, clear=False):
        # Patch OpenAI constructor to avoid real HTTP connection
        with patch("models.openai_compat.openai_compat_model.CompOpenAIModel.configure_openai"):
            model = CompOpenAIModel()
    # Replace client with a mock after init
    model._client = MagicMock()
    model._model = None
    model._nvSecretConfigured = False
    model._model_name = "test-model"
    return model


def _make_chunk(filename="test.mp4", start_pts=0, end_pts=5_000_000_000):
    chunk = ChunkInfo()
    chunk.file = filename
    chunk.start_pts = start_pts
    chunk.end_pts = end_pts
    return chunk


def _make_mock_response(content="Test response."):
    """Build a mock OpenAI chat.completions.create response."""
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20)
    return SimpleNamespace(choices=[choice], usage=usage)


def _fake_base64_jpeg(*args, **kwargs):
    return "fakejpegbase64"


def _fake_jpeg_bytes():
    return np.asarray([0xFF, 0xD8, 0xFF, 0xD9], dtype=np.uint8)


# ---------------------------------------------------------------------------
# strip_thinking_tags tests
# ---------------------------------------------------------------------------


class TestStripThinkingTags:
    def test_both_tags(self):
        content = "<think>This is my reasoning.</think>\nFinal answer."
        text, reasoning = strip_thinking_tags(content)
        assert "Final answer." in text
        assert "<think>" not in text
        assert "</think>" not in text
        assert reasoning == "This is my reasoning."

    def test_only_closing_tag(self):
        content = "Continuing reasoning here.\n</think>\nFinal answer."
        text, reasoning = strip_thinking_tags(content)
        assert "Final answer." in text
        assert "</think>" not in text
        assert "Continuing reasoning here." in reasoning

    def test_only_opening_tag(self):
        content = "Some prefix.\n<think>Incomplete reasoning"
        text, reasoning = strip_thinking_tags(content)
        assert "Some prefix." in text
        assert "<think>" not in text
        assert "Incomplete reasoning" in reasoning

    def test_no_tags_passthrough(self):
        content = "Plain response with no tags."
        text, reasoning = strip_thinking_tags(content)
        assert text == "Plain response with no tags."
        assert reasoning == ""

    def test_answer_tags_stripped(self):
        content = "<answer>The answer is 42.</answer>"
        text, reasoning = strip_thinking_tags(content)
        assert "<answer>" not in text
        assert "</answer>" not in text
        assert "The answer is 42." in text

    def test_summary_tags_stripped(self):
        content = "<summary>A brief summary.</summary>"
        text, reasoning = strip_thinking_tags(content)
        assert "<summary>" not in text
        assert "</summary>" not in text
        assert "A brief summary." in text

    def test_think_with_answer(self):
        content = "<think>reasoning</think><answer>result</answer>"
        text, reasoning = strip_thinking_tags(content)
        assert reasoning == "reasoning"
        assert "result" in text
        assert "<think>" not in text
        assert "<answer>" not in text

    def test_multiline_reasoning(self):
        content = "<think>\nLine 1\nLine 2\n</think>\nConclusion."
        text, reasoning = strip_thinking_tags(content)
        assert "Line 1" in reasoning
        assert "Line 2" in reasoning
        assert "Conclusion." in text

    def test_response_json_header_stripped(self):
        content = '### Response Json\n{"key": "value"}'
        text, reasoning = strip_thinking_tags(content)
        assert "### Response Json" not in text
        assert '{"key": "value"}' in text


# ---------------------------------------------------------------------------
# video_embeds_to_mp4_base64 tests
# ---------------------------------------------------------------------------


class TestVideoEmbedsMp4:
    def test_non_tensor_returns_none(self):
        result_b64, result_fps = video_embeds_to_mp4_base64("not a tensor")
        assert result_b64 is None
        assert result_fps is None

    def test_none_returns_none(self):
        result_b64, result_fps = video_embeds_to_mp4_base64(None)
        assert result_b64 is None
        assert result_fps is None

    def test_list_returns_none(self):
        result_b64, result_fps = video_embeds_to_mp4_base64([1, 2, 3])
        assert result_b64 is None
        assert result_fps is None

    def test_gpu_path_used_when_available(self):
        """When GPU decode+encode succeed, they are used and result is non-None."""
        dummy_tensor = MagicMock(spec=torch.Tensor)
        fake_frames = [MagicMock()]
        with patch(
            "models.openai_compat.openai_compat_model.jpeg_single_tensor_to_array_of_numpys",
            return_value=[_fake_jpeg_bytes()],
        ):
            with patch(
                "models.openai_compat.openai_compat_model._decode_jpegs_gpu",
                return_value=fake_frames,
            ):
                with patch(
                    "models.openai_compat.openai_compat_model._encode_h264_nvenc",
                    return_value=True,
                ):
                    with patch("builtins.open", mock.mock_open(read_data=b"fakemp4")):
                        with patch("os.path.exists", return_value=False):
                            b64, fps = video_embeds_to_mp4_base64(dummy_tensor)
        assert b64 is not None

    def test_cpu_fallback_when_gpu_unavailable(self):
        """CPU path is used when GPU decode returns None."""
        dummy_tensor = MagicMock(spec=torch.Tensor)
        fake_frames = [MagicMock()]
        with patch(
            "models.openai_compat.openai_compat_model.jpeg_single_tensor_to_array_of_numpys",
            return_value=[_fake_jpeg_bytes()],
        ):
            with patch(
                "models.openai_compat.openai_compat_model._decode_jpegs_gpu",
                return_value=None,
            ):
                with patch(
                    "models.openai_compat.openai_compat_model._decode_jpegs_pyav",
                    return_value=[],
                ):
                    with patch(
                        "models.openai_compat.openai_compat_model._decode_jpegs_cpu",
                        return_value=fake_frames,
                    ) as mock_cpu_decode:
                        with patch(
                            "models.openai_compat.openai_compat_model._encode_h264_pyav",
                            return_value=None,
                        ):
                            with patch(
                                "models.openai_compat.openai_compat_model._encode_h264_cpu",
                                return_value=True,
                            ):
                                with patch(
                                    "builtins.open", mock.mock_open(read_data=b"fakemp4")
                                ):
                                    with patch("os.path.exists", return_value=False):
                                        b64, fps = video_embeds_to_mp4_base64(dummy_tensor)
        mock_cpu_decode.assert_called_once()
        assert b64 is not None

    def test_nvenc_failure_falls_back_to_cpu_encode(self):
        """When GPU decode succeeds but NVENC fails, CPU encode receives numpy arrays."""
        import numpy as np

        dummy_tensor = MagicMock(spec=torch.Tensor)
        # Simulate a (3, H, W) CUDA tensor returned by _decode_jpegs_gpu
        fake_cuda_frame = MagicMock()
        fake_numpy_frame = np.zeros((8, 8, 3), dtype=np.uint8)
        # Chained call: .permute(1,2,0).cpu().numpy() returns fake numpy
        fake_cuda_frame.permute.return_value.cpu.return_value.numpy.return_value = fake_numpy_frame

        cpu_enc_call_args = []

        def capture_cpu_enc(frames, fps, path):
            cpu_enc_call_args.append(frames)
            return True

        with patch(
            "models.openai_compat.openai_compat_model.jpeg_single_tensor_to_array_of_numpys",
            return_value=[_fake_jpeg_bytes()],
        ):
            with patch(
                "models.openai_compat.openai_compat_model._decode_jpegs_gpu",
                return_value=[fake_cuda_frame],
            ):
                with patch(
                    "models.openai_compat.openai_compat_model._encode_h264_nvenc",
                    return_value=None,  # NVENC fails
                ):
                    with patch(
                        "models.openai_compat.openai_compat_model._encode_h264_pyav",
                        return_value=None,
                    ):
                        with patch(
                            "models.openai_compat.openai_compat_model._encode_h264_cpu",
                            side_effect=capture_cpu_enc,
                        ):
                            with patch(
                                "builtins.open", mock.mock_open(read_data=b"fakemp4")
                            ):
                                with patch("os.path.exists", return_value=False):
                                    b64, fps = video_embeds_to_mp4_base64(dummy_tensor)

        assert len(cpu_enc_call_args) == 1, "_encode_h264_cpu should be called exactly once"
        # Verify that numpy arrays (not CUDA tensors) were passed to CPU encode
        passed_frames = cpu_enc_call_args[0]
        assert isinstance(passed_frames, list)
        assert isinstance(
            passed_frames[0], np.ndarray
        ), "CPU encode should receive numpy arrays, not CUDA tensors"


class TestRgbToNv12:
    @pytest.mark.no_gpu
    def test_output_shape(self):
        import numpy as np

        h, w = 8, 10  # even dimensions
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        nv12 = _rgb_to_nv12(rgb)
        assert nv12.shape == (h * 3 // 2, w)
        assert nv12.dtype == np.uint8

    @pytest.mark.no_gpu
    def test_odd_dimensions_cropped(self):
        import numpy as np

        rgb = np.zeros((9, 11, 3), dtype=np.uint8)  # odd dims
        nv12 = _rgb_to_nv12(rgb)
        # Should be cropped to (8, 10) before NV12 conversion
        assert nv12.shape == (8 * 3 // 2, 10)

    @pytest.mark.no_gpu
    def test_black_frame_y_plane(self):
        import numpy as np

        rgb = np.zeros((8, 8, 3), dtype=np.uint8)  # black
        nv12 = _rgb_to_nv12(rgb)
        y_plane = nv12[:8, :]
        # Black frame: Y ≈ 16 (limited range), U/V ≈ 128
        assert y_plane.max() <= 20  # near 16

    @pytest.mark.no_gpu
    def test_white_frame_y_plane(self):
        import numpy as np

        rgb = np.full((8, 8, 3), 255, dtype=np.uint8)  # white
        nv12 = _rgb_to_nv12(rgb)
        y_plane = nv12[:8, :]
        # White frame: Y ≈ 235 (limited range)
        assert y_plane.min() >= 230


class TestRgbToNv12Tensor:
    @pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available")
    def test_output_shape_cuda(self):
        h, w = 8, 10  # even dimensions
        rgb = torch.zeros(3, h, w, dtype=torch.uint8, device="cuda")
        nv12 = _rgb_to_nv12_tensor(rgb)
        assert nv12.shape == (h * 3 // 2, w)
        assert nv12.dtype == torch.uint8
        assert nv12.device.type == "cuda"

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available")
    def test_odd_dimensions_cropped(self):
        rgb = torch.zeros(3, 9, 11, dtype=torch.uint8, device="cuda")  # odd dims
        nv12 = _rgb_to_nv12_tensor(rgb)
        # Cropped to (8, 10) before NV12 conversion
        assert nv12.shape == (8 * 3 // 2, 10)

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available")
    def test_black_frame_y_plane(self):
        h, w = 8, 8
        rgb = torch.zeros(3, h, w, dtype=torch.uint8, device="cuda")  # black
        nv12 = _rgb_to_nv12_tensor(rgb)
        y_plane = nv12[:h, :]
        # Black frame: Y ≈ 16 (BT.601 limited range)
        assert y_plane.max().item() <= 20

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available")
    def test_white_frame_y_plane(self):
        h, w = 8, 8
        rgb = torch.full((3, h, w), 255, dtype=torch.uint8, device="cuda")  # white
        nv12 = _rgb_to_nv12_tensor(rgb)
        y_plane = nv12[:h, :]
        # White frame: Y ≈ 235 (BT.601 limited range)
        assert y_plane.min().item() >= 230

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available")
    def test_matches_cpu_reference(self):
        """GPU tensor NV12 conversion should match numpy reference within ±1 (rounding)."""
        import numpy as np

        torch.manual_seed(42)
        h, w = 16, 16
        rgb_np = np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)
        rgb_tensor = torch.from_numpy(rgb_np).permute(2, 0, 1).to("cuda")  # (3, H, W)

        nv12_gpu = _rgb_to_nv12_tensor(rgb_tensor).cpu().numpy()
        nv12_cpu = _rgb_to_nv12(rgb_np)

        assert np.abs(nv12_gpu.astype(np.int16) - nv12_cpu.astype(np.int16)).max() <= 1


class TestDecodeJpegsCpu:
    @pytest.mark.no_gpu
    def test_empty_input(self):
        frames = _decode_jpegs_cpu([])
        assert frames == []

    @pytest.mark.no_gpu
    def test_invalid_bytes_skipped(self):
        import numpy as np

        # Not valid JPEG — cv2.imdecode returns None, should be skipped
        bad = np.zeros(100, dtype=np.uint8)
        frames = _decode_jpegs_cpu([bad])
        assert frames == []


class TestEncodeH264Cpu:
    @pytest.mark.no_gpu
    def test_empty_frames_returns_none(self):
        result = _encode_h264_cpu([], 10.0, "/tmp/test_empty.mp4")
        assert result is None

    @pytest.mark.no_gpu
    def test_nvenc_not_available_returns_none(self):
        """_encode_h264_nvenc returns None when PyNvVideoCodec not importable."""
        with patch.dict("sys.modules", {"PyNvVideoCodec": None}):
            result = _encode_h264_nvenc([MagicMock()], 10.0, "/tmp/test.mp4")
        assert result is None

    @pytest.mark.no_gpu
    def test_nvenc_uses_pynvvideocodec_21_api(self):
        encoder = MagicMock()
        encoder.Encode.return_value = b"frame"
        encoder.EndEncode.return_value = b"flush"

        nvc = SimpleNamespace(
            __version__="2.1.0",
            CreateEncoder=MagicMock(return_value=encoder),
        )
        stream = MagicMock()
        container = MagicMock()
        container.add_stream.return_value = stream
        container_context = MagicMock()
        container_context.__enter__.return_value = container
        av = SimpleNamespace(
            open=MagicMock(return_value=container_context),
            Packet=MagicMock(side_effect=lambda value: MagicMock(data=value)),
        )

        frame = MagicMock()
        frame.shape = (3, 4, 4)
        nv12 = MagicMock()
        nv12.cpu.return_value.numpy.return_value = np.zeros((6, 4), dtype=np.uint8)

        with (
            patch.dict(sys.modules, {"PyNvVideoCodec": nvc, "av": av}),
            patch(
                "models.openai_compat.openai_compat_model._rgb_to_nv12_tensor",
                return_value=nv12,
            ),
            patch(
                "models.openai_compat.openai_compat_model.torch.cuda.is_available",
                return_value=False,
            ),
            patch(
                "models.openai_compat.openai_compat_model._pynvcodec_nvenc_available",
                return_value=True,
            ),
        ):
            result = _encode_h264_nvenc([frame], 10.0, "/tmp/test.mp4")

        assert result is True
        nvc.CreateEncoder.assert_called_once_with(
            4,
            4,
            "NV12",
            True,
            gpu_id=0,
            codec="h264",
            preset="p4",
            fps=10,
            bitrate=4_000_000,
            profile="high",
            gop=30,
        )
        encoder.Encode.assert_called_once()
        encoder.EndEncode.assert_called_once_with()
        assert container.mux.call_count == 2

    @pytest.mark.no_gpu
    def test_nvenc_probe_isolated_and_cached(self):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        cache = {}
        with (
            patch(
                "models.openai_compat.openai_compat_model._nvenc_probe_results",
                cache,
            ),
            patch(
                "models.openai_compat.openai_compat_model.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            assert _pynvcodec_nvenc_available(2) is True
            assert _pynvcodec_nvenc_available(2) is True

        run.assert_called_once()
        assert run.call_args.args[0][-1] == "2"

    @pytest.mark.no_gpu
    def test_nvenc_probe_failure_skips_session_creation(self):
        nvc = SimpleNamespace(__version__="2.1.0", CreateEncoder=MagicMock())
        av = SimpleNamespace()
        frame = MagicMock()
        frame.shape = (3, 4, 4)

        with (
            patch.dict(sys.modules, {"PyNvVideoCodec": nvc, "av": av}),
            patch(
                "models.openai_compat.openai_compat_model._pynvcodec_nvenc_available",
                return_value=False,
            ),
            patch(
                "models.openai_compat.openai_compat_model.torch.cuda.is_available",
                return_value=False,
            ),
        ):
            assert _encode_h264_nvenc([frame], 10.0, "/tmp/test.mp4") is None

        nvc.CreateEncoder.assert_not_called()

    @pytest.mark.no_gpu
    def test_pyav_encode_success(self, tmp_path):
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        output = tmp_path / "pyav.mp4"

        assert _encode_h264_pyav([frame] * 3, 3.0, str(output)) is True
        assert output.stat().st_size > 0

    @pytest.mark.no_gpu
    def test_pyav_jpeg_decode_success(self):
        from PIL import Image

        encoded = io.BytesIO()
        Image.new("RGB", (2, 2), color="black").save(encoded, format="JPEG")
        jpeg = np.frombuffer(encoded.getvalue(), dtype=np.uint8)

        frames = _decode_jpegs_pyav([jpeg])
        assert len(frames) == 1
        assert frames[0].shape == (2, 2, 3)


# ---------------------------------------------------------------------------
# _generate_sync tests  (mocked client, no real API)
# ---------------------------------------------------------------------------


@pytest.fixture
def model():
    return _make_model()


@pytest.fixture
def chunk():
    return _make_chunk()


class TestGenerateSyncReasoningDescription:
    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_reasoning_description_populated(self, _mock_b64, model, chunk):
        """VlmModelOutput.reasoning_description is filled from <think> tags."""
        model._client.chat.completions.create.return_value = _make_mock_response(
            "<think>My reasoning here.</think>\nThe answer is 42."
        )
        outputs = model._generate_sync(
            query="What is 6 * 7?",
            chunks=[chunk],
            video_frames=[MagicMock()],
            video_frames_times=[[1.0, 2.0]],
        )
        assert len(outputs) == 1
        assert isinstance(outputs[0], VlmModelOutput)
        assert outputs[0].reasoning_description == "My reasoning here."
        assert "The answer is 42." in outputs[0].output
        assert "<think>" not in outputs[0].output

    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_preserve_reasoning_tags_keeps_raw_output(self, _mock_b64, model, chunk):
        """chat/completions can request raw CR2 reasoning tags from the model adapter."""
        raw_output = "<think>My reasoning here.</think>\n<answer>The answer is 42.</answer>"
        model._client.chat.completions.create.return_value = _make_mock_response(raw_output)

        outputs = model._generate_sync(
            query="What is 6 * 7?",
            chunks=[chunk],
            video_frames=[MagicMock()],
            video_frames_times=[[1.0, 2.0]],
            generation_config=VlmGenerationConfig(preserve_reasoning_tags=True),
        )

        assert outputs[0].output == raw_output
        assert outputs[0].reasoning_description == ""

    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_no_thinking_tags_empty_reasoning(self, _mock_b64, model, chunk):
        """reasoning_description is empty when no <think> tags present."""
        model._client.chat.completions.create.return_value = _make_mock_response(
            "Plain answer with no thinking."
        )
        outputs = model._generate_sync(
            query="Describe the scene.",
            chunks=[chunk],
            video_frames=[MagicMock()],
            video_frames_times=[[1.0, 2.0]],
        )
        assert outputs[0].reasoning_description == ""
        assert outputs[0].output == "Plain answer with no thinking."

    def test_text_only_preserve_reasoning_tags_keeps_raw_output(self, model):
        """Text-only chat/completions should preserve raw reasoning tags when requested."""
        raw_output = "<think>My reasoning here.</think>\n<answer>The answer is 42.</answer>"
        model._client.chat.completions.create.return_value = _make_mock_response(raw_output)

        outputs = model._generate_text_only_sync(
            messages=[{"role": "user", "content": "What is 6 * 7?"}],
            generation_config=VlmGenerationConfig(preserve_reasoning_tags=True),
        )

        assert outputs[0].output == raw_output
        assert outputs[0].reasoning_description == ""


class TestGenerateSyncExtraBody:
    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_no_extra_body_by_default(self, _mock_b64, model, chunk):
        """extra_body=None when no special config and no REMOTE_VIDEO_INPUT."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "false"}):
            model._generate_sync(
                query="q",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[[1.0, 2.0]],
            )
        call_kwargs = model._client.chat.completions.create.call_args[1]
        assert call_kwargs.get("extra_body") == {"ignore_eos": False}

    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_min_tokens_in_extra_body(self, _mock_b64, model, chunk):
        """min_tokens appears in extra_body when set in VlmGenerationConfig."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        config = VlmGenerationConfig(min_tokens=50)
        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "false"}):
            model._generate_sync(
                query="q",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[[1.0, 2.0]],
                generation_config=config,
            )
        call_kwargs = model._client.chat.completions.create.call_args[1]
        assert call_kwargs["extra_body"]["min_tokens"] == 50

    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_ignore_eos_in_extra_body(self, _mock_b64, model, chunk):
        """ignore_eos appears in extra_body when True."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        config = VlmGenerationConfig(ignore_eos=True)
        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "false"}):
            model._generate_sync(
                query="q",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[[1.0, 2.0]],
                generation_config=config,
            )
        call_kwargs = model._client.chat.completions.create.call_args[1]
        assert call_kwargs["extra_body"]["ignore_eos"] is True

    @patch(
        "models.openai_compat.openai_compat_model.video_embeds_to_mp4_base64",
        return_value=("fakemp4base64", 5.0),
    )
    def test_remote_video_media_io_kwargs(self, _mock_mp4, model, chunk):
        """media_io_kwargs added to extra_body when REMOTE_VIDEO_INPUT=true."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "true"}):
            model._generate_sync(
                query="q",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[[1.0, 2.0, 3.0]],  # >1 frame so not single_image
            )
        call_kwargs = model._client.chat.completions.create.call_args[1]
        assert call_kwargs["extra_body"]["media_io_kwargs"] == {"video": {"num_frames": -1}}

    @patch(
        "models.openai_compat.openai_compat_model.video_embeds_to_mp4_base64",
        return_value=("fakemp4base64", 5.0),
    )
    def test_remote_video_enabled_by_default(self, _mock_mp4, model, chunk):
        """REMOTE_VIDEO_INPUT defaults to true when unset."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REMOTE_VIDEO_INPUT", None)
            model._generate_sync(
                query="q",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[[1.0, 2.0, 3.0]],
            )

        call_kwargs = model._client.chat.completions.create.call_args[1]
        content = call_kwargs["messages"][-1]["content"]
        video_item = next(item for item in content if item["type"] == "video_url")
        assert video_item["video_url"]["url"] == "data:video/mp4;base64,fakemp4base64"

    @patch(
        "models.openai_compat.openai_compat_model.video_embeds_to_mp4_base64",
        return_value=(None, None),
    )
    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_remote_video_encode_failure_uses_ten_images(
        self, _mock_jpeg, _mock_mp4, model, chunk
    ):
        """All MP4 encoders failing falls back to at most ten JPEG images."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "true"}):
            model._generate_sync(
                query="q",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[list(range(60))],
            )

        content = model._client.chat.completions.create.call_args[1]["messages"][-1][
            "content"
        ]
        images = [item for item in content if item["type"] == "image_url"]
        assert len(images) == 10
        assert all(
            item["image_url"]["url"].startswith("data:image/jpeg;base64,")
            for item in images
        )

    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_single_image_skips_mp4(self, _mock_b64, model, chunk):
        """Single-frame chunk always uses image_url even if REMOTE_VIDEO_INPUT=true."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "true"}):
            with patch(
                "models.openai_compat.openai_compat_model.video_embeds_to_mp4_base64"
            ) as mp4_fn:
                model._generate_sync(
                    query="q",
                    chunks=[chunk],
                    video_frames=[MagicMock()],
                    video_frames_times=[[1.0]],  # single frame
                )
        mp4_fn.assert_not_called()


class TestGenerateSyncTimestampInstruction:
    @patch(
        "models.openai_compat.openai_compat_model.video_embeds_to_mp4_base64",
        return_value=("fakemp4base64", 5.0),
    )
    def test_remote_video_timestamp_instruction_in_prompt(self, _mock_mp4, model):
        """PROMPT contains explicit timestamp bounds when REMOTE_VIDEO_INPUT=true."""
        chunk = _make_chunk(filename="test.mp4", start_pts=10_000_000_000, end_pts=20_000_000_000)
        model._client.chat.completions.create.return_value = _make_mock_response("ok")

        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "true"}):
            model._generate_sync(
                query="Describe the scene.",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[[10.0, 15.0, 20.0]],
            )

        call_args = model._client.chat.completions.create.call_args[1]
        messages = call_args["messages"]
        user_content = messages[-1]["content"]
        prompt_text = next(item["text"] for item in user_content if item["type"] == "text")
        assert "IMPORTANT" in prompt_text
        assert "Do NOT use timestamps starting from 0" in prompt_text

    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_default_timestamp_instruction(self, _mock_b64, model):
        """PROMPT contains default timestamp instruction when not remote video."""
        chunk = _make_chunk()
        model._client.chat.completions.create.return_value = _make_mock_response("ok")

        with patch.dict(os.environ, {"REMOTE_VIDEO_INPUT": "false"}):
            model._generate_sync(
                query="Describe the scene.",
                chunks=[chunk],
                video_frames=[MagicMock()],
                video_frames_times=[[1.0, 2.0]],
            )

        call_args = model._client.chat.completions.create.call_args[1]
        messages = call_args["messages"]
        user_content = messages[-1]["content"]
        prompt_text = next(item["text"] for item in user_content if item["type"] == "text")
        assert "Make sure the answer contains correct timestamps." in prompt_text


class TestGenerateSyncTokenCounting:
    @patch(
        "models.openai_compat.openai_compat_model.tensor_to_base64_jpeg",
        side_effect=_fake_base64_jpeg,
    )
    def test_token_counts_populated(self, _mock_b64, model, chunk):
        """input_tokens and output_tokens extracted from API response."""
        model._client.chat.completions.create.return_value = _make_mock_response("ok")
        outputs = model._generate_sync(
            query="q",
            chunks=[chunk],
            video_frames=[MagicMock()],
            video_frames_times=[[1.0, 2.0]],
        )
        assert outputs[0].input_tokens == 10
        assert outputs[0].output_tokens == 20
