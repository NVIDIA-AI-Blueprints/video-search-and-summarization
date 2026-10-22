# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Trusted immutable identities are opt-in checkpoint inputs."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from vss.ingestion import RequestSource, ingest, ingest_sync


def _identity(**overrides):
    from vss.ingestion import ImmutableSourceIdentity

    values = {
        "sha256": "a" * 64,
        "size_bytes": 12,
        "provider_namespace": "media-registry",
        "revision": "asset-v3",
    }
    values.update(overrides)
    return ImmutableSourceIdentity(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "A" * 64),
        ("sha256", "a" * 63),
        ("sha256", "g" * 64),
        ("size_bytes", -1),
        ("size_bytes", True),
        ("provider_namespace", "  "),
        ("revision", "\t"),
        ("provider_namespace", "n" * 257),
        ("revision", "r" * 257),
    ],
)
def test_immutable_identity_rejects_untrusted_or_unbounded_fields(field, value):
    with pytest.raises(ValidationError):
        _identity(**{field: value})


def test_immutable_identity_is_frozen_and_publicly_exported():
    import vss.ingestion as ingestion
    from vss.ingestion import ImmutableSourceIdentity, SourceIdentityProvider

    identity = _identity()
    assert identity.schema_version == "1.0"
    assert ImmutableSourceIdentity is ingestion.ImmutableSourceIdentity
    assert "ImmutableSourceIdentity" in ingestion.__all__
    assert "SourceIdentityProvider" in ingestion.__all__
    with pytest.raises(ValidationError):
        identity.revision = "changed"
    assert SourceIdentityProvider is not None


async def test_provider_resolution_does_not_open_or_hash_source(tmp_path, monkeypatch):
    from vss.ingestion._persistent import resolve_source_identity

    path = tmp_path / "source.mp4"
    path.write_bytes(b"trusted bytes")
    content = path.read_bytes()
    expected = hashlib.sha256(content).hexdigest()

    class Provider:
        async def resolve(self, source):
            assert source.url == str(path)
            return _identity(sha256=expected, size_bytes=len(content))

    def unexpected_open(*args, **kwargs):
        raise AssertionError("trusted identity path must not read source bytes")

    monkeypatch.setattr(Path, "open", unexpected_open)
    identity, stat = await resolve_source_identity(
        RequestSource(url=str(path)), Provider()
    )
    assert identity.sha256 == expected
    assert identity.size_bytes == stat[2] == len(content)


async def test_provider_receives_detached_source_and_size_must_match(tmp_path):
    from vss.ingestion._persistent import resolve_source_identity
    from vss.storage import StorageConflictError

    path = tmp_path / "source.mp4"
    path.write_bytes(b"bound bytes")
    source = RequestSource(url=str(path), source_id="caller-source")

    class MutatingProvider:
        async def resolve(self, received):
            assert received is not source
            received.url = "https://example.invalid/other"
            received.source_id = "mutated"
            return _identity(
                sha256=hashlib.sha256(b"bound bytes").hexdigest(),
                size_bytes=len(b"bound bytes"),
            )

    await resolve_source_identity(source, MutatingProvider())
    assert source.url == str(path)
    assert source.source_id == "caller-source"

    class WrongSizeProvider:
        async def resolve(self, received):
            return _identity(size_bytes=1)

    with pytest.raises(StorageConflictError, match="size"):
        await resolve_source_identity(source, WrongSizeProvider())


def test_provider_option_rejects_nonpersistent_and_non_async_callbacks(tmp_path):
    from vss.storage import StorageConfig

    class Provider:
        def resolve(self, source):
            return _identity()

    with pytest.raises(ValueError, match="persistent"):
        ingest(
            RequestSource(url=str(tmp_path / "source.mp4")),
            source_identity_provider=Provider(),
        )

    with pytest.raises(ValueError, match="async"):
        ingest(
            RequestSource(url=str(tmp_path / "source.mp4")),
            backends=["vlm"],
            model="m",
            decoder="opencv",
            pipeline_revision="v1",
            storage_config=StorageConfig(backend="file", root=tmp_path / "store"),
            source_identity_provider=Provider(),
        )

    with pytest.raises(ValueError, match="persistent"):
        ingest_sync(
            RequestSource(url=str(tmp_path / "source.mp4")),
            source_identity_provider=Provider(),
        )


def test_ingest_sync_forwards_provider(monkeypatch):
    import vss.ingestion.service as service

    provider = object()
    captured = {}

    async def fake_ingest(source, *, backends=None, **kwargs):
        captured["source"] = source
        captured["backends"] = backends
        captured.update(kwargs)
        return []

    monkeypatch.setattr(service, "ingest", fake_ingest)
    source = RequestSource(url="local.mp4")
    assert (
        service.ingest_sync(source, backends=["vlm"], source_identity_provider=provider)
        == []
    )
    assert captured["source"] is source
    assert captured["backends"] == ["vlm"]
    assert captured["source_identity_provider"] is provider


def test_default_source_identity_and_fingerprint_shape_remain_unchanged(
    tmp_path, monkeypatch
):
    from vss.ingestion._persistent import _fingerprint, capture_spec, source_identity

    path = tmp_path / "source.mp4"
    content = b"old default hash path"
    path.write_bytes(content)
    digest, stat = asyncio.run(source_identity(RequestSource(url=str(path))))
    assert digest == hashlib.sha256(content).hexdigest()
    assert stat[2] == len(content)

    spec = capture_spec(
        "vlm",
        prompt="p",
        model="m",
        decoder="opencv",
        chunk_duration=1,
        chunk_overlap_duration=0,
        generation_config=None,
    )
    source = RequestSource(url=str(path), source_id="source")
    assert (
        _fingerprint(spec, source, digest, "pipeline-v1", None)
        == hashlib.sha256(
            __import__("json")
            .dumps(
                {
                    **spec.identity(),
                    "source_id": source.source_id,
                    "camera_id": source.camera_id,
                    "source_digest": digest,
                    "source_revision": None,
                    "pipeline_revision": "pipeline-v1",
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            .encode()
        ).hexdigest()
    )


@pytest.fixture
def checkpoint_pipeline(tmp_path, monkeypatch):
    # Reuse the public checkpoint harness while keeping the identity tests local.
    from test_checkpoint_pipeline import pipeline as checkpoint_fixture

    return checkpoint_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("change", ["revision", "namespace", "source_id", "content"])
async def test_provider_identity_change_refuses_resume_before_inference(
    tmp_path, checkpoint_pipeline, change
):
    from test_checkpoint_pipeline import submit

    from vss.storage import FileStorageAdapter, StorageConflictError

    source, requests, _ = checkpoint_pipeline
    state = {"namespace": "assets", "revision": "rev-1"}

    class Provider:
        async def resolve(self, received):
            assert received is not source
            content = Path(received.url).read_bytes()
            return _identity(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                provider_namespace=state["namespace"],
                revision=state["revision"],
            )

    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        first = submit(source, storage, source_identity_provider=Provider())
        await first
        await first.wait_closed()
        count = len(requests)
        resumed_source = source.model_copy(deep=True)
        if change == "revision":
            state["revision"] = "rev-2"
        elif change == "namespace":
            state["namespace"] = "other-assets"
        elif change == "source_id":
            resumed_source.source_id = "different-source"
        elif change == "content":
            Path(source.url).write_bytes(b"updated immutable bytes")
        with pytest.raises(StorageConflictError):
            await submit(
                resumed_source,
                storage,
                job_id=first.id,
                resume=True,
                source_identity_provider=Provider(),
            )
        assert len(requests) == count


async def test_provider_errors_are_sanitized_and_cancellation_propagates(tmp_path):
    from vss.ingestion._persistent import resolve_source_identity
    from vss.storage import StorageConfigurationError

    path = tmp_path / "source.mp4"
    path.write_bytes(b"content")

    class BrokenProvider:
        async def resolve(self, source):
            raise RuntimeError("secret credential payload")

    with pytest.raises(StorageConfigurationError) as error:
        await resolve_source_identity(RequestSource(url=str(path)), BrokenProvider())
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None

    entered, release = asyncio.Event(), asyncio.Event()

    class WaitingProvider:
        async def resolve(self, source):
            entered.set()
            await release.wait()
            return _identity(size_bytes=7)

    task = asyncio.create_task(
        resolve_source_identity(RequestSource(url=str(path)), WaitingProvider())
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cached_provider_resume_skips_hash_and_preserves_stored_identity(
    tmp_path, monkeypatch, checkpoint_pipeline
):
    from test_checkpoint_pipeline import submit

    from vss.ingestion import ImmutableSourceIdentity, _persistent
    from vss.storage import FileStorageAdapter
    from vss.summarization import AdapterChunkStore

    source, requests, _ = checkpoint_pipeline
    content = Path(source.url).read_bytes()
    cached = ImmutableSourceIdentity(
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        provider_namespace="cached-library",
        revision="object-v9",
    )

    class CachedProvider:
        async def resolve(self, received):
            assert received.source_id == "retained-source"
            return cached

    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        first = submit(source, storage, source_identity_provider=CachedProvider())
        captions = (await first)["vlm"]
        await first.wait_closed()
        count = len(requests)

        def unexpected_hash(*args, **kwargs):
            raise AssertionError("trusted resume must not hash the complete file")

        monkeypatch.setattr(_persistent, "_hash_source", unexpected_hash)
        replay = (
            await submit(
                source,
                storage,
                source_identity_provider=CachedProvider(),
                job_id=first.id,
                resume=True,
            )
        )["vlm"]
        assert replay == captions
        assert len(requests) == count
        stored = await AdapterChunkStore(storage).get_chunks(str(first.id))
        assert stored == captions
        assert all(item.chunk.sourceId == "retained-source" for item in replay)
        snapshot = await storage.read("checkpoints", str(first.id))
        assert snapshot.records[0]["source_revision_digest"] == cached.sha256


async def test_identity_model_instance_is_revalidated(tmp_path):
    from vss.ingestion import ImmutableSourceIdentity
    from vss.ingestion._persistent import resolve_source_identity
    from vss.storage import StorageConfigurationError

    path = tmp_path / "source.mp4"
    path.write_bytes(b"123")
    malformed = ImmutableSourceIdentity.model_construct(
        sha256="bad", size_bytes=True, provider_namespace=" ", revision="r"
    )

    class MalformedModelProvider:
        async def resolve(self, source):
            return malformed

    with pytest.raises(StorageConfigurationError):
        await resolve_source_identity(
            RequestSource(url=str(path)), MalformedModelProvider()
        )


async def test_source_stat_drift_and_directory_are_typed_failures(tmp_path):
    from vss.ingestion._persistent import resolve_source_identity
    from vss.storage import StorageConfigurationError, StorageConflictError

    path = tmp_path / "source.mp4"
    path.write_bytes(b"before")
    before = path.stat()

    class MutatingProvider:
        async def resolve(self, source):
            path.write_bytes(b"after!")
            # Same-size rapid writes may retain mtime on some filesystems. Make
            # metadata drift explicit; this test checks stat, not provider truth.
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))
            assert path.stat().st_mtime_ns != before.st_mtime_ns
            return _identity(sha256=hashlib.sha256(b"before").hexdigest(), size_bytes=6)

    with pytest.raises(StorageConflictError, match="changed"):
        await resolve_source_identity(RequestSource(url=str(path)), MutatingProvider())

    with pytest.raises(StorageConfigurationError):
        await resolve_source_identity(
            RequestSource(url=str(tmp_path)),
            type("Provider", (), {"resolve": staticmethod(_async_identity)})(),
        )


async def _async_identity(source):
    return _identity()


async def test_cancelled_queued_provider_waits_for_callback_cleanup(
    tmp_path, checkpoint_pipeline
):
    from test_checkpoint_pipeline import submit

    from vss.ingestion import IngestionQueue
    from vss.storage import FileStorageAdapter

    source, _, _ = checkpoint_pipeline
    entered, cleaned = asyncio.Event(), asyncio.Event()

    class WaitingProvider:
        async def resolve(self, received):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    queue = IngestionQueue(max_concurrency=1)
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(
            source,
            storage,
            queue=queue,
            source_identity_provider=WaitingProvider(),
        )
        await entered.wait()
        assert job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        await job.wait_closed()
    assert cleaned.is_set()


async def test_provider_source_is_frozen_at_queue_submission(
    tmp_path, checkpoint_pipeline
):
    from test_checkpoint_pipeline import submit

    from vss.ingestion import IngestionQueue
    from vss.storage import FileStorageAdapter

    source, _, _ = checkpoint_pipeline
    original_url, original_camera_id, original_id = (
        source.url,
        source.camera_id,
        source.source_id,
    )
    content = Path(original_url).read_bytes()
    observed = []
    blocker_entered, release = asyncio.Event(), asyncio.Event()

    async def blocker():
        blocker_entered.set()
        await release.wait()

    class Provider:
        async def resolve(self, received):
            observed.append((received.url, received.camera_id, received.source_id))
            return _identity(
                sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)
            )

    queue = IngestionQueue(max_concurrency=1)
    blocker_job = queue.submit(blocker)
    await blocker_entered.wait()
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(
            source,
            storage,
            queue=queue,
            source_identity_provider=Provider(),
        )
        source.url = str(tmp_path / "changed.mp4")
        source.camera_id = "mutated-after-submit"
        release.set()
        await blocker_job
        await job
        await job.wait_closed()
    assert observed == [(original_url, original_camera_id, original_id)]
