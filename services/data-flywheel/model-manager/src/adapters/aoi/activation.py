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
"""AOI activation: the load protocol layered on the shared staging machinery.

Staging itself -- download, verify, publish, delete -- is backend-agnostic and
lives in :mod:`src.adapters.activation`. What is AOI-specific, and all that is
left here, is how a staged version is made servable: an asynchronous load
followed by a build-status poll until the ONNX export and TensorRT engine build
reach a terminal phase.

Poll mode (placeholder) would publish into a Triton-watched repo via an atomic
rename/symlink and let the serving stack auto-load -- it raises
NotImplementedError.
"""

import logging
from time import monotonic, sleep

from src.adapters.activation import (
    ActivationMode,
    StagingActivation,
    _budget_exhausted,
    deterministic_build_failure,
)
from src.adapters.aoi.inference import AOIInferenceClient, BuildHandle, BuildStatus
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)


class ExplicitActivation(StagingActivation):
    """Explicit mode: shared staging + AOI Inference load/unload."""

    def __init__(self, inference: AOIInferenceClient | None = None):
        self.inference = inference or AOIInferenceClient()

    def unload(self, name: str, version: str) -> None:
        """Evict a version from serving memory, leaving its files on disk.

        The counterpart to ``delete`` and deliberately separate from it: eviction
        is reversible (the version can be loaded again from the same directory)
        whereas removal is not, so the two are never done as one step.
        """
        self.inference.unload(name, version)

    def _load_to_ready(self, name: str, version: str, idempotency_key: str) -> None:
        """Start the load and wait for its conversion to reach a terminal phase.

        This is the one place in the Model Manager where a multi-minute wait is
        correct. DFW gives ``POST /v1/models/pull`` a 900s budget precisely so the
        ONNX export and engine build happen here, before anything touches
        production traffic. It cannot be deferred to ``apply`` either: a routing
        PUT is rejected outright while its target is not READY, so there is no
        "route it now and let it warm up" state for a slow build to settle into.

        The budget is one deadline spanning the whole call, started here rather
        than once the polling begins. In synchronous mode the load POST itself
        blocks for the conversion and can consume the entire budget on its own; a
        poll that then started its own fresh budget would let a single ``pull``
        run for twice as long as configured and overshoot the caller's timeout.
        """
        deadline = monotonic() + settings.build_poll_timeout
        handle = self.inference.load_async(name, version, idempotency_key=idempotency_key)
        if handle.phase == "succeeded":
            logger.info("loaded %s@%s", name, version)
            return
        self._await_build(name, version, idempotency_key, handle, deadline)

    def _await_build(
        self,
        name: str,
        version: str,
        idempotency_key: str,
        handle: BuildHandle,
        deadline: float,
    ) -> None:
        """Poll ``build-status`` until the build is terminal; raise if it is not.

        Two recoveries are folded into the loop, because both present as "the
        thing I was following is no longer there" rather than as a failure:

        * **The ``build_id`` stops resolving** (a 404, or an ``idle`` answer for
          it). The poll drops to the ``{model_name, version}`` lookup, which is
          what a Model Manager restart re-attaches through.
        * **Nothing is in flight at all.** ``idle`` is a 200 and is not a verdict
          on the load, so the real outcome is read from the repository index: a
          READY version means the build finished while we were not looking.

        Only if both of those come up empty is the load re-POSTed, and only once.
        The same idempotency key goes back out, so the service either re-attaches
        to a live build or replays a cached success — the sole case that actually
        rebuilds is a key whose previous attempt failed, whose reservation the
        service has released on purpose. Allowing this more than once would turn a
        persistently idle backend into a rebuild loop.

        ``deadline`` bounds the whole load, POST included, and is checked before
        anything that would extend the call — sleeping, and above all re-POSTing,
        which in synchronous mode blocks for another full conversion. A terminal
        phase is always honoured even past the deadline: a build that has already
        succeeded is not worth discarding over a few milliseconds.
        """
        build_id = handle.build_id
        may_repost = True

        while True:
            status = self._read_build_status(name, version, build_id)

            if status.phase == "succeeded":
                logger.info("loaded %s@%s", name, version)
                return
            if status.phase == "failed":
                raise _build_failure(name, version, status)

            if status.phase in ("idle", "unknown"):
                if build_id is not None:
                    logger.info(
                        "build %s for %s@%s no longer resolves; re-attaching by name",
                        build_id,
                        name,
                        version,
                    )
                    build_id = None
                    continue
                if self._is_ready(name, version):
                    logger.info("loaded %s@%s (confirmed READY via /v1/models)", name, version)
                    return
                if monotonic() >= deadline:
                    raise _budget_exhausted(name, version)
                if may_repost:
                    may_repost = False
                    logger.info(
                        "no build in flight for %s@%s and it is not READY; re-driving the load "
                        "with the same idempotency key",
                        name,
                        version,
                    )
                    handle = self.inference.load_async(
                        name, version, idempotency_key=idempotency_key
                    )
                    if handle.phase == "succeeded":
                        logger.info("loaded %s@%s", name, version)
                        return
                    build_id = handle.build_id
                    continue
                raise ModelManagerError(
                    code="load_failed",
                    message=(
                        f"AOI Inference reports no build in flight for {name}@{version} and the "
                        "version is not READY"
                    ),
                    retryable=True,
                    http_status=502,
                )

            if monotonic() >= deadline:
                raise _budget_exhausted(name, version)
            sleep(settings.build_poll_interval)

    def _read_build_status(self, name: str, version: str, build_id: str | None) -> BuildStatus:
        """Read build status by ``build_id`` when one is held, else by target."""
        if build_id:
            return self.inference.build_status(build_id=build_id)
        return self.inference.build_status(name=name, version=version)

    def _is_ready(self, name: str, version: str) -> bool:
        """Return True when the repository index reports this version READY.

        The fallback confirmation for an ``idle`` build status. ``idle`` is a 200,
        not a verdict, so this call decides "did the build finish while we were
        not looking" -- and a false positive reports a successful pull for a
        version that is not serving, which the promotion only discovers at the
        routing PUT that refuses a non-READY target.

        So the state must be *present and* READY. An absent ``state`` is not
        evidence of anything. ``_loaded_versions`` in the adapter keeps the
        opposite default on purpose (see the comment there); for a confirmation
        the false negative is the safe direction -- it costs one re-attach or
        re-POST on the same idempotency key, then fails loudly.

        A failed lookup answers False for the same reason. Rows are shape-checked
        because only the envelope is validated upstream, and a malformed entry
        would raise mid-poll and lose a build that is progressing normally.
        """
        try:
            models = self.inference.list_models()
        except ModelManagerError:
            logger.warning("list_models unavailable; cannot confirm %s@%s is READY", name, version)
            return False
        if not isinstance(models, list):
            logger.warning("list_models returned a non-list; cannot confirm %s@%s", name, version)
            return False
        return any(
            isinstance(model, dict)
            and model.get("name") == name
            and model.get("version") == version
            and str(model.get("state", "")).upper() == "READY"
            for model in models
        )


class PollActivation(ActivationMode):
    """Poll mode placeholder.

    Intended logic: download to a staging dir on the same filesystem, sha256-
    verify there, then atomically publish via ``os.rename()`` or ``ln -sfn``
    into the Triton-watched repo (never a plain copy). The serving stack
    auto-loads on its next poll; no call to the AOI Inference load API.
    """

    def stage_and_load(
        self,
        name: str,
        version: str,
        artifact_uri: str,
        sha256: str,
        *,
        spec_uri: str | None = None,
        threshold: float | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Unimplemented: publishing atomically is the whole design of poll mode.

        Raising is the correct placeholder. Falling back to the explicit-mode
        staging path would write into the watched repository non-atomically, and
        the serving stack would poll and load a half-written artifact.
        """
        raise NotImplementedError("poll mode placeholder")

    def unload(self, name: str, version: str) -> None:
        """Unimplemented: poll mode retires a version by unpublishing it.

        There is no load/unload API call in this mode — the serving stack follows
        the repository — so this cannot borrow the explicit-mode implementation.
        """
        raise NotImplementedError("poll mode placeholder")

    def delete(self, name: str, version: str) -> None:
        """Unimplemented: ordering against the stack's next poll is unsolved here.

        Removing files the watcher has not yet noticed is what makes this
        different from the explicit-mode delete, rather than a copy of it.
        """
        raise NotImplementedError("poll mode placeholder")


def _build_failure(name: str, version: str, status: BuildStatus) -> ModelManagerError:
    """Map a ``phase=failed`` build onto a Model Manager error.

    A deterministic failure is surfaced non-retryable with the remedy named,
    rather than as one more opaque load failure the caller will re-drive until its
    budget runs out.
    """
    detail = status.error or "no error reported"
    remedy = deterministic_build_failure(detail)
    if remedy:
        return ModelManagerError(
            code="load_failed",
            message=f"AOI Inference could not build {name}@{version}: {detail}. {remedy}",
            retryable=False,
            http_status=502,
        )
    return ModelManagerError(
        code="load_failed",
        message=f"AOI Inference failed to build {name}@{version}: {detail}",
        retryable=True,
        http_status=502,
    )


def get_activation(inference: AOIInferenceClient | None = None) -> ActivationMode:
    """Return the activation strategy selected by ``settings.activation_mode``."""
    if settings.activation_mode == "poll":
        return PollActivation()
    return ExplicitActivation(inference=inference)
