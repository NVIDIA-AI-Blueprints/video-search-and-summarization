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

"""Shared sampling and evidence-bound manual review, with no live GPU claims."""

import hashlib
import json
import os
from copy import deepcopy

import pytest
from test_workflow_qualification import workflow as workflow

from benchmarks.e2e.config import fingerprint
from benchmarks.workflow.operation import operation
from benchmarks.workflow.quality import capture_output, check, private_read


def admission(profile):
    return {
        "source_sha256": profile.video_sha256,
        "duration_s": 2.0,
        "duration_quality": "video",
        "width": 64,
        "height": 64,
        "selected_stream": "v:0",
        "oracle_sha256": "1" * 64,
    }


@pytest.mark.parametrize(
    "drift",
    [
        None,
        {"source_sha256": "0" * 64},
        {"duration_s": float("nan")},
        {"duration_s": 1.8},
        {"width": True},
        {"selected_stream": "v:1"},
        {"duration_quality": "guessed"},
        {"oracle_sha256": "unknown"},
    ],
)
async def test_shared_sampling_rejects_bad_receipt_before_service_work(workflow, drift):
    profile, fixture, _ = workflow
    profile = profile.model_copy(
        update={"sampling_contract": "shared_duration_grid.v1", "captioning": "gpu_vlm"}
    )
    result = await operation(
        profile, 1, admission=None if drift is None else admission(profile) | drift
    )
    assert (
        result["status"] == "failed" and result["failure_stage"] == "sampling_admission"
    )
    assert not fixture.assets and fixture.llm_calls == fixture.frame_calls == 0


@pytest.mark.parametrize("unknown", [False, True])
async def test_shared_duration_drives_real_planner_and_keeps_native_lifecycle(
    workflow, monkeypatch, unknown
):
    """Simulated GPU metadata, real OpenCV decode/planner/HTTP pipeline."""
    import numpy as np

    from vss.video_processing.decoders.opencv import OpenCVBackend
    from vss.video_processing.decoders.pynv import PyNvBackend

    cv2 = pytest.importorskip("cv2")
    profile, fixture, _ = workflow
    writer = cv2.VideoWriter(
        str(profile.video), cv2.VideoWriter.fourcc(*"mp4v"), 5, (64, 64)
    )
    assert writer.isOpened()
    for i in range(10):
        writer.write(np.full((64, 64, 3), i * 20, dtype=np.uint8))
    writer.release()
    original = OpenCVBackend.open
    sessions = []

    def open_native(self, path, options):
        session = original(self, path, options)
        sessions.append(session)
        if unknown:
            probe = session.probe
            session.probe = lambda: probe().model_copy(
                update={"duration_s": None, "duration_quality": "unknown"}
            )
        return session

    monkeypatch.setattr(PyNvBackend if unknown else OpenCVBackend, "open", open_native)
    profile = profile.model_copy(
        update={
            "captioning": "gpu_vlm" if unknown else "cpu_vlm",
            "store_backend": "memory",
            "sampling_contract": "shared_duration_grid.v1",
            "video_sha256": hashlib.sha256(profile.video.read_bytes()).hexdigest(),
        }
    )
    result = await operation(profile, 1, admission=admission(profile))
    assert result["status"] == "completed_diagnostic", result
    selected = result["frame_provider_observation"]["selected"][0]
    assert selected["end_ns"] == 2_000_000_000
    assert selected["pts_ns"] == [
        0,
        400_000_000,
        600_000_000,
        800_000_000,
        1_000_000_000,
        1_400_000_000,
        1_600_000_000,
        1_800_000_000,
    ]
    assert len(sessions) == 2 and all(s.closed for s in sessions)
    facts = result["sampling"]["native_probe_observations"]
    assert (
        all(f["duration_s"] is None for f in facts)
        if unknown
        else all(f["duration_s"] == 2 for f in facts)
    )
    assert result["sampling"]["default_gpu_probe_fixed"] is False
    assert str(profile.video) not in json.dumps(result)


async def test_shared_sampling_geometry_failure_closes_native_probe(
    workflow, monkeypatch
):
    from types import SimpleNamespace

    from vss.video_processing import MediaFacts
    from vss.video_processing.decoders.pynv import PyNvBackend

    profile, fixture, _ = workflow
    session = SimpleNamespace(closed=False)
    session.probe = lambda: MediaFacts(source_id="test", width=160, height=96)
    session.close = lambda: setattr(session, "closed", True)
    monkeypatch.setattr(PyNvBackend, "open", lambda *a: session)
    profile = profile.model_copy(
        update={
            "captioning": "gpu_vlm",
            "sampling_contract": "shared_duration_grid.v1",
            "store_backend": "memory",
        }
    )
    result = await operation(profile, 1, admission=admission(profile))
    assert (
        result["status"] == "failed" and fixture.frame_calls == fixture.llm_calls == 0
    )
    assert session.closed and result["cleanup"]["local_job_closed"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
@pytest.mark.parametrize("arm", ["normal", "library"])
async def test_final_private_capture_is_bound_and_never_published(
    workflow, tmp_path, arm
):
    from vss.storage import InMemoryStorageAdapter

    profile, _, _ = workflow
    path = tmp_path / "output-private.json"
    result = await operation(
        profile.model_copy(update={"arm": arm, "output_capture": path}),
        1,
        store_factory=InMemoryStorageAdapter,
    )
    assert result["status"] == "completed_diagnostic", result
    raw = private_read(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert b"PRIVATE_" in raw and "PRIVATE_" not in json.dumps(result)
    assert hashlib.sha256(raw).hexdigest() == result["private_output_capture"]["sha256"]
    assert result["private_output_capture"]["timing"] == "after_result_boundary"


@pytest.fixture
def review_case(workflow, tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX private workflow diagnostics")
    profile = workflow[0].model_copy(
        update={"output_capture": tmp_path / "output.json"}
    )
    payload = {
        "video_summary": "A car moves.",
        "events": [{"description": "A car moves.", "start_time": 0, "end_time": 1}],
    }
    body = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    capture = capture_output(body, profile)
    raw = private_read(profile.output_capture)
    op = {
        "status": "completed_diagnostic",
        "private_output_capture": capture,
        "summary_sha256": fingerprint(payload["video_summary"]),
        "events_sha256": fingerprint(payload["events"]),
        "cleanup": {"store_closed": True},
    }
    report = {
        "status": "completed_diagnostic",
        "source_sha256": profile.video_sha256,
        "config_sha256": fingerprint(profile.model_dump(mode="json")),
        "operation": op,
    }
    operation_raw = json.dumps(report).encode()
    claim = {
        "text_sha256": fingerprint("A car moves."),
        "verdict": "supported",
        "evidence": [{"start_s": 0, "end_s": 1}],
        "timestamp_verdict": "accurate",
    }
    review = {
        "source_sha256": profile.video_sha256,
        "capture_sha256": capture["sha256"],
        "operation_sha256": hashlib.sha256(operation_raw).hexdigest(),
        "reviewer": "fixture reviewer",
        "review_method": "full_video_playback",
        "timestamp_tolerance_s": 0.1,
        "watched": [{"start_s": 0, "end_s": 2}],
        "summary_fully_decomposed": True,
        "reference_inventory_complete": True,
        "claims": [
            claim
            | {"id": "summary:0", "summary_start_char": 0, "summary_end_char": 12},
            claim | {"id": "event:0"},
        ],
        "reference_events": [
            {
                "id": "reference:0",
                "source_interval": {"start_s": 0, "end_s": 1},
                "matched_claim_ids": ["event:0"],
            }
        ],
    }
    return raw, operation_raw, review


def test_quality_receipt_counts_human_annotations_without_qualification(review_case):
    result = check(*review_case)
    assert result["claim_counts"]["supported"] == 2
    assert result["matched_reference_event_count"] == 1
    assert result["qualified"] is False and result["comparative_statistics"] is None
    assert (
        result["semantic_verdict"]
        == "human_annotations_only_not_independently_verified"
    )
    assert "A car" not in json.dumps(result) and "fixture reviewer" not in json.dumps(
        result
    )


@pytest.mark.parametrize(
    "drift",
    [
        "source",
        "capture",
        "operation",
        "claim",
        "summary",
        "missing_event",
        "duplicate",
        "gap",
        "outside",
        "incomplete",
        "unknown_match",
        "unsupported_match",
    ],
)
def test_incomplete_or_unbound_reviews_rejected(review_case, drift):
    raw, op, review = review_case
    review = deepcopy(review)
    if drift in {"source", "capture", "operation"}:
        review[
            {
                "source": "source_sha256",
                "capture": "capture_sha256",
                "operation": "operation_sha256",
            }[drift]
        ] = "0" * 64
    elif drift == "claim":
        review["claims"][1]["text_sha256"] = "0" * 64
    elif drift == "summary":
        review["claims"][0]["summary_end_char"] = 4
    elif drift == "missing_event":
        review["claims"].pop()
    elif drift == "duplicate":
        review["claims"].append(review["claims"][1])
    elif drift == "gap":
        review["watched"][0]["start_s"] = 0.1
    elif drift == "outside":
        review["claims"][0]["evidence"][0]["end_s"] = 3
    elif drift == "incomplete":
        review["reference_inventory_complete"] = False
    elif drift == "unknown_match":
        review["reference_events"][0]["matched_claim_ids"] = ["event:999"]
    elif drift == "unsupported_match":
        review["claims"][1]["verdict"] = "uncertain"
    with pytest.raises(ValueError):
        check(raw, op, review)


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_private_reader_rejects_fifo_permissions_and_symlinks(tmp_path):
    import os

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError):
        private_read(fifo)
    path = tmp_path / "public.json"
    path.write_text("{}")
    path.chmod(0o644)
    with pytest.raises(ValueError):
        private_read(path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        private_read(link)


@pytest.mark.parametrize(
    "drift", ["bare", "missing_source", "missing_config", "failed", "cleanup"]
)
def test_quality_requires_complete_cli_identity_and_success(review_case, drift):
    raw, op_raw, review = review_case
    report = json.loads(op_raw)
    if drift == "bare":
        report = report["operation"]
    elif drift == "missing_source":
        del report["source_sha256"]
    elif drift == "missing_config":
        del report["config_sha256"]
    elif drift == "failed":
        report["status"] = "failed"
    else:
        report["operation"]["cleanup"]["store_closed"] = False
    op_raw = json.dumps(report).encode()
    review["operation_sha256"] = hashlib.sha256(op_raw).hexdigest()
    with pytest.raises(ValueError):
        check(raw, op_raw, review)


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
@pytest.mark.parametrize("failure", ["collision", "symlink", "oversize"])
def test_private_output_capture_refuses_reuse_and_oversize(workflow, tmp_path, failure):
    profile = workflow[0].model_copy(
        update={"output_capture": tmp_path / "output.json"}
    )
    payload = {"video_summary": "x", "events": []}
    if failure == "collision":
        profile.output_capture.write_bytes(b"original")
    elif failure == "symlink":
        original = tmp_path / "original"
        original.write_bytes(b"original")
        profile.output_capture.symlink_to(original)
    else:
        payload["video_summary"] = "x" * (2 * 1024**2)
    with pytest.raises((ValueError, OSError)):
        capture_output(
            {"choices": [{"message": {"content": json.dumps(payload)}}]}, profile
        )
    if failure != "oversize":
        assert profile.output_capture.read_bytes() == b"original"


@pytest.mark.parametrize(
    "start,end,label",
    [
        (0, 1, "bounded_positive_interval"),
        (1, 1, "instantaneous"),
        (1, 0, "reversed"),
        (-1, 1, "outside_source"),
        (0, 3, "outside_source"),
        ("0", 1, "unsupported"),
        (True, 1, "unsupported"),
        (None, 1, "unsupported"),
    ],
)
def test_descriptive_timestamp_precheck_is_bound_and_never_a_factual_grade(
    review_case, start, end, label
):
    from benchmarks.workflow.quality import timestamp_precheck

    cap_raw, op_raw, _ = review_case
    cap = json.loads(cap_raw)
    op = json.loads(op_raw)
    cap["payload"]["events"][0].update(start_time=start, end_time=end)
    cap_raw = json.dumps(cap, allow_nan=False).encode()
    op["operation"]["private_output_capture"]["sha256"] = hashlib.sha256(
        cap_raw
    ).hexdigest()
    op["operation"]["events_sha256"] = fingerprint(cap["payload"]["events"])
    result = timestamp_precheck(cap_raw, json.dumps(op).encode())
    assert result["indices"][label] == [0]
    assert result["semantic_quality"] == "not_reviewed" and result["qualified"] is False
    assert "A car" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["source", "profile", "hash", "truthy_cleanup", "nan"])
def test_timestamp_precheck_rejects_unbound_or_malformed_evidence(review_case, mode):
    from benchmarks.workflow.quality import timestamp_precheck

    cap_raw, op_raw, _ = review_case
    cap = json.loads(cap_raw)
    op = json.loads(op_raw)
    if mode == "source":
        cap["source_sha256"] = "PRIVATE_METADATA"
        op["source_sha256"] = "PRIVATE_METADATA"
    elif mode == "profile":
        op["config_sha256"] = "0" * 64
    elif mode == "hash":
        op["operation"]["events_sha256"] = "0" * 64
    elif mode == "truthy_cleanup":
        op["operation"]["cleanup"]["store_closed"] = "true"
    else:
        cap["payload"]["events"][0]["start_time"] = float("nan")
    cap_raw = json.dumps(cap).encode()
    op["operation"]["private_output_capture"]["sha256"] = hashlib.sha256(
        cap_raw
    ).hexdigest()
    with pytest.raises(ValueError):
        timestamp_precheck(cap_raw, json.dumps(op).encode())
