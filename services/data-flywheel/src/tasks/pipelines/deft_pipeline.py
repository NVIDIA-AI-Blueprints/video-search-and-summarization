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

"""The DEFT-loop pipeline: one 8-stage DAG, one subclass per job type.

Builds the DEFT flywheel DAG: select data → request a Storage-Backend snapshot →
DEFT train → offline eval gate → register → hand to promotion → wait for the
promotion to terminate → finalize. No NIM spin-up / ICL / LLM judge (those are
NMP/LLM-only). The stages drive the DEFT backend, Eval Harness, MLflow, and
Promotion Controller through the neutral seam.

The ``wait_for_promotion`` stage blocks the chain until the promotion reaches a
terminal state. Because the parent dispatcher blocks on the whole chain via a
concurrency-1 ``parent_queue`` slot, that hold serializes entire flywheel runs:
no new run of any type starts until the current model finishes rolling out.

``AOIPipeline`` and ``ITSPipeline`` declare a ``job_type`` and inherit this DAG
verbatim; they buy nothing at runtime. Every AOI-vs-ITS divergence is resolved
downstream from ``use_case`` (evaluator registry, storage endpoints, serving
control plane, promotion policy), never from ``job_type``. The split exists so
routing is one class per job type rather than one class claiming two, and so a
use-case-specific stage can later be added by overriding ``build_workflow`` on
one leaf instead of branching on ``job_type`` inside a shared chain body.
"""

from typing import Any, ClassVar

from celery import chain

from src.job_types import JobType
from src.tasks.pipelines.base import BasePipeline


class DeftPipeline(BasePipeline):
    """Shared 8-stage DEFT DAG. Subclasses add ONLY a job type.

    Stages:
    1. initialize the DEFT run
    2. select data + request an immutable snapshot (Storage Backend)
    3. DEFT fine-tune on the snapshot
    4. offline KPI eval gate (baseline vs candidate)
    5. mint the serving version (Mongo counter) + record the candidate in
       MLflow best-effort, with snapshot lineage
    6. start promotion (shadow → canary → full via the Promotion Controller)
    7. wait for the promotion to reach a terminal state (blocks the chain,
       serializing runs — see the module docstring)
    8. finalize
    """

    #: The one job type this pipeline serves. Deliberately UNSET here so the
    #: base is not routable: ``DeftPipeline(db).get_supported_job_types()``
    #: raises rather than quietly answering for the whole family.
    job_type: ClassVar[JobType]

    def get_supported_job_types(self) -> list[str]:
        """The single job type this pipeline serves."""
        return [self.job_type.value]

    def build_workflow(
        self,
        workload_id: str,
        flywheel_run_id: str,
        client_id: str,
        job_type: str,
        job_config: Any,
        data_split_config: Any = None,
    ) -> chain:
        """Build the Celery workflow chain for the DEFT pipeline.

        ``data_split_config`` is unused for DEFT (it trains from an immutable
        object-store snapshot, not a DFW train/val/eval split).
        """
        # Imported here to avoid circular imports (mirrors the other pipelines).
        from src.tasks.deft_tasks import (
            finalize_aoi_run,
            initialize_aoi_workflow,
            register_candidate,
            run_deft_training,
            run_offline_eval_gate,
            select_and_snapshot,
            start_promotion,
            wait_for_promotion,
        )

        self.validate_config(job_type, job_config)

        workflow = chain(
            initialize_aoi_workflow.s(
                workload_id=workload_id,
                flywheel_run_id=flywheel_run_id,
                client_id=client_id,
                job_type=job_type,
                job_config=job_config.model_dump(),
            ),
            select_and_snapshot.s(),
            run_deft_training.s(),
            run_offline_eval_gate.s(),
            register_candidate.s(),
            start_promotion.s(),
            # Blocks until the promotion terminates — holds the concurrency-1
            # parent slot for the whole rollout, serializing flywheel runs.
            wait_for_promotion.s(),
            finalize_aoi_run.s(),
        )
        return workflow
