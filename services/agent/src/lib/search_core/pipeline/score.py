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
"""Scorers (enrichers): stages that add named scores to existing candidates.

A scorer may perform *feature* IO — fetching evidence about hits already in
the ``Ranks`` — but never adds candidates and never reorders. It appends its
named score (and may enrich hit metadata such as ``screenshot_url`` and
``object_ids``); ordering remains whatever the chain last established.

``attribute_scores`` ports the retired ``fusion_search_rerank`` enrichment:
per-hit attribute lookups are best-effort (an unexpected failure or an
unparseable clip timestamp degrades that single hit to a zero attribute
score), while systemic :class:`LibraryError` failures propagate and abort the
chain so callers get a precise error instead of silently-degraded results.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import logging
from typing import TYPE_CHECKING
from typing import Any

from lib._foundation.errors import LibraryError
from lib._foundation.sanitize import scrub_log
from lib._foundation.time import datetime_to_iso8601
from lib._foundation.time import safe_iso8601_to_datetime

from .._internal.coerce import _coerce_float
from .._internal.coerce import _coerce_str
from ..models.attribute_search import AttributeSearchInput
from ..models.attribute_search import AttributeSearchResult
from .rank import ATTRIBUTE

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models.common import SourceType
    from .ranks import Hit
    from .ranks import Ranks
    from .ranks import Stage

logger = logging.getLogger(__name__)

# Zero-duration clips are extended symmetrically to give the behavior overlap
# filter a real window to intersect with.
_ZERO_DURATION_PAD_SECONDS = 2.5


def _result_score(result: AttributeSearchResult) -> float:
    """Frame score when positive, else behavior score — both coerced."""
    frame_score = result.metadata.frame_score
    if frame_score is not None:
        frame = _coerce_float(frame_score)
        if frame > 0.0:
            return frame
    return _coerce_float(result.metadata.behavior_score)


def _validated(attribute_results: Any) -> list[AttributeSearchResult]:
    """Coerce a per-hit attribute payload into validated result items.

    A non-list / empty payload yields an empty list; a single unprocessable
    item is skipped (with a WARNING) rather than sinking the whole hit's data.
    """
    if not attribute_results or not isinstance(attribute_results, list):
        return []
    validated: list[AttributeSearchResult] = []
    for item in attribute_results:
        try:
            validated.append(
                item if isinstance(item, AttributeSearchResult) else AttributeSearchResult.model_validate(item)
            )
        except Exception:
            logger.warning("Skipping unprocessable attribute result during scoring", exc_info=True)
            continue
    return validated


def attribute_scores(
    exec_fn: Callable[[AttributeSearchInput], list[AttributeSearchResult]],
    *,
    attributes: list[str],
    source_type: SourceType = "video_file",
    sensor_resolver: Callable[[str], str] | None = None,
) -> Stage:
    """Score every hit by attribute evidence inside its own clip window.

    For each hit: resolve the behavior-index source filter (via the injected
    ``sensor_resolver`` when available, falling back to the hit's
    ``video_name``/``sensor_id``), query the attributes within the hit's
    (possibly zero-padded) window, and write ``scores["attribute"]`` — the
    per-result score sum normalised by the attribute count. Matched object ids
    union into the hit; an attribute screenshot is preferred over the hit's
    existing one. Hits with no attribute evidence score ``0.0`` so downstream
    fusion is total.
    """
    attribute_count = len(attributes)

    def _resolve_filter_id(hit: Hit) -> str:
        filter_sensor_id = ""
        if hit.sensor_id and sensor_resolver is not None:
            # Stream-id -> sensor-id resolution is best-effort enrichment with
            # a defined fallback (video_name / sensor_id), so it never aborts.
            try:
                filter_sensor_id = sensor_resolver(hit.sensor_id)
                if filter_sensor_id != hit.sensor_id:
                    logger.info(f"Converted stream_id '{hit.sensor_id}' to sensor_id '{filter_sensor_id}'")
            except Exception as e:
                logger.warning(f"VST conversion failed: {scrub_log(str(e))}. Using fallback")
        return filter_sensor_id or hit.video_name or hit.sensor_id or ""

    def _lookup(hit: Hit) -> list[AttributeSearchResult]:
        start_dt = safe_iso8601_to_datetime(hit.start_time)
        end_dt = safe_iso8601_to_datetime(hit.end_time)
        if start_dt is None or end_dt is None:
            logger.warning(
                f"Skipping attribute scoring for {scrub_log(hit.video_name)}: unparseable start/end timestamp"
            )
            return []
        if end_dt <= start_dt:
            original_start = start_dt
            start_dt = original_start - timedelta(seconds=_ZERO_DURATION_PAD_SECONDS)
            end_dt = original_start + timedelta(seconds=_ZERO_DURATION_PAD_SECONDS)
            logger.info(
                f"Extended 0-duration clip to ±{_ZERO_DURATION_PAD_SECONDS} seconds: {hit.start_time} -> "
                f"[{datetime_to_iso8601(start_dt)}, {datetime_to_iso8601(end_dt)}]"
            )
        filter_sensor_id = _resolve_filter_id(hit)
        request = AttributeSearchInput(
            query=attributes,
            source_type=source_type,
            video_sources=[filter_sensor_id] if filter_sensor_id else None,
            timestamp_start=start_dt,
            timestamp_end=end_dt,
            top_k=1,
            min_similarity=0.4,
            fuse_multi_attribute=True,
        )
        return exec_fn(request)

    def _scored(hit: Hit) -> Hit:
        try:
            results = _validated(_lookup(hit))
        except LibraryError:
            # Systemic failure (missing index, backend unreachable, invalid
            # input) affects every hit equally — propagate rather than degrade.
            raise
        except Exception as e:
            logger.warning(
                f"Attribute scoring failed for {scrub_log(hit.video_name)}: {scrub_log(str(e))}",
                exc_info=True,
            )
            results = []

        scores: list[float] = []
        object_ids = list(hit.object_ids)
        for result in results:
            scores.append(_result_score(result))
            oid = _coerce_str(result.metadata.object_id)
            if oid and oid not in object_ids:
                object_ids.append(oid)
        normalised = sum(scores) / attribute_count if attribute_count > 0 else 0.0
        attribute_screenshot = _coerce_str(results[0].screenshot_url) if results else ""

        enriched = hit.with_scores(**{ATTRIBUTE: normalised})
        return replace(
            enriched,
            object_ids=tuple(object_ids),
            screenshot_url=attribute_screenshot or hit.screenshot_url,
        )

    def _apply(ranks: Ranks) -> Ranks:
        logger.info(f"Attribute-scoring {len(ranks.hits)} hits using {attribute_count} attributes")
        return ranks.with_hits(_scored(hit) for hit in ranks.hits)

    return _apply
