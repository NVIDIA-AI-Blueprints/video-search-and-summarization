# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Text-only OpenAI-compatible sufficiency judge and answer synthesizer."""

from __future__ import annotations

import json
import re
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
        criteria_prompt: str,
        backend_model: str | None = None,
        api_key: str | None = None,
        settings: IntrospectionSettings | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ConfigurationError("introspection base_url must be non-empty")
        if not model.strip():
            raise ConfigurationError("introspection model must be non-empty")
        if not criteria_prompt.strip():
            raise ConfigurationError("introspection criteria_prompt must be non-empty")
        if backend_model is not None and not backend_model.strip():
            raise ConfigurationError("introspection backend_model must be non-empty when configured")
        if client is not None and transport is not None:
            raise ConfigurationError("provide either an httpx client or transport, not both")

        self._base_url = _normalize_base_url(base_url)
        self._model = model.strip()
        self._criteria_prompt = criteria_prompt
        self._backend_model = backend_model.strip() if backend_model is not None else None
        self._api_key = api_key
        self._settings = settings or IntrospectionSettings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(self._settings.timeout_seconds)),
            transport=transport,
        )

    async def judge(self, *, query: str, records: list[UnifiedMemoryRecord]) -> SufficiencyDecision:
        """Judge memory sufficiency, retrying once only for invalid model output."""
        prompt = _judge_prompt(
            query,
            records,
            self._settings.sufficiency_threshold,
            self._criteria_prompt,
        )
        last_error: InvalidJudgeResponseError | None = None
        for _attempt in range(2):
            content = await self._chat(prompt)
            try:
                decision = _normalize_evidence_ids(_parse_decision(content), records)
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
        answer = (await self._chat(prompt)).strip()
        if not answer:
            raise ValueError("answer synthesizer returned empty content")
        return answer

    async def _chat(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._backend_model is not None:
            headers["x-openclaw-model"] = self._backend_model

        response = await self._client.post(self._chat_completions_url, headers=headers, json=payload)
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("OpenAI-compatible response contained no message content") from error
        if not isinstance(content, str):
            raise ValueError("OpenAI-compatible message content must be text")
        return _strip_reasoning(content)

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
    except json.JSONDecodeError:
        payload = _extract_json_object(stripped)
    if not isinstance(payload, dict):
        raise InvalidJudgeResponseError("response JSON must be an object")
    return SufficiencyDecision.model_validate(payload)


_REASONING_BLOCK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL)
_REASONING_CLOSE = re.compile(r"</(?:think|thinking|reasoning)>")


def _strip_reasoning(content: str) -> str:
    """Drop chain-of-thought that reasoning-capable models emit around their answer."""
    without_blocks = _REASONING_BLOCK.sub("", content)
    # Some models emit only the closing tag, leaving the answer after it.
    return _REASONING_CLOSE.split(without_blocks)[-1].strip()


def _extract_json_object(content: str) -> Any:
    """Recover the first embedded JSON object from a response wrapped in prose."""
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise InvalidJudgeResponseError("response is not valid JSON")


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _judge_prompt(
    query: str,
    records: list[UnifiedMemoryRecord],
    threshold: float,
    criteria_prompt: str,
) -> str:
    schema = SufficiencyDecision.model_json_schema()
    evidence = [_judge_record(record) for record in records]
    return (
        "FIXED VSS GROUNDING AND SAFETY RULES:\n"
        "Judge only the supplied memory records. Do not invent evidence. "
        "For every evidence_record_id, copy exactly one top-level evidence_record_id value from the supplied records. "
        "Never construct an ID or cite an Elasticsearch document ID, embedding reference, or compound #event# ID. "
        "Each gap must ask one targeted question using a sensor from the supplied records, and its start_time and "
        "end_time must be ISO-8601 UTC instants that overlap that sensor's supplied record window. "
        "Use canonical input.sensors[].id names or legacy input.sensors[].info.name names. "
        "When sufficient is true, gaps must be empty. "
        "Return JSON only, with exactly the fields required by the schema; do not add markdown or commentary.\n"
        f"CONFIGURED SUFFICIENCY CRITERIA:\n{criteria_prompt}\n"
        f"SUFFICIENCY THRESHOLD:\n{threshold:.2f}\n"
        f"REQUIRED RESPONSE SCHEMA:\n{json.dumps(schema, separators=(',', ':'))}\n"
        f"USER QUERY:\n{query}\n"
        f"RETRIEVED RECORDS:\n{json.dumps(evidence, separators=(',', ':'))}"
    )


def _judge_record(record: UnifiedMemoryRecord) -> dict[str, Any]:
    """Expose one unambiguous citeable ID and omit internal embedding identifiers."""
    payload = record.model_dump_memory()
    output = payload.get("output")
    if isinstance(output, dict):
        output.pop("embedding", None)
    return {"evidence_record_id": _record_id(record), **payload}


def _normalize_evidence_ids(
    decision: SufficiencyDecision,
    records: list[UnifiedMemoryRecord],
) -> SufficiencyDecision:
    """Map only known storage/embedding aliases back to public evidence IDs."""
    aliases: dict[str, set[str]] = {}
    for record in records:
        public_id = _record_id(record)
        candidates = {public_id, _storage_id(record)}
        if record.output is not None:
            for embedding in record.output.embedding or []:
                if embedding.es_ref:
                    candidates.add(embedding.es_ref)
                    candidates.add(embedding.es_ref.rsplit("/", 1)[-1])
        for candidate in candidates:
            aliases.setdefault(candidate, set()).add(public_id)

    normalized: list[str] = []
    for evidence_id in decision.evidence_record_ids:
        targets = aliases.get(evidence_id)
        public_id = next(iter(targets)) if targets is not None and len(targets) == 1 else evidence_id
        if public_id not in normalized:
            normalized.append(public_id)
    return decision.model_copy(update={"evidence_record_ids": normalized})


def _record_id(record: UnifiedMemoryRecord) -> str:
    return record.job.record_id or record.job.job_id


def _storage_id(record: UnifiedMemoryRecord) -> str:
    if record.job.record_id is None or record.job.record_type is None:
        return record.job.job_id
    return f"{record.job.job_id}#{record.job.record_type}#{record.job.record_id}"


def _synthesis_prompt(
    query: str,
    memory_evidence: list[UnifiedMemoryRecord],
    vlm_evidence: list[VLMEvidence],
    unresolved_gaps: list[str],
) -> str:
    memory_payload = [record.model_dump_memory() for record in memory_evidence]
    vlm_payload = [item.model_dump(mode="json") for item in vlm_evidence]
    return (
        "Answer the query using only facts explicitly stated in the supplied memory and VLM evidence. "
        "An unresolved gap is unknown: never turn missing evidence into a positive or negative claim. "
        "Clearly state what cannot be confirmed; if no evidence directly supports an answer, say it cannot be "
        "determined. Return plain text only.\n"
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
