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

"""Turning a natural-language query into the search arguments, via the LLM.

The agent decomposes every query before retrieving. This benchmark drives the
CLI, which takes the decomposed arguments directly, so the decomposition has to
happen here instead.

Done **on every measured request**, because that is what a user of the product
pays for: the agent decomposes each question before it retrieves. A worker
calls :meth:`QueryRouter.plan` and then spawns the CLI, and the run reports the
LLM's share (``decompose_sec``), the CLI's (``latency_sec``) and the sum
(``e2e_latency_sec``) side by side.

The LLM runs at ``temperature=0`` but is not guaranteed deterministic, so a
request can route to a path other than its query's. Such a request is kept --
it is part of what the product does -- and counted in throughput and path
stability, but not in its cell's per-path latency. The route check before the
sweep (:func:`bench.run.route_check`) refuses a query that is not stable to
begin with.

The prompt is read out of the product source at run time rather than copied
here. A vendored copy drifts silently, and then the benchmark measures routing
the deployment does not do.
"""

from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import requests

from .routing import pinned_plan, plan_for

#: Where the agent's decomposition contract lives, relative to the repo root.
PROMPT_SOURCE = "services/agent/packages/vss_agents/src/vss_agents/tools/search.py"

#: Where the agent decides whether the model reasons. Loaded and called rather
#: than reimplemented: which models take ``enable_thinking``, and which take a
#: ``/no_think`` system tag instead, is a list that changes with every model
#: the profiles ship. A copy here that fell behind would leave reasoning on,
#: and the benchmark would measure a decomposition the deployment never makes.
REASONING_SOURCE = (
    "services/agent/packages/vss_agents/src/vss_agents/utils/reasoning_utils.py"
)

#: Pinned to match the search profile's ``llms`` block, so the benchmark asks
#: for decompositions the same way the deployment does.
TEMPERATURE = 0.0
MAX_TOKENS = 4096

_SYSTEM = (
    "You are a helpful assistant that extracts search parameters from natural "
    "language queries. Return only valid JSON."
)


class DecompositionError(RuntimeError):
    """The LLM was unreachable, or returned something that was not a decomposition."""


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up to the VSS checkout holding the agent source.

    Found by marker rather than by counting path components, so moving this
    skill within the repo does not break it. ``None`` when there is no
    checkout above us, which the caller turns into a readable error naming
    ``--vss-repo-root``.
    """
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "services/agent/pyproject.toml").exists():
            return parent
    return None


def load_prompt(repo_root: Path) -> tuple[str, str]:
    """Return ``(prompt_template, few_shot_examples)`` from the product source."""
    src = Path(repo_root) / PROMPT_SOURCE
    if not src.exists():
        raise DecompositionError(
            f"cannot read the decomposition prompt: {src} not found. This skill "
            "reads it from the product source at run time so the benchmark "
            "cannot drift from the deployment -- point --vss-repo-root at a "
            "VSS checkout."
        )
    text = src.read_text(encoding="utf-8")
    prompt = re.search(r'QUERY_DECOMPOSITION_PROMPT = """(.*?)"""', text, re.DOTALL)
    few_shot = re.search(r'DEFAULT_FEW_SHOT_EXAMPLES = """(.*?)"""', text, re.DOTALL)
    if prompt is None or few_shot is None:
        raise DecompositionError(
            f"QUERY_DECOMPOSITION_PROMPT / DEFAULT_FEW_SHOT_EXAMPLES not found in "
            f"{src}; the product source may have been restructured."
        )
    return prompt.group(1), few_shot.group(1)


def load_reasoning_controls(repo_root: Path, model: str) -> tuple[str | None, dict]:
    """Return ``(thinking_tag, request_extras)`` for this model, as the agent does.

    The agent turns reasoning *off* for decomposition, by two mechanisms that
    apply to different model families: a ``/no_think`` style tag appended to
    the system message, and an ``enable_thinking`` chat-template kwarg on the
    request. Nemotron 3.5 Lightning takes the second one, and the chat
    template defaults it to true -- so a caller that omits it gets a reply
    that is all reasoning and whose ``content`` is null.

    The product's own functions are loaded from source and called with a stub
    standing in for the LangChain client, rather than reimplemented here. They
    branch on model-name markers that change whenever a profile ships a new
    model; a copy would drift, and the failure mode of drift is silent.
    """
    src = Path(repo_root) / REASONING_SOURCE
    if not src.exists():
        raise DecompositionError(
            f"cannot read the reasoning controls: {src} not found. Without "
            "them this benchmark would decompose with reasoning on, which is "
            "not what the deployment does -- point --vss-repo-root at a VSS "
            "checkout."
        )
    spec = importlib.util.spec_from_file_location("_vss_reasoning_utils", src)
    if spec is None or spec.loader is None:
        raise DecompositionError(f"cannot load {src} as a module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001 - any import error means the same thing
        raise DecompositionError(f"cannot load {src}: {e}") from e

    # Named ChatOpenAI because that is the branch matching how this script
    # talks to the NIM: raw OpenAI-compatible HTTP. The ChatNVIDIA branch
    # produces the same kwarg for these models, just not wrapped in
    # extra_body, so the choice does not change what goes on the wire.
    stub = type("ChatOpenAI", (), {"model_name": model, "model": model})()
    try:
        tag = module.get_thinking_tag(stub, False)
        kwargs = module.get_llm_reasoning_bind_kwargs(stub, False)
    except Exception as e:  # noqa: BLE001
        raise DecompositionError(f"{src} could not answer for {model!r}: {e}") from e

    # ChatOpenAI cannot send chat_template_kwargs at the top level, so the
    # agent nests it in extra_body and the OpenAI client merges it back up.
    # Talking HTTP directly, this script does that merge itself.
    extras = dict(kwargs.pop("extra_body", {}) or {})
    extras.update(kwargs)
    return tag, extras


def strip_fences(text: str) -> str:
    """Undo markdown fencing, the way the agent's own parser does."""
    stripped = text.strip()
    if "```json" in stripped:
        return stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in stripped:
        parts = stripped.split("```")
        return (parts[1] if len(parts) > 1 else stripped).strip()
    return stripped


def require_llm_url(services_config: dict[str, Any]) -> str:
    """The OpenAI-compatible LLM endpoint. Configured, never inferred.

    A sibling skill derives this as port 30081 on the agent's host, which is
    the NIM's NodePort under Docker Compose. Under Helm the NIM Operator
    gives the LLM a ClusterIP Service named after the NIMService -- so there
    is no port to derive it from, and no name to guess either, because the
    name depends on which model the profile deploys. Hence a refusal rather
    than a default: a wrong endpoint here surfaces only at path resolution,
    after ingest has already run.
    """
    url = str((services_config or {}).get("llm_url") or "").strip()
    if not url:
        raise DecompositionError(
            "services.llm_url is not set, and it cannot be inferred: this "
            "skill targets a Helm deployment, where the LLM has its own "
            "ClusterIP Service named after the deployed model.\n"
            "  Set it, e.g.\n"
            '    llm_url: "http://nemotron-35-lightning-30b-a3b:8000"\n'
            "  Find yours with: kubectl get svc -n <namespace> "
            "| grep -iE 'nemotron|nim|llm'"
        )
    return url.rstrip("/")


class LiveDecomposer:
    """Decomposes queries against the deployment's own LLM, and times it."""

    def __init__(
        self,
        llm_url: str,
        repo_root: Path,
        model: str | None = None,
        timeout_sec: float = 120.0,
    ) -> None:
        self._url = llm_url.rstrip("/")
        self._timeout = timeout_sec
        self._prompt, self._few_shot = load_prompt(repo_root)
        self._model = model or self._discover_model()
        self._thinking_tag, self._request_extras = load_reasoning_controls(
            repo_root, self._model
        )
        self._system = _SYSTEM
        if self._thinking_tag:
            self._system += f"\n{self._thinking_tag}"

    @property
    def model(self) -> str:
        return self._model

    def _discover_model(self) -> str:
        try:
            resp = requests.get(f"{self._url}/v1/models", timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()["data"]
        except (requests.RequestException, KeyError, TypeError, ValueError) as e:
            raise DecompositionError(
                f"could not list models at {self._url}/v1/models: {e}. Check "
                "services.llm_url, or set decomposition.model to skip discovery."
            ) from e
        if not isinstance(data, list) or not data:
            raise DecompositionError(
                f"{self._url} served an empty or malformed model list."
            )
        first = data[0]
        if not isinstance(first, dict) or not first.get("id"):
            raise DecompositionError(
                f"{self._url} served a model entry without an id."
            )
        return str(first["id"])

    def probe(self) -> None:
        """Check the endpoint answers and serves this model, before any ingest."""
        try:
            resp = requests.get(f"{self._url}/v1/models", timeout=min(self._timeout, 15))
            resp.raise_for_status()
            served = {str(m.get("id")) for m in resp.json()["data"] if isinstance(m, dict)}
        except (requests.RequestException, KeyError, TypeError, ValueError) as e:
            raise DecompositionError(
                f"the LLM at {self._url} did not answer /v1/models: {e}"
            ) from e
        if self._model not in served:
            raise DecompositionError(
                f"{self._url} does not serve {self._model!r} "
                f"(serves: {', '.join(sorted(served)) or 'nothing'}); "
                "check decomposition.model"
            )

    def describe(self) -> dict[str, Any]:
        return {
            "llm_url": self._url,
            "model": self._model,
            "temperature": TEMPERATURE,
            "prompt_source": PROMPT_SOURCE,
            # Recorded because it decides whether the model reasons, and a
            # reasoning decomposition is a different measurement.
            "thinking_tag": self._thinking_tag,
            "request_extras": self._request_extras,
        }

    def decompose(
        self,
        query: str,
        video_sources: list[str] | None = None,
    ) -> tuple[dict[str, Any], float]:
        """Return ``(decomposition, seconds)``."""
        sources = (
            f"Video files: {', '.join(video_sources)}" if video_sources
            else "No specific sources available"
        )
        body = {
            "model": self._model,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": self._system},
                {
                    "role": "user",
                    "content": self._prompt.format(
                        video_sources=sources,
                        few_shot_examples=self._few_shot,
                        user_query=query,
                    ),
                },
            ],
            **self._request_extras,
        }
        started = time.perf_counter()
        try:
            resp = requests.post(
                f"{self._url}/v1/chat/completions", json=body, timeout=self._timeout
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            raise DecompositionError(
                f"decomposition request failed for {query!r}: {e}"
            ) from e
        elapsed = time.perf_counter() - started

        content = (choice.get("message") or {}).get("content")
        if not content:
            # A null content means the model answered with something that was
            # not an answer. Almost always reasoning: the reply is all
            # reasoning_content and the budget ran out before any JSON. Report
            # the evidence rather than a TypeError three frames down.
            reasoning = (choice.get("message") or {}).get("reasoning_content") or ""
            raise DecompositionError(
                f"decomposition for {query!r} came back with no content "
                f"(finish_reason={choice.get('finish_reason')!r}, "
                f"{len(reasoning)} chars of reasoning_content).\n"
                f"  Reasoning should be off here: the agent disables it for "
                f"decomposition, and this request sent {self._request_extras or '{}'}"
                + (f" plus the system tag {self._thinking_tag!r}"
                   if self._thinking_tag else "")
                + f".\n  If {self._model!r} is not covered by "
                "_ENABLE_THINKING_MARKERS in the agent's reasoning_utils.py, "
                "neither the deployment nor this benchmark can turn its "
                "reasoning off."
                + (f"\n  Reasoning began: {reasoning[:200]!r}" if reasoning else "")
            )

        try:
            decomposition = json.loads(strip_fences(content))
        except json.JSONDecodeError as e:
            raise DecompositionError(
                f"decomposition for {query!r} was not JSON: {content[:200]!r}"
            ) from e
        if not isinstance(decomposition, dict):
            raise DecompositionError(
                f"decomposition for {query!r} was "
                f"{type(decomposition).__name__}, expected an object"
            )
        return decomposition, elapsed


@dataclass
class QueryRouter:
    """What a worker needs to turn a query into a plan, once per request.

    ``decomposer`` is shared across worker threads: each call is one
    independent ``requests.post`` and holds no state between calls.
    """

    decomposer: Any = None
    video_sources: list[str] | None = None
    source_type: str = "video_file"
    #: Query id -> path for queries pinned by ``decomposition.pin_paths``. A
    #: pinned query never calls the LLM.
    pinned: dict[str, str] = field(default_factory=dict)

    def plan(self, query_id: str, query: str) -> dict[str, Any]:
        """The plan for one request. Raises :class:`DecompositionError`."""
        if query_id in self.pinned:
            return pinned_plan(query, self.pinned[query_id], self.source_type)
        if self.decomposer is None:
            raise DecompositionError(f"{query_id} is not pinned and no LLM is configured")
        decomposition, _ = self.decomposer.decompose(query, self.video_sources)
        try:
            return plan_for(query, decomposition, self.source_type)
        except ValueError as e:
            raise DecompositionError(str(e)) from e
