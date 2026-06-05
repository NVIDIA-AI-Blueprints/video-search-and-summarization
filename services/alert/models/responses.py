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

import json
import logging
import re
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_serializer

logger = logging.getLogger(__name__)

# Verdict values for error cases when VLM response is not available
ErrorVerdictType = Literal["verification-failed"]

# Binary verdict values accepted by the default (verification) path. The
# Literal type enforces strict validation at model construction time —
# anything else raises ValidationError.
VerdictType = Literal["A", "B", "yes", "no", "YES", "NO", "Yes", "No"]


class ModelType(str, Enum):
    """VLM model types for response format selection."""
    COSMOS_REASON = "cosmos-reason"  # Unified: handles both <answer>-tagged and bare verdict
    OTHER = "other"                  # Other models: description only, no verdict
    CR1 = "cr1"   # legacy alias
    CR2 = "cr2"   # legacy alias


# Model name patterns for auto-detection
MODEL_PATTERNS = {
    ModelType.COSMOS_REASON: ("cosmos-reason1", "cosmos-reason2"),
}


def detect_model_type(model_name: str) -> ModelType:
    """Detect model type from model name.

    Args:
        model_name: Model name from config (e.g., "nvidia/cosmos-reason2-8b")

    Returns:
        ModelType enum value
    """
    if not model_name:
        return ModelType.OTHER

    model_lower = model_name.lower()

    for model_type, patterns in MODEL_PATTERNS.items():
        if any(pattern in model_lower for pattern in patterns):
            return model_type

    return ModelType.OTHER


# ---------------------------------------------------------------------------
# Custom parser registry
# ---------------------------------------------------------------------------

_BUILTIN_FORMATS = frozenset({"auto", "cosmos-reason", "cr1", "cr2", "json", "other"})
_custom_parsers: Dict[str, Callable] = {}


def register_parser(
    name: str,
    parser_fn: Callable[[str, Optional[Dict]], "VLMResponse"],
) -> None:
    """Register a custom VLM response parser.

    The registered *name* can then be used as the ``response_format`` value in
    ``config.yaml``.  The *parser_fn* must accept ``(text, json_config)`` and
    return a ``VLMResponse``.

    Args:
        name: Format name (e.g. ``"xml"``).  Must not collide with built-in names.
        parser_fn: ``(text: str, json_config: Optional[Dict]) -> VLMResponse``

    Raises:
        ValueError: If *name* collides with a built-in format.
    """
    if name in _BUILTIN_FORMATS:
        raise ValueError(
            f"Cannot override built-in format '{name}'. "
            f"Built-in formats: {sorted(_BUILTIN_FORMATS)}"
        )
    _custom_parsers[name] = parser_fn
    logger.info("Registered custom VLM response parser: '%s'", name)


def _resolve_dotpath(data: dict, path: str):
    """Resolve a dot-separated path against a nested dict.

    >>> _resolve_dotpath({"a": {"b": 1}}, "a.b")
    1
    >>> _resolve_dotpath({"x": 1}, "x")
    1
    >>> _resolve_dotpath({}, "missing") is None
    True
    """
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


# Compiled regex for Cosmos Reason response parsing
# Strategy 1: <think>REASONING</think><answer>VERDICT</answer>
# Verdict validation is enforced by the VerdictType Literal on VLMResponse.
_RE_ANSWER_TAGS = re.compile(
    r"<think>\s*(?P<reasoning>.*?)\s*</think>\s*"
    r"<answer>\s*(?P<answer>.*?)\s*</answer>",
    re.DOTALL,
)
# Strategy 2: <think>REASONING</think> VERDICT  (bare verdict, no <answer> tags)
_RE_BARE_VERDICT = re.compile(
    r"^<think>(?P<reasoning>[^<]*(?:<(?!/?think>)[^<]*)*)</think>"
    r"(?:\s*)(?P<answer_text>[\s\S]*)$",
    re.DOTALL,
)


class VLMResponse(BaseModel):
    # Default verification path produces ``info["reasoning"]`` (VLM chain-of-thought).
    # The pluggable parser path (set via ``vlm.response_parser``) bypasses
    # VLMResponse entirely and writes its output to ``info["vlm_response"]``
    # so the two contracts do not clash on the wire (Option B).
    reasoning: str
    verdict: Optional[VerdictType] = None  # Strict binary verdict; None for non-verification pluggable-parser output
    description: Optional[str] = None  # For "other" models - full response text
    extra: Optional[Dict[str, Any]] = None  # Pass-through for custom format parsers

    @classmethod
    def model_validate_text(
        cls,
        text: str,
        model_name: str = "",
        response_format: str = "auto",
        json_config: Optional[Dict] = None,
    ) -> "VLMResponse":
        """Parse VLM response based on *response_format* (or auto-detected model type).

        Supported formats:
        - ``"auto"``          -- detect from *model_name*.  Default.
        - ``"cosmos-reason"`` -- unified Cosmos Reason (``<answer>`` tags or bare verdict).
          ``"cr1"`` and ``"cr2"`` are accepted as aliases.
        - ``"json"``          -- structured JSON output (configurable via *json_config*).
        - ``"other"``         -- free-form text, no verdict extraction.
        - Any other value is looked up in the custom parser registry
          (see :func:`register_parser`).

        Args:
            text: Raw VLM response text.
            model_name: Model name from config (used when *response_format* is ``"auto"``).
            response_format: Format identifier.
            json_config: Optional dict for JSON parser (``verdict_field``,
                ``verdict_mapping``, ``reasoning_fields``).  Note: the
                ``reasoning_fields`` config key describes which *input*
                JSON keys in the VLM response to read free-form text from;
                the extracted text is stored in ``VLMResponse.reasoning``.

        Returns:
            VLMResponse with appropriate fields populated.

        Raises:
            ValueError: If the text cannot be parsed or *response_format* is unknown.
        """
        if response_format in _custom_parsers:
            logger.debug("VLM response parsing: custom parser '%s'", response_format)
            return _custom_parsers[response_format](text, json_config)

        if response_format == "json":
            logger.debug("VLM response parsing: response_format='json'")
            return cls._parse_json_response(text, json_config)

        if response_format in ("cosmos-reason", "cr1", "cr2"):
            logger.debug("VLM response parsing: response_format='%s' → cosmos-reason", response_format)
            return cls._parse_cosmos_reason_response(text)

        if response_format == "other":
            logger.debug("VLM response parsing: response_format='other' (explicit)")
            return cls._parse_other_response(text)

        if response_format == "auto":
            model_type = detect_model_type(model_name)
            logger.debug(
                "VLM response parsing: model_name='%s' → model_type=%s",
                model_name,
                model_type.value,
            )
            try:
                if model_type in (ModelType.COSMOS_REASON, ModelType.CR1, ModelType.CR2):
                    return cls._parse_cosmos_reason_response(text)
                return cls._parse_other_response(text)
            except ValueError:
                raise
            except ValidationError as e:
                raise ValueError(
                    f"VLM response validation failed for model_type={model_type.value}: "
                    f"{e}. Raw response: '{text}'"
                ) from e

        raise ValueError(
            f"Unknown response_format '{response_format}'. "
            f"Built-in formats: {sorted(_BUILTIN_FORMATS)}. "
            f"Registered custom parsers: {sorted(_custom_parsers) or '(none)'}."
        )

    # ------------------------------------------------------------------
    # Cosmos Reason parser (unified CR1 + CR2)
    # ------------------------------------------------------------------

    @classmethod
    def _parse_cosmos_reason_response(cls, text: str) -> "VLMResponse":
        """Parse Cosmos Reason VLM output (unified CR1 + CR2).

        Tries three strategies in order:
        1. ``<think>...</think><answer>VERDICT</answer>``  (answer-tagged)
        2. ``<think>...</think>VERDICT``                    (bare verdict after think)
        3. Bare ``VERDICT`` without any tags               (B1 fallback)

        Raises:
            ValueError: If no strategy can extract a valid verdict.
        """
        stripped = text.strip()

        # Strategy 1: answer-tagged  <think>...<answer>VERDICT</answer>
        match = _RE_ANSWER_TAGS.search(stripped)
        if match:
            return cls._normalize_verdict(match.group("reasoning"), match.group("answer"), text)

        # Strategy 2: bare verdict after </think>
        match = _RE_BARE_VERDICT.match(stripped)
        if match:
            think_text = match.group("reasoning").strip()
            answer_text = match.group("answer_text").strip()
            if not answer_text:
                raise ValueError(
                    f"Cosmos Reason response has empty verdict after </think> tag. "
                    f"Raw response: '{text}'"
                )
            return cls._normalize_verdict(think_text, answer_text, text)

        # Strategy 3: bare verdict (no <think> tags at all)
        data = {"reasoning": "", "verdict": stripped}
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            raise ValueError(
                f"Cosmos Reason verdict validation failed. "
                f"Extracted verdict='{stripped}', raw response: '{text}'"
            ) from e

    @classmethod
    def _normalize_verdict(cls, reasoning_text: str, raw_answer: str, raw_text: str) -> "VLMResponse":
        """Normalize extracted reasoning (<think> content) + verdict into a VLMResponse."""
        reasoning_text = reasoning_text.strip()
        normalized = raw_answer.strip().upper()
        if not normalized:
            raise ValueError(
                f"Cosmos Reason response has empty verdict. "
                f"Raw response: '{raw_text}'"
            )

        try:
            return cls.model_validate({"reasoning": reasoning_text, "verdict": normalized})
        except ValidationError as e:
            raise ValueError(
                f"Cosmos Reason verdict validation failed. "
                f"Extracted verdict='{raw_answer.strip()}', raw response: '{raw_text}'"
            ) from e

    @classmethod
    def _parse_other_response(cls, text: str) -> "VLMResponse":
        """
        Parse "other" model response: Free-form description only, NO verdict.

        For non-Cosmos models, we capture the entire response as description.
        No verdict is extracted - downstream processing must handle this.
        """
        text = text.strip()

        if not text:
            raise ValueError("Empty response from VLM")

        return cls.model_validate({
            "reasoning": "",
            "verdict": None,
            "description": text
        })

    @classmethod
    def _parse_json_response(
        cls,
        text: str,
        json_config: Optional[Dict] = None,
    ) -> "VLMResponse":
        """Parse JSON-formatted VLM response with configurable field mapping.

        Handles markdown code fences (```json ... ```) that CR2 wraps around
        JSON output.  Field names are configurable via *json_config* so
        deployments can match their prompt's requested schema.

        Args:
            text: Raw VLM response text (may include code fences).
            json_config: Optional dict with keys ``verdict_field``,
                ``verdict_mapping``, and ``reasoning_fields`` (the list of
                candidate JSON keys in the VLM output to read free-form text
                from; the value becomes ``VLMResponse.reasoning``).

        Returns:
            VLMResponse with reasoning and verdict populated.

        Raises:
            ValueError: If text is not valid JSON or required fields
                are missing/empty.
        """
        json_config = json_config or {}
        verdict_field: str = json_config.get("verdict_field", "prediction_answer")
        verdict_mapping: Optional[Dict] = json_config.get("verdict_mapping")
        reasoning_fields: List[str] = json_config.get(
            "reasoning_fields", ["reasoning", "thinking", "explanation", "video_description"]
        )

        text = text.strip()

        # Strip markdown code fences (```json ... ```)
        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) >= 2:
                if lines[-1].strip() == "```":
                    lines = lines[1:-1]
                else:
                    lines = lines[1:]
                text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"VLM response is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        # Resolve verdict — supports dot-notation for nested fields
        verdict_raw = _resolve_dotpath(data, verdict_field)
        if verdict_raw is None:
            raise ValueError(
                f"JSON response missing required field '{verdict_field}'"
            )

        # Apply explicit value mapping if configured (e.g. true->"YES")
        if verdict_mapping:
            str_key = str(verdict_raw).lower()
            mapped = verdict_mapping.get(str_key)
            if mapped is None:
                mapped = verdict_mapping.get(verdict_raw)
            if mapped is not None:
                verdict_raw = mapped

        verdict = str(verdict_raw).strip()
        if not verdict:
            raise ValueError(f"JSON field '{verdict_field}' is empty")
        # Normalize case for common verdict values
        upper = verdict.upper()
        if verdict.lower() in {"yes", "no"}:
            verdict = upper
        elif upper in {"A", "B"}:
            verdict = upper
        else:
            verdict = upper

        # Resolve free-form reasoning text from the first matching reasoning_fields key
        reasoning_text = ""
        for field in reasoning_fields:
            val = _resolve_dotpath(data, field)
            if val:
                reasoning_text = str(val).strip()
                break

        return cls.model_validate({
            "reasoning": reasoning_text,
            "verdict": verdict,
        })

    @field_serializer("verdict")
    def serialize_verdict(self, verdict: Optional[str], _info):
        if verdict is None:
            return None
        mapping = {
            "yes": "confirmed",
            "no": "rejected",
            "YES": "confirmed",
            "NO": "rejected",
            "Yes": "confirmed",
            "No": "rejected",
            "A": "confirmed",
            "B": "rejected",
            "a": "confirmed",
            "b": "rejected",
        }
        return mapping.get(verdict, verdict)


class EnrichmentResponse(BaseModel):
    """Response from enrichment VLM call."""
    reasoning: Optional[str] = None  # Free-form enrichment text from VLM
    response_code: int = Field(serialization_alias="responseCode")  # HTTP-style status code
    response_status: str = Field(serialization_alias="responseStatus")  # Human-readable status

    def model_dump(self) -> Dict[str, Any]:
        """Return dict for merging into message['info']['enrichment']."""
        data = super().model_dump(by_alias=True)
        return data


class AlertBridgeResponse(BaseModel):
    vlm_response: Optional[VLMResponse] = None
    video_source: Optional[str] = Field(default=None, serialization_alias="videoSource")
    verification_response_code: Optional[int] = Field(default=None, serialization_alias="verificationResponseCode")
    verification_response_status: Optional[str] = Field(default=None, serialization_alias="verificationResponseStatus")
    verdict: Optional[ErrorVerdictType] = None  # Explicit verdict for error cases
    # Structured error classification so downstream consumers do not have to
    # substring-match ``verificationResponseStatus`` to tell pluggable-parser
    # crashes apart from VLM schema failures or VLM API failures
    # Buckets are defined
    # in :mod:`models.pluggable_parser_runtime` (``ERROR_SOURCE_*``). Left
    # ``None`` on success paths and filtered out of :meth:`model_dump_flat`
    # so successful VLM responses do not emit an empty ``info["errorSource"]``
    # on the wire.
    error_source: Optional[str] = Field(default=None, serialization_alias="errorSource")

    def model_dump_flat(self) -> Dict[str, Any]:
        data = self.model_dump(by_alias=True)
        # ``vlm_response_fields`` here is the *nested* VLMResponse object dict
        # (keys: reasoning, verdict, description, extra). We pop it from the
        # outer envelope, then splat its keys to the top level so consumers get
        # flat ``info["reasoning"]``/``info["verdict"]``/... keys on the
        # default verification path. The pluggable-parser path bypasses
        # VLMResponse entirely and writes ``info["vlm_response"]`` directly
        # via the orchestrator helpers (Option B — the two
        # schemas are disjoint so they never appear in the same output).
        vlm_response_fields = data.pop("vlm_response", {}) or {}
        standalone_verdict = data.pop("verdict", None)
        extra = vlm_response_fields.pop("extra", None)

        # VLM response verdict takes precedence; fallback to standalone verdict
        if "verdict" not in vlm_response_fields and standalone_verdict:
            vlm_response_fields["verdict"] = standalone_verdict

        # Drop unset ``errorSource`` so the success paths do not emit
        # ``info["errorSource"] = ""`` on every VLM response (the
        # map<string,string> coercion in ``merge_info_with_response`` would
        # otherwise turn ``None`` into the empty string and leak the field
        # onto the wire unconditionally).
        if data.get("errorSource") is None:
            data.pop("errorSource", None)

        merged = {**data, **vlm_response_fields}
        if extra:
            for k, v in extra.items():
                if k not in merged:
                    merged[k] = v
        return merged


def merge_info_with_response(
    message: Dict[str, Any],
    response: AlertBridgeResponse,
    latency: Optional[Dict[str, Any]] = None,
    include_latency: bool = False,
) -> None:
    """Merge AlertBridgeResponse into message['info'], preserving original fields.

    This ensures critical fields like 'primaryObjectId' from the original event
    are preserved when adding VLM response metadata, which is required for
    fingerprint matching between mdx-incidents and mdx-vlm-incidents.

    Schema contract (Option B — conditional schema):
        - **Default verification path** (no ``vlm.response_parser`` set): the
          VLM free-form text lands in ``info["reasoning"]`` via
          :class:`VLMResponse`. Wire schema is unchanged.
        - **Pluggable-parser path** (``vlm.response_parser`` set): the
          orchestrator bypasses :class:`VLMResponse` and writes the parser
          JSON into ``info["vlm_response"]`` directly. ``merge_info_with_response``
          is still used, but the caller passes ``AlertBridgeResponse(vlm_response=None, ...)``
          and attaches ``info["vlm_response"]`` after the merge.

    The two schemas are disjoint — deployments that do not opt into the
    pluggable parser see zero wire change.

    Args:
        message: The event message dict to update in place.
        response: The AlertBridgeResponse to merge into info.
        latency: Optional latency metrics dict.
        include_latency: Whether to include latency in the merged info.
    """
    original_info = message.get("info") or {}
    if not isinstance(original_info, dict):
        original_info = {}

    merged = {**original_info, **response.model_dump_flat()}

    if include_latency and latency:
        merged["latency"] = json.dumps(latency, separators=(',', ':'))

    # Ensure all values conform to map<string, string>.
    # Kafka path already stringifies via _stringify_map_values in schema_util;
    # do it here so the ES path matches.
    for key, value in list(merged.items()):
        if isinstance(value, dict) or isinstance(value, list):
            merged[key] = json.dumps(value, separators=(',', ':'))
        elif value is None:
            merged[key] = ""
        elif not isinstance(value, str):
            merged[key] = str(value)

    message["info"] = merged
