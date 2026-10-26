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

import asyncio
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

import vss.ingestion as ingestion
import vss.ingestion.service as ingestion_service
from vss.ingestion import (
    ChunkInfo,
    CPUVLMBackend,
    EmbeddingChunkResponse,
    GPUVLMBackend,
    IngestionBackend,
    IngestionJob,
    IngestionJobState,
    RequestSource,
    RTEmbedBackend,
    RTVIBackend,
    TextEmbeddingResponse,
    VlmCaptionsCompletionResponse,
    VlmChunkResponse,
    VLMIngestionBackend,
    available_backends,
    get_backend,
    ingest,
    ingest_sync,
)


def test_unknown_public_name_cannot_control_lazy_import(monkeypatch) -> None:
    imported: list[tuple[str, str | None]] = []

    def record_import(name: str, package: str | None = None) -> None:
        imported.append((name, package))

    monkeypatch.setattr(ingestion, "import_module", record_import)

    unknown_name = "attacker_chosen_module"
    with pytest.raises(
        AttributeError,
        match="module 'vss.ingestion' has no attribute 'attacker_chosen_module'",
    ):
        getattr(ingestion, unknown_name)

    assert imported == []


def test_request_source_preserves_existing_shape() -> None:
    source = RequestSource.for_file(
        "https://example.test/video.mp4",
        camera_id="camera-001",
    )

    assert source.url == "https://example.test/video.mp4"
    assert source.camera_id == "camera-001"
    assert str(uuid.UUID(source.source_id)) == source.source_id
    assert source.schema_version == "1.0"
    assert RequestSource.model_validate_json(source.model_dump_json()) == source


def test_chunk_info_preserves_existing_json_field_names() -> None:
    chunk = ChunkInfo(
        sourceId="source-001",
        chunkIdx=2,
        file="/videos/example.mp4",
        start_pts=10_000_000_000,
        end_pts=20_000_000_000,
    )

    restored = ChunkInfo.model_validate_json(chunk.model_dump_json())

    assert restored == chunk
    assert restored.model_dump()["sourceId"] == "source-001"
    assert restored.model_dump()["chunkIdx"] == 2


def test_vlm_chunk_response_preserves_existing_fields() -> None:
    response = VlmChunkResponse()
    response.chunk = ChunkInfo(chunkIdx=3)
    response.vlm_response = "A person enters the room."
    response.vlm_stats = {"input_tokens": 10, "output_tokens": 6}

    assert response.chunk.chunkIdx == 3
    assert response.vlm_response == "A person enters the room."
    assert response.vlm_stats["output_tokens"] == 6
    assert "chunk[3]" in str(response)
    assert response.schema_version == "1.0"
    assert VlmChunkResponse.model_validate_json(response.model_dump_json()) == response


def test_embedding_chunk_response_is_versioned_and_validated() -> None:
    response = EmbeddingChunkResponse(
        chunk=ChunkInfo(sourceId="source-1", chunkIdx=2),
        embeddings=[0.1, 0.2],
    )

    assert response.schema_version == "1.0"
    assert (
        EmbeddingChunkResponse.model_validate_json(response.model_dump_json())
        == response
    )
    with pytest.raises(ValidationError):
        EmbeddingChunkResponse(chunk=ChunkInfo(), embeddings=[])


def test_embedding_chunk_response_allows_error_without_vector() -> None:
    response = EmbeddingChunkResponse(
        chunk=ChunkInfo(sourceId="source-1", chunkIdx=2),
        error="chunk decoding failed",
    )

    assert response.embeddings == []
    assert response.error == "chunk decoding failed"


def test_text_embedding_response_matches_rt_embed_leaf_contract() -> None:
    response = TextEmbeddingResponse(
        text_input="A person enters.",
        embeddings=[0.1, 0.2],
    )

    assert (
        TextEmbeddingResponse.model_validate_json(response.model_dump_json())
        == response
    )


@pytest.mark.parametrize(
    ("contract_type", "other_fields"),
    [
        (EmbeddingChunkResponse, {"chunk": ChunkInfo(sourceId="source-1")}),
        (TextEmbeddingResponse, {"text_input": "A person enters."}),
    ],
)
@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), -float("inf")])
def test_embedding_contracts_reject_non_finite_vectors_and_round_trip(
    contract_type: type[EmbeddingChunkResponse] | type[TextEmbeddingResponse],
    other_fields: dict[str, object],
    invalid_value: float,
) -> None:
    with pytest.raises(ValidationError):
        contract_type(embeddings=[invalid_value], **other_fields)

    response = contract_type(embeddings=[0.1, -0.2], **other_fields)
    payload = response.model_dump_json()
    assert '"embeddings":[0.1,-0.2]' in payload
    assert contract_type.model_validate_json(payload) == response


def test_ingestion_contracts_validate_construction_and_assignment() -> None:
    with pytest.raises(ValidationError):
        ChunkInfo.model_validate({"unsupported": True})

    response = VlmChunkResponse()
    with pytest.raises(ValidationError):
        response.processing_latency = -1


def test_ingestion_plan_is_internal_only() -> None:
    assert not hasattr(ingestion, "IngestionPlan")
    assert not hasattr(ingestion, "Observation")
    assert not hasattr(ingestion, "IngestionResult")
    assert not hasattr(ingestion, "JobStatus")


async def test_rtvi_backend_is_registered_behind_abc() -> None:
    assert issubclass(CPUVLMBackend, VLMIngestionBackend)
    assert issubclass(GPUVLMBackend, VLMIngestionBackend)
    assert issubclass(VLMIngestionBackend, IngestionBackend)
    assert issubclass(RTVIBackend, IngestionBackend)
    assert issubclass(RTEmbedBackend, IngestionBackend)
    assert available_backends() == (
        "cpu_vlm",
        "gpu_vlm",
        "rtvi",
        "rtvi_cv",
        "rtvi_embed",
        "vlm",
    )
    vlm_backend = get_backend("vlm")
    assert isinstance(vlm_backend, VLMIngestionBackend)
    await vlm_backend.aclose()
    assert get_backend("cpu_vlm").capability_map()["decoder"] == "opencv"
    assert get_backend("gpu_vlm").capability_map()["decoder"] == "pynv"
    backend = get_backend("rtvi")
    assert isinstance(backend, RTVIBackend)
    await backend.aclose()
    embed_backend = get_backend("rtvi_embed")
    assert isinstance(embed_backend, RTEmbedBackend)
    await embed_backend.aclose()

    with pytest.raises(TypeError):
        IngestionBackend()  # pyright: ignore[reportAbstractUsage]


@pytest.mark.parametrize(
    "decoder",
    ["opencv", "pynv"],
)
async def test_vlm_ingestion_backend_uses_selected_decoder(
    monkeypatch: pytest.MonkeyPatch,
    decoder: str,
) -> None:
    import vss.ingestion.vlm as vlm

    captured: dict[str, object] = {}

    async def fake_process_video(
        source: RequestSource, **kwargs: object
    ) -> VlmCaptionsCompletionResponse:
        captured["source"] = source
        captured.update(kwargs)
        return []

    monkeypatch.setattr(vlm, "_process_video", fake_process_video)
    source = RequestSource(source_id="source-1")

    async with VLMIngestionBackend(decoder=decoder) as backend:
        assert await backend.ingest(source, prompt="Describe.") == []

    assert captured["source"] is source
    assert captured["decoder"] == decoder
    assert captured["source_id"] == "source-1"
    assert captured["chunk_duration"] == 60


@pytest.mark.parametrize(
    "backends",
    [[], ["rtvi", "rtvi"], ["rt_embed", "rtvi_embed"], [""], ["unknown-vlm"]],
)
async def test_ingest_rejects_invalid_backend_lists(backends: list[str]) -> None:
    with pytest.raises(ValueError):
        ingest(RequestSource(), backends=backends)


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [("rt_embed", "rtvi_embed"), ("rtvi-embed", "rtvi_embed")],
)
def test_persistent_ingestion_canonicalizes_alias_before_spec_resolution(
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
    canonical: str,
) -> None:
    import vss.ingestion._persistent as persistent

    def capture(pipeline: str, **_: object) -> None:
        assert pipeline == canonical
        raise RuntimeError("spec captured")

    monkeypatch.setattr(persistent, "capture_spec", capture)
    with pytest.raises(RuntimeError, match="spec captured"):
        ingest(RequestSource(), backends=[alias], storage=object())  # type: ignore[arg-type]


async def test_ingest_rejects_non_list_backends() -> None:
    with pytest.raises(TypeError):
        ingest(
            RequestSource(),
            backends=("rtvi",),  # pyright: ignore[reportArgumentType]
        )


def test_single_decode_requires_explicit_decoder() -> None:
    with pytest.raises(ValueError, match="requires an explicit decoder"):
        ingest(RequestSource(), backends=["vlm"], single_decode=True)

    with pytest.raises(TypeError, match="must be a boolean"):
        ingest(
            RequestSource(),
            backends=["vlm"],
            single_decode=1,  # type: ignore[arg-type]
        )


def test_cuda_ipc_requires_single_decode_and_boolean_value() -> None:
    with pytest.raises(ValueError, match="requires single_decode=True"):
        ingest(RequestSource(), backends=["rtvi"], cuda_ipc=True)

    with pytest.raises(TypeError, match="cuda_ipc must be a boolean or None"):
        ingest(
            RequestSource(),
            backends=["rtvi"],
            cuda_ipc="yes",  # type: ignore[arg-type]
        )


def test_persistent_ingestion_requires_exactly_one_backend() -> None:
    with pytest.raises(ValueError, match="exactly one backend"):
        ingest(
            RequestSource(),
            backends=["rtvi", "vlm"],
            storage=object(),  # type: ignore[arg-type]
        )


def test_unknown_environment_backend_fails_before_queue_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VSS_INGESTION_BACKEND", "unsupported")
    with pytest.raises(
        ValueError, match="backends contain unknown selections: unsupported"
    ):
        ingest(RequestSource(), prompt="Describe.")


def test_unknown_explicit_backend_fails_before_queue_submission() -> None:
    with pytest.raises(
        ValueError, match="backends contain unknown selections: unknown"
    ):
        ingest(RequestSource(), backends=["unknown"])


@pytest.mark.parametrize("configured", ["", "  "])
async def test_empty_environment_selection_defaults_to_rtvi(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    selected: list[str] = []

    class FakeBackend:
        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def ingest(
            self, source: RequestSource, **kwargs: object
        ) -> VlmCaptionsCompletionResponse:
            del source, kwargs
            return []

    def fake_get_backend(name: str) -> FakeBackend:
        selected.append(name)
        return FakeBackend()

    monkeypatch.setenv("VSS_INGESTION_BACKEND", configured)
    monkeypatch.setattr(ingestion_service, "get_backend", fake_get_backend)

    assert await ingest(RequestSource()) == {"rtvi": []}
    assert selected == ["rtvi"]


async def test_public_ingest_selects_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected = VlmChunkResponse()

    class FakeBackend:
        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            captured["closed"] = True

        async def ingest(
            self, source: RequestSource, **kwargs: object
        ) -> VlmCaptionsCompletionResponse:
            captured["source"] = source
            captured["kwargs"] = kwargs
            return [expected]

    def fake_get_backend(name: str) -> FakeBackend:
        captured["backend"] = name
        return FakeBackend()

    monkeypatch.setattr(ingestion_service, "get_backend", fake_get_backend)
    source = RequestSource(source_id="source-1")

    job = ingest(source, backends=["vlm"], prompt="Describe.")

    assert isinstance(job, IngestionJob)
    assert job.state is IngestionJobState.RUNNING
    assert captured == {}

    result = await job

    assert result == {"vlm": [expected]}
    assert job.state is IngestionJobState.SUCCEEDED
    assert captured["backend"] == "vlm"
    assert captured["source"] is source
    assert captured["closed"] is True


async def test_explicit_custom_backend_preserves_registered_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str] = []

    class FakeBackend:
        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def ingest(
            self, source: RequestSource, **kwargs: object
        ) -> VlmCaptionsCompletionResponse:
            del source, kwargs
            return []

    monkeypatch.setattr(
        ingestion_service,
        "available_backends",
        lambda: ("MyBackend", "rtvi"),
    )
    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name: selected.append(name) or FakeBackend(),
    )

    assert await ingest(RequestSource(), backends=["MyBackend"]) == {"MyBackend": []}
    assert selected == ["MyBackend"]


async def test_multiple_ingestion_backends_can_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: set[str] = set()
    release = asyncio.Event()

    class FakeBackend:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def ingest(self, source: RequestSource, **kwargs: object) -> Any:
            del source, kwargs
            started.add(self.name)
            if len(started) == 2:
                release.set()
            await release.wait()
            if self.name == "rtvi_embed":
                return [
                    EmbeddingChunkResponse(
                        chunk=ChunkInfo(sourceId="source-1"),
                        embeddings=[0.1],
                    )
                ]
            return [VlmChunkResponse()]

    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name: FakeBackend(name),
    )
    source = RequestSource(source_id="source-1")
    results = await ingest(
        source,
        backends=["rtvi", "rt_embed"],
        prompt="Describe.",
    )

    assert started == {"rtvi", "rtvi_embed"}
    assert isinstance(results["rtvi"][0], VlmChunkResponse)
    assert isinstance(results["rt_embed"][0], EmbeddingChunkResponse)


async def test_backend_failure_cancels_and_settles_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: set[str] = set()
    both_started = asyncio.Event()
    slow_cancelled = asyncio.Event()
    slow_closed = asyncio.Event()

    class FakeBackend:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            if self.name == "rtvi_embed":
                slow_closed.set()

        async def ingest(self, source: RequestSource, **kwargs: object) -> Any:
            del source, kwargs
            started.add(self.name)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            if self.name == "rtvi":
                raise RuntimeError("caption backend failed")
            try:
                await asyncio.Event().wait()
            finally:
                slow_cancelled.set()

    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name: FakeBackend(name),
    )

    job = ingest(RequestSource(), backends=["rtvi", "rtvi_embed"])
    with pytest.raises(RuntimeError, match="caption backend failed"):
        await job
    await job.wait_closed()

    assert slow_cancelled.is_set()
    assert slow_closed.is_set()


async def test_auto_pipeline_defaults_to_rtvi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str] = []

    class FakeBackend:
        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def ingest(
            self,
            source: RequestSource,
            **kwargs: object,
        ) -> VlmCaptionsCompletionResponse:
            del source, kwargs
            return []

    def fake_get_backend(name: str) -> FakeBackend:
        selected.append(name)
        return FakeBackend()

    monkeypatch.delenv("VSS_INGESTION_BACKEND", raising=False)
    monkeypatch.setattr(ingestion_service, "get_backend", fake_get_backend)

    assert await ingest(RequestSource(), prompt="Describe.") == {"rtvi": []}
    assert selected == ["rtvi"]


async def test_default_backends_canonicalize_environment_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str] = []

    class FakeBackend:
        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def ingest(self, source: RequestSource, **kwargs: object) -> list[Any]:
            del source, kwargs
            return []

    monkeypatch.setenv("VSS_INGESTION_BACKEND", "rtvi-embed")
    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name: selected.append(name) or FakeBackend(),
    )

    assert await ingest(RequestSource(), prompt="Describe.") == {"rtvi-embed": []}
    assert selected == ["rtvi_embed"]


def test_ingest_sync_awaits_queued_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = VlmChunkResponse()

    class FakeBackend:
        async def __aenter__(self) -> "FakeBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def ingest(
            self, source: RequestSource, **kwargs: object
        ) -> VlmCaptionsCompletionResponse:
            return [expected]

    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda _: FakeBackend(),
    )
    monkeypatch.setenv("VSS_INGESTION_BACKEND", "rtvi")

    assert ingest_sync(RequestSource(), prompt="Describe.") == {"rtvi": [expected]}


async def test_ingest_job_propagates_backend_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingBackend:
        async def __aenter__(self) -> "FailingBackend":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def ingest(
            self, source: RequestSource, **kwargs: object
        ) -> VlmCaptionsCompletionResponse:
            raise RuntimeError("backend failed")

    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda _: FailingBackend(),
    )
    monkeypatch.setenv("VSS_INGESTION_BACKEND", "rtvi")
    job = ingest(RequestSource(), prompt="Describe.")

    with pytest.raises(RuntimeError, match="backend failed"):
        await job
    assert job.state is IngestionJobState.FAILED
