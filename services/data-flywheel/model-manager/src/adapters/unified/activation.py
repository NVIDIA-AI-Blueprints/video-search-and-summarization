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

"""Staging for a unified-API backend.

Everything about staging is backend-agnostic — download, sha256 verification,
the idempotent re-pull skip, atomic ``spec.yaml`` and ``metadata.json`` writes,
and the version-directory removal — so it is inherited from
``StagingActivation`` in :mod:`src.adapters.activation`. All that is added here
is the pair of methods that talk to the serving stack: ``_load_to_ready`` and
``unload``.

The unified load is a single request with no build handle to poll. A backend
that completes it synchronously answers 200; one that accepts it answers 202 and
warms in the background, and the wait for that is ``_await_loaded`` below rather
than anything inherited — the shared base has no notion of a build protocol.
"""

import logging
from time import monotonic, sleep

from src.adapters.activation import StagingActivation, _budget_exhausted
from src.adapters.unified import state as state_mod
from src.adapters.unified.builder import build_extra_load_params
from src.adapters.unified.client import UnifiedModelClient
from src.adapters.unified.state import _SERVABLE_STATES as _SERVABLE_LOAD_STATES
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)

# Lifecycle states that mean "this load will never become servable". Kept
# deliberately narrow: ``unloaded``/``unavailable`` are NOT here, because a
# version legitimately passes through them while its engine is being built (the
# serving stack builds before it indexes the version), and calling that terminal
# would abort a healthy cold start. Burning the poll budget and reporting a
# retryable timeout is the safe direction; declaring a false failure is not.
_TERMINAL_FAILURE_STATES = frozenset({"failed", "error"})

# Consecutive status reads that may fail before the wait gives up. A blip while
# a load is in flight says nothing about the load, but an endpoint that is
# simply gone should surface as itself rather than as a timeout minutes later.
_MAX_CONSECUTIVE_STATUS_FAILURES = 5


class UnifiedActivation(StagingActivation):
    """Shared staging whose load/unload speak the unified control API."""

    def __init__(self, client: UnifiedModelClient):
        self.client = client

    def _load_to_ready(self, name: str, version: str, idempotency_key: str) -> None:
        """Load a version through the unified API and wait for it to be servable.

        ``idempotency_key`` is accepted and dropped: the unified load envelope is
        ``additionalProperties: false`` and defines no idempotency field, so
        sending one is a ``FIELD_UNSUPPORTED`` rejection. Repeat safety comes
        instead from the caller's own re-pull skip (a matching digest means the
        download never happens) and from the backend's handling of a redundant
        load. The contract permits TWO answers there -- a no-op 200, or a 409
        naming the already-loaded case -- so both are accepted here; the second
        is resolved against model-status, because the spec attaches no error
        code to it. That resolution is skipped for a conflict the contract says
        clears itself, which model-status cannot speak to.

        Whether to wait is decided by the BACKEND'S OWN ANSWER, not by
        configuration: ``200`` means the load completed on the request thread,
        anything else 2xx means it was accepted and is still warming. That is the
        contract's own instruction to clients, and it is the reason this adapter
        needs no per-backend capability declaration — the one capability that
        genuinely varies is the one the wire already reports, per request, and it
        cannot go stale the way a deploy-time constant can.

        A ``200`` whose body nonetheless reports a non-servable ``state`` is
        believed over its own status line and waited out. That combination is a
        contract violation, but the cost of trusting it is a version routed
        before it can serve, and the cost of distrusting it is one status read.

        The wait belongs HERE rather than with the caller: a routing write is
        rejected while its target is not READY, so there is no "route it and let
        it warm up" state for a slow load to settle into. Returning early would
        discard the pull's whole budget and then fail at the next gate with an
        error naming the routing verb instead of the load.
        """
        started = monotonic()
        # ONE deadline for the whole verb -- build, then the load request, then
        # the warm-up poll. Each stage gets what is LEFT of it, never a fresh
        # budget of its own.
        #
        # This is the same reasoning the poll deadline below already applies,
        # extended to cover the build. Without it the stages stack: a build may
        # burn build_poll_timeout and the load request would then still open a
        # full unified_load_timeout, so the pair can outlast the caller's pull
        # budget -- DFW times out and re-POSTs the pull while this load is still
        # running, which is the duplicate-work case client.load refuses to cause.
        deadline = started + settings.unified_load_timeout
        # A no-op returning {} unless a builder is configured, so a deployment
        # without one is byte-identical to before.
        extra_load_params = build_extra_load_params(
            name, version, min(deadline, started + settings.build_poll_timeout)
        )
        try:
            status_code, body = self.client.load(
                name,
                version,
                extra_load_params=extra_load_params,
                timeout=max(1.0, deadline - monotonic()),
            )
        except ModelManagerError as exc:
            # The revision enumerates "already-loaded models" among its Conflict
            # cases, so refusing a redundant load is now a legal answer -- and a
            # re-pull of a version that is already serving is the ordinary DFW
            # retry. But the spec names NO code for it, so this cannot be keyed
            # on in the error mapping; the only way to tell "already in the
            # desired state" from a real conflict is to ask model-status.
            #
            # Everything else keeps the classification translate() gave it, and
            # that has to be decided BEFORE the status probe. At 409 ``retryable``
            # marks exactly the codes errors.py calls self-clearing, which is the
            # precise negation of "the desired state is already reached": a
            # conflict that resolves itself is one whose state is still IN
            # MOTION, so a ready row read during it is a snapshot the update may
            # invalidate a moment later.
            #
            # This is reachable in ordinary operation, not a corner case.
            # UPDATE_IN_FLIGHT is scoped to the MODEL (``field: value.name``,
            # "a model update for this model is still in progress"), so a load of
            # THIS version can be refused while THIS version's row still reads
            # ready -- describing the pre-update engine. Re-raising keeps it
            # retryable so DFW re-drives the pull, which is the contract's own
            # remedy, rather than reporting a load the backend REFUSED as done
            # against bytes it never saw.
            #
            # The guard goes first rather than merely before the ``return``:
            # ``or`` short-circuits, so this changes nothing for a non-409, and
            # it drops a status GET that could not have informed the answer.
            if exc.retryable:
                raise
            if exc.http_status != 409 or not self._already_servable(name, version):
                raise
            logger.info(
                "load of %s@%s was refused as a conflict, but model-status reports it servable",
                name,
                version,
            )
            return
        if status_code == 200 and _reports_servable(body):
            logger.info("loaded %s@%s via the unified control API", name, version)
            return
        logger.info(
            "%s@%s accepted (HTTP %d, state=%r); waiting for it to become servable",
            name,
            version,
            status_code,
            body.get("state"),
        )
        # The poll gets what is LEFT of the pull's budget, never a fresh one.
        #
        # Both halves of this matter. Opening the deadline before the load would
        # hand a fast 202 a budget already spent by a slow request. But giving
        # the poll its own full ``build_poll_timeout`` afterwards lets a slow
        # load and a slow warm-up stack — up to ``unified_load_timeout`` plus
        # ``build_poll_timeout`` — which overruns the caller's own pull budget.
        # DFW would then time out and re-POST the pull while this one is still
        # building, causing exactly the duplicate engine build ``client.load``
        # refuses to cause itself. Capping at ``started + unified_load_timeout``
        # keeps the whole verb inside one pull budget either way.
        self._await_loaded(name, version, min(deadline, monotonic() + settings.build_poll_timeout))
        logger.info("loaded %s@%s via the unified control API (after warm-up)", name, version)

    def _already_servable(self, name: str, version: str) -> bool:
        """True when model-status reports ``version`` as servable right now.

        Deliberately NOT ``_reports_servable``. That helper reads a *load
        response* envelope and returns True when its top-level ``state`` is
        absent -- correct there, because a 200 already means the load completed.
        A model-status snapshot has no top-level ``state`` at all (versions live
        under ``models[].versions[]``), so reusing it would return True
        unconditionally and convert EVERY load 409 -- UPDATE_IN_FLIGHT,
        MODE_DISABLED, a genuine conflict -- into a silent success, reporting a
        promotion as pulled with nothing loaded.

        This reads an explicit ready row instead, and a failed status read
        answers False: unable to confirm is not confirmation.
        """
        try:
            status = self.client.get_status(name, version)
        except ModelManagerError:
            return False
        rows = [r for r in state_mod.version_rows(status, name) if r.get("version") == version]
        _known, ready, _resident = state_mod.version_sets(rows)
        return version in ready

    def _await_loaded(self, name: str, version: str, deadline: float) -> None:
        """Poll model-status until an accepted load reaches a terminal state.

        A status read that fails *transiently* is treated as "keep waiting" — the
        load is still in flight and a blip says nothing about it — but only for a
        bounded run of consecutive failures. A read that fails
        *deterministically* is not waited on at all; it will answer the same way
        every time, so it is surfaced as itself rather than as a readiness
        timeout several minutes later that names the wrong problem.

        A version with no row at all is NOT an error and is waited out. On a
        backend that builds the engine before indexing the version, absence is
        exactly what a healthy cold start looks like; the poll budget is the
        thing that bounds it.
        """
        consecutive_failures = 0
        ever_seen = False
        while True:
            try:
                status = self.client.get_status(name, version)
                consecutive_failures = 0
            except ModelManagerError as exc:
                if not exc.retryable:
                    # A deterministic failure — a rejected credential, a gone
                    # endpoint, a malformed request — will answer identically on
                    # every remaining poll. Burning the budget and then reporting
                    # a retryable ``load_failed`` would hide the real cause AND
                    # invite the caller to re-drive, and re-driving a pull
                    # re-issues the load: the one call this adapter never
                    # replays, because a concurrent reload races in-flight
                    # inference. Surface it as itself, immediately.
                    raise ModelManagerError(
                        code=exc.code,
                        message=(
                            f"could not read the status of {name}@{version} while waiting "
                            f"for it to load: {exc.message}"
                        ),
                        retryable=False,
                        http_status=exc.http_status,
                        details=exc.details,
                    ) from exc
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_STATUS_FAILURES:
                    raise ModelManagerError(
                        code="load_failed",
                        message=(
                            f"could not read the status of {name}@{version} after "
                            f"{consecutive_failures} consecutive attempts: {exc.message}"
                        ),
                        retryable=True,
                        http_status=502,
                    ) from exc
                status = {}
            rows = [r for r in state_mod.version_rows(status, name) if r["version"] == version]
            ever_seen = ever_seen or bool(rows)
            _, ready, _ = state_mod.version_sets(rows)
            if version in ready:
                return
            terminal = next(
                (
                    state_mod.state_of(row)
                    for row in rows
                    if state_mod.state_of(row) in _TERMINAL_FAILURE_STATES
                ),
                None,
            )
            if terminal is not None:
                raise ModelManagerError(
                    code="load_failed",
                    message=(
                        f"the unified backend reports {name}@{version} as {terminal!r} after "
                        "accepting the load; retrying will not change the outcome"
                    ),
                    retryable=False,
                    http_status=502,
                )
            if monotonic() >= deadline:
                raise _budget_exhausted_loading(name, version, ever_seen)
            sleep(settings.build_poll_interval)

    def unload(self, name: str, version: str) -> None:
        """Evict a version from serving memory, leaving its files on disk."""
        self.client.unload(name, version)


def _reports_servable(body: dict) -> bool:
    """True unless the load response explicitly names a non-servable state.

    An absent ``state`` is taken as servable: ``200`` already means the load
    completed, and the field is optional in the response envelope. Only an
    explicit contradiction overrides the status line.
    """
    state = body.get("state") if isinstance(body, dict) else None
    if not isinstance(state, str):
        return True
    return state.lower() in _SERVABLE_LOAD_STATES


def _budget_exhausted_loading(name: str, version: str, ever_seen: bool) -> ModelManagerError:
    """Return the poll-budget error, saying WHICH way the wait failed.

    "It never appeared in the status view" and "it appeared but never became
    servable" have different causes and different next steps, and a single
    generic timeout message sends an operator looking in the wrong place.
    """
    error = _budget_exhausted(name, version)
    detail = (
        "it never appeared in the model status view"
        if not ever_seen
        else "it appeared in the model status view but never became servable"
    )
    return ModelManagerError(
        code=error.code,
        message=f"{error.message}: {detail}",
        retryable=error.retryable,
        http_status=error.http_status,
    )
