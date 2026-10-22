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

"""
Celery tasks for the Data Flywheel pipeline.

This module contains thin wrappers around task handlers. The actual business logic
is implemented in separate handler classes (src/tasks/handlers/) for better separation
of concerns and easier extensibility.

For adding new pipeline types (e.g., DPO):
1. Create new handlers in src/tasks/handlers/ if needed
2. Create a new pipeline orchestrator in src/tasks/pipelines/
3. Update run_nim_workflow_dag to use the new pipeline

Triggers:
- Automatic retraining triggers are implemented in src/tasks/triggers/
- Trigger execution tasks are defined below (execute_trigger)
"""

import sys

from celery import signals

from src.api.config_manager.manager import ConfigurationManager
from src.api.db import init_db
from src.api.db_manager import get_db_manager
from src.api.models import (
    DatasetType,
    EvalType,
    FlywheelRunStatus,
    TaskResult,
)
from src.celery_app import celery_app
from src.config import (
    CustomizerTarget,
    DataSplitConfig,
    EmbeddingDataSplitConfig,
    EmbeddingJobConfig,
    JobConfig,
    LLMJobConfig,
    NIMConfig,
    settings,
)
from src.job_types import JobType, is_deft_job_type
from src.lib.flywheel.cleanup_manager import CleanupManager
from src.lib.flywheel.job_manager import FlywheelJobManager
from src.lib.ingestion import IngestionConfig
from src.log_utils import setup_logging

# Import task handlers
from src.tasks.handlers import (
    CreateDatasetsHandler,
    CreateDPODatasetsHandler,
    CreateEmbeddingDatasetsHandler,
    CustomizationHandler,
    EmbeddingCustomizationHandler,
    EmbeddingEvaluationHandler,
    EvaluationHandler,
    FinalizeFlywheelRunHandler,
    InitializeWorkflowHandler,
    PrepareCustomizedNIMHandler,
    ShutdownCustomizedNIMHandler,
    ShutdownDeploymentHandler,
    SpinUpCustomizedNIMHandler,
    SpinUpNIMHandler,
    WaitForLLMJudgeHandler,
)

# Import ingestion service
from src.tasks.ingestion import run_data_ingestion
from src.tasks.pipelines import (
    AOIPipeline,
    DeftPipeline,
    DPOPipeline,
    EmbeddingPipeline,
    ITSPipeline,
    LLMPipeline,
)

logger = setup_logging("data_flywheel.tasks")

#: DEFT-family job type → its pipeline. One entry per member of
#: ``DEFT_JOB_TYPES`` (pinned by a parity test); routing only, since every leaf
#: builds the same DAG.
_DEFT_PIPELINES: dict[str, type[DeftPipeline]] = {
    JobType.AOI.value: AOIPipeline,
    JobType.ITS.value: ITSPipeline,
}

# Centralised DB helper - keeps Mongo specifics out of individual tasks
db_manager = None

# Configuration manager - global instance for configuration management
config_manager = None


@signals.worker_shutting_down.connect
def worker_shutdown(sig, how, exitcode, **kwargs):
    if str(kwargs.get("sender", "")).startswith("main_worker"):
        logger.info(f"Worker shutdown for the worker: {sig!s}")
        logger.info(f"How: {how!s}")
        logger.info(f"Exitcode: {exitcode!s}")

        logger.info("Calling Cleanup Manager")
        db_manager = get_db_manager()
        cleanup_manager = CleanupManager(db_manager)
        cleanup_manager.cleanup_all_running_resources()


@signals.worker_ready.connect
def worker_startup_sweep(sender=None, **kwargs):
    """Mark stale runs cancelled after a (re)start — hard-kill recovery.

    The graceful path is ``worker_shutdown`` above; on a hard kill (OOM, pod
    eviction) that signal never fires, and the worker starts with ``--purge``,
    so any in-flight chain messages are gone. Every non-terminal run
    (PENDING/RUNNING/PROMOTING) found at startup is therefore stale by
    definition — mark it cancelled and close out its promotions, exactly like
    the shutdown cleanup. (Deployment assumption, as with the shutdown hook:
    a single main worker consumes the chain queue.)
    """
    if not str(getattr(sender, "hostname", "")).startswith("main_worker"):
        return
    try:
        sweep_db = get_db_manager()
        stale = sweep_db.find_running_flywheel_runs()
        if stale:
            # WARNING: this cancels real user runs — the blast radius of a
            # restart should be visible, not buried at info level.
            logger.warning(
                "Startup sweep: cancelling %d stale non-terminal flywheel run(s)", len(stale)
            )
        cleanup_manager = CleanupManager(sweep_db)
        cleanup_manager.cleanup_all_running_resources()
    except Exception:
        logger.exception("Startup sweep failed; stale runs may need manual cleanup")


@signals.worker_process_init.connect
def init_worker(**kwargs):
    """Initialize database connection after worker process is forked."""
    global db_manager, config_manager
    try:
        init_db()
        db_manager = get_db_manager()
        config_manager = ConfigurationManager()
        logger.info("Successfully initialized database connection and db_manager")
    except Exception as e:
        logger.critical(
            f"FATAL: Failed to initialize database connection: {e}. "
            "Worker cannot function without database. Exiting..."
        )
        # Force worker to exit
        sys.exit(1)


# -------------------------------------------------------------
# Common Tasks (used by both LLM and Embedding pipelines)
# -------------------------------------------------------------


@celery_app.task(name="tasks.initialize_workflow", pydantic=True)
def initialize_workflow(
    workload_id: str,
    flywheel_run_id: str,
    client_id: str,
    job_type: str,
    job_config: dict,
    output_dataset_prefix: str = "",
    data_split_config: dict | None = None,
) -> TaskResult:
    """Initialize the workflow for the given workload.

    This is a thin wrapper around InitializeWorkflowHandler.
    Works for both LLM and embedding workflows.
    """
    handler = InitializeWorkflowHandler(db_manager)
    return handler.execute(
        workload_id=workload_id,
        flywheel_run_id=flywheel_run_id,
        client_id=client_id,
        job_type=job_type,
        job_config=job_config,
        output_dataset_prefix=output_dataset_prefix,
        data_split_config=data_split_config,
    )


@celery_app.task(name="tasks.create_datasets", pydantic=True)
def create_datasets(previous_result: TaskResult) -> TaskResult:
    """Create datasets for LLM workloads.

    This is a thin wrapper around CreateDatasetsHandler.
    """
    assert isinstance(previous_result, TaskResult)
    handler = CreateDatasetsHandler(db_manager)
    return handler.execute(previous_result)


@celery_app.task(name="tasks.create_embedding_datasets", pydantic=True)
def create_embedding_datasets(previous_result: TaskResult) -> TaskResult:
    """Create datasets for embedding workloads.

    This is a thin wrapper around CreateEmbeddingDatasetsHandler.
    """
    assert isinstance(previous_result, TaskResult)
    handler = CreateEmbeddingDatasetsHandler(db_manager)
    return handler.execute(previous_result)


@celery_app.task(name="tasks.create_dpo_datasets", pydantic=True)
def create_dpo_datasets(previous_result: TaskResult) -> TaskResult:
    """Create datasets for DPO (Direct Preference Optimization) workloads.

    This is a thin wrapper around CreateDPODatasetsHandler.
    """
    assert isinstance(previous_result, TaskResult)
    handler = CreateDPODatasetsHandler(db_manager)
    return handler.execute(previous_result)


@celery_app.task(name="tasks.spin_up_nim", pydantic=True)
def spin_up_nim(
    previous_result: TaskResult, nim_config: dict | None = None, use_customized_nim: bool = False
) -> TaskResult:
    """Spin up a NIM instance.

    This is a thin wrapper around SpinUpNIMHandler.

    Args:
        previous_result: Result from the previous task
        nim_config: Full NIM configuration (if not using customized)
        use_customized_nim: If True, use the customized NIM config from previous_result
    """
    assert isinstance(previous_result, TaskResult)

    # Route to appropriate handler based on use_customized_nim flag
    if use_customized_nim:
        # Handler extracts customized_nim from previous_result
        handler = SpinUpCustomizedNIMHandler(db_manager)
        return handler.execute(previous_result)
    else:
        # Use the regular handler for base models
        handler = SpinUpNIMHandler(db_manager)
        return handler.execute(previous_result, nim_config)


@celery_app.task(name="tasks.shutdown_deployment", pydantic=True)
def shutdown_deployment(previous_results: list[TaskResult] | TaskResult) -> TaskResult:
    """Shutdown the NIM deployment.

    This is a thin wrapper around ShutdownDeploymentHandler.
    """
    handler = ShutdownDeploymentHandler(db_manager)
    return handler.execute(previous_results)


@celery_app.task(name="tasks.finalize_flywheel_run", pydantic=True)
def finalize_flywheel_run(previous_results: list[TaskResult] | TaskResult) -> TaskResult:
    """Finalize the Flywheel run.

    This is a thin wrapper around FinalizeFlywheelRunHandler.
    """
    handler = FinalizeFlywheelRunHandler(db_manager)
    return handler.execute(previous_results)


# -------------------------------------------------------------
# LLM Pipeline Tasks
# -------------------------------------------------------------


@celery_app.task(name="tasks.wait_for_llm_as_judge", pydantic=True)
def wait_for_llm_as_judge(previous_result: TaskResult) -> TaskResult:
    """Wait for LLM judge to be ready.

    This is a thin wrapper around WaitForLLMJudgeHandler.
    """
    assert isinstance(previous_result, TaskResult)
    handler = WaitForLLMJudgeHandler(db_manager)
    return handler.execute(previous_result)


@celery_app.task(name="tasks.run_base_eval", pydantic=True)
def run_base_eval(previous_result: TaskResult) -> TaskResult:
    """Run base evaluation for LLM models.

    This is a thin wrapper around EvaluationHandler.
    """
    handler = EvaluationHandler(db_manager)
    return handler.execute(previous_result, EvalType.BASE, DatasetType.BASE)


@celery_app.task(name="tasks.run_icl_eval", pydantic=True)
def run_icl_eval(previous_result: TaskResult) -> TaskResult:
    """Run ICL evaluation for LLM models.

    This is a thin wrapper around EvaluationHandler.
    """
    handler = EvaluationHandler(db_manager)
    return handler.execute(previous_result, EvalType.ICL, DatasetType.ICL)


@celery_app.task(name="tasks.start_customization", pydantic=True)
def start_customization(previous_result: TaskResult) -> TaskResult:
    """Start LLM customization process.

    This is a thin wrapper around CustomizationHandler.
    """
    handler = CustomizationHandler(db_manager)
    return handler.execute(previous_result)


@celery_app.task(name="tasks.run_customization_eval", pydantic=True)
def run_customization_eval(previous_result: TaskResult) -> TaskResult:
    """Run evaluation on the customized LLM model.

    This is a thin wrapper around EvaluationHandler.
    """
    # Skip customization evaluation if the NIM is not enabled for customization
    if not previous_result.nim.customization_enabled:
        logger.info(f"Customization disabled for {previous_result.nim.model_name}")
        return previous_result

    handler = EvaluationHandler(db_manager)
    return handler.execute(previous_result, EvalType.CUSTOMIZED, DatasetType.BASE)


# -------------------------------------------------------------
# Embedding Pipeline Tasks
# -------------------------------------------------------------


@celery_app.task(name="tasks.run_base_embedding_eval", pydantic=True)
def run_base_embedding_eval(
    previous_result: TaskResult,
    dataset_type: DatasetType = DatasetType.BASE,
    dataset_name: str | None = None,
) -> TaskResult:
    """Run base evaluation for embedding models.

    This is a thin wrapper around EmbeddingEvaluationHandler.
    """
    handler = EmbeddingEvaluationHandler(db_manager)
    return handler.execute(
        previous_result,
        EvalType.BASE,
        use_customized=False,
        dataset_type=dataset_type,
        dataset_name=dataset_name,
    )


@celery_app.task(name="tasks.start_embedding_customization", pydantic=True)
def start_embedding_customization(previous_result: TaskResult) -> TaskResult:
    """Start embedding customization process.

    This is a thin wrapper around EmbeddingCustomizationHandler.
    """
    handler = EmbeddingCustomizationHandler(db_manager)
    return handler.execute(previous_result)


@celery_app.task(name="tasks.prepare_customized_nim", pydantic=True)
def prepare_customized_nim(previous_result: TaskResult) -> TaskResult:
    """Prepare customized NIM configuration (works for both embedding and LLM/DPO).

    This is a thin wrapper around PrepareCustomizedNIMHandler.
    """
    handler = PrepareCustomizedNIMHandler(db_manager)
    return handler.execute(previous_result)


@celery_app.task(name="tasks.run_embedding_customization_eval", pydantic=True)
def run_embedding_customization_eval(
    previous_result: TaskResult,
    dataset_type: DatasetType = DatasetType.BASE,
    dataset_name: str | None = None,
) -> TaskResult:
    """Run evaluation on the customized embedding model.

    This is a thin wrapper around EmbeddingEvaluationHandler.
    """
    handler = EmbeddingEvaluationHandler(db_manager)
    return handler.execute(
        previous_result,
        EvalType.CUSTOMIZED,
        use_customized=True,
        dataset_type=dataset_type,
        dataset_name=dataset_name,
    )


@celery_app.task(name="tasks.shutdown_customized_nim", pydantic=True)
def shutdown_customized_nim(previous_result: list[TaskResult]) -> TaskResult:
    """Shutdown customized NIM deployment (works for both embedding and LLM/DPO).

    This is a thin wrapper around ShutdownCustomizedNIMHandler.
    """
    handler = ShutdownCustomizedNIMHandler(db_manager)
    return handler.execute(previous_result)


# -------------------------------------------------------------
# Workflow Orchestration
# -------------------------------------------------------------


def _update_config_for_finetuned_model(
    nim: NIMConfig,
    target_model: str,
    model_name: str,
) -> None:
    """
    Update NIM config for iterative fine-tuning on a previously customized model.

    Updates both the eval config (top-level NIM) and customizer config to use the
    fine-tuned model as the base for further training.

    Args:
        nim: The NIM configuration to update
        target_model: Fine-tuned model target string (e.g., "dfwbp/llama-3.2-1b@cust-123")
        model_name: Original base model name

    Configuration Updates (in order to avoid partial state):
        1. Prepare nim_image_model_name: Save original base model (if not present)

        2. Update Customizer Config:
           - If target is a string: Retrieve full target from NeMo Customizer API
           - Update target with:
             * name: Target name with version (e.g., "nvidia/llama-3.2@v2")
             * base_model: Original base model
             * model_uri: Constructed as "hf://{finetuned_model_name}"
             * num_parameters, precision: From retrieved target

        3. Update Eval Config (last, after validation):
           - model_name: Set to fine-tuned model name

    Note:
        Pydantic validates that customizer_configs.target cannot be None,
        so we don't need explicit None checking here.

    Example:
        Input:
            nim.model_name = "meta/llama-3.2-1b-instruct"
            target_model = "dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte"

        Output:
            nim.model_name = "dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte"
            nim.nim_image_model_name = "meta/llama-3.2-1b-instruct"
            nim.customizer_configs.target = {
                "name": "nvidia/llama-3.2-nv-embedqa-1b-custom@v2",
                "base_model": "meta/llama-3.2-1b-instruct",
                "model_uri": "hf://dfwbp/q1s@cust-KjP4tQNFDkXxbAZmySDKte",
                "num_parameters": 1000000000,
                "precision": "bf16-mixed"
            }
    """
    if not nim.customizer_configs:
        logger.error(
            f"Cannot update fine-tuned model config: customizer_configs is None for {model_name}"
        )
        return

    logger.info(f"Updating config for fine-tuned model: {model_name} → {target_model}")

    # Save original model name for reference
    original_model_name = nim.model_name

    # 1. Prepare nim_image_model_name (save original base model if not already set)
    if not nim.nim_image_model_name:
        nim.nim_image_model_name = nim.model_name
        logger.info(f"Set nim_image_model_name to original base model: {nim.nim_image_model_name}")

    # 2. Update customizer config target
    current_target = nim.customizer_configs.target

    # Check if target is a string (simple target name) or already a dict/object
    if isinstance(current_target, str):
        # Target is a simple string - need to retrieve full target from API
        logger.info(
            f"Customizer target is a string: '{current_target}'. "
            "Retrieving full target definition from NeMo Customizer API..."
        )

        # Use the training backend's get_target_info() to retrieve target from API
        from src.backends.factory import get_backend_provider

        customizer = get_backend_provider().training()
        logger.info(f"Retrieving target from NeMo Customizer API: {current_target}")

        retrieved_target = customizer.get_target_info(current_target)

        if not retrieved_target:
            raise ValueError(f"Failed to retrieve target info for: {current_target}")

        logger.info(
            f"Successfully retrieved target: {retrieved_target.name}, "
            f"num_parameters={retrieved_target.num_parameters}, "
            f"precision={retrieved_target.precision}"
        )

        # Construct CustomizerTarget from API response
        # API response fields: name, namespace, base_model, model_uri, model_path,
        # tokenizer, num_parameters, precision, description, etc.
        full_target = CustomizerTarget(
            name=target_model,  # Use the fine-tuned model target as name (e.g., dfwbp/q1s@cust-xyz)
            target_type=getattr(retrieved_target, "target_type", "nemo"),  # Default to 'nemo'
            description=getattr(retrieved_target, "description", None),
            base_model=nim.nim_image_model_name or original_model_name,  # Override with original
            model_uri=f"hf://{target_model}",  # Override with fine-tuned model URI
            hf_endpoint=getattr(retrieved_target, "hf_endpoint", None),
            tokenizer=getattr(retrieved_target, "tokenizer", None) or {},  # Ensure dict, not None
            num_parameters=retrieved_target.num_parameters,  # From API
            precision=retrieved_target.precision,  # From API
            model_path=getattr(retrieved_target, "model_path", None),
        )

        logger.info(
            f"Created CustomizerTarget from API: name={full_target.name}, "
            f"model_uri={full_target.model_uri}, num_parameters={full_target.num_parameters}"
        )

        nim.customizer_configs.target = full_target
        logger.info(
            f"Updated customizer target to full object: name={full_target.name}, "
            f"model_uri={full_target.model_uri}"
        )

    elif isinstance(current_target, dict):
        # Target is a dict - update model_uri and ensure name has version
        logger.info("Customizer target is a dict. Updating model_uri and checking name...")

        # Update model_uri to point to fine-tuned model
        original_uri = current_target.get("model_uri")
        current_target["model_uri"] = f"hf://{target_model}"
        logger.info(f"Updated model_uri: {original_uri} → {current_target['model_uri']}")

        # Ensure name has a version if present
        if "name" in current_target:
            target_name = current_target["name"]
            if "@" not in target_name:
                current_target["name"] = f"{target_name}@v1"
                logger.warning(
                    f"Added version to target name: {target_name} → {current_target['name']}"
                )

        # Ensure base_model is consistent with original
        if "base_model" in current_target:
            if current_target["base_model"] != (nim.nim_image_model_name or original_model_name):
                logger.info(
                    f"Updating base_model: {current_target['base_model']} → "
                    f"{nim.nim_image_model_name or original_model_name}"
                )
                current_target["base_model"] = nim.nim_image_model_name or original_model_name

    else:
        # Target is an object (e.g., CustomizerTarget) - update model_uri
        logger.info("Customizer target is an object. Updating model_uri...")

        # Update model_uri to point to fine-tuned model
        if hasattr(current_target, "model_uri"):
            original_uri = current_target.model_uri
            current_target.model_uri = f"hf://{target_model}"
            logger.info(f"Updated model_uri: {original_uri} → {current_target.model_uri}")

        # Ensure name has a version if present
        if hasattr(current_target, "name"):
            target_name = current_target.name
            if "@" not in target_name:
                current_target.name = f"{target_name}@v1"
                logger.warning(
                    f"Added version to target name: {target_name} → {current_target.name}"
                )

        # Ensure base_model is consistent
        if hasattr(current_target, "base_model"):
            expected_base = nim.nim_image_model_name or original_model_name
            if current_target.base_model != expected_base:
                logger.info(f"Updating base_model: {current_target.base_model} → {expected_base}")
                current_target.base_model = expected_base

    # 3. Update eval config (top-level NIM) - done last to avoid partial state
    # Update model_name to fine-tuned model only after customizer target is successfully updated
    nim.model_name = target_model
    logger.info(f"Updated model_name: {original_model_name} → {nim.model_name}")

    logger.info(
        f"Successfully updated config for fine-tuned model {model_name}. "
        f"Ready for iterative fine-tuning on {target_model}"
    )


def _apply_training_enabled_override(nims: list[NIMConfig], training_enabled: bool) -> None:
    """
    Apply training_enabled override to NIMs by updating customizer_enabled.

    Args:
        nims: List of NIM configurations to update
        training_enabled: Whether training should be enabled
    """
    for nim in nims:
        original_customizer_enabled = nim.customization_enabled
        nim.customization_enabled = training_enabled

        if original_customizer_enabled != nim.customization_enabled:
            logger.info(
                f"Runtime override: {nim.model_name} customizer_enabled: "
                f"{original_customizer_enabled} → {nim.customization_enabled}"
            )


def _apply_eval_enabled_override(nims: list[NIMConfig], eval_enabled: bool) -> None:
    """
    Apply eval_enabled override to NIMs.

    Args:
        nims: List of NIM configurations to update
        eval_enabled: Whether evaluation should be enabled
    """
    for nim in nims:
        original_eval_enabled = nim.eval_enabled
        nim.eval_enabled = eval_enabled

        if original_eval_enabled != nim.eval_enabled:
            logger.info(
                f"Runtime override: {nim.model_name} eval_enabled: "
                f"{original_eval_enabled} → {nim.eval_enabled}"
            )


def _apply_target_models_override(nims: list[NIMConfig], target_models: dict) -> None:
    """
    Apply target_models override for fine-tuned model retraining.

    For each NIM with a corresponding target_model entry:
    - Base models: No action needed (trains on base model directly)
    - Fine-tuned models: Update customizer_configs for iterative fine-tuning

    Args:
        nims: List of NIM configurations to update
        target_models: Mapping of model_name → ModelInfo (target + is_fine_tuned)
    """
    if not target_models:
        return

    logger.info(f"Runtime override: Processing target_models for {len(target_models)} NIMs")

    for nim in nims:
        model_info = target_models.get(nim.model_name)

        if not model_info:
            continue

        # Extract target and is_fine_tuned flag from ModelInfo
        target = (
            model_info.get("target")
            if isinstance(model_info, dict)
            else getattr(model_info, "target", None)
        )
        is_fine_tuned = (
            model_info.get("is_fine_tuned")
            if isinstance(model_info, dict)
            else getattr(model_info, "is_fine_tuned", False)
        )

        if not target:
            continue

        # For base models: no action needed (trains on base model directly)
        if not is_fine_tuned:
            logger.info(
                f"Runtime override: {nim.model_name} is base model, "
                f"training on base model directly (target: {target})"
            )
            continue

        # For fine-tuned models: Update customizer_configs for iterative fine-tuning
        logger.info(
            f"Runtime override: {nim.model_name} is fine-tuned model, "
            f"updating config for iterative fine-tuning (target: {target})"
        )

        # Call helper function to update config for iterative fine-tuning
        _update_config_for_finetuned_model(nim=nim, target_model=target, model_name=nim.model_name)


def _apply_date_range_override(
    job_config: LLMJobConfig | EmbeddingJobConfig,
    from_date: str | None,
    to_date: str | None,
) -> None:
    """
    Apply date range overrides for embedding jobs.

    Both from_date and to_date must be provided together to ensure a valid date window.

    Args:
        job_config: The job configuration to update
        from_date: Start date for data split (YYYY-MM-DD)
        to_date: End date for data split (YYYY-MM-DD)
    """
    if from_date and to_date:
        if isinstance(job_config, EmbeddingJobConfig):
            job_config.data_split_config.from_date = from_date
            job_config.data_split_config.to_date = to_date
            logger.info(
                f"Runtime override: data_split_config date range → "
                f"from_date={from_date}, to_date={to_date}"
            )
        else:
            logger.warning(
                "Runtime override: from_date/to_date provided but job_type is not embedding_job. "
                "Date overrides are only supported for embedding jobs."
            )
    elif from_date or to_date:
        # Log warning if only one date is provided
        logger.warning(
            f"Runtime override: Incomplete date range provided "
            f"(from_date={from_date}, to_date={to_date}). "
            "Both from_date and to_date must be provided together. Ignoring date overrides."
        )


def _apply_runtime_overrides(
    job_config: LLMJobConfig | EmbeddingJobConfig,
    runtime_overrides: dict,
) -> LLMJobConfig | EmbeddingJobConfig:
    """
    Apply runtime overrides from triggers to the job configuration.

    Runtime overrides allow triggers to dynamically modify job behavior without
    changing the base configuration.

    Args:
        job_config: The base job configuration loaded from config_name
        runtime_overrides: Dictionary of overrides from trigger execution:
            - eval_enabled: bool - Whether to run evaluation (stored in NIM configs)
            - training_enabled: bool - Enables training phase (customizer_enabled)
            - target_models: dict - Mapping of model_name → ModelInfo (target + is_fine_tuned)
            - from_date: str - Override data split from_date (embedding jobs)
            - to_date: str - Override data split to_date (embedding jobs)

    Returns:
        Modified job configuration with overrides applied

    Notes:
        Each override type is handled by a dedicated helper function for better
        modularity and testability.
    """
    # 1. Handle training_enabled → customizer_enabled
    training_enabled = runtime_overrides.get("training_enabled", True)
    _apply_training_enabled_override(job_config.nims, training_enabled)

    # 2. Handle eval_enabled (for pipeline to check if evaluation should run)
    eval_enabled = runtime_overrides.get("eval_enabled", True)
    _apply_eval_enabled_override(job_config.nims, eval_enabled)

    # 3. Handle target_models for fine-tuned model retraining
    target_models = runtime_overrides.get("target_models", {})
    _apply_target_models_override(job_config.nims, target_models)

    # 4. Handle date range overrides for embedding jobs
    from_date = runtime_overrides.get("from_date")
    to_date = runtime_overrides.get("to_date")
    _apply_date_range_override(job_config, from_date, to_date)

    return job_config


@celery_app.task(
    name="tasks.run_nim_workflow_dag",
    pydantic=True,
    queue="parent_queue",
)
def run_nim_workflow_dag(
    workload_id: str,
    flywheel_run_id: str,
    client_id: str,
    data_split_config: dict | None = None,
    job_type: str = "llm_job",
    config_name: str | None = None,
    runtime_overrides: dict | None = None,
    ingestion_config: dict | None = None,
) -> dict:
    """Execute a flywheel run as a DAG — the dispatcher for every job type.

    Despite the NIM-era name this is not NMP-only: ``job_type`` may also be a
    DEFT-family type (``aoi_job`` / ``its_job``), for which the NMP-shaped steps
    below are inert (``DeftJobConfig`` declares neither ``ingestion_config`` nor
    ``data_split_config``, and ``runtime_overrides`` are refused with a warning).
    This task blocks on the whole chain, so a DEFT run holds the concurrency-1
    ``parent_queue`` slot until its promotion rollout terminates — hours or days.

    Args:
        workload_id: Workload identifier
        flywheel_run_id: ID of the FlywheelRun document
        client_id: Client identifier
        data_split_config: Optional data split configuration (ignored by the DEFT
            pipeline — DEFT trains from an immutable snapshot, not a split)
        job_type: Type of job ("llm_job", "embedding_job", "aoi_job" or "its_job")
        config_name: Optional configuration name
        runtime_overrides: Optional runtime overrides from triggers (ignored, with
            a warning, for DEFT job types):
            - eval_enabled: If True, run evaluation phase
            - training_enabled: If True, run training phase
            - target_models: Dict mapping model_name → fine-tuning target
        ingestion_config: Optional ingestion config (same schema as IngestionConfig in
            src.lib.ingestion). When passed from the API it is serialized as a dict.
            When omitted, job config's ingestion_config is used if present.
    """
    # Log runtime overrides if provided
    if runtime_overrides:
        logger.info(f"Runtime overrides received for job {flywheel_run_id}: {runtime_overrides}")
    # Check if this flywheel run is already running to prevent duplicate execution
    flywheel_run_doc = db_manager.get_flywheel_run(flywheel_run_id)
    if not flywheel_run_doc:
        error_msg = f"FlywheelRun {flywheel_run_id} not found in database"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Sometimes celery runs the task multiple times, check if already running
    if flywheel_run_doc.get("status") not in [FlywheelRunStatus.PENDING]:
        logger.warning(
            f"FlywheelRun {flywheel_run_id} is already {flywheel_run_doc.get('status')}. Skipping execution."
        )
        return {
            "status": "skipped",
            "reason": f"already_{flywheel_run_doc.get('status')}",
            "flywheel_run_id": flywheel_run_id,
        }

    # Load job configuration - wrap in try-catch to mark job as failed on error
    try:
        job_config = load_job_configuration(job_type, config_name)
    except Exception as e:
        error_msg = f"Failed to load configuration '{config_name}': {e}"
        logger.error(error_msg)
        db_manager.mark_flywheel_run_error(flywheel_run_id, error_msg)
        raise ValueError(error_msg) from e

    # Run data ingestion if config provided (API override or job config). Same IngestionConfig
    effective_config: IngestionConfig | None = None
    if ingestion_config:
        effective_config = (
            IngestionConfig(**ingestion_config)
            if isinstance(ingestion_config, dict)
            else ingestion_config
        )
    elif hasattr(job_config, "ingestion_config") and job_config.ingestion_config:
        effective_config = job_config.ingestion_config

    if effective_config:
        from src.api.db import get_db

        try:
            db = get_db()
            result = run_data_ingestion(
                db=db,
                workload_id=workload_id,
                client_id=client_id,
                plugin_name=effective_config.plugin_name,
                config=effective_config.config,
                job_type=job_type,
                dataset_type=effective_config.dataset_type,
                job_id=flywheel_run_id,
            )
            logger.info(f"Ingestion completed: {result.records_loaded} records loaded")
        except Exception as e:
            logger.error(f"Data ingestion failed for job {flywheel_run_id}: {e}")
            # Continue with the job even if ingestion fails - data may already exist
            logger.warning("Continuing with existing data in Elasticsearch")

    # Apply runtime overrides if provided (e.g., from triggers).
    # _apply_runtime_overrides is NMP/LLM-shaped (touches nims/eval) and
    # DeftJobConfig has no such fields: reached with a truthy overrides dict it
    # would die on `job_config.nims` outside every mark_flywheel_run_error
    # guard, leaving the run PENDING forever. Skipped for the whole family so a
    # new DEFT job type cannot fall through to the NMP branch.
    if runtime_overrides:
        if is_deft_job_type(job_type):
            logger.warning(
                "Runtime overrides are not supported for %s jobs; ignoring %s",
                job_type,
                list(runtime_overrides),
            )
        else:
            job_config = _apply_runtime_overrides(job_config, runtime_overrides)

    # Handle data_split_config: convert if provided, otherwise use job_config default.
    # AOI (DEFT-loop) jobs have no train/val/eval split — DEFT trains from an
    # immutable object-store snapshot — so split_config stays None for them.
    if data_split_config:
        if job_type == "embedding_job":
            split_config = EmbeddingDataSplitConfig(**data_split_config)
        else:
            split_config = DataSplitConfig(**data_split_config)
    else:
        # Use the default from job_config (absent for AOI configs).
        split_config = getattr(job_config, "data_split_config", None)

    # Reject job types the active backend can't run. Covers the trigger path,
    # which calls this dispatcher directly (bypassing the /jobs API gate).
    from src.backends.factory import get_backend_provider

    provider = get_backend_provider()
    if not provider.supports_job_type(job_type):
        error_msg = (
            f"Backend '{provider.kind}' does not support job_type '{job_type}'. "
            f"Supported: {sorted(provider.supported_job_types)}."
        )
        db_manager.mark_flywheel_run_error(flywheel_run_id, error_msg)
        raise ValueError(error_msg)

    # Select pipeline based on job type and training type
    if job_type == JobType.LLM:
        # Check if this is a DPO job
        if (
            hasattr(job_config, "training_config")
            and job_config.training_config.training_type == "dpo"
        ):
            pipeline = DPOPipeline(db_manager)
            logger.info("Using DPOPipeline for DPO training job")
        else:
            pipeline = LLMPipeline(db_manager)
    elif job_type == JobType.EMBEDDING:
        pipeline = EmbeddingPipeline(db_manager)
    elif is_deft_job_type(job_type):
        # DEFT-loop pipeline (initialize → snapshot → DEFT train → offline eval gate
        # → register → promote → wait → finalize). DEFT configs build from the
        # central contract registry (src.config.build_job_config).
        #
        # Tested with ``is_deft_job_type`` rather than ``job_type in
        # _DEFT_PIPELINES`` on purpose: a family member missing from the map must
        # mark the run failed here, not fall to the else-branch raise below,
        # which sits outside every mark_flywheel_run_error guard and would leave
        # the run PENDING forever.
        try:
            pipeline_cls = _DEFT_PIPELINES[job_type]
        except KeyError:
            error_msg = f"No pipeline registered for DEFT job_type '{job_type}'"
            logger.error(error_msg)
            db_manager.mark_flywheel_run_error(flywheel_run_id, error_msg)
            raise ValueError(error_msg) from None
        pipeline = pipeline_cls(db_manager)
    else:
        raise ValueError(f"Unsupported job type: {job_type}")

    # Build workflow using the pipeline orchestrator
    workflow = pipeline.build_workflow(
        workload_id=workload_id,
        flywheel_run_id=flywheel_run_id,
        client_id=client_id,
        job_type=job_type,
        job_config=job_config,
        data_split_config=split_config,
    )

    # Submit the workflow to Celery and block until completion
    async_result = workflow.apply_async()
    return async_result.get(disable_sync_subtasks=False)


# -------------------------------------------------------------
# Resource Management Tasks
# -------------------------------------------------------------


@celery_app.task(name="tasks.delete_job_resources", pydantic=True)
def delete_job_resources(job_id: str) -> None:
    """Delete all resources associated with a job."""
    cleanup_manager = FlywheelJobManager(db_manager)
    cleanup_manager.delete_job(job_id)


@celery_app.task(name="tasks.cancel_job_resources", pydantic=True)
def cancel_job_resources(job_id: str) -> None:
    """Cancel a job by marking it as cancelled."""
    job_manager = FlywheelJobManager(db_manager)
    job_manager.cancel_job(job_id)


# -------------------------------------------------------------
# Trigger Tasks
# -------------------------------------------------------------


@celery_app.task(name="tasks.execute_trigger", pydantic=True)
def execute_trigger(trigger_id: str, execution_id: str | None = None) -> dict:
    """
    Execute an automatic retraining trigger.

    This task is scheduled by Celery Beat based on trigger's eval_schedule.
    Execution behavior depends on trigger_type:
    - data_threshold: Check min records → if met: eval + train
    - metric_degradation: Ingest → eval → compare → train if metrics fail
    - schedule: Ingest → eval + train (always)

    The executor checks for existing running/pending executions and creates
    a "skipped" record if another execution is already in progress.

    Args:
        trigger_id: ID of the trigger to execute
        execution_id: Optional pre-created execution ID (for API-triggered executions)

    Returns:
        Dict with execution status and ID
    """
    from bson import ObjectId

    from src.api.db import get_db
    from src.api.triggers.models import RetrainingTrigger
    from src.tasks.triggers.executor import TriggerExecutor

    logger.info(f"Executing trigger: {trigger_id} (execution_id: {execution_id})")

    # Get synchronous database connection
    db = get_db()

    # Load trigger
    trigger_doc = db.retraining_triggers.find_one({"_id": ObjectId(trigger_id)})

    if not trigger_doc:
        raise ValueError(f"Trigger not found: {trigger_id}")

    trigger = RetrainingTrigger.from_mongo(trigger_doc)

    if not trigger.enabled:
        logger.info(f"Trigger {trigger.name} is disabled, skipping execution")
        return {"status": "skipped", "reason": "disabled"}

    # Execute trigger (synchronous)
    # Executor checks for running/pending executions and skips if needed
    executor = TriggerExecutor(db)
    execution = executor.execute(trigger, execution_id=execution_id)

    # Update trigger's last execution info
    if execution.status == "failed":
        last_status = "failure"
        # Only increment failures on actual failure, not skip
        new_consecutive_failures = trigger.consecutive_failures + 1
    elif execution.status == "skipped":
        # Skipped executions don't affect status or failure count
        last_status = trigger.last_execution_status or "success"
        new_consecutive_failures = trigger.consecutive_failures
    elif execution.triggered_training:
        last_status = "triggered_retraining"
        new_consecutive_failures = 0
    else:
        last_status = "success"
        new_consecutive_failures = 0

    # Check if trigger should be auto-disabled due to consecutive failures
    should_disable = (
        trigger.max_consecutive_failures > 0
        and new_consecutive_failures >= trigger.max_consecutive_failures
    )

    update_fields = {
        "last_execution_time": execution.execution_time,
        "last_execution_status": last_status,
        "consecutive_failures": new_consecutive_failures,
    }

    if should_disable and trigger.enabled:
        update_fields["enabled"] = False
        logger.warning(
            f"Trigger '{trigger.name}' ({trigger_id}) auto-disabled after "
            f"{new_consecutive_failures} consecutive failures "
            f"(max_consecutive_failures={trigger.max_consecutive_failures})"
        )

    db.retraining_triggers.update_one(
        {"_id": ObjectId(trigger_id)},
        {"$set": update_fields},
    )

    result = {
        "status": execution.status,
        "execution_id": str(execution.id),
        "triggered_training": execution.triggered_training,
        "skip_reason": execution.skip_reason,
    }

    logger.info(f"Trigger execution complete: {result}")
    return result


# -------------------------------------------------------------
# Configuration Management
# -------------------------------------------------------------


def load_job_configuration(job_type: str, config_name: str | None = None) -> JobConfig:
    """Load job configuration from database or use default settings.

    Returns the active backend's ``JobConfig`` subclass — ``LLMJobConfig`` /
    ``EmbeddingJobConfig`` (NMP) or ``DeftJobConfig`` (DEFT).
    """
    if config_name:
        try:
            config_doc = config_manager.get_configuration(config_name)
            if not config_doc:
                raise ValueError(f"Configuration '{config_name}' not found")
            return config_doc.to_job_config()
        except Exception as e:
            logger.error(f"Failed to load configuration '{config_name}': {e}")
            raise ValueError(f"Failed to load configuration '{config_name}': {e}") from e
    else:
        # Use default configuration based on job type (from the backend-loaded
        # job_configs dict, keyed by job_type).
        job_config = settings.job_configs.get(job_type)
        if job_config is not None:
            return job_config
        # No default for this job_type — preserve the historical error messages.
        if job_type == "embedding_job":
            raise ValueError("No default embedding job configuration available")
        raise ValueError(f"Unsupported job type: {job_type}")


# Note: Helper functions like _should_skip_stage, _check_cancellation, and _extract_previous_result
# have been refactored into the BaseTaskHandler class (src/tasks/base.py) and are now used
# as methods by the task handlers.
