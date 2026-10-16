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

"""Golden-master parity: the staged pipeline must reproduce the old runner.

The fixture was captured from the pre-refactor ``run_evaluation`` by
``gen_parity_baseline.py``. These tests drive the refactored
``flows.pipeline.run_retrieve`` + ``run_score`` through the SAME fake backend
and decomposer and assert the scored output is unchanged (wall-clock fields
excluded). If this fails, the refactor changed behaviour -- not the other way
round.

    python3 -m pytest scripts/tests/test_pipeline_parity.py
"""

# isort: skip_file

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/tests -> scripts, so `import flows` / `import run_eval_flows` work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _parity_harness as h
import flows
from flows import pipeline
from flows.artifacts import IngestManifest, RunConfig

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "parity_baseline.json"


def _run_refactored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setitem(flows.DATASETS, h.DATASET, {"": f"{h.DATASET}/dataset.json"})
    h.build_dataset(tmp_path)
    cfg = RunConfig(
        endpoint="http://fake:8000",
        data_dir=tmp_path,
        dataset=h.DATASET,
        subset=h.SUBSET,
        concurrency=1,
        vst_url=None,
        tolerate_unanswered=1,
        use_dataset_decompositions=True,
        output_file=str(tmp_path / "out.json"),
        run_name="parity",
    )
    manifest = IngestManifest(
        ingest_description={"flow": "fake-ingest", "ingest_proof": "none"}
    )
    artifact = pipeline.run_retrieve(
        cfg, h.make_backend(), h.make_decomposer(), None, manifest
    )
    result = pipeline.run_score(cfg, artifact)
    return h.strip_nondeterministic(result.to_output())


def test_staged_pipeline_reproduces_the_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = json.loads(FIXTURE.read_text())
    assert _run_refactored(tmp_path, monkeypatch) == expected


def test_retrieve_produces_no_scores_and_score_produces_no_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam is real: the artifact holds raw hits, not metric numbers."""
    monkeypatch.setitem(flows.DATASETS, h.DATASET, {"": f"{h.DATASET}/dataset.json"})
    h.build_dataset(tmp_path)
    cfg = RunConfig(
        endpoint="http://fake:8000",
        data_dir=tmp_path,
        dataset=h.DATASET,
        subset=h.SUBSET,
        concurrency=1,
        vst_url=None,
        tolerate_unanswered=1,
        use_dataset_decompositions=True,
        output_file=str(tmp_path / "o.json"),
        run_name="parity",
    )
    artifact = pipeline.run_retrieve(
        cfg,
        h.make_backend(),
        h.make_decomposer(),
        None,
        IngestManifest(ingest_description={"flow": "fake-ingest"}),
    )
    # The artifact carries raw hits and a status, but no metric keys.
    answered = [r for r in artifact.queries if r.status == "answered"]
    assert answered, "expected some answered queries"
    for record in answered:
        assert record.hits is not None
        assert "average_precision" not in vars(record)
        assert not any("precision" in hit for hit in record.hits)
    # The unanswered query is recorded, not scored, and not silently zero.
    assert any(r.status == "unanswerable" for r in artifact.queries)
    assert len(artifact.unanswered) == 1
