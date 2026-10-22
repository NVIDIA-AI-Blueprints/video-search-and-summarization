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
"""DEFT backend provider — wires the DEFT-loop clients to the neutral seam.

Selected when ``backend.kind == "deft"``. Declares support for the DEFT-family
job types — ``aoi_job`` and ``its_job`` (``src.job_types.DEFT_JOB_TYPES``) —
which flips the API gate (``/jobs``, ``/configs``, ``/triggers``) and the task
dispatcher on for both. Each factory method returns a wrapper
(``src/backends/deft/wrappers.py``) that implements the neutral base ABC.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.backends.base.provider import BackendProvider
from src.backends.deft.deft_client import DeftClient
from src.backends.deft.eval import get_evaluator
from src.backends.deft.storage_backend_client import StorageBackendClient
from src.backends.deft.wrappers import (
    DeftDatastore,
    DeftDeploymentClient,
    DeftEmbedding,
    DeftTraining,
)
from src.config import DEFTConfig
from src.job_types import DEFT_JOB_TYPES
from src.log_utils import setup_logging

if TYPE_CHECKING:
    from src.backends.base.interfaces import EvalBackend

logger = setup_logging("data_flywheel.backends.deft.provider")


#: Bounded readiness poll for a freshly-created eval set (design §4.3).
_EVAL_SET_POLL_MAX_ATTEMPTS = 15
_EVAL_SET_POLL_INTERVAL_S = 2.0


def _http_results_source(results_url: str):
    """LEGACY direct reader: ``GET {results_url}?model=&version=&role=`` → samples.

    Pre-dates the Storage Backend eval-set design; kept as an explicit override
    (``deft_config.aoi_results_url``) for deployments/mocks still serving the
    inline-results endpoint. Fail-safe: any fetch error returns ``[]`` — the
    evaluator then verdicts ``hold``.
    """

    def _fetch(window: Any, role: str) -> list[dict]:
        import requests

        params: dict[str, str] = {"role": role}
        if isinstance(window, dict):
            params.update({k: str(v) for k, v in window.items() if v is not None})
        elif window is not None:
            params["window"] = str(window)

        try:
            resp = requests.get(results_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            samples = data.get("results") if isinstance(data, dict) else data
            return list(samples or [])
        except Exception:
            logger.warning(
                "AOI results fetch failed from %s; returning no samples (verdict holds)",
                results_url,
                exc_info=True,
            )
            return []

    return _fetch


def _eval_set_results_source(client: StorageBackendClient):
    """Build a ``(window, role) -> samples`` reader over the eval-set flow.

    Storage Backend contract v2 (aoi-api!5 final) — snapshot lifecycle:

    1. ``POST /v1/results`` creates an eval set from the online inference
       results matching the window (model/version/role); the response is
       ID-only (``{eval_id}``).
    2. ``GET /v1/eval/{eval_id}`` is polled (bounded) until ``status: ready``.
    3. The detail's ``manifests[]`` (ABSOLUTE URIs + sha256) are the
       retrieval reference: each jsonl manifest is fetched (HTTP(S) directly;
       ``s3://`` is the known object-store TODO), sha256-verified when the
       checksum is present, and its lines parsed as sample records.

    Fail-safe throughout: any error returns ``[]`` so the evaluator verdicts
    ``hold`` — advancement never happens on missing/partial/corrupt data.

    CONCURRENCY BOUND (documented, not yet a problem): the readiness poll
    blocks the calling beat task for up to POLL_MAX x INTERVAL (currently
    15 x 2s = 30s) per promotion, and ``online_eval_poll`` iterates promotions
    sequentially — N concurrent promotions cost up to N x 30s per tick. Fine
    at AOI's one-run-at-a-time scale; move the poll into its own queued task
    before multi-promotion scenarios land.
    """

    def _norm_status(value: Any) -> Any:
        # Defensive casing normalization ("Ready"/"READY" must not burn the
        # whole poll budget and spuriously hold every tick).
        return value.lower() if isinstance(value, str) else value

    def _read_manifest(manifest: dict) -> list[dict]:
        import hashlib
        import json as _json
        from urllib.parse import urlsplit, urlunsplit

        import requests

        def _safe_uri_for_log(raw: Any) -> str:
            # Presigned URLs carry credentials in the QUERY — never log it.
            if not isinstance(raw, str):
                return repr(raw)
            parts = urlsplit(raw)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

        def _presign(raw: Any) -> str | None:
            """Resolve an object-store locator (``s3://…``) to a fetchable HTTPS URL.

            DFW deliberately carries NO object-store credentials — the Storage
            Backend mints short-lived credential-free links instead
            (``POST /v1/get_presigned_urls``), and its presign allow-list already
            covers the ``evals/`` prefix for exactly this purpose. So we resolve
            the locator and reuse the ordinary HTTP path below, rather than
            teaching DFW to speak S3.

            Fail-safe: any problem degrades to skip-with-a-warning (``None``) so
            the verdict holds, exactly as before — this never raises into the beat.
            """
            if not isinstance(raw, str) or not raw:
                logger.warning("Eval-set manifest has an unusable URI (%r); skipping", raw)
                return None
            try:
                resolved = client.get_presigned_urls([raw])
            except Exception:
                logger.warning(
                    "Eval-set manifest %s could not be presigned; skipping",
                    _safe_uri_for_log(raw),
                    exc_info=True,
                )
                return None
            for item in resolved.get("items") or []:
                presigned = item.get("presigned_url")
                if presigned:
                    return presigned
            logger.warning(
                "Eval-set manifest %s could not be presigned (failed=%r); skipping",
                _safe_uri_for_log(raw),
                resolved.get("failed"),
            )
            return None

        fmt = manifest.get("format")
        if fmt and fmt != "jsonl":
            logger.warning("Eval-set manifest format %r not supported (jsonl only); skipping", fmt)
            return []
        uri = manifest.get("uri")
        if isinstance(uri, str) and uri.startswith(("http://", "https://")):
            fetch_url = uri
        else:
            # Object-store locator — presign it through the Storage Backend.
            fetch_url = _presign(uri)
            if fetch_url is None:
                return []
        # NB: every log line below deliberately reports `uri` (the stable,
        # credential-free locator), never `fetch_url` (which carries signature
        # material in its query string).
        resp = requests.get(fetch_url, timeout=30)
        resp.raise_for_status()
        payload = resp.content
        expected = manifest.get("sha256")
        if expected and len(str(expected)) != 64:
            # Not a SHA-256 hex digest (the contract example is 32 chars) —
            # verifying against it would mismatch FOREVER and silently freeze
            # rollouts. Proceed unverified, loudly.
            logger.warning(
                "Eval-set manifest %s carries a non-SHA-256 checksum (len=%d); "
                "proceeding WITHOUT verification",
                _safe_uri_for_log(uri),
                len(str(expected)),
            )
            expected = None
        if expected:
            actual = hashlib.sha256(payload).hexdigest()
            if actual.lower() != str(expected).lower():
                logger.warning(
                    "Eval-set manifest %s sha256 mismatch (expected %s, got %s); "
                    "discarding its samples",
                    _safe_uri_for_log(uri),
                    expected,
                    actual,
                )
                return []
        return [_json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]

    def _fetch(window: Any, role: str) -> list[dict]:
        import time as _time

        # CreateEvaluationSetRequest: the window rides a `selection` envelope
        # (additionalProperties: false at both levels), and the selection
        # REQUIRES model + version + role — send nothing the contract would
        # 422, and don't bother the backend when the required fields can't
        # be met (fail-safe hold instead).
        selection: dict[str, Any] = {"role": role}
        if isinstance(window, dict):
            # Whitelist the contract's EvaluationSetSelection keys — an
            # unexpected window key would 422 (additionalProperties: false),
            # and `role` stays owned by the function argument.
            allowed = {"model", "version", "from", "to"}
            selection.update({k: v for k, v in window.items() if k in allowed and v is not None})
        if not (selection.get("model") and selection.get("version")):
            logger.warning(
                "Eval-set window %r lacks the contract-required model/version; "
                "no samples (verdict holds)",
                window,
            )
            return []
        try:
            # The client owns the CreateEvaluationSetRequest envelope.
            created = client.create_eval_set(selection)
            eval_id = created.get("eval_id") or created.get("id")
            if not eval_id:
                logger.warning("Eval-set create returned no eval_id; no samples")
                return []
            detail = client.get_eval_stats(eval_id)
            status = _norm_status(detail.get("status"))
            attempts = 0
            while (
                status not in ("ready", "failed", "deleted")
                and attempts < _EVAL_SET_POLL_MAX_ATTEMPTS
            ):
                _time.sleep(_EVAL_SET_POLL_INTERVAL_S)
                detail = client.get_eval_stats(eval_id)
                status = _norm_status(detail.get("status"))
                attempts += 1
            if status != "ready":
                logger.warning(
                    "Eval set %s not ready (status=%r after %d polls); no samples",
                    eval_id,
                    status,
                    attempts,
                )
                return []

            samples: list[dict] = []
            for manifest in detail.get("manifests") or []:
                samples.extend(_read_manifest(manifest))
            if not samples:
                logger.warning("Eval set %s yielded no samples from its manifests", eval_id)
            return samples
        except Exception:
            logger.warning(
                "Eval-set collection failed (window=%r role=%r); no samples (verdict holds)",
                window,
                role,
                exc_info=True,
            )
            return []

    return _fetch


def _is_aoi_family(use_case: str) -> bool:
    """True when ``use_case`` is scored by the AOI evaluator (or a subclass).

    The registry — not a hard-coded name list — is the authority, so an
    AOI-shaped use case registered later cannot silently lose the legacy
    ``aoi_results_url`` override. An UNREGISTERED use
    case answers False: "not known to be AOI" must never be read as "AOI", which
    is the direction that grades a rollout on another use case's data.
    """
    # Local import: read the registry at call time, so a use case registered
    # after this module was imported is still seen.
    from src.backends.deft.eval import DeftAOIEvaluator, evaluator_for

    evaluator_cls = evaluator_for(use_case)
    return evaluator_cls is not None and issubclass(evaluator_cls, DeftAOIEvaluator)


class DeftBackendProvider(BackendProvider):
    kind = "deft"
    #: DEFT runs the image-model flywheel; no LLM/embedding pipelines. Consumed
    #: from ``src.job_types`` rather than re-listed here, so this declaration
    #: cannot drift from the family predicate the API edge and the trigger
    #: validators gate on.
    supported_job_types = DEFT_JOB_TYPES

    def __init__(self, config: DEFTConfig | None = None) -> None:
        # Resolve service endpoints from the loaded settings (config.deft.yaml's
        # deft_config block) so a deployment's URLs actually reach the clients;
        # fall back to contract defaults for bare/unit-test construction.
        if config is None:
            from src.config import settings

            config = getattr(settings.system_config, "deft_config", None) or DEFTConfig()
        self._config = config

    def training(self) -> DeftTraining:
        return DeftTraining(DeftClient(self._config))

    def evaluator(self, use_case: str | None = None) -> EvalBackend | None:
        # Returns the backend-owned evaluator, which implements both EvalBackend
        # (the neutral seam) and BaseEvaluator (so the Eval Harness can run it).
        # With no `use_case` (the cleanup/cancel path) DEFT has no eval to operate
        # on, so return None. (No `judge_model_config` — that's NMP-only.)
        if use_case is None:
            return None
        # Online-sample source: the Storage Backend eval-set flow (design §4.3,
        # POST /v1/results → poll /v1/eval/{id} → manifests) is the default.
        # ``aoi_results_url`` remains an explicit legacy override for
        # deployments still serving the inline-results GET endpoint.
        results_url = getattr(self._config, "aoi_results_url", None)
        if results_url and not _is_aoi_family(use_case):
            # The override names a single AOI endpoint serving AOI board records,
            # so honouring it for a use case that scores something else would
            # grade that rollout on AOI data — a wrong number, not an error.
            # Fall through to that use case's own eval-set flow instead.
            logger.warning(
                "deft_config.aoi_results_url is set but use_case=%r is not scored by the "
                "AOI evaluator; IGNORING the legacy override and using the eval-set flow "
                "for this use case (the override names an AOI-only endpoint).",
                use_case,
            )
            results_url = None
        if results_url:
            results_source = _http_results_source(results_url)
        else:
            # Use-case-scoped: the eval set is created in, and its manifests read
            # from, the store that owns THIS use case's online results.
            results_source = _eval_set_results_source(
                StorageBackendClient(self._config.for_use_case(use_case), use_case=use_case)
            )
        return get_evaluator(use_case, results_source=results_source)

    def datastore(self, dataset_name: str, use_case: str | None = None) -> DeftDatastore:
        """Datastore for ``dataset_name``, resolved against ``use_case``'s store.

        Sibling use cases do not necessarily share storage. ``for_use_case``
        returns the config unchanged when a use case declares no overrides — as
        every use case in the shipped config does — so the common path costs
        nothing; ``use_case`` is defaulted so the neutral ``BackendProvider``
        signature still holds.
        """
        return DeftDatastore(
            StorageBackendClient(self._config.for_use_case(use_case), use_case=use_case),
            dataset_name,
        )

    def deployment_client(self, nim: Any) -> DeftDeploymentClient:
        return DeftDeploymentClient()

    def embedding(
        self,
        endpoint_url: str | None = None,
        model_name: str | None = None,
        api_key: str | None = None,
    ) -> DeftEmbedding:
        return DeftEmbedding()
