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

"""Live query decomposition, per query, using the deployment's own LLM.

The CLI takes an explicit retrieval path and explicit arguments; it has no
decomposition step. The agent path does: it sends the user's sentence to an LLM
that returns ``{query, attributes, has_action, object_ids, top_k, ...}``, and
that structure decides which of the four paths runs.

Decomposing here rather than baking the answer into a dataset means the run
measures what a deployment actually does -- including how long decomposition
takes, which is otherwise invisible in a latency breakdown that starts at
``search()``. It also means routing is only as good as the deployed model, and
a wrong decomposition shows up as a retrieval regression. That is the intended
behaviour: the eval reports the deployment, not an idealised version of it.

The prompt is read from the product source at run time rather than copied::

    services/agent/packages/vss_agents/src/vss_agents/tools/search.py

This skill ships inside that repo, so the file is always present and always the
version this checkout would send. A vendored copy would silently diverge the
first time someone edited the prompt.

Not deterministic. This model returns different attributes for the same query
between runs even at ``temperature=0``, so two runs can route the same query
differently. Raw retrieval metrics stay comparable only for queries that routed
the same way -- ``summary.decomposition`` reports the path distribution so a
shift is visible rather than silently changing what was measured.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

#: Where the agent's decomposition contract lives, relative to the repo root.
PROMPT_SOURCE = "services/agent/packages/vss_agents/src/vss_agents/tools/search.py"

#: From the search profile's ``llms`` block (dev-profile-search config.yml):
#: nim/openai profiles both pin these, so a run matches the deployment.
TEMPERATURE = 0.0
MAX_TOKENS = 4096

_SYSTEM = (
    "You are a helpful assistant that extracts search parameters from natural "
    "language queries. Return only valid JSON."
)


class DecompositionError(RuntimeError):
    """The LLM was unreachable, or returned something that was not a decomposition."""


def load_prompt(repo_root: Path) -> tuple[str, str]:
    """Return ``(prompt_template, few_shot_examples)`` from the product source."""
    src = repo_root / PROMPT_SOURCE
    if not src.exists():
        raise DecompositionError(
            f"cannot read the decomposition prompt: {src} not found. "
            "This skill must run from inside a VSS checkout."
        )
    text = src.read_text()
    prompt = re.search(r'QUERY_DECOMPOSITION_PROMPT = """(.*?)"""', text, re.S)
    few_shot = re.search(r'DEFAULT_FEW_SHOT_EXAMPLES = """(.*?)"""', text, re.S)
    if prompt is None or few_shot is None:
        raise DecompositionError(
            f"QUERY_DECOMPOSITION_PROMPT / DEFAULT_FEW_SHOT_EXAMPLES not found in {src}; "
            "the product source may have been restructured."
        )
    return prompt.group(1), few_shot.group(1)


def _strip_fences(text: str) -> str:
    """Undo markdown fencing. Mirrors what ``decompose_query`` does upstream."""
    stripped = text.strip()
    if "```json" in stripped:
        return stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in stripped:
        parts = stripped.split("```")
        return (parts[1] if len(parts) > 1 else stripped).strip()
    return stripped


class LiveDecomposer:
    """Decomposes each query against the deployment's LLM, and times it."""

    def __init__(
        self,
        llm_url: str,
        *,
        repo_root: Path,
        model: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._url = llm_url.rstrip("/")
        self._timeout = timeout_s
        self._prompt, self._few_shot = load_prompt(repo_root)
        self._model = model or self._discover_model()

    @property
    def model(self) -> str:
        return self._model

    def _discover_model(self) -> str:
        try:
            resp = requests.get(f"{self._url}/v1/models", timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()["data"]
        except (requests.RequestException, KeyError, ValueError) as e:
            raise DecompositionError(
                f"could not list models at {self._url}/v1/models: {e}. "
                "Check --llm-url, or pass --llm-model to skip discovery."
            ) from e
        if not data:
            raise DecompositionError(f"{self._url} served an empty model list.")
        return str(data[0]["id"])

    def describe(self) -> dict[str, Any]:
        return {
            "llm_url": self._url,
            "model": self._model,
            "temperature": TEMPERATURE,
            "prompt_source": PROMPT_SOURCE,
        }

    def decompose(self, query: str, video_sources: list[str] | None = None) -> tuple[dict[str, Any], float]:
        """Return ``(decomposition, seconds)``.

        ``seconds`` is wall clock around the call, so it belongs to the query's
        end-to-end cost the same way the CLI subprocess does.
        """
        sources = (
            f"Video files: {', '.join(video_sources)}" if video_sources else "No specific sources available"
        )
        body = {
            "model": self._model,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": self._prompt.format(
                        video_sources=sources,
                        few_shot_examples=self._few_shot,
                        user_query=query,
                    ),
                },
            ],
        }
        started = time.perf_counter()
        try:
            resp = requests.post(
                f"{self._url}/v1/chat/completions", json=body, timeout=self._timeout
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            raise DecompositionError(f"decomposition request failed for {query!r}: {e}") from e
        elapsed = time.perf_counter() - started

        try:
            decomposition = json.loads(_strip_fences(content))
        except json.JSONDecodeError as e:
            raise DecompositionError(
                f"decomposition for {query!r} was not JSON: {content[:200]!r}"
            ) from e
        if not isinstance(decomposition, dict):
            raise DecompositionError(
                f"decomposition for {query!r} was {type(decomposition).__name__}, expected object"
            )
        # The prompt marks has_action REQUIRED, but the model omits it often
        # enough that routing would silently fall through to `fusion` for
        # attribute-only queries. Absent stays absent -- routing treats it as
        # unknown and says so -- rather than being invented here.
        return decomposition, elapsed
