# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hermetic tests for the bounded memory introspection workflow."""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from typing import Any

import pytest

from vss_core._foundation.errors import BackendUnreachableError
from vss_core.introspection.judge import InvalidJudgeResponseError
from vss_core.introspection.models import GroundedGap
from vss_core.introspection.models import IntrospectionRequest
from vss_core.introspection.models import IntrospectionSettings
from vss_core.introspection.models import SufficiencyDecision
from vss_core.introspection.models import VLMEvidence
from vss_core.introspection.orchestrator import introspect
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.models import JobInfo
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import SensorInfo
from vss_core.memory.models import TimestampPoint
from vss_core.memory.models import TimeWindow
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryService
from vss_core.memory.store import storage_id_for

START = "2026-08-26T12:00:00Z"
END = "2026-08-26T12:01:00Z"


def _record(
    *,
    record_id: str | None = "event-1",
    query: str = "forklift",
    answer: str = "A forklift crossed the loading bay.",
    sensor: str = "camera-east",
    start: datetime | None = None,
    end: datetime | None = None,
) -> UnifiedMemoryRecord:
    start = start or datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    end = end or datetime(2026, 8, 26, 12, 1, tzinfo=UTC)
    return UnifiedMemoryRecord(
        job=JobInfo(
            job_id="search-1",
            record_id=record_id,
            record_type="search_hit" if record_id is not None else None,
            group="search",
            status="completed",
            created_at=start,
        ),
        input=MemoryInput(
            query=query,
            sensors=[SensorInfo(id=sensor)],
            window=TimeWindow(
                start=TimestampPoint(timestamp=start),
                end=TimestampPoint(timestamp=end),
            ),
        ),
        output=MemoryOutput(answer=answer),
    )


def _memory(*records: UnifiedMemoryRecord) -> tuple[MemoryService, InMemoryStore]:
    store = InMemoryStore()
    for record in records:
        store.upsert(record)
    store.upsert_ids.clear()
    return MemoryService(store), store


def _gap(
    *,
    question: str = "What was the forklift carrying?",
    sensor: str = "camera-east",
    start_time: str = "2026-08-26T12:00:10Z",
    end_time: str = "2026-08-26T12:00:20Z",
) -> GroundedGap:
    return GroundedGap(
        question=question,
        sensor=sensor,
        start_time=start_time,
        end_time=end_time,
    )


def _decision(*, sufficient: bool = False, gaps: list[GroundedGap] | None = None) -> SufficiencyDecision:
    return SufficiencyDecision(
        sufficient=sufficient,
        reason="Memory is enough." if sufficient else "A visual detail is missing.",
        evidence_record_ids=["event-1"],
        gaps=[] if sufficient else (gaps if gaps is not None else [_gap()]),
    )


class _Judge:
    def __init__(
        self,
        decision: SufficiencyDecision | None = None,
        error: Exception | None = None,
        *,
        expected_query: str = "forklift",
    ) -> None:
        self.decision = decision or _decision()
        self.error = error
        self.expected_query = expected_query
        self.calls = 0
        self.records: list[UnifiedMemoryRecord] = []

    async def judge(self, **kwargs: Any) -> SufficiencyDecision:
        self.calls += 1
        assert kwargs["query"] == self.expected_query
        self.records = kwargs["records"]
        if self.error is not None:
            raise self.error
        return self.decision


class _Synthesizer:
    def __init__(self, answer: str = "Supported answer.", *, delay: float = 0) -> None:
        self.answer = answer
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def synthesize(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.answer


class _Runner:
    def __init__(
        self,
        *,
        failures: set[int] | None = None,
        delay: float = 0,
        persisted: bool = True,
    ) -> None:
        self.failures = failures or set()
        self.delay = delay
        self.persisted = persisted
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> VLMEvidence:
        index = len(self.calls)
        self.calls.append(kwargs)
        assert kwargs["intent"] == "introspection"
        if self.delay:
            await asyncio.sleep(self.delay)
        if index in self.failures:
            raise RuntimeError(f"inference {index + 1} unavailable")
        return VLMEvidence(
            job_id=f"vlm-{index + 1}",
            persisted=self.persisted,
            sensor=kwargs["sensor"],
            start_time=kwargs["start_time"],
            end_time=kwargs["end_time"],
            answer=f"visual answer {index + 1}",
        )


async def _run(
    *,
    memory: MemoryService | None = None,
    judge: _Judge | None = None,
    synthesizer: _Synthesizer | None = None,
    runner: _Runner | None = None,
    settings: IntrospectionSettings | None = None,
) -> tuple[Any, _Judge, _Synthesizer, _Runner]:
    if memory is None:
        memory, _ = _memory(_record(record_id=None), _record())
    actual_judge = judge or _Judge()
    actual_synthesizer = synthesizer or _Synthesizer()
    actual_runner = runner or _Runner()
    result = await introspect(
        IntrospectionRequest(query="forklift"),
        memory=memory,
        judge=actual_judge,
        synthesizer=actual_synthesizer,
        vlm_runner=actual_runner,
        settings=settings or IntrospectionSettings(),
    )
    return result, actual_judge, actual_synthesizer, actual_runner


@pytest.mark.asyncio
async def test_sufficient_memory_uses_zero_vlm_and_one_synthesis() -> None:
    result, judge, synthesizer, runner = await _run(judge=_Judge(_decision(sufficient=True)))

    assert result.status == "completed"
    assert result.sufficient_from_memory is True
    assert result.answer == "Supported answer."
    assert result.model_dump().keys() == {
        "status",
        "sufficient_from_memory",
        "answer",
        "memory_evidence",
        "vlm_evidence",
        "unresolved_gaps",
    }
    assert judge.calls == 1
    assert len(synthesizer.calls) == 1
    assert runner.calls == []


@pytest.mark.asyncio
async def test_builds_internal_parent_child_query_with_all_request_selectors() -> None:
    class _QueryMemory:
        def __init__(self) -> None:
            self.queries: list[Any] = []

        def query(self, query: Any) -> list[UnifiedMemoryRecord]:
            self.queries.append(query)
            return [_record()] if query.record_type else [_record(record_id=None)]

    memory = _QueryMemory()
    judge = _Judge(_decision(sufficient=True))
    result = await introspect(
        IntrospectionRequest(
            query="forklift",
            sensor="camera-east",
            job_id="search-1",
            record_id="event-1",
            record_type="search_hit",
            group="search",
            start_time=START,
            end_time=END,
        ),
        memory=memory,  # type: ignore[arg-type]
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(max_memory_records=7),
    )

    assert result.status == "completed"
    query = memory.queries[0]
    assert query.text is None
    assert query.sensor_id == "camera-east"
    assert query.job_id == "search-1"
    assert query.record_id == "event-1"
    assert query.record_type == "search_hit"
    assert query.group == "search"
    assert query.time_field == "window"
    assert query.include_children is True
    assert query.limit == 7
    parent_query = memory.queries[1]
    assert parent_query.parents_only is True
    assert parent_query.include_children is False
    assert parent_query.job_id == "search-1"
    assert parent_query.sensor_id == "camera-east"
    assert parent_query.group == "search"
    assert parent_query.time_field == "created_at"
    assert parent_query.since is None
    assert parent_query.until is None
    assert parent_query.text is None
    assert parent_query.record_id is None
    assert [record.job.is_child for record in judge.records] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity",
    (
        {"job_id": "search-1"},
        {"job_id": "search-1", "record_type": "search_hit", "record_id": "event-1"},
    ),
)
async def test_exact_identity_uses_paraphrased_question_only_as_judge_prompt(identity: dict[str, str]) -> None:
    child = _record(
        query="Describe the worker's clothing.",
        answer="The worker wore a hard hat and yellow safety vest.",
    )
    memory, _ = _memory(child)
    judge = _Judge(_decision(sufficient=True), expected_query="Was the worker wearing PPE?")

    result = await introspect(
        IntrospectionRequest(
            query="Was the worker wearing PPE?",
            **identity,
        ),
        memory=memory,
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(),
    )

    assert result.status == "completed"
    assert judge.calls == 1
    assert [record.job.record_id for record in judge.records] == ["event-1"]


@pytest.mark.asyncio
async def test_exact_identity_never_embeds_the_question_when_semantic_retrieval_is_enabled() -> None:
    child = _record()
    store = InMemoryStore()
    store.upsert(child)

    class _Provider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed_query(self, text: str) -> list[float]:
            self.calls.append(text)
            return [1.0]

    class _Semantic:
        def semantic_search(self, *_args: Any) -> list[str]:
            raise AssertionError("identity lookup must not use semantic search")

    provider = _Provider()
    memory = MemoryService(
        store,
        semantic_memory=_Semantic(),  # type: ignore[arg-type]
        embedding_provider=provider,
        retrieval_mode="hybrid",
    )
    judge = _Judge(_decision(sufficient=True), expected_query="Was the worker wearing PPE?")

    result = await introspect(
        IntrospectionRequest(query="Was the worker wearing PPE?", job_id="search-1"),
        memory=memory,
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(),
    )

    assert result.status == "completed"
    assert provider.calls == []
    assert [record.job.record_id for record in judge.records] == ["event-1"]


@pytest.mark.asyncio
async def test_hybrid_sensor_time_recall_resolves_semantic_child_and_hydrates_parent_for_judge() -> None:
    parent = _record(record_id=None, query="shift overview", answer="Loading bay shift summary.")
    child = _record(query="worker activity", answer="A worker crossed the loading bay.")
    store = InMemoryStore()
    store.upsert(parent)
    store.upsert(child)

    class _Provider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed_query(self, text: str) -> list[float]:
            self.calls.append(text)
            return [1.0]

    class _Semantic:
        def __init__(self) -> None:
            self.queries: list[Any] = []

        def semantic_search(self, query: Any, _vector: list[float], candidate_count: int) -> list[str]:
            self.queries.append(query)
            assert candidate_count == 7
            return [storage_id_for(child)]

    provider = _Provider()
    semantic = _Semantic()
    memory = MemoryService(
        store,
        semantic_memory=semantic,  # type: ignore[arg-type]
        embedding_provider=provider,
        retrieval_mode="hybrid",
        semantic_candidate_count=7,
    )
    judge = _Judge(_decision(sufficient=True), expected_query="Were safety procedures followed?")

    result = await introspect(
        IntrospectionRequest(
            query="Were safety procedures followed?",
            sensor="camera-east",
            start_time=START,
            end_time=END,
        ),
        memory=memory,
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(),
    )

    assert result.status == "completed"
    assert provider.calls == ["Were safety procedures followed?"]
    semantic_query = semantic.queries[0]
    assert semantic_query.sensor_id == "camera-east"
    assert semantic_query.time_field == "window"
    assert semantic_query.since is not None
    assert semantic_query.until is not None
    assert [record.job.is_child for record in judge.records] == [True, False]
    assert judge.records[0] is child
    assert judge.records[1] is parent


@pytest.mark.asyncio
async def test_parent_expansion_does_not_add_out_of_scope_parents() -> None:
    scoped_child = _record()
    other_sensor_parent = _record(record_id=None, sensor="camera-west")
    memory, _ = _memory(other_sensor_parent, scoped_child)
    judge = _Judge(_decision(sufficient=True))

    result = await introspect(
        IntrospectionRequest(
            query="forklift",
            sensor="camera-east",
            start_time=START,
            end_time=END,
        ),
        memory=memory,
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(),
    )

    assert result.status == "completed"
    assert [record.job.record_id for record in judge.records] == ["event-1"]
    assert all(record.input.sensors[0].id == "camera-east" for record in judge.records)


@pytest.mark.asyncio
async def test_parent_expansion_does_not_add_parents_outside_the_time_window() -> None:
    scoped_child = _record()
    earlier_parent = _record(
        record_id=None,
        start=datetime(2026, 8, 26, 10, 0, tzinfo=UTC),
        end=datetime(2026, 8, 26, 10, 5, tzinfo=UTC),
    )
    memory, _ = _memory(earlier_parent, scoped_child)
    judge = _Judge(_decision(sufficient=True))

    result = await introspect(
        IntrospectionRequest(
            query="forklift",
            sensor="camera-east",
            start_time=START,
            end_time=END,
        ),
        memory=memory,
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(),
    )

    assert result.status == "completed"
    assert [record.job.record_id for record in judge.records] == ["event-1"]


@pytest.mark.asyncio
async def test_parent_expansion_keeps_windowless_parent_of_time_scoped_child() -> None:
    child = _record()
    parent = _record(record_id=None, query="shift notes", answer="Warehouse shift summary.")
    assert parent.input is not None
    parent = parent.model_copy(update={"input": parent.input.model_copy(update={"window": None})})
    memory, _ = _memory(parent, child)
    judge = _Judge(_decision(sufficient=True))

    result = await introspect(
        IntrospectionRequest(
            query="forklift",
            sensor="camera-east",
            start_time=START,
            end_time=END,
        ),
        memory=memory,
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(),
    )

    assert result.status == "completed"
    assert [record.job.is_child for record in judge.records] == [True, False]
    assert judge.records[1].output.answer == "Warehouse shift summary."


@pytest.mark.asyncio
async def test_parent_expansion_keeps_open_ended_parent_of_time_scoped_child() -> None:
    child = _record()
    parent = _record(record_id=None, query="shift notes", answer="Open warehouse shift.")
    assert parent.input is not None and parent.input.window is not None
    parent = parent.model_copy(
        update={
            "input": parent.input.model_copy(update={"window": TimeWindow(start=parent.input.window.start, end=None)})
        }
    )
    memory, _ = _memory(parent, child)
    judge = _Judge(_decision(sufficient=True))

    result = await introspect(
        IntrospectionRequest(
            query="forklift",
            sensor="camera-east",
            start_time=START,
            end_time=END,
        ),
        memory=memory,
        judge=judge,
        synthesizer=_Synthesizer(),
        vlm_runner=_Runner(),
        settings=IntrospectionSettings(),
    )

    assert result.status == "completed"
    assert [record.job.is_child for record in judge.records] == [True, False]
    assert judge.records[1].output.answer == "Open warehouse shift."


@pytest.mark.asyncio
async def test_one_grounded_gap_runs_one_vlm_and_one_final_synthesis() -> None:
    result, judge, synthesizer, runner = await _run()

    assert result.status == "completed"
    assert result.sufficient_from_memory is False
    assert len(result.vlm_evidence) == 1
    assert result.unresolved_gaps == []
    assert judge.calls == len(synthesizer.calls) == len(runner.calls) == 1


@pytest.mark.asyncio
async def test_vlm_calls_are_capped_at_three_in_returned_order() -> None:
    gaps = [_gap(question=f"question {index}") for index in range(4)]
    result, _, _, runner = await _run(judge=_Judge(_decision(gaps=gaps)))

    assert [call["prompt"] for call in runner.calls] == ["question 0", "question 1", "question 2"]
    assert result.status == "partial"
    assert "question 3" in result.unresolved_gaps[0]
    assert "query cap is 3" in result.unresolved_gaps[0]


@pytest.mark.asyncio
async def test_gap_over_sixty_seconds_is_rejected_without_vlm_call() -> None:
    gap = _gap(end_time="2026-08-26T12:01:11Z")
    result, _, _, runner = await _run(judge=_Judge(_decision(gaps=[gap])))

    assert runner.calls == []
    assert result.status == "partial"
    assert "exceeds the 60s clip limit" in result.unresolved_gaps[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gap",
    [
        _gap(sensor="invented-camera"),
        _gap(start_time="2026-08-26T13:00:00Z", end_time="2026-08-26T13:00:10Z"),
    ],
    ids=["invented-sensor", "ungrounded-window"],
)
async def test_ungrounded_sensor_or_window_is_rejected_without_vlm_call(gap: GroundedGap) -> None:
    result, _, _, runner = await _run(judge=_Judge(_decision(gaps=[gap])))

    assert runner.calls == []
    assert result.status == "partial"
    assert "rejected as ungrounded" in result.unresolved_gaps[0]


@pytest.mark.asyncio
async def test_no_memory_returns_structured_result_without_model_calls() -> None:
    memory, _ = _memory()
    result, judge, synthesizer, runner = await _run(memory=memory)

    assert result.model_dump() == {
        "status": "no_memory",
        "sufficient_from_memory": False,
        "answer": None,
        "memory_evidence": [],
        "vlm_evidence": [],
        "unresolved_gaps": [],
    }
    assert judge.calls == 0
    assert synthesizer.calls == []
    assert runner.calls == []


@pytest.mark.asyncio
async def test_judge_invalid_once_then_succeeds_inside_single_client_call() -> None:
    class _RetryingJudge(_Judge):
        def __init__(self) -> None:
            super().__init__(_decision(sufficient=True))
            self.attempts = 0

        async def judge(self, **kwargs: Any) -> SufficiencyDecision:
            self.calls += 1
            for attempt in range(2):
                self.attempts += 1
                if attempt == 0:
                    continue
                return self.decision
            raise AssertionError

    judge = _RetryingJudge()
    result, _, _, runner = await _run(judge=judge)

    assert result.status == "completed"
    assert judge.calls == 1
    assert judge.attempts == 2
    assert runner.calls == []


@pytest.mark.asyncio
async def test_judge_invalid_twice_returns_partial_supported_answer_without_speculation() -> None:
    judge = _Judge(error=InvalidJudgeResponseError("invalid after two attempts"))
    result, _, synthesizer, runner = await _run(judge=judge)

    assert result.status == "partial"
    assert result.answer == "Supported answer."
    assert result.memory_evidence
    assert runner.calls == []
    assert len(synthesizer.calls) == 1
    assert "judge failed after validation retry" in result.unresolved_gaps[0]


@pytest.mark.asyncio
async def test_judge_backend_failure_carries_nonserialized_exit_signal() -> None:
    judge = _Judge(error=BackendUnreachableError("rt-vlm", "offline"))
    result, _, _, _ = await _run(judge=judge)

    assert result.status == "partial"
    assert result.failure_kind == "backend_unreachable"
    assert "failure_kind" not in result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_mixed_vlm_failure_keeps_successful_evidence_and_synthesizes_once() -> None:
    gaps = [_gap(question=f"question {index}") for index in range(3)]
    result, _, synthesizer, runner = await _run(
        judge=_Judge(_decision(gaps=gaps)),
        runner=_Runner(failures={1}),
    )

    assert len(runner.calls) == 3
    assert [item.job_id for item in result.vlm_evidence] == ["vlm-1", "vlm-3"]
    assert result.status == "partial"
    assert "inference 2 unavailable" in result.unresolved_gaps[0]
    assert len(synthesizer.calls) == 1


@pytest.mark.asyncio
async def test_all_vlm_calls_fail_but_memory_supports_one_final_synthesis() -> None:
    gaps = [_gap(question=f"question {index}") for index in range(2)]
    result, _, synthesizer, _ = await _run(
        judge=_Judge(_decision(gaps=gaps)),
        runner=_Runner(failures={0, 1}),
    )

    assert result.status == "partial"
    assert result.answer == "Supported answer."
    assert result.vlm_evidence == []
    assert len(result.unresolved_gaps) == 2
    assert len(synthesizer.calls) == 1


@pytest.mark.asyncio
async def test_total_workflow_timeout_is_partial_and_does_not_synthesize_after_deadline() -> None:
    result, _, synthesizer, runner = await _run(
        runner=_Runner(delay=2),
        settings=IntrospectionSettings(timeout_seconds=1),
    )

    assert result.status == "partial"
    assert result.answer is None
    assert result.failure_kind == "timeout"
    assert "failure_kind" not in result.model_dump(mode="json")
    assert any("workflow timed out after 1 seconds" in gap for gap in result.unresolved_gaps)
    assert synthesizer.calls == []
    assert 0 < runner.calls[0]["timeout_seconds"] <= 1


@pytest.mark.asyncio
async def test_introspection_never_writes_memory() -> None:
    memory, store = _memory(_record(record_id=None), _record())
    result, _, _, _ = await _run(memory=memory)

    assert result.status == "completed"
    assert store.upsert_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [True, False], ids=["persistence-enabled", "persistence-disabled"])
async def test_injected_vlm_adapter_reports_internal_job_persistence_policy(persisted: bool) -> None:
    result, _, _, _ = await _run(runner=_Runner(persisted=persisted))

    assert result.status == "completed"
    assert result.vlm_evidence[0].persisted is persisted
    assert result.vlm_evidence[0].job_id == "vlm-1"
