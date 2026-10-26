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

"""Stage evidence through real library code and controlled provider responses."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import vss.summarization.service as service
from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.observability import (
    OpenTelemetrySink,
    collect_vss_metrics,
    metric_scope,
    record,
)
from vss.summarization import (
    GeneratedSummary,
    OpenAISummaryGenerator,
    SummaryGenerator,
    summarize_video,
    summarize_video_sync,
)


def chunk(source: str = "test-source") -> VlmChunkResponse:
    return VlmChunkResponse(
        chunk=ChunkInfo(sourceId=source, start_pts=0, end_pts=10_000_000_000),
        vlm_response="A person enters.",
    )


class Generator(SummaryGenerator):
    async def generate(
        self, events: list[dict[str, Any]], **_: Any
    ) -> GeneratedSummary:
        return GeneratedSummary(
            text="Fixture summary", model="fixture-model", prompt_tokens=10
        )


def test_sync_metrics_preserve_compatibility_and_token_availability() -> None:
    with collect_vss_metrics() as metrics:
        result = summarize_video_sync([chunk()], generator=Generator())
    by_name = {e.name: e for e in metrics.observations}
    assert metrics.complete
    assert result.usage is not None
    assert result.usage.summary_tokens == 0 and result.usage.aggregation_tokens == 10
    assert by_name["summarization.events"].value == 1
    assert by_name["summarization.batches"].value == 1
    assert by_name["summarization.requests"].value == 1
    assert by_name["summarization.input_tokens"].value == 10
    assert by_name["summarization.output_tokens"].availability == "not_reported"
    assert by_name["summarization.total"].unit == "seconds"
    assert all(e.source_id.startswith("opaque:") for e in metrics.observations)


async def test_empty_summary_has_no_llm_call_and_no_fake_usage() -> None:
    with collect_vss_metrics() as metrics:
        await summarize_video([])
    by_name = {e.name: e for e in metrics.observations}
    assert metrics.complete
    assert by_name["summarization.requests"].value == 0
    assert by_name["summarization.llm"].availability == "not_applicable"
    assert by_name["summarization.input_tokens"].value is None


async def test_multiple_calls_are_separate_single_batches_with_span_attribution():
    """Several chunks or requests must not invent per-request batch support."""
    with collect_vss_metrics() as metrics:
        await summarize_video([chunk()], generator=Generator())
        await summarize_video([chunk(), chunk()], generator=Generator())
        await summarize_video([], generator=Generator())
    assert metrics.complete
    totals = [e for e in metrics.observations if e.name == "summarization.total"]
    assert len(totals) == 3 and len({e.span_id for e in totals}) == 3
    for total, expected_chunks, expected_requests in zip(
        totals, (1, 2, 0), (1, 1, 0), strict=True
    ):
        own = {e.name: e for e in metrics.observations if e.parent_id == total.span_id}
        assert own["summarization.batches"].value == 1
        assert own["summarization.chunks"].value == expected_chunks
        assert own["summarization.requests"].value == expected_requests


def test_repeated_sync_calls_preserve_one_batch_per_call():
    with collect_vss_metrics() as metrics:
        for _ in range(2):
            summarize_video_sync([chunk()], generator=Generator())
    assert metrics.complete
    batches = [e for e in metrics.observations if e.name == "summarization.batches"]
    assert [e.value for e in batches] == [1, 1]
    assert len({e.parent_id for e in batches}) == 2


@pytest.mark.parametrize("api_type", ["responses", "chat_completions"])
async def test_provider_boundary_distinguishes_missing_from_measured_zero(
    api_type: str,
) -> None:
    async def create(**_: Any) -> Any:
        return SimpleNamespace(
            model="provider-model",
            output_text="Summary",
            usage=SimpleNamespace(input_tokens=0, prompt_tokens=0),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Summary"), finish_reason="stop"
                )
            ],
        )

    client = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    generator = OpenAISummaryGenerator(client=client, api_type=api_type)
    with collect_vss_metrics() as metrics:
        await summarize_video([chunk()], generator=generator)
    inputs = [e for e in metrics.observations if e.name == "summarization.input_tokens"]
    outputs = [
        e for e in metrics.observations if e.name == "summarization.output_tokens"
    ]
    assert len(inputs) == len(outputs) == 1
    assert inputs[0].value == 0 and inputs[0].availability == "measured"
    assert outputs[0].value is None and outputs[0].availability == "not_reported"
    assert inputs[0].origin == "provider"


@pytest.mark.parametrize("cancellation", [False, True])
async def test_primary_generation_error_survives_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, cancellation: bool
) -> None:
    class BrokenGenerator(Generator):
        async def generate(self, *_: Any, **__: Any) -> GeneratedSummary:
            if cancellation:
                raise asyncio.CancelledError
            raise ValueError("primary failure")

        async def aclose(self) -> None:
            raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(service, "get_generator", lambda _: BrokenGenerator())
    with collect_vss_metrics() as metrics:
        with pytest.raises(asyncio.CancelledError if cancellation else ValueError):
            await summarize_video([chunk()])
    by_name = {e.name: e for e in metrics.observations}
    assert metrics.complete  # complete failure evidence, not workload success
    assert by_name["summarization.cleanup"].status == "error"
    assert by_name["summarization.total"].status == (
        "cancelled" if cancellation else "error"
    )


async def test_multisource_lineage_is_bounded_and_correlated() -> None:
    with collect_vss_metrics() as metrics:
        await summarize_video([chunk("first"), chunk("second")], generator=Generator())
    assert metrics.complete
    assert len(metrics.source_sets) == 1
    reference, sources = next(iter(metrics.source_sets.items()))
    assert len(sources) == 2
    assert {e.source_id for e in metrics.observations} == {reference}


def test_otel_does_not_export_source_or_model_as_metric_labels() -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    reader, exporter = InMemoryMetricReader(), InMemorySpanExporter()
    traces, meters = TracerProvider(), MeterProvider(metric_readers=[reader])
    traces.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        sink = OpenTelemetrySink(tracer_provider=traces, meter_provider=meters)
        with (
            collect_vss_metrics(sink=sink) as metrics,
            metric_scope(source_id="secret://source", model="private-model"),
        ):
            record("summarization.events", 1, library="summarization")
        assert metrics.complete
        spans = exporter.get_finished_spans()
        assert len(spans) == 1 and spans[0].attributes is not None
        assert spans[0].attributes["source_id"].startswith("opaque:")
        data = reader.get_metrics_data()
        points = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points
        assert set(points[0].attributes) == {
            "library",
            "status",
            "origin",
            "schema_version",
        }
    finally:
        traces.shutdown()
        meters.shutdown()
