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
"""DEFT wrappers — derive from the neutral base ABCs and forward to DEFT clients.

Mirrors ``src/backends/nmp/wrappers.py``: each wrapper implements the neutral
contract (``TrainingBackend`` etc.) so the generic pipeline drives DEFT through
the same seam as NMP.
"""

from __future__ import annotations

from typing import Any

from src.backends.base.interfaces import (
    DatastoreBackend,
    DeploymentClient,
    EmbeddingBackend,
    TrainingBackend,
)
from src.backends.deft.deft_client import DeftClient
from src.backends.deft.storage_backend_client import StorageBackendClient
from src.config.deft import SnapshotSelectionConfig
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.backends.deft.wrappers")

#: Default DEFT workflow for the AOI active-learning loop (deft_api/openapi.json).
_DEFAULT_AOI_WORKFLOW = "aoi_active_learning"


class DeftTraining(TrainingBackend):
    """Training via the external DEFT fine-tuning service."""

    def __init__(self, client: DeftClient) -> None:
        self._client = client

    @staticmethod
    def _build_fine_tune_request(
        snapshot_uri: str | None,
        model_name: str | None,
        deft_params: dict[str, Any] | None,
        baseline_checkpoint: str | None = None,
        kpi_set_uri: str | None = None,
    ) -> dict[str, Any]:
        """Assemble a DEFT ``FineTuneRequest`` from the snapshot + model refs.

        ``deft_params`` (``DeftJobConfig.deft``) supplies the DEFT-shaped body: it
        may carry a ``profile`` (+ ``overrides``) or a full ``config`` block. We
        inject the snapshot as ``config.data_pool.path``, the registry model
        name as ``config.model.name`` and the baseline checkpoint (the model
        DEFT fine-tunes FROM) as ``config.model.checkpoint`` when not already
        provided. In profile mode the injected refs go into ``overrides`` — the
        contract's vehicle for partials (a partial ``config`` fails
        ``ExperimentConfig`` validation).

        The assembled request is validated against the contract's required
        fields (``ExperimentConfig`` requires ``model`` + ``data_pool``;
        ``ModelConfig`` requires ``name`` + ``checkpoint``) and raises a clear
        ``ValueError`` instead of letting the real service return an opaque 422.

        ``profile`` and ``config`` are alternative request bodies, not layers: in
        profile mode the whole ``ExperimentConfig`` is assembled server-side, so
        a ``config`` sent alongside is dropped — and warned about, since the run
        then succeeds on the profile's own values with nothing reporting the
        substitution.
        """
        params = dict(deft_params or {})
        request: dict[str, Any] = {}

        profile_mode = bool(params.get("profile"))
        if profile_mode:
            request["profile"] = params["profile"]
            if params.get("workflow"):
                # The profile implies the workflow; an explicit conflicting
                # workflow would be silently misleading — surface it.
                logger.warning(
                    "deft.workflow %r ignored: profile %r implies the workflow",
                    params["workflow"],
                    params["profile"],
                )
            if params.get("config"):
                # Not folded into `overrides` automatically: `config` is a full
                # ExperimentConfig and `overrides` is ExperimentConfigOverrides,
                # so reinterpreting one as the other would send DEFT a body the
                # operator never wrote. Name the remedy instead.
                logger.warning(
                    "deft.config %s ignored: profile %r supplies the complete "
                    "ExperimentConfig server-side. Move any values that must survive to "
                    "deft.overrides (the contract's vehicle for partials), or drop the "
                    "profile and use workflow mode.",
                    sorted(params["config"]) if isinstance(params["config"], dict) else "block",
                    params["profile"],
                )
        else:
            request["workflow"] = params.get("workflow", _DEFAULT_AOI_WORKFLOW)
        if params.get("step_overrides"):
            request["step_overrides"] = params["step_overrides"]
        if params.get("storage_access"):
            request["storage_access"] = params["storage_access"]

        # The snapshot/model/checkpoint refs to inject. Explicit config wins
        # for these — EXCEPT the baseline checkpoint: when the caller passes
        # one it is the resolved iterative-fine-tuning baseline (the promoted
        # @production model) and OVERRIDES the config's day-0 seed; the
        # handler owns that policy and passes None to defer to the config.
        injected: dict[str, Any] = {}
        if snapshot_uri is not None:
            injected.setdefault("data_pool", {})["path"] = snapshot_uri
        if model_name is not None:
            injected.setdefault("model", {})["name"] = model_name
        if kpi_set_uri is not None:
            # The KPI set the candidate is judged on (eval.kpi_set_uri /
            # Storage Backend KPI set) — without this DEFT falls back to the
            # kpi/testing_set.csv baked into the workspace.
            injected.setdefault("kpi", {})["path"] = kpi_set_uri

        if profile_mode:
            # Partials ride on `overrides` (ExperimentConfigOverrides) — never a
            # partial `config`, which would fail ExperimentConfig validation.
            overrides: dict[str, Any] = dict(params.get("overrides") or {})
            for section, values in injected.items():
                merged = dict(overrides.get(section) or {})
                for key, value in values.items():
                    merged.setdefault(key, value)
                overrides[section] = merged
            if baseline_checkpoint is not None:
                model_section = dict(overrides.get("model") or {})
                model_section["checkpoint"] = baseline_checkpoint
                overrides["model"] = model_section
            if overrides:
                request["overrides"] = overrides
            # Early return ON PURPOSE: the required-field validation below is
            # workflow-mode only — in profile mode the PROFILE supplies the
            # complete ExperimentConfig server-side, so a partial here is valid.
            return request

        if params.get("overrides"):
            request["overrides"] = params["overrides"]

        # Build/augment the full config block (explicit values win, except the
        # resolved baseline checkpoint — see the injection comment above).
        config: dict[str, Any] = dict(params.get("config") or {})
        for section, values in injected.items():
            merged = dict(config.get(section) or {})
            for key, value in values.items():
                merged.setdefault(key, value)
            config[section] = merged
        if baseline_checkpoint is not None:
            model_section = dict(config.get("model") or {})
            model_section["checkpoint"] = baseline_checkpoint
            config["model"] = model_section
        if config:
            request["config"] = config

        # Contract validation (workflow mode requires a complete ExperimentConfig).
        missing = []
        if not config.get("data_pool", {}).get("path"):
            missing.append("config.data_pool.path (training snapshot)")
        model = config.get("model") or {}
        if not model.get("name"):
            missing.append("config.model.name (registry model)")
        if not model.get("checkpoint"):
            missing.append(
                "config.model.checkpoint (baseline checkpoint DEFT fine-tunes from — "
                "set deft.config.model.checkpoint or promote a baseline so it can be "
                "resolved from the registry)"
            )
        if missing:
            raise ValueError(
                "FineTuneRequest is missing contract-required fields: " + "; ".join(missing)
            )

        return request

    def start_run(
        self,
        snapshot_uri: str | None = None,
        model_name: str | None = None,
        deft_params: dict[str, Any] | None = None,
        baseline_checkpoint: str | None = None,
        kpi_set_uri: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Launch a DEFT fine-tune and return the ``FineTuneResponse`` handle.

        The handle includes the run ``id`` (used to poll status / fetch results).
        """
        request = self._build_fine_tune_request(
            snapshot_uri, model_name, deft_params, baseline_checkpoint, kpi_set_uri
        )
        return self._client.create_fine_tune(request)

    def get_status(self, run_id: str) -> dict[str, Any]:
        return self._client.get_status(run_id)

    def cancel(self, run_id: str) -> None:
        self._client.cancel(run_id)

    def fetch_artifact(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
        """Return the candidate checkpoint + metrics for a completed run.

        Delegates to ``DeftClient.get_results`` → ``{"checkpoint"/"artifact_uri",
        "metrics", "best_iteration"}``.
        """
        return self._client.get_results(run_id)


# NOTE: there is no DeftEval wrapper. AOI evaluation is the DFW Eval Harness
# running a backend-owned evaluator (src/backends/deft/eval.py); the harness USES
# that evaluator via get_backend_provider().evaluator(use_case=...). The neutral
# EvalBackend (submit/poll/cancel a remote eval job) is an NMP-service shape that
# does not fit the in-process, use-case-plugin AOI model.


class DeftDatastore(DatastoreBackend):
    """Dataset persistence via the AOI Storage Backend (snapshot-by-reference).

    DFW requests an immutable snapshot; it never copies bytes. ``get_uri`` returns
    the snapshot reference handed to DEFT.
    """

    def __init__(self, client: StorageBackendClient, dataset_name: str) -> None:
        self._client = client
        self._dataset_name = dataset_name
        self._snapshot_uri: str | None = None
        self._snapshot_id: str | None = None

    def put(self, *args: Any, **kwargs: Any) -> Any:
        # AOI uploads nothing: records are already in the object store; DFW only
        # *requests* an immutable snapshot by reference (see ``register``).
        raise NotImplementedError("DeftDatastore.put not applicable for AOI")

    def register(
        self,
        selection: SnapshotSelectionConfig,
        reason: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Request an immutable snapshot and stash its id.

        Calls the Storage Backend ``POST /v1/snapshot``. Contract v2 creates
        are ID-only — the real storage prefix comes from the READY detail's
        manifests (``derive_storage_prefix`` in the handler); ``get_uri`` only
        provides the ``snapshot://{id}`` placeholder fallback.
        """
        snapshot = self._client.create_snapshot(selection, reason=reason)
        self._snapshot_id = snapshot.get("snapshot_id")
        self._snapshot_uri = f"snapshot://{self._snapshot_id}" if self._snapshot_id else None
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Fetch current snapshot state (backend-specific: used to poll to ready)."""
        return self._client.get_snapshot(snapshot_id)

    def register_kpi_set(self, reason: str | None = None) -> dict[str, Any]:
        """Request a dedicated KPI set (``POST /v1/kpi``, ``selection={'tag':'kpi'}``).

        Same ID-only / poll-to-ready semantics as ``register`` (a snapshot); the
        READY detail's manifests give the prefix (``derive_storage_prefix``) that
        DFW hands DEFT as ``kpi.path`` (DEFT materializes + uses it as the test set).
        """
        return self._client.create_kpi_set(reason=reason)

    def get_kpi_set(self, kpi_id: str) -> dict[str, Any]:
        """Fetch current KPI-set state (poll to ready).

        Forwards to the client's ``get_kpi_stats`` — the client method keeps the
        Storage Backend's ``GET /v1/kpi/{id}`` (status + ``manifests[]``) naming;
        this wrapper uses ``get_kpi_set`` for symmetry with ``register_kpi_set`` /
        ``get_snapshot``.
        """
        return self._client.get_kpi_stats(kpi_id)

    def get_uri(self) -> str:
        if self._snapshot_uri is None:
            raise RuntimeError(
                "DeftDatastore.get_uri: no snapshot registered — call register() first"
            )
        return self._snapshot_uri

    def delete(self) -> None:
        # Snapshots are immutable, reference-only, and retention-managed by the
        # Storage Backend; DFW does not delete them.
        raise NotImplementedError("DeftDatastore.delete not applicable for AOI (skeleton)")


class DeftDeploymentClient(DeploymentClient):
    """Deployment via the Infer Model Manager.

    NOTE: AOI promotion runs through the Promotion Controller + the
    ``DeploymentBackend`` (``src/lib/promotion/deployment_backend``) that drives the
    Model Manager — not this per-model client. Kept for seam completeness.
    """

    def deploy(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "DeftDeploymentClient.deploy: AOI deploys via the Promotion Controller / "
            "DeploymentBackend, not the provider deployment_client (skeleton)"
        )

    def get_status(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("DeftDeploymentClient.get_status not implemented (skeleton)")

    def wait(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("DeftDeploymentClient.wait not implemented (skeleton)")

    def teardown(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("DeftDeploymentClient.teardown not implemented (skeleton)")


class DeftEmbedding(EmbeddingBackend):
    """Embeddings are not used by the AOI (image) pipeline."""

    def embed(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("DEFT/AOI backend does not provide embeddings")
