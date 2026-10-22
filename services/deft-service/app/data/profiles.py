"""Static profile catalog — mirrors the ProfileInfo examples in openapi.json."""

from __future__ import annotations

from app.schemas.config_models import (
    DataPoolConfig,
    EmbeddingConfig,
    ExperimentConfig,
    KpiConfig,
    MiningConfig,
    ModelConfig,
    PipelineConfig,
    TrainingConfig,
)
from app.schemas.profile_models import ProfileInfo

PROFILES: dict[str, ProfileInfo] = {
    "santomas_gdino": ProfileInfo(
        name="santomas_gdino",
        description="Grounding DINO profile tuned for the Santomas dataset.",
        workflow="tmm_only",
        config=ExperimentConfig(
            model=ModelConfig(
                name="grounding_dino",
                checkpoint="s3://deft-models/models/gdino_swin_tiny.pth",
            ),
            data_pool=DataPoolConfig(path="s3://deft-data/raw_frames/santomas/"),
            kpi=KpiConfig(
                path="s3://deft-data/kpi/santomas/images/",
                ground_truth_dir="s3://deft-data/kpi/santomas/labels/",
            ),
            classes={"pedestrian": 0.7, "vehicle": 0.99},
            embedding=EmbeddingConfig(model="SigLIP", model_path="file:///mnt/deft/sda_required_data/siglip-base-patch16-224"),
            mining=MiningConfig(metric="cosine", topn=5),
            pipeline=PipelineConfig(iterations=5, convergence_epsilon=0.005, convergence_window=2),
            training=TrainingConfig(
                batch_size=4,
                epochs=5,
                initial_spec_path="lustre://deft-fs/training/specs/train_grounding_dino.yaml",
                learning_rate=2e-05,
                num_gpus=4,
                freeze_backbone=True,
            ),
        ),
    ),
    "aoi_default": ProfileInfo(
        name="aoi_default",
        description="Default AOI secondary-inspection fine-tuning profile.",
        workflow="aoi_active_learning",
        config=ExperimentConfig(
            model=ModelConfig(
                name="aoi-secondary-inspection",
                checkpoint="s3://aoi-models/baseline/1.0.0/model.pt",
                checkpoint_filename="model.pt",
            ),
            data_pool=DataPoolConfig(
                path="s3://aoi-training/snapshots/aoi-train-snapshot-123/",
                annotations_dir="s3://aoi-training/snapshots/aoi-train-snapshot-123/labels/",
            ),
            kpi=KpiConfig(
                path="s3://aoi-kpi/snapshots/aoi-kpi-snapshot-001/images/",
                ground_truth_dir="s3://aoi-kpi/snapshots/aoi-kpi-snapshot-001/labels/",
            ),
            pipeline=PipelineConfig(iterations=3, convergence_epsilon=0.005, convergence_window=2),
            training=TrainingConfig(batch_size=8, epochs=5, num_gpus=4),
        ),
    ),
}
