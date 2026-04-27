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

"""Question answering over stored LVS dense-caption/event documents."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch import NotFoundError as ESNotFoundError
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

logger = logging.getLogger(__name__)

PTS_NS_PER_SECOND = 1_000_000_000
RAW_EVENTS_DOC_TYPE = "raw_events"
STRUCTURED_EVENTS_DOC_TYPE = "structured_events"
AGGREGATED_SUMMARY_DOC_TYPE = "aggregated_summary"

DEFAULT_SOURCE_FIELDS = [
    "metadata.content_metadata.uuid",
    "metadata.content_metadata.streamId",
    "metadata.content_metadata.camera_id",
    "metadata.content_metadata.file",
    "metadata.source",
]
DEFAULT_TEXT_FIELDS = [
    "text^4",
    "metadata.content_metadata.camera_id^2",
    "metadata.content_metadata.file",
    "metadata.source",
]

ANSWER_SYSTEM_PROMPT = """You answer questions about video using stored LVS caption and event evidence.
Use only the evidence provided in the prompt. If the evidence is insufficient, say that directly.
When useful, cite the relevant timestamp ranges from the evidence. Do not invent visual details."""

ANSWER_USER_PROMPT = """Video or stream: {sensor_id}

User question:
{question}

Stored LVS caption/event evidence:
{evidence}

Answer the user question in plain English."""


class LVSCaptionQAConfig(FunctionBaseConfig, name="lvs_caption_qa"):
    """Configuration for Q&A over stored LVS dense-caption/event documents."""

    llm_name: LLMRef = Field(
        ...,
        description="The LLM used to answer from retrieved stored LVS captions.",
    )
    es_endpoint: str = Field(
        ...,
        description="Elasticsearch endpoint where LVS documents are stored.",
    )
    es_index: str = Field(
        default="lvs-events",
        description="Elasticsearch index containing LVS dense-caption/event documents.",
    )
    video_understanding_tool: FunctionRef = Field(
        default="video_understanding",
        description="Fallback VLM tool used when stored LVS captions are unavailable.",
    )
    max_results: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Maximum ES documents to retrieve for caption evidence.",
    )
    max_evidence_chars: int = Field(
        default=12000,
        ge=1000,
        description="Maximum characters of caption evidence injected into the LLM prompt.",
    )
    default_timestamp_window_seconds: float = Field(
        default=30.0,
        ge=1.0,
        description="Fallback VLM clip duration when the user mentions a single timestamp.",
    )
    fallback_to_vlm: bool = Field(
        default=True,
        description="Whether to call the configured VLM tool when no usable stored captions are found.",
    )
    source_fields: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SOURCE_FIELDS),
        description="ES fields used to match the requested video or stream.",
    )
    text_fields: list[str] = Field(
        default_factory=lambda: list(DEFAULT_TEXT_FIELDS),
        description="ES text fields used for BM25 keyword search.",
    )

    model_config = ConfigDict(extra="forbid")


class LVSCaptionQAInput(BaseModel):
    """Input for Q&A over stored LVS captions."""

    sensor_id: str = Field(
        ...,
        min_length=1,
        description="The video file name, camera ID, or stream ID to answer about.",
    )
    question: str = Field(
        ...,
        min_length=1,
        description="The user question to answer from stored LVS captions.",
    )
    start_timestamp: float | None = Field(
        default=None,
        description="Optional start time in seconds since the beginning of the video/stream.",
    )
    end_timestamp: float | None = Field(
        default=None,
        description="Optional end time in seconds since the beginning of the video/stream.",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Optional maximum number of ES documents to retrieve.",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def validate_time_range(cls, info: dict) -> dict:
        start = info.get("start_timestamp")
        end = info.get("end_timestamp")

        if start is not None:
            start = float(start)
            if start < 0:
                raise ValueError("start_timestamp must be non-negative")
            info["start_timestamp"] = start

        if end is not None:
            end = float(end)
            if end < 0:
                raise ValueError("end_timestamp must be non-negative")
            info["end_timestamp"] = end

        if start is not None and end is not None and start >= end:
            raise ValueError("start_timestamp must be before end_timestamp")

        return info


@dataclass(frozen=True)
class CaptionEvidence:
    """Normalized evidence extracted from an LVS ES document."""

    text: str
    doc_type: str
    score: float
    start_seconds: float | None = None
    end_seconds: float | None = None
    event_type: str | None = None


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds_to_pts_ns(value: float) -> int:
    return round(value * PTS_NS_PER_SECOND)


def _pts_ns_to_seconds(value: Any) -> float | None:
    coerced = _coerce_float(value)
    if coerced is None:
        return None
    return coerced / PTS_NS_PER_SECOND


def _format_seconds(value: float) -> str:
    value_float = float(value)
    if value_float.is_integer():
        return f"{int(value_float)}s"
    return f"{value_float:.2f}".rstrip("0").rstrip(".") + "s"


def _parse_time_value(value: str, unit: str | None = None) -> float | None:
    value = value.strip()
    if not value:
        return None

    try:
        if ":" in value:
            parts = [float(part) for part in value.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            return None

        seconds = float(value)
    except ValueError:
        return None

    normalized_unit = (unit or "").lower()
    if normalized_unit in {"ms", "millisecond", "milliseconds"}:
        return seconds / 1000.0
    if normalized_unit in {"m", "min", "mins", "minute", "minutes"}:
        return seconds * 60.0
    return seconds


_TIME_VALUE_PATTERN = r"\d+(?::\d{1,2}){1,2}(?:\.\d+)?|\d+(?:\.\d+)?"
_TIME_UNIT_PATTERN = r"ms|milliseconds?|s|sec|secs|seconds?|m|min|mins|minutes?"
_TIME_TOKEN = rf"({_TIME_VALUE_PATTERN})\s*({_TIME_UNIT_PATTERN})?"
_TIME_TOKEN_NAMED = rf"(?P<value>{_TIME_VALUE_PATTERN})" rf"\s*(?P<unit>{_TIME_UNIT_PATTERN})?"


def _infer_time_scope_from_question(question: str) -> tuple[float | None, float | None]:
    """Infer a simple seconds-based time scope from natural language text."""
    text = question.lower()

    bracket_match = re.search(rf"\[\s*{_TIME_TOKEN}\s*,\s*{_TIME_TOKEN}\s*\]", text)
    if bracket_match:
        values = re.findall(_TIME_TOKEN, bracket_match.group(0))
        if len(values) >= 2:
            start = _parse_time_value(values[0][0], values[0][1])
            end = _parse_time_value(values[1][0], values[1][1])
            if start is not None and end is not None and start < end:
                return start, end

    range_match = re.search(rf"(?:between|from)\s+{_TIME_TOKEN}\s+(?:and|to|-)\s+{_TIME_TOKEN}", text)
    if range_match:
        values = re.findall(_TIME_TOKEN, range_match.group(0))
        if len(values) >= 2:
            start = _parse_time_value(values[0][0], values[0][1])
            end = _parse_time_value(values[1][0], values[1][1])
            if start is not None and end is not None and start < end:
                return start, end

    after_match = re.search(rf"(?:after|since)\s+{_TIME_TOKEN_NAMED}", text)
    if after_match:
        value, unit = after_match.group("value"), after_match.group("unit")
        return _parse_time_value(value, unit), None

    before_match = re.search(rf"(?:before|until|up to)\s+{_TIME_TOKEN_NAMED}", text)
    if before_match:
        value, unit = before_match.group("value"), before_match.group("unit")
        return None, _parse_time_value(value, unit)

    point_match = re.search(rf"(?:at|around|near)\s+{_TIME_TOKEN_NAMED}", text)
    if point_match:
        value, unit = point_match.group("value"), point_match.group("unit")
        return _parse_time_value(value, unit), None

    return None, None


def _resolve_time_scope(input_data: LVSCaptionQAInput) -> tuple[float | None, float | None]:
    if input_data.start_timestamp is not None or input_data.end_timestamp is not None:
        return input_data.start_timestamp, input_data.end_timestamp
    return _infer_time_scope_from_question(input_data.question)


def _has_time_scope(start_seconds: float | None, end_seconds: float | None) -> bool:
    return start_seconds is not None or end_seconds is not None


def _get_time_overlap_pts_filter(start_seconds: float | None, end_seconds: float | None) -> list[dict[str, Any]]:
    if not _has_time_scope(start_seconds, end_seconds):
        return []

    query_start = 0.0 if start_seconds is None else start_seconds
    query_end = end_seconds if end_seconds is not None else query_start

    return [
        {"range": {"metadata.content_metadata.start_pts": {"lte": _seconds_to_pts_ns(query_end)}}},
        {"range": {"metadata.content_metadata.end_pts": {"gte": _seconds_to_pts_ns(query_start)}}},
    ]


def _term_or_keyword_clauses(field: str, value: str) -> list[dict[str, Any]]:
    fields = [field] if field.endswith(".keyword") else [field, f"{field}.keyword"]
    clauses: list[dict[str, Any]] = []
    for field_name in fields:
        clauses.append({"term": {field_name: value}})
    return clauses


def _doc_type_filter(doc_types: list[str]) -> dict[str, Any]:
    should: list[dict[str, Any]] = []
    for doc_type in doc_types:
        should.extend(_term_or_keyword_clauses("metadata.content_metadata.doc_type", doc_type))
    return {"bool": {"should": should, "minimum_should_match": 1}}


def _source_filter(sensor_id: str, source_fields: list[str]) -> dict[str, Any]:
    should: list[dict[str, Any]] = []
    for field in source_fields:
        should.extend(_term_or_keyword_clauses(field, sensor_id))
        should.append({"match_phrase": {field: sensor_id}})
        if not field.endswith(".keyword"):
            should.append({"wildcard": {f"{field}.keyword": {"value": f"*{sensor_id}*", "case_insensitive": True}}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


def _build_lvs_caption_query(
    *,
    question: str,
    sensor_id: str,
    doc_types: list[str],
    source_fields: list[str] | None = None,
    text_fields: list[str] | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    include_time_filter: bool = True,
    use_keyword_query: bool = True,
    size: int = 8,
) -> dict[str, Any]:
    """Build the Elasticsearch BM25 query for stored LVS documents."""
    filter_clauses: list[dict[str, Any]] = [
        _doc_type_filter(doc_types),
        _source_filter(sensor_id, source_fields or DEFAULT_SOURCE_FIELDS),
    ]
    if include_time_filter:
        filter_clauses.extend(_get_time_overlap_pts_filter(start_seconds, end_seconds))

    if use_keyword_query:
        must_clauses: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": question,
                    "fields": text_fields or DEFAULT_TEXT_FIELDS,
                    "type": "best_fields",
                    "operator": "or",
                }
            }
        ]
    else:
        must_clauses = [{"match_all": {}}]

    return {
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "size": size,
        "_source": ["text", "metadata"],
    }


def _content_metadata(source: dict[str, Any]) -> dict[str, Any]:
    metadata = source.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    content_metadata = metadata.get("content_metadata")
    return content_metadata if isinstance(content_metadata, dict) else {}


def _parse_events_payload(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict):
        return []

    events = payload.get("events", [])
    return [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []


def _normalize_event_seconds(
    doc_type: str,
    metadata: dict[str, Any],
    event_start: float | None,
    event_end: float | None,
) -> tuple[float | None, float | None]:
    metadata_start = _pts_ns_to_seconds(metadata.get("start_pts"))
    metadata_end = _pts_ns_to_seconds(metadata.get("end_pts"))

    if event_start is None and event_end is None:
        return metadata_start, metadata_end

    start = event_start
    end = event_end if event_end is not None else event_start

    # Raw chunk event times may be relative to the chunk. If the event starts before the
    # chunk start, treat it as relative and shift it into full-video seconds.
    if (
        doc_type == RAW_EVENTS_DOC_TYPE
        and metadata_start is not None
        and start is not None
        and start < max(metadata_start - 1e-6, 0.0)
    ):
        start += metadata_start
        if end is not None:
            end += metadata_start

    return start, end


def _overlaps_time_scope(
    evidence: CaptionEvidence,
    start_seconds: float | None,
    end_seconds: float | None,
) -> bool:
    if not _has_time_scope(start_seconds, end_seconds):
        return True
    if evidence.start_seconds is None and evidence.end_seconds is None:
        return False

    query_start = 0.0 if start_seconds is None else start_seconds
    query_end = end_seconds if end_seconds is not None else query_start
    evidence_start = evidence.start_seconds if evidence.start_seconds is not None else evidence.end_seconds
    evidence_end = evidence.end_seconds if evidence.end_seconds is not None else evidence_start

    if evidence_start is None or evidence_end is None:
        return False
    return evidence_start <= query_end and evidence_end >= query_start


def _extract_evidence_from_hit(hit: dict[str, Any]) -> list[CaptionEvidence]:
    source = hit.get("_source", {})
    if not isinstance(source, dict):
        return []

    text = str(source.get("text") or "").strip()
    metadata = _content_metadata(source)
    doc_type = str(metadata.get("doc_type") or "")
    score = float(hit.get("_score") or 0.0)

    if not text:
        return []

    if doc_type == AGGREGATED_SUMMARY_DOC_TYPE:
        return [CaptionEvidence(text=text, doc_type=doc_type, score=score)]

    events = _parse_events_payload(text)
    if not events:
        start, end = _normalize_event_seconds(doc_type, metadata, None, None)
        return [CaptionEvidence(text=text, doc_type=doc_type, score=score, start_seconds=start, end_seconds=end)]

    evidence: list[CaptionEvidence] = []
    for event in events:
        description = str(event.get("description") or event.get("text") or "").strip()
        if not description:
            description = json.dumps(event, ensure_ascii=False)

        event_type = str(event.get("type") or "").strip() or None
        event_start = _coerce_float(event.get("start_time"))
        event_end = _coerce_float(event.get("end_time"))
        start, end = _normalize_event_seconds(doc_type, metadata, event_start, event_end)

        evidence.append(
            CaptionEvidence(
                text=description,
                doc_type=doc_type,
                score=score,
                start_seconds=start,
                end_seconds=end,
                event_type=event_type,
            )
        )

    return evidence


def _format_evidence(
    hits: list[dict[str, Any]],
    *,
    start_seconds: float | None,
    end_seconds: float | None,
    max_chars: int,
) -> str:
    extracted: list[CaptionEvidence] = []
    seen: set[tuple[str, float | None, float | None, str | None]] = set()

    for hit in hits:
        for item in _extract_evidence_from_hit(hit):
            if not _overlaps_time_scope(item, start_seconds, end_seconds):
                continue
            key = (item.text, item.start_seconds, item.end_seconds, item.event_type)
            if key in seen:
                continue
            seen.add(key)
            extracted.append(item)

    extracted.sort(
        key=lambda item: (
            item.start_seconds is None,
            item.start_seconds if item.start_seconds is not None else 0.0,
            -item.score,
        )
    )

    lines: list[str] = []
    total_chars = 0
    for item in extracted:
        if item.start_seconds is not None and item.end_seconds is not None:
            prefix = f"[{_format_seconds(item.start_seconds)}-{_format_seconds(item.end_seconds)}]"
        elif item.start_seconds is not None:
            prefix = f"[{_format_seconds(item.start_seconds)}]"
        else:
            prefix = "[summary]"

        type_prefix = f" {item.event_type}:" if item.event_type else ""
        line = f"{prefix}{type_prefix} {item.text}".strip()
        if total_chars + len(line) > max_chars:
            break
        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


def _fallback_window(
    start_seconds: float | None,
    end_seconds: float | None,
    default_window_seconds: float,
) -> tuple[float | None, float | None]:
    if start_seconds is not None and end_seconds is not None:
        return start_seconds, end_seconds
    if start_seconds is not None:
        return start_seconds, start_seconds + default_window_seconds
    if end_seconds is not None:
        return 0.0, end_seconds
    return None, None


@register_function(config_type=LVSCaptionQAConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def lvs_caption_qa(config: LVSCaptionQAConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Answer video questions from stored LVS dense captions, falling back to VLM when needed."""

    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    async def _search_hits(
        es_client: AsyncElasticsearch,
        input_data: LVSCaptionQAInput,
        start_seconds: float | None,
        end_seconds: float | None,
    ) -> list[dict[str, Any]]:
        size = input_data.top_k or config.max_results
        has_time_scope = _has_time_scope(start_seconds, end_seconds)
        attempts: list[tuple[list[str], bool, bool]]
        if has_time_scope:
            attempts = [
                ([RAW_EVENTS_DOC_TYPE], True, True),
                ([RAW_EVENTS_DOC_TYPE], True, False),
                ([STRUCTURED_EVENTS_DOC_TYPE], False, True),
            ]
        else:
            attempts = [
                ([STRUCTURED_EVENTS_DOC_TYPE, AGGREGATED_SUMMARY_DOC_TYPE], False, True),
                ([STRUCTURED_EVENTS_DOC_TYPE, AGGREGATED_SUMMARY_DOC_TYPE], False, False),
                ([RAW_EVENTS_DOC_TYPE], False, True),
            ]

        for doc_types, include_time_filter, use_keyword_query in attempts:
            query = _build_lvs_caption_query(
                question=input_data.question,
                sensor_id=input_data.sensor_id,
                doc_types=doc_types,
                source_fields=config.source_fields,
                text_fields=config.text_fields,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                include_time_filter=include_time_filter,
                use_keyword_query=use_keyword_query,
                size=size,
            )
            logger.debug("LVS caption QA ES query: %s", query)
            response = await es_client.search(index=config.es_index, body=query)
            hits = response.get("hits", {}).get("hits", [])
            if hits:
                logger.info(
                    "Found %d LVS caption docs for doc_types=%s (keyword_query=%s)",
                    len(hits),
                    doc_types,
                    use_keyword_query,
                )
                return list(hits)

        return []

    async def _fallback_to_vlm(
        input_data: LVSCaptionQAInput,
        start_seconds: float | None,
        end_seconds: float | None,
    ) -> str:
        if not config.fallback_to_vlm:
            return "No stored LVS captions were found for this video and question."

        video_understanding_tool = await builder.get_tool(
            config.video_understanding_tool,
            wrapper_type=LLMFrameworkEnum.LANGCHAIN,
        )
        fallback_start, fallback_end = _fallback_window(
            start_seconds,
            end_seconds,
            config.default_timestamp_window_seconds,
        )
        payload = {
            "sensor_id": input_data.sensor_id,
            "start_timestamp": fallback_start,
            "end_timestamp": fallback_end,
            "user_prompt": input_data.question,
        }
        logger.info("Falling back to VLM video understanding with payload: %s", payload)
        result = await video_understanding_tool.ainvoke(input=payload)
        return str(result)

    async def _lvs_caption_qa(input_data: LVSCaptionQAInput) -> str:
        """
        Answer a question about a video using stored LVS dense captions/events.

        The primary path searches Elasticsearch for LVS documents using BM25 over
        stored event text, optionally scoped by a time range. If no usable stored
        captions are found, the tool falls back to direct VLM video understanding.
        """
        start_seconds, end_seconds = _resolve_time_scope(input_data)

        es_client = AsyncElasticsearch(config.es_endpoint)
        try:
            if not await es_client.indices.exists(index=config.es_index):
                logger.info("LVS caption ES index '%s' does not exist", config.es_index)
                return await _fallback_to_vlm(input_data, start_seconds, end_seconds)

            hits = await _search_hits(es_client, input_data, start_seconds, end_seconds)
            evidence = _format_evidence(
                hits,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                max_chars=config.max_evidence_chars,
            )

            if not evidence:
                logger.info("No usable stored LVS caption evidence found")
                return await _fallback_to_vlm(input_data, start_seconds, end_seconds)

            messages = [
                SystemMessage(content=ANSWER_SYSTEM_PROMPT),
                HumanMessage(
                    content=ANSWER_USER_PROMPT.format(
                        sensor_id=input_data.sensor_id,
                        question=input_data.question,
                        evidence=evidence,
                    )
                ),
            ]
            response = await llm.ainvoke(messages)
            answer = str(getattr(response, "content", response)).strip()
            if not answer:
                return "Stored LVS captions were found, but no answer could be generated from them."
            return answer
        except ESNotFoundError:
            logger.info("LVS caption ES index '%s' was not found", config.es_index)
            return await _fallback_to_vlm(input_data, start_seconds, end_seconds)
        except Exception as e:
            logger.warning("Stored LVS caption QA failed; falling back to VLM: %s", e, exc_info=True)
            return await _fallback_to_vlm(input_data, start_seconds, end_seconds)
        finally:
            await es_client.close()

    yield FunctionInfo.create(
        single_fn=_lvs_caption_qa,
        description=_lvs_caption_qa.__doc__,
        input_schema=LVSCaptionQAInput,
        single_output_schema=str,
    )
