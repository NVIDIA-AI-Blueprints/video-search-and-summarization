# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Consistent-hash stream-routing header for RTVI-bound requests.

Multi-replica RTVI deployments front the workers with a proxy that
consistent-hashes the ``x-stream-id`` request header — HAProxy
``balance hdr(x-stream-id)`` + ``hash-type consistent``, nginx ingress
``upstream-hash-by`` (the helm reference), or Envoy ring hash (used by the
optional SDR coordinator, among others). The hash pins a stream's whole
lifecycle — add, generate, config, remove — to a single worker pod, but only
if every request for the stream carries the **same** header value. The
contract is proxy-agnostic; SDR is just one deployment of it.

The convention: ``x-stream-id`` carries the **VST stream UUID** — the
``sensorId`` VST returns from ``/sensor/add`` (identical to the stream key in
``/sensor/streams`` for live sensors) or the uploaded video's ``video_id``
for file assets. Service-minted resource ids (e.g. the id RTVI-embed echoes
back from ``/v1/streams/add``) are NOT routing keys: they address the
resource *inside* a pod, not the pod. Hashing them lands follow-up calls on a
pod that does not own the stream.

Every RTVI call site must build its headers through this helper so the
convention has one implementation and one place to change.
"""

from __future__ import annotations

STREAM_ROUTING_HEADER = "x-stream-id"


def stream_routing_headers(stream_id: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return request headers that pin this call to the stream's worker pod.

    ``stream_id`` must be the VST stream UUID (see module docstring) — the
    same value on the add and every subsequent call for that stream.
    """
    headers = {STREAM_ROUTING_HEADER: stream_id}
    if extra:
        headers.update(extra)
    return headers
