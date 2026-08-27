# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Text-only OpenAI-compatible sufficiency judge and answer synthesizer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

import httpx
from pydantic import ValidationError

from vss_core._foundation.errors import ConfigurationError
from vss_core.introspection.models import IntrospectionSettings
from vss_core.introspection.models import SufficiencyDecision
from vss_core.introspection.models import VLMEvidence

if TYPE_CHECKING:
    from vss_core.memory.models import UnifiedMemoryRecord


class InvalidJudgeResponseError(ValueError):
    """The judge returned invalid JSON, an invalid schema, or ungrounded data."""


class OpenAIIntrospectionClient:
    """OpenAI-compatible text client implementing both introspection text roles."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        settings: IntrospectionSettings | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ConfigurationError("introspection base_url must be non-empty")
        if not model.strip():
            raise ConfigurationError("introspection model must be non-empty")
        if client is not None and transport is not None:
            raise ConfigurationError("provide either an httpx client or transport, not both")

        self._base_url = _normalize_base_url(base_url)
        self._model = model.strip()
        self._api_key = api_key
        self._settings = settings or IntrospectionSettings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(self._settings.timeout_seconds)),
            transport=transport,
        )

    async def judge(self, *, query: str, records: list[UnifiedMemoryRecord]) -> SufficiencyDecision:
        """Judge memory sufficiency, retrying once only for invalid model output."""
        prompt = _judge_prompt(query, records, self._settings.sufficiency_threshold)
        last_error: InvalidJudgeResponseError | None = None
        for _attempt in range(2):
            content = await self._chat(prompt, json_only=True)
            try:
                decision = _parse_decision(content)
                return decision.validate_grounding(records)
            except (InvalidJudgeResponseError, ValidationError, ValueError) as error:
                last_error = InvalidJudgeResponseError(f"invalid sufficiency judge response: {error}")
        if last_error is None:  # pragma: no cover - the fixed two-attempt loop always sets this
            raise AssertionError("judge retry loop produced no result")
        raise last_error

    async def synthesize(
        self,
        *,
        query: str,
        memory_evidence: list[UnifiedMemoryRecord],
        vlm_evidence: list[VLMEvidence],
        unresolved_gaps: list[str],
    ) -> str:
        """Produce a final answer from supplied evidence without another judge pass."""
        prompt = _synthesis_prompt(query, memory_evidence, vlm_evidence, unresolved_gaps)
        answer = (await self._chat(prompt, json_only=False)).strip()
        if not answer:
            raise ValueError("answer synthesizer returned empty content")
        return answer

    async def _chat(self, prompt: str, *, json_only: bool) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_only:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        response = await self._client.post(self._chat_completions_url, headers=headers, json=payload)
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("OpenAI-compatible response contained no message content") from error
        if not isinstance(content, str):
            raise ValueError("OpenAI-compatible message content must be text")
        return content

    @property
    def _chat_completions_url(self) -> str:
        if self._base_url.endswith("/chat/completions"):
            return self._base_url
        return f"{self._base_url}/chat/completions"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _parse_decision(content: str) -> SufficiencyDecision:
    stripped = _strip_code_fence(content)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise InvalidJudgeResponseError("response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise InvalidJudgeResponseError("response JSON must be an object")
    return SufficiencyDecision.model_validate(payload)


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _judge_prompt(query: str, records: list[UnifiedMemoryRecord], threshold: float) -> str:
    schema = SufficiencyDecision.model_json_schema()
    evidence = [record.model_dump_memory() for record in records]
    return (
        "Decide whether the retrieved memory evidence is sufficient to answer the query. "
        f"Use a sufficiency threshold of {threshold:.2f}. "
        "Return JSON only, with exactly the fields approved by the schema; do not add markdown or commentary. "
        "Every evidence_record_id must be an ID from the supplied records. Each gap must ask one targeted question "
        "using a supplied sensor name plus start_time and end_time as ISO-8601 UTC instants that overlap that "
        "sensor's supplied record window. "
        "Use canonical input.sensors[].id names or legacy input.sensors[].info.name names. "
        "If sufficient is true, gaps must be empty.\n"
        f"SCHEMA:\n{json.dumps(schema, separators=(',', ':'))}\n"
        f"QUERY:\n{query}\n"
        f"RECORDS:\n{json.dumps(evidence, separators=(',', ':'))}"
    )


def _synthesis_prompt(
    query: str,
    memory_evidence: list[UnifiedMemoryRecord],
    vlm_evidence: list[VLMEvidence],
    unresolved_gaps: list[str],
) -> str:
    memory_payload = [record.model_dump_memory() for record in memory_evidence]
    vlm_payload = [item.model_dump(mode="json") for item in vlm_evidence]
    return (
        "Answer the query using only the supplied memory and VLM evidence. Clearly qualify uncertainty represented "
        "by unresolved gaps. Return plain text only.\n"
        f"QUERY:\n{query}\n"
        f"MEMORY_EVIDENCE:\n{json.dumps(memory_payload, separators=(',', ':'))}\n"
        f"VLM_EVIDENCE:\n{json.dumps(vlm_payload, separators=(',', ':'))}\n"
        f"UNRESOLVED_GAPS:\n{json.dumps(unresolved_gaps, separators=(',', ':'))}"
    )


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions") or normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


__all__ = ["InvalidJudgeResponseError", "OpenAIIntrospectionClient"]
