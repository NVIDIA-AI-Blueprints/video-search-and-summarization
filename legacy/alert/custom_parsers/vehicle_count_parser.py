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

"""Sample custom parser: vehicle counting (pass-through demo).

Demonstrates the ``extra`` field for custom parsers that return arbitrary
structured data beyond the standard reasoning/verdict fields.

Expected VLM output format (JSON)::

    {
      "result": {
        "vehicle_counts": {
          "cars": 20,
          "trucks": 40,
          "buses": 5,
          "motorcycles": 12
        },
        "reasoning": "Vehicle counts are based on objects detected in the road scene."
      }
    }

Usage in config.yaml::

    vlm:
      response_format: "vehicle-count"
      custom_parser_module: "custom_parsers.vehicle_count_parser"
"""

import json
import logging
from typing import Dict, Optional

from models.responses import VLMResponse, register_parser

logger = logging.getLogger(__name__)


def parse_vehicle_count(text: str, json_config: Optional[Dict] = None) -> VLMResponse:
    """Parse JSON VLM response containing vehicle counts.

    Extracts ``vehicle_counts`` and ``reasoning`` from the response and stores
    the counts in ``extra`` so they flow through to the Elasticsearch document
    without being lost.

    Args:
        text: Raw VLM response text (JSON).
        json_config: Unused (kept for registry interface compatibility).

    Returns:
        VLMResponse with reasoning and vehicle_counts in extra.

    Raises:
        ValueError: If the response is not valid JSON or missing expected fields.
    """
    text = text.strip()

    # Strip markdown code fences
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
        raise ValueError(f"Vehicle count parser: not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Vehicle count parser: expected JSON object, got {type(data).__name__}")

    result = data.get("result", data)
    if not isinstance(result, dict):
        raise ValueError("Vehicle count parser: 'result' field is not a JSON object")

    vehicle_counts = result.get("vehicle_counts")
    if vehicle_counts is None:
        raise ValueError("Vehicle count parser: missing 'vehicle_counts' field")

    reasoning = str(result.get("reasoning", "")).strip()

    return VLMResponse(
        reasoning=reasoning,
        verdict=None,
        extra={"vehicle_counts": vehicle_counts},
    )


register_parser("vehicle-count", parse_vehicle_count)
