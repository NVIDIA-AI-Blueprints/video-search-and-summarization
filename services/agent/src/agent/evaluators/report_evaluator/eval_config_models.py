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

from typing import Any

from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator


class FieldConfig(BaseModel):
    """Configuration for a single field in the evaluation tree."""

    method: str | None = Field(default=None, description="Evaluation method to use.")
    fields: dict[str, "FieldConfig"] | None = Field(
        default=None,
        description="Nested fields for sections. Required if this is a section with explicit fields.",
    )
    allow_dynamic_field_discovery: bool = Field(
        default=False,
        description="If true, dynamically discover and evaluate fields not explicitly defined in 'fields'.",
    )
    _methods: set[str] = set()  # Private field to cache collected methods

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_and_collect_methods(self) -> "FieldConfig":
        """Validate field configuration and collect methods."""

        # If fields is explicitly provided, it cannot be None or empty
        if ("fields" in self.model_fields_set) and (self.fields is None or len(self.fields) == 0):
            raise ValueError("If 'fields' is specified, it must contain at least one field.")

        # If average is used as method, fields must be specified or allow_dynamic_field_discovery
        if self.method == "average" and not self.fields and not self.allow_dynamic_field_discovery:
            raise ValueError(
                "Method 'average' can only be used for sections with 'fields' or 'allow_dynamic_field_discovery'"
            )

        # Collect methods
        methods = set()

        # Collect method for current node
        if self.method is not None and self.method != "average":
            methods.add(self.method)
        else:
            methods.add("llm_judge")  # Use llm_judge by default

        # Register llm_judge if dynamic discovery is enabled
        if self.allow_dynamic_field_discovery:
            methods.add("llm_judge")

        # Collect from children
        if self.fields:
            for field_config in self.fields.values():
                methods.update(field_config._methods)

        self._methods = methods
        return self


class EvalMetricsConfig(BaseModel):
    """Root configuration for evaluation metrics."""

    root: FieldConfig = Field(
        ...,
        description="Root node of the evaluation tree.",
    )
    root_key: str = Field(
        ...,
        description="The root key name from the original config.",
    )
    methods: set[str] = Field(
        default_factory=set,
        description="A set of all methods used in the config tree.",
    )

    model_config = {"extra": "forbid"}

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "EvalMetricsConfig":
        """
        Create EvalMetricsConfig from a dictionary with single root key.

        Args:
            config: Dictionary with exactly one root key

        Returns:
            EvalMetricsConfig instance

        Raises:
            ValueError: If config doesn't have exactly one root key
        """
        if not isinstance(config, dict):
            raise ValueError(f"Config must be a dict, got {type(config).__name__}")

        if len(config) != 1:
            raise ValueError(f"Config must have exactly one root key, found {len(config)}: {list(config.keys())}")

        root_key = next(iter(config.keys()))
        root_value = config[root_key]
        root_config = FieldConfig(**root_value)

        return cls(root=root_config, root_key=root_key, methods=root_config._methods)
