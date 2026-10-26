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

"""Chunk-level progress without double-counting cumulative provider payloads."""

import asyncio

import httpx
import pytest

from vss.ingestion import RequestSource, RTEmbedBackend, RTVIBackend, RTVIBackendError
from vss.ingestion.contracts import VlmModelInfo
from vss.ingestion.rtvi import _RTVITransport
from vss.observability import collect_vss_metrics


class CaptionTransport:
    released = False

    async def prepare_source(self, source):
        return RequestSource(source_id="uploaded-asset")

    async def get_model_info(self):
        return VlmModelInfo(id="fixture-model")

    async def generate_captions_stream(self, **kwargs):
        yield {
            "chunk_responses": [
                {
                    "chunk_id": 0,
                    "start_time": "0",
                    "end_time": "1",
                    "content": "first",
                    "input_tokens": 2,
                    "output_tokens": 0,
                    "vlm_latency_ms": 0,
                }
            ]
        }
        yield {
            "chunk_responses": [
                {
                    "chunk_id": 0,
                    "start_time": "0",
                    "end_time": "1",
                    "content": "final",
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "vlm_latency_ms": 12,
                }
            ]
        }

    async def release_source(self, source):
        self.released = True


async def test_final_chunks_totals_presence_and_uploaded_source_correlation() -> None:
    transport = CaptionTransport()
    backend = RTVIBackend(transport=transport)
    with collect_vss_metrics() as metrics:
        result = await backend.ingest(
            RequestSource(source_id="original-secret-id"), prompt="private prompt"
        )
    assert metrics.complete and transport.released and result[0].vlm_response == "final"
    chunks = [e for e in metrics.observations if e.name == "ingestion.chunk"]
    tokens = [
        e for e in metrics.observations if e.name == "ingestion.total_input_tokens"
    ]
    assert len(chunks) == 1 and chunks[0].chunk_idx == 0
    assert tokens[0].value == 7
    assert {e.source_id for e in metrics.observations} == {chunks[0].source_id}
    decode = next(
        e for e in metrics.observations if e.name == "ingestion.provider.decode"
    )
    assert decode.value is None and decode.availability == "not_reported"
    infer = next(
        e for e in metrics.observations if e.name == "ingestion.provider.infer"
    )
    assert infer.value == 0.012 and infer.origin == "provider"
    assert "original-secret-id" not in "".join(
        e.model_dump_json() for e in metrics.observations
    )


@pytest.mark.parametrize("embedding", [False, True])
async def test_uploaded_source_alias_preserves_public_caption_and_embedding_ids(
    embedding,
):
    class Transport(CaptionTransport):
        async def generate_video_embeddings_stream(self, **kwargs):
            yield {
                "chunk_responses": [
                    {"start_time": "0", "end_time": "1", "embeddings": [0.1]}
                ]
            }

    transport = Transport()
    backend = (RTEmbedBackend if embedding else RTVIBackend)(transport=transport)
    with collect_vss_metrics() as metrics:
        result = await backend.ingest(
            RequestSource(source_id="private-original"), prompt="private"
        )
        assert result[0].chunk.sourceId == (
            "private-original" if embedding else "uploaded-asset"
        )
        assert metrics.sources(
            [result[0].chunk.sourceId, "private-original"]
        ) == metrics.source_identity("private-original")
    assert metrics.complete and transport.released


async def test_interleaved_uploads_keep_distinct_source_aliases():
    from vss.observability import metric_scope

    class Transport(CaptionTransport):
        async def prepare_source(self, source):
            await asyncio.sleep(0)
            return RequestSource(source_id=source.source_id + "-uploaded")

        async def generate_captions_stream(self, **kwargs):
            await asyncio.sleep(0)
            async for payload in super().generate_captions_stream(**kwargs):
                yield payload

    async def one(source, request_id):
        with metric_scope(request_id=request_id):
            return await RTVIBackend(transport=Transport()).ingest(
                RequestSource(source_id=source), prompt="private"
            )

    with collect_vss_metrics() as metrics:
        result = await asyncio.gather(one("first", "1" * 32), one("second", "2" * 32))
    assert metrics.complete
    for source, request_id, chunks in zip(
        ("first", "second"), ("1" * 32, "2" * 32), result, strict=True
    ):
        assert chunks[0].chunk.sourceId == source + "-uploaded"
        own = [
            event for event in metrics.observations if event.request_id == request_id
        ]
        assert own and {event.source_id for event in own} == {
            metrics.source_identity(source)
        }
    assert metrics.source_identity("first") != metrics.source_identity("second")


async def test_partial_stream_retains_failed_progress_and_primary_error() -> None:
    class Broken(CaptionTransport):
        async def generate_captions_stream(self, **kwargs):
            yield {
                "chunk_responses": [
                    {
                        "chunk_id": 0,
                        "start_time": "0",
                        "end_time": "1",
                        "content": "partial",
                    }
                ]
            }
            raise ValueError("primary")

        async def release_source(self, source):
            raise RuntimeError("cleanup")

    with collect_vss_metrics() as metrics:
        with pytest.raises(RTVIBackendError, match="invalid caption"):
            await RTVIBackend(transport=Broken()).ingest(
                RequestSource(source_id="source"), prompt="prompt"
            )
    assert metrics.complete
    assert (
        next(e for e in metrics.observations if e.name == "ingestion.chunk").status
        == "error"
    )
    assert (
        next(e for e in metrics.observations if e.name == "ingestion.backend").status
        == "error"
    )


async def test_embedding_chunk_local_errors_fail_evidence_and_sort_indexes() -> None:
    class Embeddings(CaptionTransport):
        async def generate_video_embeddings_stream(self, **kwargs):
            yield {
                "chunk_responses": [
                    {"start_time": "1", "end_time": "2", "embeddings": [0.1]},
                    {
                        "start_time": "0",
                        "end_time": "1",
                        "embeddings": [],
                        "error": "chunk failure",
                    },
                ]
            }

    with collect_vss_metrics() as metrics:
        result = await RTEmbedBackend(transport=Embeddings()).ingest(
            RequestSource(source_id="source"), prompt="prompt"
        )
    assert result[0].error == "chunk failure"
    chunks = [e for e in metrics.observations if e.name == "ingestion.chunk"]
    assert {e.chunk_idx for e in chunks} == {0, 1}
    assert next(e for e in chunks if e.chunk_idx == 0).status == "error"
    assert (
        next(e for e in metrics.observations if e.name == "ingestion.backend").status
        == "error"
    )


async def test_repeated_cancellation_does_not_cancel_owned_deletion() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    deleted = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            entered.set()
            await release.wait()
            deleted.append(True)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fixture"
    ) as client:
        transport = _RTVITransport(client=client)
        source = RequestSource(source_id="asset")
        transport._prepared_uploads.add("asset")
        with collect_vss_metrics() as metrics:
            task = asyncio.create_task(transport.release_source(source))
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not deleted
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert deleted == [True] and not transport._prepared_uploads
    assert metrics.complete


async def test_successful_stream_cleanup_failure_retains_unsuccessful_chunks() -> None:
    class BrokenRelease(CaptionTransport):
        async def release_source(self, source):
            raise RuntimeError("release failed")

    with collect_vss_metrics() as metrics:
        with pytest.raises(RuntimeError, match="release failed"):
            await RTVIBackend(transport=BrokenRelease()).ingest(
                RequestSource(source_id="source"), prompt="prompt"
            )
    assert metrics.complete
    assert (
        next(e for e in metrics.observations if e.name == "ingestion.chunk").status
        == "error"
    )
    assert (
        next(
            e for e in metrics.observations if e.name == "ingestion.total_input_tokens"
        ).value
        == 7
    )
    assert (
        next(e for e in metrics.observations if e.name == "ingestion.release").status
        == "error"
    )


async def test_cleanup_retry_counter_reports_actual_conflicts() -> None:
    attempts = []

    async def handler(request):
        attempts.append(request.method)
        return httpx.Response(409 if len(attempts) < 3 else 200, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fixture"
    ) as client:
        with collect_vss_metrics() as metrics:
            await _RTVITransport(client=client).delete_video("asset", retry_delay_s=0)
    assert attempts == ["DELETE"] * 3
    assert (
        next(
            e for e in metrics.observations if e.name == "ingestion.cleanup_retries"
        ).value
        == 2
    )
    assert [e.status for e in metrics.observations if e.name == "ingestion.http"] == [
        "error",
        "error",
        "ok",
    ]


async def test_interrupted_embedding_updates_keep_distinct_chunk_indexes():
    class PartialEmbedding(CaptionTransport):
        async def generate_video_embeddings_stream(self, **kwargs):
            a = {"start_time": "0", "end_time": "1", "embeddings": [0.1]}
            b = {"start_time": "1", "end_time": "2", "embeddings": [0.2]}
            yield {"chunk_responses": [a, b]}
            yield {"chunk_responses": [a | {"embeddings": [0.3]}]}
            raise ValueError("interrupted")

    with collect_vss_metrics() as metrics:
        with pytest.raises(ValueError, match="interrupted"):
            await RTEmbedBackend(transport=PartialEmbedding()).ingest(
                RequestSource(source_id="source"), prompt="prompt"
            )
    assert metrics.complete
    chunks = [e for e in metrics.observations if e.name == "ingestion.chunk"]
    assert [e.chunk_idx for e in chunks] == [0, 1]
    assert all(e.status == "error" for e in chunks)


async def test_cancellation_survives_secondary_owned_delete_failure():
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(503, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fixture"
    ) as client:
        transport = _RTVITransport(client=client)
        transport._prepared_uploads.add("asset")
        with collect_vss_metrics() as metrics:
            task = asyncio.create_task(
                transport.release_source(RequestSource(source_id="asset"))
            )
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert metrics.complete
    assert transport._prepared_uploads == {"asset"}
    assert (
        next(e for e in metrics.observations if e.name == "ingestion.cleanup").status
        == "error"
    )


async def test_real_upload_transport_preserves_original_metric_source(tmp_path):
    from vss.observability import metric_scope

    path = tmp_path / "private-input.mp4"
    path.write_bytes(b"controlled fixture")

    async def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/files"
        return httpx.Response(200, json={"id": "uploaded-asset"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://fixture"
    ) as client:
        transport = _RTVITransport(client=client)
        with collect_vss_metrics() as metrics, metric_scope(source_id="original"):
            prepared = await transport.prepare_source(
                RequestSource(source_id="original", url=str(path))
            )
    assert prepared.source_id == "uploaded-asset" and metrics.complete
    assert len({e.source_id for e in metrics.observations}) == 1
    assert {e.name for e in metrics.observations} == {
        "ingestion.upload",
        "ingestion.http",
    }
