# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Manual smoke runner for the FULL AOI workflow — through the Promotion Controller.

Not a pytest test — a hand-runnable driver exercising the *real* code path a
``/jobs`` (aoi_job) request takes, minus the HTTP + Celery + Mongo layers:

    config build (default + named)
    → initialize → select_and_snapshot → run_deft_training
    → run_offline_eval_gate → register_candidate → start_promotion → finalize
    → Promotion Controller gates: pull → deploy(shadow) → canary → full
      (driven by the online-eval / HITL beats, exactly as in production)

External surfaces are contract-accurate in-process fakes (shared with
``tests/unit/promotion/test_aoi_e2e_promotion.py``): Storage Backend + DEFT via
the provider seam, Model Manager behind the REAL HTTP client, MLflow alias/tag
trail, and an in-memory Mongo double for promotion records.

Run as a module (BACKEND-agnostic; the provider seam is patched):

    uv run python -m tests.e2e_scripts.deft.aoi_orchestration_smoke            # promoted
    uv run python -m tests.e2e_scripts.deft.aoi_orchestration_smoke regression # rolled_back
    uv run python -m tests.e2e_scripts.deft.aoi_orchestration_smoke gate_fail  # offline gate blocks
"""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path

# Import src.config first so `settings` fully initializes before any
# src.backends.deft.* module — under BACKEND=deft the module-level settings load
# imports the deft loader, and importing a deft module first would re-enter it.
import src.config  # noqa: F401  isort:skip

CONFIG_YAML = Path(__file__).resolve().parents[3] / "config" / "config.deft.yaml"


def show_configs() -> None:
    """Prove both config paths (default YAML block + named via the registry)."""
    from src.config import build_job_config, config_type_for, load_deft_settings
    from src.config_renderer import load_rendered_yaml

    _, job_configs = load_deft_settings(load_rendered_yaml(CONFIG_YAML))
    default_cfg = job_configs.get("aoi_job")
    print(
        "[config] default (config.deft.yaml aoi_job_config):",
        type(default_cfg).__name__ if default_cfg else None,
        f"kpi_target={[c.render() for c in default_cfg.eval.kpi_target]}" if default_cfg else "",
    )

    named = build_job_config(
        "aoi_job",
        {
            "use_case": "aoi",
            "model_name": "changenet-aoi",
            "selection": {
                "mode": "query",
                # Required by DeftJobConfig: the /v1/snapshot contract demands
                # selection.created_after. Without it this script dies in
                # show_configs() before reaching the workflow it exists to smoke.
                "filters": {"classes": ["NG"], "created_after": "1970-01-01T00:00:00Z"},
                "limit": 5000,
            },
            "eval": {"kpi_target": [{"metric": "far", "op": "<", "threshold": 0.001}]},
            "deft": {"config": {"training": {"epochs": 50, "num_gpus": 2}}},
        },
    )
    print(
        "[config] named   (src.config.build_job_config):",
        type(named).__name__,
        f"config_type={config_type_for(named)}",
    )


def main() -> int:
    from src.tasks.deft_tasks import AOIStageError
    from tests.unit.promotion.conftest import FakeDB
    from tests.unit.promotion.test_aoi_e2e_promotion import (
        FAILING_METRICS,
        E2EHarness,
        _aoi_samples,
    )

    scenario = sys.argv[1] if len(sys.argv) > 1 else "promoted"
    kwargs = {}
    if scenario == "regression":
        kwargs["online_samples"] = _aoi_samples(200, candidate_correct=False)
    elif scenario == "gate_fail":
        kwargs["metrics"] = FAILING_METRICS
    print(f"→ scenario={scenario}")

    harness = E2EHarness(FakeDB(), **kwargs)

    try:
        with ExitStack() as stack:
            for p in harness.patches():
                stack.enter_context(p)

            show_configs()

            print("\n[1-7] pipeline chain (initialize … start_promotion → finalize)")
            ctx = harness.run_chain()
            # NMP-parity contract: a failed stage never raises out of the
            # chain — the error rides the ctx and the later stages skip, so
            # a gate_fail run reaches here with `error` set and NO promotion.
            if error := ctx.get("error"):
                print(f"\n⛔ run ended — {error}")
                return 1
            print("    snapshot:", ctx["snapshot_id"], "→", ctx["snapshot_uri"])
            print("    deft run:", ctx["deft_run_id"], "metrics:", json.dumps(ctx["eval_metrics"]))
            print("    offline gate:", "PASSED" if ctx.get("gate_passed") else "FAILED")
            print("    promotion:", ctx["promotion_id"], f"state={ctx['promotion_state']}")

            print("[8]  promotion controller (worker + beats simulation)")
            record = harness.drive(ctx["promotion_id"])

            print("\n    Model Manager call sequence:")
            for endpoint, body in harness.mm.calls:
                if body:
                    keys = {
                        k: body[k] for k in ("version", "control", "canary_weight") if k in body
                    }
                    print(f"      {endpoint}  {keys}")
            print("    MLflow alias trail:", harness.mlflow.alias_history)
            print(
                f"\n{'✅' if record.state == 'promoted' else '↩️'} promotion "
                f"{record.promotion_id} ended state={record.state} "
                f"(serving default={harness.mm.default_version})"
            )
    except AOIStageError as e:
        # Defensive only: since the NMP-parity chain contract, stage failures
        # arrive via ctx["error"] (handled above), not as raised exceptions.
        print(f"\n⛔ run ended — {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
