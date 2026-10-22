# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The tool contract: what a ``vision-ai <tool>`` command is made of.

First-party and third-party tools register the same way, as an entry point:

.. code-block:: toml

    [project.entry-points."vision_ai.tools"]
    acme = "acme_vision_ai.tool:TOOL"

A tool declares two pydantic models and one coroutine:

* ``Settings`` -- persistent parameters. The CLI Runtime resolves them per call
  as flags > ``VISION_AI_<TOOL>_<FIELD>`` env > ``~/.vision-ai/<tool>/config.json`` >
  model defaults, and ``vision-ai configure <tool>`` edits the file. ``SecretStr``
  fields are never exposed as flags and never written to job records.
* ``Input`` -- per-call arguments, flags only.
* ``execute(inputs, settings)`` -- the call itself, returning a pydantic result.

Every call is synchronous and recorded as a job in the memory store.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
import types
from typing import Annotated
from typing import Any
from typing import ClassVar
from typing import Union
from typing import get_args
from typing import get_origin

from pydantic import BaseModel
from pydantic import SecretStr

#: Contract version. A tool declaring a different version is refused at load time.
API_VERSION = 1


class Tool(ABC):
    """Base class for ``vision-ai`` tools."""

    api_version: ClassVar[int] = API_VERSION
    name: ClassVar[str]
    summary: ClassVar[str]
    Settings: ClassVar[type[BaseModel]]
    Input: ClassVar[type[BaseModel]]

    def prepare(self, inputs: BaseModel, settings: BaseModel, explicit: frozenset[str]) -> BaseModel:  # noqa: ARG002
        """Check and adjust settings before the job is recorded; return the settings to run with.

        ``explicit`` names the settings given as flags on this call. Raise
        ``nvidia_vision_ai.ConfigurationError`` (exit 3) or ``ValueError`` (exit 2)
        to reject the call without creating a job. Default: settings unchanged.
        """
        return settings

    @abstractmethod
    async def execute(self, inputs: BaseModel, settings: BaseModel) -> BaseModel:
        """Run the call. Raise ``nvidia_vision_ai`` errors or ``pydantic.ValidationError``."""

    async def check(self, settings: BaseModel) -> dict[str, Any]:  # noqa: ARG002
        """Probe the configured backend for ``vision-ai configure <tool> check``. Default: nothing to probe."""
        return {}

    def record_input(self, inputs: BaseModel, settings: BaseModel) -> dict[str, Any]:
        """What the job record keeps as ``input``: the call's inputs and settings, minus secrets."""
        return {**inputs.model_dump(mode="json"), **public_settings(settings)}


def unwrap(annotation: Any) -> Any:
    """Strip ``Annotated[...]`` and ``X | None`` down to the base type ``X``."""
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, types.UnionType):
            args = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(args) != 1:
                return annotation
            annotation = args[0]
        else:
            return annotation


def secret_fields(model: type[BaseModel]) -> frozenset[str]:
    """Names of fields typed ``SecretStr`` (optionally ``Annotated`` and/or ``| None``)."""
    return frozenset(name for name, field in model.model_fields.items() if unwrap(field.annotation) is SecretStr)


def public_settings(settings: BaseModel) -> dict[str, Any]:
    """Settings as JSON, secrets dropped."""
    return settings.model_dump(mode="json", exclude=set(secret_fields(type(settings))))
