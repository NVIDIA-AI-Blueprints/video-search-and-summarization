# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Resolve static memory policy and safe per-request opt-outs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import config as config_mod

if TYPE_CHECKING:
    from .config import Deployment


class MemoryPolicyInputError(ValueError):
    """A per-request memory override contradicts static operator policy."""


@dataclass(frozen=True, slots=True)
class EffectiveMemoryPolicy:
    persist: bool
    write_note: bool


def effective_persist(deployment: Deployment | None, *, no_persist: bool) -> bool:
    """Whether this job should initialize and write authoritative memory."""
    if no_persist or deployment is None:
        return False
    memory_config = deployment.memory
    return bool(memory_config and memory_config.enabled and memory_config.persist_by_default)


def resolve_memory_policy(
    deployment: Deployment | None,
    *,
    no_persist: bool,
    note_override: bool | None,
) -> EffectiveMemoryPolicy:
    """Resolve static persistence plus a tri-state per-request note choice."""
    persist = effective_persist(deployment, no_persist=no_persist)
    if note_override is False:
        return EffectiveMemoryPolicy(persist=persist, write_note=False)
    if note_override is True:
        if no_persist:
            raise MemoryPolicyInputError("cannot combine --write-memory-note with --no-persist")
        memory_config = deployment.memory if deployment is not None else None
        if memory_config is None:
            raise config_mod.ConfigError(
                "Markdown memory is not configured; run `vss configure memory --markdown "
                "--workspace /absolute/openclaw/workspace`"
            )
        if not memory_config.enabled or not memory_config.persist_by_default:
            raise MemoryPolicyInputError(
                "--write-memory-note cannot override disabled static persistence; "
                "run `vss configure memory --enable --persist-by-default`"
            )
        if not memory_config.markdown.enabled:
            raise config_mod.ConfigError(
                "Markdown memory is disabled; run `vss configure memory --markdown "
                "--workspace /absolute/openclaw/workspace`"
            )
        return EffectiveMemoryPolicy(persist=True, write_note=True)

    memory_config = deployment.memory if deployment is not None else None
    write_note = bool(
        persist and memory_config and memory_config.markdown.enabled and memory_config.markdown.write_by_default
    )
    return EffectiveMemoryPolicy(persist=persist, write_note=write_note)


__all__ = [
    "EffectiveMemoryPolicy",
    "MemoryPolicyInputError",
    "effective_persist",
    "resolve_memory_policy",
]
