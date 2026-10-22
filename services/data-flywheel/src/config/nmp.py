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
"""NMP (NeMo Platform) config schemas + the NMP config loader.

Pure Pydantic contract for the NMP backend — no ``nemo_platform`` import, so the
module is generic/isolation-safe and can be imported from anywhere. ``Settings``
builds it via ``load_nmp_settings`` when ``backend.kind == "nmp"``.
"""

import os
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.config.base import JobConfig, SystemConfig, build_shared_system_kwargs
from src.lib.ingestion import IngestionConfig

__all__ = [
    "CustomEvaluation",
    "CustomizerConfig",
    "CustomizerTarget",
    "DPOConfig",
    "DataSplitConfig",
    "DatasetParametersConfig",
    "EmbeddingConfig",
    "EmbeddingDataSplitConfig",
    "EmbeddingEvalMetrics",
    "EmbeddingJobConfig",
    "EvaluationConfig",
    "ICLConfig",
    "LLMJobConfig",
    "LLMJudgeConfig",
    "LoRAConfig",
    "NIMConfig",
    "NMPConfig",
    "NmpSystemConfig",
    "SimilarityConfig",
    "TrainingConfig",
    "load_nmp_settings",
]


class NMPConfig(BaseModel):
    """Configuration for NMP (NeMo Platform 26.03+)"""

    datastore_base_url: str = Field(..., frozen=True)
    nemo_base_url: str = Field(..., frozen=True)
    nim_base_url: str = Field(..., frozen=True)
    # Retained for backward compatibility (used as default workspace if nmp_workspace is unset).
    nmp_namespace: str = Field("dwfbp", frozen=True)
    # Workspace replaces namespace as the primary scoping concept in NMP 26.03.
    nmp_workspace: str | None = Field(default=None, frozen=True)

    @property
    def workspace(self) -> str:
        return self.nmp_workspace or self.nmp_namespace


class DataSplitConfig(BaseModel):
    """Configuration for data split"""

    eval_size: int = Field(default=100, description="Size of evaluation set")
    val_ratio: float = Field(
        default=0.1,
        description="Validation ratio",
        ge=0,
        lt=1,
    )
    min_total_records: int = Field(default=50, description="Minimum total records")
    random_seed: int | None = Field(None, description="Random seed")
    limit: int | None = Field(default=None, description="Limit on number of records to evaluate")
    parse_function_arguments: bool = Field(
        default=True, description="Data Validation: Parse function arguments to JSON"
    )


class EmbeddingDataSplitConfig(DataSplitConfig):
    """Configuration for embedding data split"""

    from_date: date | None = Field(None, description="From date")
    to_date: date | None = Field(None, description="To date")
    min_negatives: int = Field(
        default=1,
        description="Minimum number of negative samples per triplet for embedding training",
        ge=1,
    )
    max_negatives: int = Field(
        default=5,
        description="Maximum number of negative samples per triplet for embedding training",
        ge=1,
    )


class SimilarityConfig(BaseModel):
    """Configuration for semantic similarity-based ICL example selection"""

    relevance_ratio: float | None = Field(
        default=0.7,
        description="Ratio of examples selected by pure relevance (0.7 = 70% relevance, 30% coverage). Only used with semantic_similarity.",
        ge=0.0,
        le=1.0,
    )
    embedding_nim_config: "EmbeddingConfig | None" = Field(
        default=None, description="Configuration for embedding NIM when using semantic_similarity"
    )


class ICLConfig(BaseModel):
    """Configuration for ICL"""

    max_context_length: int = Field(default=8192, description="Maximum context length for ICL")
    reserved_tokens: int = Field(default=2048, description="Reserved tokens for ICL")
    max_examples: int = Field(default=3, description="Maximum examples for ICL")
    min_examples: int = Field(default=1, description="Minimum examples for ICL")
    example_selection: Literal["uniform_distribution", "semantic_similarity"] = Field(
        default="uniform_distribution", description="ICL example selection method"
    )
    similarity_config: "SimilarityConfig | None" = Field(
        default=None, description="Configuration for semantic similarity example selection"
    )

    @model_validator(mode="after")
    def validate_semantic_similarity_config(self) -> "ICLConfig":
        """Validate that similarity_config is provided when semantic_similarity is selected."""
        if self.example_selection == "semantic_similarity":
            if self.similarity_config is None:
                raise ValueError(
                    "similarity_config is required when example_selection is set to 'semantic_similarity'. "
                    "Please provide similarity_config in your ICL configuration."
                )
            if self.similarity_config.embedding_nim_config is None:
                raise ValueError(
                    "embedding_nim_config is required within similarity_config when example_selection is set to 'semantic_similarity'. "
                    "Please provide embedding_nim_config in your similarity_config."
                )
        return self

    def validate_examples_limit(self, eval_size: int) -> None:
        """Validate that max_examples doesn't exceed the evaluation dataset size."""
        if self.max_examples > eval_size:
            raise ValueError(
                f"ICL max_examples ({self.max_examples}) cannot exceed "
                f"data split eval_size ({eval_size}). "
            )


class CustomizerTarget(BaseModel):
    """Configuration for customizer target."""

    name: str = Field(
        ...,
        description="Full target path with version (e.g., 'meta/llama-3.2-1b-instruct@2.0' or 'Qwen/Qwen3-4B-Thinking-2507@v1')",
    )
    target_type: str = Field(default="nemo", description="Target type: 'nemo' or 'hf'")
    description: str | None = Field(None, description="Description of the target")
    base_model: str | None = Field(
        None, description="Base model without version (e.g., 'meta/llama-3.2-1b-instruct')"
    )
    model_uri: str | None = Field(
        None, description="Model URI (e.g., 'ngc://...' for nemo or 'hf://...' for HF)"
    )
    hf_endpoint: str | None = Field(None, description="HuggingFace endpoint URL")
    tokenizer: dict[str, Any] = Field(default_factory=dict, description="Tokenizer configuration")
    num_parameters: int | None = Field(None, description="Number of model parameters")
    precision: str | None = Field(None, description="Model precision (e.g., 'bf16-mixed')")
    model_path: str | None = Field(None, description="Optional model path for certain targets")
    trust_remote_code: bool = Field(
        default=False,
        description=(
            "Pass ``trust_remote_code=True`` when registering the model entity. "
            "Required for HuggingFace repos that ship custom modeling code (e.g. "
            "``nvidia/llama-nemotron-embed-1b-v2`` includes "
            "``llama_bidirectional_model.py``)."
        ),
    )

    @model_validator(mode="after")
    def validate_hf_target(self) -> "CustomizerTarget":
        """Validate that HuggingFace targets have all required parameters."""
        if self.target_type == "hf":
            required_hf_fields = [
                "description",
                "base_model",
                "model_uri",
                "hf_endpoint",
                "num_parameters",
                "precision",
            ]

            missing_fields = []
            for field in required_hf_fields:
                if getattr(self, field) is None:
                    missing_fields.append(field)

            if missing_fields:
                raise ValueError(
                    f"HuggingFace target requires the following fields: {', '.join(missing_fields)}. "
                    f"Please provide all required fields when target_type is 'hf'."
                )

            # Validate model_uri format for HF targets
            if not self.model_uri.startswith("hf://"):
                raise ValueError(
                    f"HuggingFace target model_uri must start with 'hf://', got: {self.model_uri}"
                )

        return self


class CustomizerConfig(BaseModel):
    """Configuration for model customization"""

    gpus: int = Field(..., description="Number of GPUs for customization")
    num_nodes: int = Field(default=1, description="Number of nodes for customization")
    max_seq_length: int = Field(default=4096, description="Maximum sequence length")
    pod_spec: dict[str, Any] | None = Field(
        default=None,
        description="Pod specification for customization jobs (tolerations, node_selectors, annotations)",
    )
    # Customizer target parameters - now required and can be either string or CustomizerTarget
    target: CustomizerTarget | str = Field(
        ...,
        description="Target configuration or target string. If string, only the target name is used without creating a new target.",
    )

    @property
    def target_type(self) -> str:
        """Get target type from target or default to 'nemo'."""
        if isinstance(self.target, str):
            return "nemo"  # Default for string targets
        return self.target.target_type


class EmbeddingEvalMetrics(BaseModel):
    """Embedding-specific evaluation metrics configuration."""

    metrics: list[str] = Field(
        default=["recall_5", "map", "ndcg_cut_5", "P_5"],
        description="Evaluation metrics for embedding models",
    )


class CustomEvaluation(BaseModel):
    """Custom evaluation dataset configuration."""

    name: str = Field(..., description="Name of the evaluation dataset")
    workload_id: str = Field(..., description="Workload identifier")
    client_id: str = Field(..., description="Client identifier")

    @model_validator(mode="after")
    def validate_ids(self) -> "CustomEvaluation":
        """Validate that workload_id and client_id are not None."""
        if not self.workload_id:
            raise ValueError("workload_id cannot be None or empty")
        if not self.client_id:
            raise ValueError("client_id cannot be None or empty")
        return self


class EvaluationConfig(BaseModel):
    """Evaluation configuration for jobs."""

    custom_evaluations: list[CustomEvaluation] | None = Field(
        default=[],
        description="List of custom evaluation datasets",
    )
    embedding_eval_metrics: EmbeddingEvalMetrics | None = Field(
        default=None,
        description="Embedding-specific evaluation metrics (only for embedding jobs)",
    )

    @model_validator(mode="after")
    def validate_custom_evaluations_names(self) -> "EvaluationConfig":
        """Validate that custom_evaluations names are unique."""
        names = [custom_eval.name for custom_eval in self.custom_evaluations]
        if len(names) != len(set(names)):
            raise ValueError("custom_evaluation names must be unique")
        return self


class NIMConfig(BaseModel):
    """Configuration for a NIM (Neural Information Model)"""

    model_name: str = Field(..., description="Name of the model")
    tag: str | None = Field(None, description="Container tag for the NIM")
    context_length: int = Field(..., description="Context length for ICL evaluations")
    gpus: int | None = Field(None, description="Number of GPUs for deployment")
    pvc_size: str | None = Field(None, description="Size of PVC for deployment")
    registry_base: str = Field(default="nvcr.io/nim", frozen=True)
    nim_image_model_name: str | None = Field(None, description="NIM image model name")
    customization_enabled: bool = Field(default=False, description="Enable customization")
    eval_enabled: bool = Field(default=True, description="Enable evaluation phase for this NIM")
    customizer_configs: CustomizerConfig | None = Field(
        default=None, description="Customizer configuration"
    )
    model_type: Literal["llm", "embedding"] = Field(
        default="llm", description="Type of NIM - llm or embedding"
    )
    base_model_for_image: str | None = Field(
        None,
        description="Base model name used to select the NIM container image for a customized model",
    )

    @model_validator(mode="after")
    def validate_customization_config(self) -> "NIMConfig":
        """Validate that customizer_configs is provided when customization is enabled."""
        if self.customization_enabled and self.customizer_configs is None:
            raise ValueError(
                "customizer_configs is required when customization_enabled is set to True. "
                "Please provide customizer_configs in your NIM configuration."
            )
        return self

    def nmp_model_name(self) -> str:
        """NMP 26.03 model-entity / deployment names use ``[a-z0-9-]+``.

        Slashes, ``@``, and ``.`` are all rewritten to ``-`` to match the
        inference gateway / model-entity naming used elsewhere
        (``customizer._manage_target``, ``evaluator._to_nmp_model_name``). The
        ``.`` substitution is critical: without it, DFW's legacy model name
        ``meta/llama-3.2-1b-instruct`` deploys as
        ``dfwbp/meta-llama-3.2-1b-instruct`` while the customizer attaches the
        LoRA adapter to the (different) entity
        ``dfwbp/meta-llama-3-2-1b-instruct``, so the running NIM never sees its
        own adapter.
        """
        return self.model_name.replace("/", "-").replace("@", "-").replace(".", "-")

    @staticmethod
    def generate_config_name(base_model: str) -> str:
        """
        Generate consistent config name from base model.

        Args:
            base_model: Base model name in format "namespace/model"

        Returns:
            Configuration name in format "model@v1.0.0+dfw"

        Raises:
            ValueError: If base_model format is invalid
        """
        if base_model is None:
            raise ValueError("Invalid base model format")
        model_parts = base_model.split("/")
        if len(model_parts) != 2:
            raise ValueError(f"Invalid base model format: {base_model}")

        _, model = model_parts
        if "@" in model:
            return model

        return f"{model}@v1.0.0+dfw"

    def to_customized_dms_config(self, base_model_for_image: str | None = None) -> dict[str, Any]:
        """Convert NIMConfig to NMP 26.03 inference deployment_config payload for customized models.

        Args:
            base_model_for_image: Base model name to use for container image reference
        """
        from src.config.base import settings

        # Use the provided base model for image, or fall back to the model name
        image_model = base_model_for_image or self.model_name

        config_name = self.nmp_model_name()

        # ``self.model_name`` for a customized NIM is workspace-qualified
        # (e.g. ``dfwbp/vcy``). Split it into namespace + name so the NMP
        # deployment-config references the right model entity — without
        # ``model_namespace``/``model_name`` the NIM operator builds a
        # NIMService with no model attached (deployments list `models: []`,
        # IG never exposes the deployment).
        ws = settings.system_config.nmp_config.workspace
        if "/" in self.model_name:
            model_namespace, model_entity_name = self.model_name.split("/", 1)
        else:
            model_namespace, model_entity_name = ws, self.model_name

        nim_deployment: dict[str, Any] = {
            "image_name": f"{self.registry_base}/{self.nim_image_model_name or image_model}",
            # Embedding NIMs are merged-checkpoint deployments — no lora
            # sidecar (sidecar's uvicorn binds :8080, which collides with
            # the embedding NIM's Triton HTTP service).
            "lora_enabled": self.model_type != "embedding",
            "model_namespace": model_namespace,
            "model_name": model_entity_name,
        }
        if self.tag is not None:
            nim_deployment["image_tag"] = self.tag
        if self.gpus is not None:
            nim_deployment["gpu"] = self.gpus

        return {
            "name": config_name,
            "workspace": ws,
            "description": f"Customized model config for {self.model_name}",
            "nim_deployment": nim_deployment,
        }

    def is_hf_model(self) -> bool:
        """Check if this is a HuggingFace model based on customizer config."""
        return (
            hasattr(self, "customizer_configs")
            and self.customizer_configs
            and self.customizer_configs.target_type == "hf"
        )

    def get_nemo_dms_config(self) -> dict[str, Any]:
        from src.config.base import settings

        nim_deployment: dict[str, Any] = {
            "image_name": (f"{self.registry_base}/{self.nim_image_model_name or self.model_name}"),
            "additional_envs": {
                "NIM_GUIDED_DECODING_BACKEND": "outlines",
            },
        }
        if self.tag is not None:
            nim_deployment["image_tag"] = self.tag
        if self.pvc_size is not None:
            nim_deployment["disk_size"] = self.pvc_size
        if self.gpus is not None:
            nim_deployment["gpu"] = self.gpus

        # When customization is enabled on this NIM, the customization output
        # for plain ``lora`` finetuning is attached as an adapter to this base
        # entity's ``adapters[]`` list. ``lora_enabled`` tells the NIM operator
        # to provision a lora-sidecar + ``NIM_PEFT_SOURCE`` mount and configure
        # the NIM to refresh adapters periodically.
        #
        # The sidecar's adapter-fetch controller requires
        # ``NMP_MODEL_ENTITY_WORKSPACE`` / ``NMP_MODEL_ENTITY_NAME`` to know
        # which model entity to watch for adapter additions. We pass them
        # explicitly via ``additional_envs`` rather than setting
        # ``model_namespace`` / ``model_name`` on the deployment-config —
        # the latter triggers the operator's "BYO model" path (the main NIM
        # then loads from ``/model-store`` instead of its baked-in weights)
        # which on the llama-3.2-1b 1.8.3 image disables tool-use entirely
        # (every request 400s with ``Tool use has not been enabled``).
        #
        # (Don't set ``NIM_MODEL_PROFILE`` here — the value is image-specific:
        # the NMP tutorial uses ``vllm-lora`` for Qwen3, but the llama-3.2-1b
        # NIM 1.8.3 image doesn't ship that profile and crashloops if forced
        # to it. Letting the NIM auto-select the profile based on its
        # ``model_manifest.yaml`` is what works.)
        ws = settings.system_config.nmp_config.workspace
        # Embedding NIMs do not support LoRA adapter serving — the embedding
        # NIM requires ONNX format which cannot represent standalone adapters
        # (per upstream NMP embedding customization tutorial). Skip the
        # lora-sidecar entirely for embedding base NIMs; embedding
        # customizations land as a fresh BYO-model deployment.
        # Also: the embedding NIM image binds Triton HTTP on 0.0.0.0:8080,
        # which collides with the lora-sidecar's uvicorn (also 8080) and
        # crashes the main container with ``Socket '0.0.0.0:8080' already
        # in use``.
        if self.customization_enabled and self.model_type != "embedding":
            nim_deployment["lora_enabled"] = True
            nim_deployment["additional_envs"]["NMP_MODEL_ENTITY_WORKSPACE"] = ws
            nim_deployment["additional_envs"]["NMP_MODEL_ENTITY_NAME"] = self.nmp_model_name()

        return {
            "name": self.nmp_model_name(),
            "workspace": ws,
            "description": f"NeMo deployment config for {self.model_name}",
            "nim_deployment": nim_deployment,
        }

    def get_hf_dms_config(self) -> dict[str, Any]:
        """Convert NIMConfig to NMP 26.03 inference deployment_config payload for HF models."""
        from src.config.base import settings

        nim_deployment: dict[str, Any] = {
            "image_name": f"{self.registry_base}/{self.nim_image_model_name or 'nvidia/llm-nim'}",
            "model_provider": "hf",
            "model_name": self.model_name,
            "additional_envs": {
                "NIM_GUIDED_DECODING_BACKEND": "outlines",
            },
        }
        if self.tag is not None:
            nim_deployment["image_tag"] = self.tag
        if self.pvc_size is not None:
            nim_deployment["disk_size"] = self.pvc_size
        if self.gpus is not None:
            nim_deployment["gpu"] = self.gpus

        return {
            "name": self.nmp_model_name(),
            "workspace": settings.system_config.nmp_config.workspace,
            "description": f"HuggingFace deployment config for {self.model_name}",
            "hf_token": os.environ.get("HF_TOKEN", ""),
            "nim_deployment": nim_deployment,
        }

    def to_dms_config(self) -> dict[str, Any]:
        """Convert NIMConfig to DMS deployment configuration."""
        if self.is_hf_model():
            return self.get_hf_dms_config()
        else:
            return self.get_nemo_dms_config()

    def target_model_for_evaluation(self) -> str | dict[str, Any]:
        """Get the model name for evaluation"""
        if "@" in self.model_name:
            return self.model_name.split("@")[0]
        return self.model_name


class LoRAConfig(BaseModel):
    adapter_dim: int = Field(default=32, description="Adapter dimension")
    adapter_dropout: float = Field(default=0.1, description="Adapter dropout")
    alpha: int = Field(default=64, description="Alpha")


class DatasetParametersConfig(BaseModel):
    """Configuration for dataset parameters"""

    in_batch_negatives: bool | None = Field(default=None, description="In-batch negatives")
    num_hard_negatives: int | None = Field(default=None, description="Number of hard negatives")


class DPOConfig(BaseModel):
    """Configuration for DPO (Direct Preference Optimization) training."""

    ref_policy_kl_penalty: float = Field(
        default=0.05,
        description="KL penalty coefficient for reference policy divergence",
        ge=0.0,
    )
    preference_loss_weight: float = Field(
        default=1.0,
        description="Weight for preference loss component",
        ge=0.0,
    )
    preference_average_log_probs: bool = Field(
        default=False,
        description="Whether to average log probabilities for preference loss",
    )
    sft_loss_weight: float = Field(
        default=0.0,
        description="Weight for supervised fine-tuning loss component",
        ge=0.0,
    )
    sft_average_log_probs: bool = Field(
        default=False,
        description="Whether to average log probabilities for SFT loss",
    )


class TrainingConfig(BaseModel):
    training_type: str = Field(default="sft", description="Training type (sft, dpo, etc.)")
    finetuning_type: str = Field(default="lora", description="Finetuning type (lora, all_weights)")
    epochs: int = Field(default=2, description="Number of epochs")
    batch_size: int = Field(default=16, description="Batch size")
    learning_rate: float = Field(default=0.0001, description="Learning rate")
    lora: LoRAConfig | None = Field(default_factory=LoRAConfig, description="LoRA configuration")
    dpo: DPOConfig | None = Field(default_factory=DPOConfig, description="DPO configuration")
    pipeline_parallel_size: int | None = Field(None, description="Pipeline parallel size")
    training_precision: str = Field(default="bf16-mixed", description="Training precision")
    use_sequence_parallel: bool = Field(
        default=False, description="Whether to use sequence parallel"
    )
    data_parallel_size: int = Field(default=1, description="Data parallel size for customization")
    tensor_parallel_size: int = Field(
        default=1, description="Tensor parallel size for customization"
    )
    dataset_parameters: DatasetParametersConfig | None = Field(
        default_factory=DatasetParametersConfig, description="Dataset parameters"
    )
    warmup_steps: int = Field(default=100, description="Warmup steps")
    micro_batch_size: int = Field(default=1, description="Micro batch size")
    weight_decay: float | None = Field(default=None, description="Weight decay")
    min_learning_rate: float | None = Field(default=None, description="Minimum learning rate")


class EmbeddingConfig(NIMConfig):
    """Configuration for embedding models"""

    # Override the nim type to be embedding
    model_type: Literal["llm", "embedding"] = Field(
        default="embedding", description="Type of NIM - always embedding for EmbeddingConfig"
    )

    # Deployment type for embedding models
    deployment_type: Literal["remote", "local"]

    # Remote fields
    url: str | None = None
    api_key: str | None = None
    context_length: int | None = None  # overwrite NIMConfig to be optional
    model_name: str | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            **kwargs,
        )

    @property
    def is_remote(self) -> bool:
        return self.deployment_type == "remote"

    @classmethod
    def remote_config(cls, data: dict, api_key: str) -> "EmbeddingConfig":
        """Get configuration based on type"""
        return cls(
            url=data.get("url"),
            deployment_type="remote",
            model_name=data.get("model_name"),
            api_key=api_key,
        )

    @classmethod
    def local_config(cls, data: dict) -> "EmbeddingConfig":
        return cls(
            model_name=data.get("model_name"),
            deployment_type="local",
            tag=data.get("tag"),
            context_length=data.get("context_length"),
            gpus=data.get("gpus"),
            pvc_size=data.get("pvc_size"),
            registry_base=data.get("registry_base") or "nvcr.io/nim",
            customization_enabled=False,  # customization should be disabled for embedding
            url=data.get("url"),  # Remove hardcoded localhost URL
        )

    def get_endpoint_url(self) -> str:
        """Get the endpoint URL for the embedding service"""
        if self.is_remote:
            return self.url
        else:
            return getattr(self, "url", "http://localhost:8000/v1/embeddings")

    @classmethod
    def from_json(cls, data: dict) -> "EmbeddingConfig":
        # Read API key directly from EMB_API_KEY environment variable, default to NVIDIA_API_KEY
        api_key = os.environ.get("EMB_API_KEY") or os.environ.get("NVIDIA_API_KEY")
        is_remote = data.get("deployment_type") == "remote"
        return cls.remote_config(data, api_key) if is_remote else cls.local_config(data)


class LLMJudgeConfig(NIMConfig):
    deployment_type: Literal["remote", "local"]
    # Remote fields
    url: str | None = None
    api_key: str | None = None
    context_length: int | None = None  # overwrite NIMConfig to be optional
    model_name: str | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            **kwargs,
        )

    @property
    def is_remote(self) -> bool:
        return self.deployment_type == "remote"

    @classmethod
    def remote_config(cls, data: dict, api_key: str) -> "LLMJudgeConfig":
        """Get configuration based on type"""
        return cls(
            url=data.get("url"),
            deployment_type="remote",
            model_name=data.get("model_name"),
            api_key=api_key,
        )

    @classmethod
    def local_config(cls, data: dict) -> "LLMJudgeConfig":
        return cls(
            model_name=data.get("model_name"),
            deployment_type="local",
            tag=data.get("tag"),
            context_length=data.get("context_length"),
            gpus=data.get("gpus"),
            pvc_size=data.get("pvc_size"),
            registry_base=data.get("registry_base") or "nvcr.io/nim",
            customization_enabled=False,  # customization should be disabled for local LLM judge
        )

    def judge_model_config(self) -> dict[str, Any]:
        if self.is_remote:
            return {
                "api_endpoint": {
                    "url": self.url,
                    "model_id": self.model_name,
                    "api_key": self.api_key,
                },
            }
        else:
            return self.target_model_for_evaluation()

    @classmethod
    def from_json(cls, data: dict) -> "LLMJudgeConfig":
        # Read API key directly from LLM_JUDGE_API_KEY environment variable, default to NVIDIA_API_KEY
        api_key = os.environ.get("LLM_JUDGE_API_KEY") or os.environ.get("NVIDIA_API_KEY")
        is_remote = data.get("deployment_type") == "remote"
        return cls.remote_config(data, api_key) if is_remote else cls.local_config(data)


def _parse_nim_config(nim: dict) -> NIMConfig:
    """Parse a single NIM configuration with customizer_configs handling.

    This function is shared between LLMJobConfig and EmbeddingJobConfig
    to ensure consistent NIM parsing logic.

    Args:
        nim: Dictionary containing NIM configuration data

    Returns:
        NIMConfig: Parsed NIM configuration object
    """
    nim_copy = nim.copy()
    # Handle customizer_configs if present
    if nim_copy.get("customizer_configs"):
        customizer_data = nim_copy["customizer_configs"]
        # Check if target is present and needs parsing
        if "target" in customizer_data:
            ct = customizer_data["target"]
            # If it's a dict, convert to CustomizerTarget object
            if isinstance(ct, dict):
                customizer_data["target"] = CustomizerTarget(**ct)
            # If it's a string, leave it as is
        # Create CustomizerConfig with the parsed data
        nim_copy["customizer_configs"] = CustomizerConfig(**customizer_data)
    return NIMConfig(**nim_copy)


class LLMJobConfig(JobConfig):
    """Job-level configuration for LLM jobs that can be managed dynamically."""

    _NIMS_EMPTY_ERROR = (
        "At least one NIM must be configured. "
        "Please add at least one NIM to the 'nims' list in your configuration."
    )

    nims: list[NIMConfig]
    data_split_config: DataSplitConfig
    icl_config: ICLConfig
    training_config: TrainingConfig
    evaluation_config: EvaluationConfig = Field(default_factory=EvaluationConfig)
    ingestion_config: IngestionConfig | None = None

    @model_validator(mode="after")
    def validate_icl_and_data_split_consistency(self) -> "LLMJobConfig":
        """Validate that max_examples in ICL config doesn't exceed eval_size in data split config."""
        self.icl_config.validate_examples_limit(self.data_split_config.eval_size)
        return self

    @model_validator(mode="after")
    def validate_nims_not_empty(self) -> "LLMJobConfig":
        """Validate that at least one NIM is configured."""
        if not self.nims or len(self.nims) == 0:
            raise ValueError(self._NIMS_EMPTY_ERROR)
        return self

    @classmethod
    def from_dict(cls, config_data: dict) -> "LLMJobConfig":
        """Create LLM job configuration from dictionary data with proper transformations."""
        # Deduplicate NIMs by model_name
        unique_nims = cls._deduplicate_nims(config_data["nims"])

        # Handle ICL config with similarity config
        icl_data = config_data["icl_config"]
        if icl_data.get("similarity_config") and icl_data["similarity_config"].get(
            "embedding_nim_config"
        ):
            icl_data["similarity_config"]["embedding_nim_config"] = EmbeddingConfig.from_json(
                icl_data["similarity_config"]["embedding_nim_config"]
            )
            icl_data["similarity_config"] = SimilarityConfig(**icl_data["similarity_config"])

        # Handle evaluation config (optional)
        evaluation_config = None
        if "evaluation_config" in config_data:
            evaluation_config = cls._parse_evaluation_config(config_data["evaluation_config"])

        # Parse NIMs with customizer_configs handling
        parsed_nims = [_parse_nim_config(nim) for nim in unique_nims]

        # Handle ingestion config (optional). When present, ingestion is enabled;
        # same schema as trigger ingestion_config (IngestionConfig).
        ingestion_config = None
        if config_data.get("ingestion_config"):
            ingestion_config = IngestionConfig(**config_data["ingestion_config"])

        # Build the config dict
        config_dict = {
            "nims": parsed_nims,
            "data_split_config": DataSplitConfig(**config_data["data_split_config"]),
            "icl_config": ICLConfig(**icl_data),
            "training_config": TrainingConfig(**config_data["training_config"]),
        }

        # Only add evaluation_config if it was provided
        if evaluation_config is not None:
            config_dict["evaluation_config"] = evaluation_config

        # Only add ingestion_config if it was provided
        if ingestion_config is not None:
            config_dict["ingestion_config"] = ingestion_config

        return cls(**config_dict)

    @classmethod
    def _parse_evaluation_config(cls, eval_data: dict) -> EvaluationConfig:
        """Parse evaluation configuration."""
        custom_evaluations = []
        if "custom_evaluations" in eval_data:
            for custom_eval in eval_data["custom_evaluations"]:
                custom_evaluations.append(CustomEvaluation(**custom_eval))
        return EvaluationConfig(custom_evaluations=custom_evaluations)

    @classmethod
    def _deduplicate_nims(cls, nims: list[dict]) -> list[dict]:
        """Deduplicate NIMs by model_name, keeping the first occurrence."""
        seen_models = set()
        unique_nims = []
        for nim in nims:
            if nim["model_name"] not in seen_models:
                unique_nims.append(nim)
                seen_models.add(nim["model_name"])
        return unique_nims


class EmbeddingJobConfig(JobConfig):
    """Job-level configuration for embedding jobs that can be managed dynamically."""

    _NIMS_EMPTY_ERROR = (
        "At least one NIM must be configured. "
        "Please add at least one NIM to the 'nims' list in your configuration."
    )
    _EMBEDDING_TYPE_ERROR = (
        "EmbeddingJobConfig requires embedding models, but found {model_type} model: {model_name}"
    )

    nims: list[NIMConfig]
    data_split_config: EmbeddingDataSplitConfig
    training_config: TrainingConfig
    evaluation_config: EvaluationConfig = Field(default_factory=EvaluationConfig)
    ingestion_config: IngestionConfig | None = None

    @model_validator(mode="after")
    def validate_nims_not_empty(self) -> "EmbeddingJobConfig":
        """Validate that at least one NIM is configured."""
        if not self.nims or len(self.nims) == 0:
            raise ValueError(self._NIMS_EMPTY_ERROR)
        return self

    @model_validator(mode="after")
    def validate_embedding_nims(self) -> "EmbeddingJobConfig":
        """Validate that all NIMs are embedding models."""
        for nim in self.nims:
            if nim.model_type != "embedding":
                raise ValueError(
                    self._EMBEDDING_TYPE_ERROR.format(
                        model_type=nim.model_type, model_name=nim.model_name
                    )
                )
        return self

    @classmethod
    def from_dict(cls, config_data: dict) -> "EmbeddingJobConfig":
        """Create embedding job configuration from dictionary data with proper transformations."""
        # Deduplicate embedding NIMs by model_name
        unique_embedding_nims = cls._deduplicate_nims(config_data["nims"])

        # Parse evaluation config (optional)
        evaluation_config = None
        if "evaluation_config" in config_data:
            evaluation_config = cls._parse_evaluation_config(config_data["evaluation_config"])

        # Parse NIMs with customizer_configs handling
        parsed_nims = [_parse_nim_config(nim) for nim in unique_embedding_nims]

        # Handle ingestion config (optional). When present, ingestion is enabled;
        # same schema as trigger ingestion_config (IngestionConfig).
        ingestion_config = None
        if config_data.get("ingestion_config"):
            ingestion_config = IngestionConfig(**config_data["ingestion_config"])

        # Build the config dict
        config_dict = {
            "nims": parsed_nims,
            "data_split_config": EmbeddingDataSplitConfig(**config_data["data_split_config"]),
            "training_config": TrainingConfig(**config_data["training_config"]),
        }

        # Only add evaluation_config if it was provided
        if evaluation_config is not None:
            config_dict["evaluation_config"] = evaluation_config

        # Only add ingestion_config if it was provided
        if ingestion_config is not None:
            config_dict["ingestion_config"] = ingestion_config

        return cls(**config_dict)

    @classmethod
    def _parse_evaluation_config(cls, eval_data: dict) -> EvaluationConfig:
        """Parse evaluation configuration for embedding jobs."""
        custom_evaluations = []
        if "custom_evaluations" in eval_data:
            for custom_eval in eval_data["custom_evaluations"]:
                custom_evaluations.append(CustomEvaluation(**custom_eval))

        # Handle embedding metrics
        embedding_eval_metrics = None
        if "embedding_eval_metrics" in eval_data:
            embedding_eval_metrics = EmbeddingEvalMetrics(**eval_data["embedding_eval_metrics"])
        else:
            # Default metrics for embedding
            embedding_eval_metrics = EmbeddingEvalMetrics()

        return EvaluationConfig(
            custom_evaluations=custom_evaluations,
            embedding_eval_metrics=embedding_eval_metrics,
        )

    @classmethod
    def _deduplicate_nims(cls, nims: list[dict]) -> list[dict]:
        """Deduplicate NIMs by model_name, keeping the first occurrence."""
        seen_models = set()
        unique_nims = []
        for nim in nims:
            if nim["model_name"] not in seen_models:
                unique_nims.append(nim)
                seen_models.add(nim["model_name"])
        return unique_nims


class NmpSystemConfig(SystemConfig):
    """NMP system config — the shared blocks plus NMP's own system sub-configs."""

    nmp_config: NMPConfig
    llm_judge_config: LLMJudgeConfig


def load_nmp_settings(config_data: dict) -> tuple[SystemConfig, dict[str, JobConfig]]:
    """Build the NMP system config + job configs from raw YAML data.

    Returns ``(system_config, job_configs)`` where ``job_configs`` is keyed by
    ``job_type``: ``llm_job`` always, ``embedding_job`` when present.

    Built via ``JobConfig.build`` so each entry is stamped with the key it loaded
    under. The NMP types are unambiguous by ``isinstance``, so the stamp is
    redundant here — applied anyway so every construction door behaves alike.
    """
    system_config = NmpSystemConfig(
        nmp_config=NMPConfig(**config_data["nmp_config"]),
        llm_judge_config=LLMJudgeConfig.from_json(config_data["llm_judge_config"]),
        **build_shared_system_kwargs(config_data),
    )

    job_configs: dict[str, JobConfig] = {
        "llm_job": LLMJobConfig.build(config_data["llm_job_config"], "llm_job"),
    }
    if "embedding_job_config" in config_data:
        job_configs["embedding_job"] = EmbeddingJobConfig.build(
            config_data["embedding_job_config"], "embedding_job"
        )
    return system_config, job_configs
