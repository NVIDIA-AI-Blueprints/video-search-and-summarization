# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Round-trip and schema-version tests for the on-disk artifacts.

python3 -m pytest scripts/tests/test_artifacts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows.artifacts as A


def _manifest() -> A.IngestManifest:
    return A.IngestManifest(
        ingest_description={"flow": "vst-direct", "ingest_proof": "none"},
        upload_stats={"total_uploads": 2, "successful": 2, "failed": 0},
        readiness={"ready": True, "attempts": 1},
        index_probe={"checked": True, "covered": True},
        cleared=None,
    )


def _artifact() -> A.RetrievalArtifact:
    record = A.QueryRecord(
        index=0,
        query="a person",
        status="answered",
        plan={"path": "embed", "query": "a person"},
        decomposition={"query": "a person", "attributes": []},
        decompose_s=0.5,
        search_latency_s=1.0,
        timings=None,
        hits=[
            {
                "video_name": "a.mp4",
                "similarity": 0.9,
                "_verification_source": "verification",
                "verification": {"result": "confirmed", "criteria_met": {}},
            }
        ],
    )
    unanswered_record = A.QueryRecord(
        index=1, query="times out", status="unanswerable", error="timeout"
    )
    return A.RetrievalArtifact(
        dataset="warehouse",
        subset="",
        data_dir="/data",
        concurrency=1,
        described={"backend": "cli", "routing": "fixed"},
        queries=[record, unanswered_record],
        unanswered=[{"index": 1, "query": "times out", "reason": "timeout"}],
        executed_plans=[{"path": "embed"}],
        wall_clock_s=1.5,
        sources_seen=["verification"],
        verdicts={"confirmed": 1},
        inventory={"stable": None},
        ingest=_manifest(),
        tolerate_unanswered=1,
        run_id="warehouse_default_20250101_000000",
    )


def test_ingest_manifest_round_trip(tmp_path: Path) -> None:
    path = A.write_ingest_manifest(tmp_path / "m.json", _manifest())
    loaded = A.read_ingest_manifest(path)
    assert loaded.to_dict() == _manifest().to_dict()


def test_retrieval_artifact_round_trip_preserves_raw_hits(tmp_path: Path) -> None:
    artifact = _artifact()
    path = A.write_retrieval_artifact(tmp_path / "a.json", artifact)
    loaded = A.read_retrieval_artifact(path)
    assert loaded.to_dict() == artifact.to_dict()
    # The seam survives serialization: raw hits, not metric numbers.
    assert loaded.queries[0].hits[0]["_verification_source"] == "verification"
    assert "average_precision" not in loaded.queries[0].hits[0]
    assert loaded.queries[1].status == "unanswerable"


def test_artifact_carries_schema_version(tmp_path: Path) -> None:
    path = A.write_retrieval_artifact(tmp_path / "a.json", _artifact())
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == A.SCHEMA_VERSION


def test_migrate_rejects_a_newer_schema() -> None:
    with pytest.raises(SystemExit, match="newer than this runner"):
        A.migrate({"schema_version": A.SCHEMA_VERSION + 1}, "retrieval artifact")


def test_migrate_accepts_current_and_treats_missing_as_v1() -> None:
    A.migrate({"schema_version": A.SCHEMA_VERSION}, "x")
    A.migrate({}, "x")  # no version -> treated as v1


def test_read_missing_artifact_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not found"):
        A.read_retrieval_artifact(tmp_path / "nope.json")


def test_read_invalid_json_is_a_clean_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    with pytest.raises(SystemExit, match="not valid JSON"):
        A.read_retrieval_artifact(bad)


def test_malformed_artifact_names_the_missing_field(tmp_path: Path) -> None:
    """A hand-edited artifact fails with a clear 'missing field' error, not a KeyError."""
    bad = tmp_path / "malformed.json"
    bad.write_text(
        json.dumps({"schema_version": 1, "dataset": "x"})
    )  # missing described, queries, ...
    with pytest.raises(SystemExit, match="missing required field"):
        A.read_retrieval_artifact(bad)
