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
"""HTTP client to the AOI Storage Backend.

DFW never owns the catalog or builds snapshots — it *requests* them. This wraps
the Storage Backend surface DFW uses (contract v2, aoi-api!5): live catalog
stats (``GET /v1/snapshot/stats``), immutable training snapshots
(``POST /v1/snapshot`` + ``GET /v1/snapshot/{id}``), KPI sets
(``POST /v1/kpi`` + ``GET /v1/kpi/{id}``) and online eval sets
(``POST /v1/results`` + ``GET /v1/eval/{id}``).

Snapshot and KPI CREATION are catalog-scoped for a use case that owns a catalog
(``/v1/its/snapshot``); everything else, and every use case that does not, keeps
the unprefixed paths above. See :data:`_CATALOG_BY_USE_CASE`.

Create responses are ID-only;
readiness/status and the retrieval reference (``manifests[]`` with ABSOLUTE
``s3://`` URIs + sha256) come from the detail GET. DEFT reads the snapshot
manifests + referenced artifacts directly from the object store. See
``aoi-api/storage-backend/openapi.json``.

Uses the ``requests`` library with bounded transient-error retries (the neutral
``src/lib/retry.py`` helper — no NMP SDK import). Only idempotent GETs retry;
``create_snapshot`` is a resource-creating POST without an idempotency key, so
a retry after a timeout could materialize duplicate snapshots — single-attempt.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from src.config.deft import DEFTConfig, SnapshotSelectionConfig
from src.lib.retry import retry_on_transient_error
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.backends.deft.storage")

#: Per-request network timeout (connect/read) in seconds.
_REQUEST_TIMEOUT_S = 30


#: DFW use case -> Storage Backend catalog prefix. A MAP, not the use-case name
#: reused: the two namespaces are independent, so a use case whose name differs
#: from its catalog (or several sharing one catalog) is a table entry rather
#: than a special case in the path builder.
#:
#: ``None`` means the UNPREFIXED paths (``/v1/snapshot``), which the backend
#: resolves to the AOI catalog. AOI is spelled out as ``None`` rather than left
#: absent so the table reads as the whole routing decision -- and so the switch
#: below is a one-line edit rather than a new entry.
#:
#: AOI is on ``None`` DELIBERATELY AND TEMPORARILY. The contract (aoi-api!13)
#: calls the unprefixed paths a "legacy alias ... equivalent to
#: POST /v1/aoi/snapshot" and says new callers SHOULD name the catalog. We do
#: not yet, because a Storage Backend that has not deployed that change serves
#: ONLY the unprefixed paths: ``/v1/aoi/snapshot`` would 404 there, while the
#: alias works against both. That keeps DFW deployable against either version
#: during the rollout.
#:
#: ONCE aoi-api!13 AND the deft-aoi-active-learning `its` branch are deployed
#: everywhere, change this to ``"aoi": "aoi"`` and update the routing tests --
#: nothing else moves. Until then the alias is the only form that works on both.
#:
#: A use case absent from the table also falls back to unprefixed, which is what
#: any AOI-family sibling wants; the backend's own ``Literal["aoi", "its"]``
#: would reject a made-up prefix outright.
_CATALOG_BY_USE_CASE: dict[str, str | None] = {
    "aoi": None,
    "its": "its",
}


class StorageBackendClient:
    """Thin client to the AOI Storage Backend (catalog stats + snapshots)."""

    def __init__(self, config: DEFTConfig | None = None, use_case: str | None = None) -> None:
        self.config = config or DEFTConfig()
        #: Which use case this client speaks for. Only ever selects the catalog;
        #: endpoints/credentials still come from ``config`` (already resolved by
        #: ``DEFTConfig.for_use_case``). Optional so the legacy callers that
        #: never routed by use case keep working unchanged.
        self.use_case = use_case

    def _catalog_path(self, suffix: str) -> str:
        """``/v1/{catalog}{suffix}`` for a catalog-owning use case, else ``/v1{suffix}``.

        The Storage Backend scoped snapshot and KPI CREATION by catalog and kept
        the unprefixed paths as aliases resolving to AOI, so only the routes it
        actually scoped go through here -- see the module docstring.

        Derived from the use case rather than configured, so a deployment cannot
        declare a catalog that the backend does not serve, nor forget to declare
        one and silently write another use case's catalog.
        """
        catalog = _CATALOG_BY_USE_CASE.get(self.use_case or "")
        return f"/v1/{catalog}{suffix}" if catalog else f"/v1{suffix}"

    # ------------------------------------------------------------------
    # Low-level HTTP plumbing
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json", "Content-Type": "application/json"}
        # Optional bearer credential (see DeftClient for the same assumption).
        api_key = os.environ.get(self.config.storage_api_key_env)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _request(
        self, method: str, path: str, *, retryable: bool = True, **kwargs: Any
    ) -> requests.Response:
        url = f"{self.config.storage_backend_url.rstrip('/')}{path}"

        def _do() -> requests.Response:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=_REQUEST_TIMEOUT_S, **kwargs
            )
            resp.raise_for_status()
            return resp

        if not retryable:
            # Non-idempotent (resource-creating) call — fail fast rather than
            # risk a duplicate submit after an ambiguous timeout.
            return _do()
        return retry_on_transient_error(_do, f"StorageBackend {method} {path}")

    # ------------------------------------------------------------------
    # Catalog + snapshot surface
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """GET /v1/snapshot/stats — LIVE active-learning catalog stats.

        Returns ``LiveCatalogStats`` (total_records, records_since_last_snapshot,
        class_counts, last_snapshot_id, …). Drives the ``data_threshold`` trigger.
        """
        return self._request("GET", self._catalog_path("/snapshot/stats")).json()

    def create_snapshot(
        self, selection: SnapshotSelectionConfig, reason: str | None = None
    ) -> dict[str, Any]:
        """POST /v1/snapshot — materialize an immutable training snapshot.

        DFW sends the ``CreateSnapshotRequest`` ``{selection, reason}``; the
        response is ID-ONLY (``{snapshot_id}``) — status and the retrieval
        manifests come from :meth:`get_snapshot`.
        """
        # The Storage Backend's ``SnapshotSelection`` reads facets FLAT off the
        # selection object (``additionalProperties: true``): it requires
        # ``created_after`` at the top level and compiles EVERY other key into a
        # ``metadata.content_metadata.<key>`` term filter (see
        # snapshot_service.compile_selection). So we send ONLY the config's facet
        # map (``created_after`` + real data facets) — deliberately NOT ``mode``.
        # ``mode`` is a DFW selection-STRATEGY marker ("query"), not a data facet;
        # sending it adds a ``content_metadata.mode`` term that no record carries,
        # yielding an empty snapshot. Any real facet the backend adds passes
        # through here without a typed field.
        #
        # ``selection.limit`` is withheld for the same reason and has no other
        # home: the contract's ``SnapshotRequest`` is {selection, snapshot_id,
        # reason, metadata} with no cap anywhere, so a limit sent inside the
        # selection would only compile into a ``content_metadata.limit`` term
        # and empty the snapshot. The config field stays as a no-op rather than
        # silently truncating a pool DFW cannot actually cap.
        selection_body: dict[str, Any] = {**selection.filters}

        body: dict[str, Any] = {"selection": selection_body}
        if reason is not None:
            body["reason"] = reason

        data = self._request(
            "POST", self._catalog_path("/snapshot"), retryable=False, json=body
        ).json()
        logger.info("Created snapshot %s", data.get("snapshot_id"))
        return data

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """GET /v1/snapshot/{id} — ``SnapshotStats``: status + ``manifests[]``.

        ``status`` is the shared set enum (creating/ready/failed/deleted);
        ``manifests`` carry ABSOLUTE object-store URIs (+ sha256) — the
        snapshot's storage prefix is derived from them (see
        :func:`derive_storage_prefix`).
        """
        return self._request("GET", self._catalog_path(f"/snapshot/{snapshot_id}")).json()

    # ------------------------------------------------------------------
    # KPI set + online eval set (Storage Backend design §4.2 / §4.3)
    #
    # Both follow the snapshot design: a resource-creating POST returns a
    # snapshot-like handle (id + status + retrieval reference), readiness is
    # polled via a stats endpoint, and the set is retrieved via the reference
    # from the create response.
    # ------------------------------------------------------------------
    def create_kpi_set(
        self, selection: dict[str, Any] | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        """POST /v1/kpi — materialize a KPI set (same semantics as a snapshot).

        ``KpiSetSelection`` is the contract's fixed ``{"tag": "kpi"}`` (a
        const) — enforced here so a bad caller fails fast locally instead of
        as a server-side 422. Response is ID-only (``{kpi_id}``); status +
        manifests via :meth:`get_kpi_stats`.
        """
        if selection is not None and selection != {"tag": "kpi"}:
            raise ValueError("KPI selection must be exactly {'tag': 'kpi'} (contract const)")
        body: dict[str, Any] = {"selection": {"tag": "kpi"}}
        if reason is not None:
            body["reason"] = reason
        data = self._request("POST", self._catalog_path("/kpi"), retryable=False, json=body).json()
        logger.info("Created KPI set %s", data.get("kpi_id"))
        return data

    def get_kpi_stats(self, kpi_id: str) -> dict[str, Any]:
        """GET /v1/kpi/{id} — ``KpiSetStats``: status + ``manifests[]``."""
        return self._request("GET", f"/v1/kpi/{kpi_id}").json()

    def create_eval_set(self, selection: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/results — materialize an eval set from online inference results.

        ``selection`` is the ``EvaluationSetSelection`` (model / version /
        role, optional from / to); this client owns the request envelope and
        wraps it as ``{"selection": ...}`` per ``CreateEvaluationSetRequest``
        — same as :meth:`create_snapshot` / :meth:`create_kpi_set`. Response
        is ID-only (``{eval_id}``); status + retrieval manifests via
        :meth:`get_eval_stats` — results are NOT returned inline.
        """
        if not selection:
            raise ValueError("Evaluation set selection is required (model/version/role)")
        data = self._request(
            "POST", "/v1/results", retryable=False, json={"selection": selection}
        ).json()
        logger.info("Created eval set %s", data.get("eval_id"))
        return data

    def get_eval_stats(self, eval_id: str) -> dict[str, Any]:
        """GET /v1/eval/{id} — ``EvaluationSetStats``: status + ``manifests[]``."""
        return self._request("GET", f"/v1/eval/{eval_id}").json()

    # ------------------------------------------------------------------
    # Presigned download URLs (aoi-api!8)
    # ------------------------------------------------------------------
    def get_presigned_urls(self, urls: list[str], expires_in: int | None = None) -> dict[str, Any]:
        """POST /v1/get_presigned_urls — resolve object-store locators to short-lived
        credential-free HTTPS download links.

        ``urls`` are object-store keys or ``s3://`` URIs; ``expires_in`` (seconds,
        60..86400) overrides the backend default TTL when provided (sent only when
        set). Presigning is a safe/idempotent read (it mints a link, it does not
        create a resource), so — unlike the resource-creating POSTs above — the
        call is ``retryable``. Returns the parsed response
        ``{"items": [{input, key, presigned_url, expires_at}], "failed": [{input, error}]}``.
        """
        body: dict[str, Any] = {"urls": urls}
        if expires_in is not None:
            body["expires_in"] = expires_in
        return self._request("POST", "/v1/get_presigned_urls", retryable=True, json=body).json()


def derive_storage_prefix(detail: dict[str, Any]) -> str | None:
    """Derive a set's object-store prefix from its detail ``manifests``.

    Contract v2 responses carry no ``storage_prefix`` field — the manifests'
    ABSOLUTE URIs are the locator, and the prefix (what DFW hands DEFT as
    ``data_pool.path``) is a manifest URI's parent directory. Falls back to a
    legacy ``storage_prefix`` field when present; returns None when neither
    is available.
    """
    prefix = detail.get("storage_prefix")
    if prefix:
        return str(prefix)
    for manifest in detail.get("manifests") or []:
        uri = manifest.get("uri") if isinstance(manifest, dict) else manifest
        if isinstance(uri, str) and "/" in uri:
            return uri.rsplit("/", 1)[0] + "/"
    return None
