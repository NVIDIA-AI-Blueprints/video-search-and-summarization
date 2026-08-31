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

"""Redaction shared by the two console sinks.

A console sink writes the whole payload to the log, which is the point of it —
but an Alert document carries material an operator may not want in a log
aggregator: ``info.reasoning`` describes the people and vehicles in the footage,
``info.videoSource`` is a VST URL that can embed access parameters, and
``info.location`` is a GPS fix.

**Redaction is on by default.** It used to be opt-in, on the reasoning that
hiding fields from a sink whose only job is to show them is its own surprise.
That has the failure mode backwards: selecting the sink is a debugging decision,
usually made quickly, while the log destination it writes to is someone else's
long-lived system. Defaulting to off means the quick decision silently exports
footage descriptions and media URLs, and nothing about the working deployment
reveals it. Defaulting to on costs a config line when the detail is wanted and
says so in the log when it is masked.

:data:`DEFAULT_REDACT_PATHS` is what is masked when the ``redact`` option is
unset. Naming paths explicitly overrides the list; the word ``none`` turns
masking off, so a destination cleared to hold this material states that rather
than inheriting it.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Iterable, List, Tuple

logger = logging.getLogger(__name__)

#: Substituted for a redacted value so its absence is visible in the log
#: instead of looking like the producer omitted the field.
REDACTED = "[redacted]"

#: Masked when ``redact`` is unset. Each entry is a field the pipeline writes
#: that describes the footage or how to reach it, rather than the verdict:
#:
#: * ``info.reasoning`` / ``info.description`` / ``info.vlm_response`` — the
#:   VLM's own words about the people and vehicles it was shown. The pluggable
#:   parser path writes the third instead of the first two.
#: * ``info.enrichment`` — the enrichment VLM's free text, same material.
#: * ``info.videoSource`` — a VST media URL, which can carry access parameters.
#: * ``info.location`` / ``locations`` / ``smoothLocations`` — a GPS fix for
#:   the event, flat on the Alert document and nested on a Behavior.
#: * ``videoPath`` / ``video_path`` — where the footage is, on disk or in VST.
#:   Both spellings at both levels, because which one a document carries depends
#:   on what produced it: the Behavior schema writes ``videoPath``, while the
#:   HTTP and direct-media response entities write ``video_path``. An
#:   unresolvable path costs nothing, and one that resolves and was left out is
#:   a filesystem layout in someone's log aggregator.
#:
#: The verdict, id, sensorId, category and timestamps are deliberately *not*
#: here: masking those would leave the sink unable to answer the question it is
#: selected to answer.
DEFAULT_REDACT_PATHS: Tuple[str, ...] = (
    "info.reasoning",
    "info.description",
    "info.vlm_response",
    "info.enrichment",
    "info.videoSource",
    "info.location",
    "locations",
    "smoothLocations",
    "videoPath",
    "video_path",
    "info.videoPath",
    "info.video_path",
)

#: Spellings of "do not redact anything". Only an explicit one of these turns
#: masking off — an empty or absent value means the default list, because a
#: rendered deployment config substitutes an unset variable as ``""`` and that
#: must not be the same input as a deliberate opt-out.
REDACTION_OFF_VALUES = frozenset({"none", "off", "no", "false", "disabled"})

#: Return values of :func:`resolve_redact_paths`, for callers that log which
#: of the three states they are in.
REDACTION_DEFAULT = "default"
REDACTION_CONFIGURED = "configured"
REDACTION_DISABLED = "disabled"


def resolve_redact_paths(value: Any) -> Tuple[List[str], str]:
    """Turn a configured ``redact`` option into the paths to mask, and why.

    Three inputs have to stay distinguishable, which is why this returns a mode
    alongside the list:

    * **unset** — ``None``, or the empty string a rendered config produces for
      an unset variable — yields :data:`DEFAULT_REDACT_PATHS`. This is the case
      that used to yield nothing.
    * **``none``** (or any of :data:`REDACTION_OFF_VALUES`), or an explicitly
      empty list, yields no masking. Deliberate, and logged as such.
    * **a list or comma-separated string** replaces the default entirely, so an
      operator who names three fields gets exactly those three and is not left
      guessing whether the defaults were added underneath.

    Returns:
        ``(paths, mode)`` where mode is one of :data:`REDACTION_DEFAULT`,
        :data:`REDACTION_CONFIGURED`, :data:`REDACTION_DISABLED`.
    """
    if value is None:
        return list(DEFAULT_REDACT_PATHS), REDACTION_DEFAULT

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return list(DEFAULT_REDACT_PATHS), REDACTION_DEFAULT
        if stripped.lower() in REDACTION_OFF_VALUES:
            return [], REDACTION_DISABLED

    if isinstance(value, bool):
        # `redact: false` reads as an opt-out; `redact: true` as "use the
        # defaults", which is what an author writing that almost certainly
        # means.
        return ([], REDACTION_DISABLED) if not value else (
            list(DEFAULT_REDACT_PATHS), REDACTION_DEFAULT
        )

    if isinstance(value, (list, tuple, set)) and not value:
        return [], REDACTION_DISABLED

    paths = parse_redact_paths(value)
    if not paths:
        # A value that parsed to nothing usable — a lone comma, a number. Not
        # a recognized opt-out, so fall back to the safe side.
        return list(DEFAULT_REDACT_PATHS), REDACTION_DEFAULT
    return paths, REDACTION_CONFIGURED


def resolve_max_chars(value: Any, config_key: str) -> int:
    """A rendered line-length cap, or ``0`` for no truncation.

    Both console sinks read this straight through ``int()``, which raises on the
    empty string a rendered config produces for an unset variable -- so a
    deployment that mentioned ``max_chars`` without setting it crash-looped the
    pipeline child inside a sink constructor. A display setting is not worth
    refusing to start over, which is the same line the Redis broker's tuning
    knobs draw: what decides *where* a component connects fails loudly, what
    decides how it looks falls back and says so.

    Negative is read as no truncation rather than rejected: slicing by it would
    silently cut from the other end.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "%s is %r, which is not a number of characters; logging documents "
            "in full instead.", config_key, value,
        )
        return 0
    return max(resolved, 0)


def redaction_notice(paths: List[str], mode: str, config_key: str) -> str:
    """Describe the redaction state for the sink's startup log line.

    Shared so the two sinks cannot drift into describing the same three states
    differently, and so the opt-out is always named next to the key that sets
    it.
    """
    if mode == REDACTION_DISABLED:
        return (
            f". Redaction is OFF because {config_key} says so: the document is "
            f"written in full, including the VLM's description of the footage "
            f"and the media URL"
        )
    if mode == REDACTION_CONFIGURED:
        return f". Masking the configured paths {paths}"
    return (
        f". Masking {paths} by default; set {config_key} to a list of dotted "
        f"paths to choose others, or to 'none' to log the document in full"
    )


def parse_redact_paths(value: Any) -> List[str]:
    """Normalize a ``redact`` value into a list of dotted paths.

    Accepts a list or a single comma-separated string, since deployment configs
    render values through environment substitution and cannot always produce a
    YAML list. Returns ``[]`` for anything empty — callers that need to tell an
    unset option from a deliberate opt-out want :func:`resolve_redact_paths`.
    """
    if not value:
        return []
    if isinstance(value, str):
        candidates: Iterable[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        return []
    return [str(item).strip() for item in candidates if str(item).strip()]


def redact(payload: Any, paths: List[str]) -> Any:
    """Return ``payload`` with every dotted path in ``paths`` masked.

    The payload is copied first: these sinks render documents that the caller
    still owns, and the Elasticsearch and Redis sinks are expected to publish
    the unredacted original.

    A **list** is masked element by element. It used to be returned untouched
    along with every other non-dictionary, which read as "there are no field
    paths here" — but a JSON array of alert documents has exactly the paths
    below, one set per element, and the raw write path renders whatever a
    producer published. So a batch arrived as an array and was logged in full.

    Any path that does not resolve, and any other non-dictionary, is left alone —
    a console sink must never fail because of a redaction rule.
    """
    if not paths:
        return payload
    if isinstance(payload, list):
        return [redact(item, paths) for item in payload]
    if not isinstance(payload, dict):
        return payload

    redacted = copy.deepcopy(payload)
    for path in paths:
        segments = [segment for segment in path.split(".") if segment]
        if not segments:
            continue
        cursor: Any = redacted
        for segment in segments[:-1]:
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(segment)
        if isinstance(cursor, dict) and segments[-1] in cursor:
            cursor[segments[-1]] = REDACTED
    return redacted
