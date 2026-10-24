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

"""HTTP client for the AOI control-plane model API (``/v1/models/*``).

Covers the asynchronous load handshake (``POST /v1/models/load?wait=0`` plus
``GET /v1/models/build-status``), the per-version ``GET /v1/models/versions``
inventory, ``POST /v1/models/unload``, ``GET /v1/models`` and ``GET /v1/ready``.

Three properties of that API shape everything in this module:

* **A 2xx from the load POST is not a loaded model.** Under ``wait=0`` the call
  returns ``202`` the instant a build is accepted, and the ONNX export plus
  ``trtexec`` engine build then run for minutes. The only source of truth for
  the outcome is ``build-status``.
* **``load``/``unload`` read typed body fields.** Both a
  ``Content-Type: application/json`` header and an explicit ``version`` are
  mandatory: with either missing, ``version`` falls back to *every staged
  version*, so a call meant for one version rebuilds or evicts the whole model.
* **A 404 from ``build-status`` is narrow.** It means one specific thing — an
  unknown ``build_id``. A ``{model_name, version}`` target with nothing in
  flight answers ``200`` with ``phase=idle``, which is not an error and not a
  failed load.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from src.clients.http import (
    access_denied,
    conflict,
    error_code_of,
    json_object,
    list_field,
    request_with_retries,
)
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)

# Pinned explicitly rather than relying on ``requests`` deriving it from the
# ``json=`` kwarg: load/unload silently fall back to "all staged versions" when
# the body is not recognised as JSON, so the header that prevents that must be
# visible at the call site instead of being an implementation detail of the
# transport that a later refactor could drop.
JSON_HEADERS = {"Content-Type": "application/json"}

# ``unknown`` is not a phase the service reports. It is this client's marker for
# "the outcome of the load could not be established" — an ambiguous timeout on
# the POST, a 404 for a build_id the service no longer knows, or a phase string
# outside the documented set. All three recover the same way: re-attach by
# ``{model_name, version}`` and confirm against the repository index.
Phase = Literal["building", "succeeded", "failed", "idle", "unknown"]


@dataclass(frozen=True)
class BuildHandle:
    """What a load POST established about the build it may have started.

    ``succeeded`` is terminal and needs no poll: a synchronous load finished, an
    idempotency key replayed a cached success, or the version was already READY
    and the service skipped the reload. ``building`` must be polled, and carries
    a ``build_id`` only when the POST returned one — a re-attach via
    ``409 idempotency_in_progress`` proves a build is running without naming it,
    so it is followed by ``{model_name, version}`` instead. ``unknown`` means the
    POST's fate is undetermined.
    """

    phase: Phase
    build_id: str | None = None


@dataclass(frozen=True)
class BuildStatus:
    """One ``GET /v1/models/build-status`` reading."""

    phase: Phase
    build_id: str | None = None
    error: str | None = None
    http_status: int | None = None


class AOIInferenceClient:
    """Client for ``/v1/models/*`` on the AOI control plane."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.aoi_inference_url or settings.aoi_control_url).rstrip(
            "/"
        )
        self.timeout = timeout if timeout is not None else settings.request_timeout
        self.max_retries = settings.request_max_retries
        self.backoff_base = settings.request_backoff_base

    def load_async(
        self, name: str, version: str, *, idempotency_key: str | None = None
    ) -> BuildHandle:
        """Start a load and report what is now in flight, without waiting for it.

        The deployed inference resolves the artifact from its own shared repo at
        ``{repo}/{model_name}/{version}/`` (``model.pth``/``model.onnx``/
        ``model.plan``), so the body carries only the flat identifiers and no
        ``model_path``.

        ``idempotency_key`` is what makes this call safe to repeat. Against a key
        whose build is still running the service answers
        ``409 idempotency_in_progress`` and starts nothing new; against one that
        succeeded it replays the original result; against one that *failed* it
        releases the reservation so the retry rebuilds. Failures are therefore
        never cached, and a success is never re-converted.
        """
        body: dict[str, object] = {"model_name": name, "version": version}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        # Synchronous mode blocks for the entire conversion, so it borrows the
        # build budget; the async POST returns as soon as the build is queued and
        # needs no more than the ordinary request timeout.
        params = {"wait": 0} if settings.load_async else None
        timeout = self.timeout if settings.load_async else settings.build_poll_timeout

        try:
            response = request_with_retries(
                "POST",
                f"{self.base_url}/v1/models/load",
                timeout=timeout,
                max_retries=self.max_retries,
                backoff_base=self.backoff_base,
                unreachable_code="load_failed",
                # A timed-out load POST must not be replayed here. The request may
                # well have been accepted, and a backoff-window retry would ask a
                # backend that is already converting to convert again. The
                # ambiguity is resolved by asking build-status what is running,
                # which costs one cheap read instead of an extra engine build.
                retry_on_timeout=False,
                headers=JSON_HEADERS,
                json=body,
                params=params,
            )
        except ModelManagerError as exc:
            if not (exc.details or {}).get("timed_out"):
                raise
            logger.warning(
                "load POST for %s@%s timed out; will re-attach by name rather than re-POST",
                name,
                version,
            )
            return BuildHandle(phase="unknown")

        if response.status_code == 202:
            build_id = _string_field(json_object(response), "build_id")
            logger.info("load accepted for %s@%s (build_id=%s)", name, version, build_id)
            return BuildHandle(phase="building", build_id=build_id)
        if response.status_code // 100 == 2:
            # Either the synchronous path ran to completion, or an idempotency key
            # replayed a cached success, or the version was already READY.
            logger.info("load completed inline for %s@%s", name, version)
            return BuildHandle(phase="succeeded")

        error = error_code_of(response)
        if response.status_code == 409 and error == "idempotency_in_progress":
            # Not a failure: the key is held by a build that is still running, so
            # this call re-attached to it and no second conversion was started.
            # Treating it as an error would fail a promotion whose build is
            # progressing perfectly well.
            logger.info(
                "load re-attached to the in-flight build for %s@%s (idempotency key held)",
                name,
                version,
            )
            return BuildHandle(phase="building")
        if response.status_code == 409 and error == "load_model_already_loaded":
            # Legacy tolerance. The current contract skips the reload of an
            # already-READY version server-side and documents no such code, but
            # older AOI builds still return it, and it describes a version that is
            # already in the desired state — the opposite of a failure.
            logger.info("load idempotent: %s@%s already loaded", name, version)
            return BuildHandle(phase="succeeded")
        if response.status_code in (401, 403):
            raise access_denied(
                f"load of {name}@{version}", response.status_code, error or response.text
            )
        if response.status_code == 409:
            # Any other 409 here is ``idempotency_conflict``: the key was already
            # used with *different* parameters. That was falling through to the
            # generic branch and being reported as an ``internal`` 502 — a caller
            # would read a service fault where the truth is a key collision it owns
            # and must fix. Terminal by definition: the same key with the same
            # mismatched parameters will be refused every time.
            raise conflict(f"load of {name}@{version}", error or response.text, retryable=False)
        if response.status_code == 404:
            raise ModelManagerError(
                code="not_found",
                message=(
                    f"AOI Inference has no staged {name}@{version} to load: "
                    f"{error or response.text}"
                ),
                retryable=False,
                http_status=404,
            )
        if response.status_code >= 500:
            raise ModelManagerError(
                code="load_failed",
                message=f"AOI Inference failed to load {name}@{version}: {error or response.text}",
                retryable=True,
                http_status=502,
            )
        if response.status_code in (400, 422):
            raise ModelManagerError(
                code="bad_request",
                message=f"AOI Inference rejected load of {name}@{version}: {error or response.text}",
                retryable=False,
                http_status=400,
            )
        raise ModelManagerError(
            code="internal",
            message=f"Unexpected AOI Inference load response {response.status_code}: {error or response.text}",
            retryable=False,
            http_status=502,
        )

    def build_status(
        self,
        *,
        build_id: str | None = None,
        name: str | None = None,
        version: str | None = None,
    ) -> BuildStatus:
        """Read the phase of a build, by ``build_id`` or by ``{name, version}``.

        The ``{name, version}`` form is the one that survives a Model Manager
        restart: the service keeps an in-memory registry of builds per target, so
        a poller that lost its ``build_id`` re-attaches without re-POSTing
        anything. That registry does not survive a *control-service* restart,
        which is what the durable idempotency key covers instead.

        A 404 is reported as ``unknown`` rather than ``idle``. The distinction is
        load-bearing: ``idle`` is a 200 that authoritatively says nothing is in
        flight for a target, whereas a 404 only says this ``build_id`` is no
        longer known and says nothing at all about the model.
        """
        params = {"build_id": build_id} if build_id else {"model_name": name, "version": version}
        response = request_with_retries(
            "GET",
            f"{self.base_url}/v1/models/build-status",
            timeout=settings.build_status_request_timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            unreachable_code="load_failed",
            params=params,
        )
        if response.status_code == 404:
            return BuildStatus(phase="unknown", build_id=build_id)
        if response.status_code // 100 != 2:
            raise ModelManagerError(
                code="load_failed",
                message=(
                    f"AOI Inference build-status lookup failed ({response.status_code}): "
                    f"{error_code_of(response) or response.text}"
                ),
                retryable=True,
                http_status=502,
            )

        payload = json_object(response)
        phase = payload.get("phase")
        http_status = payload.get("http_status")
        return BuildStatus(
            phase=phase if phase in ("building", "succeeded", "failed", "idle") else "unknown",
            build_id=_string_field(payload, "build_id") or build_id,
            error=_string_field(payload, "error"),
            http_status=http_status if isinstance(http_status, int) else None,
        )

    def list_versions(self, name: str) -> dict:
        """Return the raw ``GET /v1/models/versions`` inventory for ``name``.

        This is the service's answer to "which version directories may the Model
        Manager remove?", and the only authority the Model Manager acts on when
        reclaiming disk. It is returned unnormalized; the adapter maps it onto the
        client-agnostic inventory shape.
        """
        response = request_with_retries(
            "GET",
            f"{self.base_url}/v1/models/versions",
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            params={"model_name": name},
        )
        if response.status_code // 100 != 2:
            raise ModelManagerError(
                code="internal",
                message=f"AOI Inference list_versions failed ({response.status_code})",
                retryable=True,
                http_status=502,
            )
        return json_object(response)

    def unload(self, name: str, version: str, *, idempotency_key: str | None = None) -> None:
        """Evict a version from serving memory (idempotent on not-loaded).

        Carries the same flat ``{model_name, version}`` body as ``load`` and for
        the same reason: this endpoint reads typed fields, and a ``version`` it
        cannot see defaults to every staged version — turning the eviction of one
        retired version into a mass eviction of the whole model, live versions
        included. Files are untouched either way; deleting them is a separate,
        inventory-gated step.
        """
        body: dict[str, object] = {"model_name": name, "version": version}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        response = request_with_retries(
            "POST",
            f"{self.base_url}/v1/models/unload",
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            headers=JSON_HEADERS,
            json=body,
        )
        if response.status_code // 100 == 2:
            logger.info("unloaded %s@%s", name, version)
            return

        error = error_code_of(response)
        if response.status_code == 404 and error == "unload_model_not_loaded":
            # Legacy tolerance, kept for the same reason as the load counterpart:
            # the current contract documents no such code, but a version that is
            # already absent from memory is the state this call was asking for.
            logger.info("unload idempotent: %s@%s not loaded", name, version)
            return
        if response.status_code in (401, 403):
            raise access_denied(
                f"unload of {name}@{version}", response.status_code, error or response.text
            )
        if response.status_code == 409:
            # ``model_version_serving``: the version is part of the active rollout
            # and the server will not strand it. ``cleanup`` refuses the same thing
            # locally as ``conflict``/409, and its local check cannot replace this
            # one — routing can change in the window between them, which is exactly
            # when the server's refusal is the one that fires. Reporting it as a
            # 400 made the caller's reading of an identical situation depend on
            # which side spotted it first. The server's message names the action
            # that clears it (switch routing away first), so it is passed through.
            #
            # Non-retryable, matching the local guard: repeating the same unload
            # cannot succeed until something moves the routing. The exception is an
            # idempotent unload already in flight, which clears on its own.
            raise conflict(
                f"unload of {name}@{version}",
                error or response.text,
                retryable=error == "idempotency_in_progress",
            )
        if response.status_code >= 500:
            raise ModelManagerError(
                code="internal",
                message=f"AOI Inference failed to unload {name}@{version}: {error or response.text}",
                retryable=True,
                http_status=502,
            )
        raise ModelManagerError(
            code="bad_request",
            message=f"AOI Inference rejected unload of {name}@{version}: {error or response.text}",
            retryable=False,
            http_status=400,
        )

    def list_models(self) -> list[dict]:
        """Return the ``models`` array (LoadedModel dicts) from GET /v1/models.

        The return type is a guarantee, not a hope: a ``models`` key present with
        ``null`` or a non-array value raises rather than being handed on. Callers
        iterate this to decide whether a version is still resident, and every one
        of them already treats a raised ``ModelManagerError`` as "the inventory is
        unknown" — which is the correct reading of a malformed envelope, and the
        one thing a ``None`` leaking out of here could not produce.

        An *absent* ``models`` key is not malformed; it reads as an empty list, on
        the same basis as any other omitted array field.
        """
        response = request_with_retries(
            "GET",
            f"{self.base_url}/v1/models",
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
        )
        if response.status_code // 100 != 2:
            raise ModelManagerError(
                code="internal",
                message=f"AOI Inference list_models failed ({response.status_code})",
                retryable=True,
                http_status=502,
            )
        models = list_field(json_object(response), "models")
        if models is None:
            raise ModelManagerError(
                code="internal",
                message=(
                    "AOI Inference /v1/models returned a 'models' field that is not an array; "
                    "the loaded-model inventory cannot be read"
                ),
                retryable=True,
                http_status=502,
            )
        return models

    def ready(self) -> bool:
        """Return True when GET /v1/ready reports ready (HTTP 200)."""
        try:
            response = request_with_retries(
                "GET",
                f"{self.base_url}/v1/ready",
                timeout=self.timeout,
                max_retries=self.max_retries,
                backoff_base=self.backoff_base,
            )
        except ModelManagerError:
            return False
        return response.status_code == 200


def _string_field(payload: dict, key: str) -> str | None:
    """Return ``payload[key]`` when it is a non-empty string, else None."""
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
