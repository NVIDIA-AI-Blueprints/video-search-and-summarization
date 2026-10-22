# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Strictly render environment-backed values in Data Flywheel YAML config."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml

_ENV_REFERENCE_PATTERN = re.compile(r"\$\{")
_ENV_SCALAR_PATTERN = re.compile(
    r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\|(?P<format>bool|nullable-string))?"
    r"(?::-(?P<default>[^}]*))?\}$"
)


def _format_value(name: str, value: str, value_format: str | None) -> object:
    """Convert one environment value to the YAML scalar type requested."""
    if value_format is None:
        return value
    if value_format == "nullable-string":
        return None if value.lower() == "null" else value
    normalized_value = value.lower()
    if normalized_value not in {"true", "false"}:
        raise ValueError(f"Environment variable must be true or false: {name}")
    return normalized_value == "true"


def _render_environment_values(
    node: object,
    *,
    values: Mapping[str, str],
) -> object:
    """Recursively render environment-backed values within one YAML node."""
    if isinstance(node, dict):
        return {
            key: _render_environment_values(value, values=values) for key, value in node.items()
        }
    if isinstance(node, list):
        return [_render_environment_values(value, values=values) for value in node]
    if not isinstance(node, str) or _ENV_REFERENCE_PATTERN.search(node) is None:
        return node

    match = _ENV_SCALAR_PATTERN.fullmatch(node)
    if match is None:
        raise ValueError(
            "Invalid environment placeholder; it must occupy an entire YAML scalar "
            f"and use a supported format: {node!r}"
        )

    name = match.group("name")
    value = values.get(name)
    if not value:
        value = match.group("default")
    if value is None or value == "":
        raise ValueError(f"Missing non-empty environment variable required by config: {name}")
    return _format_value(name, value, match.group("format"))


def render_environment_values(
    node: object,
    *,
    environ: Mapping[str, str] | None = None,
) -> object:
    """Recursively replace complete environment-backed YAML scalars.

    Supported forms are ``${NAME}``, ``${NAME|bool}``, and
    ``${NAME|nullable-string}``. Each accepts a shell-style fallback such as
    ``${NAME:-value}`` or ``${NAME|bool:-true}``. Missing and empty required
    values fail before Pydantic sees the config.
    """
    values = os.environ if environ is None else environ
    return _render_environment_values(node, values=values)


def load_rendered_yaml(
    source: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """Load one YAML mapping and resolve its environment-backed scalars."""
    source_path = Path(source)
    loaded = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Top-level config must be a mapping: {source_path}")
    rendered = render_environment_values(loaded, environ=environ)
    if not isinstance(rendered, dict):  # The recursive renderer preserves mappings.
        raise TypeError(f"Rendered config must remain a mapping: {source_path}")
    return rendered


__all__ = ["load_rendered_yaml", "render_environment_values"]
