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

"""Shared harness for the golden-master parity test.

The refactor of ``run_eval_flows.py`` into ``flows/pipeline.py`` stages must not
change the scored output. To prove that, one fixed scenario is run through a
fake query backend and a fake live decomposer, and the resulting output dict is
compared field for field.

``gen_parity_baseline.py`` runs the CURRENT (pre-refactor) ``run_evaluation``
and writes the golden fixture. ``test_pipeline_parity.py`` runs the REFACTORED
pipeline against the same fakes and asserts it reproduces the fixture. Because
both sides drive the *same* fakes, the comparison isolates the refactor itself.

Two output fields are wall-clock and therefore excluded from comparison:
``summary.timestamp`` and the ``throughput`` block (``wall_clock_s`` and the
derived QPS numbers). Everything else is deterministic given the fixed fakes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# scripts/tests -> scripts, so `import flows` and `import run_eval_flows` work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows

DATASET = "parityds"
SUBSET = ""

#: Ground truth, one entry per query. ``q4`` is the unanswered query: its search
#: raises, so it is excluded from scoring and counted under ``unanswered``.
GROUND_TRUTH: dict[str, Any] = {
    "queries": {
        "person with box": [
            {
                "video_name": "warehouse_sample",
                "start_time": "2025-01-01T00:00:05Z",
                "end_time": "2025-01-01T00:00:10Z",
            },
        ],
        "forklift moving": [
            {
                "video_name": "warehouse_sample",
                "start_time": "2025-01-01T00:00:15Z",
                "end_time": "2025-01-01T00:00:20Z",
            },
            {
                "video_name": "other_video",
                "start_time": "2025-01-01T00:01:00Z",
                "end_time": "2025-01-01T00:01:05Z",
            },
        ],
        "a query with no hits": [
            {
                "video_name": "warehouse_sample",
                "start_time": "2025-01-01T00:00:25Z",
                "end_time": "2025-01-01T00:00:30Z",
            },
        ],
        "query that times out": [
            {
                "video_name": "warehouse_sample",
                "start_time": "2025-01-01T00:00:35Z",
                "end_time": "2025-01-01T00:00:40Z",
            },
        ],
    }
}


def _hit(
    video: str, start: str, end: str, sim: float, verdict: str | None
) -> dict[str, Any]:
    """A raw wire-format hit (new-flow spelling), as a query backend returns it."""
    hit: dict[str, Any] = {
        "video_name": video,
        "description": "a clip",
        "start_time": start,
        "end_time": end,
        "sensor_id": "sensor-1",
        "screenshot_url": "https://vst/shot.jpg",
        "similarity": sim,
        "object_ids": [],
    }
    if verdict is not None:
        hit["verification"] = {"result": verdict, "criteria_met": {}}
    return hit


#: Raw results each query returns. ``q1`` mixes a confirmed true positive with a
#: rejected false positive, so raw and critic-filtered metrics diverge.
RAW_HITS: dict[str, list[dict[str, Any]]] = {
    "person with box": [
        _hit(
            "warehouse_sample",
            "2025-01-01T00:00:05Z",
            "2025-01-01T00:00:10Z",
            0.9,
            "confirmed",
        ),
        _hit(
            "other_video",
            "2025-01-01T00:02:00Z",
            "2025-01-01T00:02:05Z",
            0.4,
            "rejected",
        ),
    ],
    "forklift moving": [
        _hit(
            "warehouse_sample",
            "2025-01-01T00:00:15Z",
            "2025-01-01T00:00:20Z",
            0.8,
            "confirmed",
        ),
    ],
    "a query with no hits": [],
}


class FakeDecomposer:
    """A live decomposer with fixed output, so decomposition metrics are deterministic."""

    model = "fake-model"

    def describe(self) -> dict[str, Any]:
        return {"llm_url": "http://fake-llm", "model": self.model}

    def decompose(
        self, query: str, video_sources: list[str] | None = None
    ) -> tuple[dict[str, Any], float]:
        return (
            {
                "query": f"rewritten: {query}",
                "attributes": ["person wearing a hardhat"],
                "has_action": True,
                "source_type": "video_file",
            },
            0.5,  # fixed decompose_s
        )


class FakeBackend:
    """Stands in for ``CliQueryBackend``: same surface, no subprocess, no network."""

    name = "cli"

    def __init__(self) -> None:
        self.decompositions: dict[str, dict[str, Any]] = {}
        self.executed_plans: list[dict[str, Any]] = []
        self.timings_by_query: dict[str, dict[str, Any]] = {}

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "vss_cmd": "fake-vss",
            "routing": "fixed",
            "search_path": "embed",
            "top_k": 5,
            "min_cosine_similarity": 0.0,
            "source_type": "video_file",
            "attributes": [],
            "merge_adjacent": False,
            "decompositions": len(self.decompositions),
            "pass_original_query": True,
            "critic_eval_count": None,
        }

    def plan_for_query(self, query: str) -> dict[str, Any]:
        return flows.plan_for(
            query, self.decompositions.get(query), default_path="embed"
        )

    def search(self, query: str) -> tuple[list[dict[str, Any]], float]:
        if query == "query that times out":
            raise flows.QueryUnanswerableError("simulated query timeout")
        plan = self.plan_for_query(query)
        self.executed_plans.append(plan)
        return list(RAW_HITS.get(query, [])), 1.0  # fixed latency


def build_dataset(data_dir: Path) -> None:
    """Write the dataset file where ``flows.load_dataset_file`` expects it."""
    path = data_dir / DATASET / "dataset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(GROUND_TRUTH))


def register_dataset() -> None:
    """Point the dataset registry at the parity dataset (caller restores it)."""
    flows.DATASETS[DATASET] = {"": f"{DATASET}/dataset.json"}


def make_backend() -> FakeBackend:
    return FakeBackend()


def make_decomposer() -> FakeDecomposer:
    return FakeDecomposer()


def strip_nondeterministic(output: dict[str, Any]) -> dict[str, Any]:
    """Drop wall-clock fields so the golden comparison is deterministic."""
    out = json.loads(json.dumps(output))  # deep copy of plain-JSON data
    out.get("summary", {}).pop("timestamp", None)
    out.get("summary", {}).pop("throughput", None)
    return out
