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

"""Tell "this service is not part of this deployment" from "it is, and it is unwell".

Every HTTP backend the agent calls is now one origin plus a path — the VSS
gateway — instead of a Docker DNS name per service. That is what lets the same
agent image run colocated or remote, but it costs a signal the transport used
to give away for free:

* Addressed directly, a service a profile does not deploy has no listener, so
  the connection is REFUSED. Only a service that is actually running can
  answer ``503`` at all, so ``ConnectError`` meant absent and ``503`` meant
  present-and-failing, with no ambiguity.
* Addressed through the gateway, both are ``503`` from the one origin. The
  connection always succeeds — to the gateway.

That is why ``_register_with_rtvi_cv``'s tolerance stopped working when
``RTVI_CV_ENDPOINT`` moved behind the edge: rt-cv is optional infrastructure,
absence was detected as ``httpx.ConnectError``, and through HAProxy an absent
backend answers 503 instead. Uploads hard-failed on a profile that simply does
not deploy rt-cv.

Widening the tolerated set to "any 503" would have been worse than the bug. It
would silently skip a service that IS deployed and is overloaded, restarting or
broken, turning a real outage into a no-op that reports success. So the
distinction is restored at the only place that still holds it — the gateway —
and read back here.

``deploy/docker/services/infra/haproxy/haproxy.cfg.template`` answers a route
whose backend has no usable server (``nbsrv() eq 0``) with a 503 it synthesises
itself, carrying :data:`GATEWAY_UNAVAILABLE_HEADER`. The service never sees the
request, so it cannot have formed that response. A 503 arriving WITHOUT the
header therefore came from a live backend and still means
present-and-failing. The gateway strips the header from every backend-origin
response, so a service cannot forge it.

The header's *presence* is the contract. Its value names the route, for logs.
Callers must not switch on the value, or every new mount would need a matching
edit here.

Same shape as the existing ``x-vss-gateway-deny: unknown-host``, and for the
same reason: a status code alone cannot say why the gateway refused, so the
gateway says it.
"""

from __future__ import annotations

from typing import Any

#: Marker the VSS gateway sets on a 503 it synthesised because the route's
#: backend had no usable server. Must match the header name in
#: ``deploy/docker/services/infra/haproxy/haproxy.cfg.template``;
#: ``.github/scripts/check_gateway_optional_backends.py`` fails CI if the two
#: drift apart.
GATEWAY_UNAVAILABLE_HEADER = "x-vss-gateway-unavailable"


def gateway_reports_service_absent(response: Any) -> bool:
    """True when ``response`` is the gateway saying the service is not deployed.

    Only ever true for a 503 that carries :data:`GATEWAY_UNAVAILABLE_HEADER`,
    which only the gateway can set and only when it routed the request nowhere.
    An unmarked 503 is a real answer from a live service and returns False, so a
    caller that tolerates absence still fails on a genuine outage.

    ``Any`` rather than ``httpx.Response``: the alert service and the CLI use
    different clients, and this only needs ``status_code`` and a mapping-like
    ``headers``.
    """
    if getattr(response, "status_code", None) != 503:
        return False
    headers = getattr(response, "headers", None)
    if headers is None:
        return False
    try:
        return GATEWAY_UNAVAILABLE_HEADER in headers
    except TypeError:  # pragma: no cover — a headers object without membership
        return False


def gateway_absent_service(response: Any) -> str:
    """The route name the gateway reported absent, for a log line. ``""`` if none.

    Diagnostic only — never branch on this. Use
    :func:`gateway_reports_service_absent`.
    """
    if not gateway_reports_service_absent(response):
        return ""
    return str(response.headers.get(GATEWAY_UNAVAILABLE_HEADER) or "")


__all__ = [
    "GATEWAY_UNAVAILABLE_HEADER",
    "gateway_absent_service",
    "gateway_reports_service_absent",
]
