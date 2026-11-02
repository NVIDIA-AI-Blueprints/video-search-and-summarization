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
"""The judge request, built exactly as V1 builds it, and its worst-case token bound.

Every judge gets the same body shape as V1's ``eval_gt._judge_once``: ``model``, then two messages (the judge's
hash-pinned prompt file as the system message, and ``EXPECTED: <expected>\\nPREDICTED: <predicted>`` as the user
message), then the judge's request settings in their pinned order. The builder holds no prompt text: the caller
passes the prompt file's contents. ``tests/v2/test_v2_judges.py`` pins V1's continuity judge byte for byte against
V1's own body, and goldens pin every catalog judge's body.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

USER_TEMPLATE_ID = "judge-user-v1"  # the user message below; part of every judge cache identity
# Tokens a chat template can add around the two messages (role markers, a reasoning model's preamble such as
# gpt-oss's harmony header). Deliberately generous: it only widens the pessimistic bound charged before a call.
TEMPLATE_OVERHEAD_TOKENS = 256


def user_message(expected: str | None, predicted: str | None) -> str:
    """V1's user message, ``or ''`` included, so a missing text is sent as an empty one."""
    return f"EXPECTED: {expected or ''}\nPREDICTED: {predicted or ''}"


def build_judge_request(model: str, system_prompt: str, expected: str | None, predicted: str | None,
                        settings: Mapping[str, Any]) -> dict[str, Any]:
    """The body sent to an OpenAI-compatible ``/chat/completions`` endpoint (key order is part of the pin)."""
    if "max_tokens" not in settings:
        raise ValueError("judge settings must bound the output with max_tokens")
    return {"model": model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_message(expected, predicted)}],
            **dict(settings)}


def prompt_bytes(request: Mapping[str, Any]) -> int:
    """UTF-8 bytes of every message's text: the input a judge request carries."""
    return sum(len(str(message.get("content") or "").encode("utf-8")) for message in request["messages"])


def tokens_bound(request: Mapping[str, Any]) -> int:
    """The most one attempt can use: a byte-level tokenizer makes at most one token per byte of text, plus the
    chat template's overhead, plus the output limit. Charged before sending; replaced by measured usage after."""
    return prompt_bytes(request) + TEMPLATE_OVERHEAD_TOKENS + int(request["max_tokens"])
