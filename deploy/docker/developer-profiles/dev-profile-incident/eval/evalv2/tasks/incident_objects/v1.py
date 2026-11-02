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

"""The only bridge from the incident-and-objects task to V1: its pure scoring rules, store and aggregators.

V2 scores incident fields and objects with V1's own functions so the two can never drift apart (continuity with
Stage 3): field rules (``eval_gt.compare_incident_fields``), the Hungarian assignment and threshold
(``matching.solve_assignment``), the item texts, exact matches, holder resolution and P/R/F1, the contract policy,
and the database-free store that coerces and orders rows as V1 did. What V2 replaces is only the I/O around them:
similarity and judge scores come from injected providers that record their results, so scoring can be repeated
offline. Nothing here is edited V1 code; the frozen-V1 guard checks that.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parents[3]
for _path in (EVAL_DIR / "scripts", EVAL_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from eval_experiment import frozen_store  # noqa: E402
from eval_run import EMPTY_PREDICTION, persist_prediction  # noqa: E402
from eval_run_lib import classify_outcome  # noqa: E402

import contract as v1_contract  # noqa: E402
import eval_gt  # noqa: E402
import experiment_report  # noqa: E402
import extraction_stats  # noqa: E402
import matching  # noqa: E402
import methodology  # noqa: E402
import severity_metrics  # noqa: E402
from experiment_runner import P1_TOKENS_CEILING  # noqa: E402, F401  (re-exported)
from incident_report import INCIDENT_TYPES, canonical_incident_type  # noqa: E402, F401  (re-exported)
from offline_store import OfflineEvalStore  # noqa: E402

KINDS = ("entities", "instruments", "assets")
ID_FIELD = {"entities": "entity_id", "instruments": "instrument_id", "assets": "asset_id"}
MODEL_LIST = {"entities": "list_incident_entities", "instruments": "list_incident_instruments",
              "assets": "list_incident_assets"}
GT_LIST = {"entities": "list_gt_entities", "instruments": "list_gt_instruments", "assets": "list_gt_assets"}
TEXT = {"entities": matching._entity_text, "instruments": matching._named_text, "assets": matching._named_text}
MATCH_THRESHOLD: float = methodology.MATCH_THRESHOLD
PASS_SCORE: float = methodology.DESCRIPTION_PASS_SCORE
METHODOLOGY_VERSION: str = methodology.METHODOLOGY_VERSION
NO_MODEL_OUTPUT = extraction_stats.NO_MODEL_OUTPUT

compare_incident_fields = eval_gt.compare_incident_fields
exact_match = eval_gt._exact_match
resolve_holder = eval_gt.resolve_holder
prf1 = eval_gt.prf1
solve_assignment = matching.solve_assignment
cosine_matrix = matching._cosine_similarity_matrix
evaluate_policy = v1_contract.evaluate_policy
field_intervals = extraction_stats.field_intervals
pooled_items = experiment_report.items
severity_summary = severity_metrics.summarise


def empty_prediction() -> dict[str, Any]:
    return {key: (dict(value) if isinstance(value, dict) else list(value)) for key, value in EMPTY_PREDICTION.items()}


def gt_store(sheets: dict[str, list[dict[str, Any]]]) -> OfflineEvalStore:
    """A store holding the frozen ground-truth sheets through V1's ``write_gt`` path (``frozen_store``)."""
    return frozen_store(sheets)


def put_prediction(store: OfflineEvalStore, incident_id: str, run_id: str, prediction: dict[str, Any]) -> None:
    """Persist one prediction exactly as V1 did (``persist_prediction``: integer coercion, column types)."""
    persist_prediction(store, incident_id, run_id, prediction)


def outcome(p1_raw: dict[str, Any], validation: dict[str, Any]) -> str:
    """V1's outcome label: ``scored``, ``contract_invalid``, ``parse_failure`` or a no-output failure kind."""
    return str(classify_outcome(p1_raw, validation))


def has_model_output(video: dict[str, Any]) -> bool:
    return bool(extraction_stats.has_model_output(video))


def is_out_of_taxonomy(video: dict[str, Any]) -> bool:
    return bool(extraction_stats.is_out_of_taxonomy(video))
