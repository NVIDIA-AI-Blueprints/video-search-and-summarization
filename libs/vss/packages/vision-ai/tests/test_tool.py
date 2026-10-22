# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Vision AI adapter over public VSS APIs."""

from __future__ import annotations

import asyncio
import importlib
from importlib.metadata import entry_points

import pytest

from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss_vision_ai import TOOL, VssInput, VssSettings

tool_module = importlib.import_module("vss_vision_ai.tool")


class FakeJob:
    def __init__(self, result=None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.closed = False

    def __await__(self):
        async def run():
            if self.error is not None:
                raise self.error
            return self.result

        return run().__await__()

    async def wait_closed(self) -> None:
        self.closed = True


def test_tool_declares_vision_ai_contract() -> None:
    assert TOOL.name == "vss"
    assert TOOL.Settings is VssSettings
    assert TOOL.Input is VssInput


def test_tool_is_registered_for_vision_ai_discovery() -> None:
    matches = [
        entry_point
        for entry_point in entry_points(group="vision_ai.tools")
        if entry_point.name == "vss"
    ]

    assert len(matches) == 1
    assert matches[0].load() is TOOL


@pytest.mark.asyncio
async def test_adapter_preserves_structured_backend_results(monkeypatch) -> None:
    caption = VlmChunkResponse(
        chunk=ChunkInfo(sourceId="source-1", chunkIdx=2),
        vlm_response='{"events":[{"type":"vehicle"}]}',
    )
    captured = {}

    def fake_ingest(source, **kwargs):
        captured["source"] = source
        captured["kwargs"] = kwargs
        return FakeJob({"vlm": [caption]})

    monkeypatch.setattr(tool_module, "ingest", fake_ingest)

    result = await TOOL.execute(
        VssInput(
            source="/video.mp4",
            camera_id="dock-1",
            backends="vlm",
            decoder="opencv",
            correlation_id="request-7",
        ),
        VssSettings(),
    )

    assert result.backend_results["vlm"][0] == caption
    assert (
        result.model_dump(mode="json")["backend_results"]["vlm"][0]["vlm_response"]
        == caption.vlm_response
    )
    assert result.requested_frame_transport == "deployment_default"
    assert result.correlation_id == "request-7"
    assert captured["source"].url == "/video.mp4"
    assert captured["source"].camera_id == "dock-1"
    assert captured["kwargs"]["backends"] == ["vlm"]


@pytest.mark.asyncio
async def test_summarization_requires_caption_results(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_module, "ingest", lambda source, **kwargs: FakeJob({"rtvi_embed": []})
    )

    with pytest.raises(ValueError, match="requires at least one backend"):
        await TOOL.execute(
            VssInput(source="/video.mp4", backends="rtvi_embed", summarize=True),
            VssSettings(),
        )


@pytest.mark.asyncio
async def test_cancellation_waits_for_ingestion_cleanup(monkeypatch) -> None:
    job = FakeJob(error=asyncio.CancelledError())
    monkeypatch.setattr(tool_module, "ingest", lambda source, **kwargs: job)

    with pytest.raises(asyncio.CancelledError):
        await TOOL.execute(VssInput(source="/video.mp4"), VssSettings())

    assert job.closed


def test_backend_names_reject_empty_and_duplicates() -> None:
    with pytest.raises(ValueError, match="at least one"):
        tool_module._backend_names(" , ")
    with pytest.raises(ValueError, match="duplicate"):
        tool_module._backend_names("vlm,vlm")
