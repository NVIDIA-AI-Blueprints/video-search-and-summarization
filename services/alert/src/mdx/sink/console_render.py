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
aggregator: ``info.reasoning`` describes people and vehicles in the footage,
``info.videoSource`` is a VST URL that can embed access parameters, and
``info.location`` is a GPS fix. Redaction is opt-in through configuration
rather than on by default, because hiding fields from a sink whose only job is
to show them would be its own surprise. The names to redact are listed in the
config so the choice stays with whoever owns the log destination.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable, List

#: Substituted for a redacted value so its absence is visible in the log
#: instead of looking like the producer omitted the field.
REDACTED = "[redacted]"


def parse_redact_paths(value: Any) -> List[str]:
    """Normalize the configured ``redact`` option into a list of dotted paths.

    Accepts a list or a single comma-separated string, since deployment configs
    render values through environment substitution and cannot always produce a
    YAML list.
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

    Anything that is not a dictionary, and any path that does not resolve, is
    left alone — a console sink must never fail because of a redaction rule.
    """
    if not paths or not isinstance(payload, dict):
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
