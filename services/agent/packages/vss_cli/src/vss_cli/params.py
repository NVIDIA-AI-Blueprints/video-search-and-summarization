# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Derive Click options from a Pydantic input model.

A group declares one model per verb. That model is already mandatory -- SDD
§5.2 makes ``job.request`` "the normalized input model" and pins validation to
Pydantic -- so deriving the flags from it means the CLI surface, the MCP tool
schema (``model_json_schema()``) and the persisted record cannot drift from
each other. Declaring Click parameters *and* a model would be two statements
of the same 19 fields.

What is derived, and from where::

    top_k: int | None = Field(None, ge=1, le=1000, description="...")
    └─ name ──┘  └ type ┘        └── constraints ──┘  └──── help ────┘
       --top-k    IntRange(1, 1000)                    shown in --help

Deliberately narrow. It covers the shapes the VSS surface actually uses --
scalars, ``Literal`` choices, repeatable lists, tri-state booleans -- and
nothing else. Anything it cannot express (mutually exclusive flags, help
sections) is appended by the group as raw Click parameters rather than
smuggled through the model; see ``CommandGroup.extra_params``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Union
from typing import get_args
from typing import get_origin

import click

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel
    from pydantic.fields import FieldInfo

#: ``json_schema_extra`` key naming the CLI flag when it differs from the
#: field. ``--attribute`` populating ``attributes`` is the motivating case:
#: singular flag, plural field, repeatable.
FLAG_KEY = "cli_flag"

#: ``json_schema_extra`` key to keep a field out of the CLI entirely -- set on
#: fields that are populated from config or computed, never typed by a caller.
HIDE_KEY = "cli_hide"


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Strip ``| None``, reporting whether it was there."""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) is Union or str(get_origin(annotation)) == "<class 'types.UnionType'>":
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
        return annotation, len(args) != len(get_args(annotation))
    return annotation, False


def _constraint(field: FieldInfo, name: str) -> Any:
    """Read an annotated-types constraint (``ge``/``le``) off a field."""
    for meta in field.metadata:
        value = getattr(meta, name, None)
        if value is not None:
            return value
    return None


def _click_type(annotation: Any, field: FieldInfo) -> tuple[Any, bool]:
    """Map a field annotation to a Click type. Returns (type, is_multiple)."""
    inner, _ = _unwrap_optional(annotation)

    if get_origin(inner) is list:
        item = (get_args(inner) or (str,))[0]
        item_type, _ = _click_type(item, field)
        return item_type, True

    if get_origin(inner) is Literal:
        return click.Choice([str(v) for v in get_args(inner)]), False

    low, high = _constraint(field, "ge"), _constraint(field, "le")
    if inner is int:
        return (click.IntRange(low, high) if (low is not None or high is not None) else int), False
    if inner is float:
        return (click.FloatRange(low, high) if (low is not None or high is not None) else float), False
    if inner is bool:
        return bool, False
    return str, False


def option_for(name: str, field: FieldInfo) -> click.Option | None:
    """Build one Click option from one model field, or None if hidden."""
    extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
    if extra.get(HIDE_KEY):
        return None

    flag = str(extra.get(FLAG_KEY) or f"--{name.replace('_', '-')}")
    click_type, multiple = _click_type(field.annotation, field)
    inner, _ = _unwrap_optional(field.annotation)

    if inner is bool:
        # A flag whose declared name already reads as a negation ("--no-x")
        # becomes a single switch: the field defaults to on, so the positive
        # spelling would never change anything and is dead surface.
        if flag.startswith("--no-"):
            return click.Option([flag, name], is_flag=True, default=False, help=field.description or "")
        # Otherwise a --x/--no-x pair, so "unset" stays distinguishable from
        # "explicitly false" -- which matters only when something else (a
        # config file, a deployment default) can supply the value.
        negative = flag.replace("--", "--no-", 1)
        return click.Option(
            [f"{flag}/{negative}", name],
            default=None,
            help=field.description or "",
        )

    return click.Option(
        [flag, name],
        type=click_type,
        multiple=multiple,
        default=() if multiple else None,
        help=field.description or "",
        show_default=False,
    )


def options_from_model(model: type[BaseModel]) -> list[click.Option]:
    """Every CLI option a model declares, in declaration order."""
    out: list[click.Option] = []
    for name, field in model.model_fields.items():
        option = option_for(name, field)
        if option is not None:
            out.append(option)
    return out


def collect(model: type[BaseModel], values: dict[str, Any]) -> dict[str, Any]:
    """Drop unset values so model defaults win.

    Click hands back ``None`` for untouched options and ``()`` for untouched
    repeatables. Passing those through would overwrite the model's own
    defaults with null, so an unspecified flag must be absent, not empty.
    """
    supplied: dict[str, Any] = {}
    for name in model.model_fields:
        if name not in values:
            continue
        value = values[name]
        if value is None or value == ():
            continue
        supplied[name] = list(value) if isinstance(value, tuple) else value
    return supplied


def shared_options() -> Sequence[click.Option]:
    """Transport and output flags, identical in every group (SDD §8)."""
    # No --base-url here on purpose. An origin alone is not a deployment: the
    # services, indices and model ids come from probing it, which is what
    # `vss configure` does. A per-call origin would skip that discovery and
    # yield a deployment with no services, so every action would fail on the
    # first endpoint it needed. Configuring is a separate step by design.
    return (
        click.Option(
            ["--json", "json_payload"],
            help="Request as a JSON object matching the verb's input model.",
        ),
        click.Option(
            ["--output"],
            type=click.Choice(["json", "jsonl", "table"]),
            default="json",
            show_default=True,
            help="Output format.",
        ),
        click.Option(["--pretty/--raw"], "pretty", default=None, help="Pretty-print or emit compact output."),
        click.Option(
            ["--log-level"],
            type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
            default="WARNING",
            show_default=True,
            help="Logging verbosity.",
        ),
    )
