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

"""The optional pre-load engine build.

The ONNX -> TensorRT build used to happen inside ``POST /api/v1/model/load``. A
multi-minute GPU build on the serving process drops frames on live traffic, so
it moved to a separate Model Builder service::

    POST /api/v1/model/build                    {"name": ..., "version": ...}
    GET  /api/v1/model/build/{name}/{version}

Not every deployment runs one -- the AOI backend builds inside its own load and
needs nothing -- so the step is optional. ``settings.builder_url`` is the entire
switch: set, the build runs; unset, this module makes no HTTP call at all and
returns ``{}``. Nothing here asks what is on the other end of the wire, which is
the same principle the adapter already applies to load completion and routing
stages.

**Why the whole retry design is one sentence of the spec.** ``POST .../build`` is
keyed on ``{name, version}`` and a repeat answers *"200 The immutable build
already exists or is in progress"*. So a replay ATTACHES to the running build
rather than starting a second one -- the exact opposite of ``client.load``,
which runs ``max_retries=0`` because replaying a load that is still being served
starts a second engine build. That property is what lets this module lean on the
shared transport retries and stay small.

The contract also constrains the identifiers it will accept::

    name     ^[A-Za-z0-9_-]+$
    version  ^[1-9][0-9]*$

which is narrower than this service's own ``PathComponent``. DFW's minted
integer versions satisfy it today, so nothing is broken -- but that is a real
coupling to DFW's version counter, and a ``0`` or a dotted name would surface as
a ``422`` from the builder rather than as a validation error here. Terminal
either way: we composed that request, so replaying it sends the same rejected
shape.

``BuildError.code`` is an open string with no enum, so it is **never branched
on** -- a code table here would be invented API surface. It is echoed into the
operator's message and nothing else. Retry decisions key only on closed sets:
the HTTP status, and ``state``, which the spec declares as a ``const`` per
variant.
"""

import logging
from time import monotonic, sleep

from src.adapters.unified.errors import translate
from src.clients.http import json_object, request_with_retries
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)

_JSON_HEADERS = {"Content-Type": "application/json"}

# ``ReadyBuildStatus`` and ``FailedBuildStatus`` declare these as ``const``, so
# they are a closed set and safe to branch on -- unlike ``BuildError.code``.
_READY = "ready"
_FAILED = "error"


def build_extra_load_params(name: str, version: str, deadline: float) -> dict:
    """Build ``name@version`` and return the load parameters it produced.

    Returns ``{}`` -- and makes NO HTTP call -- when no builder is configured,
    which is what keeps every existing deployment byte-identical. Raises
    ``ModelManagerError`` on any build failure, because a configured builder
    that cannot produce an engine must fail the promotion rather than fall
    through to a load that would bind the previous version's artifacts.

    ``deadline`` is a ``monotonic()`` value, passed in rather than computed here
    so the build and the load that follows it share ONE budget. Opening a fresh
    one here would let a slow build and a slow warm-up stack past what the
    caller is prepared to wait for.
    """
    base = (settings.builder_url or "").rstrip("/")
    if not base:
        return {}

    status = _submit(base, name, version)
    while True:
        state = status.get("state") if status is not None else None
        if state == _READY:
            return _ready_params(name, version, status)
        if state == _FAILED:
            raise _failed(name, version, status)
        # Any other state -- queued, building, or something this client has
        # never heard of -- is waited out. Treating an unrecognised state as a
        # failure would turn a spec addition into an outage; the deadline is
        # what bounds it.
        if monotonic() >= deadline:
            # Distinguish "running but unfinished" from "the builder stopped
            # reporting it", so nobody goes looking for a slow build that the
            # builder no longer knows about.
            detail = (
                f"last state {state!r}"
                if state is not None
                else "the builder is no longer reporting a status for it"
            )
            raise ModelManagerError(
                code="readiness_timeout",
                message=(
                    f"engine build for {name}@{version} did not finish in time "
                    f"({detail}). The build survives this timeout: a re-driven "
                    "pull re-attaches to it rather than starting a second one."
                ),
                retryable=True,
                http_status=504,
            )
        sleep(settings.build_poll_interval)
        status = _read_status(base, name, version)


def build_settled(name: str, version: str) -> bool:
    """True only when no engine build can still be in flight for this version.

    Consulted before a version directory is deleted, so every ambiguous answer
    is False. That is the opposite of ``build_extra_load_params``, which waits an
    unrecognised state out so a spec addition cannot become an outage; here an
    unrecognised state must not become an ``rmtree`` of a build's inputs.

    True when no builder is configured -- presence of the URL is the entire
    switch, so a deployment that builds nothing has nothing in flight.

    A 404 is False, and that is the whole reason this wrapper exists.
    ``_read_status`` returns None for it, and its docstring is explicit that the
    GET's 404 means "no status exists YET" rather than "no build": the contract
    does not order the status write against the 202. A concurrent re-pull that
    has just staged the directory and POSTed its build reads exactly that 404,
    and calling it settled deletes what the build is about to consume.
    """
    base = settings.builder_url
    if not base:
        return True
    try:
        status = _read_status(base.rstrip("/"), name, version)
    except ModelManagerError as exc:
        logger.warning(
            "build status for %s@%s is unreadable (%s); treating the build as in flight",
            name,
            version,
            exc.code,
        )
        return False
    if status is None:
        logger.info(
            "build status for %s@%s does not exist yet; treating the build as in flight",
            name,
            version,
        )
        return False
    state = status.get("state")
    settled = isinstance(state, str) and state.lower() in (_READY, _FAILED)
    if not settled:
        logger.info("build for %s@%s reports state %r, which is not terminal", name, version, state)
    return settled


def _submit(base: str, name: str, version: str) -> dict:
    """POST the build and return the status body it answers with.

    Both 202 and 200 carry a full status, so a build that is already ``ready``
    needs no poll at all -- the common case on a re-driven pull.
    """
    response = request_with_retries(
        "POST",
        f"{base}/api/v1/model/build",
        timeout=settings.request_timeout,
        max_retries=settings.request_max_retries,
        backoff_base=settings.request_backoff_base,
        unreachable_code="load_failed",
        headers=_JSON_HEADERS,
        json={"name": name, "version": version},
    )
    if response.status_code // 100 != 2:
        raise translate("model build", response, unreachable_code="load_failed")
    return json_object(response)


def _read_status(base: str, name: str, version: str) -> dict | None:
    """Read the current build status, or ``None`` when it is not observable yet.

    The contract gives the two 404s DIFFERENT meanings, and they must not share
    a classification::

        POST .../build           404 "The staged name/version directory does not exist."
        GET  .../build/{n}/{v}   404 "No build status exists for this name/version."

    The POST's is terminal -- we created that directory seconds earlier, so the
    builder and this service disagree about the model repo root, and no retry
    reconciles two mounts. The GET's is not: the contract does not order the
    status write against the 202, so a poll can legitimately arrive before the
    status exists and the very next one returns ``ready``. Failing there would
    let a status read that has not caught up destroy a healthy build -- and it
    would contradict this module's own rule that an unrecognised state is waited
    out so a spec addition cannot become an outage.
    """
    response = request_with_retries(
        "GET",
        f"{base}/api/v1/model/build/{name}/{version}",
        timeout=settings.build_status_request_timeout,
        max_retries=settings.request_max_retries,
        backoff_base=settings.request_backoff_base,
        unreachable_code="load_failed",
        headers=_JSON_HEADERS,
    )
    if response.status_code == 404:
        return None
    if response.status_code // 100 != 2:
        raise translate("model build status", response, unreachable_code="load_failed")
    return json_object(response)


def _ready_params(name: str, version: str, status: dict) -> dict:
    """Return ``extra_load_params`` from a ready build, refusing a hollow one.

    ``ReadyBuildStatus`` makes ``extra_load_params`` REQUIRED, so its absence is
    a contract violation rather than "nothing to add". Loading anyway is the
    dangerous reading: on a backend that resolves artifacts by name, a load
    carrying no build output binds the INCUMBENT version's engine, reaches
    ``ready``, and serves the previous model under the new version number --
    with every downstream metric then computed between two identical models.
    """
    params = status.get("extra_load_params")
    if not isinstance(params, dict):
        raise ModelManagerError(
            code="load_failed",
            message=(
                f"the model builder reported {name}@{version} ready but returned no "
                f"extra_load_params ({params!r}); loading without them risks binding "
                "the previously built version's artifacts"
            ),
            retryable=False,
            http_status=502,
        )
    logger.info(
        "engine build ready for %s@%s (%s)",
        name,
        version,
        ", ".join(sorted(params)) or "no parameters",
    )
    return params


def _failed(name: str, version: str, status: dict) -> ModelManagerError:
    """Turn a failed build into a terminal error carrying the builder's words.

    Terminal, never rebuilt from here. ``FailedBuildStatus`` carries ``attempt``
    -- the builder models its OWN retry ladder and has already re-run -- and the
    spec gives no way to force a rebuild: a re-POST over a persisted failure
    answers "already exists". A client-side ladder would therefore either spin on
    a replayed verdict or double-retry underneath the builder's, at GPU-minutes
    per rung.
    """
    error = status.get("error") if isinstance(status.get("error"), dict) else {}
    code = error.get("code") or "unknown"
    message = error.get("message") or "no message"
    hint = error.get("hint")
    detail = f"{code}: {message}" + (f" ({hint})" if hint else "")
    return ModelManagerError(
        code="load_failed",
        message=f"engine build for {name}@{version} failed -- {detail}",
        retryable=False,
        http_status=502,
    )
