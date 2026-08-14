# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Versioned summary payload stored inside unified-memory output extensions."""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

SUMMARY_SCHEMA_ID: Literal["nv.vss.summary/1.0"] = "nv.vss.summary/1.0"


class SummaryEvent(BaseModel):
    """One event emitted by LVS for a file or stream summary."""

    model_config = ConfigDict(extra="allow")

    id: str | int
    start_time: float | str
    end_time: float | str
    type: str
    description: str


class SummaryExtension(BaseModel):
    """The independently versioned summary result contract."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["nv.vss.summary/1.0"] = Field(default=SUMMARY_SCHEMA_ID, alias="schema")
    summary_id: str = Field(min_length=1)
    events: list[SummaryEvent] = Field(default_factory=list)
    total_events: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def event_count_matches(self) -> SummaryExtension:
        if self.total_events != len(self.events):
            raise ValueError(f"total_events={self.total_events} does not match the {len(self.events)} supplied events")
        return self

    def model_dump_summary(self) -> dict[str, Any]:
        """Serialize with the public ``schema`` field name."""
        return self.model_dump(by_alias=True, mode="json")


__all__ = [
    "SUMMARY_SCHEMA_ID",
    "SummaryEvent",
    "SummaryExtension",
]
