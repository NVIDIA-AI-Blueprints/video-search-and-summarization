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
import time
from types import SimpleNamespace
from typing import Any, ClassVar

from nemo_platform import APIStatusError, NeMoPlatform

from src.config import CustomizerConfig, CustomizerTarget, NIMConfig, TrainingConfig, settings
from src.lib.flywheel.cancellation import check_cancellation
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.nemo.customizer")

# In NMP 26.03 the Target concept was replaced by Model Entity and JobStatus
# vocabulary changed: "running" → "active", "failed" → "error".
# These maps translate to the legacy DFW vocabulary so existing consumers stay
# working without sweeping changes outside this module.
_LEGACY_STATUS_MAP = {
    "active": "running",
    "error": "failed",
}


def _to_legacy_status(status: str | None) -> str:
    """Translate a 26.03 PlatformJobStatus value to the legacy DFW vocabulary."""
    if not status:
        return ""
    return _LEGACY_STATUS_MAP.get(status, status)


class Customizer:
    def __init__(self):
        """
        Initialize the Customizer.
        """
        self.nemo_url = settings.system_config.nmp_config.nemo_base_url
        self.namespace = settings.system_config.nmp_config.nmp_namespace
        self.workspace = settings.system_config.nmp_config.workspace
        assert self.nemo_url, "nemo_base_url must be set in config"

        # Initialize NeMo Platform SDK client (replaces NeMoMicroservices)
        self.nemo_client = NeMoPlatform(
            base_url=self.nemo_url,
            inference_base_url=settings.system_config.nmp_config.nim_base_url,
            workspace=self.workspace,
        )

    # NMP 26.03 narrowed the precision vocabulary on `training.{sft,dpo}.precision`
    # to {fp8, bf16, fp16, fp32}. The DFW config / 25.12 SDK used PyTorch-Lightning
    # style aliases like `bf16-mixed`; translate those back to the plain dtype so
    # the validator accepts them.
    _PRECISION_ALIASES: ClassVar[dict[str, str]] = {
        "bf16-mixed": "bf16",
        "fp16-mixed": "fp16",
        "fp32-mixed": "fp32",
        "fp8-mixed": "fp8",
    }

    @classmethod
    def _normalize_precision(cls, precision: str | None) -> str | None:
        if precision is None:
            return None
        return cls._PRECISION_ALIASES.get(precision, precision)

    def _build_training_spec(
        self, training_config: TrainingConfig, customizer_config: CustomizerConfig
    ) -> dict[str, Any]:
        """Build the NMP 26.03 ``spec.training`` block from DFW training/customizer configs."""
        training: dict[str, Any] = {
            "type": training_config.training_type,
            "epochs": training_config.epochs,
            "batch_size": training_config.batch_size,
            "learning_rate": training_config.learning_rate,
            "max_seq_length": customizer_config.max_seq_length,
            "precision": self._normalize_precision(training_config.training_precision),
        }

        # Parallelism block consolidates what was spread across config training_options.
        parallelism: dict[str, Any] = {
            "num_gpus_per_node": customizer_config.gpus,
            "num_nodes": customizer_config.num_nodes,
            "tensor_parallel_size": training_config.tensor_parallel_size,
            "data_parallel_size": training_config.data_parallel_size,
        }
        if training_config.pipeline_parallel_size:
            parallelism["pipeline_parallel_size"] = training_config.pipeline_parallel_size
        if training_config.use_sequence_parallel:
            parallelism["use_sequence_parallel"] = training_config.use_sequence_parallel
        if training_config.micro_batch_size:
            parallelism["micro_batch_size"] = training_config.micro_batch_size
        training["parallelism"] = parallelism

        if training_config.warmup_steps:
            training["warmup_steps"] = training_config.warmup_steps
        if training_config.weight_decay is not None:
            training["weight_decay"] = training_config.weight_decay
        if training_config.min_learning_rate is not None:
            training["min_learning_rate"] = training_config.min_learning_rate

        # PEFT (LoRA) — when finetuning_type is lora/lora_merged.
        # ``lora_merged`` keeps the legacy DFW semantics where the adapter is
        # merged back into the base weights after training (required for
        # embedding NIMs that load ONNX). ``lora`` produces a standalone
        # adapter. NMP 26.03 takes ``merge: True/False`` on the peft block.
        if (
            training_config.finetuning_type in ("lora", "lora_merged")
            and training_config.lora is not None
        ):
            training["peft"] = {
                "type": "lora",
                "rank": training_config.lora.adapter_dim,
                "alpha": training_config.lora.alpha,
                "dropout": training_config.lora.adapter_dropout,
                "merge": training_config.finetuning_type == "lora_merged",
            }

        # DPO-specific fields live directly on the training dict in NMP 26.03
        # (DpoTrainingParam: ref_policy_kl_penalty, preference_loss_weight, etc.
        # are top-level alongside type="dpo"). Older code nested them under
        # training["dpo"], which the server silently dropped.
        if training_config.training_type == "dpo" and training_config.dpo is not None:
            training["ref_policy_kl_penalty"] = training_config.dpo.ref_policy_kl_penalty
            training["preference_loss_weight"] = training_config.dpo.preference_loss_weight
            training["preference_average_log_probs"] = (
                training_config.dpo.preference_average_log_probs
            )
            training["sft_loss_weight"] = training_config.dpo.sft_loss_weight
            training["sft_average_log_probs"] = training_config.dpo.sft_average_log_probs

        # Pod spec is now passed via override_config / k8s_nim_operator_config in deployment.
        # Keep customizer_config.pod_spec for downstream callers but it no longer maps cleanly into spec.
        # dataset_parameters intentionally not set here — it is a top-level field on
        # the customization job spec in 26.03, not on training. ``start_training_job``
        # places it correctly.
        return training

    def _manage_target(self, target: CustomizerTarget | str, flywheel_run_id: str) -> str:
        """Ensure the model entity for ``target`` exists and return the workspace/name path.

        Targets were a Customizer concept in <=25.12; in 26.03 they were unified into
        ``client.models`` (model entities). DFW callers still use the same ``CustomizerTarget``
        config schema, so this helper resolves the entity (or creates it from a fileset) and
        returns the canonical path the customization job should reference.

        NMP 26.03 ships base NIM/NeMo models as pre-registered entities under the
        ``system`` workspace (e.g. ``system/meta-llama-3-2-1b-instruct``). DFW
        config carries them in the legacy ``namespace/name@version`` form
        (``meta/llama-3.2-1b-instruct@2.0``). When the caller passes a bare
        string we fall back to the ``system`` workspace and apply the same
        name-normalization the inference gateway uses (``/`` / ``.`` / ``@`` →
        ``-``) before giving up.
        """
        if not target:
            error_msg = "No target provided"
            logger.error(error_msg)
            raise ValueError(error_msg)

        full_target_path = target.name if isinstance(target, CustomizerTarget) else target
        namespace, target_name = self._parse_model_path(full_target_path)

        # Check if model entity already exists at the path as-given.
        target_info = self.get_target_info(full_target_path)
        if target_info is not None:
            # If config carries a CustomizerTarget with a ``model_uri`` but
            # the existing entity has no ``fileset``, it's an auto-discovered
            # provider entity (created by NMP when the base NIM was deployed)
            # — those have no backing weights, so the customizer pod will
            # crash with ``Unsupported model type: . Cannot infer model class
            # from /var/run/scratch/job/model``. Replace the auto-discovered
            # entity with one backed by the configured fileset.
            needs_fileset = (
                isinstance(target, CustomizerTarget)
                and target.model_uri
                and not getattr(target_info, "fileset", None)
            )
            if needs_fileset:
                logger.info(
                    f"Model entity {full_target_path} exists but has no fileset; "
                    f"replacing with config-driven fileset from {target.model_uri}"
                )
                try:
                    self.nemo_client.models.delete(name=target_name, workspace=namespace)
                    logger.info(f"Deleted auto-discovered entity {full_target_path}")
                except Exception as e:
                    logger.warning(
                        f"Could not delete auto-discovered entity {full_target_path}: {e}"
                    )
                # Fall through to create_target below.
            else:
                logger.info(f"Model entity {full_target_path} found")
                self.wait_for_target_ready(full_target_path, flywheel_run_id)
                logger.info(f"Model entity {full_target_path} is ready")
                return f"{namespace}/{target_name}" if namespace else target_name

        # Fallback search: base models are exposed under ``system`` with NMP-normalized
        # names. Try a few candidates derived from the configured target.
        # Strip an optional ``@version`` suffix BEFORE normalization so we match the
        # bare base-model entity name (which has no version segment).
        target_name_no_version = target_name.split("@", 1)[0]
        nmp_name_full = target_name.replace("/", "-").replace("@", "-").replace(".", "-")
        nmp_name_bare = target_name_no_version.replace("/", "-").replace(".", "-")
        candidates: list[tuple[str, str]] = []
        for ws_candidate in (self.workspace, "system"):
            for nm in (nmp_name_bare, nmp_name_full):
                if nm and (ws_candidate, nm) not in candidates:
                    candidates.append((ws_candidate, nm))
        # If the original namespace prefix encoded part of the model id (e.g.
        # ``meta`` in ``meta/llama-3.2-1b-instruct``), the entity name actually
        # fuses them: ``meta-llama-3-2-1b-instruct``. Try the fused form in
        # both workspace and system, with and without the @version segment.
        if namespace and namespace != self.workspace:
            for ws_candidate in (self.workspace, "system"):
                for nm in (
                    f"{namespace}-{nmp_name_bare}",
                    f"{namespace}-{nmp_name_full}",
                ):
                    if (ws_candidate, nm) not in candidates:
                        candidates.append((ws_candidate, nm))

        for ws_candidate, nm in candidates:
            try:
                entity = self.nemo_client.models.retrieve(name=nm, workspace=ws_candidate)
            except APIStatusError as e:
                if getattr(e, "status_code", None) == 404:
                    continue
                logger.warning(f"Error checking model entity {ws_candidate}/{nm}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Error checking model entity {ws_candidate}/{nm}: {e}")
                continue
            if entity is not None:
                resolved_path = f"{ws_candidate}/{nm}"
                logger.info(f"Resolved customizer target {full_target_path} → {resolved_path}")
                # If the config carries a ``CustomizerTarget`` with a
                # ``model_uri``, we expected to create our own workspace
                # entity backed by that fileset — but a fallback search hit
                # either (a) an auto-discovered workspace entity with
                # ``fileset: null`` (created by NMP when the base NIM was
                # deployed), or (b) a pre-shipped ``system/`` entity with
                # ``fileset: null`` too (just exposes the NVIDIA Build remote
                # endpoint). Neither has the actual checkpoint files the
                # customizer-automodel image needs to load —
                # ``/var/run/scratch/job/model`` stays empty and training
                # crashes with ``Unsupported model type: . Cannot infer
                # model class``. In both cases, fall through to
                # ``create_target`` so we install our own fileset-backed
                # entity at ``{workspace}/{normalized_name}`` and the
                # customizer can pull weights from the configured ``hf://``
                # or ``ngc://`` source.
                if (
                    isinstance(target, CustomizerTarget)
                    and target.model_uri
                    and not getattr(entity, "fileset", None)
                ):
                    logger.info(
                        f"{resolved_path} has no fileset; replacing with "
                        f"config-driven fileset from {target.model_uri}"
                    )
                    if ws_candidate == self.workspace:
                        try:
                            self.nemo_client.models.delete(name=nm, workspace=ws_candidate)
                        except Exception as e:
                            logger.warning(
                                f"Could not delete auto-discovered entity {resolved_path}: {e}"
                            )
                    # Force ``create_target`` to use the workspace path with
                    # the normalized name. ``create_target`` will sanitize
                    # again but we want consistency with the entity we just
                    # deleted (or with the workspace's equivalent of the
                    # system entity name).
                    namespace = self.workspace
                    target_name = nm
                    break
                # Skip ``wait_for_target_ready`` for system entities — they
                # ship pre-ready (and ``status`` doesn't apply).
                if ws_candidate != self.workspace:
                    return resolved_path
                self.wait_for_target_ready(resolved_path, flywheel_run_id)
                return resolved_path

        if isinstance(target, str):
            error_msg = (
                f"Model entity {full_target_path} not found. Provide CustomizerTarget details "
                "to create it."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Create model entity from CustomizerTarget params.
        # ``create_target`` sanitizes ``target_name`` to the NMP-regex-valid
        # form (lowercase, ``/``/``.``/``@`` → ``-``) and creates the entity
        # at ``{namespace}/{sanitized}``. The original config target string
        # (``full_target_path``) may include ``/``, dots, or an ``@version``
        # suffix — none of those will exist as paths in NMP, so we wait on
        # the actually-created path.
        logger.info(f"Model entity {target_name} not found. Creating...")
        self.create_target(namespace=namespace, target_name=target_name, params=target)

        created_name = target_name.lower().replace("/", "-").replace("@", "-").replace(".", "-")
        while "--" in created_name:
            created_name = created_name.replace("--", "-")
        created_name = created_name.strip("-")
        created_path = f"{namespace}/{created_name}"

        logger.info(f"Waiting for model entity {created_path} to be ready...")
        self.wait_for_target_ready(created_path, flywheel_run_id)
        logger.info(f"Model entity {created_path} is ready")

        return created_path

    def create_customizer_config(
        self,
        base_model: str,
        customizer_config: CustomizerConfig,
        training_config: TrainingConfig,
        flywheel_run_id: str,
    ) -> str:
        """Resolve the model entity (formerly Target) for the job.

        Customization configs were removed in NMP 26.03 — training params are inlined into
        the job spec. This method retains the legacy signature so DFW callers don't change,
        and returns the canonical model entity path that ``start_training_job`` references.
        """
        logger.info("Resolving model entity (target) for customization job")
        target_path = self._manage_target(customizer_config.target, flywheel_run_id)
        logger.info(f"Resolved model entity: {target_path}")
        return target_path

    def start_training_job(
        self,
        name: str,
        base_model: str,
        output_model_name: str,
        dataset_name: str,
        training_config: TrainingConfig,
        nim_config: NIMConfig,
        flywheel_run_id: str,
    ) -> tuple[str, str]:
        """Start a customization job against NMP 26.03.

        Returns a tuple of (job_name, customized_model_name). The first element is the
        ``CustomizationJob.name`` (used for retrieve/cancel/get_status/get_logs) since
        26.03 keys jobs by ``workspace``+``name`` rather than the opaque ``id`` 25.12 used.
        """
        customizer_config = nim_config.customizer_configs
        if not customizer_config:
            raise ValueError(f"No customizer config found for base model {base_model}")

        # Resolve the model entity referenced by the customization job.
        target_path = self.create_customizer_config(
            base_model, customizer_config, training_config, flywheel_run_id
        )

        try:
            spec: dict[str, Any] = {
                "model": target_path,
                "dataset": f"fileset://{self.workspace}/{dataset_name}",
                "training": self._build_training_spec(training_config, customizer_config),
                "output": {"name": output_model_name},
            }

            # dataset_parameters is a top-level spec field in NMP 26.03 (see the
            # tutorial at docs/customizer/tutorials/format-training-dataset.md
            # — "Shared Tools" / hard-negatives use this slot).
            if training_config.dataset_parameters and (
                training_config.dataset_parameters.num_hard_negatives
                or training_config.dataset_parameters.in_batch_negatives
            ):
                spec["dataset_parameters"] = {
                    "num_hard_negatives": training_config.dataset_parameters.num_hard_negatives,
                    "in_batch_negatives": training_config.dataset_parameters.in_batch_negatives,
                }

            customization = self.nemo_client.customization.jobs.create(
                name=name,
                workspace=self.workspace,
                spec=spec,
            )
            logger.info(f"Created customization job: {customization.name}")
            customized_model = f"{self.workspace}/{output_model_name}"
            return customization.name, customized_model
        except Exception as e:
            error_msg = f"Failed to start training job: {e}"
            logger.error(error_msg)
            raise Exception(error_msg) from e

    def get_job_uri(self, job_id: str) -> str:
        """Workspace-scoped customization job URI (26.03)."""
        return f"{self.nemo_url}/apis/customization/v2/workspaces/{self.workspace}/jobs/{job_id}"

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Get job status from NMP 26.03 and translate to the legacy DFW dict shape.

        Legacy keys preserved for callers: ``status``, ``status_logs``, ``percentage_done``,
        ``epochs_completed``, ``steps_completed``.
        """
        try:
            status_response = self.nemo_client.customization.jobs.get_status(
                name=job_id, workspace=self.workspace
            )
            status_dict = (
                status_response.model_dump()
                if hasattr(status_response, "model_dump")
                else dict(status_response)
            )
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            raise

        legacy_status = _to_legacy_status(status_dict.get("status"))

        # Walk steps/tasks to derive a percentage and a flat status_logs list.
        # ``percentage_done`` should reflect the CURRENTLY ACTIVE step's
        # progress, not the max across all steps — otherwise once the early
        # ``model-and-dataset-download`` step completes at 100%, the later
        # training step's progress display jumps straight to 100% and stays
        # there from step 0 onwards. The customization-training-job step is
        # the one we care about for training-loop visibility, so prefer its
        # progress when it is in progress; otherwise fall back to the last
        # in-progress step.
        epochs_completed = 0
        steps_completed = 0
        status_logs: list[dict[str, Any]] = []
        training_progress: float | None = None
        active_progress: float | None = None

        for step in status_dict.get("steps") or []:
            step_status = step.get("status")
            step_name = step.get("name") or ""
            step_details = step.get("status_details") or {}
            if step_details:
                status_logs.append({"detail": step_details.get("message"), "message": step_status})
            for task in step.get("tasks") or []:
                td = task.get("status_details") or {}
                # NMP 26.03 is inconsistent about which field carries the
                # per-task progress: the download step emits ``progress_pct``,
                # the customization-training-job step emits ``percentage_done``.
                # Prefer whichever is populated.
                progress_pct = None
                for key in ("progress_pct", "percentage_done"):
                    raw = td.get(key)
                    if raw is None:
                        continue
                    try:
                        progress_pct = float(raw)
                        break
                    except (TypeError, ValueError):
                        progress_pct = None
                if progress_pct is not None:
                    if step_status not in ("completed", "succeeded"):
                        active_progress = progress_pct
                    if step_name == "customization-training-job":
                        training_progress = progress_pct
                if td.get("epoch") is not None:
                    epochs_completed = max(epochs_completed, int(td["epoch"]))
                if td.get("step") is not None:
                    steps_completed = max(steps_completed, int(td["step"]))

        percentage = (
            training_progress
            if training_progress is not None
            else active_progress
            if active_progress is not None
            else 0.0
        )

        if status_dict.get("error_details"):
            status_logs.append({"detail": str(status_dict["error_details"]), "message": "error"})

        return {
            **status_dict,
            "status": legacy_status,
            "status_logs": status_logs,
            "percentage_done": percentage,
            "epochs_completed": epochs_completed,
            "steps_completed": steps_completed,
        }

    def _parse_model_path(self, model_path: str) -> tuple[str, str]:
        """Parse 'workspace/name' (or 'namespace/name' legacy) into (workspace, name)."""
        if "/" in model_path:
            parts = model_path.split("/", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                error_msg = f"Invalid model path format '{model_path}'. Expected 'workspace/name'."
                logger.error(error_msg)
                raise ValueError(error_msg)
            return parts[0], parts[1]
        return self.workspace, model_path

    def get_target_info(self, target: str) -> Any | None:
        """Retrieve a model entity (post-25.12 replacement for Target).

        Returns a SimpleNamespace exposing ``status``, ``enabled``, ``name``, ``id``,
        ``num_parameters``, ``precision``, ``description`` so downstream callers in
        ``src/tasks/tasks.py`` continue to work unchanged.
        """
        workspace, name = self._parse_model_path(target)
        try:
            entity = self.nemo_client.models.retrieve(name=name, workspace=workspace)
        except APIStatusError as e:
            if getattr(e, "status_code", None) == 404:
                return None
            logger.error(f"Failed to get model entity info: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get model entity info: {e}")
            return None

        spec = getattr(entity, "spec", None)
        spec_dict = (
            spec.model_dump() if spec is not None and hasattr(spec, "model_dump") else (spec or {})
        )

        return SimpleNamespace(
            id=getattr(entity, "id", None),
            name=getattr(entity, "name", name),
            namespace=getattr(entity, "workspace", workspace),
            workspace=getattr(entity, "workspace", workspace),
            description=getattr(entity, "description", None) or spec_dict.get("description"),
            base_model=spec_dict.get("base_model") or getattr(entity, "base_model", None),
            model_uri=spec_dict.get("model_uri"),
            hf_endpoint=spec_dict.get("hf_endpoint"),
            tokenizer=spec_dict.get("tokenizer") or {},
            num_parameters=spec_dict.get("num_parameters"),
            precision=spec_dict.get("precision"),
            model_path=spec_dict.get("model_path"),
            target_type=spec_dict.get("target_type", "nemo"),
            # Model entities don't have an ``enabled`` flag; treat presence as enabled.
            enabled=True,
            # ``ready`` once the platform finishes background work on the
            # entity. Two cases produce a usable entity:
            # (a) user-created with a ``fileset`` — platform populates ``spec``
            #     from the fileset's HF metadata (LlamaForCausalLM etc).
            # (b) auto-discovered from a NIM deployment — ``spec`` stays null
            #     but ``model_providers`` lists the backing deployment in
            #     ``<workspace>/<deployment-name>`` form, and the model is
            #     servable via the inference gateway at that path.
            # For (b) we also verify the provider points at the SAME entity
            # path (workspace/name) we just looked up — that way a stale
            # auto-discovered entity referencing a deleted/different
            # deployment doesn't count as ready.
            status=(
                "ready"
                if (
                    spec_dict
                    or (
                        getattr(entity, "model_providers", None)
                        and f"{workspace}/{name}" in entity.model_providers
                    )
                )
                else "pending"
            ),
        )

    def create_target(
        self,
        namespace: str,
        target_name: str,
        params: CustomizerTarget,
    ) -> None:
        """Create the customization-base fileset + model entity from
        ``CustomizerTarget`` params.

        NMP 26.03's customizer pod loads base-model weights from the workspace
        fileset its model entity references. DFW config carries enough info
        (``model_uri``) to provision both:

        * ``hf://<repo_id>``  → ``HuggingfaceStorageConfig`` fileset, optional
          ``token_secret`` for gated repos.
        * ``ngc://<org>/<team>/<asset>:<version>``  → ``NGCStorageConfig`` fileset
          using the workspace's ``ngc-api-key`` secret.

        If ``model_uri`` is omitted, falls back to the legacy behavior where
        the fileset is assumed pre-created at ``<workspace>/<base_model>``.

        The entity name is the sanitized hyphen-only form so it matches NMP's
        ``^[a-z](?!.*--)[a-z0-9\\-@.+_]{1,62}(?<!-)$`` regex; the original
        ``target_name`` (which may contain ``@`` versions or dots) is preserved
        in the description.
        """
        try:
            if not params.base_model:
                raise ValueError("base_model is required for target creation")

            # Auto-create the fileset when model_uri is a recognized
            # ``hf://`` or ``ngc://`` reference. ``ConflictError`` (409)
            # means the fileset already exists — reuse it.
            uri = params.model_uri or ""
            # NMP entity / fileset names must match
            # ``^[a-z](?!.*--)[a-z0-9\-@.+_]{1,62}(?<!-)$`` — collapse ``/``,
            # ``@``, ``.`` to ``-`` (no consecutive dashes, no trailing dash).
            fileset_name = target_name.lower().replace("/", "-").replace("@", "-").replace(".", "-")
            while "--" in fileset_name:
                fileset_name = fileset_name.replace("--", "-")
            fileset_name = fileset_name.strip("-")
            fileset_ref = f"{namespace}/{fileset_name}"

            storage: dict[str, Any] | None = None
            if uri.startswith("hf://"):
                repo_id = uri[len("hf://") :]
                storage = {
                    "type": "huggingface",
                    "repo_id": repo_id,
                    "repo_type": "model",
                    "token_secret": "hf-token",
                }
                if params.hf_endpoint:
                    storage["endpoint"] = params.hf_endpoint
            elif uri.startswith("ngc://"):
                # Expected form: ``ngc://<org>/<team>/<asset>[:<version>]``.
                rest = uri[len("ngc://") :]
                version: str | None = None
                if ":" in rest:
                    rest, version = rest.rsplit(":", 1)
                parts = rest.split("/")
                if len(parts) != 3:
                    raise ValueError(
                        f"NGC model_uri must be 'ngc://<org>/<team>/<asset>[:<ver>]', got {uri!r}"
                    )
                org, team, asset = parts
                storage = {
                    "type": "ngc",
                    "org": org,
                    "team": team,
                    "target": asset,
                    "target_type": "model",
                    "api_key_secret": "ngc-api-key",
                }
                if version is not None:
                    storage["version"] = version

            if storage is not None:
                try:
                    self.nemo_client.files.filesets.create(
                        workspace=namespace,
                        name=fileset_name,
                        description=f"Customization base fileset for {params.base_model}",
                        storage=storage,
                        cache=True,
                    )
                    logger.info(f"Created customization base fileset: {fileset_ref}")
                except APIStatusError as e:
                    if getattr(e, "status_code", None) != 409 and "already exists" not in str(
                        getattr(e, "message", "")
                    ):
                        raise
                    logger.info(f"Customization base fileset already exists: {fileset_ref}")
            else:
                # Legacy fallback: caller pre-created a fileset somewhere.
                fileset_ref = uri or f"{namespace}/{params.base_model}"
                logger.info(
                    f"No hf:// or ngc:// model_uri — assuming pre-existing fileset {fileset_ref}"
                )

            entity = self.nemo_client.models.create(
                workspace=namespace,
                name=fileset_name,
                fileset=fileset_ref,
                trust_remote_code=getattr(params, "trust_remote_code", False) or False,
                description=params.description
                or f"Customization base model entity for {params.base_model}",
            )
            logger.info(
                f"Successfully created model entity {namespace}/{fileset_name} "
                f"(base: {params.base_model}, fileset: {fileset_ref}) — id: {entity.name}"
            )
        except APIStatusError as e:
            if getattr(e, "status_code", None) == 409:
                logger.info(f"Model entity {namespace}/{fileset_name} already exists; reusing")
                return
            error_msg = f"Failed to create model entity {target_name}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg) from e
        except Exception as e:
            error_msg = f"Failed to create model entity {target_name}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg) from e

    def enable_target(self, namespace: str, target_name: str) -> None:
        """No-op in 26.03 — model entities don't have an enable/disable flag.

        Retained for API compatibility with the Target lifecycle in 25.12.
        """
        logger.info(
            f"enable_target({namespace}/{target_name}) is a no-op in NMP 26.03 "
            "(model entities are always enabled when present)"
        )

    def get_customized_model_info(self, customized_model: str) -> dict[str, Any]:
        """Retrieve a customized model entity by 'workspace/name'."""
        try:
            workspace, model_name = self._parse_model_path(customized_model)
            model = self.nemo_client.models.retrieve(name=model_name, workspace=workspace)
            return model.model_dump() if hasattr(model, "model_dump") else dict(model)
        except Exception as e:
            logger.error(f"Failed to get model info: {e}")
            raise

    def wait_for_target_ready(
        self, target: str, flywheel_run_id: str, timeout: int = 3600, check_interval: int = 30
    ) -> Any:
        """Wait for a model entity to be retrievable with a populated ``spec``."""
        start_time = time.time()

        while True:
            try:
                check_cancellation(flywheel_run_id)
            except Exception as e:
                logger.info(f"Target wait cancelled: {e}")
                raise e

            try:
                target_info = self.get_target_info(target)
                if target_info is not None and target_info.status == "ready":
                    return target_info
            except Exception as e:
                logger.error(f"Failed to get model entity info: {e}")
                raise

            if time.time() - start_time > timeout:
                msg = f"Model entity {target} did not sync within {timeout} seconds"
                logger.error(msg)
                raise TimeoutError(msg)
            time.sleep(check_interval)

    def wait_for_model_sync(
        self,
        customized_model: str,
        flywheel_run_id: str,
        check_interval: int = 30,
        timeout: int = 3600,
    ) -> dict[str, Any]:
        """Wait for the customized model to appear in the inference models list."""

        start_time = time.time()

        while True:
            try:
                check_cancellation(flywheel_run_id)
            except Exception as e:
                logger.info(f"Model sync wait cancelled: {e}")
                raise e

            try:
                models = self.nemo_client.inference.models.list(workspace=self.workspace)
                # 26.03 list response is paginated — ``data`` for raw page or iterable.
                model_iter = getattr(models, "data", None) or list(models)
                model_names = [
                    getattr(m, "id", None) or getattr(m, "name", None) for m in model_iter
                ]

                if customized_model in model_names:
                    return {"status": "synced", "model_id": customized_model}
            except Exception as e:
                logger.error(f"Failed to get models list: {e}")
                raise

            if time.time() - start_time > timeout:
                msg = f"Model {customized_model} did not sync within {timeout} seconds"
                logger.error(msg)
                raise TimeoutError(msg)
            time.sleep(check_interval)

    def wait_for_customization(
        self,
        job_id: str,
        flywheel_run_id: str,
        check_interval: int = 30,
        timeout: int = 3600,
        progress_callback=None,
    ) -> dict[str, Any]:
        """Poll a customization job until it terminates.

        ``job_id`` is the 26.03 job ``name`` (returned by ``start_training_job``).
        Statuses are translated to the legacy DFW vocabulary by ``get_job_status``.
        """
        start_time = time.time()
        last_progress = 0.0

        while True:
            try:
                check_cancellation(flywheel_run_id)
            except Exception as e:
                logger.info(f"Customization wait cancelled: {e}")
                raise e

            status_response = self.get_job_status(job_id)
            current_status = status_response.get("status", "")
            status_logs = status_response.get("status_logs", [])

            if current_status == "completed":
                if progress_callback:
                    progress_callback(
                        {
                            "progress": 100.0,
                            "epochs_completed": status_response.get("epochs_completed", 0),
                            "steps_completed": status_response.get("steps_completed", 0),
                        }
                    )
                return status_response

            elif current_status == "failed":  # translated from 26.03 "error"
                error_detail = "No error details available"
                for log in reversed(status_logs):
                    if log.get("detail"):
                        error_detail = log["detail"]
                        break

                error_message = f"Job {job_id} failed: {error_detail}"
                logger.error(error_message)
                if progress_callback:
                    progress_callback({"progress": 0.0, "error": error_message})
                raise Exception(error_message)

            elif current_status == "cancelled":
                error_message = f"Job {job_id} was cancelled"
                logger.error(error_message)
                if progress_callback:
                    progress_callback({"progress": 0.0, "error": error_message})
                raise Exception(error_message)

            elif current_status == "running":  # translated from 26.03 "active"
                progress = float(status_response.get("percentage_done", 0))
                epochs_completed = status_response.get("epochs_completed", 0)
                steps_completed = status_response.get("steps_completed", 0)

                logger.info(
                    f"Job {job_id} is running - Progress: {progress}%, "
                    f"Epochs: {epochs_completed}, Steps: {steps_completed}"
                )

                if progress > last_progress:
                    start_time = time.time()
                    last_progress = progress

                if progress_callback:
                    progress_callback(
                        {
                            "progress": progress,
                            "epochs_completed": epochs_completed,
                            "steps_completed": steps_completed,
                        }
                    )

            elif current_status not in (
                "pending",
                "created",
                "cancelling",
                "paused",
                "pausing",
                "resuming",
            ):
                if progress_callback:
                    progress_callback(
                        {"progress": 0.0, "error": f"Unknown job status '{current_status}'"}
                    )
                msg = f"Warning: Unknown job status '{current_status}'"
                logger.error(msg)
                raise Exception(msg)

            if time.time() - start_time > timeout:
                if status_logs and status_logs[-1].get("message") == "NotEnoughResources":
                    error_message = f"Job {job_id} failed due to insufficient resources"
                    logger.error(error_message)
                    if progress_callback:
                        progress_callback({"progress": 0.0, "error": error_message})
                    raise Exception(error_message)

                error_message = f"Job {job_id} stalled for more than {timeout} seconds"
                if progress_callback:
                    progress_callback({"progress": 0.0, "error": error_message})
                logger.error(error_message)
                raise TimeoutError(error_message)

            time.sleep(check_interval)

    def delete_customization_config(self, config_name: str) -> None:
        """No-op in 26.03 — customization configs were removed; training is inlined into job spec."""
        logger.info(
            f"delete_customization_config({config_name}) is a no-op in NMP 26.03 "
            "(configs are inlined into the job spec)"
        )

    def delete_customized_model(self, model_name: str) -> None:
        """Delete a customized model entity from NMP."""
        model_id = f"{model_name}"

        try:
            self.get_customized_model_info(model_id)
        except Exception as e:
            msg = f"Model {model_id} not found or cannot be accessed: {e!s}"
            logger.error(msg)
            raise Exception(msg) from e

        try:
            workspace, name = self._parse_model_path(model_id)
            self.nemo_client.models.delete(name=name, workspace=workspace)
            logger.info(f"Successfully deleted model {model_id}")
        except Exception as e:
            logger.error(f"Failed to delete model {model_id}: {e}")
            raise

    def cancel_job(self, job_id: str) -> None:
        """Cancel a customization job by its 26.03 ``name``."""
        try:
            self.nemo_client.customization.jobs.cancel(name=job_id, workspace=self.workspace)
            logger.info(f"Successfully cancelled job {job_id}")
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            raise
