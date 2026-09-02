# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded, memory-first introspection orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Literal

from vss_core.introspection.models import GroundedGap
from vss_core.introspection.models import IntrospectionRequest
from vss_core.introspection.models import IntrospectionResult
from vss_core.introspection.models import IntrospectionSettings
from vss_core.introspection.models import MemoryEvidence
from vss_core.introspection.models import SufficiencyDecision
from vss_core.introspection.models import VLMEvidence
from vss_core.introspection.models import parse_utc_instant
from vss_core.memory.store import MemoryQuery

if TYPE_CHECKING:
    from vss_core.introspection.protocols import AnswerSynthesizer
    from vss_core.introspection.protocols import IntrospectionVLMRunner
    from vss_core.introspection.protocols import SufficiencyJudge
    from vss_core.memory.models import UnifiedMemoryRecord
    from vss_core.memory.service import MemoryService


@dataclass
class _WorkflowState:
    records: list[UnifiedMemoryRecord] = field(default_factory=list)
    memory_records: list[UnifiedMemoryRecord] = field(default_factory=list)
    vlm_evidence: list[VLMEvidence] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    pending: list[GroundedGap] = field(default_factory=list)
    sufficient_from_memory: bool = False
    failure_kind: Literal["backend_unreachable"] | None = None


async def introspect(
    request: IntrospectionRequest,
    *,
    memory: MemoryService,
    judge: SufficiencyJudge,
    synthesizer: AnswerSynthesizer,
    vlm_runner: IntrospectionVLMRunner,
    settings: IntrospectionSettings,
) -> IntrospectionResult:
    """Answer one request with one judge, bounded inspections, and one synthesis."""
    state = _WorkflowState()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.timeout_seconds
    try:
        async with asyncio.timeout_at(deadline):
            return await _run_workflow(
                request,
                memory=memory,
                judge=judge,
                synthesizer=synthesizer,
                vlm_runner=vlm_runner,
                settings=settings,
                state=state,
                deadline=deadline,
            )
    except TimeoutError:
        state.unresolved.extend(f"{_gap_label(gap)}: not inspected before workflow timeout" for gap in state.pending)
        state.unresolved.append(f"introspection workflow timed out after {settings.timeout_seconds} seconds")
        return _result(
            status="partial",
            sufficient_from_memory=state.sufficient_from_memory,
            answer=None,
            memory_records=state.memory_records,
            vlm_evidence=state.vlm_evidence,
            unresolved=state.unresolved,
            failure_kind="timeout",
        )


async def _run_workflow(
    request: IntrospectionRequest,
    *,
    memory: MemoryService,
    judge: SufficiencyJudge,
    synthesizer: AnswerSynthesizer,
    vlm_runner: IntrospectionVLMRunner,
    settings: IntrospectionSettings,
    state: _WorkflowState,
    deadline: float,
) -> IntrospectionResult:
    identity_scoped = request.job_id is not None
    query = MemoryQuery(
        # Identity selectors choose the evidence; the natural-language question
        # remains the judge/synthesis prompt and must not filter that evidence.
        text=None if identity_scoped else request.query,
        sensor_id=request.sensor,
        job_id=request.job_id,
        record_id=request.record_id,
        record_type=request.record_type,
        group=request.group,
        since=request.start_time,
        until=request.end_time,
        time_field="window",
        include_children=True,
        parents_only=False,
        limit=settings.max_memory_records,
    )
    try:
        state.records = await _retrieve_records(memory, query, settings.max_memory_records)
    except Exception as error:
        return _result(
            status="partial",
            sufficient_from_memory=False,
            answer=None,
            unresolved=[f"memory retrieval failed: {error}"],
            failure_kind="backend_unreachable" if _is_backend_unreachable(error) else None,
        )
    if not state.records:
        return _result(status="no_memory", sufficient_from_memory=False, answer=None)

    try:
        decision = await judge.judge(query=request.query, records=state.records)
    except Exception as error:
        if _is_backend_unreachable(error):
            state.failure_kind = "backend_unreachable"
        state.memory_records = _useful_records(state.records)
        state.unresolved.append(f"sufficiency judge failed after validation retry: {error}")
        answer = await _synthesize_best_effort(request, synthesizer, state)
        return _result(
            status="partial",
            sufficient_from_memory=False,
            answer=answer,
            memory_records=state.memory_records,
            unresolved=state.unresolved,
            failure_kind=state.failure_kind,
        )

    state.sufficient_from_memory = decision.sufficient
    state.memory_records = _validated_memory_evidence(decision, state.records, state.unresolved)
    if decision.sufficient:
        answer = await _synthesize_best_effort(request, synthesizer, state)
        return _result(
            status="completed" if answer is not None and not state.unresolved else "partial",
            sufficient_from_memory=True,
            answer=answer,
            memory_records=state.memory_records,
            unresolved=state.unresolved,
            failure_kind=state.failure_kind,
        )

    valid_gaps = _validated_gaps(decision.gaps, state.records, settings, state.unresolved)
    state.pending = list(valid_gaps)
    for index, gap in enumerate(valid_gaps):
        if index >= settings.max_vlm_queries:
            state.unresolved.extend(
                f"{_gap_label(remaining)}: not inspected because the VLM query cap is {settings.max_vlm_queries}"
                for remaining in valid_gaps[index:]
            )
            break
        try:
            evidence = await vlm_runner.run(
                sensor=gap.sensor,
                start_time=gap.start_time,
                end_time=gap.end_time,
                prompt=gap.question,
                intent="introspection",
                timeout_seconds=max(0.0, deadline - asyncio.get_running_loop().time()),
            )
            state.vlm_evidence.append(evidence)
        except Exception as error:
            state.unresolved.append(f"{_gap_label(gap)}: VLM inspection failed: {error}")
        state.pending.pop(0)

    if not decision.gaps:
        state.unresolved.append(f"memory was insufficient: {decision.reason}")

    answer = await _synthesize_best_effort(request, synthesizer, state)
    status: Literal["completed", "partial"] = "completed" if answer is not None and not state.unresolved else "partial"
    return _result(
        status=status,
        sufficient_from_memory=False,
        answer=answer,
        memory_records=state.memory_records,
        vlm_evidence=state.vlm_evidence,
        unresolved=state.unresolved,
        failure_kind=state.failure_kind,
    )


async def _synthesize_best_effort(
    request: IntrospectionRequest,
    synthesizer: AnswerSynthesizer,
    state: _WorkflowState,
) -> str | None:
    try:
        return await synthesizer.synthesize(
            query=request.query,
            memory_evidence=state.memory_records,
            vlm_evidence=state.vlm_evidence,
            unresolved_gaps=state.unresolved,
        )
    except Exception as error:
        if _is_backend_unreachable(error):
            state.failure_kind = "backend_unreachable"
        state.unresolved.append(f"answer synthesis failed: {error}")
        return None


def _validated_memory_evidence(
    decision: SufficiencyDecision,
    records: list[UnifiedMemoryRecord],
    unresolved: list[str],
) -> list[UnifiedMemoryRecord]:
    by_id = {_record_id(record): record for record in records}
    selected: list[UnifiedMemoryRecord] = []
    for record_id in decision.evidence_record_ids:
        record = by_id.get(record_id)
        if record is None:
            unresolved.append(f"judge referenced unknown memory evidence record_id {record_id!r}")
        else:
            selected.append(record)
    return selected


def _validated_gaps(
    gaps: list[GroundedGap],
    records: list[UnifiedMemoryRecord],
    settings: IntrospectionSettings,
    unresolved: list[str],
) -> list[GroundedGap]:
    valid: list[GroundedGap] = []
    for gap in gaps:
        duration = (parse_utc_instant(gap.end_time) - parse_utc_instant(gap.start_time)).total_seconds()
        if duration > settings.max_clip_duration_seconds:
            unresolved.append(
                f"{_gap_label(gap)}: rejected because {duration:g}s exceeds "
                f"the {settings.max_clip_duration_seconds}s clip limit"
            )
            continue
        try:
            SufficiencyDecision(
                sufficient=False,
                reason="validate one gap",
                evidence_record_ids=[],
                gaps=[gap],
            ).validate_grounding(records)
        except ValueError as error:
            unresolved.append(f"{_gap_label(gap)}: rejected as ungrounded: {error}")
            continue
        valid.append(gap)
    return valid


def _prioritize(records: list[UnifiedMemoryRecord]) -> list[UnifiedMemoryRecord]:
    indexed = list(enumerate(records))
    indexed.sort(
        key=lambda item: (
            0
            if item[1].job.is_child and item[1].job.record_type in {"event", "search_hit", "incident"}
            else 1
            if item[1].job.is_parent and item[1].output is not None and bool(item[1].output.answer)
            else 2,
            item[0],
        )
    )
    return [record for _, record in indexed]


async def _retrieve_records(
    memory: MemoryService,
    query: MemoryQuery,
    limit: int,
) -> list[UnifiedMemoryRecord]:
    records = await asyncio.to_thread(memory.query, query)
    known_parents = {record.job.job_id for record in records if record.job.is_parent}
    child_job_ids = list(dict.fromkeys(record.job.job_id for record in records if record.job.is_child))
    for job_id in child_job_ids:
        if job_id in known_parents:
            continue
        parents = await asyncio.to_thread(
            memory.query,
            MemoryQuery(
                job_id=job_id,
                sensor_id=query.sensor_id,
                group=query.group,
                include_children=False,
                parents_only=True,
                limit=1,
            ),
        )
        if parents and _parent_matches_time_scope(parents[0], query):
            records.append(parents[0])
            known_parents.add(job_id)

    prioritized = _prioritize(list({_record_identity(record): record for record in records}.values()))
    selected = prioritized[:limit]
    useful_parents = [
        record
        for record in prioritized
        if record.job.is_parent and record.output is not None and bool(record.output.answer)
    ]
    if useful_parents and selected and not any(record.job.is_parent for record in selected):
        selected[-1] = useful_parents[0]
    return selected


def _useful_records(records: list[UnifiedMemoryRecord]) -> list[UnifiedMemoryRecord]:
    useful = [record for record in records if record.output is not None and bool(record.output.answer)]
    return useful or records


def _record_id(record: UnifiedMemoryRecord) -> str:
    return record.job.record_id or record.job.job_id


def _record_identity(record: UnifiedMemoryRecord) -> tuple[str, str | None, str | None]:
    return (record.job.job_id, record.job.record_type, record.job.record_id)


def _parent_matches_time_scope(parent: UnifiedMemoryRecord, query: MemoryQuery) -> bool:
    """Keep scoped parents without windows, but reject known non-overlapping windows."""
    window = parent.input.window if parent.input is not None else None
    if window is None:
        return True
    if query.since is not None and window.end is not None and window.end.timestamp < query.since:
        return False
    return query.until is None or window.start.timestamp <= query.until


def _gap_label(gap: GroundedGap) -> str:
    return f"{gap.question!r} on {gap.sensor!r} from {gap.start_time} to {gap.end_time}"


def _is_backend_unreachable(error: BaseException) -> bool:
    return any(
        klass.__name__
        in {
            "BackendUnreachableError",
            "ConnectError",
            "ConnectTimeout",
            "HTTPStatusError",
            "ReadTimeout",
            "VSTError",
            "VIOSTimeoutError",
        }
        for klass in type(error).__mro__
    )


def _result(
    *,
    status: Literal["completed", "partial", "no_memory"],
    sufficient_from_memory: bool,
    answer: str | None,
    memory_records: list[UnifiedMemoryRecord] | None = None,
    vlm_evidence: list[VLMEvidence] | None = None,
    unresolved: list[str] | None = None,
    failure_kind: Literal["backend_unreachable", "timeout"] | None = None,
) -> IntrospectionResult:
    return IntrospectionResult(
        status=status,
        sufficient_from_memory=sufficient_from_memory,
        answer=answer,
        memory_evidence=[
            MemoryEvidence(record_id=_record_id(record), job_id=record.job.job_id) for record in (memory_records or [])
        ],
        vlm_evidence=vlm_evidence or [],
        unresolved_gaps=unresolved or [],
        failure_kind=failure_kind,
    )


__all__ = ["introspect"]
