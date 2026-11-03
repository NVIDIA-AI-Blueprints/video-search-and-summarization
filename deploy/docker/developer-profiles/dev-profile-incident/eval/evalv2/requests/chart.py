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

"""The dashboard "Ask for a chart" request, built exactly as the console builds it, and its reply parser.

A port of the console's ``GatewayChartInterpreter`` (``incident-console-v2/src/modules/dashboard/infrastructure/
gateway/chart-interpreter.ts``) and the trim and length rule of its route (``src/app/api/dashboard/chart/route.ts``):

- the system prompt is ``contracts/prompts/chart/system.txt`` (the console's export), served only while its SHA-256
  matches ``contracts/prompts/registry.json``; the user's request is the only other message;
- the settings and the key order are the console's, so the body the adapter posts upstream is byte-identical to the
  one ``vlm-gateway`` forwards (both re-serialise the same JSON object with httpx). ``tests/v2/test_v2_dashboard_task``
  pins it to ``contracts/golden/chart.json`` (eval is a producer of ``chart/request``) byte for byte;
- :func:`parse_reply` is ``parseReply``: the text from the first ``{`` to the last ``}``, parsed as JSON, else ``None``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evalv2.core.hashing import sha256_bytes, sha256_json

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
PROMPT_ID = "chart/system"
ALLOWLIST_ID = "chart/allowlist"
GOLDEN = "chart/request"
MAX_REQUEST_LENGTH = 300  # CHART_REQUEST_MAX_LENGTH (generated-chart.ts)
CONSOLE_TIMEOUT_S = 45.0  # chart-interpreter.ts TIMEOUT_MS: the console gives up on a slower reply
# chart-interpreter.ts, in its key order (the golden pins them).
SETTINGS: dict[str, Any] = {"stream": False, "temperature": 0, "max_tokens": 200,
                            "chat_template_kwargs": {"enable_thinking": False}}
# Request shapes. ``console-gateway-v1`` is the console's own upstream body before #459 (9b2d09925), the one the
# 2026-11-02 C1c pilot sent. ``agent-route-v2`` is what the agent's POST /api/v1/incidents/chart-requests now sends
# upstream for the same two messages: LangChain's ChatOpenAI with the profile's ``openai_llm`` entry (temperature 0,
# ``enable_thinking`` false by default) bound to ``CHART_REPLY_MAX_TOKENS`` 200, which it posts as
# ``max_completion_tokens``. tests/v2/test_v2_dashboard_task.py checks both against their sources.
AGENT_SETTINGS: dict[str, Any] = {"stream": False, "temperature": 0.0, "max_completion_tokens": 200,
                                  "chat_template_kwargs": {"enable_thinking": False}}
# ``agent-route-v3`` is ``agent-route-v2`` after the console's severity-word normalisation
# (``chart-severity-words-v1``, domain/severity-words.ts): the same upstream settings, but the user message is the
# normalised request. The chart prompt bytes are unchanged.
REQUEST_SHAPES: dict[str, dict[str, Any]] = {"console-gateway-v1": SETTINGS, "agent-route-v2": AGENT_SETTINGS,
                                             "agent-route-v3": AGENT_SETTINGS}
NORMALISING_SHAPES = frozenset({"agent-route-v3"})
SEVERITY_WORDS_ID = "chart/severity-words"
DEFAULT_SHAPE = "console-gateway-v1"  # the sealed C1c pilot's shape; new runs name ``agent-route-v2``
# ECMAScript's String.prototype.trim: WhiteSpace and LineTerminator code points (zod's .trim() calls it).
_JS_SPACE = frozenset("\t\n\v\f\r              "
                      "    　﻿")


class ChartRequestError(ValueError):
    """A request the console's route would refuse (empty, or longer than 300 characters), or a changed prompt."""


def _registry_entry(prompt_id: str) -> dict[str, Any]:
    registry = json.loads((CONTRACTS_DIR / "prompts" / "registry.json").read_text(encoding="utf-8"))
    for entry in registry["prompts"]:
        if entry["id"] == prompt_id:
            return dict(entry)
    raise ChartRequestError(f"{prompt_id} is not in contracts/prompts/registry.json")


def _registered_bytes(prompt_id: str) -> bytes:
    entry = _registry_entry(prompt_id)
    data = (CONTRACTS_DIR / "prompts" / entry["file"]).read_bytes()
    if sha256_bytes(data) != entry["sha256"]:
        raise ChartRequestError(f"contracts/prompts/{entry['file']} does not match its registry sha256: re-run "
                                "`npm run export:prompts` and update the registry and the golden together")
    return data


def system_prompt() -> str:
    return _registered_bytes(PROMPT_ID).decode("utf-8")


def js_trim(text: str) -> str:
    start, end = 0, len(text)
    while start < end and text[start] in _JS_SPACE:
        start += 1
    while end > start and text[end - 1] in _JS_SPACE:
        end -= 1
    return text[start:end]


def request_text(request: str) -> str:
    """The text the console sends for a typed request: trimmed, 1 to 300 characters (UTF-16 units, as zod counts)."""
    text = js_trim(request)
    length = len(text.encode("utf-16-le")) // 2
    if not 1 <= length <= MAX_REQUEST_LENGTH:
        raise ChartRequestError(f"the console accepts 1 to {MAX_REQUEST_LENGTH} characters; this request has {length}")
    return text


def severity_words() -> dict[str, Any]:
    """The console's exported severity-word table (hash-checked against the registry)."""
    return json.loads(_registered_bytes(SEVERITY_WORDS_ID).decode("utf-8"))


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def normalize_severity_words(request: str) -> str:
    """``normalizeSeverityWords``: each canonical severity word that qualifies severity or an incident noun becomes
    ``<band>-severity``; the typed request comes back unchanged when the result would pass the length limit."""
    table = severity_words()
    flags = re.ASCII | (re.IGNORECASE if "i" in table["flags"] else 0)

    def band(word: str) -> str:
        level = table["word_levels"][re.sub(r"[ \t]+", " ", word.lower())]
        return str(table["band_of_level"][str(level)])

    def replace(match: re.Match[str]) -> str:
        return f"{band(match.group(1))}-severity" + ("" if match.group(3) else " ")

    out = re.sub(table["pattern"], replace, request, flags=flags)
    return request if _utf16_len(out) > table["max_length"] else out


def chart_messages(request: str, *, normalize: bool = False) -> list[dict[str, str]]:
    """The two messages the console sends (to the agent since #459; to the gateway before): the golden's body.
    With ``normalize`` the user message is the request after the severity-word normalisation (agent-route-v3)."""
    text = request_text(request)
    return [{"role": "system", "content": system_prompt()},
            {"role": "user", "content": normalize_severity_words(text) if normalize else text}]


def build_chart_request(model: str, request: str, shape: str = DEFAULT_SHAPE) -> dict[str, Any]:
    """The upstream body for ``shape`` (``model``, ``messages``, then that shape's settings)."""
    if shape not in REQUEST_SHAPES:
        raise ChartRequestError(f"unknown chart request shape {shape!r}; known: {sorted(REQUEST_SHAPES)}")
    return {"model": model, "messages": chart_messages(request, normalize=shape in NORMALISING_SHAPES),
            **json.loads(json.dumps(REQUEST_SHAPES[shape]))}


def chart_provenance(shape: str = DEFAULT_SHAPE) -> dict[str, Any]:
    """What a chart record pins: the prompt and allowlist SHA-256s, the request shape and its settings, the golden."""
    return {"stage": "chart", "prompt_id": PROMPT_ID, "prompt_sha256": sha256_bytes(_registered_bytes(PROMPT_ID)),
            "allowlist_sha256": sha256_bytes(_registered_bytes(ALLOWLIST_ID)), "request_shape": shape,
            "settings_sha256": sha256_json(REQUEST_SHAPES[shape]), "golden": GOLDEN,
            **({"severity_words_sha256": sha256_bytes(_registered_bytes(SEVERITY_WORDS_ID))} if shape in NORMALISING_SHAPES else {})}


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")  # JSON.parse refuses NaN and Infinity; Python's json accepts them


def parse_reply(content: str | None) -> Any:
    """``parseReply``: the JSON between the first ``{`` and the last ``}``, or ``None`` when there is none."""
    if not content:
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(content[start:end + 1], parse_constant=_no_constant)
    except ValueError:
        return None
