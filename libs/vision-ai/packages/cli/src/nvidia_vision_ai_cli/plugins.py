# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Entry-point discovery for ``vision-ai`` tools (group ``vision_ai.tools``).

A tool is imported only when its command is resolved, so one broken plugin
cannot stop the others from running. ``VISION_AI_DISABLE_PLUGINS=a,b`` skips
named tools when an installed plugin is actively breaking the CLI.
"""

from __future__ import annotations

from importlib.metadata import entry_points
import os

from pydantic import BaseModel

from nvidia_vision_ai.tool import API_VERSION
from nvidia_vision_ai.tool import Tool

TOOLS_GROUP = "vision_ai.tools"
DISABLE_ENV = "VISION_AI_DISABLE_PLUGINS"

#: Names owned by the CLI itself; a plugin cannot shadow them.
RESERVED = frozenset({"configure", "memory"})


class PluginLoadError(Exception):
    """A registered tool could not be imported or does not honour the contract."""


def _disabled() -> frozenset[str]:
    return frozenset(name.strip() for name in os.environ.get(DISABLE_ENV, "").split(",") if name.strip())


def discover() -> list[str]:
    """Names of installed, enabled tools, sorted."""
    disabled = _disabled()
    return sorted(
        {ep.name for ep in entry_points(group=TOOLS_GROUP) if ep.name not in disabled and ep.name not in RESERVED}
    )


def load(name: str) -> Tool:
    if name in RESERVED or name in _disabled():
        raise PluginLoadError(f"tool {name!r} is reserved or disabled")
    matches = [ep for ep in entry_points(group=TOOLS_GROUP) if ep.name == name]
    if not matches:
        raise PluginLoadError(f"no tool named {name!r}")
    if len(matches) > 1:
        sources = ", ".join(sorted(ep.value for ep in matches))
        raise PluginLoadError(f"tool {name!r} is registered more than once ({sources})")
    try:
        obj = matches[0].load()
        tool = obj() if isinstance(obj, type) and issubclass(obj, Tool) else obj
    except Exception as exc:
        raise PluginLoadError(f"tool {name!r} failed to load: {type(exc).__name__}: {exc}") from exc
    if not isinstance(tool, Tool):
        raise PluginLoadError(f"tool {name!r} is not a nvidia_vision_ai.Tool ({type(obj).__name__})")
    if getattr(tool, "api_version", None) != API_VERSION:
        raise PluginLoadError(f"tool {name!r} does not declare contract v{API_VERSION}")
    for attr in ("Settings", "Input"):
        model = getattr(tool, attr, None)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise PluginLoadError(f"tool {name!r} has no pydantic {attr} model")
    if getattr(tool, "name", None) != name or not isinstance(getattr(tool, "summary", None), str):
        raise PluginLoadError(f"entry point {name!r} does not load a tool named {name!r} with a summary")
    return tool
