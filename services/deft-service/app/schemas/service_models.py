from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class InfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    backend: Literal["docker", "kfp", "slurm", "brev"]
    storage_root: str
