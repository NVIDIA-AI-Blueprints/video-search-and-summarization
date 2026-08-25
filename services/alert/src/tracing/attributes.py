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

"""Attribute policy for every manually-built span (REQ-014).

Default attributes are IDs and metadata only. Prompt text, VLM response bodies
and video URLs are attached only when ``alert_agent.tracing.include_content`` is
explicitly enabled, and are truncated when they are. The AB project guideline
this implements: *alert data may contain PII, never log raw payloads*.

Every manual span must build its attributes through :func:`manual_attributes`
rather than calling ``set_attribute`` ad hoc — a single gated builder is the only
way the policy stays true as call sites are added. Auto-instrumented spans are
outside this module's reach and are handled by the sanitizing exporter in
``src.tracing`` instead; the two mechanisms are complementary, not alternatives.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

# Content attributes are the ones that can carry PII. They are only ever emitted
# when the content gate is open, and always truncated.
CONTENT_KEYS = frozenset(
    {
        "vlm.prompt",
        "vlm.system_prompt",
        "vlm.response",
        "video.url",
    }
)

DEFAULT_MAX_CONTENT_CHARS = 512


def truncate(value: Any, max_chars: int = DEFAULT_MAX_CONTENT_CHARS) -> Optional[str]:
    """Coerce to ``str`` and cut to ``max_chars``, marking that it was cut.

    Returns ``None`` for ``None`` so callers can drop the attribute entirely
    rather than emitting the string ``"None"``.
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if max_chars is None or max_chars < 0 or len(text) <= max_chars:
        return text
    # The suffix counts against the budget. Appending it to a full-length prefix
    # made `max_content_chars` a floor rather than the maximum it is named for -
    # a 512 setting produced 528 characters. When the budget is too small to hold
    # any suffix at all, a bare cut is the honest result.
    marker = f"...[+{len(text) - max_chars} chars]"
    keep = max_chars - len(marker)
    if keep <= 0:
        return text[:max_chars]
    marker = f"...[+{len(text) - keep} chars]"
    return text[:max_chars - len(marker)] + marker


def _put(target: Dict[str, Any], key: str, value: Any) -> None:
    """Set ``key`` only when the value carries information.

    OTel drops ``None`` attributes anyway, but emitting empty strings makes
    Jaeger's attribute list noisier without adding signal.
    """
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    target[key] = value


def manual_attributes(
    message: Optional[Mapping[str, Any]] = None,
    *,
    include_content: bool = False,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    **extra: Any,
) -> Dict[str, Any]:
    """Build the attribute set for a manually-created span.

    ``message`` is the alert/incident dict the pipeline threads around; the
    identifying fields are lifted from it. ``extra`` carries per-span additions
    (``attempt``, ``service``, ``success`` and so on).

    Keys in :data:`CONTENT_KEYS` are dropped unless ``include_content`` is true,
    and truncated when kept. Everything else is emitted as-is: sensor ids,
    categories, verdicts and counts are not PII, and withholding them would make
    the trace useless for the incident triage it exists to support.
    """
    attrs: Dict[str, Any] = {}

    if message:
        _put(attrs, "sensorId", message.get("sensorId"))
        _put(attrs, "category", message.get("category"))
        _put(attrs, "alert_type", message.get("alert_type") or message.get("alertType"))
        _put(attrs, "alert_rule_id", message.get("alert_rule_id") or message.get("alertRuleId"))
        _put(attrs, "correlationId", message.get("correlationId") or message.get("correlation_id"))

    for key, value in extra.items():
        if key in CONTENT_KEYS:
            if include_content:
                _put(attrs, key, truncate(value, max_content_chars))
            # Gate closed: drop it. Not even a redacted placeholder — an absent
            # attribute is unambiguous, a placeholder invites someone to "just
            # turn it on to see" in an environment where that is not safe.
            continue
        _put(attrs, key, value)

    return attrs


def verdict_of(message: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Pull the verdict off a completed message, tolerating a missing ``info``."""
    if not message:
        return None
    info = message.get("info")
    if isinstance(info, Mapping):
        return info.get("verdict")
    return None
