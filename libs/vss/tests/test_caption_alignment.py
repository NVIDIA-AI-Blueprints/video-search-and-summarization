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

"""Provenance consistency is distinct from globally bounded/factual timestamps."""

import hashlib
import json

import pytest

from benchmarks.e2e.config import fingerprint
from benchmarks.workflow.quality import caption_alignment_precheck


def material(events, *, corrupt=None):
    chunks = [
        {
            "chunk": {
                "sourceId": "source",
                "chunkIdx": i,
                "start_pts": i * 30 * 10**9,
                "end_pts": (i + 1) * 30 * 10**9,
            },
            "vlm_response": "PRIVATE",
        }
        for i in range(2)
    ]
    identity = [
        {
            "index": i,
            "start": i * 30 * 10**9,
            "end": (i + 1) * 30 * 10**9,
            "content": "PRIVATE",
        }
        for i in range(2)
    ]
    task = {
        "source_sha256": "1" * 64,
        "duration_s": 60,
        "chunk_duration_s": 30,
        "vlm_model": "test",
        "caption_prompt_sha256": "2" * 64,
        "caption_max_tokens": 1024,
    }
    capsule = {
        "schema_version": "private_caption_capsule.1",
        "task": task,
        "caption_sha256": fingerprint(identity),
        "chunks": chunks,
    }
    if corrupt:
        corrupt(capsule)
    cap_raw = json.dumps(capsule).encode()
    payload = {"video_summary": "PRIVATE", "events": events}
    capture = {
        "schema_version": "private_workflow_output.1",
        "source_sha256": "1" * 64,
        "profile_sha256": "3" * 64,
        "duration_s": 60,
        "payload": payload,
    }
    raw = json.dumps(capture).encode()
    operation = {
        "status": "completed_diagnostic",
        "source_sha256": "1" * 64,
        "config_sha256": "3" * 64,
        "module_comparison_contract": task,
        "operation": {
            "status": "completed_diagnostic",
            "cleanup": {"done": True},
            "private_output_capture": {"sha256": hashlib.sha256(raw).hexdigest()},
            "private_caption_capture": {"sha256": hashlib.sha256(cap_raw).hexdigest()},
            "caption_sha256": fingerprint(identity),
            "summary_sha256": fingerprint("PRIVATE"),
            "events_sha256": fingerprint(events),
        },
    }
    return raw, json.dumps(operation).encode(), cap_raw


def event(start, end, *indices):
    return {
        "description": "PRIVATE",
        "start_time": start,
        "end_time": end,
        "evidence": [{"source_id": "source", "chunk_idx": i} for i in indices],
    }


def test_disjoint_is_not_hidden_by_global_bounds_and_merged_spans_are_not_overclaimed():
    result = caption_alignment_precheck(
        *material(
            [
                event(0, 10, 1),
                event(0, 45, 0, 1, 1),
                event(0, 30, 1),
                event(30, 60, 1),
                event(0, 10, 9),
                event(0, 0, 0),
                event(0, "NaN", 0),
                event(0, 1),
            ]
        )
    )
    assert result["counts"] == {
        "disjoint_from_all": 2,
        "not_disjoint_from_all": 2,
        "unassessable": 4,
    }
    assert result["events"][1]["overlaps"] == [True, True, True]
    assert result["qualified"] is False and "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("drift", ["source", "content", "capsule_hash", "bool_window"])
def test_caption_identity_and_task_are_bound(drift):
    args = list(material([event(0, 10, 0)]))
    body = json.loads(args[2])
    if drift == "source":
        body["task"]["source_sha256"] = "f" * 64
    elif drift == "bool_window":
        body["chunks"][0]["chunk"]["start_pts"] = False
    else:
        body["chunks"][0]["vlm_response"] = "changed"
    args[2] = json.dumps(body).encode()
    if drift != "capsule_hash":
        op = json.loads(args[1])
        op["operation"]["private_caption_capture"]["sha256"] = hashlib.sha256(
            args[2]
        ).hexdigest()
        args[1] = json.dumps(op).encode()
    with pytest.raises(ValueError):
        caption_alignment_precheck(*args)
