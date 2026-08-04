#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Pydantic schemas for the always-on rules YAML config file.

These model the on-disk shape of the file pointed at by
``ALWAYS_ON_RULES_CONFIG`` and live in the :mod:`realtime.schemas`
package so any caller — REST route, CLI, worker, agent flow, tests —
that needs to load or validate the always-on rules file can reuse the
same schema without depending on the FastAPI layer.

The ``always_on_params`` block is generated dynamically from
:class:`AlertRuleConfig`'s dataclass fields so that adding a field to
that dataclass automatically makes it a legal YAML key (no second
place to update) and ``extra="forbid"`` catches typos like
``modle: foo`` at load time instead of silently falling back to defaults.
"""

from dataclasses import MISSING, fields as dataclass_fields
from typing import Any, Dict, List, Optional, Type

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    field_validator,
    model_validator,
)

from .alert_config import AlertRuleConfig


# Fields populated by the always-on service from the camera event or
# the rule entry — *not* accepted inside `always_on_params`. Putting any
# of these in YAML is a config error.
_DERIVED_RULE_FIELDS = frozenset({"live_stream_url", "alert_type", "sensor_name", "sensor_id"})

# Fields that have defaults in AlertRuleConfig but must be explicitly
# provided in the always-on YAML config (cannot be left out or blank).
_ALWAYS_ON_REQUIRED_FIELDS = frozenset({"prompt", "system_prompt", "model"})


def _build_always_on_params_model() -> Type[BaseModel]:
    """Generate ``AlwaysOnRuleParams`` from ``AlertRuleConfig`` fields.

    Every non-derived field of ``AlertRuleConfig`` becomes a field on the
    generated model with the same type and default (or required, if the
    dataclass field has no default). ``extra="forbid"`` rejects unknown
    keys so typos in the YAML fail at load time.

    Fields in ``_ALWAYS_ON_REQUIRED_FIELDS`` (``prompt``, ``system_prompt``,
    ``model``) are always required in the YAML regardless of their dataclass
    defaults so operators cannot accidentally omit them.
    """
    pydantic_fields: Dict[str, Any] = {}
    for f in dataclass_fields(AlertRuleConfig):
        if f.name in _DERIVED_RULE_FIELDS:
            continue
        default: Any
        # Force config-required fields to be required in the YAML schema even
        # if AlertRuleConfig gives them a default (e.g. "" for system_prompt).
        if f.name in _ALWAYS_ON_REQUIRED_FIELDS:
            default = ...
        elif f.default is not MISSING:
            default = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            default = Field(default_factory=f.default_factory)  # type: ignore[arg-type]
        else:
            default = ...
        pydantic_fields[f.name] = (f.type, default)
    return create_model(  # type: ignore[call-overload]
        "AlwaysOnRuleParams",
        __config__=ConfigDict(extra="forbid"),
        **pydantic_fields,
    )


AlwaysOnRuleParams = _build_always_on_params_model()
AlwaysOnRuleParams.__doc__ = (
    "Parameters accepted inside an always-on rule's `always_on_params:` "
    "block. Mirrors `AlertRuleConfig` minus the fields derived from the "
    f"VST event or the rule entry ({sorted(_DERIVED_RULE_FIELDS)!r}). "
    f"Fields {sorted(_ALWAYS_ON_REQUIRED_FIELDS)!r} are required even "
    "though AlertRuleConfig carries defaults for them. "
    "Unknown keys are rejected with `extra=\"forbid\"`, so YAML typos "
    "fail at config load instead of silently falling back to defaults."
)


def _reject_blank_string(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a non-empty, non-whitespace string")
    return value


class AlwaysOnRuleEntry(BaseModel):
    """A single entry under the top-level ``always_on_rules:`` YAML list."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(
        ...,
        min_length=1,
        description="Free-form label used in logs and the per-camera sidecar. Required.",
    )
    alert_type: str = Field(
        ...,
        min_length=1,
        description=(
            "Forwarded to RTVI as `alert_category`. Required — a missing "
            "value previously defaulted to the literal string "
            '"always_on" and became indistinguishable from an '
            "intentional always-on alert type."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional human-readable notes for operators.",
    )
    always_on_params: AlwaysOnRuleParams = Field(  # type: ignore[valid-type]
        ...,
        description=(
            "Per-rule VLM parameters; see AlwaysOnRuleParams. Required "
            "because the per-rule `prompt` lives inside this block."
        ),
    )

    @field_validator("rule_id", "alert_type")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _reject_blank_string(v)


class AlwaysOnRulesFile(BaseModel):
    """Top-level shape of ``ALWAYS_ON_RULES_CONFIG`` YAML."""

    model_config = ConfigDict(extra="forbid")

    always_on_rules: List[AlwaysOnRuleEntry] = Field(
        ...,
        min_length=1,
        description=(
            "Non-empty list of rules to fan out for each camera_streaming event."
        ),
    )

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> "AlwaysOnRulesFile":
        seen: set = set()
        for entry in self.always_on_rules:
            if entry.rule_id in seen:
                raise ValueError(
                    f"duplicate rule_id {entry.rule_id!r} in always_on_rules"
                )
            seen.add(entry.rule_id)
        return self


__all__ = [
    "AlwaysOnRuleEntry",
    "AlwaysOnRuleParams",
    "AlwaysOnRulesFile",
]
