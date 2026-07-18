# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Second-pass Smart Cities scene-enrichment response parser.

This is a class-based pluggable parser selected through ``vlm.response_parser``.
It intentionally does not use the ``register_parser`` first-pass format registry.
"""

import json
import logging
import re
from typing import Dict


logger = logging.getLogger(__name__)

PROMPT_OUTPUT_CONTRACT = {
    "event_type": "string categorizing the event",
    "description": "detailed description",
    "risk_level": ["low", "medium", "high", "critical"],
    "recommended_action": "what should be done",
}

_FIELDS = ("event_type", "description", "risk_level", "recommended_action")
_RE_MD_FENCE = re.compile(r"^```(?:\w+)?\s*\n(.*?)```\s*$", re.DOTALL)


def _strip_fences(text: object) -> str:
    normalized = str(text if text is not None else "").strip()
    match = _RE_MD_FENCE.match(normalized)
    return match.group(1).strip() if match else normalized


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _salvage(text: str, key: str) -> str:
    """Recover a string field from almost-JSON, such as a missing comma."""
    match = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if not match:
        return ""
    try:
        return _text(json.loads(f'"{match.group(1)}"'))
    except json.JSONDecodeError:
        return _text(match.group(1))


class SceneEnhancer:
    """Parse structured scene-description fields from a VLM response."""

    def parse(self, raw_response: str) -> Dict[str, str]:
        clean = _strip_fences(raw_response)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            data = None

        if not isinstance(data, dict):
            data = {key: _salvage(clean, key) for key in _FIELDS}

        parsed = {
            "event_type": _text(data.get("event_type")) or "unknown",
            "description": _text(data.get("description")),
            "risk_level": _text(data.get("risk_level")) or "unknown",
            "recommended_action": _text(data.get("recommended_action")),
        }
        logger.debug("Parsed Smart Cities scene enrichment: %s", parsed)
        return parsed
