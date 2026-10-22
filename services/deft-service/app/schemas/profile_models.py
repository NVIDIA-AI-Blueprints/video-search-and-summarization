from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.config_models import ExperimentConfig


class ProfileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    workflow: str
    config: ExperimentConfig
