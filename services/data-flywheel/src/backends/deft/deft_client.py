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
"""HTTP client to the external DEFT fine-tuning service.

Wraps the DEFT ``/v1/fine-tune`` surface (create / status / results / logs /
cancel) — see ``aoi-api/deft_api/openapi.json``. DFW polls for status ("pull,
not push"); the optional training-complete callback is a latency optimization
handled at the API layer, not here.

Uses the ``requests`` library with bounded transient-error retries. The retry
helper is the neutral ``src/lib/retry.py`` (no NMP SDK import — this backend
must run on images without ``nemo_platform``), so both backends share one
backoff policy. Only idempotent calls retry: ``create_fine_tune`` is a
resource-creating POST, and a retry after a timeout could double-submit a
training run, so it is single-attempt.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from src.config.deft import DEFTConfig
from src.lib.retry import retry_on_transient_error
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.backends.deft.client")

#: DEFT ``RunStatus.state`` values (deft_api/openapi.json) that are terminal.
#: The DEFT state vocabulary (pending/running/completed/failed/cancelled) is
#: already neutral, so ``get_status`` maps it 1:1 into the neutral status dict;
#: only ``completed`` counts as success.
_TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})
_SUCCESS_STATE = "completed"

#: Per-request network timeout (connect/read) in seconds.
_REQUEST_TIMEOUT_S = 30

#: Results-materialization lag: GET /results returns **409** while the run's
#: results are not yet available (per the contract: "Run has not completed yet
#: (no results available)"). Status can flip terminal slightly before results
#: materialize, so a short bounded retry bridges that lag.
_RESULTS_409_MAX_ATTEMPTS = 6
_RESULTS_409_RETRY_INTERVAL_S = 5.0


class DeftClient:
    """Thin client to the DEFT service. One instance per run/config."""

    def __init__(self, config: DEFTConfig | None = None) -> None:
        self.config = config or DEFTConfig()

    # ------------------------------------------------------------------
    # Low-level HTTP plumbing
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json", "Content-Type": "application/json"}
        # Optional bearer credential sourced from the env var named in config.
        # Assumption: the DEFT service uses bearer auth when a token is present;
        # absent the env var we call unauthenticated (local/dev deployments).
        api_key = os.environ.get(self.config.api_key_env)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _request(
        self, method: str, path: str, *, retryable: bool = True, **kwargs: Any
    ) -> requests.Response:
        url = f"{self.config.deft_base_url.rstrip('/')}{path}"

        def _do() -> requests.Response:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=_REQUEST_TIMEOUT_S, **kwargs
            )
            resp.raise_for_status()
            return resp

        if not retryable:
            # Non-idempotent (resource-creating) call: a retry after a timeout
            # could double-submit even though the first attempt succeeded
            # server-side. Fail fast and let the orchestration layer decide.
            return _do()
        return retry_on_transient_error(_do, f"DEFT {method} {path}")

    # ------------------------------------------------------------------
    # Fine-tune surface
    # ------------------------------------------------------------------
    def create_fine_tune(self, request: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/fine-tune — launch a run from a snapshot + KPI/model refs.

        ``request`` is a DEFT ``FineTuneRequest`` (workflow/profile + config or
        overrides). Returns the ``FineTuneResponse`` ``{"id", "state"}``.

        Single-attempt (``retryable=False``): the contract has no idempotency
        key, so retrying could launch duplicate training runs.
        """
        resp = self._request("POST", "/v1/fine-tune", retryable=False, json=request)
        data = resp.json()
        logger.info("Created DEFT fine-tune run %s (state=%s)", data.get("id"), data.get("state"))
        return data

    def get_status(self, run_id: str) -> dict[str, Any]:
        """GET /v1/fine-tune/{id}/status — normalized run status.

        Returns a neutral dict::

            {"status": <deft state>, "terminal": bool, "succeeded": bool,
             "progress": <Progress|None>, "error": <str|None>,
             "convergence_reason": <str|None>, "raw": <full RunStatus>}

        ``error`` (why a run failed) and ``convergence_reason`` (why DEFT's
        loop stopped) are surfaced top-level so callers can log/persist an
        actionable reason instead of a bare state string.
        """
        data = self._request("GET", f"/v1/fine-tune/{run_id}/status").json()
        state = data.get("state")
        return {
            "status": state,
            "terminal": state in _TERMINAL_STATES,
            "succeeded": state == _SUCCESS_STATE,
            "progress": data.get("progress"),
            "error": data.get("error"),
            "convergence_reason": data.get("convergence_reason"),
            "raw": data,
        }

    def get_results(self, run_id: str) -> dict[str, Any]:
        """GET /v1/fine-tune/{id}/results — checkpoint ref + KPI metrics.

        Extracts the DEFT ``ResultsResponse`` into the neutral artifact shape
        ``{"artifact_uri", "checkpoint", "metrics", "best_iteration"}``.

        A **409** ("run has not completed yet") is retried briefly: the status
        endpoint can report terminal slightly before results materialize, and
        without the retry that lag failed an otherwise-successful run.
        """
        data: dict[str, Any] | None = None
        for attempt in range(1, _RESULTS_409_MAX_ATTEMPTS + 1):
            try:
                data = self._request("GET", f"/v1/fine-tune/{run_id}/results").json()
                break
            except requests.HTTPError as e:
                status_code = getattr(e.response, "status_code", None)
                if status_code != 409 or attempt == _RESULTS_409_MAX_ATTEMPTS:
                    raise
                logger.info(
                    "DEFT results for %s not materialized yet (409); retry %d/%d",
                    run_id,
                    attempt,
                    _RESULTS_409_MAX_ATTEMPTS,
                )
                time.sleep(_RESULTS_409_RETRY_INTERVAL_S)
        assert data is not None  # loop either broke with data or raised
        checkpoint = data.get("best_checkpoint")
        # The checkpoint's content digest, emitted by DEFT alongside the artifact.
        # OPTIONAL and generic here (every checkpoint has a digest; it is not
        # AOI-specific): use it only when DEFT provides it, otherwise leave None
        # and never force it — so this stays compatible with services/use-cases
        # that omit it. Accept the canonical "best_checkpoint_sha256" and fall
        # back to "checkpoint_sha256"/"sha256" for rolling upgrades. Downstream,
        # the AOI promotion handoff (StartPromotionHandler) is what requires a
        # non-empty digest, because the Model Manager verifies PullRequest.sha256
        # after download (a missing one 409s at the pull gate).
        checkpoint_sha256 = (
            data.get("best_checkpoint_sha256")
            or data.get("checkpoint_sha256")
            or data.get("sha256")
        )
        # DEFT's per-model best decision threshold. Optional (older services omit
        # it); accept the canonical "threshold" key and fall back to
        # "best_threshold" when it is missing OR explicitly null (a rolling
        # upgrade may send {"threshold": null, "best_threshold": 0.63}).
        threshold = data.get("threshold")
        if threshold is None:
            threshold = data.get("best_threshold")
        return {
            "artifact_uri": checkpoint,
            "checkpoint": checkpoint,
            "sha256": checkpoint_sha256,
            "metrics": data.get("metrics", {}),
            "best_iteration": data.get("best_iteration"),
            # The training/inference spec published NEXT TO the checkpoint
            # (object-store URI once DEFT publishes artifacts; may be a
            # host-local file:// path or absent on older services). The
            # serving stack needs it beside the weights (spec.yaml) to
            # rebuild/convert the model, so it is threaded through
            # registration and the promotion record.
            "training_spec": data.get("training_spec"),
            "threshold": threshold,
        }

    def cancel(self, run_id: str) -> None:
        """POST /v1/fine-tune/{id}/cancel — cancel an in-flight run (idempotent)."""
        self._request("POST", f"/v1/fine-tune/{run_id}/cancel")
        logger.info("Requested cancel for DEFT run %s", run_id)
        return None
