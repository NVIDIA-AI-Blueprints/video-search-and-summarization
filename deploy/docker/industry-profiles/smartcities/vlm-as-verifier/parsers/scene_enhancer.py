# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One-pass Smart Cities structured-response parser."""

import json
import logging
import re
from typing import Dict


logger = logging.getLogger(__name__)

PROMPT_OUTPUT_CONTRACT = {
    "verdict": ["Yes", "No"],
    "description": "detailed description",
    "risk_level": ["low", "medium", "high", "critical"],
    "recommended_action": "what should be done",
}

_FIELDS = ("verdict", "description", "risk_level", "recommended_action")
_VERDICTS = {"yes": "Yes", "no": "No"}
_RISK_LEVELS = {"low", "medium", "high", "critical"}
_RE_MD_FENCE = re.compile(r"^```(?:\w+)?\s*\n(.*?)```\s*$", re.DOTALL)


def _strip_fences(text: object) -> str:
    normalized = str(text if text is not None else "").strip()
    match = _RE_MD_FENCE.match(normalized)
    return match.group(1).strip() if match else normalized


class SceneEnhancer:
    """Parse structured collision fields from the single VLM response."""

    def parse(self, raw_response: str) -> Dict[str, str]:
        clean = _strip_fences(raw_response)
        data = json.loads(clean)
        if not isinstance(data, dict):
            raise TypeError("VLM response must be a JSON object")

        parsed: Dict[str, str] = {}
        for field in _FIELDS:
            value = data.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"VLM response field '{field}' must be a non-empty string")
            parsed[field] = value.strip()

        verdict = parsed["verdict"].lower()
        if verdict not in _VERDICTS:
            raise ValueError("VLM response field 'verdict' must be Yes or No")
        parsed["verdict"] = _VERDICTS[verdict]

        if parsed["risk_level"].lower() not in _RISK_LEVELS:
            raise ValueError(
                "VLM response field 'risk_level' must be low, medium, high, or critical"
            )
        parsed["risk_level"] = parsed["risk_level"].lower()
        logger.debug("Parsed Smart Cities response: %s", parsed)
        return parsed
