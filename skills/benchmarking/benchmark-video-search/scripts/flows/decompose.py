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

import copy
import hashlib
import json
import math
import re
import time
import warnings
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from pathlib import Path

#: Where the agent's decomposition contract lives, relative to the repo root.
PROMPT_SOURCE = "services/agent/packages/vss_agents/src/vss_agents/tools/search.py"

#: From the search profile's ``llms`` block (dev-profile-search config.yml):
#: nim/openai profiles both pin these, so a run matches the deployment.
TEMPERATURE = 0.0
MAX_TOKENS = 4096

#: The agent decomposes with reasoning off (``reasoning_utils``). Nemotron chat
#: templates default ``enable_thinking`` to true, and a thinking reply is
#: reasoning text -- or ``content: null`` -- rather than the JSON object.
CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}

#: Models whose template reads that kwarg: the agent's
#: ``_ENABLE_THINKING_MARKERS``, kept equal by a test. Any other model is sent
#: nothing, since a strict OpenAI-compatible server rejects unknown fields.
ENABLE_THINKING_MARKERS = ("nemotron-3.5-lightning", "nemotron-3-nano")


def thinking_kwargs(model: str) -> dict[str, Any]:
    """The ``chat_template_kwargs`` the agent would send this model, if any."""
    name = (model or "").lower()
    return dict(CHAT_TEMPLATE_KWARGS) if any(m in name for m in ENABLE_THINKING_MARKERS) else {}


def thinking_off_tag(model: str) -> str | None:
    """The line the agent appends to the system message to turn thinking off.

    Mirrors ``reasoning_utils.get_thinking_tag(llm, False)``: Llama and NVIDIA
    Nemotron NIMs read it from the system prompt, not from a template kwarg.
    """
    name = (model or "").lower().translate(str.maketrans("_.", "--"))
    if name.startswith("nvidia/nvidia"):
        return None if "nemotron-3" in name else "/no_think"
    if name.startswith("nvidia/llama"):
        if "v1-0" in name or "v1-1" in name or name.endswith("v1"):
            return "detailed thinking off"
        return "/no_think"
    return None

_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL)

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
    prompt = re.search(r'QUERY_DECOMPOSITION_PROMPT = """(.*?)"""', text, re.DOTALL)
    few_shot = re.search(r'DEFAULT_FEW_SHOT_EXAMPLES = """(.*?)"""', text, re.DOTALL)
    if prompt is None or few_shot is None:
        raise DecompositionError(
            f"QUERY_DECOMPOSITION_PROMPT / DEFAULT_FEW_SHOT_EXAMPLES not found in {src}; "
            "the product source may have been restructured."
        )
    return prompt.group(1), few_shot.group(1)


def _strip_fences(text: str) -> str:
    """Undo markdown fencing. Mirrors what ``decompose_query`` does upstream."""
    stripped = _THINK_BLOCK.sub("", text).strip()
    if "```json" in stripped:
        return stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in stripped:
        parts = stripped.split("```")
        return (parts[1] if len(parts) > 1 else stripped).strip()
    return stripped


def validate_chat_template_kwargs(value: Any) -> dict[str, Any] | None:
    """Validate explicit JSON object settings and detach caller-owned containers."""
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("chat_template_kwargs must be a JSON object")

    def check(item: Any, ancestors: set[int]) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) not in (dict, list):
            raise ValueError(
                "chat_template_kwargs must contain only finite JSON values"
            )
        if id(item) in ancestors:
            raise ValueError("chat_template_kwargs must not contain cycles")
        ancestors.add(id(item))
        try:
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError("chat_template_kwargs object keys must be strings")
                values = item.values()
            else:
                values = item
            for child in values:
                check(child, ancestors)
        finally:
            ancestors.remove(id(item))

    try:
        check(value, set())
        return copy.deepcopy(value)
    except RecursionError as exc:
        raise ValueError("chat_template_kwargs nesting is too deep") from exc


class LiveDecomposer:
    """Decomposes each query against the deployment's LLM, and times it."""

    def __init__(
        self,
        llm_url: str,
        *,
        repo_root: Path,
        model: str | None = None,
        timeout_s: float = 120.0,
        chat_template_kwargs: dict[str, Any] | None = None,
        source_type: str = "video_file",
    ) -> None:
        explicit_settings = validate_chat_template_kwargs(chat_template_kwargs)
        if source_type not in ("video_file", "rtsp"):
            raise ValueError("source_type must be video_file or rtsp")
        self._source_type = source_type
        self._sources_label = "Video streams" if source_type == "rtsp" else "Video files"
        self._url = llm_url.rstrip("/")
        self._timeout = timeout_s
        self._prompt, self._few_shot = load_prompt(repo_root)
        self._model = model or self._discover_model()
        # Explicit settings, including {}, override model-specific defaults.
        self._chat_template_kwargs = (
            explicit_settings
            if explicit_settings is not None
            else thinking_kwargs(self._model) or None
        )
        self._thinking_tag = (
            None if explicit_settings is not None else thinking_off_tag(self._model)
        )
        self._system_prompt = (
            f"{_SYSTEM}\n{self._thinking_tag}" if self._thinking_tag else _SYSTEM
        )

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
                f"could not list models at {self._url}/v1/models: {e}. "
                "Check --llm-url, or pass --llm-model to skip discovery."
            ) from e
        if not isinstance(data, list) or not data:
            raise DecompositionError(
                f"{self._url} served an empty or malformed model list."
            )
        first = data[0]
        if not isinstance(first, dict) or not first.get("id"):
            raise DecompositionError(f"{self._url} served a model entry without an id.")
        if len(data) > 1:
            warnings.warn(
                f"{self._url} serves multiple LLM models; auto-selected {first['id']!r}. "
                "Pass --llm-model matching the deployed agent before comparing routing metrics.",
                stacklevel=2,
            )
        return str(first["id"])

    def describe(self) -> dict[str, Any]:
        return {
            "llm_url": self._url,
            "model": self._model,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "prompt_source": PROMPT_SOURCE,
            "prompt_sha256": hashlib.sha256(self._prompt.encode()).hexdigest(),
            "source_type": self._source_type,
            "system_thinking_tag": self._thinking_tag,
            "system_prompt": self._system_prompt,
            "system_prompt_sha256": hashlib.sha256(self._system_prompt.encode()).hexdigest(),
            "few_shot_sha256": hashlib.sha256(self._few_shot.encode()).hexdigest(),
            **(
                {"chat_template_kwargs": copy.deepcopy(self._chat_template_kwargs)}
                if self._chat_template_kwargs is not None
                else {}
            ),
        }

    def decompose(
        self,
        query: str,
        video_sources: list[str] | None = None,
        evidence_sink: Any = None,
        query_id: str | None = None,
    ) -> tuple[dict[str, Any], float]:
        """Return ``(decomposition, seconds)``.

        ``seconds`` is wall clock around the call, so it belongs to the query's
        end-to-end cost the same way the CLI subprocess does.
        """
        sources = (
            f"{self._sources_label}: {', '.join(video_sources)}"
            if video_sources
            else "No specific sources available"
        )
        body = {
            "model": self._model,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": self._system_prompt},
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
        if self._chat_template_kwargs is not None:
            body["chat_template_kwargs"] = copy.deepcopy(self._chat_template_kwargs)
        evidence = {
            "original_query": query,
            "query_id": query_id,
            "submitted_request": body,
            "model": self._model,
            "prompt_source": PROMPT_SOURCE,
            "configuration": self.describe(),
            "raw_response": None,
            "parsed": None,
            "error": None,
        }
        if evidence_sink:
            evidence_sink(evidence)
        started = time.perf_counter()
        try:
            resp = requests.post(
                f"{self._url}/v1/chat/completions", json=body, timeout=self._timeout
            )
            evidence["raw_http_response"] = resp.text
            evidence["http_status"] = getattr(resp, "status_code", None)
            if evidence_sink:
                evidence_sink(evidence)
            resp.raise_for_status()
            message = resp.json()["choices"][0]["message"]
            evidence["raw_message"] = copy.deepcopy(message)
            content = message["content"]
        except (
            requests.RequestException,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as e:
            evidence.update(error=str(e), inference_s=time.perf_counter() - started)
            if evidence_sink:
                evidence_sink(evidence)
            raise DecompositionError(
                f"decomposition request failed for {query!r}: {e}"
            ) from e
        elapsed = time.perf_counter() - started
        evidence.update(raw_response=content, inference_s=elapsed)
        if evidence_sink:
            evidence_sink(evidence)

        if not isinstance(content, str) or not _strip_fences(content):
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            error = (
                f"decomposition for {query!r} returned no content"
                + (f" (only reasoning: {str(reasoning)[:120]!r})" if reasoning else "")
                + ("; the model answered in thinking mode." if reasoning or content else ".")
            )
            evidence["error"] = error
            if evidence_sink:
                evidence_sink(evidence)
            raise DecompositionError(error)
        try:
            decomposition = json.loads(_strip_fences(content))
        except json.JSONDecodeError as e:
            evidence["error"] = str(e)
            if evidence_sink:
                evidence_sink(evidence)
            raise DecompositionError(
                f"decomposition for {query!r} was not JSON: {content[:200]!r}"
            ) from e
        if not isinstance(decomposition, dict):
            evidence["error"] = "expected JSON object"
            if evidence_sink:
                evidence_sink(evidence)
            raise DecompositionError(
                f"decomposition for {query!r} was {type(decomposition).__name__}, expected object"
            )
        from flows.routing import validate_decomposition

        try:
            validate_decomposition(decomposition)
        except ValueError as exc:
            evidence["error"] = str(exc)
            if evidence_sink:
                evidence_sink(evidence)
            raise DecompositionError(
                f"invalid decomposition for {query!r}: {exc}"
            ) from exc
        # The prompt marks has_action REQUIRED, but the model omits it often
        # enough that routing would silently fall through to `fusion` for
        # attribute-only queries. Absent stays absent -- routing treats it as
        # unknown and says so -- rather than being invented here.
        evidence["parsed"] = decomposition
        if evidence_sink:
            evidence_sink(evidence)
        return decomposition, elapsed
