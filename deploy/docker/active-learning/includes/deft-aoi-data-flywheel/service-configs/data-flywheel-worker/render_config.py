# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""Render a strict environment-backed YAML template, then execute a command."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

_ENV_REFERENCE_PATTERN = re.compile(r"\$\{[^}]+\}")
_ENV_SCALAR_PATTERN = re.compile(
    r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\|(?P<format>bool|nullable-string))?\}$"
)


def _render_node(node: object, values: Mapping[str, str]) -> object:
    """Replace complete environment-backed YAML scalars recursively."""
    if isinstance(node, dict):
        return {key: _render_node(value, values) for key, value in node.items()}
    if isinstance(node, list):
        return [_render_node(value, values) for value in node]
    if not isinstance(node, str):
        return node

    if _ENV_REFERENCE_PATTERN.search(node) is None:
        return node
    match = _ENV_SCALAR_PATTERN.fullmatch(node)
    if match is None:
        raise ValueError(f"Environment placeholders must occupy an entire YAML scalar: {node!r}")
    name = match.group("name")
    value = values.get(name)
    if value is None:
        raise ValueError(f"Missing environment variable required by Data Flywheel config: {name}")
    if value == "":
        raise ValueError(
            f"Empty environment variable is not valid for Data Flywheel config: {name}"
        )
    value_format = match.group("format")
    if value_format is None:
        return value
    if value_format == "nullable-string":
        return None if value.lower() == "null" else value
    normalized_value = value.lower()
    if normalized_value not in {"true", "false"}:
        raise ValueError(f"Environment variable must be true or false: {name}")
    return normalized_value == "true"


def render_template(
    source: Path,
    output: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Render ``source`` atomically and fail if any required value is absent."""
    values = os.environ if environ is None else environ
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError("Data Flywheel config template must contain a YAML mapping")
    rendered = yaml.safe_dump(
        _render_node(loaded, values),
        sort_keys=False,
        allow_unicode=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, output)


def _parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    """Separate renderer options from the command following ``--``."""
    arguments = list(argv)
    try:
        separator = arguments.index("--")
    except ValueError as exc:
        raise ValueError("Expected '--' before the Data Flywheel command") from exc

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments[:separator])
    command = arguments[separator + 1 :]
    if not command:
        raise ValueError("A Data Flywheel command is required after '--'")
    return options, command


def main(argv: Sequence[str] | None = None) -> int:
    """Render the configuration and replace this wrapper with the real process."""
    options, command = _parse_args(sys.argv[1:] if argv is None else argv)
    render_template(options.source, options.output)
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
