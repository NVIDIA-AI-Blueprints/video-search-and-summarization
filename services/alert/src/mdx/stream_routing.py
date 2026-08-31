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

"""One definition of what makes a stream routing map valid.

Four places need it: the source reads a kind-to-stream map, the event-bridge sink
and the terminal sink each resolve their own routes, and configuration validation
checks a map before any of them is built. All four were rejecting the same
mistakes in four separate implementations, with four wordings and four failure
contracts. Which message an operator saw, and whether it arrived as a validation
failure or as a constructor traceback, depended on which happened to run first.

The rules are worth stating once:

* A **blank** stream name is what a rendered config produces for an unset
  variable, so accepting one means the deployment silently routes to nothing.
* A stream may be named by **one key only**, because the key selects the decode
  schema: sharing one means a payload is decoded with the other kind's schema,
  which does not fail -- it publishes a wrong document.
* A source's map must **cover both kinds**. Both are produced upstream and
  verified by the same pipeline, so a map naming one is a config that lost a
  line, not a deployment shape somebody chose.

What callers supply is the setting path, the key vocabulary their section uses,
and the remedy -- the parts that genuinely differ. An absent key means "do not
consume that kind" to the source and "do not publish it" to the event-bridge
sink, while the terminal sink has no such option: it requires a stream per kind
and infers none.
"""

from typing import Any, Dict, Mapping, Optional, Sequence

#: Event kinds the pipeline can actually decode. The kind comes from the
#: configured stream key and selects the protobuf schema downstream, where
#: anything that is not ``incident`` is decoded as a Behavior — so a typo in a
#: stream key does not fail, it silently decodes every incident with the wrong
#: schema. Validating the key at construction is what turns that into an error
#: an operator sees at boot.
SUPPORTED_KINDS = ("incident", "alert")
HEARTBEAT_KIND = "heartbeat"

#: The event-bridge sink's output routes -- not event kinds, which is why it is a
#: second vocabulary rather than a reuse of the first.
#:
#: Here rather than in the sink because startup validation needs it too, and it
#: cannot import that module: doing so would pull the ``redis`` package into
#: every Kafka deployment. Two literals were the alternative, and the reason
#: this module exists is that the copies drifted.
EVENT_BRIDGE_SINK_ROUTES = ("enhanced_anomaly", "incidents")

#: Stream keys accepted as an older spelling of a canonical kind, and what they
#: mean. ``anomaly`` is the pre-``event_bridge`` configuration layout's name for
#: an alert; it decodes as a Behavior either way, so an existing config keeps
#: working. Normalized to the canonical kind on the way in rather than carried
#: through, so nothing downstream has to know two names for one thing — and
#: warned about, because a config still using it is a config nobody has revisited
#: since the layout changed.
LEGACY_KIND_ALIASES: Dict[str, str] = {"anomaly": "alert"}

#: Appended to every blank-value rejection. Named because it is the single most
#: common cause and the least obvious from the message alone: the config on disk
#: looks like it has a value, and the template that produced it is elsewhere.
UNRESOLVED_VARIABLE_HINT = (
    "A blank stream name is almost always an unresolved variable in a rendered "
    "config."
)


def clean_stream_name(value: Any) -> Optional[str]:
    """``value`` as a usable stream name, or ``None`` if it is not one.

    The predicate behind every check here, kept separate so that callers which
    only want to know -- rather than to reject -- do not have to catch an
    exception to find out. Surrounding whitespace is removed rather than
    rejected: it is invisible in a config file and never intended.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def require_stream_name(value: Any, setting: str, remedy: str = "") -> str:
    """``value`` as a stream name, or raise saying why it is not one.

    ``setting`` is the full config path, so the message names the line to edit
    rather than the class that happened to read it. ``remedy`` is the caller's
    own sentence about what the alternative is, since removing the key means
    something different to each caller.
    """
    if value is not None and not isinstance(value, str):
        raise ValueError(
            f"{setting} must be a stream name, got {type(value).__name__}"
        )

    name = clean_stream_name(value)
    if name is None:
        detail = f" {remedy}" if remedy else ""
        raise ValueError(f"{setting} is empty.{detail} {UNRESOLVED_VARIABLE_HINT}")
    return name


def require_stream_map(streams: Any, setting: str,
                       keys: Sequence[str]) -> Dict[str, Any]:
    """``streams`` as a non-empty mapping, or raise.

    A missing or empty map is separated from a map with bad entries because the
    two are different mistakes: one is a section that was never filled in, the
    other is a section that was filled in wrongly.

    ``keys`` is the vocabulary that section accepts, and it is required rather
    than defaulted because the two sections do not share one: the source's keys
    are event kinds, the event-bridge sink's are ``enhanced_anomaly`` and
    ``incidents``. Defaulting to the kinds meant a sink with an empty map was
    told to name "at least one of incident, alert" -- two keys it does not
    accept, sending the operator to write a config that would then be rejected
    for using them.

    Whether the map covers every kind is decided by :func:`require_kind_coverage`,
    for the callers whose keys are kinds.
    """
    if not isinstance(streams, dict) or not streams:
        raise ValueError(
            f"{setting} must be a mapping naming a stream for at least one of "
            f"{', '.join(keys)}"
        )
    return streams


def require_known_keys(streams: Mapping[str, Any], setting: str,
                       keys: Sequence[str], extra: Sequence[str] = ()) -> None:
    """Raise if the map holds a key the reader does not look for.

    A reader asks for the keys it knows and ignores the rest, which makes a
    misspelt one indistinguishable from an absent one -- and absent means "do
    not publish that kind". So ``incident:`` where ``incidents:`` was meant
    silently disables that route: the sink starts, reports healthy, publishes
    the other kind, and logs one line per dropped message about a stream nobody
    configured.

    The legacy ``<key>_stream`` spelling is accepted for every key, because the
    readers accept it. ``extra`` is for keys a section takes but should not
    advertise -- the heartbeat stream, which is not an event kind, and the
    legacy kind spellings -- so they are not rejected and not recommended.
    """
    accepted = {
        key for name in (*keys, *extra) for key in (name, f"{name}_stream")
    }
    unknown = [key for key in streams if key not in accepted]
    if not unknown:
        return
    raise ValueError(
        f"{setting} has no place for {', '.join(sorted(unknown))}. This section "
        f"accepts {', '.join(keys)}, and a key it does not accept is ignored "
        f"rather than read -- which is indistinguishable from leaving the route "
        f"out, so half the output would go nowhere while the sink reported "
        f"healthy."
    )


def canonical_kind(key: str) -> str:
    """The event kind a stream key names, after the spellings it may use.

    ``incident_stream`` and ``incident`` are one key with a suffix the config
    layout used to carry, and ``anomaly`` is the pre-``event_bridge`` name for an
    alert. Folding them here means a rule about kind coverage counts a config
    using either spelling as covering that kind, rather than reporting it
    missing while the stream is plainly configured.
    """
    kind = key[: -len('_stream')] if key.endswith('_stream') else key
    return LEGACY_KIND_ALIASES.get(kind, kind)


def require_kind_coverage(present: Any, setting: str,
                          kinds: Sequence[str] = SUPPORTED_KINDS) -> None:
    """Raise unless every kind in ``kinds`` is among ``present``.

    A source consuming one kind is a working service that silently carries half
    its traffic: nothing raises, nothing is dropped by a counter anyone reads,
    and "the alerts never arrived" looks from the outside exactly like "no alert
    stream was configured" -- which is the same sentence an operator would need
    and the one nobody gets. Both kinds reach the same pipeline and the same
    verifier, so consuming one is not a deployment shape anybody chose on
    purpose; it is a config that lost a line.

    It was a warning once, on the grounds that ``SourceKafka`` accepts one topic
    and the transports should not disagree. That reasoning is inverted: Kafka's
    laxness is not a contract worth reproducing, and this transport is the one
    still cheap to make strict.
    """
    missing = [kind for kind in kinds if kind not in set(present)]
    if not missing:
        return
    raise ValueError(
        f"{setting} configures no {' or '.join(missing)} stream. Both kinds are "
        f"produced upstream and verified by the same pipeline, so consuming only "
        f"{', '.join(k for k in kinds if k not in missing) or 'some'} means the "
        f"rest is never read while the service reports healthy. Add a "
        f"'{missing[0]}' key naming the stream it arrives on."
    )


def require_distinct_streams(streams: Mapping[str, Any], setting: str) -> None:
    """Raise if two keys name the same stream.

    Blank and non-string values are ignored here rather than reported again:
    :func:`require_stream_name` owns that message, and a caller that has not run
    it yet gets the clearer error from doing so.
    """
    claimed_by: Dict[str, str] = {}
    for key, value in streams.items():
        name = clean_stream_name(value)
        if name is None:
            continue
        if name in claimed_by:
            raise ValueError(
                f"{setting} maps '{name}' to both '{claimed_by[name]}' and "
                f"'{key}'. One stream cannot carry two event kinds -- the kind "
                f"selects the decode schema, so one of them would be decoded "
                f"wrongly."
            )
        claimed_by[name] = key
