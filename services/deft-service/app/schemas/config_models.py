"""ExperimentConfig and its sub-schemas — mirrors components.schemas in
openapi.json for everything under FineTuneRequest.config /
FineTuneRequest.overrides.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import MAX_NUM_GPUS


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    checkpoint: str
    checkpoint_filename: str | None = None


class PartialModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    checkpoint: str | None = None
    checkpoint_filename: str | None = None


class PresignedFile(BaseModel):
    """One object in a presigned per-object snapshot manifest.

    ``path`` is the object's location relative to the workspace/snapshot root
    (e.g. ``kpi/images/a.jpg``); ``url`` is a presigned http(s) GET URL. Used
    when the snapshot is delivered as a manifest of presigned URLs instead of an
    ``s3://`` prefix — a single presigned URL cannot mirror a whole prefix.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    url: str


class DataPoolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    # Presigned per-object snapshot manifest — an alternative to an s3:// `path`
    # prefix: every object is downloaded to workspace/<path>. When set it wins
    # over `path` for materialization (`path` may then be the snapshot's logical
    # s3:// URI kept for reference).
    files: list[PresignedFile] | None = None
    annotations_dir: str | None = None
    detection_file: str | None = None
    embeddings_path: str | None = None


class PartialDataPoolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    annotations_dir: str | None = None
    detection_file: str | None = None
    embeddings_path: str | None = None


class KpiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    ground_truth_dir: str | None = None
    inference_spec_path: str | None = None
    analyze_spec_path: str | None = None
    detection_file: str | None = None


PartialKpiConfig = KpiConfig  # identical shape; every field already optional


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iterations: int = Field(default=5, ge=1)
    convergence_epsilon: float | None = Field(default=None, ge=0)
    convergence_window: int = Field(default=2, ge=1)
    kpi_target: str | None = None
    num_sdg: int | None = Field(default=None, ge=1)


PartialPipelineConfig = PipelineConfig  # every field optional/defaulted already


class TrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    epochs: int = Field(default=5, ge=1)
    learning_rate: float = Field(default=2e-05, gt=0)
    # 4 rather than the node's full count: a box running training is usually
    # also serving inference, so a default that claims every GPU starves the
    # thing the training is for. Callers on a smaller box must set this down --
    # nothing here checks it against the GPUs actually visible.
    num_gpus: int = Field(default=4, ge=1, le=MAX_NUM_GPUS)
    batch_size: int | None = Field(default=None, ge=1)
    initial_spec_path: str | None = None
    freeze_backbone: bool = False


PartialTrainingConfig = TrainingConfig  # every field optional/defaulted already


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["SigLIP", "CLIP"] = "SigLIP"
    num_gpus: int = Field(default=4, ge=1, le=MAX_NUM_GPUS)
    model_path: str | None = None
    model_config_path: str | None = None


PartialEmbeddingConfig = EmbeddingConfig  # every field optional/defaulted already


class MiningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["euclidean", "cosine", "manhattan"] = "cosine"
    rare_classes: list[str] | None = None
    topn: int = Field(default=5, ge=1)
    min_similarity: float | None = Field(default=0.9, ge=0, le=1)
    filter_by_label: bool = False
    desired_unique_count: int | None = Field(default=None, ge=1)
    multiplier: int | None = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _topn_xor_desired_unique_count(self) -> "MiningConfig":
        # Mirrors openapi.json's `not: {required: [topn, desired_unique_count]}` —
        # topn (AOI k-NN retention) and desired_unique_count/multiplier
        # (grounding-dino over-fetch) are mutually exclusive parameterizations.
        # `topn` carries a default, so only treat it as "set" when the caller
        # supplied it explicitly alongside desired_unique_count.
        if "topn" in self.model_fields_set and self.desired_unique_count is not None:
            raise ValueError(
                "topn and desired_unique_count are mutually exclusive — "
                "set one or the other, never both."
            )
        return self


PartialMiningConfig = MiningConfig  # every field optional/defaulted already


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelConfig
    data_pool: DataPoolConfig
    kpi: KpiConfig = Field(default_factory=KpiConfig)
    classes: dict[str, float] | None = None
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    mining: MiningConfig = Field(default_factory=MiningConfig)

    @model_validator(mode="after")
    def _classes_are_probabilities(self) -> "ExperimentConfig":
        if self.classes is not None:
            for name, value in self.classes.items():
                if not (0 <= value <= 1):
                    raise ValueError(f"classes[{name!r}] must be between 0 and 1, got {value}")
        return self


class ExperimentConfigOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: PartialModelConfig | None = None
    data_pool: PartialDataPoolConfig | None = None
    kpi: KpiConfig | None = None
    classes: dict[str, float] | None = None
    pipeline: PipelineConfig | None = None
    training: TrainingConfig | None = None
    embedding: EmbeddingConfig | None = None
    mining: MiningConfig | None = None
