# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Source identity, supported locators, and dependency direction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest


@pytest.mark.parametrize("kind", ["path", "string", "file_uri"])
async def test_supported_local_locators(
    processing: Any,
    register: Any,
    video_path: Path,
    kind: str,
) -> None:
    backend = register("opencv")
    source = {
        "path": video_path,
        "string": str(video_path),
        "file_uri": video_path.as_uri(),
    }[kind]
    facts = await processing.probe_video(source, source_id="stable-video")
    assert facts.source_id == "stable-video"
    assert backend.open_paths == [video_path.resolve()]
    assert backend.sessions[0].closed


async def test_relative_path_with_colon_remains_a_local_path(
    processing: Any, register: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = register("opencv")
    monkeypatch.chdir(tmp_path)
    path = Path("review:local.mp4")
    path.write_bytes(b"video")
    facts = await processing.probe_video(path, source_id="colon-path")
    assert facts.source_id == "colon-path"
    assert backend.open_paths == [path.resolve()]


async def test_source_protocol_preserves_identity_without_mutation(
    processing: Any,
    register: Any,
    video_path: Path,
) -> None:
    register("opencv")
    source = SimpleNamespace(
        url=str(video_path), source_id="stable-video", camera_id="cam-1"
    )
    original = vars(source).copy()
    facts = await processing.probe_video(source)
    assert facts.source_id == "stable-video"
    assert vars(source) == original


async def test_actual_ingestion_request_source_is_accepted(
    processing: Any,
    register: Any,
    video_path: Path,
) -> None:
    from vss.ingestion import RequestSource

    register("opencv")
    source = RequestSource.for_file(str(video_path))
    assert (await processing.probe_video(source)).source_id == source.source_id


async def test_path_identity_is_generated_per_operation(
    processing: Any,
    register: Any,
    video_path: Path,
) -> None:
    register("opencv")
    first = await processing.probe_video(video_path)
    second = await processing.probe_video(video_path)
    assert str(UUID(first.source_id)) == first.source_id
    assert first.source_id != second.source_id


@pytest.mark.parametrize(
    "source",
    [
        "https://example.test/video.mp4",
        "s3://bucket/video.mp4",
        "rtsp://example.test/live",
        "file://remote-host/video.mp4",
        SimpleNamespace(url=None, source_id="rtvi-asset-id"),
    ],
)
async def test_remote_and_asset_only_sources_are_explicitly_unsupported(
    processing: Any,
    source: object,
) -> None:
    with pytest.raises(processing.UnsupportedSourceError):
        await processing.probe_video(source)


async def test_source_error_redacts_credentials(processing: Any, caplog: Any) -> None:
    source = "https://user:do-not-log@host.test/video.mp4?token=secret-token"
    with pytest.raises(processing.UnsupportedSourceError) as raised:
        await processing.probe_video(source)
    diagnostic = str(raised.value) + caplog.text
    assert "do-not-log" not in diagnostic
    assert "secret-token" not in diagnostic


async def test_missing_file_fails_before_decoder_open(
    processing: Any,
    register: Any,
    tmp_path: Path,
) -> None:
    backend = register("opencv")
    with pytest.raises((FileNotFoundError, processing.ProcessingError)):
        await processing.probe_video(tmp_path / "absent.mp4")
    assert backend.open_paths == []


def test_processing_import_is_independent_of_native_and_upper_layers() -> None:
    script = """
import importlib.abc
import sys
class RejectNativeAndUpperLayers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        optional = {'cv2', 'PyNvVideoCodec', 'torch', 'cupy', 'gi', 'httpx', 'openai'}
        if fullname.split('.')[0] in optional:
            raise AssertionError('Eager optional import: ' + fullname)
        if fullname.startswith(("vss.ingestion", "vss.summarization")):
            raise AssertionError('Upward dependency: ' + fullname)
sys.meta_path.insert(0, RejectNativeAndUpperLayers())
import vss.video_processing
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
