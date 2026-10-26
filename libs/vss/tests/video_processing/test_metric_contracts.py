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

"""Interleaved stream attribution, bounded sinks and actual stage boundaries."""

import asyncio

from vss.observability import collect_vss_metrics, metric_scope, record
from vss.observability.context import current_scope, defer_observations


async def test_decode_advancement_and_close_in_other_tasks_keep_origin(
    processing, register, video_path
) -> None:
    register()
    stream = processing.decode_frames(
        video_path,
        processing.DecodePlan(),
        source_id="origin",
        options=processing.DecodeOptions(decoder="memory", batch_size=2),
    )
    with collect_vss_metrics() as original, metric_scope(source_id="origin"):
        before = current_scope()
        first = await anext(stream)
        assert current_scope() == before
        with collect_vss_metrics() as other, metric_scope(source_id="other"):

            async def advance():
                return await anext(stream)

            second = await asyncio.create_task(advance())
            await asyncio.create_task(stream.aclose())
            record("summarization.events", 1, library="summarization")
        assert first.source_id == second.source_id == "origin"
    assert original.complete and other.complete
    assert len(other.observations) == 1
    assert all(e.library == "processing" for e in original.observations)
    lifetime = next(
        e for e in original.observations if e.name == "processing.decode.lifetime"
    )
    assert lifetime.status == "closed"
    assert lifetime.source_id != other.observations[0].source_id


def test_deferred_sinks_keep_origin_and_never_recurse_indefinitely() -> None:
    delivered = []

    def sink(event):
        delivered.append(event)

    with collect_vss_metrics(sink=sink) as outer:
        with defer_observations():
            record("summarization.events", 1, library="summarization")
            with collect_vss_metrics(sink=sink) as inner:
                record("summarization.events", 2, library="summarization")
            assert not delivered
        assert len(delivered) == 1
    assert outer.complete
    assert not inner.complete  # exporter work remained at nested scope exit
    assert inner.observations[0].value == 2

    def recursive_sink(_event):
        record("summarization.events", 2, library="summarization")

    with collect_vss_metrics(sink=recursive_sink) as recursive:
        record("summarization.events", 1, library="summarization")
    assert not recursive.complete
    assert len(recursive.observations) == 2


async def test_processing_probe_extract_sample_and_encode_observations(
    processing, register, video_path, monkeypatch
) -> None:
    backend = register()
    with collect_vss_metrics() as metrics:
        await processing.probe_video(
            video_path,
            source_id="fixture",
            options=processing.DecodeOptions(decoder="memory"),
        )
        extracted = await processing.extract_frames(
            video_path,
            [0, 0.2],
            source_id="fixture",
            options=processing.DecodeOptions(decoder="memory"),
        )
        samples = [
            frame
            async for frame in processing.subsample_frames(
                video_path,
                processing.SamplingPolicy(mode="all"),
                source_id="fixture",
                options=processing.DecodeOptions(decoder="memory"),
            )
        ]
    assert metrics.complete and extracted and samples
    names = {e.name for e in metrics.observations}
    assert {
        "processing.probe",
        "processing.extract",
        "processing.sample",
        "processing.decode",
        "processing.copy",
        "processing.close",
    } <= names
    assert backend.sessions and all(session.closed for session in backend.sessions)


async def test_processing_ingestion_summary_share_redacted_source_lineage(
    processing, register, video_path
):
    """Production boundaries with fixture decoder/provider, not live GPU evidence."""
    import json

    from vss.ingestion import RequestSource, RTVIBackend
    from vss.ingestion.contracts import VlmModelInfo
    from vss.summarization import GeneratedSummary, SummaryGenerator, summarize_video

    source = "https://user:password@example.invalid/video?token=private"
    uploaded = "private-uploaded-service-id"
    model = "private-provider-model"

    class Transport:
        released = False

        async def prepare_source(self, original):
            return RequestSource(source_id=uploaded)

        async def get_model_info(self):
            return VlmModelInfo(id=model)

        async def generate_captions_stream(self, **kwargs):
            yield {
                "chunk_responses": [
                    {
                        "chunk_id": 0,
                        "start_time": "0",
                        "end_time": "1",
                        "content": "Private fixture caption",
                    }
                ]
            }

        async def release_source(self, prepared):
            self.released = True

    class Generator(SummaryGenerator):
        async def generate(self, events, **kwargs):
            return GeneratedSummary(text="Private fixture summary", model=model)

    backend = register()
    transport = Transport()
    with collect_vss_metrics() as metrics:
        await processing.extract_frames(
            video_path,
            [0],
            source_id=source,
            options=processing.DecodeOptions(decoder="memory"),
        )
        chunks = await RTVIBackend(transport=transport).ingest(
            RequestSource(source_id=source), prompt="Private prompt"
        )
        await summarize_video(chunks, generator=Generator(), model=model)
    assert metrics.complete and transport.released
    assert backend.sessions and all(session.closed for session in backend.sessions)
    assert {event.library for event in metrics.observations} == {
        "processing",
        "ingestion",
        "summarization",
    }
    assert len({event.run_id for event in metrics.observations}) == 1
    assert len({event.source_id for event in metrics.observations}) == 1
    required = {
        "source_id",
        "pipeline",
        "pipeline_version",
        "model",
        "backend",
        "schema_version",
    }
    assert all(required <= event.model_dump().keys() for event in metrics.observations)
    assert all(
        event.source_id.startswith("opaque:") and event.pipeline.startswith("opaque:")
        for event in metrics.observations
    )
    encoded = json.dumps([event.model_dump() for event in metrics.observations])
    assert all(
        secret not in encoded
        for secret in (
            source,
            uploaded,
            model,
            "Private fixture",
            "Private prompt",
            "password",
            "token=private",
        )
    )


async def test_sampling_interleaved_advancement_and_early_exit(
    processing, register, video_path
):
    register()
    stream = processing.subsample_frames(
        video_path,
        processing.SamplingPolicy(mode="all"),
        source_id="origin",
        options=processing.DecodeOptions(decoder="memory"),
    )
    with collect_vss_metrics() as original:
        await anext(stream)
        assert current_scope().source_id == "unknown"
        with collect_vss_metrics() as other:
            await asyncio.create_task(anext(stream))
            await asyncio.create_task(stream.aclose())
            record("summarization.events", 1, library="summarization")
    assert original.complete and other.complete
    assert len(other.observations) == 1
    assert (
        next(e for e in original.observations if e.name == "processing.sample").status
        == "closed"
    )


def test_deferred_overflow_and_throwing_sink_preserve_workload():
    def broken(_event):
        raise RuntimeError("observer failure")

    with collect_vss_metrics(sink=broken, max_events=512) as metrics:
        with defer_observations():
            for _ in range(150):
                record("summarization.events", 1, library="summarization")
    assert not metrics.complete
    assert len(metrics.observations) == 150
