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

"""Build adapters from subject configs, and look up what each model is believed to support.

Adding an OpenAI-compatible model needs no code: a subject entry in the run config, and a ``capabilities.json``
entry (``verified: false`` until a read-only live check confirms it). A provider with a different API adds one
module implementing ``base.ModelAdapter`` and one ``ADAPTERS`` entry; nothing in tasks or scorers changes.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from evalv2.adapters.base import Capabilities, ModelAdapter
from evalv2.adapters.openai_compat import ENDPOINTS, OpenAICompatAdapter
from evalv2.adapters.vss import VssAgentAdapter, build_vss_adapter

CAPABILITIES_PATH = Path(__file__).resolve().with_name("capabilities.json")


def load_capabilities(path: Path = CAPABILITIES_PATH) -> dict[str, Capabilities]:
    models = json.loads(path.read_text())["models"]
    return {model: Capabilities(**{**entry, "listed_on": tuple(entry.get("listed_on", ()))})
            for model, entry in models.items()}


def capabilities_for(model: str, table: Mapping[str, Capabilities] | None = None) -> Capabilities:
    """A model missing from the table supports nothing beyond text (and is reported as unverified)."""
    return (table if table is not None else load_capabilities()).get(model, Capabilities(notes="not in table"))


def _openai_compat(subject: Any, **kwargs: Any) -> ModelAdapter:
    if subject.endpoint not in ENDPOINTS:
        raise ValueError(f"subject {subject.id}: unknown endpoint {subject.endpoint!r}; known: {sorted(ENDPOINTS)}")
    request = dict(getattr(subject, "request", None) or {})
    unknown = sorted(set(request) - {"extra_body", "builder", "prompt"})
    if unknown:
        raise ValueError(f"subject {subject.id}: an openai_compat subject's request takes builder, prompt and extra_body; got {unknown}")
    return OpenAICompatAdapter(subject.id, subject.model, ENDPOINTS[subject.endpoint],
                               capabilities_for(subject.model), extra_body=request.get("extra_body"), **kwargs)


ADAPTERS: dict[str, Callable[..., ModelAdapter]] = {"openai_compat": _openai_compat}
PIPELINE_ADAPTERS = ("vss",)  # whole-pipeline subjects (Track B): built by build_pipeline_adapter, not build_adapter


def build_adapter(subject: Any, **kwargs: Any) -> ModelAdapter:
    if subject.adapter not in ADAPTERS:
        raise ValueError(f"subject {subject.id}: unknown adapter {subject.adapter!r}; known: {sorted(ADAPTERS)}")
    return ADAPTERS[subject.adapter](subject, **kwargs)


def build_pipeline_adapter(subject: Any, **kwargs: Any) -> VssAgentAdapter:
    """A Track B subject: the local experiment agent running one pipeline profile with the subject's P1 model."""
    if subject.adapter not in PIPELINE_ADAPTERS:
        raise ValueError(f"subject {subject.id}: {subject.adapter!r} is not a pipeline adapter; "
                         f"known: {list(PIPELINE_ADAPTERS)}")
    return build_vss_adapter(subject, capabilities=capabilities_for(subject.model), **kwargs)
