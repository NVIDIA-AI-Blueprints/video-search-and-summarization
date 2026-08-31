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

"""The Redis Streams wire format, separated from the client that carries it.

Deciding which field of an entry holds the event body is the part of this
transport with real rules -- two envelope formats, an ordered precedence between
them, and a sidecar field that must not be mistaken for a body. It is also the
part with no connection in it: given a field map it is a pure function.

Which is why it is here rather than in :mod:`mdx.redis_stream_broker`. Reaching
it there meant importing the broker, and the broker imports ``redis``, so a
Kafka-only deployment picked up the client library to read a dict and
:mod:`mdx.stream_message` -- used by both transports -- had to defer its import
inside a function to avoid it. It also means these rules can be tested as the
functions they are, with no client, real or fake, to stand up first.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Canonical MDX envelope fields.
#:
#: ``value`` is the field the publish path writes, because it is the one the
#: readers shipped in this repository expect: the Logstash input plugin under
#: ``tools/logstash-plugins/input/redis-stream`` defaults ``data_field`` to
#: ``value`` (and decodes protobuf there via ``data_codec``),
#: ``tools/message-broker-consumers/redis_to_file.py`` reads ``value``, and
#: behavior-analytics' own Redis sink writes ``key``/``value``/``headers``.
#: Writing the body elsewhere would leave all three reading nothing.
#:
#: Recorded here because a JSON-envelope design that names ``data`` as the body
#: field describes the *read* path, which accepts it — see
#: :data:`PAYLOAD_FIELD_PRECEDENCE` — and not this one.
KEY_FIELD = b"key"
PAYLOAD_FIELD = b"value"
HEADERS_FIELD = b"headers"

#: Ordered field precedence for locating the event body on the read path.
#: Publishing always uses ``value``.
#:
#: Two envelope formats reach this source, and both have to decode to the same
#: answer every time:
#:
#: 1. the **MDX envelope** — ``key`` / ``value`` / ``headers`` — where ``value``
#:    holds the body. This is what behavior-analytics and VIOS publish.
#: 2. the **JSON envelope** — ``data`` / ``timestamp`` / ``metadata`` — where
#:    ``data`` holds the body and ``metadata`` is a sidecar of attributes
#:    describing it. This is what RT-VLM and the pre-MDX Alert Redis prototype
#:    publish.
#:
#: ``metadata`` is accepted because it is RT-VLM's ``REDIS_PAYLOAD_KEY``
#: default, but it is deliberately **last**: an entry carrying both ``data`` and
#: ``metadata`` has a body *and* a sidecar, and reading the sidecar as the event
#: yields a payload that decodes but describes nothing the pipeline can verify.
#: Precedence rather than first-match-wins is what makes that unambiguous.
PAYLOAD_FIELD_PRECEDENCE: Tuple[bytes, ...] = (
    PAYLOAD_FIELD,
    b"data",
    b"payload",
    b"metadata",
)

#: Retained for callers that imported the previous name. Same contract, minus
#: the canonical ``value`` field which is tried first regardless.
FALLBACK_PAYLOAD_FIELDS: Tuple[bytes, ...] = PAYLOAD_FIELD_PRECEDENCE[1:]


#: Upper bound on a timestamp this will hand back: 9999-12-31T23:59:59.999Z.
#:
#: A stream ID's millisecond half is whatever the producer put there — Redis
#: accepts an explicit ID, so it is not necessarily a clock reading — and an
#: arbitrarily large integer is a valid one. Everything downstream treats the
#: result as a Unix epoch, where a year-out-of-range value is not a bad
#: measurement but an argument no date library accepts. Bounding it here keeps
#: that a missing datapoint rather than an exception in whichever caller
#: converted it first.
_MAX_PLAUSIBLE_EPOCH_MS = 253_402_300_799_999


def message_id_to_epoch_ms(message_id: Any) -> Optional[int]:
    """Extract the millisecond timestamp encoded in a Redis stream entry ID.

    Redis stream IDs are ``<ms>-<seq>``, so the publish time is available
    without the producer having to stamp it. This is the Redis analogue of the
    Kafka record timestamp and feeds the same end-to-end latency metrics.

    Returns ``None`` for anything outside a representable date as well as for an
    unparsable ID, because the two mean the same thing to a caller: this entry
    has no usable publish time. A latency stamp is not worth a raised exception
    on a path that has already acked the entry it belongs to.
    """
    if message_id is None:
        return None
    try:
        raw = message_id.decode("utf-8") if isinstance(message_id, (bytes, bytearray)) else str(message_id)
        ms = int(raw.split("-", 1)[0])
        return ms if 0 < ms <= _MAX_PLAUSIBLE_EPOCH_MS else None
    except (ValueError, AttributeError):
        return None


def extract_envelope(fields: Dict[Any, Any]) -> Tuple[Optional[bytes], Optional[bytes], Dict[str, Any]]:
    """Split a stream entry's field map into ``(payload, key, headers)``.

    The body is located by walking :data:`PAYLOAD_FIELD_PRECEDENCE` in order, so
    an entry carrying several candidate fields always decodes the same one. See
    that constant for why the order is what it is.

    Tolerates both ``bytes`` and ``str`` field names so the helper works
    whether or not the caller enabled ``decode_responses``.
    """
    if not fields:
        return None, None, {}

    normalized: Dict[bytes, Any] = {}
    for name, value in fields.items():
        if isinstance(name, str):
            name = name.encode("utf-8")
        normalized[name] = value

    payload = None
    for candidate in PAYLOAD_FIELD_PRECEDENCE:
        if normalized.get(candidate) is not None:
            payload = normalized[candidate]
            break

    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    key = normalized.get(KEY_FIELD)
    if isinstance(key, str):
        key = key.encode("utf-8")

    headers: Dict[str, Any] = {}
    raw_headers = normalized.get(HEADERS_FIELD)
    if raw_headers:
        try:
            if isinstance(raw_headers, (bytes, bytearray)):
                raw_headers = raw_headers.decode("utf-8")
            decoded = json.loads(raw_headers)
            if isinstance(decoded, dict):
                headers = decoded
        except (ValueError, UnicodeDecodeError):
            logger.debug("Ignoring non-JSON headers field on Redis stream entry")

    return payload, key, headers
