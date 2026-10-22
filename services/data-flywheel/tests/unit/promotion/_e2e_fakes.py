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

"""Shared external-surface fakes + the in-process E2E harness for promotion tests.

Extracted VERBATIM from ``test_aoi_e2e_promotion.py`` so ITS can drive the
identical harness against the identical controller. If the two suites needed
different harnesses, the "one pipeline, one config schema" architecture would
already have failed.

The harness carries BOTH axes, and they are independent:

* ``job_type`` — the SUBMISSION edge (``aoi_job`` / ``its_job``): which pipeline
  the dispatcher builds, which ``settings.job_configs`` block is the default.
* ``use_case`` — RUNTIME (``aoi`` / ``its``): evaluator, object store, control
  plane, promotion policy.

Not named ``test_*`` so pytest never collects it; ``harness_factory`` is a
fixture available to any module that imports it. Every use-case seam defaults to
today's AOI value.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.api.promotion.service import PromotionService
from src.config import MLflowConfig, build_job_config, settings
from src.config.deft import AOIJobConfig
from src.job_types import DEFT_JOB_TYPES, JobType
from src.lib.promotion.deployment_backend.model_manager_client import ModelManagerBackend
from src.tasks import deft_tasks
from src.tasks.promotion import controller as controller_mod
from src.tasks.promotion.controller import TERMINAL_STATES, PromotionController
from src.tasks.promotion.hitl_beat import hitl_poll
from src.tasks.promotion.online_eval_beat import online_eval_poll

# F3: two identities. The Mongo-minted serving integer is what reaches the Model
# Manager; the DEFT run id is lineage only, MLflow best-effort tracking.
BASELINE = "1"  # the deployed baseline's serving integer version
CANDIDATE = "2"  # the candidate's serving integer version (minted at register)
CANDIDATE_RUN_ID = "run-777"  # the DEFT run id (lineage → PromotionRecord.deft_run_id)
MODEL = "changenet-aoi"

PASSING_METRICS = {"mAP50": 0.95, "far": 0.0005, "recall": 1.0}
FAILING_METRICS = {"mAP50": 0.80, "far": 0.02, "recall": 0.90}


def _job_config(
    *,
    use_case: str = "aoi",
    model_name: str = MODEL,
    model_format: str = "onnx",
    **overrides,
) -> dict:
    """Build the chain's job_config dict through the REAL AOIJobConfig schema.

    The three keyword seams default to the AOI values, so a no-argument call
    returns the dict the AOI scenarios were written against. ``**overrides``
    replaces whole top-level blocks, which is how the ITS shape is built: ITS is
    a different body on the same schema, not "AOI with a flag".
    """
    data: dict[str, Any] = {
        "use_case": use_case,
        "model_name": model_name,
        # Reaches the API as PullRequest.format, so a wrong value fails minutes
        # later inside the serving stack.
        "model_format": model_format,
        "selection": {
            "mode": "query",
            "filters": {"created_after": "1970-01-01T00:00:00Z"},
        },
        "eval": {
            "kpi_target": [
                {"metric": "far", "op": "<", "threshold": 0.001},
                {"metric": "recall", "op": ">=", "threshold": 1.0},
            ]
        },
        "deft": {
            "config": {
                # First rollout only; later runs resolve from MLflow.
                "model": {"checkpoint": "s3://aoi-models/baseline/model.onnx"},
                # DEFT's own convergence target: an opaque string DEFT parses.
                "pipeline": {"kpi_target": "FAR < 0.1% at recall=100%"},
                "training": {"epochs": 3},
            }
        },
        "promotion_policy": {
            "hitl": {"pull": False, "deploy": False, "canary": False, "full": False},
            "canary_steps": {"steps": [5, 25]},
            # With no criteria the online gate is fail-closed and holds forever.
            "eval_harness": {
                "min_sample_size": 10,
                "online_target": [{"metric": "FAR_pct", "op": "<", "threshold": 5.0}],
            },
            "poll_interval_s": 0.0,
            "poll_max_attempts": 1,
            "auto_rollback_on_regression": True,
        },
    }
    data.update(overrides)
    return AOIJobConfig.from_dict(data).model_dump()


def _raw_job_config_dict(dumped: dict) -> dict:
    """A model_dump'd job config, filtered so from_dict re-validates cleanly."""
    return {k: v for k, v in dumped.items() if v is not None}


def _assert_pipeline_accepts(job_type: str, job_config) -> None:
    """Run the REAL submission-edge gate for ``job_type``, or raise as it would.

    ``run_chain`` enters BELOW ``DeftPipeline.build_workflow`` and therefore
    below ``BasePipeline.validate_config``, the gate every real submission
    clears. Ungated, a harness parameterized on ``job_type`` would drive
    ``"llm_job"`` (or a typo'd ``"its"``) through the DEFT stages and go green
    while production raises ``ValueError`` at pipeline construction.

    Looked up through the dispatcher's own ``_DEFT_PIPELINES`` map, not a
    hardcoded class: the claim is "the pipeline the DISPATCHER would pick serves
    this job type", so that pipeline has to be the one answering. A job type
    absent from the map raises ``ValueError``, as the dispatcher does.

    ``db_manager`` is unused by the gate, hence the mock.
    """
    from src.tasks.tasks import _DEFT_PIPELINES

    pipeline_cls = _DEFT_PIPELINES.get(job_type)
    if pipeline_cls is None:
        raise ValueError(
            f"Job type '{job_type}' has no DEFT pipeline. Registered: {sorted(_DEFT_PIPELINES)}"
        )
    pipeline_cls(db_manager=MagicMock()).validate_config(job_type, job_config)


# -- External-surface fakes (contract-accurate) ----------------------------- #


class FakeModelManagerHTTP:
    """Stateful Model Manager double implementing the Model Promotion API.

    Speaks the exact response shapes ``ModelManagerBackend`` parses (see
    ``aoi-api/model_promotion``): pull → staged; apply → swap; status →
    succeeded; unload/cleanup → removed. Records every request body so tests can
    assert the gate order and idempotency keys the controller sent.
    """

    def __init__(
        self,
        default_version: str | None = BASELINE,
        *,
        serves_state: bool = False,
        name: str = "mm",
    ):
        #: Whether GET /v1/models/{name}/serving-state exists. False by default,
        #: so the suite exercises the older-MM fallback.
        self.serves_state = serves_state
        #: Label and base URL for the deployment's single Model Manager. Requests
        #: are attributed by the model they name; the label only namespaces swap
        #: ids so two fakes cannot answer each other's polls.
        self.name = name
        self.base_url = f"http://{name}:8090"
        self.calls: list[tuple[str, dict | None]] = []
        self.loaded: list[str] = [default_version] if default_version else []
        #: Every version with a directory, INCLUDING unloaded ones — the
        #: repository-wide COLLISION FLOOR. Deriving it from `loaded` would shrink
        #: the floor on unload and hand a later mint a number already on disk.
        self.known_versions: list[str] = list(self.loaded)
        self.default_version = default_version
        self._swap_seq = 0
        #: Live routing. Convergence is evaluated against it, so it is modelled
        #: rather than stubbed.
        self.routing: list[dict] = (
            [{"version": default_version, "role": "default", "weight": 1.0}]
            if default_version
            else []
        )
        #: Distinct from `loaded`.
        self.ready_versions: list[str] = list(self.loaded)
        #: Each apply's `applied` token, so a test can assert what came back.
        self.applied_returned: list[dict] = []
        #: Next apply answers a normal 202 without writing routing, so it never
        #: converges.
        self.fail_next_apply: bool = False
        #: Injected into successive convergence polls, front first: an int is an
        #: HTTP status, an Exception is raised as-is.
        self.convergence_faults: list = []

    def _route(self, control: str, version: str, weight: float | None) -> None:
        """Apply a routing control, the way the real backend would.

        A full switch moves the default and leaves routing exclusive. Shadow and
        canary add an arm beside the default. Unload removes the version from
        what is loaded and ready.
        """
        if version not in self.ready_versions:
            self.ready_versions.append(version)
        if control == "full_switch":
            self.default_version = version
            self.routing = [{"version": version, "role": "default", "weight": 1.0}]
        elif control in ("shadow", "canary"):
            self.routing = [r for r in self.routing if r["version"] != version]
            share = 0.0 if control == "shadow" else float(weight or 0.0)
            if control == "canary":
                # The canary's share comes off the default arm so the split sums
                # to 1.0; a shadow arm takes no traffic.
                for entry in self.routing:
                    if entry["role"] == "default":
                        entry["weight"] = round(1.0 - share, 10)
            self.routing.append({"version": version, "role": control, "weight": share})
        elif control == "unload":
            self.routing = [r for r in self.routing if r["version"] != version]
            for seq in (self.loaded, self.ready_versions):
                if version in seq:
                    seq.remove(version)

    def _is_serving(self, version: str) -> bool:
        """Mirrors ``ModelManager._evict``, which 409s any version routing still names
        -- default, canary or shadow -- and which both unload and cleanup pass through.
        """
        return version == self.default_version or any(r["version"] == version for r in self.routing)

    def _converged(self, expectation: dict) -> dict:
        """Answer a convergence poll from live state only.

        A pure function of the expectation and the current routing, default and
        ready sets. It reads no swap id and stores nothing, which is the
        property under test.
        """
        control = expectation["control"]
        want = expectation["to_version"]
        entry = next((r for r in self.routing if r["version"] == want), None)

        if control == "full_switch":
            ok = self.default_version == want and [r["version"] for r in self.routing] == [want]
            code, msg = (
                "default_version_mismatch",
                f"default is {self.default_version}, want {want}",
            )
        elif control == "shadow":
            ok = entry is not None and entry["role"] == "shadow"
            code, msg = "shadow_absent", f"no shadow arm for {want}"
        elif control == "canary":
            expected_weight = expectation.get("canary_weight")
            ok = (
                entry is not None
                and entry["role"] == "canary"
                and (expected_weight is None or abs(entry["weight"] - expected_weight) < 1e-9)
            )
            code, msg = "canary_absent", f"no canary arm for {want} at {expected_weight}"
        else:  # unload
            ok = want not in self.loaded
            code, msg = "still_resident", f"{want} is still loaded"

        if ok and control != "unload" and want not in self.ready_versions:
            ok, code, msg = False, "candidate_not_ready", f"{want} is not ready"

        # `terminal` is always False; a test wanting that branch sets it itself.
        return {
            "converged": bool(ok),
            "reason": None if ok else {"code": code, "message": msg, "terminal": False},
            # REQUIRED on the real ConvergenceReport. Omit it and the day
            # something reads it the e2e stays green while production raises.
            "observed": self._state_body(expectation["model_name"]),
            "at": "2026-01-01T00:00:00+00:00",  # frozen, like apply's started_at
        }

    def _state_body(self, name: str) -> dict:
        """The serving-state projection, shared with convergence's ``observed``.

        Emitted even when ``serves_state`` is False: that flag models a Model
        Manager with no by-name serving-state ROUTE, which still returns
        ``observed`` on a convergence report.
        """
        return {
            "model_name": name,
            "default_version": self.default_version,
            "routing": list(self.routing),
            "serving_versions": [self.default_version] if self.default_version else [],
            "known_versions": list(self.known_versions),
            "ready_versions": list(self.ready_versions),
            "ready": True,
            "routing_available": True,
            "loaded_available": True,
        }

    def post(self, url: str, json: dict | None = None, timeout: float | None = None):
        path = url.split("//", 1)[-1].split("/", 1)[-1]
        self.calls.append((f"POST /{path}", json))
        if path == "v1/models/pull":
            if json["version"] not in self.loaded:
                self.loaded.append(json["version"])
            # Pulling stages a directory; the slot stays taken after an unload.
            if json["version"] not in self.known_versions:
                self.known_versions.append(json["version"])
            return _resp(
                {
                    "model_name": json["model_name"],
                    "version": json["version"],
                    "repo_path": f"/models/{json['model_name']}/{json['version']}/",
                    "status": "staged",
                }
            )
        if path == "v1/models/apply":
            self._swap_seq += 1
            swap_id = f"swap-{self.name}-{self._swap_seq}"
            landed = not self.fail_next_apply
            self.fail_next_apply = False
            control, version = json["control"], json["version"]
            weight = json.get("canary_weight")
            # Captured BEFORE the switch, or every full switch would report
            # `from_version == to_version`.
            from_version = self.default_version
            if landed:
                self._route(control, version, weight)
            applied = {
                "model_name": json["model_name"],
                "to_version": version,
                "control": control,
                "from_version": from_version,
                "canary_weight": weight,
                "key": json.get("idempotency_key") or f"{json['model_name']}:{version}:{control}",
            }
            self.applied_returned.append(dict(applied))
            return _resp(
                {
                    "swap_id": swap_id,
                    "from_version": from_version,
                    "to_version": version,
                    "control": control,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "applied": applied,
                    "noop": False,
                }
            )
        if path == "v1/models/convergence":
            if self.convergence_faults:
                fault = self.convergence_faults.pop(0)
                if isinstance(fault, Exception):
                    raise fault
                # Empty body: _error_from_response lets a body win, so an
                # envelope here would override the injected status.
                return _resp(None, status=fault)
            return _resp(self._converged(json["expectation"]))
        if path == "v1/models/unload":
            # `_evict` 409s before touching anything; evicting the live default
            # would model a serving stack that cannot exist.
            if self._is_serving(json["version"]):
                return _resp(
                    {
                        "error": {
                            "code": "conflict",
                            "message": "still serving as default/canary/shadow",
                        }
                    },
                    status=409,
                )
            # Drops `loaded`, `ready_versions` and any routing arm. Removing only
            # from `loaded` left the version ready forever, and since `loaded` is
            # not in `_state_body` the unload was invisible on the wire.
            self._route("unload", json["version"], None)
            # `known_versions` untouched: unload frees the GPU, the weights stay
            # on disk, and rollback stays a routing change. No `disk_reclaimed`
            # key either — that is this endpoint's contract.
            return _resp({"version": json["version"], "status": "removed"})
        if path == "v1/models/cleanup":
            # Cleanup evicts through the same `_evict` and inherits the refusal.
            if self._is_serving(json["version"]):
                return _resp(
                    {
                        "error": {
                            "code": "conflict",
                            "message": "still serving as default/canary/shadow",
                        }
                    },
                    status=409,
                )
            self._route("unload", json["version"], None)
            # Cleanup frees the directory, so the floor drops. The monotonic Mongo
            # counter, not the floor, stops a re-mint landing on an occupied one.
            if json["version"] in self.known_versions:
                self.known_versions.remove(json["version"])
            return _resp({"version": json["version"], "status": "removed", "disk_reclaimed": True})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url: str, timeout: float | None = None):
        path = url.split("//", 1)[-1].split("/", 1)[-1]
        self.calls.append((f"GET /{path}", None))
        if path.startswith("v1/models/status/"):
            # Never remembers swap ids: a Model Manager that restarted, or a
            # replica that never saw the apply.
            raise AssertionError(
                f"polled a swap id ({path.rsplit('/', 1)[-1]}); swap state is not "
                "recoverable across a restart -- poll /v1/models/convergence instead"
            )
        if path.startswith("v1/models/") and path.endswith("/serving-state"):
            # An older Model Manager has no such route; `serves_state=False`
            # reproduces that so the compat path stays covered.
            if not self.serves_state:
                return _resp({"error": {"code": "not_found"}}, status=404)
            name = path[len("v1/models/") : -len("/serving-state")]
            # Shared with convergence's `observed` so the two cannot drift apart.
            return _resp(self._state_body(name))
        raise AssertionError(f"unexpected GET {url}")

    def bodies(self, endpoint: str) -> list[dict]:
        return [body for (ep, body) in self.calls if ep == endpoint and body is not None]


def _resp(payload: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.content = b"x"
    resp.json.return_value = payload
    return resp


class FakeMLflow:
    """Stateful alias/tag double for the registry trail.

    MLflow is best-effort (F3) and is NOT the version counter: the serving
    integer is minted from Mongo and passed in as ``serving_version``.
    ``register_candidate`` only records the tracking trail.
    """

    def __init__(
        self,
        model_name: str = MODEL,
        *,
        registry: dict[str, dict] | None = None,
        artifact_root: str = "s3://aoi-artifacts",
    ):
        #: Which model the flat accessors project. Every METHOD keys off its own
        #: ``model_name`` argument, as the real client does.
        self.model_name = model_name
        self.artifact_root = artifact_root.rstrip("/")
        #: model_name -> {"aliases", "tags", "alias_history"}.
        #:
        #: Real MLflow is ONE registry holding MANY models. Two instances sharing
        #: a ``registry`` behave as one registry seen from two models, which lets
        #: a two-use-case test share the one MLflow ``_build_mlflow`` returns.
        #: Flat per-model dicts let an ITS run overwrite AOI's @production tag.
        self.registry: dict[str, dict] = registry if registry is not None else {}

    def _model(self, model_name) -> dict:
        return self.registry.setdefault(
            str(model_name), {"aliases": {}, "tags": {}, "alias_history": []}
        )

    # Flat views on THIS instance's model.
    @property
    def aliases(self) -> dict[str, str]:
        return self._model(self.model_name)["aliases"]

    @property
    def tags(self) -> dict[str, dict[str, str]]:
        return self._model(self.model_name)["tags"]

    @property
    def alias_history(self) -> list[tuple[str, str]]:
        return self._model(self.model_name)["alias_history"]

    def register_candidate(
        self, model_name, deft_run_id, serving_version, artifact_uri, metrics, tags
    ):
        version = str(serving_version)
        self.set_alias(model_name, "candidate", version)
        model_tags = self._model(model_name)["tags"]
        model_tags.setdefault(version, {}).update(tags or {})
        model_tags[version]["promotion.deft_run_id"] = str(deft_run_id)
        model_tags[version]["promotion.version_label"] = version
        return True

    def get_version_by_alias(self, model_name, alias):
        return self._model(model_name)["aliases"].get(alias)

    def get_serving_version_by_alias(self, model_name, alias):
        # F3: the serving integer is on the promotion.version_label tag, not on
        # the alias binding.
        version = self._model(model_name)["aliases"].get(alias)
        if version is None:
            return None
        return self._model(model_name)["tags"].get(version, {}).get("promotion.version_label")

    def get_version_source(self, model_name, version):
        if not version:
            # A None version must not produce a plausible URI and mask a missing
            # baseline.
            raise ValueError(f"No version given for {model_name} source resolution")
        # A checkpoint is only fetchable from the store its training run wrote to.
        return f"{self.artifact_root}/runs/{version}/weights.onnx"

    def set_alias(self, model_name, alias, version):
        model = self._model(model_name)
        model["aliases"][alias] = version
        model["alias_history"].append((alias, version))

    def set_promotion_tags(self, model_name, version, tags):
        self._model(model_name)["tags"].setdefault(version, {}).update(tags or {})

    def get_promotion_tags(self, model_name, version):
        return dict(self._model(model_name)["tags"].get(version, {}))


def _aoi_samples(n: int, *, candidate_correct: bool) -> list[dict]:
    """Collected online samples for the FAR gate.

    The gate is the false-ALARM rate: of the good units, the share the candidate
    flagged as defective. A regressing candidate raises false alarms on good
    units — it does not miss defects (that is recall, which never gates).
    """
    out = []
    for i in range(n):
        actual_defect = i % 2 == 0
        confirmed = "NotGood" if actual_defect else "Good"
        ai = confirmed if candidate_correct else "NotGood"  # bad => FAR 100%
        out.append(
            {
                "target_id": f"R{i}",
                "ai_classification": ai,
                "ai_confidence": 0.95,
                "confirmed_label": confirmed,
                "baseline_classification": confirmed,
            }
        )
    return out


class FakeProvider:
    """Provider-seam double: snapshot + training per contract, REAL evaluator.

    Also implements the capability surface (``kind`` / ``supports_job_type``)
    that the API edge gate and the dispatcher check, so the same provider can
    stand in from ``POST /jobs`` all the way down.
    """

    kind = "deft"
    #: The REAL set, not a copy: restating it as ``{"aoi_job"}`` would disagree
    #: with production in whichever direction the family last moved.
    supported_job_types = DEFT_JOB_TYPES

    def __init__(
        self,
        metrics: dict[str, float],
        online_samples: list[dict],
        *,
        run_id: str = CANDIDATE_RUN_ID,
        artifact_root: str = "s3://aoi-artifacts",
    ):
        self._metrics = metrics
        self.run_id = run_id
        self.artifact_root = artifact_root.rstrip("/")
        #: Read at collect() time, so a test can change what the fleet reports
        #: BETWEEN beat ticks.
        self.online_samples = online_samples

        self.datastore_mock = MagicMock()
        self.datastore_mock.register.return_value = {
            "snapshot_id": "snap-001",
            "status": "ready",
            # Non-empty, or the snapshot stage refuses to start DEFT
            # (job_config.min_snapshot_records).
            "total_records": 3,
            "storage_prefix": f"{self.artifact_root}/snapshots/snap-001/",
        }
        # kpi.pull_from_storage defaults on, so a ready KPI set is needed.
        self.datastore_mock.register_kpi_set.return_value = {
            "kpi_id": "kpi-001",
            "status": "ready",
            "storage_prefix": f"{self.artifact_root}/kpi/kpi-001/",
        }
        self.datastore_mock.get_kpi_set.return_value = {
            "kpi_id": "kpi-001",
            "status": "ready",
            "storage_prefix": f"{self.artifact_root}/kpi/kpi-001/",
        }

        self.training_mock = MagicMock()
        self.training_mock.start_run.return_value = {"id": run_id, "state": "pending"}
        self.training_mock.get_status.return_value = {
            "status": "completed",
            "terminal": True,
            "succeeded": True,
        }
        self.training_mock.fetch_artifact.return_value = {
            "artifact_uri": f"{self.artifact_root}/runs/{run_id}/weights.onnx",
            "checkpoint": f"{self.artifact_root}/runs/{run_id}/weights.onnx",
            "sha256": "a" * 64,
            "metrics": dict(metrics),
        }

    def datastore(self, name, use_case=None):
        # One store, so the double only has to ACCEPT the kwarg the real seam
        # resolves on.
        return self.datastore_mock

    def training(self):
        return self.training_mock

    def evaluator(self, use_case=None):
        # The REAL registry, not a hard-coded class: the claim under test is that
        # a second use case is a second ENTRY there. An unregistered one raises
        # ValueError as production does, which makes the initialize guard testable.
        from src.backends.deft.eval import get_evaluator

        if use_case is None:
            return None
        return get_evaluator(use_case, results_source=lambda window, role: self.online_samples)

    def supports_job_type(self, job_type) -> bool:
        # str-enum members equal their value, so this works for both a JobType
        # and a plain string.
        return job_type in self.supported_job_types


class FakeDeftServiceHTTP:
    """Stateful DEFT fine-tune service double (see ``aoi-api/deft_api/openapi.json``).

    Stands in for ``requests.request`` inside ``DeftClient`` so the REAL client
    (request building, status normalization, results extraction) is exercised.
    The run advances pending → running → completed across status polls.
    """

    def __init__(
        self,
        metrics: dict[str, float],
        *,
        run_id: str = CANDIDATE_RUN_ID,
        final_state: str = "completed",
        artifact_root: str = "s3://aoi-artifacts",
    ):
        self.metrics = dict(metrics)
        self.run_id = run_id
        # The presign and the pull both follow this locator, so it has to name
        # the store that owns THIS use case's artifacts.
        self.artifact_root = artifact_root.rstrip("/")
        self.final_state = final_state  # completed | failed | cancelled
        self.calls: list[tuple[str, str, dict | None]] = []
        self._status_polls = 0

    def request(self, method: str, url: str, *, headers=None, timeout=None, json=None, **kw):
        path = url.split("//", 1)[-1].split("/", 1)[-1]
        self.calls.append((method, f"/{path}", json))
        if method == "POST" and path == "v1/fine-tune":
            return _resp({"id": self.run_id, "state": "pending"})
        if method == "GET" and path == f"v1/fine-tune/{self.run_id}/status":
            self._status_polls += 1
            state = "running" if self._status_polls == 1 else self.final_state
            return _resp({"id": self.run_id, "state": state, "progress": {"current_iteration": 3}})
        if method == "GET" and path == f"v1/fine-tune/{self.run_id}/results":
            return _resp(
                {
                    "id": self.run_id,
                    "best_checkpoint": f"{self.artifact_root}/runs/{self.run_id}/weights.onnx",
                    "best_checkpoint_sha256": "a" * 64,
                    "metrics": dict(self.metrics),
                    "best_iteration": 3,
                }
            )
        raise AssertionError(f"unexpected DEFT call {method} {url}")

    def bodies(self, method: str, path: str) -> list[dict]:
        return [b for (m, p, b) in self.calls if m == method and p == path and b is not None]


class FakeStorageBackendHTTP:
    """Stateful AOI Storage Backend double (``aoi-api/storage-backend`` contract).

    Stands in for ``requests.request`` inside ``StorageBackendClient``; the
    snapshot goes creating → ready across polls, exercising the real client and
    the real ``DeftDatastore`` polling.

    ``base_host`` / ``artifact_root`` let a SECOND instance stand in for a
    sibling use case's store. Getting the routing wrong is not a crash — it is an
    ITS rollout snapshotted from, and graded on, AOI's inspection records — so
    two fakes on two hosts turn that into a routing assertion.
    """

    SNAPSHOT_ID = "snap-001"
    PREFIX = f"s3://aoi-artifacts/snapshots/{SNAPSHOT_ID}/"
    KPI_ID = "kpi-001"
    KPI_PREFIX = f"s3://aoi-artifacts/kpi/{KPI_ID}/"

    def __init__(
        self,
        *,
        base_host: str = "storage-backend",
        artifact_root: str = "s3://aoi-artifacts",
    ):
        self.calls: list[tuple[str, str, dict | None]] = []
        self._polls = 0
        #: The host this store answers on — how the shared ``requests.request``
        #: patch decides which store a call was addressed to.
        self.base_host = base_host
        self.artifact_root = artifact_root.rstrip("/")
        # SHADOWING the class constants: the defaults reproduce them exactly, so
        # AOI scenarios can keep reading ``PREFIX`` off the class.
        self.PREFIX = f"{self.artifact_root}/snapshots/{self.SNAPSHOT_ID}/"
        self.KPI_PREFIX = f"{self.artifact_root}/kpi/{self.KPI_ID}/"
        #: Catalogs actually exercised, so a test can assert ITS was scoped.
        self.seen_catalogs: set[str] = set()
        #: Re-assignable: a selection matching nothing comes back READY with 0
        #: records and no manifests, which is a scenario, not a bad response.
        self.snapshot_records = 3

    #: Catalogs the real backend serves. Snapshot and KPI CREATION are scoped
    #: ``/v1/{catalog}/...``; unprefixed paths stay aliases resolving to AOI, so
    #: ``/v1/aoi/...`` 404s on a pre-catalog backend and fails here too.
    CATALOGS = ("its",)

    def _strip_catalog(self, path: str) -> str:
        """Normalise ``v1/{catalog}/rest`` to ``v1/rest``, recording the catalog."""
        for catalog in self.CATALOGS:
            prefix = f"v1/{catalog}/"
            if path.startswith(prefix):
                self.seen_catalogs.add(catalog)
                return f"v1/{path[len(prefix) :]}"
        return path

    def request(self, method: str, url: str, *, headers=None, timeout=None, json=None, **kw):
        raw = url.split("//", 1)[-1].split("/", 1)[-1]
        self.calls.append((method, f"/{raw}", json))
        path = self._strip_catalog(raw)
        if method == "POST" and path == "v1/snapshot":
            return _resp({"snapshot_id": self.SNAPSHOT_ID})  # contract v2: id only
        if method == "GET" and path == f"v1/snapshot/{self.SNAPSHOT_ID}":
            self._polls += 1
            status = "creating" if self._polls == 1 else "ready"
            return _resp(
                {
                    "snapshot_id": self.SNAPSHOT_ID,
                    "status": status,
                    "total_records": self.snapshot_records,
                    # ABSOLUTE URI: DFW takes the manifest's parent as the storage
                    # prefix. An empty snapshot carries no manifests.
                    "manifests": [
                        {
                            "format": "jsonl",
                            "uri": f"{self.PREFIX}manifest0.jsonl",
                            "size_bytes": 1,
                            "sha256": "0" * 64,
                        }
                    ]
                    if self.snapshot_records
                    else [],
                }
            )
        if method == "POST" and path == "v1/kpi":
            # Create-then-poll, same wire contract as a snapshot.
            return _resp({"kpi_id": self.KPI_ID})
        if method == "GET" and path == f"v1/kpi/{self.KPI_ID}":
            return _resp(
                {
                    "kpi_id": self.KPI_ID,
                    "status": "ready",
                    "manifests": [
                        {
                            "format": "jsonl",
                            "uri": f"{self.KPI_PREFIX}manifest0.jsonl",
                            "size_bytes": 1,
                            "sha256": "0" * 64,
                        }
                    ],
                }
            )
        if method == "POST" and path == "v1/get_presigned_urls":
            # A short-lived credential-free link per locator, threaded through
            # StartPromotion as the pull URI.
            urls = (json or {}).get("urls") or []
            return _resp(
                {
                    "items": [
                        {
                            "input": u,
                            "key": u.split("://", 1)[-1].split("/", 1)[-1] if "://" in u else u,
                            "presigned_url": (
                                f"https://presigned.example.com/{u.rsplit('/', 1)[-1]}"
                                "?X-Amz-Signature=sig"
                            ),
                            "expires_at": "2026-07-22T01:00:00Z",
                        }
                        for u in urls
                    ],
                    "failed": [],
                }
            )
        raise AssertionError(f"unexpected Storage Backend call {method} {url}")

    def bodies(self, method: str, path: str) -> list[dict]:
        return [b for (m, p, b) in self.calls if m == method and p == path and b is not None]


# -- Harness ----------------------------------------------------------------- #


class E2EHarness:
    """Wires the fakes into every seam and simulates the worker + beats.

    ``real_deft_backend=True`` swaps the provider-seam double for the REAL
    ``DeftBackendProvider`` (real ``DeftTraining``/``DeftDatastore`` wrappers +
    real ``DeftClient``/``StorageBackendClient``) over the DEFT/Storage HTTP
    fakes — only the evaluator's collected-sample source is injected (its
    object-store read is a marked TODO in the backend).

    USE-CASE SEAMS. ``job_type`` / ``use_case`` / ``model_name`` /
    ``model_format`` / ``baseline_version`` / ``run_id`` / ``artifact_root`` /
    ``storage_host`` / ``mm_name`` / ``mlflow_registry`` all default to the AOI
    values, so a no-argument harness is the one the AOI scenarios were written
    against. Two harnesses drift silently; one harness driven twice cannot.

    ``job_type`` and ``use_case`` are separate seams because they are separate
    axes. Nothing here derives one from the other.
    """

    def __init__(
        self,
        fake_db,
        *,
        metrics=None,
        online_samples=None,
        policy_overrides=None,
        real_deft_backend=False,
        job_type: str | JobType = JobType.AOI,
        use_case: str = "aoi",
        model_name: str = MODEL,
        model_format: str = "onnx",
        baseline_version: str = BASELINE,
        run_id: str = CANDIDATE_RUN_ID,
        artifact_root: str = "s3://aoi-artifacts",
        storage_host: str = "storage-backend",
        mm_name: str = "mm",
        mlflow_registry: dict[str, dict] | None = None,
    ):
        self.db = fake_db
        #: The WIRE string even when a ``JobType`` member is passed: it becomes a
        #: JSON body and a ``settings.job_configs`` key, which production only
        #: ever carries as a plain string.
        self.job_type = getattr(job_type, "value", job_type)
        self.use_case = use_case
        self.model_name = model_name
        self.model_format = model_format
        self.baseline_version = baseline_version
        #: What register mints, given the counter seed below and a Model Manager
        #: that cannot report serving state.
        self.candidate_version = str(int(baseline_version) + 1)
        self.run_id = run_id
        self.artifact_root = artifact_root.rstrip("/")
        self.mm = FakeModelManagerHTTP(baseline_version, name=mm_name)
        # Seeded to the deployed baseline so the next mint is the next integer —
        # what a brownfield deployment already serving version N does.
        self.db.serving_version_counters.insert_one(
            {"_id": model_name, "seq": int(baseline_version)}
        )
        self.mlflow = FakeMLflow(
            model_name, registry=mlflow_registry, artifact_root=self.artifact_root
        )
        self.mlflow.set_alias(model_name, "production", baseline_version)  # deployed baseline
        # F3: stamp the version_label tag so @production resolves to the serving
        # integer, not the raw MLflow alias number.
        self.mlflow.set_promotion_tags(
            model_name, baseline_version, {"promotion.version_label": baseline_version}
        )
        self.real_deft_backend = real_deft_backend
        samples = (
            online_samples
            if online_samples is not None
            else _aoi_samples(200, candidate_correct=True)
        )
        self.deft_http = FakeDeftServiceHTTP(
            metrics or PASSING_METRICS, run_id=run_id, artifact_root=self.artifact_root
        )
        self.storage_http = FakeStorageBackendHTTP(
            base_host=storage_host, artifact_root=self.artifact_root
        )
        if real_deft_backend:
            from src.backends.deft.eval import get_evaluator
            from src.backends.deft.provider import DeftBackendProvider

            harness = self

            class _SampledDeftProvider(DeftBackendProvider):
                # Everything real except the online-sample source; the evaluator
                # CLASS still comes from the real registry.
                def evaluator(self, use_case=None):
                    if use_case is None:
                        return None
                    return get_evaluator(
                        use_case, results_source=lambda window, role: harness.online_samples
                    )

            self.provider = _SampledDeftProvider()
        else:
            self.provider = FakeProvider(
                metrics or PASSING_METRICS,
                samples,
                run_id=run_id,
                artifact_root=self.artifact_root,
            )
        #: Read at collect() time by BOTH provider flavours.
        self.online_samples = samples
        self.flywheel_db = MagicMock()
        # A bare MagicMock would be truthy here, i.e. cancelled.
        self.flywheel_db.is_flywheel_run_cancelled.return_value = False
        self.job_config = _job_config(
            use_case=use_case,
            model_name=model_name,
            model_format=model_format,
            **(policy_overrides or {}),
        )
        #: Built through ``build_job_config``, not ``DeftJobConfig.from_dict``:
        #: only that door STAMPS ``_config_type``, and since ``aoi_job``/``its_job``
        #: share a class an unstamped ``its_job`` classifies back as ``aoi_job``.
        self.typed_job_config = build_job_config(
            self.job_type, _raw_job_config_dict(self.job_config)
        )
        # run_chain enters below build_workflow, so this is the only place a
        # harness job_type meets the gate a real submission clears.
        _assert_pipeline_accepts(self.job_type, self.typed_job_config)
        self.beat_ticks = 0
        # Off the SAME built config the dispatcher would resolve.
        self.policy = self.typed_job_config.promotion_policy
        self.adv = MagicMock()
        self.rb = MagicMock()
        self._adv_seen = 0
        self._rb_seen = 0

    @staticmethod
    def _mm_for(
        family: Sequence[E2EHarness], url: str, body: dict | None = None
    ) -> FakeModelManagerHTTP:
        """The Model Manager fake holding the state this request is about.

        One control plane fronts every use case, so a request is attributed by
        the model it names — in the body, the serving-state path, or a
        convergence expectation. Attributing by the request, not by whichever
        harness installed the patch, catches an ITS apply carrying AOI's model
        name. An unattributable call is an error, never a fallback.
        """
        model = (body or {}).get("model_name")
        if model is None and url.endswith("/serving-state"):
            model = url.split("/v1/models/", 1)[-1][: -len("/serving-state")]
        if model is None and isinstance((body or {}).get("expectation"), dict):
            # A convergence poll names its model inside the expectation.
            model = body["expectation"].get("model_name")
        if model is not None:
            for harness in family:
                if harness.model_name == model:
                    return harness.mm
            raise AssertionError(
                f"Model Manager call for an unknown model {model!r} "
                f"(known: {[h.model_name for h in family]})"
            )
        raise AssertionError(f"unattributable Model Manager call: {url}")

    def set_online_samples(self, samples) -> None:
        """Change what the live fleet is reporting, mid-rollout.

        The online gate is re-evaluated on EVERY beat tick, e.g. a
        camera-coverage hold that clears once a second camera reports. Keeps the
        harness's copy and the seam double's copy from diverging.
        """
        self.online_samples = list(samples)
        if isinstance(self.provider, FakeProvider):
            self.provider.online_samples = self.online_samples

    def patches(self, *, also: Sequence[E2EHarness] = (), provider: Any = None):
        """Every seam, patched once.

        ``also`` / ``provider`` are the TWO-USE-CASE seams, and both default to
        today's single-harness behaviour:

        - ``also`` names sibling harnesses this ONE patch stack must also serve.
          ``patch`` is last-writer-wins, so a second harness entering its own
          stack would steal the first's fakes. Every dispatcher below is
          table-driven over the family, keyed by model name or host.
        - ``provider`` overrides the backend provider, because
          ``src/backends/factory.py`` caches exactly ONE per process. A test
          giving each use case its own would test an architecture DFW lacks.
        """
        family: list[E2EHarness] = [self, *also]

        # One backend for the family: a deployment fronts one serving stack.
        backend = ModelManagerBackend(
            {"base_url": self.mm.base_url, "timeout": 5, "max_retries": 1}
        )

        # host -> store. Distinct hosts make "the ITS snapshot came from the ITS
        # store" checkable; a shared host merges them silently.
        stores = {h.storage_http.base_host: h.storage_http for h in family}

        def _route_deft_request(method, url, **kwargs):
            for host, store in stores.items():
                if host in url:
                    return store.request(method, url, **kwargs)
            # Not a store: the DEFT fine-tune service, one per deployment, so it
            # is NOT routed per use case.
            return self.deft_http.request(method, url, **kwargs)

        # Patched even in the seam-level tests: StartPromotion presigns the pull
        # artifact through the REAL StorageBackendClient.
        deft_http_patches = [patch("requests.request", side_effect=_route_deft_request)]

        def _mm_post(url, **kwargs):
            return self._mm_for(family, url, kwargs.get("json")).post(url, **kwargs)

        def _mm_get(url, **kwargs):
            return self._mm_for(family, url).get(url, **kwargs)

        return [
            *deft_http_patches,
            # Enabled so the alias trail is exercised; the unit env is off.
            patch.object(settings.system_config, "mlflow_config", MLflowConfig(enabled=True)),
            # Provider seam, resolved lazily from factory. ONE per process.
            patch(
                "src.backends.factory.get_backend_provider",
                return_value=provider if provider is not None else self.provider,
            ),
            patch("src.api.db_manager.get_db_manager", return_value=self.flywheel_db),
            # One stateful MLflow fake for the register stage AND the controller.
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=self.mlflow),
            patch.object(controller_mod, "_get_db", return_value=self.db),
            patch.object(PromotionController, "_load_policy", staticmethod(lambda: self.policy)),
            patch.object(PromotionController, "_build_backend", staticmethod(lambda: backend)),
            # Outside the controller too: the register stage reads serving state
            # through the package-level builder.
            patch(
                "src.lib.promotion.deployment_backend.build_deployment_backend",
                lambda *a, **k: backend,
            ),
            # ONE MLflow object for every model: `_build_mlflow` takes no argument
            # and could not be dispatched per use case anyway.
            patch.object(PromotionController, "_build_mlflow", staticmethod(lambda: self.mlflow)),
            patch(
                "src.lib.promotion.deployment_backend.model_manager_client.requests.post",
                side_effect=_mm_post,
            ),
            patch(
                "src.lib.promotion.deployment_backend.model_manager_client.requests.get",
                side_effect=_mm_get,
            ),
            # Enqueues captured here; drained by drive().
            patch("src.tasks.promotion.tasks.advance_promotion", self.adv),
            patch("src.tasks.promotion.tasks.rollback_promotion", self.rb),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ]

    def api_patches(self):
        """Extra seams for driving the flow from the REAL ``POST /jobs`` endpoint.

        Adds the API-edge + dispatcher surfaces on top of :meth:`patches`: the
        backend gate's module-bound provider lookup, the API's Mongo handle, the
        Celery enqueue capture, the dispatcher's db_manager global, and the
        default job_configs entry ``load_job_configuration`` resolves.
        """
        from bson import ObjectId

        from src.config import settings

        self.api_mongo = MagicMock()
        self.api_mongo.flywheel_runs.insert_one.return_value = MagicMock(
            inserted_id=ObjectId("507f1f77bcf86cd799439011")
        )
        self.dispatcher_db = MagicMock()
        self.dispatcher_db.get_flywheel_run.return_value = {
            "_id": ObjectId("507f1f77bcf86cd799439011"),
            "status": "pending",
        }
        self.enqueued_dag = MagicMock()  # captures run_nim_workflow_dag.delay(...)
        return [
            # The API gate binds get_backend_provider at module import.
            patch("src.api.backend_gate.get_backend_provider", return_value=self.provider),
            patch("src.api.endpoints.get_db", return_value=self.api_mongo),
            patch("src.api.endpoints.run_nim_workflow_dag", self.enqueued_dag),
            patch("src.tasks.tasks.db_manager", self.dispatcher_db),
            # Keyed by THIS harness's job type, so an its_job submission resolves
            # the its_job block as load_deft_settings lays it out.
            patch.dict(settings.job_configs, {self.job_type: self.typed_job_config}),
        ]

    def run_from_api(self) -> tuple[Any, dict]:
        """POST /jobs, then execute the enqueued dispatcher exactly as a worker.

        The dispatcher's Celery chain runs EAGERLY in-process with real task
        objects and real link passing, covering the untrimmed path: endpoint gate
        → FlywheelRun insert → run_nim_workflow_dag → pipeline selection →
        7-stage chain → promotion handoff.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.endpoints import router
        from src.celery_app import celery_app
        from src.tasks.tasks import run_nim_workflow_dag

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            "/jobs",
            json={"workload_id": "w1", "client_id": "c1", "job_type": self.job_type},
        )
        if response.status_code != 200:
            return response, {}

        assert self.enqueued_dag.delay.call_count == 1
        dag_kwargs = self.enqueued_dag.delay.call_args.kwargs
        old_eager = celery_app.conf.task_always_eager
        old_prop = celery_app.conf.task_eager_propagates
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        try:
            ctx = run_nim_workflow_dag(**dag_kwargs)
        finally:
            celery_app.conf.task_always_eager = old_eager
            celery_app.conf.task_eager_propagates = old_prop
        return response, ctx

    def run_chain(self) -> dict:
        """Run the 7 stages in chain order (what /jobs ➜ the DEFT pipeline executes)."""
        ctx = deft_tasks.initialize_aoi_workflow(
            workload_id="w1",
            flywheel_run_id="507f1f77bcf86cd799439011",
            client_id="c1",
            job_type=self.job_type,
            job_config=self.job_config,
        )
        ctx = deft_tasks.select_and_snapshot(ctx)
        ctx = deft_tasks.run_deft_training(ctx)
        ctx = deft_tasks.run_offline_eval_gate(ctx)
        ctx = deft_tasks.register_candidate(ctx)
        ctx = deft_tasks.start_promotion(ctx)
        return deft_tasks.finalize_aoi_run(ctx)

    def _drain_queues(self) -> bool:
        """Execute enqueued advance/rollback tasks exactly as a worker would."""
        progressed = False
        while self._adv_seen < len(self.adv.delay.call_args_list):
            pid = self.adv.delay.call_args_list[self._adv_seen].args[0]
            self._adv_seen += 1
            PromotionController().advance(pid)
            progressed = True
        while self._rb_seen < len(self.rb.delay.call_args_list):
            pid = self.rb.delay.call_args_list[self._rb_seen].args[0]
            self._rb_seen += 1
            PromotionController().rollback(pid)
            progressed = True
        return progressed

    def record(self, promotion_id: str):
        return PromotionService(self.db).get(promotion_id)

    def drive(self, promotion_id: str, *, hitl: bool = False, max_ticks: int = 30):
        """Alternate worker drains and beat ticks until a terminal state."""
        for _ in range(max_ticks):
            progressed = self._drain_queues()
            record = self.record(promotion_id)
            if record.state in TERMINAL_STATES:
                return record
            if record.state == "pending_approval":
                if not hitl:
                    return record  # caller inspects/acts
                hitl_poll()
                continue
            # Shadow/canary promotions get a verdict, and a decisive one enqueues
            # the next advance.
            ticked = online_eval_poll()
            self.beat_ticks += 1
            if not progressed and not ticked.get("checked"):
                return record  # stalled — let the test assert why
        return self.record(promotion_id)

    def resolve_promotion_synchronously(self, *, hitl: bool = False) -> None:
        """Make captured advance enqueues drive the promotion to terminal inline.

        ``run_from_api`` ends with ``wait_for_promotion``, which polls the record
        in-process until terminal — but ``advance_promotion.delay`` is an inert
        MagicMock :meth:`drive` only drains AFTER the chain returns, so the wait
        would spin forever. So the first enqueue runs :meth:`drive` inline. The
        reentrancy guard lets the controller's own ``_enqueue_advance`` calls
        merely record; :meth:`_drain_queues` consumes them.
        """
        driving = False

        def _drive_on_enqueue(promotion_id, *_args, **_kwargs):
            nonlocal driving
            if driving:
                return  # reentrant enqueue: recorded now, drained by the loop
            driving = True
            try:
                self.drive(promotion_id, hitl=hitl)
            finally:
                driving = False

        self.adv.delay.side_effect = _drive_on_enqueue


@pytest.fixture
def harness_factory(fake_db):
    def _make(**kwargs) -> E2EHarness:
        return E2EHarness(fake_db, **kwargs)

    return _make


def _run(harness: E2EHarness, **drive_kwargs):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in harness.patches():
            stack.enter_context(p)
        ctx = harness.run_chain()
        record = harness.drive(ctx["promotion_id"], **drive_kwargs)
        return ctx, record
