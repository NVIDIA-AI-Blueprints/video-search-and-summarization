from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WorkflowInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    pre_loop: list[str]
    loop: list[str]
    post_loop: list[str]
    relevant_config: list[str]
