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

"""Private output capture and structural checks for manual video review.

The checker verifies a review worksheet, not the truth of its annotations.
Do not publish capture files or worksheets containing model/source text.
"""

import argparse
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from benchmarks.e2e.config import Strict, fingerprint

LIMIT = 2 * 1024**2


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        facts = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(facts.st_mode)
            or facts.st_mode & 0o077
            or facts.st_size > LIMIT
        ):
            raise ValueError("private_review_file")
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("private_review_budget")
    return raw


def capture_output(body, profile):
    payload = json.loads(body["choices"][0]["message"]["content"])
    raw = json.dumps(
        {
            "schema_version": "private_workflow_output.1",
            "source_sha256": profile.video_sha256,
            "profile_sha256": fingerprint(profile.model_dump(mode="json")),
            "duration_s": profile.duration_s,
            "payload": payload,
        },
        allow_nan=False,
    ).encode()
    if len(raw) > LIMIT:
        raise ValueError("private_output_budget")
    fd = os.open(
        profile.output_capture,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "scope": "private_text_never_published",
        "timing": "after_result_boundary",
    }


class Interval(Strict):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_s <= self.start_s:
            raise ValueError("review_interval")
        return self


class Claim(Strict):
    id: str = Field(min_length=1, max_length=128)
    # A reviewer splits the complete summary into atomic claims; each event is
    # also reviewed. Hashes bind the quoted claim text without publishing it.
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["supported", "contradicted", "uncertain"]
    evidence: list[Interval] = Field(min_length=1, max_length=64)
    timestamp_verdict: Literal["accurate", "inaccurate", "not_applicable", "uncertain"]
    summary_start_char: int | None = Field(default=None, ge=0)
    summary_end_char: int | None = Field(default=None, gt=0)


class ReferenceEvent(Strict):
    id: str = Field(min_length=1, max_length=128)
    source_interval: Interval
    matched_claim_ids: list[str] = Field(max_length=10000)


class Worksheet(Strict):
    schema_version: Literal["manual_video_review.1"] = "manual_video_review.1"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    review_method: Literal["full_video_playback"]
    timestamp_tolerance_s: float = Field(ge=0, le=30)
    watched: list[Interval] = Field(min_length=1, max_length=128)
    summary_fully_decomposed: bool
    reference_inventory_complete: bool
    claims: list[Claim] = Field(min_length=1, max_length=10000)
    reference_events: list[ReferenceEvent] = Field(max_length=10000)


def bound_output(capture_raw, operation_raw):
    if max(len(capture_raw), len(operation_raw)) > LIMIT:
        raise ValueError("private_review_budget")
    capture = json.loads(capture_raw)
    report = json.loads(operation_raw)
    if not isinstance(report, dict) or not isinstance(report.get("operation"), dict):
        raise ValueError("complete_cli_report_required")
    op = report["operation"]
    if (
        any(
            not isinstance(capture.get(key), str)
            or not re.fullmatch(r"[a-f0-9]{64}", capture[key])
            for key in ("source_sha256", "profile_sha256")
        )
        or not isinstance(op.get("cleanup"), dict)
        or capture.get("schema_version") != "private_workflow_output.1"
        or report.get("source_sha256") != capture["source_sha256"]
        or report.get("config_sha256") != capture["profile_sha256"]
        or report.get("status") != "completed_diagnostic"
        or op["private_output_capture"]["sha256"]
        != hashlib.sha256(capture_raw).hexdigest()
        or op["status"] != "completed_diagnostic"
        or any(value is not True for value in op["cleanup"].values())
        or not op["cleanup"]
    ):
        raise ValueError("review_evidence_binding")
    payload = capture["payload"]
    if (
        not isinstance(payload.get("video_summary"), str)
        or not 0 < len(payload["video_summary"].encode()) <= 65536
        or not isinstance(payload.get("events"), list)
        or not 1 <= len(payload["events"]) <= 10000
        or any(
            not isinstance(e, dict)
            or not isinstance(e.get("description"), str)
            or not e["description"].strip()
            for e in payload["events"]
        )
    ):
        raise ValueError("review_output_shape")
    if (
        fingerprint(payload["video_summary"]) != op["summary_sha256"]
        or fingerprint(payload["events"]) != op["events_sha256"]
    ):
        raise ValueError("review_output_identity")
    duration = capture["duration_s"]
    if type(duration) not in {int, float} or not 0 < duration <= 600:
        raise ValueError("review_duration")
    return capture, payload, duration


def timestamp_precheck(capture_raw, operation_raw):
    """Numeric source-relative timestamp structure, never factual accuracy."""
    capture, payload, duration = bound_output(capture_raw, operation_raw)
    indices = {
        key: []
        for key in (
            "unsupported",
            "reversed",
            "outside_source",
            "instantaneous",
            "bounded_positive_interval",
        )
    }
    for i, event in enumerate(payload["events"]):
        start, end = event.get("start_time"), event.get("end_time")
        if any(
            type(v) not in {int, float} or not math.isfinite(v) for v in (start, end)
        ):
            indices["unsupported"].append(i)
            continue
        if end < start:
            indices["reversed"].append(i)
        if start < 0 or end > duration or start > duration or end < 0:
            indices["outside_source"].append(i)
        if start == end:
            indices["instantaneous"].append(i)
        if 0 <= start < end <= duration:
            indices["bounded_positive_interval"].append(i)
    return {
        "schema_version": "descriptive_timestamp_precheck.1",
        "source_sha256": capture["source_sha256"],
        "capture_sha256": hashlib.sha256(capture_raw).hexdigest(),
        "operation_sha256": hashlib.sha256(operation_raw).hexdigest(),
        "assumption": "numeric_source_relative_seconds_no_tolerance",
        "event_count": len(payload["events"]),
        "indices": indices,
        "counts": {k: len(v) for k, v in indices.items()},
        "semantic_quality": "not_reviewed",
        "qualified": False,
        "comparative_statistics": None,
    }


def caption_alignment_precheck(capture_raw, operation_raw, capsule_raw):
    """Compare source-relative event intervals with their exact caption evidence.

    This detects disjoint provenance windows. It neither proves factual truth
    nor infers/corrects a relative timestamp convention in generated text.
    """
    from benchmarks.e2e.worker import caption_identity
    from vss.ingestion.contracts import VlmChunkResponse

    capture, payload, duration = bound_output(capture_raw, operation_raw)
    report = json.loads(operation_raw)
    op = report["operation"]
    if len(capsule_raw) > LIMIT or hashlib.sha256(capsule_raw).hexdigest() != op.get(
        "private_caption_capture", {}
    ).get("sha256"):
        raise ValueError("caption_review_binding")
    body = json.loads(capsule_raw)
    contract = report["module_comparison_contract"]
    task = {
        "source_sha256": capture["source_sha256"],
        **{
            key: contract[key]
            for key in (
                "duration_s",
                "chunk_duration_s",
                "vlm_model",
                "caption_prompt_sha256",
                "caption_max_tokens",
            )
        },
    }
    if (
        body.get("schema_version") != "private_caption_capsule.1"
        or body.get("task") != task
        or task["duration_s"] != duration
        or not isinstance(body.get("chunks"), list)
        or not 1 <= len(body["chunks"]) <= 64
    ):
        raise ValueError("caption_review_contract")
    for item in body["chunks"]:
        raw_chunk = item.get("chunk") if isinstance(item, dict) else None
        if (
            not isinstance(raw_chunk, dict)
            or any(
                type(raw_chunk.get(key)) is not int
                for key in ("chunkIdx", "start_pts", "end_pts")
            )
            or not isinstance(raw_chunk.get("sourceId"), str)
        ):
            raise ValueError("caption_review_windows")
    chunks = [VlmChunkResponse.model_validate(c) for c in body["chunks"]]
    identity = fingerprint(caption_identity(chunks))
    if identity != body.get("caption_sha256") or identity != op.get("caption_sha256"):
        raise ValueError("caption_review_identity")
    windows = {}
    for item in chunks:
        chunk = item.chunk
        key = (chunk.sourceId, chunk.chunkIdx)
        if (
            key in windows
            or not chunk.sourceId
            or type(chunk.start_pts) is not int
            or type(chunk.end_pts) is not int
            or not 0 <= chunk.start_pts < chunk.end_pts <= (duration + 0.1) * 1e9
        ):
            raise ValueError("caption_review_windows")
        windows[key] = (chunk.start_pts / 1e9, chunk.end_pts / 1e9)
    rows = []
    for index, event in enumerate(payload["events"]):
        start, end = event.get("start_time"), event.get("end_time")
        refs = event.get("evidence")
        row = {"event_index": index, "status": "unassessable", "reason": None}
        if (
            any(
                type(v) not in {int, float} or not math.isfinite(v)
                for v in (start, end)
            )
            or not 0 <= start < end <= duration
        ):
            row["reason"] = "unsupported_event_interval"
        elif (
            not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, dict)
                or not isinstance(ref.get("source_id"), str)
                or type(ref.get("chunk_idx")) is not int
                for ref in refs
            )
        ):
            row["reason"] = "missing_or_malformed_references"
        elif any((r["source_id"], r["chunk_idx"]) not in windows for r in refs):
            row["reason"] = "unknown_caption_reference"
        else:
            spans = [windows[(r["source_id"], r["chunk_idx"])] for r in refs]
            overlaps = [max(start, a) < min(end, b) for a, b in spans]
            row.update(
                status="not_disjoint_from_all"
                if any(overlaps)
                else "disjoint_from_all",
                reason=None,
                event_interval_s=[start, end],
                referenced_intervals_s=[list(span) for span in spans],
                overlaps=overlaps,
            )
        rows.append(row)
    return {
        "schema_version": "descriptive_caption_alignment.1",
        "source_sha256": capture["source_sha256"],
        "capture_sha256": hashlib.sha256(capture_raw).hexdigest(),
        "operation_sha256": hashlib.sha256(operation_raw).hexdigest(),
        "capsule_sha256": hashlib.sha256(capsule_raw).hexdigest(),
        "assumption": (
            "numeric_source_relative_seconds_half_open_overlap_no_auto_offset"
        ),
        "event_count": len(rows),
        "events": rows,
        "counts": {
            key: sum(r["status"] == key for r in rows)
            for key in ("disjoint_from_all", "not_disjoint_from_all", "unassessable")
        },
        "semantic_quality": "not_established_by_provenance_check",
        "qualified": False,
        "comparative_statistics": None,
    }


def check(capture_raw, operation_raw, review):
    worksheet = Worksheet.model_validate(review)
    capture, payload, duration = bound_output(capture_raw, operation_raw)
    if (
        hashlib.sha256(capture_raw).hexdigest() != worksheet.capture_sha256
        or hashlib.sha256(operation_raw).hexdigest() != worksheet.operation_sha256
        or capture["source_sha256"] != worksheet.source_sha256
    ):
        raise ValueError("review_evidence_binding")
    spans = (
        worksheet.watched
        + [v for c in worksheet.claims for v in c.evidence]
        + [e.source_interval for e in worksheet.reference_events]
    )
    if any(s.end_s > duration for s in spans):
        raise ValueError("review_timestamp_outside_source")
    end = 0
    for span in sorted(worksheet.watched, key=lambda v: v.start_s):
        if span.start_s > end:
            raise ValueError("incomplete_source_review")
        end = max(end, span.end_s)
    if (
        end < duration
        or not worksheet.summary_fully_decomposed
        or not worksheet.reference_inventory_complete
    ):
        raise ValueError("incomplete_source_review")
    ids = [c.id for c in worksheet.claims]
    ref_ids = [e.id for e in worksheet.reference_events]
    if len(set(ids)) != len(ids) or len(set(ref_ids)) != len(ref_ids):
        raise ValueError("duplicate_review_id")
    event_ids = {"event:" + str(i) for i in range(len(payload["events"]))}
    summary_claims = [c for c in worksheet.claims if c.id.startswith("summary:")]
    if (
        not summary_claims
        or {c.id for c in worksheet.claims if not c.id.startswith("summary:")}
        != event_ids
    ):
        raise ValueError("incomplete_output_review")
    summary = payload["video_summary"]
    if not isinstance(summary, str) or not 0 < len(summary.encode()) <= 65536:
        raise ValueError("review_summary_budget")
    end = 0
    for claim in sorted(summary_claims, key=lambda c: c.summary_start_char or 0):
        a, b = claim.summary_start_char, claim.summary_end_char
        if (
            a is None
            or b is None
            or not 0 <= a < b <= len(summary)
            or fingerprint(summary[a:b]) != claim.text_sha256
        ):
            raise ValueError("review_summary_claim_identity")
        if a < end or summary[end:a].strip():
            raise ValueError("incomplete_or_overlapping_summary_review")
        end = b
    if summary[end:].strip():
        raise ValueError("incomplete_summary_review")
    by_id = {c.id: c for c in worksheet.claims}
    for i, event in enumerate(payload["events"]):
        claim = by_id["event:" + str(i)]
        if claim.text_sha256 != fingerprint(event["description"]):
            raise ValueError("review_claim_identity")
    known = {c.id for c in worksheet.claims if c.verdict == "supported"}
    if any(
        len(set(e.matched_claim_ids)) != len(e.matched_claim_ids)
        or not set(e.matched_claim_ids) <= known
        for e in worksheet.reference_events
    ):
        raise ValueError("review_reference_match")
    return {
        "schema_version": "manual_video_review_receipt.1",
        "structural_check": "passed",
        "semantic_verdict": "human_annotations_only_not_independently_verified",
        "source_sha256": worksheet.source_sha256,
        "capture_sha256": worksheet.capture_sha256,
        "operation_sha256": worksheet.operation_sha256,
        "worksheet_sha256": fingerprint(review),
        "timestamp_tolerance_s": worksheet.timestamp_tolerance_s,
        "claim_counts_by_stage": {
            stage: {
                v: sum(
                    c.verdict == v for c in worksheet.claims if c.id.startswith(prefix)
                )
                for v in ("supported", "contradicted", "uncertain")
            }
            for stage, prefix in (("summary", "summary:"), ("events", "event:"))
        },
        "claim_counts": {
            v: sum(c.verdict == v for c in worksheet.claims)
            for v in ("supported", "contradicted", "uncertain")
        },
        "timestamp_counts": {
            v: sum(c.timestamp_verdict == v for c in worksheet.claims)
            for v in ("accurate", "inaccurate", "not_applicable", "uncertain")
        },
        "reference_event_count": len(worksheet.reference_events),
        "matched_reference_event_count": sum(
            bool(e.matched_claim_ids) for e in worksheet.reference_events
        ),
        "qualified": False,
        "comparative_statistics": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--operation", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(
        private_read(args.capture),
        private_read(args.operation),
        json.loads(private_read(args.review)),
    )
    with args.output.open("x") as out:
        json.dump(result, out, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
