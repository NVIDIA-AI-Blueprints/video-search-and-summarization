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

"""Model resolver for triggers - resolves model references to actual model names.

This is the SYNCHRONOUS version for use in Celery tasks.

Supports multi-NIM jobs where each NIM has its own model_name and customizer target.
The trigger's model_source (base/fine_tuned) is applied to ALL NIMs.

Returns a dict mapping model_name to model info:
  - Key: model_name (NIM identifier, e.g., "meta/llama-3.2-1b-instruct")
  - Value: dict with:
    - "target": str (model name to use for fine-tuning target)
      - For base models: base model name from config (e.g., "meta/llama-3.2-1b-instruct@2.0")
      - For fine-tuned models: fine-tuned model name (e.g., "dfwbp/model@cust-abc123")
      - The pipeline constructs full target config (with model_uri, base_model, etc.) from the configuration
    - "is_fine_tuned": bool (True if using fine-tuned model, False if base model)
"""

from typing import TypedDict

from bson import ObjectId
from pymongo.database import Database

from src.api.triggers.models import RetrainingTrigger


class ModelInfo(TypedDict):
    """Information about a resolved model."""

    target: str  # Model name (base or fine-tuned) - pipeline constructs full config from this
    is_fine_tuned: bool  # True if using fine-tuned model, False if base


# Type alias for resolved models: {model_name: ModelInfo}
TargetModels = dict[str, ModelInfo]


class ModelResolver:
    """Resolve trigger's model selection to actual model names (sync version for Celery).

    Supports multi-NIM jobs where:
    - Each NIM has its own base_model and customized_model
    - Trigger's model_source (base/fine_tuned) applies to ALL NIMs
    """

    def __init__(self, db: Database):
        self.db = db

    def resolve(self, trigger: RetrainingTrigger) -> TargetModels:
        """
        Resolve models for ALL NIMs.

        Returns:
            dict mapping model_name → ModelInfo
            - model_name: NIM identifier (e.g., "meta/llama-3.2-1b-instruct")
            - ModelInfo contains:
              - target: str (model name for fine-tuning target)
                  - Base model: from config's customizer_configs.target
                  - Fine-tuned: stored fine-tuned model name
              - is_fine_tuned: bool indicating if this is a fine-tuned model (True) or base (False)

        Note: Only the model name (string) is returned in 'target'. The pipeline is responsible
        for constructing the full target config (with model_uri, base_model, etc.) from the
        configuration using this model name.

        Resolution logic by model_source:

        model_source="base":
        - ALWAYS uses config's customizer_configs.target (base model name)
        - is_fine_tuned=False for all models
        - reference_job_id is ignored for base mode

        model_source="fine_tuned":
        - Uses stored fine-tuned model name when available
        - is_fine_tuned=True when using stored fine-tuned model
        - is_fine_tuned=False when falling back to base (no fine-tuned model available yet)
        1. If reference_job_id provided:
           - Use that job's fine-tuned models
           - For NIMs not in that job, fallback to trigger's latest_fine_tuned_models
        2. If no reference_job_id:
           - Use trigger's latest_fine_tuned_models directly
        3. If no stored models yet:
           - Fallback to config targets (same as base)
        """
        if trigger.model_source == "fine_tuned":
            return self._resolve_fine_tuned(trigger)
        else:
            return self._resolve_base(trigger)

    def _resolve_fine_tuned(self, trigger: RetrainingTrigger) -> TargetModels:
        """
        Resolve fine-tuned models for all NIMs.

        Uses trigger's stored latest_fine_tuned_models (no job lookup for latest).
        For reference_job_id, uses that job's models with fallback to stored latest.
        Sets is_fine_tuned=True when using fine-tuned model, False when falling back to base.
        """
        # If reference job specified, overlay its models onto all configured NIMs
        if trigger.reference_job_id:
            # Start with all configured NIMs (base or stored fine-tuned models)
            if trigger.latest_fine_tuned_models:
                target_models = self._build_from_stored_models(trigger)
            else:
                # No stored models - start with config targets (all base)
                config_targets = self._get_target_models_from_config_raw(trigger.config_name)
                target_models = {
                    model_name: {"target": target, "is_fine_tuned": False}
                    for model_name, target in config_targets.items()
                }

            # Now overlay reference job's models (only for NIMs present in that job)
            job = self._get_reference_job(trigger, required=True)
            job_models = self._get_target_models_from_job(job, "fine_tuned")

            # Overlay job models - this updates NIMs present in the job, leaves others unchanged
            target_models.update(job_models)

            return target_models

        # No reference job - use stored latest_fine_tuned_models directly
        if trigger.latest_fine_tuned_models:
            return self._build_from_stored_models(trigger)

        # No stored models yet - fallback to config targets (all base models)
        config_targets = self._get_target_models_from_config_raw(trigger.config_name)
        return {
            model_name: {"target": target, "is_fine_tuned": False}
            for model_name, target in config_targets.items()
        }

    def _resolve_base(self, trigger: RetrainingTrigger) -> TargetModels:
        """
        Resolve base models for all NIMs.

        For base mode, target ALWAYS comes from config's customizer_configs.target.
        The reference_job_id is ignored for base mode - it only applies to fine_tuned mode.
        All models are marked as is_fine_tuned=False.
        """
        # Base mode always uses config targets, regardless of reference_job_id
        config_targets = self._get_target_models_from_config_raw(trigger.config_name)

        # Wrap in ModelInfo with is_fine_tuned=False
        return {
            model_name: {"target": target, "is_fine_tuned": False}
            for model_name, target in config_targets.items()
        }

    def _build_from_stored_models(self, trigger: RetrainingTrigger) -> TargetModels:
        """Build target models dict from trigger's stored latest_fine_tuned_models.

        latest_fine_tuned_models is keyed by model_name and contains fine-tuned model name strings.
        Returns ModelInfo with is_fine_tuned=True for stored models, False for config fallback.
        """
        # Get config targets for fallback (model_name → base model name)
        config_targets = self._get_target_models_from_config_raw(trigger.config_name)

        target_models: TargetModels = {}
        for model_name, fine_tuned_model in trigger.latest_fine_tuned_models.items():
            if fine_tuned_model:
                # Use stored fine-tuned model name (string)
                target_models[model_name] = {
                    "target": fine_tuned_model,
                    "is_fine_tuned": True,
                }
            else:
                # Fallback to config target (base model)
                target_models[model_name] = {
                    "target": config_targets.get(model_name, model_name),
                    "is_fine_tuned": False,
                }

        # Add any config models not in stored list (base models)
        for model_name, target in config_targets.items():
            if model_name not in target_models:
                target_models[model_name] = {"target": target, "is_fine_tuned": False}

        return target_models

    def _get_target_models_from_job(self, job: dict, model_source: str) -> TargetModels:
        """Get target models dict for all NIMs in a job.

        Gets fine-tuned model names from the trigger execution record.

        Returns dict mapping model_name → ModelInfo with:
        - target: str (fine-tuned model name if available, else base model name)
        - is_fine_tuned: bool flag indicating whether using fine-tuned or base
        """
        job_id = str(job["_id"])

        # Find the trigger execution that created this job
        execution = self.db.trigger_executions.find_one({"training_job_id": job_id})

        if not execution:
            raise ValueError(
                f"No trigger execution found for job {job_id}. Cannot resolve models from this job."
            )

        # Get config targets as fallback for base mode or missing fine-tuned models
        trigger = self.db.retraining_triggers.find_one({"_id": ObjectId(execution["trigger_id"])})
        if not trigger:
            raise ValueError(f"Trigger not found for execution {execution['_id']}")

        config_targets = self._get_target_models_from_config_raw(trigger["config_name"])

        target_models: TargetModels = {}

        # Build target models from execution.nim_results
        for nim_result in execution.get("nim_results", []):
            model_name = nim_result.get("model_name")
            if not model_name:
                continue

            # For fine_tuned mode: use fine_tuned_model name from execution if available
            if model_source == "fine_tuned":
                fine_tuned_model = nim_result.get("fine_tuned_model")
                if fine_tuned_model:
                    # fine_tuned_model is a string model name (e.g., "dfwbp/model@cust-abc123")
                    target_models[model_name] = {
                        "target": fine_tuned_model,
                        "is_fine_tuned": True,
                    }
                else:
                    # No fine-tuned model for this NIM - use config target as fallback
                    target_models[model_name] = {
                        "target": config_targets.get(model_name, model_name),
                        "is_fine_tuned": False,
                    }
            else:
                # Base mode: use config target
                target_models[model_name] = {
                    "target": config_targets.get(model_name, model_name),
                    "is_fine_tuned": False,
                }

        if not target_models:
            raise ValueError(f"No NIM results found in execution for job {job_id}")

        return target_models

    def _get_reference_job(self, trigger: RetrainingTrigger, required: bool = True):
        """
        Get the specific reference job by ID.

        Only used when reference_job_id is explicitly provided.
        Validates that the job was created by this trigger.
        """
        if not trigger.reference_job_id:
            if required:
                raise ValueError("reference_job_id is required but not provided")
            return None

        trigger_id = str(trigger.id)

        # Verify this job was created by this trigger
        execution = self.db.trigger_executions.find_one(
            {
                "trigger_id": trigger_id,
                "training_job_id": trigger.reference_job_id,
            }
        )
        if not execution:
            raise ValueError(
                f"Job {trigger.reference_job_id} was not created by trigger {trigger.name}. "
                "reference_job_id must be from this trigger's execution history."
            )

        job = self.db.flywheel_runs.find_one({"_id": ObjectId(trigger.reference_job_id)})
        if not job and required:
            raise ValueError(f"Job not found: {trigger.reference_job_id}")
        return job

    def _get_target_models_from_config_raw(self, config_name: str) -> dict[str, str]:
        """
        Get base model names from job configuration.

        Returns dict mapping model_name → base_model (string):
          - model_name: NIM identifier (e.g., "nvidia/llama-3.2-nv-embedqa-1b-v2")
          - base_model: The NIM's base model name (e.g., "nvidia/llama-3.2-nv-embedqa-1b-v2")

        Note: The base model is picked up from customizer_configs.target.base_model if present,
        otherwise the model_name itself is used as the base model (the NIM identifier IS the base model).
        """
        config = self.db.configurations.find_one({"name": config_name})

        if not config:
            raise ValueError(f"Configuration not found: {config_name}")

        target_models: dict[str, str] = {}
        config_data = config.get("config_data", {})
        nims = config_data.get("nims", [])

        for nim in nims:
            model_name = nim.get("model_name")
            if not model_name:
                continue

            # The base model is picked up from customizer_configs.target.base_model if present,
            # otherwise the model_name itself is used as the base model.
            target = (nim.get("customizer_configs") or {}).get("target")
            if isinstance(target, dict) and target.get("base_model"):
                target_models[model_name] = target["base_model"]
            else:
                # Use model_name as the base model (standard case)
                target_models[model_name] = model_name

        if not target_models:
            raise ValueError(f"No NIMs found in configuration: {config_name}")

        return target_models
