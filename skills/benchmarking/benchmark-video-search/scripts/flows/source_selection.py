# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate saved source selection without performing retrieval or reselection."""
from __future__ import annotations

import math
import re
from typing import Any

POLICY_VERSION = "source-cap-v1"


def source_key(hit: dict[str, Any]) -> str | None:
    for field in ("source_id", "sensor_id", "video_name"):
        value = hit.get(field)
        if isinstance(value, str) and value.strip() and value != "unknown":
            return value
    return None


def comparable(hit: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in hit.items()
            if key not in {"critic_result", "verification", "_verification_source", "_raw"}}


def validate_selection(original, selected, selection, *, cap=None, requested=None, candidate_limit=None):
    """Check complete-pool provenance and greedy positions; return metadata only."""
    if not isinstance(original, list) or any(not isinstance(hit, dict) for hit in original):
        raise ValueError("source selection original_pool must be a list of result objects")
    if not isinstance(selected, list) or any(not isinstance(hit, dict) for hit in selected):
        raise ValueError("source selection selected hits must be a list of result objects")
    if not isinstance(selection, dict) or selection.get("policy_version") != POLICY_VERSION:
        raise ValueError("unknown or missing source selection policy")
    if any(hit.get("source_id") is not None and not isinstance(hit["source_id"], str) for hit in original):
        raise ValueError("source selection source_id must be a string when present")
    for name in ("max_hits_per_source", "requested_top_k", "candidate_limit"):
        value = selection.get(name)
        if type(value) is not int or not 1 <= value <= 100000:
            raise ValueError(f"invalid source selection {name}")
    for name, expected in (("max_hits_per_source", cap), ("requested_top_k", requested), ("candidate_limit", candidate_limit)):
        if expected is not None and selection[name] != expected:
            raise ValueError(f"source selection {name} disagrees with request")
    completeness = selection.get("completeness")
    if not isinstance(completeness, dict) or completeness.get("complete") is not True:
        raise ValueError("source selection original pool is not complete")
    if completeness.get("snapshot_verified") is not True and not (
        not original and completeness.get("absent_uploads_anchor") is True
    ):
        raise ValueError("source selection snapshot is unverified")
    scans = completeness.get("scans")
    expected_scans = completeness.get("expected_scans")
    if not isinstance(scans, list) or not scans or any(not isinstance(scan, dict) for scan in scans) or type(expected_scans) is not int or expected_scans != len(scans):
        raise ValueError("source selection lacks per-attribute scan proof")
    if completeness.get("absent_uploads_anchor") is True:
        if original or completeness.get("snapshot_verified") is not False or any(
            scan.get("absent_uploads_anchor") is not True or scan.get("index") != "mdx-behavior-2025-01-01"
            or type(scan.get("eligible_documents")) is not int
            or scan.get("eligible_documents") != 0 for scan in scans
        ):
            raise ValueError("invalid absent-uploads source selection proof")
    else:
        identities = set()
        for scan in scans:
            if (not isinstance(scan, dict) or scan.get("exhaustive") is not True
                or scan.get("snapshot_verified") is not True or type(scan.get("budget")) is not int
                or scan.get("budget") != selection["candidate_limit"]
                or type(scan.get("eligible_documents")) is not int
                or not 0 <= scan["eligible_documents"] <= selection["candidate_limit"]
                or type(scan.get("scored_documents")) is not int
                or scan.get("scored_documents") != scan["eligible_documents"]
                or any(not isinstance(scan.get(field), str) or re.fullmatch(r"[0-9a-f]{64}", scan[field]) is None
                       for field in ("snapshot_digest", "snapshot_scope_digest"))
                or scan.get("absent_uploads_anchor")):
                raise ValueError("incomplete source selection scan proof")
            snapshot_indices = scan.get("snapshot_indices")
            if (not isinstance(snapshot_indices, list) or not snapshot_indices
                or any(not isinstance(index, dict) or any(not isinstance(index.get(field), str) or not index[field]
                                                         for field in ("name", "uuid")) for index in snapshot_indices)
                or len({index["name"] for index in snapshot_indices}) != len(snapshot_indices)):
                raise ValueError("incomplete source selection scan proof")
            identities.add((scan["snapshot_digest"], scan["snapshot_scope_digest"],
                            tuple(sorted((index["name"], index["uuid"]) for index in snapshot_indices))))
        if len(identities) != 1:
            raise ValueError("source selection snapshot scope changed")
    if len(original) > selection["candidate_limit"] or any(
        isinstance(hit.get("similarity"), bool) or not isinstance(hit.get("similarity"), (float, int))
        or not math.isfinite(hit["similarity"]) for hit in original
    ):
        raise ValueError("source selection original ranking exceeds bounds or has invalid scores")
    stages = selection.get("stages")
    if not isinstance(stages, list) or any(not isinstance(row, dict) for row in stages):
        raise ValueError("source selection stages are missing or malformed")
    for row in stages:
        before, after = row.get("before"), row.get("after")
        if (not isinstance(row.get("stage"), str) or not row["stage"]
            or row.get("kind") not in {"aggregation", "filter", "conversion", "truncation"}
            or type(before) is not int or type(after) is not int or not 0 <= after <= before
            or type(row.get("removed")) is not int or row.get("removed") != before - after
            or row.get("kind") in {"truncation", "conversion"} and after != before):
            raise ValueError("source selection original ranking lost candidates")
    indices = selection.get("selected_indices")
    if not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(original) for i in indices):
        raise ValueError("invalid source selection indices")
    if indices != sorted(set(indices)) or len(indices) != len(selected):
        raise ValueError("source selection indices must be unique ordered and aligned")
    expected_indices = []
    counts = {}
    known_sources = set()
    unknown_rows = excluded_cap = excluded_top = 0
    for index, hit in enumerate(original):
        key = source_key(hit)
        if key is None:
            unknown_rows += 1
        else:
            known_sources.add(key)
        if key is not None and counts.get(key, 0) >= selection["max_hits_per_source"]:
            excluded_cap += 1
            continue
        if len(expected_indices) >= selection["requested_top_k"]:
            excluded_top += 1
            continue
        expected_indices.append(index)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
    if indices != expected_indices:
        raise ValueError("source selection positions do not match declared policy")
    for index, hit in zip(indices, selected, strict=True):
        if comparable(original[index]) != comparable(hit):
            raise ValueError("source selection changed a result outside critic annotations")
    declared = selection.get("counts")
    expected_counts = {"original_pool": len(original), "selected": len(selected),
                       "known_sources": len(known_sources), "unknown_rows": unknown_rows,
                       "excluded_by_source_cap": excluded_cap, "excluded_by_top_k": excluded_top}
    if not isinstance(declared, dict) or any(type(value) is not int for value in declared.values()) or declared != expected_counts:
        raise ValueError("source selection counts disagree with saved results")
    return {key: value for key, value in selection.items() if key != "original_pool"}
