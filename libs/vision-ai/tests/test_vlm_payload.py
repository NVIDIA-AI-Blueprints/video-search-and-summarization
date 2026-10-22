# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Per-backend request bodies and request validation."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
import pytest

from nvidia_vision_ai.vlm import Endpoint
from nvidia_vision_ai.vlm import VlmRequest
from nvidia_vision_ai.vlm.payload import build_payload

URL = "https://example.com/clip.mp4"


def req(**overrides: Any) -> VlmRequest:
    values: dict[str, Any] = {
        "endpoint": Endpoint(base_url="http://vlm:8000"),
        "model": "m",
        "prompt": "what happens?",
        "media_url": URL,
    }
    values.update(overrides)
    return VlmRequest(**values)


def test_base_fields_and_message_shape() -> None:
    body = build_payload(req(temperature=0.2, max_tokens=64, seed=7), URL)
    assert body["model"] == "m"
    assert body["messages"][0]["content"] == [
        {"type": "video_url", "video_url": {"url": URL}},
        {"type": "text", "text": "what happens?"},
    ]
    assert (body["temperature"], body["max_tokens"], body["seed"]) == (0.2, 64, 7)
    assert "media_io_kwargs" not in body


@pytest.mark.parametrize("backend", ["rt_vlm", "cosmos_reason_nim"])
def test_rt_vlm_sends_fps_or_frames_never_both(backend: str, caplog: pytest.LogCaptureFixture) -> None:
    assert build_payload(req(backend=backend, fps=2), URL)["media_io_kwargs"] == {"video": {"fps": 2}}
    assert build_payload(req(backend=backend, max_frames=16), URL)["media_io_kwargs"] == {"video": {"num_frames": 16}}
    both = build_payload(req(backend=backend, fps=2, max_frames=16), URL)
    assert both["media_io_kwargs"] == {"video": {"fps": 2}}
    assert "max_frames 16 not sent" in caplog.text


def test_rt_vlm_engine_fields() -> None:
    body = build_payload(req(enable_reasoning=True, chunk_duration=10, total_pixels=1_000_000), URL)
    assert body["enable_reasoning"] is True
    assert body["chunk_duration"] == 10
    assert body["mm_processor_kwargs"] == {"size": {"shortest_edge": 128 * 32 * 32, "longest_edge": 1_000_000}}


def test_total_pixels_below_floor_clamps_shortest_edge() -> None:
    body = build_payload(req(total_pixels=1000), URL)
    assert body["mm_processor_kwargs"]["size"] == {"shortest_edge": 1000, "longest_edge": 1000}


def test_vllm_qwen3_loader_cap() -> None:
    fps_only = build_payload(req(backend="vllm", fps=1.5), URL)
    assert fps_only["media_io_kwargs"] == {"video": {"fps": 1.5, "num_frames": -1}}
    assert fps_only["mm_processor_kwargs"] == {"do_sample_frames": False}
    both = build_payload(req(backend="vllm", fps=1.5, max_frames=8), URL)
    assert both["media_io_kwargs"] == {"video": {"fps": 1.5, "num_frames": 8, "max_frames": 8}}


def test_vllm_reasoning_uses_chat_template_kwargs() -> None:
    body = build_payload(req(backend="vllm", enable_reasoning=False), URL)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "enable_reasoning" not in body


def test_openai_backend_is_plain(caplog: pytest.LogCaptureFixture) -> None:
    body = build_payload(req(backend="openai", fps=2, enable_reasoning=True, temperature=0.1), URL)
    assert set(body) == {"model", "messages", "temperature"}
    assert "fps, enable_reasoning not sent" in caplog.text


@pytest.mark.parametrize("backend", ["vllm", "openai"])
def test_positive_chunk_duration_rejected_off_rt_vlm(backend: str) -> None:
    with pytest.raises(ValidationError, match="chunk_duration"):
        req(backend=backend, chunk_duration=5)
    assert req(backend=backend, chunk_duration=0).chunk_duration == 0


def test_exactly_one_media_source(tmp_path: Any) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        req(media_url=None)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    with pytest.raises(ValidationError, match="exactly one"):
        req(file=clip)
    with pytest.raises(ValidationError, match="does not exist"):
        req(media_url=None, file=tmp_path / "missing.mp4")


def test_bounds_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        req(temperature=1.5)
    with pytest.raises(ValidationError):
        req(fps=0)
    with pytest.raises(ValidationError):
        req(sensor="cam-1")
