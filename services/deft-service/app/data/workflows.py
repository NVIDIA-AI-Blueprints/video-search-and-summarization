"""Static workflow catalog — mirrors the WorkflowInfo examples in
openapi.json. A real deployment would load these from the workflow
definitions the skill bank's `tao-run-*` skills already encode
(pipeline-and-state.md's Pipeline steps for aoi_active_learning, etc.);
here they're inlined so the service has something real to serve and to
drive the run simulator's step list.
"""

from __future__ import annotations

from app.schemas.workflow_models import WorkflowInfo

WORKFLOWS: dict[str, WorkflowInfo] = {
    "tmm_only": WorkflowInfo(
        name="tmm_only",
        description="Iterative mine-train-evaluate pipeline for object detection",
        pre_loop=["initial_eval"],
        loop=["data_mining", "train", "evaluate"],
        post_loop=["results"],
        relevant_config=["model", "data_pool", "kpi", "training", "mining"],
    ),
    "aoi_active_learning": WorkflowInfo(
        name="aoi_active_learning",
        description="AOI active-learning workflow with anomaly generation, fine-tuning, and evaluation.",
        pre_loop=["validate_training_pool", "baseline_eval"],
        loop=["rca", "routing", "anomalygen", "data_mining", "train", "evaluate"],
        post_loop=["register_candidate_model", "publish_results"],
        relevant_config=["model", "data_pool", "kpi", "training", "pipeline"],
    ),
}
