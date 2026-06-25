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

"""Sample custom parser: XML verdict format.

Demonstrates the custom parser extension point. This parser handles VLM
responses that return structured XML instead of the default Cosmos Reason
think/answer tags.

Expected VLM output format::

    <result>
      <verdict>YES</verdict>
      <reasoning>The worker is not wearing a hard hat.</reasoning>
    </result>

Usage in config.yaml::

    vlm:
      response_format: "xml-verdict"
      custom_parser_module: "custom_parsers.xml_verdict_parser"
"""

import logging
import re
from typing import Dict, Optional

from models.responses import VLMResponse, register_parser

logger = logging.getLogger(__name__)

_RE_XML_VERDICT = re.compile(
    r"<result>\s*"
    r"<verdict>\s*(?P<verdict>[^<]+?)\s*</verdict>\s*"
    r"<reasoning>\s*(?P<reasoning>.*?)\s*</reasoning>\s*"
    r"</result>",
    re.DOTALL,
)

_RE_XML_VERDICT_ALT = re.compile(
    r"<result>\s*"
    r"<reasoning>\s*(?P<reasoning>.*?)\s*</reasoning>\s*"
    r"<verdict>\s*(?P<verdict>[^<]+?)\s*</verdict>\s*"
    r"</result>",
    re.DOTALL,
)


def parse_xml_verdict(text: str, json_config: Optional[Dict] = None) -> VLMResponse:
    """Parse XML-formatted VLM response with <result>/<verdict>/<reasoning> tags.

    Handles both tag orderings (verdict-first or reasoning-first).

    Args:
        text: Raw VLM response text.
        json_config: Unused (kept for registry interface compatibility).

    Returns:
        VLMResponse with reasoning and verdict populated.

    Raises:
        ValueError: If the response doesn't match the expected XML structure.
    """
    stripped = text.strip()

    match = _RE_XML_VERDICT.search(stripped) or _RE_XML_VERDICT_ALT.search(stripped)
    if not match:
        raise ValueError(
            f"XML verdict parser: response does not match "
            f"<result><verdict>...</verdict><reasoning>...</reasoning></result> "
            f"format. Raw response: '{text}'"
        )

    verdict = match.group("verdict").strip().upper()
    reasoning = match.group("reasoning").strip()

    return VLMResponse.model_validate({"reasoning": reasoning, "verdict": verdict})


register_parser("xml-verdict", parse_xml_verdict)
