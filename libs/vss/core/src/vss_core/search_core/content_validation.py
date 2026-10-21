# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in, scoped ingestion contracts; no inference or dataset assumptions."""

from __future__ import annotations

import math
import re
from typing import Any

from .errors import InvalidInputError


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_content_contract(value: Any, sources: list[str], require_attributes: bool) -> dict[str, Any]:
    """Validate explicit expectations before opening any network clients."""
    if not isinstance(value, dict) or set(value) - {"schema_version", "embedding", "frame_counts", "document_limit"}:
        raise InvalidInputError("Invalid content contract fields")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise InvalidInputError("Content contract schema_version must be 1")
    embedding = value.get("embedding")
    if not isinstance(embedding, dict) or set(embedding) - {"model", "dimensions", "unit_normalized", "norm_tolerance"}:
        raise InvalidInputError("Invalid embedding content contract")
    if not isinstance(embedding.get("model"), str) or not embedding["model"].strip():
        raise InvalidInputError("Embedding contract requires an explicit nonempty model")
    if type(embedding.get("dimensions")) is not int or not 1 <= embedding["dimensions"] <= 65536:
        raise InvalidInputError("Embedding dimensions must be an integer between 1 and 65536")
    if type(embedding.get("unit_normalized")) is not bool:
        raise InvalidInputError("Embedding contract requires unit_normalized boolean")
    tolerance = embedding.get("norm_tolerance", 0.001)
    if not _finite_number(tolerance) or not 0 <= tolerance < 1:
        raise InvalidInputError("Embedding norm_tolerance must be finite and in [0, 1)")
    counts = value.get("frame_counts", {})
    if not isinstance(counts, dict) or any(not isinstance(key, str) for key in counts):
        raise InvalidInputError("frame_counts must map exact source names to decoded frame counts")
    if (require_attributes or counts) and set(counts) != set(sources):
        raise InvalidInputError("frame_counts must exactly cover the requested source names")
    if any(type(count) is not int or count <= 0 for count in counts.values()):
        raise InvalidInputError("Decoded frame counts must be positive integers")
    limit = value.get("document_limit")
    if type(limit) is not int or not 1 <= limit <= 1000000:
        raise InvalidInputError("Content document_limit must be an integer between 1 and 1000000")
    return {
        "schema_version": 1,
        "embedding": {**embedding, "norm_tolerance": tolerance},
        "frame_counts": counts,
        "document_limit": limit,
    }


class ContentInspector:
    """Keep aggregate evidence and bounded examples, never retain document vectors."""

    def __init__(self, contract: dict[str, Any], identities: dict[str, list[str]], kind: str):
        self.contract = contract
        self.kind = kind
        self.aliases: dict[str, str] = {}
        for source, aliases in identities.items():
            for alias in aliases:
                if alias in self.aliases and self.aliases[alias] != source:
                    raise InvalidInputError("Content source identities overlap")
                self.aliases[alias] = source
        self.result: dict[str, Any] = {
            "documents_checked": 0, "vectors_checked": 0, "violation_count": 0, "examples": [],
            "per_source": {source: {"documents": 0, "vectors": 0, "violations": 0} for source in identities},
        }

    def _violation(self, hit, source, reason):
        self.result["violation_count"] += 1
        if source is not None:
            self.result["per_source"][source]["violations"] += 1
        if len(self.result["examples"]) < 50:
            self.result["examples"].append({"index": hit.get("_index"), "id": hit.get("_id"), "source": source, "reason": reason})

    def visit(self, hit):
        self.result["documents_checked"] += 1
        body = hit.get("_source")
        if not isinstance(body, dict):
            self._violation(hit, None, "missing document source")
            return
        sensor = body.get("sensor")
        identity = sensor.get("id") if self.kind == "embedding" and isinstance(sensor, dict) else body.get("sensorId")
        source = self.aliases.get(identity) if isinstance(identity, str) else None
        if source is None:
            self._violation(hit, None, "missing or unexpected source identity")
            return
        self.result["per_source"][source]["documents"] += 1
        if self.kind == "raw":
            frame = body.get("id")
            if type(frame) is int:
                frame_id = frame
            elif isinstance(frame, str) and re.fullmatch(r"[0-9]+", frame):
                # Bound conversion size; huge numeric strings are malformed IDs.
                frame_id = int(frame) if len(frame) <= 20 else -1
            else:
                self._violation(hit, source, "invalid frame id (requires integer or decimal integer string)")
                return
            if not 0 <= frame_id < self.contract["frame_counts"][source]:
                self._violation(hit, source, "frame id outside decoded file bounds")
            return
        llm = body.get("llm")
        info = llm.get("info") if isinstance(llm, dict) else None
        if not isinstance(info, dict) or info.get("requestedModel") != self.contract["embedding"]["model"]:
            self._violation(hit, source, "requested embedding model mismatch or missing")
        embeddings = llm.get("visionEmbeddings") if isinstance(llm, dict) else None
        if not isinstance(embeddings, list) or not embeddings:
            self._violation(hit, source, "missing or malformed vision embeddings")
            return
        expected = self.contract["embedding"]
        for embedding in embeddings:
            self.result["vectors_checked"] += 1
            self.result["per_source"][source]["vectors"] += 1
            vector = embedding.get("vector") if isinstance(embedding, dict) else None
            if not isinstance(vector, list) or len(vector) != expected["dimensions"]:
                self._violation(hit, source, "embedding dimension or shape mismatch")
                continue
            if any(not _finite_number(number) for number in vector):
                self._violation(hit, source, "embedding contains nonnumeric or nonfinite values")
                continue
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or norm == 0:
                self._violation(hit, source, "embedding has zero or nonfinite L2 norm")
            elif expected["unit_normalized"] and abs(norm - 1) > expected["norm_tolerance"]:
                self._violation(hit, source, "embedding is not unit L2 normalized within tolerance")
