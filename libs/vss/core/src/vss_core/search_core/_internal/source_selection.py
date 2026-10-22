# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in source diversity over a complete, unchanged attribute ranking."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from ..errors import InvalidInputError


@dataclass
class SourceSelectionContext:
    scans: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    max_candidates: int | None = None
    expected_scans: int | None = None


source_selection_context: ContextVar[SourceSelectionContext | None] = ContextVar(
    "source_selection_context", default=None
)


def record_stage(stage: str, before: int, after: int, *, kind: str) -> None:
    context = source_selection_context.get()
    if context is None:
        return
    context.stages.append({"stage": stage, "before": before, "after": after, "removed": before - after, "kind": kind})
    if after < before and kind in {"truncation", "conversion"}:
        raise InvalidInputError(
            f"Source diversity requires a complete ranking: {stage} removed {before - after} candidate(s); increase candidate_limit or repair invalid indexed documents"
        )


def source_identity(row: Any) -> str | None:
    """Use exact resolved identity, then exact display name; unknowns are independent."""
    for value in (getattr(row, "source_id", None), row.sensor_id, row.video_name):
        if isinstance(value, str) and value.strip() and value != "unknown":
            return value
    return None


def validate_source_selection(
    mode: str, source_type: str, candidate_limit: int | None, max_hits_per_source: int | None
) -> None:
    cap = max_hits_per_source
    if cap is None:
        return
    if type(cap) is not int or not 1 <= cap <= 100000:
        raise InvalidInputError("max_hits_per_source must be a positive integer <= 100000")
    if mode != "attribute" or source_type != "video_file" or candidate_limit is None:
        raise InvalidInputError(
            "max_hits_per_source requires attribute mode, video_file source_type, and candidate_limit"
        )


def validate_selection_request(inp: Any, cap: int | None) -> None:
    validate_source_selection(inp.search_mode, inp.source_type, inp.candidate_limit, cap)


def selection_positions(pool: list[Any], *, cap: int, requested_top_k: int) -> tuple[list[int], dict[str, int]]:
    selected = []
    counts_by_source: dict[str, int] = {}
    known_sources = set()
    unknown_rows = excluded_cap = excluded_top_k = 0
    for index, row in enumerate(pool):
        source = source_identity(row)
        if source is None:
            unknown_rows += 1
        else:
            known_sources.add(source)
        if source is not None and counts_by_source.get(source, 0) >= cap:
            excluded_cap += 1
            continue
        if len(selected) >= requested_top_k:
            excluded_top_k += 1
            continue
        selected.append(index)
        if source is not None:
            counts_by_source[source] = counts_by_source.get(source, 0) + 1
    return selected, {
        "original_pool": len(pool),
        "selected": len(selected),
        "known_sources": len(known_sources),
        "unknown_rows": unknown_rows,
        "excluded_by_source_cap": excluded_cap,
        "excluded_by_top_k": excluded_top_k,
    }


def select_sources(pool: list[Any], *, cap: int, requested_top_k: int, candidate_limit: int):
    from ..models.search import SourceSelection

    context = source_selection_context.get()
    if context is None or not context.scans:
        raise InvalidInputError("Source diversity requires verified exact-scan evidence for every attribute")
    scans = context.scans
    if context.expected_scans is not None and len(scans) != context.expected_scans:
        raise InvalidInputError("Source diversity requires exact-scan evidence for every requested attribute")
    absent = all(scan.get("absent_uploads_anchor") is True for scan in scans)
    if absent:
        if pool or any(scan.get("eligible_documents") != 0 for scan in scans):
            raise InvalidInputError("Absent uploads coverage conflicts with nonempty source ranking")
    else:
        if any(scan.get("absent_uploads_anchor") for scan in scans):
            raise InvalidInputError("Attribute scan scope changed during source selection")
        for scan in scans:
            if (
                scan.get("exhaustive") is not True
                or scan.get("snapshot_verified") is not True
                or not scan.get("snapshot_digest")
                or not scan.get("snapshot_scope_digest")
                or not scan.get("snapshot_indices")
                or type(scan.get("eligible_documents")) is not int
                or scan["eligible_documents"] > candidate_limit
            ):
                raise InvalidInputError("Source diversity requires complete snapshot-verified indexed coverage")
        identities = {(scan["snapshot_digest"], scan["snapshot_scope_digest"]) for scan in scans}
        if len(identities) != 1:
            raise InvalidInputError("Attribute snapshots changed between scans; repeat against an unchanged gallery")
    if len(pool) > candidate_limit:
        raise InvalidInputError(
            "Post-aggregation ranking exceeds candidate_limit; increase the explicit candidate budget"
        )
    selected, counts = selection_positions(pool, cap=cap, requested_top_k=requested_top_k)
    return SourceSelection(
        original_pool=pool,
        selected_indices=selected,
        policy_version="source-cap-v1",
        max_hits_per_source=cap,
        requested_top_k=requested_top_k,
        candidate_limit=candidate_limit,
        counts=counts,
        stages=list(context.stages),
        completeness={
            "complete": True,
            "snapshot_verified": not absent,
            "absent_uploads_anchor": absent,
            "scans": list(scans),
            "expected_scans": context.expected_scans if context.expected_scans is not None else len(scans),
        },
    )
