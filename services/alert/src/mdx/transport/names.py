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

"""What an operator may call each transport, and how that is read.

Two factories select a transport from a name in the same config file: the event
bridge for its source and error sink, and the terminal sink for verified
results. Each had its own alias table and its own normalizer, and the second
said in its docstring that it was kept on the same contract as the first --
which is an invariant maintained by remembering, and the kind that holds until
someone adds a spelling to one table.

They are not identical sets, and that is fine: the terminal sink can be an
Elasticsearch index, which is not something the event bridge can read from. What
they share is the vocabulary underneath and the folding rule applied to it, so
those are here and the difference is expressed as one table extending the other.
"""

from typing import Any, Dict, Mapping, Optional

KAFKA = 'kafka'
REDIS_STREAM = 'redisStream'
CONSOLE = 'console'
ELASTIC = 'elastic'

#: Accepted spellings for each transport the event bridge can carry. Selection
#: is case- and separator-insensitive so ``redisStream``, ``redis_stream`` and
#: ``redis-stream`` all resolve to the same transport, matching the
#: ``redisStream`` spelling used by vss-behavior-analytics.
TRANSPORT_ALIASES: Dict[str, str] = {
    'kafka': KAFKA,
    'redisstream': REDIS_STREAM,
    'redis': REDIS_STREAM,
    'console': CONSOLE,
}

#: The same, for a terminal sink -- which additionally may be an Elasticsearch
#: index. Written as an extension rather than a second literal so a spelling
#: added above cannot be one the terminal sink silently refuses.
TERMINAL_SINK_ALIASES: Dict[str, str] = {
    **TRANSPORT_ALIASES,
    'elastic': ELASTIC,
    'elasticsearch': ELASTIC,
}


def fold(value: str) -> str:
    """The comparison form of a configured transport name.

    Case and separators are discarded because they are the difference between
    one YAML author's ``redis_stream`` and another's ``redisStream``, and neither
    meant a different transport.
    """
    return value.strip().lower().replace('_', '').replace('-', '')


def normalize(value: Any, aliases: Mapping[str, str] = TRANSPORT_ALIASES) -> Optional[str]:
    """Resolve a configured transport name to its canonical form.

    Returns ``None`` when the value is not a recognized transport, including
    when it is not a string at all -- a caller distinguishing "not configured"
    from "configured wrongly" reads that from its own config, not from here.
    """
    if not isinstance(value, str):
        return None
    return aliases.get(fold(value))


def require_terminal_sink_type(value: Any) -> str:
    """The transport ``vlm_enhanced_sink.type`` names, or raise saying it names none.

    Absent or blank is Elasticsearch, which is the default this service has
    always had. Anything else present has to resolve.

    Here rather than in the sink factory because the factory runs inside a
    forked pipeline child, and that is where the refusal used to happen: a
    typo -- ``mongo``, ``elasticsearc`` -- passed startup validation, because
    validation asked only whether the value was *Redis* and read "no" for
    everything it did not recognize. The container then crash-looped on a
    traceback about a sink class, several steps after the config that caused it
    had been declared valid.

    One function so the answer validation gives and the one the factory builds
    from cannot disagree, and so the supported spellings are listed in the place
    that knows them.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return ELASTIC

    resolved = normalize(value, TERMINAL_SINK_ALIASES)
    if resolved is None:
        raise ValueError(
            f"Unsupported vlm_enhanced_sink.type: {value!r} (supported: "
            f"'{ELASTIC}', '{KAFKA}', '{REDIS_STREAM}', '{CONSOLE}')"
        )
    return resolved
