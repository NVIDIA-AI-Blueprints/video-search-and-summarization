# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""HTTP client for the Unified Model Control API.

Five operations behind one base URL, all using the RTVI ``key``/``value``
control envelope::

    POST /api/v1/model/load      {"key": "model",   "value": {"name", "version"}}
    POST /api/v1/model/unload    {"key": "model",   "value": {"name", "version"}}
    GET  /api/v1/model/routing
    POST /api/v1/model/routing   {"key": "routing", "value": {"default": {...}}}
    GET  /api/v1/model/status?model=&version=

Every request body carries only the contract's PORTABLE CORE — the fields every
implementation is required to accept. That is what lets one client talk to any
conforming backend: the spec makes an unsupported field a hard rejection rather
than something quietly ignored, so the safe set is the intersection, and the
intersection is what we send.

Stated precisely, because "nothing optional is ever emitted" is not quite what
the code does:

1. Nothing outside the portable core is emitted BY DEFAULT.
2. An optional is emitted only when a caller explicitly passes it. Today the
   only such knob is ``batch_size``, and no production caller sets it.
3. Profile-specific fields — ``gpus``, ``config_file``, ``engine_file``, all
   marked for one implementation only — are not added at all, not even as
   opt-in parameters. This client is deliberately profile-blind and the contract
   carries no runtime capability discovery, so no caller is in a position from
   which passing one is knowably safe.
4. ONE profile-specific field is emitted anyway: ``routes``, behind
   ``settings.unified_stream_routes``. The exception is the setting, not the
   field — a caller cannot know a backend supports it, but the deployment that
   configures the backend can, which is the one vantage point item 3 lacks.
   Off by default, so the portable core is still what ships.
"""

import logging

from src.adapters.activation import deterministic_build_failure
from src.adapters.unified.errors import translate
from src.clients.http import json_object, request_with_retries
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)

# Policy keys that also fit a ``StreamRoute`` (``additionalProperties: false``
# over ``{streams, model, shadow}``). A canary policy has no route
# representation, so it is sent as ``default`` alone -- which costs nothing:
# RTVI-CV, the only profile carrying ``routes``, has modes ``[single, shadow]``,
# so its canary rung is refused before the mirror could matter.
_ROUTABLE_POLICY_KEYS = frozenset({"model", "shadow"})

_JSON_HEADERS = {"Content-Type": "application/json"}


class UnifiedModelClient:
    """Client for a unified-API inference service."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.unified_url).rstrip("/")

    # ------------------------------------------------------------------ load
    def load(
        self,
        name: str,
        version: str,
        *,
        batch_size: int | None = None,
        extra_load_params: dict | None = None,
        timeout: float | None = None,
    ) -> tuple[int, dict]:
        """Load a version, returning ``(status_code, body)``.

        The STATUS CODE is part of the answer, not transport noise: the contract
        makes load completion self-describing on the wire — "a synchronous
        implementation returns 200; an asynchronous implementation returns 202,
        clients then observe progress through GET /api/v1/model/status". Handing
        it back is what lets the caller wait exactly when waiting is required,
        without anyone having to declare in configuration which kind of backend
        this is.

        The load carries its own long timeout and NO retries. A synchronous load
        blocks for the whole cold engine build (minutes), so the ordinary 30s
        request timeout would expire mid-build; and a retry of a request that is
        still being served looks identical to one that was lost, so replaying it
        starts a second engine build inside the backoff window rather than
        recovering anything. The serving stack documents that concurrent reload
        as a use-after-free race against in-flight inference, so the cost of
        replaying is far worse than the cost of one lost recoverable 5xx.
        """
        value: dict = {"name": name, "version": version}
        if batch_size is not None:
            value["batch_size"] = batch_size
        # Relayed VERBATIM from the builder, never interpreted. This client
        # still holds no capability table and never learns what these keys
        # mean -- which is the difference between relaying and profiling.
        #
        # Verbatim rather than filtered through an allow-list: the load value
        # is additionalProperties:false, so a key the backend does not define
        # is a loud 400 from the authority that owns the field set, whereas
        # dropping a key we did not recognise would produce a load that
        # silently binds the wrong artifact. ``name``/``version`` cannot be
        # overridden: ExtraLoadParams forbids them by schema, and this rejects
        # them anyway. A builder that ignored the schema would otherwise retarget
        # the request at a DIFFERENT {name, version} while the activation flow
        # kept polling -- and reporting on -- the original tuple, so the promotion
        # would report success against a version it never loaded.
        reserved = {"name", "version"} & set(extra_load_params or {})
        if reserved:
            raise ModelManagerError(
                code="load_failed",
                message=(
                    f"the model builder returned reserved extra_load_params "
                    f"{sorted(reserved)} for {name}@{version}; a builder may not "
                    "retarget the load"
                ),
                retryable=False,
                http_status=502,
            )
        value.update(extra_load_params or {})
        response = request_with_retries(
            "POST",
            f"{self.base_url}/api/v1/model/load",
            timeout=settings.unified_load_timeout if timeout is None else timeout,
            max_retries=0,
            backoff_base=settings.request_backoff_base,
            unreachable_code="load_failed",
            retry_on_timeout=False,
            headers=_JSON_HEADERS,
            json={"key": "model", "value": value},
        )
        if response.status_code // 100 != 2:
            error = translate("load", response, unreachable_code="load_failed")
            # The unified load is synchronous and has no build-status channel, so
            # the backend funnels EVERY build outcome through one blanket
            # 500 MODEL_OPERATION_FAILED. Left as a plain 5xx, a deterministic
            # failure — a version directory the serving process cannot write to —
            # reads as transient, and the caller re-drives a GPU build that can
            # never succeed. The legacy adapter classifies the same condition
            # terminally; this keeps the two answers identical.
            if response.status_code >= 500:
                remedy = deterministic_build_failure(error.message)
                if remedy:
                    raise ModelManagerError(
                        code="load_failed",
                        message=f"{error.message}. {remedy}",
                        retryable=False,
                        http_status=502,
                    )
            raise error
        return response.status_code, json_object(response)

    # ---------------------------------------------------------------- unload
    def unload(self, name: str, version: str) -> tuple[int, dict]:
        """Unload a version, returning ``(status_code, body)`` as ``load`` does.

        The code is not decoration. A ``200`` and a ``202`` are both success, but
        only the first is a statement that the eviction has already run, and a
        caller about to delete the directory needs to tell them apart.
        """
        response = self._request(
            "POST",
            "/api/v1/model/unload",
            json={"key": "model", "value": {"name": name, "version": version}},
        )
        if response.status_code // 100 != 2:
            raise translate("unload", response)
        return response.status_code, json_object(response)

    # --------------------------------------------------------------- routing
    def get_routing(self) -> dict | None:
        """Return the ``routing`` object, or ``None`` if the body carried none.

        The two are NOT the same answer and must not collapse into one value.
        ``{}`` is a backend saying "nothing is routed" -- a fact, and one that
        lets cleanup proceed. ``None`` is "this response contained no routing
        view at all", which includes a 200 carrying an error page, and has to
        fail closed.

        Collapsing them is not hypothetical: a spec-conformant backend with
        nothing routed and none of the RTVI-CV extensions returns exactly
        ``{"routing": {}}``, and reading that as "no view" leaves cleanup
        answering 503 in perpetuity.
        """
        response = self._request("GET", "/api/v1/model/routing")
        if response.status_code // 100 != 2:
            raise translate("routing read", response)
        routing = json_object(response).get("routing")
        return routing if isinstance(routing, dict) else None

    def put_routing(self, default_policy: dict, *, if_revision: int | None = None) -> dict:
        """Apply a default routing policy.

        ``if_revision`` is the compare-and-set guard. No caller supplies one
        today; a backend that does not implement it answers
        ``400 FIELD_UNSUPPORTED``, which is the contract's own way of saying so.

        Under ``unified_stream_routes`` a policy that also validates as a
        ``StreamRoute`` is mirrored into a ``routes`` arm naming ``all``. Every
        rung needs it, including the 409 retry, and all of them come through
        here; see ``settings.unified_stream_routes`` for why the backend
        requires it.

        It must ride the SAME request, never a second one: ``ModelManager.apply``
        skips a write whose control already reads as reflected, and that
        predicate sees only ``default`` -- so a follow-up POST carrying the
        routes could be skipped forever, stranding the streams on the old
        version while the promotion reports success.
        """
        value: dict = {"default": default_policy}
        if settings.unified_stream_routes and default_policy.keys() <= _ROUTABLE_POLICY_KEYS:
            value["routes"] = [{"streams": "all", **default_policy}]
        if if_revision is not None:
            value["if_revision"] = if_revision
        # The routing policy is the one request body whose exact SHAPE is the
        # behaviour -- which roles it names decides what a merging backend keeps
        # and what it clears, and a role named as ``null`` is a clear rather than
        # an omission. Nothing logged it, so verifying a routing change meant
        # inferring the wire from side effects: the absence of a retry line plus
        # a calibrated revision delta. Cheap to log, and it is the difference
        # between reading what was sent and reconstructing it.
        #
        # DEBUG rather than INFO because it is per-write detail, and the policy
        # carries only model names, versions and a weight -- nothing secret.
        logger.debug("routing write: %s", value)
        response = self._request(
            "POST",
            "/api/v1/model/routing",
            json={"key": "routing", "value": value},
            # A compare-and-set write must never be replayed -- not after a
            # timeout and not after a 5xx. The first attempt may well have landed
            # and bumped the revision, in which case the retry comes back
            # REVISION_CONFLICT and we report a failure for a write that actually
            # succeeded. Suppressing only the timeout retry is not enough: the
            # transport retries a 5xx too, and a 5xx can also follow an applied
            # write. Re-driving a CAS write is the CALLER's job, because only the
            # caller can re-read the revision first — which is the whole point of
            # the guard.
            max_retries=0 if if_revision is not None else settings.request_max_retries,
        )
        if response.status_code // 100 != 2:
            raise translate("routing update", response)
        return json_object(response)

    # ---------------------------------------------------------------- status
    def get_status(self, name: str | None = None, version: str | None = None) -> dict:
        """Return the ``model-status`` object from ``GET /api/v1/model/status``.

        Note the hyphen in the response key -- it is ``model-status``, not
        ``model_status``, and the spec is explicit about it.

        The filter goes in ``params`` rather than as a bare keyword, because
        ``requests`` owns the name ``stream`` and would read a ``stream=`` kwarg
        as "stream the response" instead of "filter by stream". The unified
        status endpoint happens to define a ``stream`` query parameter, so the
        collision is real rather than theoretical.
        """
        params: dict[str, str] = {}
        if name is not None:
            params["model"] = name
        if version is not None:
            params["version"] = version
        response = self._request("GET", "/api/v1/model/status", params=params or None)
        if response.status_code // 100 != 2:
            raise translate("status read", response)
        status = json_object(response).get("model-status")
        return status if isinstance(status, dict) else {}

    # --------------------------------------------------------------- helpers
    def _request(self, method: str, path: str, **kwargs):
        """Issue a control-plane request with the shared retry/timeout policy.

        ``max_retries`` may be overridden per call — a compare-and-set routing
        write must not be replayed by the transport at all.
        """
        return request_with_retries(
            method,
            f"{self.base_url}{path}",
            timeout=settings.request_timeout,
            max_retries=kwargs.pop("max_retries", settings.request_max_retries),
            backoff_base=settings.request_backoff_base,
            headers=_JSON_HEADERS,
            **kwargs,
        )
