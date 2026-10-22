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

"""Settings for the standalone Model Manager service.

Read from environment variables (prefix ``MODEL_MANAGER_``). The Model Manager
is intentionally decoupled from DFW: it only speaks the Model Promotion HTTP
contract upstream and the AOI Inference / AOI Client HTTP contracts downstream.
"""

import logging
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Model Manager configuration."""

    model_config = SettingsConfigDict(env_prefix="MODEL_MANAGER_", extra="ignore")

    # Serving backend selected via the adapter registry (env MODEL_MANAGER_CLIENT).
    # "aoi" is the legacy control plane (/v1/models/*, /v1/model-routing);
    # "unified" is ANY backend speaking the Unified Model Control API -- the
    # adapter sends only the portable core and reads load completion from the
    # response status, so it never needs to know which implementation answers.
    client: Literal["aoi", "unified"] = "aoi"
    # HAProxy pins every /v1/models/* and /v1/model-routing request to the single
    # control replica, so one address serves loads, build status, inventory and
    # the routing PUT. Only inference fans out, and MM never calls it.
    aoi_control_url: str = "http://localhost:5000"
    # Per-API overrides, kept because the two APIs were once separate services
    # and both env vars are still wired. Unset means "use aoi_control_url".
    aoi_inference_url: str | None = None
    aoi_client_url: str | None = None
    model_repo_path: str = "/models"
    # Explicit mode wires AOI Inference load/unload; poll mode is a placeholder.
    activation_mode: Literal["explicit", "poll"] = "explicit"
    model_name: str = "aoi-secondary-inspection"
    request_timeout: float = 30.0
    # Bounded exponential backoff for transient downstream failures.
    request_max_retries: int = 2
    request_backoff_base: float = 0.5
    # Load asynchronously (``?wait=0``) and read the outcome from build-status: a
    # cold ``.pth`` load exports ONNX and builds a TensorRT engine for minutes,
    # outlasting the 120s HAProxy timeout, so a synchronous load cannot report its
    # own result. ``false`` selects the synchronous fallback for a backend without
    # the toggle.
    load_async: bool = True
    # Budget for the build poll after the load -- NOT bounded by ``request_timeout``,
    # which times a single cheap build-status read. 600s clears the ~5-6 min cold
    # build inside the 900s DFW allows ``POST /v1/models/pull``.
    #
    # The wait lives in ``pull`` rather than ``apply`` because a routing PUT is
    # rejected while its target is not READY -- there is no "route it and let it
    # warm up" state.
    build_poll_interval: float = 5.0
    build_poll_timeout: float = 600.0
    build_status_request_timeout: float = 10.0
    # Inline readiness poll after an apply, sized for the FAST path only: an
    # ordinary routing flip settles well under a second.
    #
    # It deliberately does not cover a cold candidate (~5 min TensorRT build) --
    # holding the request open that long makes any proxy timeout surface as a
    # re-applied swap. An apply that has not landed simply returns; the caller
    # polls ``convergence``, bounded by the CALLER's budget (DFW polls 900s)
    # rather than by a held connection.
    readiness_max_checks: int = 15
    readiness_poll_interval: float = 2.0

    # ------------------------------------------------------------------ unified
    # One address serves load, unload, routing and status -- the unified contract
    # collapses the legacy split between the model plane and the routing plane.
    unified_url: str = "http://localhost:5000"
    # The ONLY switch for the optional pre-load build step: set => the build runs,
    # unset or empty => no HTTP request is ever made to any builder. No default,
    # because a builder is the exception (AOI builds inside its own load) and a
    # default would silently turn the step on for every existing deployment.
    #
    # Read for truthiness rather than ``is None``: deploy/docker-compose.mm.yaml
    # passes settings through ``${VAR:-}``, which arrives as an empty string, and
    # an empty string must mean OFF.
    builder_url: str | None = None
    # Budget for a single unified load REQUEST. A backend that loads synchronously
    # blocks for the ONNX export and TensorRT build (minutes), so this is sized
    # like build_poll_timeout rather than request_timeout. Never retried: replaying
    # a request that is still building starts a second build. A backend that
    # ACCEPTS the load (202) instead moves the wait to build_poll_timeout.
    unified_load_timeout: float = 900.0
    # Whether the unified adapter may issue an unload at all.
    #
    # Default True: both backends ship per-version unload, verified at the wire.
    # deft-aoi-inference was fixed by 0fce90f3 (2026-08-05); RTVI-CV refuses to
    # evict a routed version itself (409 MODEL_IN_USE). An earlier deft build
    # evicted the whole model, so set this false against an image older than that
    # fix -- granularity is a property of the BUILD, not of the contract.
    #
    # Gates memory only. `cleanup` no longer evicts, so disk reclaim stays on.
    unified_allow_unload: bool = True
    # Whether a routing write also names the streams it applies to.
    #
    # Default False because ``routes`` is not portable: the spec marks
    # ``ModelRoutingValue.routes`` ``x-supported-by: [rtvi-cv]`` and uses
    # ``field: value.routes`` as its own worked example of a field AOI Inference
    # must reject. Sending it there 400s, and errors.py only recognises the
    # ``canary``/``shadow`` fields, so it would surface as an MM-internal 500.
    #
    # Turn it on against RTVI-CV. It MERGES the default policy but does not
    # re-derive the bindings of streams already attached: a default-only promote
    # of a live shadow builds a FRESH inference bin for the candidate and
    # initialises it with the OUTGOING version's Triton ensemble, then
    # ``compact-idle-models-across-pools`` evicts that ensemble and the app dies
    # with ``Request for unknown model ... is not at ready state``. Naming the
    # streams keeps the attached ones on the instance loaded for them. Reproduced
    # on metropolis-perception 26.08.1-172. There is no capability-discovery
    # endpoint to read this from, so it is a setting, not negotiated per request.
    unified_stream_routes: bool = False

    # How long a `.staging.lock` is honoured before it reads as abandoned.
    # 0 disables expiry; negatives are rejected here because `staging_in_progress`
    # also reads them as "never expire", and one off switch is enough.
    #
    # The marker leaks: a pull killed mid-stage never removes it, and only
    # another pull of the SAME version overwrites it. 24h is the longest pull
    # this repo reasons about (`config/deft.py`), so an older marker belongs to a
    # pull that is not running. Expiry always logs at WARNING -- it means a leak.
    staging_lock_max_age_s: float = Field(default=86400.0, ge=0.0)

    # Operator floor on cleanup's age gate: the requirement is the greater of
    # this and the request's `min_age_s`, so a misconfigured caller cannot
    # override it.
    #
    # A default of 0.0 (no floor) is safe because the pull -> apply window is held
    # by the ROOTS now, not by this clock: the residency root refuses anything the
    # backend still reports in memory, and `.staging.lock` covers the pull itself
    # through the engine build. What is left for the floor is a writer that
    # declared nothing; 86400 (24h) suits a deployment that wants that backstop.
    #
    # Age is the newest mtime anywhere in the version's tree, not the directory's
    # own -- a download writing into a file that already exists never moves the
    # directory mtime, so measured that way a running pull read as OLD.
    #
    # Wrong tool against a caller that still expects `cleanup` to evict: refusing
    # on age blocks the sweep's own targets, which are old, just as long. Use
    # `reclaim_dry_run` for that.
    reclaim_min_age_s: float = 0.0

    # Operator override forcing dry-run on every cleanup: every guard and the
    # deletable predicate still run for real, the outcome is logged, nothing is
    # deleted. Default False so upgrades behave as before.
    #
    # Set it while MM is deployed ahead of DFW -- a caller predating the
    # unload/cleanup split then cannot lose a directory. It cannot give that
    # caller back the eviction it expected; only upgrading the caller does that.
    reclaim_dry_run: bool = False


settings = Settings()

logger = logging.getLogger(__name__)


def log_reclaim_posture() -> None:
    """Say at startup whether this process will delete a version directory.

    INFO in both cases: the reclaiming posture is the shipped default, so
    warning on it would fire on every healthy deployment and be filtered long
    before the skew window it was written for.
    """
    if settings.reclaim_dry_run:
        logger.info(
            "disk reclaim: DRY RUN -- cleanup will run every guard and evaluate the "
            "deletable predicate for real, and delete nothing "
            "(MODEL_MANAGER_RECLAIM_DRY_RUN=true)"
        )
        return
    logger.info(
        "disk reclaim: ACTIVE -- cleanup may delete a version directory, with an age "
        "floor of %.0fs. If DFW has not yet been upgraded to the release that splits "
        "unload from cleanup, set MODEL_MANAGER_RECLAIM_DRY_RUN=true until it has: an "
        "older caller calls cleanup expecting it to evict, and here it evicts nothing "
        "and goes straight to the delete decision.",
        settings.reclaim_min_age_s,
    )
