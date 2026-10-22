# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Click options generated from a tool's pydantic models."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal
from typing import get_args
from typing import get_origin

import click
from pydantic import BaseModel

from nvidia_vision_ai.tool import secret_fields
from nvidia_vision_ai.tool import unwrap

#: ``json_schema_extra`` key naming a boolean field's off-switch, e.g.
#: ``{NEGATIVE_FLAG: "--disable-reasoning"}``. Default: ``--no-<field>``.
NEGATIVE_FLAG = "vision_ai_negative_flag"


def _click_type(annotation: Any) -> click.ParamType | type:
    base = unwrap(annotation)
    if get_origin(base) is Literal:
        return click.Choice([str(arg) for arg in get_args(base)])
    if base is Path:
        return click.Path(path_type=Path)
    if base in (int, float, str):
        return base
    return str


def options_for(model: type[BaseModel], *, all_optional: bool = False) -> list[click.Option]:
    """One ``--field-name`` option per field, default ``None`` so unset flags stay unset.

    ``SecretStr`` fields get no flag: secrets come from env or the config file,
    never from argv (shell history, process listings). Booleans become an
    on/off pair. Required fields are required flags unless ``all_optional``.
    Bounds are enforced by pydantic, not click, so every error reads the same.
    """
    options: list[click.Option] = []
    secrets = secret_fields(model)
    for name, field in model.model_fields.items():
        if name in secrets:
            continue
        flag = "--" + name.replace("_", "-")
        help_text = field.description
        if unwrap(field.annotation) is bool:
            extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
            negative = extra.get(NEGATIVE_FLAG) or f"--no-{name.replace('_', '-')}"
            options.append(click.Option([f"{flag}/{negative}", name], default=None, help=help_text))
            continue
        options.append(
            click.Option(
                [flag, name],
                type=_click_type(field.annotation),
                required=field.is_required() and not all_optional,
                default=None,
                help=help_text,
            )
        )
    return options


def given(values: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    """The flags for ``model`` that the caller actually passed."""
    return {name: value for name, value in values.items() if name in model.model_fields and value is not None}
