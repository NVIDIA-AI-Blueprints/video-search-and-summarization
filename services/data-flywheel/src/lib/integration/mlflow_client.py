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

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import mlflow
import mlflow.exceptions
import pandas as pd
import requests

from src.api.models import WorkloadClassification
from src.config import MLflowConfig
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.mlflow")


class MLflowClient:
    """Client for MLflow integration with Data Flywheel Blueprint."""

    def __init__(self, mlflow_config: MLflowConfig):
        """Initialize the MLflow client.

        Args:
            mlflow_config: MLflow configuration
        """
        self.config = mlflow_config
        if self.config.enabled:
            mlflow.set_tracking_uri(self.config.tracking_uri)

    def download_and_process_eval(
        self, eval_id: str, nmp_eval_uri: str, save_dir: Path, model: str, eval_type: str
    ) -> Path | None:
        """
        Downloads evaluation results and extracts ZIP contents to save_dir.

        Args:
            eval_id: Evaluation ID
            nmp_eval_uri: NMP evaluation URI
            save_dir: Directory to save results
            model: Model name
            eval_type: Evaluation type

        Returns:
            Path to the processed results file or None if failed
        """
        try:
            # Create output directory
            save_dir.mkdir(parents=True, exist_ok=True)

            # Download results
            url = f"{nmp_eval_uri}/download-results"
            response = requests.get(url, headers={"accept": "application/json"}, timeout=30)

            if not response.ok:
                logger.error(f"Download failed [{response.status_code}]: {response.text}")
                return None

            # Save ZIP file
            zip_path = save_dir / f"result_{eval_id}.zip"
            zip_path.write_bytes(response.content)
            logger.info(f"Downloaded results to {zip_path}")

            # Extract ZIP contents
            with zipfile.ZipFile(zip_path) as zip_file:
                zip_file.extractall(save_dir)
                logger.info(f"Extracted {len(zip_file.namelist())} files")

            # Cleanup ZIP
            zip_path.unlink()

            # Find and rename results.json
            results_json = next(save_dir.glob("**/results.json"), None)
            if results_json:
                # Sanitize model name to avoid directory separators in filename
                safe_model_name = model.replace("/", "_").replace("\\", "_")
                new_name = save_dir / f"{safe_model_name}_{eval_type}.json"
                results_json.rename(new_name)
                logger.info(f"Renamed results to {new_name}")
                logger.info(f"Successfully processed {eval_id}")
                return new_name

            logger.error("Error: results.json not found in extracted files")
            logger.error(f"Failed to process {eval_id}")
            return None

        except Exception as e:
            logger.error(f"Error processing evaluation {eval_id}: {e!s}")
            return None

    def _load_results(self, file_path: Path) -> dict[str, Any]:
        """Load results from JSON file."""
        try:
            with open(file_path) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading results from {file_path}: {e!s}")
            raise

    def _safe_float(self, value: Any) -> float | None:
        """Safely convert value to float."""
        try:
            if value is None:
                return None
            return float(value)
        except (ValueError, TypeError):
            return None

    def _extract_metrics(
        self, item: dict[str, Any], workload_type: WorkloadClassification
    ) -> dict[str, Any]:
        """Extract metrics from evaluation item."""
        try:
            metrics = item.get("metrics") or {}
            sample = item.get("sample") or {}
            response = sample.get("response") or {}
            usage = response.get("usage") or {}

            if workload_type == WorkloadClassification.TOOL_CALLING:
                # Tool calling metrics
                tool_calling = (metrics.get("tool-calling-accuracy") or {}).get("scores") or {}
                correctness = (metrics.get("correctness") or {}).get("scores") or {}
                return {
                    "function_name_accuracy": self._safe_float(
                        (tool_calling.get("function_name_accuracy") or {}).get("value")
                    ),
                    "function_name_and_args_accuracy": self._safe_float(
                        (tool_calling.get("function_name_and_args_accuracy") or {}).get("value")
                    ),
                    "correctness_rating": self._safe_float(
                        (correctness.get("rating") or {}).get("value")
                    ),
                    "total_tokens": self._safe_float(usage.get("total_tokens")),
                    "prompt_tokens": self._safe_float(usage.get("prompt_tokens")),
                    "completion_tokens": self._safe_float(usage.get("completion_tokens")),
                }
            elif workload_type == WorkloadClassification.DPO:
                # DPO metrics
                dpo_scores = (metrics.get("dpo-preference-alignment") or {}).get("scores") or {}
                return {
                    "preference_score": self._safe_float(
                        (dpo_scores.get("preference_score") or {}).get("value")
                    ),
                    "total_tokens": self._safe_float(usage.get("total_tokens")),
                    "prompt_tokens": self._safe_float(usage.get("prompt_tokens")),
                    "completion_tokens": self._safe_float(usage.get("completion_tokens")),
                }
            else:
                # Generic metrics (llm-as-judge)
                judge_scores = (metrics.get("llm-judge") or {}).get("scores") or {}
                return {
                    "similarity": self._safe_float(
                        (judge_scores.get("similarity") or {}).get("value")
                    ),
                    "total_tokens": self._safe_float(usage.get("total_tokens")),
                    "prompt_tokens": self._safe_float(usage.get("prompt_tokens")),
                    "completion_tokens": self._safe_float(usage.get("completion_tokens")),
                }
        except Exception as e:
            logger.error(f"Error extracting metrics: {e!s}")
            return {}

    def _extract_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """Extract metadata from evaluation item."""
        try:
            sample = item.get("sample") or {}
            response = sample.get("response") or {}
            item_data = item.get("item") or {}
            return {
                "model": response.get("model"),
                "workload_id": item_data.get("workload_id"),
                "client_id": item_data.get("client_id"),
                "timestamp": item_data.get("timestamp"),
            }
        except Exception as e:
            logger.error(f"Error extracting metadata: {e!s}")
            return {}

    def upload_result(
        self,
        results_path: Path,
        flywheel_run_id: str,
        model_name: str,
        eval_type: str,
        workload_type: WorkloadClassification = WorkloadClassification.GENERIC,
    ) -> str | None:
        """
        Upload evaluation results to MLflow.

        Args:
            results_path: Path to results file
            flywheel_run_id: Flywheel run ID
            model_name: Model name
            eval_type: Evaluation type

        Returns:
            MLflow run URL or None if failed
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        try:
            # Parse experiment name and run name
            experiment_name = f"{self.config.experiment_name_prefix}-{flywheel_run_id}"
            run_name = f"{model_name.replace('/', '_')}_{eval_type}"

            # Get or create experiment
            experiment = mlflow.get_experiment_by_name(experiment_name)
            if experiment is None:
                experiment_id = mlflow.create_experiment(
                    experiment_name, artifact_location=self.config.artifact_location
                )
                logger.info(f"Created new experiment: {experiment_name}")
            else:
                experiment_id = experiment.experiment_id
                logger.info(f"Using existing experiment: {experiment_name} (ID: {experiment_id})")

            # Load results
            results = self._load_results(results_path)

            # Group by model (if needed)
            # Determine task key based on workload type
            if workload_type == WorkloadClassification.TOOL_CALLING:
                task_key = "custom-tool-calling"
            elif workload_type == WorkloadClassification.DPO:
                task_key = "dpo-judge"
            else:
                task_key = "llm-as-judge"

            model_evaluations = {}
            for item in results.get(task_key, []):
                metadata = self._extract_metadata(item)
                model_name_from_item = metadata.get("model", model_name)
                model_evaluations.setdefault(model_name_from_item, []).append(item)

            # Print summary of what will be uploaded
            logger.info("📊 Uploading evaluation results to MLflow:")
            logger.info(f"   📁 Experiment: {experiment_name}")
            logger.info(f"   📄 Results file: {results_path.name}")
            logger.info(f"   🤖 Models found: {len(model_evaluations)}")
            for model_name_in_item, evaluations in model_evaluations.items():
                logger.info(f"      - {model_name_in_item}: {len(evaluations)} evaluation samples")

            # Upload each model's results as a separate run (if multiple models)
            mlflow_run_url = None
            for model_name_in_item, evaluations in model_evaluations.items():
                this_run_name = (
                    f"{run_name}_{model_name_in_item.replace('/', '_')}"
                    if len(model_evaluations) > 1
                    else run_name
                )

                with mlflow.start_run(experiment_id=experiment_id, run_name=this_run_name):
                    mlflow.log_param("model", model_name_in_item)
                    mlflow.log_param("run_name", run_name)
                    mlflow.log_param("experiment_name", experiment_name)
                    mlflow.log_param("flywheel_run_id", flywheel_run_id)
                    mlflow.log_param("eval_type", eval_type)

                    metrics_data = []
                    for item in evaluations:
                        metrics = self._extract_metrics(item, workload_type)
                        metadata = self._extract_metadata(item)
                        metrics_data.append({"timestamp": metadata.get("timestamp"), **metrics})

                    metrics_df = pd.DataFrame(metrics_data)
                    # Sort by timestamp if available, otherwise use natural order
                    if "timestamp" in metrics_df.columns and metrics_df["timestamp"].notna().any():
                        metrics_df = metrics_df.sort_values("timestamp")

                    # Log step-by-step metrics
                    for step, (_, row) in enumerate(metrics_df.iterrows()):
                        step_metrics = row.drop("timestamp").to_dict()
                        # Filter out None values
                        step_metrics = {k: v for k, v in step_metrics.items() if v is not None}
                        if step_metrics:
                            mlflow.log_metrics(step_metrics, step=step)

                    # Log summary statistics
                    summary_metrics = {}
                    if workload_type == WorkloadClassification.TOOL_CALLING:
                        metric_columns = [
                            "function_name_accuracy",
                            "function_name_and_args_accuracy",
                            "correctness_rating",
                            "total_tokens",
                            "prompt_tokens",
                            "completion_tokens",
                        ]
                    elif workload_type == WorkloadClassification.DPO:
                        metric_columns = [
                            "preference_score",
                            "total_tokens",
                            "prompt_tokens",
                            "completion_tokens",
                        ]
                    else:
                        metric_columns = [
                            "similarity",
                            "total_tokens",
                            "prompt_tokens",
                            "completion_tokens",
                        ]

                    for col in metric_columns:
                        if col in metrics_df.columns:
                            mean_val = metrics_df[col].mean()
                            if pd.notna(mean_val):
                                summary_metrics[f"mean_{col}"] = mean_val

                    summary_metrics["total_evaluations"] = len(evaluations)
                    mlflow.log_metrics(summary_metrics)

                    # Store the experiment URL for the first model (or only model)
                    if mlflow_run_url is None:
                        # Construct the experiment URL from tracking URI and experiment ID
                        tracking_uri = mlflow.get_tracking_uri()
                        if tracking_uri.endswith("/"):
                            tracking_uri = tracking_uri[:-1]
                        mlflow_run_url = f"{tracking_uri}/#/experiments/{experiment_id}"

                logger.info(f"✅ Uploaded run '{this_run_name}' to experiment '{experiment_name}'")

            return mlflow_run_url

        except Exception as e:
            logger.error(f"Error uploading results to MLflow: {e!s}")
            return None

    def upload_evaluation_results(
        self,
        job_id: str,
        nmp_eval_uri: str,
        flywheel_run_id: str,
        model_name: str,
        eval_type: str,
        workload_type: WorkloadClassification = WorkloadClassification.GENERIC,
    ) -> str | None:
        """
        Complete workflow to download and upload evaluation results to MLflow.

        Args:
            job_id: Job ID for the evaluation
            nmp_eval_uri: NMP evaluation URI
            flywheel_run_id: Flywheel run ID
            model_name: Model name
            eval_type: Evaluation type

        Returns:
            MLflow experiment URL or None if failed
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        temp_dir = None
        try:
            # Create temporary directory for results using artifact_location
            temp_dir = Path(self.config.artifact_location) / f"temp_mlflow_results_{job_id}"
            temp_dir.mkdir(parents=True, exist_ok=True)

            # Download and process evaluation results
            results_file = self.download_and_process_eval(
                eval_id=job_id,
                nmp_eval_uri=nmp_eval_uri,
                save_dir=temp_dir,
                model=model_name,
                eval_type=eval_type,
            )

            if results_file:
                # Upload to MLflow
                mlflow_uri = self.upload_result(
                    results_path=results_file,
                    flywheel_run_id=flywheel_run_id,
                    model_name=model_name,
                    eval_type=eval_type,
                    workload_type=workload_type,
                )
                logger.info(f"MLflow upload completed for {eval_type} evaluation")
                return mlflow_uri
            else:
                logger.warning(f"Failed to download results for MLflow upload: {job_id}")
                return None

        except Exception as e:
            logger.error(f"MLflow upload failed for {eval_type} evaluation: {e!s}")
            return None
        finally:
            # Clean up temporary directory
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                    logger.debug(f"Cleaned up temporary directory: {temp_dir}")
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary directory {temp_dir}: {e!s}")

    def upload_embedding_results(
        self,
        flywheel_run_id: str,
        model_name: str,
        eval_type: str,
        scores: dict[str, float],
    ) -> str | None:
        """
        Upload embedding evaluation results to MLflow.

        Unlike LLM evaluations, embedding evaluations produce aggregate metrics only,
        so we don't need to download and parse per-sample results.

        Args:
            flywheel_run_id: Flywheel run ID
            model_name: Model name
            eval_type: Evaluation type
            scores: Dictionary of aggregate metric scores

        Returns:
            MLflow experiment URL or None if failed
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        try:
            # Parse experiment name and run name
            experiment_name = f"{self.config.experiment_name_prefix}-{flywheel_run_id}"
            run_name = f"{model_name.replace('/', '_')}_{eval_type}"

            # Get or create experiment
            experiment = mlflow.get_experiment_by_name(experiment_name)
            if experiment is None:
                experiment_id = mlflow.create_experiment(
                    experiment_name, artifact_location=self.config.artifact_location
                )
                logger.info(f"Created new experiment: {experiment_name}")
            else:
                experiment_id = experiment.experiment_id
                logger.info(f"Using existing experiment: {experiment_name} (ID: {experiment_id})")

            # Print summary
            logger.info("📊 Uploading embedding evaluation results to MLflow:")
            logger.info(f"   📁 Experiment: {experiment_name}")
            logger.info(f"   🤖 Model: {model_name}")
            logger.info(f"   📊 Metrics: {list(scores.keys())}")

            # Upload results as a single run
            with mlflow.start_run(experiment_id=experiment_id, run_name=run_name):
                mlflow.log_param("model", model_name)
                mlflow.log_param("run_name", run_name)
                mlflow.log_param("experiment_name", experiment_name)
                mlflow.log_param("flywheel_run_id", flywheel_run_id)
                mlflow.log_param("eval_type", eval_type)
                mlflow.log_param("job_type", "embedding")

                # Log aggregate metrics
                # Filter out None values
                filtered_scores = {k: v for k, v in scores.items() if v is not None}
                if filtered_scores:
                    mlflow.log_metrics(filtered_scores)

            # Construct the experiment URL
            tracking_uri = mlflow.get_tracking_uri()
            if tracking_uri.endswith("/"):
                tracking_uri = tracking_uri[:-1]
            mlflow_run_url = f"{tracking_uri}/#/experiments/{experiment_id}"

            logger.info(f"✅ Uploaded run '{run_name}' to experiment '{experiment_name}'")
            return mlflow_run_url

        except Exception as e:
            logger.error(f"Error uploading embedding results to MLflow: {e!s}")
            return None

    # =========================================================================
    # AOI promotion: model registry + alias/tag scheme.
    # The Promotion Controller and HITL beat use these to register candidates
    # and drive the @candidate -> @shadow -> @canary -> @production alias trail.
    #
    # Version identity (F3 — Mongo is the counter, MLflow is best-effort):
    #   * The candidate/serving/Triton version is a monotonic INTEGER minted by
    #     DFW in Mongo (see ``src/tasks/promotion/serving_version.py``). Aliases,
    #     tags and the promotion record's ``candidate_version``/``baseline_version``
    #     all speak that integer. MLflow is NOT the version authority.
    #   * The DEFT run id is kept as lineage on the ``promotion.deft_run_id`` tag.
    #   * MLflow provides only the tracking/registry trail: ``register_candidate``
    #     records the serving integer on the ``promotion.version_label`` tag (so
    #     the MLflow UI shows 1, 2, 3) and the run id on ``promotion.deft_run_id``.
    # All methods fail SOFT: when MLflow is disabled or the registry errors, they
    # log and return a sensible empty value rather than raising into the caller —
    # a promotion must proceed on the Mongo-assigned integer even if MLflow is down.
    # =========================================================================

    def _registry_client(self) -> "mlflow.MlflowClient":
        """Construct an MLflow registry client honoring the tracking URI set in __init__."""
        return mlflow.MlflowClient()

    def register_candidate(
        self,
        model_name: str,
        deft_run_id: str,
        serving_version: str,
        artifact_uri: str,
        metrics: dict[str, float],
        tags: dict[str, str],
    ) -> bool:
        """Best-effort: record a candidate model version in the MLflow registry.

        MLflow is NOT the version authority (F3): the serving/Triton version is
        the ``serving_version`` integer DFW minted in Mongo. This records the
        tracking trail only — it stamps ``promotion.version_label`` with that
        integer (so the MLflow UI shows 1, 2, 3), keeps the DEFT run id on the
        ``promotion.deft_run_id`` tag for lineage, and binds the ``@candidate``
        alias to MLflow's own version number.

        Args:
            model_name: Registered model name.
            deft_run_id: The DEFT run id — stored as the ``promotion.deft_run_id``
                tag for lineage; NOT used as the serving version.
            serving_version: The DFW/Mongo-minted serving integer (e.g. ``"2"``);
                recorded as the ``promotion.version_label`` tag.
            artifact_uri: Source URI for the model version artifacts.
            metrics: Metrics to record; stored as ``metric.<name>`` version tags since
                MLflow model versions do not accept metrics directly.
            tags: Promotion tags (``promotion.state``, ``snapshot_id``, plus any
                extra keys) merged onto the model version.

        Returns:
            ``True`` when the version was recorded, ``False`` when MLflow is
            disabled or registration failed. The return value is best-effort and
            NOT used for versioning — the caller proceeds on the Mongo integer
            regardless (MLflow is a tracking trail, not the system of record).
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return False

        try:
            client = self._registry_client()

            # Ensure the registered model exists; ignore an already-exists error.
            try:
                client.create_registered_model(model_name)
                logger.info(f"Created registered model: {model_name}")
            except mlflow.exceptions.MlflowException as e:
                if getattr(e, "error_code", "") == "RESOURCE_ALREADY_EXISTS":
                    logger.debug(f"Registered model already exists: {model_name}")
                else:
                    raise

            # Assemble version tags: promotion.* + snapshot_id + deft_run_id lineage +
            # the serving-version label + metrics.
            version_tags: dict[str, str] = {str(k): str(v) for k, v in (tags or {}).items()}
            # Lineage: the DEFT run id rides its own queryable tag (provenance).
            version_tags["promotion.deft_run_id"] = str(deft_run_id)
            # The human-facing serving/Triton version (the DFW/Mongo integer) —
            # so the MLflow UI and any registry tooling show 1, 2, 3 rather than
            # MLflow's own internal version number.
            version_tags["promotion.version_label"] = str(serving_version)
            # The TRUE artifact locator always rides a tag: MLflow rejects
            # local/file:// values in `source` (security validation), and DEFT
            # returns file:// checkpoints — consumers (baseline resolution)
            # read this tag first, so the checkpoint chain never depends on
            # what `source` had to be mangled into to pass validation.
            version_tags["checkpoint_uri"] = str(artifact_uri)
            for metric_name, metric_value in (metrics or {}).items():
                version_tags[f"metric.{metric_name}"] = str(metric_value)

            # Create the model version; MLflow assigns the integer-string version number.
            try:
                model_version = client.create_model_version(
                    name=model_name,
                    source=artifact_uri,
                    tags=version_tags,
                )
            except mlflow.exceptions.MlflowException as e:
                if getattr(e, "error_code", "") != "INVALID_PARAMETER_VALUE":
                    raise
                # The source was rejected by MLflow's validation (e.g. a
                # file:// checkpoint on the DEFT host — a local path from the
                # server's perspective, only allowed inside a run's artifact
                # root). Fall back to registering THROUGH a tracking run: log
                # a small pointer document as the run artifact and register
                # the run's artifact root as the version source. The true
                # checkpoint locator stays on the checkpoint_uri tag (which
                # get_version_source prefers), so baseline resolution is
                # unaffected.
                logger.warning(
                    "MLflow rejected the artifact source for %s (deft_run_id=%s); "
                    "registering via a tracking-run pointer artifact (true "
                    "URI kept in the checkpoint_uri tag)",
                    model_name,
                    deft_run_id,
                )
                # Ensure a registry experiment exists (a fresh tracking server
                # may not even have the default experiment 0).
                mlflow.set_experiment(f"{self.config.experiment_name_prefix}-registry")
                with mlflow.start_run(run_name=f"register-{model_name}-{deft_run_id}") as run:
                    mlflow.log_dict(
                        {"checkpoint_uri": artifact_uri, "deft_run_id": str(deft_run_id)},
                        "checkpoint_ref.json",
                    )
                model_version = client.create_model_version(
                    name=model_name,
                    source=run.info.artifact_uri,
                    run_id=run.info.run_id,
                    tags=version_tags,
                )
            created_version = str(model_version.version)
            # Note: artifact_uri is intentionally omitted from logs; signed URLs/tokens
            # in the source URI can leak credentials into log sinks.
            logger.info(
                f"Recorded model version {model_name} v{created_version} "
                f"(serving_version={serving_version}, deft_run_id={deft_run_id})"
            )

            # Bind the @candidate alias to the freshly-created MLflow version number.
            client.set_registered_model_alias(model_name, "candidate", created_version)
            logger.info(f"Set alias {model_name}@candidate -> v{created_version}")

            return True
        except Exception as e:
            logger.error(
                f"Error registering candidate {model_name} (deft_run_id={deft_run_id}): {e!s}"
            )
            return False

    def set_alias(self, model_name: str, alias: str, version: str) -> None:
        """Move a registered-model alias to a version.

        Drives the @candidate -> @shadow -> @canary -> @production progression;
        @production always marks the live default, and rollback moves it back.
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        try:
            client = self._registry_client()
            client.set_registered_model_alias(model_name, alias, str(version))
            logger.info(f"Set alias {model_name}@{alias} -> v{version}")
            return None
        except Exception as e:
            logger.error(f"Error setting alias {model_name}@{alias} -> v{version}: {e!s}")
            return None

    def get_version_by_alias(self, model_name: str, alias: str) -> str | None:
        """Return the version currently bound to an alias, or None if not found."""
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        try:
            client = self._registry_client()
            model_version = client.get_model_version_by_alias(model_name, alias)
            return model_version.version
        except Exception as e:
            logger.debug(f"No version bound to {model_name}@{alias}: {e!s}")
            return None

    def get_serving_version_by_alias(self, model_name: str, alias: str) -> str | None:
        """Return the SERVING/Triton integer bound to an alias, or None.

        MLflow aliases bind to MLflow's own auto-assigned version *number*, but
        the serving/Triton version is the DFW/Mongo-minted integer recorded on
        the ``promotion.version_label`` tag (F3) — these differ (e.g. once the
        Mongo counter is seeded). This resolves the alias's model version and
        returns that tag, i.e. the value the serving stack actually keys on.

        Returns None when MLflow is disabled, the alias is unbound, or the tag
        is absent — under F3 a wrong baseline is worse than none (the controller
        then simply proceeds without a rollback/cleanup target).
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        try:
            client = self._registry_client()
            model_version = client.get_model_version_by_alias(model_name, alias)
            tags = getattr(model_version, "tags", None) or {}
            return tags.get("promotion.version_label") or None
        except Exception as e:
            logger.debug(f"No serving version bound to {model_name}@{alias}: {e!s}")
            return None

    def get_version_source(self, model_name: str, version: str) -> str | None:
        """Return a model version's ``source`` (artifact/checkpoint URI), or None.

        ``version`` is the MLflow integer-string version number (e.g. what
        :meth:`get_version_by_alias` returns) — used to resolve the deployed
        baseline's checkpoint for DEFT's ``config.model.checkpoint``.
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        try:
            client = self._registry_client()
            model_version = client.get_model_version(model_name, version)
            # Prefer the checkpoint_uri tag — the TRUE locator (see
            # register_candidate: `source` may carry a scheme-prefixed value
            # mangled to pass MLflow's local-path validation).
            tag_uri = (getattr(model_version, "tags", None) or {}).get("checkpoint_uri")
            if tag_uri:
                return tag_uri
            return model_version.source
        except Exception as e:
            logger.debug(f"No source for {model_name} v{version}: {e!s}")
            return None

    def set_promotion_tags(self, model_name: str, version: str, tags: dict[str, str]) -> None:
        """Set promotion/gate tags on a model version.

        Supports ``promotion.state``, ``promotion.pending_gate`` and per-gate
        ``gate.<g>.decision/by/at/note/eval`` tags.
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return None

        try:
            client = self._registry_client()
            for key, value in (tags or {}).items():
                client.set_model_version_tag(model_name, str(version), key, str(value))
            logger.debug(f"Set promotion tags {model_name} v{version}: {list(tags or {})}")
            return None
        except Exception as e:
            logger.error(f"Error setting promotion tags {model_name} v{version}: {e!s}")
            return None

    def get_promotion_tags(self, model_name: str, version: str) -> dict[str, str]:
        """Return promotion/gate tags for a model version (or {} on error/disabled)."""
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled")
            return {}

        try:
            client = self._registry_client()
            model_version = client.get_model_version(model_name, str(version))
            return dict(model_version.tags or {})
        except Exception as e:
            logger.debug(f"Could not read promotion tags {model_name} v{version}: {e!s}")
            return {}

    def cleanup_experiment(self, experiment_name: str) -> bool:
        """
        Clean up MLflow experiment and all its runs, including artifacts.

        Args:
            experiment_name: Name of the experiment to clean up

        Returns:
            True if cleanup was successful, False otherwise
        """
        if not self.config.enabled:
            logger.debug("MLflow integration is disabled, skipping cleanup")
            return True

        try:
            # Get experiment
            experiment = mlflow.get_experiment_by_name(experiment_name)
            if experiment is None:
                logger.info(f"Experiment {experiment_name} not found, nothing to clean up")
                return True

            # Clean up artifacts first
            artifact_location = Path(self.config.artifact_location)
            if artifact_location.exists():
                # Find and remove experiment artifacts
                experiment_artifacts = artifact_location / experiment.experiment_id
                if experiment_artifacts.exists():
                    try:
                        shutil.rmtree(experiment_artifacts)
                        logger.info(f"Cleaned up experiment artifacts: {experiment_artifacts}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to clean up experiment artifacts {experiment_artifacts}: {e!s}"
                        )

            # Delete experiment (this will delete all runs in the experiment)
            mlflow.delete_experiment(experiment.experiment_id)
            logger.info(f"Successfully deleted experiment: {experiment_name}")
            return True

        except Exception as e:
            logger.error(f"Error cleaning up MLflow experiment {experiment_name}: {e!s}")
            return False
