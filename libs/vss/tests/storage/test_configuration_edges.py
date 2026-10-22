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

"""Additive persistence must preserve backend and introspection contracts."""

import asyncio
from pathlib import Path
from typing import get_type_hints

import pytest

from vss.ingestion import RequestSource, ingest


def test_public_ingestion_annotations_resolve():
    from vss.storage import StorageAdapter

    assert StorageAdapter in get_type_hints(ingest)["storage"].__args__


def test_explicit_file_environment_without_root_selects_home(monkeypatch):
    from vss.ingestion.persistence import environment_storage_config

    monkeypatch.setenv("VSS_STORAGE_BACKEND", "file")
    monkeypatch.delenv("VSS_STORAGE_ROOT", raising=False)
    config = environment_storage_config()
    assert config is not None and config.backend == "file"
    assert config.root == Path.home() / ".vss"


async def test_persistent_whole_backend_preserves_zero_argument_factory(
    tmp_path, monkeypatch
):
    from vss.ingestion import RTVIBackend
    from vss.ingestion.backend import _BACKEND_REGISTRY
    from vss.ingestion.contracts import VlmChunkResponse
    from vss.storage import FileStorageAdapter
    from vss.summarization import AdapterChunkStore

    class Custom(RTVIBackend):
        def __init__(self):
            self.closed = False

        async def ingest(self, source, **kwargs):
            return [VlmChunkResponse(vlm_response="Custom backend caption")]

        async def aclose(self):
            self.closed = True

    monkeypatch.setitem(_BACKEND_REGISTRY, "custom_whole", Custom)
    async with FileStorageAdapter(root=tmp_path) as adapter:
        job = ingest(
            RequestSource(url="video.mp4"),
            backends=["custom_whole"],
            storage=adapter,
        )
        responses = (await job)["custom_whole"]
        await job.wait_closed()
        assert responses[0].vlm_response == "Custom backend caption"
        assert await AdapterChunkStore(adapter).get_chunks(str(job.id)) == responses


async def test_existing_local_colon_filename_is_checkpoint_source(
    tmp_path, monkeypatch
):
    from vss.ingestion._persistent import source_identity

    path = tmp_path / "camera:morning.mp4"
    path.write_bytes(b"local media")
    monkeypatch.chdir(tmp_path)
    digest, stat = await source_identity(RequestSource(url=path.name))
    assert len(digest) == 64 and stat[2] == len(b"local media")


@pytest.mark.parametrize("path", ["https://camera/video", "s3://bucket/key"])
async def test_checkpoint_sources_do_not_fetch_remote_urls(path):
    from vss.ingestion._persistent import source_identity
    from vss.storage import StorageConfigurationError

    with pytest.raises(StorageConfigurationError):
        await source_identity(RequestSource(url=path))


async def test_builtin_rtvi_freezes_absent_credentials_and_discovery_model(
    tmp_path, monkeypatch
):
    from vss.ingestion import IngestionQueue, RTVIBackend
    from vss.ingestion.contracts import VlmChunkResponse
    from vss.storage import FileStorageAdapter

    captured = []

    async def finish(backend, source, **kwargs):
        captured.append((backend._transport._headers, backend._transport._model))
        return [VlmChunkResponse(vlm_response="completed")]

    monkeypatch.setattr(RTVIBackend, "ingest", finish)
    monkeypatch.delenv("RTVI_API_KEY", raising=False)
    monkeypatch.delenv("RTVI_MODEL", raising=False)
    queue = IngestionQueue(max_concurrency=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocker():
        entered.set()
        await release.wait()

    head = queue.submit(blocker)
    await entered.wait()
    async with FileStorageAdapter(root=tmp_path) as storage:
        job = ingest(
            RequestSource(url="video.mp4"),
            backends=["rtvi"],
            storage=storage,
            queue=queue,
        )
        monkeypatch.setenv("RTVI_API_KEY", "late-secret")
        monkeypatch.setenv("RTVI_MODEL", "late-model")
        release.set()
        await head
        await job
        await job.wait_closed()
    assert captured == [({}, None)]


@pytest.mark.parametrize("backend", ["rt_embed", "rtvi_embed", "rtvi-cv"])
async def test_persistent_ingestion_rejects_non_caption_backends(
    tmp_path, monkeypatch, backend
):
    from vss.ingestion import RTEmbedBackend, RTVICvBackend
    from vss.storage import FileStorageAdapter, StorageConfigurationError

    called = False

    async def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(RTEmbedBackend, "ingest", fail_if_called)
    monkeypatch.setattr(RTVICvBackend, "ingest", fail_if_called)
    async with FileStorageAdapter(root=tmp_path) as storage:
        with pytest.raises(
            StorageConfigurationError,
            match="supports caption backends only",
        ):
            ingest(
                RequestSource(url="video.mp4"),
                backends=[backend],
                storage=storage,
            )

    assert not called
