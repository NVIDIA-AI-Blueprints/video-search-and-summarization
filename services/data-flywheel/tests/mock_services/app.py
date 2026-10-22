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
"""Mock AOI-loop microservices for DFW deployment testing.

One FastAPI app hosting the four externally-delivered services DFW consumes
(per the DEFT POR "Updated Arch" slide), each under a path prefix so a single
process/container stands in for the whole loop:

    /storage  → AOI Storage Backend   (contract v2: POST /v1/snapshot,
                                       GET /v1/snapshot/{id}, GET /v1/snapshot/stats,
                                       POST /v1/results + GET /v1/eval/{id},
                                       POST /v1/kpi + GET /v1/kpi/{id})
    /deft     → DEFT fine-tune service (POST /v1/fine-tune, GET status/results,
                                       POST cancel)
    /mm       → Infer Model Manager    (POST /v1/models/pull|apply|cleanup,
                                       POST /v1/models/convergence)
    /aoi      → AOI Client results     (GET /v1/results — secondary-inspection
                                       samples with operator-confirmed labels)

Behavior is stateful and contract-accurate (aoi-api openapi specs): snapshots go
creating→ready across polls, DEFT runs go pending→running→terminal, Model
Manager tracks routing/loaded versions and honors idempotency keys.

Scenario knobs (env, or POST /admin/scenario):
    MOCK_TRAINING_OUTCOME   completed | failed | cancelled      (default completed)
    MOCK_METRICS            pass | fail                          (default pass)
    MOCK_ONLINE_BEHAVIOR    advance | regress                    (default advance)

Debug: GET /state dumps everything; GET /healthz for readiness.

Run:  uv run python -m tests.mock_services  (MOCK_PORT, default 9000)
"""

from __future__ import annotations

import itertools
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

PASS_METRICS = {"mAP50": 0.95, "far": 0.0005, "recall": 1.0}
FAIL_METRICS = {"mAP50": 0.80, "far": 0.02, "recall": 0.90}
# Meets the KPI constraints (far < 0.001, recall >= 1.0) but is WORSE than a
# previously-promoted baseline that recorded PASS_METRICS — exercises that the
# offline gate is KPI-only (candidate-only DEFT metrics; no DFW-side baseline
# comparison), so a second-generation run like this must still roll out.
WORSE_METRICS = {"mAP50": 0.90, "far": 0.0009, "recall": 1.0}

#: Polls before a snapshot turns ready / a DEFT run turns terminal.
SNAPSHOT_READY_AFTER = 1
TRAINING_DONE_AFTER = 2

#: DEFT's per-model best decision threshold reported in fine-tune results.
DEFT_BEST_THRESHOLD = 0.42

#: Content digest of the best checkpoint reported in fine-tune results, so the
#: DEFT -> DFW -> Model Manager sha256 threading is exercised end-to-end (MM
#: verifies PullRequest.sha256 after download). Deterministic placeholder.
DEFT_CHECKPOINT_SHA256 = "a" * 64


class LoopState:
    """All mutable state for one simulated AOI loop."""

    def __init__(self) -> None:
        self.scenario = {
            "training_outcome": os.environ.get("MOCK_TRAINING_OUTCOME", "completed"),
            "metrics": os.environ.get("MOCK_METRICS", "pass"),  # pass | fail | worse
            "online_behavior": os.environ.get("MOCK_ONLINE_BEHAVIOR", "advance"),
            # Samples returned by /aoi/v1/results; below the evaluator's
            # min_sample_size (100) the online verdict HOLDS.
            "online_samples": int(os.environ.get("MOCK_ONLINE_SAMPLES", "200")),
            # When true, every /deft endpoint returns 503 (service outage).
            "deft_outage": False,
        }
        self.snapshots: dict[str, dict] = {}
        self.eval_sets: dict[str, dict] = {}
        self.kpi_sets: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.swaps: dict[str, dict] = {}
        self.idempotency: dict[str, str] = {}  # idempotency_key -> swap_id
        self.loaded_versions: list[str] = ["run-baseline"]
        #: Versions the serving stack would call ready. Tracked separately from
        #: loaded_versions so convergence can check readiness on its own.
        self.ready_versions: list[str] = ["run-baseline"]
        self.default_version: str = "run-baseline"
        self.routing: list[dict] = []
        self.calls: list[dict] = []
        # Per-server-instance tag so ids never collide across restarts (a DEFT
        # run id doubles as the model version label registered in MLflow).
        self.tag = uuid.uuid4().hex[:4]
        self._snap_seq = itertools.count(1)
        self._run_seq = itertools.count(1)
        self._swap_seq = itertools.count(1)
        self._eval_seq = itertools.count(1)
        self._kpi_seq = itertools.count(1)

    def record(self, service: str, method: str, path: str, body: Any = None) -> None:
        self.calls.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "service": service,
                "call": f"{method} {path}",
                "body": body,
            }
        )

    def metrics(self) -> dict:
        table = {"pass": PASS_METRICS, "fail": FAIL_METRICS, "worse": WORSE_METRICS}
        return dict(table.get(self.scenario["metrics"], PASS_METRICS))


state = LoopState()
app = FastAPI(title="AOI-loop mock microservices")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "scenario": state.scenario}


@app.get("/state")
def dump_state() -> dict:
    return {
        "scenario": state.scenario,
        "snapshots": state.snapshots,
        "runs": {
            k: {kk: vv for kk, vv in v.items() if kk != "polls"} for k, v in state.runs.items()
        },
        "default_version": state.default_version,
        "loaded_versions": state.loaded_versions,
        "routing": state.routing,
        "swaps": state.swaps,
        "calls": state.calls,
    }


@app.post("/admin/scenario")
async def set_scenario(request: Request) -> dict:
    state.scenario.update(await request.json())
    return {"scenario": state.scenario}


@app.post("/admin/reset")
def reset() -> dict:
    global state
    scenario = dict(state.scenario)
    # Preserve id counters across resets so snapshot/run/swap ids stay unique
    # per server lifetime — otherwise scenario runs collide (e.g. two distinct
    # MLflow versions both labeled run-001).
    counters = (state._snap_seq, state._run_seq, state._swap_seq, state._eval_seq, state._kpi_seq)
    tag = state.tag
    state = LoopState()
    state.scenario = scenario
    state._snap_seq, state._run_seq, state._swap_seq, state._eval_seq, state._kpi_seq = counters
    state.tag = tag
    return {"status": "reset", "scenario": state.scenario}


# --------------------------------------------------------------------------- #
# /storage — AOI Storage Backend (aoi-api/storage-backend/openapi.json)
# --------------------------------------------------------------------------- #


@app.get("/storage/v1/snapshot/stats")
def storage_live_stats() -> dict:
    # Contract v2 LiveCatalogStats. NOTE: declared BEFORE /v1/snapshot/{id}
    # so "stats" is never matched as a snapshot id.
    state.record("storage", "GET", "/v1/snapshot/stats")
    return {
        "status": "ready",
        "total_records": 5000,
        "records_since_last_snapshot": 1200,
        "bytes_since_last_snapshot": 987654321,
        "class_counts": {"Good": 4200, "NotGood": 800},
        "last_snapshot_id": None,
        "last_snapshot_created_at": None,
    }


@app.post("/storage/v1/snapshot", status_code=201)
async def create_snapshot(request: Request) -> dict:
    body = await request.json()
    state.record("storage", "POST", "/v1/snapshot", body)
    snapshot_id = f"snap-{state.tag}-{next(state._snap_seq):03d}"
    snap = {
        "snapshot_id": snapshot_id,
        "status": "creating",
        "total_records": 1200,
        "records_since_last_snapshot": 0,
        "bytes_since_last_snapshot": 0,
        "class_counts": {"Good": 1000, "NotGood": 200},
        "last_snapshot_id": None,
        "last_snapshot_created_at": None,
        # SetManifest entries with ABSOLUTE URIs — the prefix DFW derives
        # (manifest parent) is s3://aoi-artifacts/snapshots/<id>/.
        "manifests": [
            {
                "format": "jsonl",
                "uri": f"s3://aoi-artifacts/snapshots/{snapshot_id}/manifest0.jsonl",
                "size_bytes": 1048576,
                "sha256": "0" * 64,
            }
        ],
        "polls": 0,
    }
    state.snapshots[snapshot_id] = snap
    # Contract v2: the create response is ID-only.
    return {"snapshot_id": snapshot_id}


@app.get("/storage/v1/snapshot/{snapshot_id}")
def get_snapshot(snapshot_id: str):
    state.record("storage", "GET", f"/v1/snapshot/{snapshot_id}")
    snap = state.snapshots.get(snapshot_id)
    if snap is None:
        return JSONResponse(status_code=404, content={"detail": "snapshot not found"})
    snap["polls"] += 1
    if snap["status"] == "creating" and snap["polls"] >= SNAPSHOT_READY_AFTER:
        snap["status"] = "ready"
    return {k: v for k, v in snap.items() if k != "polls"}


# --------------------------------------------------------------------------- #
# /deft — DEFT fine-tune service (aoi-api/deft_api/openapi.json)
# --------------------------------------------------------------------------- #


# 201 per the contract: deft_api/openapi.json declares 201 (not 202) as the
# success response for POST /v1/fine-tune.
@app.post("/deft/v1/fine-tune", status_code=201)
async def create_fine_tune(request: Request):
    body = await request.json()
    state.record("deft", "POST", "/v1/fine-tune", body)
    # Contract validation (mirrors deft_api FineTuneRequest / ExperimentConfig /
    # ModelConfig required fields) — a request the real service would 422 must
    # fail here too, or the e2e tests are blind to contract drift.
    if not body.get("profile"):
        errors = []
        config = body.get("config") or {}
        if not (config.get("data_pool") or {}).get("path"):
            errors.append({"loc": ["config", "data_pool", "path"], "msg": "field required"})
        model = config.get("model") or {}
        if not model.get("name"):
            errors.append({"loc": ["config", "model", "name"], "msg": "field required"})
        if not model.get("checkpoint"):
            errors.append({"loc": ["config", "model", "checkpoint"], "msg": "field required"})
        if errors:
            return JSONResponse(status_code=422, content={"detail": errors})
    run_id = f"run-{state.tag}-{next(state._run_seq):03d}"
    state.runs[run_id] = {
        "id": run_id,
        "state": "pending",
        "request": body,
        "polls": 0,
        "convergence_reason": None,
    }
    return {"id": run_id, "state": "pending"}


@app.get("/deft/v1/fine-tune/{run_id}/status")
def fine_tune_status(run_id: str):
    state.record("deft", "GET", f"/v1/fine-tune/{run_id}/status")
    if state.scenario.get("deft_outage"):
        return JSONResponse(status_code=503, content={"detail": "simulated DEFT outage"})
    run = state.runs.get(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"detail": "run not found"})
    run["polls"] += 1
    if run["state"] in ("pending", "running"):
        if run["polls"] >= TRAINING_DONE_AFTER:
            run["state"] = state.scenario["training_outcome"]
            if run["state"] == "completed":
                run["convergence_reason"] = (
                    "kpi_met" if state.scenario["metrics"] == "pass" else "max_iterations_reached"
                )
        else:
            run["state"] = "running"
    return {
        "id": run_id,
        "state": run["state"],
        "progress": {"current_iteration": min(run["polls"], 3), "total_iterations": 3},
        "convergence_reason": run["convergence_reason"],
    }


@app.get("/deft/v1/fine-tune/{run_id}/results")
def fine_tune_results(run_id: str):
    state.record("deft", "GET", f"/v1/fine-tune/{run_id}/results")
    run = state.runs.get(run_id)
    if run is None or run["state"] != "completed":
        return JSONResponse(status_code=409, content={"detail": "run not completed"})
    return {
        "id": run_id,
        "best_checkpoint": f"s3://aoi-artifacts/runs/{run_id}/weights.onnx",
        # Content digest of the checkpoint, surfaced so the value flows
        # DEFT -> DFW -> Model Manager and is verified after download (MM
        # requires PullRequest.sha256; without it pull 409s at checksum).
        "best_checkpoint_sha256": DEFT_CHECKPOINT_SHA256,
        "best_iteration": 3,
        "metrics": state.metrics(),
        # DEFT's per-model best decision threshold, surfaced so the value
        # flows DEFT -> DFW -> Model Manager -> metadata.json sidecar e2e.
        "threshold": DEFT_BEST_THRESHOLD,
        "convergence_reason": run["convergence_reason"],
    }


@app.post("/deft/v1/fine-tune/{run_id}/cancel")
def cancel_fine_tune(run_id: str):
    state.record("deft", "POST", f"/v1/fine-tune/{run_id}/cancel")
    run = state.runs.get(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"detail": "run not found"})
    run["state"] = "cancelled"
    return {"id": run_id, "state": "cancelled"}


# --------------------------------------------------------------------------- #
# /mm — Infer Model Manager (Model Promotion API)
# --------------------------------------------------------------------------- #


@app.post("/mm/v1/models/pull", status_code=201)
async def mm_pull(request: Request) -> dict:
    body = await request.json()
    state.record("mm", "POST", "/v1/models/pull", body)
    version = body["version"]
    if version not in state.loaded_versions:
        state.loaded_versions.append(version)
    # pull stages the weights and waits out the engine build, so a pulled
    # version is ready when it returns.
    if version not in state.ready_versions:
        state.ready_versions.append(version)
    response = {
        "model_name": body["model_name"],
        "version": version,
        "repo_path": f"/models/{body['model_name']}/{version}/",
        "status": "staged",
    }
    # Optional DEFT best threshold (backward compatible): when the caller
    # threads it through the pull contract, echo it back so the e2e run can
    # assert the value reached the Model Manager (which persists it as a
    # metadata.json sidecar in the real service).
    threshold = body.get("threshold")
    if threshold is not None:
        response["threshold"] = threshold
    return response


@app.post("/mm/v1/models/apply", status_code=202)
async def mm_apply(request: Request) -> dict:
    body = await request.json()
    state.record("mm", "POST", "/v1/models/apply", body)
    key = body.get("idempotency_key")
    # Idempotent replay: the same key returns the SAME swap (no double-apply).
    if key and key in state.idempotency:
        swap = state.swaps[state.idempotency[key]]
        return swap["response"]

    swap_id = f"swap-{state.tag}-{next(state._swap_seq):03d}"
    control = body["control"]
    version = body["version"]
    from_version = state.default_version

    if control == "shadow":
        state.routing = [
            {"version": from_version, "role": "default", "weight": 1.0},
            {"version": version, "role": "shadow", "weight": 0.0},
        ]
    elif control == "canary":
        weight = float(body.get("canary_weight") or 0.0)
        state.routing = [
            {"version": from_version, "role": "default", "weight": 1.0 - weight},
            {"version": version, "role": "canary", "weight": weight},
        ]
    elif control == "full_switch":
        state.default_version = version
        state.routing = [{"version": version, "role": "default", "weight": 1.0}]
    elif control == "unload":
        if version in state.loaded_versions:
            state.loaded_versions.remove(version)
        if version in state.ready_versions:
            state.ready_versions.remove(version)

    response = {
        "swap_id": swap_id,
        "from_version": from_version,
        "to_version": version,
        "control": control,
        "started_at": datetime.now(UTC).isoformat(),
        # The stateless poll token. Also stored in state.swaps["response"] so an
        # idempotent replay hands back the same token.
        "applied": {
            "model_name": body["model_name"],
            "to_version": version,
            "control": control,
            "from_version": from_version,
            "canary_weight": body.get("canary_weight"),
            "key": key or f"{body['model_name']}:{version}:{control}",
        },
        "noop": False,
    }
    # Kept only so the idempotent replay above returns the same response object.
    # Do not add a "status" entry back: nothing resolves a swap id any more.
    state.swaps[swap_id] = {"response": response}
    if key:
        state.idempotency[key] = swap_id
    return response


def _serving_state_body(model_name: str) -> dict:
    """The one projection both ``/serving-state`` and convergence's ``observed`` render.

    Shared so the two cannot drift apart, and so neither can drift into a
    friendlier shape than the real ``ServingStateResponse``.
    """
    return {
        "model_name": model_name,
        "default_version": state.default_version,
        "routing": state.routing,
        "serving_versions": list(state.loaded_versions),
        "known_versions": list(state.loaded_versions),
        "ready_versions": list(state.ready_versions),
        "ready": True,
        "routing_available": True,
        # The real Model Manager emits ``loaded_available``. This mock said
        # ``known_available``, which only ever worked through a legacy alias in
        # the DFW client -- i.e. the mock was exercising a shape production
        # never sends.
        "loaded_available": True,
    }


@app.post("/mm/v1/models/convergence")
async def mm_convergence(request: Request) -> dict:
    """Report whether the expectation has landed.

    A pure function of the expectation and the live state. It reads no swap id
    and mutates nothing, so it survives a restart and any replica answers alike.
    """
    body = await request.json()
    state.record("mm", "POST", "/v1/models/convergence", body)
    want = body["expectation"]
    version, control = want["to_version"], want["control"]
    entry = next((r for r in state.routing if r["version"] == version), None)

    if control == "full_switch":
        ok = state.default_version == version and [r["version"] for r in state.routing] == [version]
        code = "default_version_mismatch"
    elif control == "shadow":
        ok = entry is not None and entry["role"] == "shadow"
        code = "shadow_absent"
    elif control == "canary":
        weight = want.get("canary_weight")
        ok = (
            entry is not None
            and entry["role"] == "canary"
            and (weight is None or abs(entry["weight"] - weight) < 1e-9)
        )
        code = "canary_weight_mismatch"
    else:
        ok = version not in state.loaded_versions
        code = "still_resident"

    # Readiness is checked for every control except unload, matching the real
    # predicate. Without it the routing write alone would satisfy the poll.
    def _report(converged: bool, reason: dict | None) -> dict:
        # ``observed`` and ``at`` are REQUIRED on the real ConvergenceReport.
        # Omitting them let this mock pass a client that had quietly stopped
        # reading them -- the drift this suite exists to catch.
        return {
            "converged": converged,
            "reason": reason,
            "observed": _serving_state_body(want["model_name"]),
            "at": datetime.now(UTC).isoformat(),
        }

    if ok and control != "unload" and version not in state.ready_versions:
        return _report(
            False,
            {
                "code": "candidate_not_ready",
                "message": f"{version} is routed but not ready",
                "terminal": False,
            },
        )

    if ok:
        return _report(True, None)
    return _report(
        False,
        {
            "code": code,
            "message": f"{control} for {version} has not landed",
            "terminal": False,
        },
    )


@app.get("/mm/v1/models/status/{swap_id}")
def mm_status(swap_id: str):
    """The retired status surface, answering as the real Model Manager does.

    Returns 410 with an ErrorEnvelope. The call is still recorded so a regressed
    status poll shows up in the e2e call trace instead of passing silently.
    """
    state.record("mm", "GET", f"/v1/models/status/{swap_id}")
    return JSONResponse(
        status_code=410,
        content={
            "error": {
                "code": "bad_request",
                "message": (
                    f"GET /v1/models/status/{{swap_id}} is gone (swap_id={swap_id}). "
                    "Poll POST /v1/models/convergence with the 'applied' object that "
                    "apply returned."
                ),
                "retryable": False,
                "details": {},
            }
        },
    )


@app.get("/mm/v1/models/{model_name}/serving-state")
def mm_serving_state(model_name: str) -> dict:
    """What the stack holds for one model, asked BEFORE anything is applied.

    ``register_candidate`` needs this to pick a version floor; answering 404
    reads as "could not find out", and the allocator then refuses to mint at all
    unless a counter already exists. The lists are what this mock actually
    holds, so a first-ever rollout correctly reports an empty repo rather than
    an unknown one.
    """
    state.record("mm", "GET", f"/v1/models/{model_name}/serving-state")
    return _serving_state_body(model_name)


@app.post("/mm/v1/models/cleanup")
async def mm_cleanup(request: Request) -> dict:
    body = await request.json()
    state.record("mm", "POST", "/v1/models/cleanup", body)
    version = body["version"]
    if version in state.loaded_versions:
        state.loaded_versions.remove(version)
    return {"version": version, "status": "removed"}


# --------------------------------------------------------------------------- #
# online eval samples — generated per the current scenario knobs
# --------------------------------------------------------------------------- #


def _online_results() -> list[dict]:
    candidate_correct = state.scenario["online_behavior"] == "advance"
    results = []
    for i in range(int(state.scenario.get("online_samples", 200))):
        actual_defect = i % 2 == 0
        confirmed = "NotGood" if actual_defect else "Good"
        ai = confirmed if candidate_correct else "Good"
        results.append(
            {
                "target_id": f"R{i}",
                "ai_classification": ai,
                "ai_confidence": 0.95,
                "confirmed_label": confirmed,
                "baseline_classification": confirmed,
            }
        )
    return results


# --------------------------------------------------------------------------- #
# /aoi — LEGACY direct inline-results endpoint (aoi_results_url override)
# --------------------------------------------------------------------------- #


@app.get("/aoi/v1/results")
def aoi_results(role: str = Query(default="shadow"), window: str = Query(default="")) -> dict:
    state.record("aoi", "GET", f"/v1/results?role={role}")
    return {"role": role, "window": window, "results": _online_results()}


# --------------------------------------------------------------------------- #
# /storage — eval sets + KPI sets (Storage Backend design §4.2 / §4.3):
# snapshot-like create → poll stats → retrieve via the create-response ref.
# --------------------------------------------------------------------------- #

#: Stats polls before a freshly-created eval/KPI set reports ready — forces
#: DFW through the readiness-poll path, like SNAPSHOT_READY_AFTER.
EVAL_SET_READY_AFTER = 1


def _jsonl(records: list[dict]) -> bytes:
    import json as _json

    lines = [_json.dumps(r) for r in records]
    return ("\n".join(lines) + ("\n" if lines else "")).encode()


@app.post("/storage/v1/results", status_code=201)
async def storage_create_eval_set(request: Request) -> dict:
    body = await request.json()
    state.record("storage", "POST", "/v1/results", body)
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": [{"loc": ["body"], "msg": "value is not a valid object"}]},
        )
    # CONTRACT VALIDATION (CreateEvaluationSetRequest): the window MUST ride a
    # `selection` envelope requiring model+version+role, additionalProperties
    # false at both levels. A non-validating mock previously masked a flat
    # body that the real backend would 422 — never again.
    allowed_top = {"selection", "reason", "metadata"}
    selection = body.get("selection")
    errors = []
    if not isinstance(selection, dict):
        errors.append({"loc": ["body", "selection"], "msg": "field required"})
    else:
        for f in ("model", "version", "role"):
            if not selection.get(f):
                errors.append({"loc": ["body", "selection", f], "msg": "field required"})
        for k in selection:
            if k not in ("model", "version", "role", "from", "to"):
                errors.append(
                    {"loc": ["body", "selection", k], "msg": "extra fields not permitted"}
                )
    for k in body:
        if k not in allowed_top:
            errors.append({"loc": ["body", k], "msg": "extra fields not permitted"})
    if errors:
        return JSONResponse(status_code=422, content={"detail": errors})
    eval_id = f"eval-{state.tag}-{next(state._eval_seq):03d}"
    records = _online_results()
    payload = _jsonl(records)
    state.eval_sets[eval_id] = {
        "status": "creating",
        "polls": 0,
        "jsonl": payload,
        "total_records": len(records),
        "window": body["selection"],
        "base_url": str(request.base_url).rstrip("/"),
    }
    # Contract v2: the create response is ID-only.
    return {"eval_id": eval_id}


@app.get("/storage/v1/eval/{eval_id}/manifest0.jsonl")
def storage_eval_manifest(eval_id: str):
    # The manifest object itself. Real deployments serve this from the object
    # store; the mock serves it over HTTP so DFW's manifest reader (which
    # fetches http(s) URIs) is exercised end-to-end, sha256 and all.
    from fastapi.responses import Response

    state.record("storage", "GET", f"/v1/eval/{eval_id}/manifest0.jsonl")
    es = state.eval_sets.get(eval_id)
    if es is None:
        return JSONResponse(status_code=404, content={"detail": "eval set not found"})
    return Response(content=es["jsonl"], media_type="application/jsonl")


@app.get("/storage/v1/eval/{eval_id}")
def storage_get_eval_set(eval_id: str) -> dict:
    import hashlib

    state.record("storage", "GET", f"/v1/eval/{eval_id}")
    es = state.eval_sets.get(eval_id)
    if es is None:
        return JSONResponse(status_code=404, content={"detail": "eval set not found"})
    es["polls"] += 1
    if es["polls"] >= EVAL_SET_READY_AFTER:
        es["status"] = "ready"
    return {
        "eval_id": eval_id,
        "status": es["status"],
        "total_records": es["total_records"],
        "manifests": [
            {
                "format": "jsonl",
                "uri": f"{es['base_url']}/storage/v1/eval/{eval_id}/manifest0.jsonl",
                "size_bytes": len(es["jsonl"]),
                "sha256": hashlib.sha256(es["jsonl"]).hexdigest(),
            }
        ],
        "created_at": "2026-01-01T00:00:00Z",
    }


@app.post("/storage/v1/kpi", status_code=201)
async def storage_create_kpi_set(request: Request) -> dict:
    body = await request.json()
    state.record("storage", "POST", "/v1/kpi", body)
    kpi_id = f"kpi-{state.tag}-{next(state._kpi_seq):03d}"
    state.kpi_sets[kpi_id] = {"status": "creating", "polls": 0, "selection": body}
    # Contract v2: the create response is ID-only.
    return {"kpi_id": kpi_id}


@app.get("/storage/v1/kpi/{kpi_id}")
def storage_get_kpi_set(kpi_id: str) -> dict:
    state.record("storage", "GET", f"/v1/kpi/{kpi_id}")
    ks = state.kpi_sets.get(kpi_id)
    if ks is None:
        return JSONResponse(status_code=404, content={"detail": "kpi set not found"})
    ks["polls"] += 1
    if ks["polls"] >= EVAL_SET_READY_AFTER:
        ks["status"] = "ready"
    return {
        "kpi_id": kpi_id,
        "status": ks["status"],
        "total_records": 21148,
        "manifests": [
            {
                "format": "jsonl",
                "uri": f"s3://aoi-artifacts/kpi/{kpi_id}/manifest0.jsonl",
                "size_bytes": 1048576,
                "sha256": "0" * 64,
            }
        ],
        "created_at": "2026-01-01T00:00:00Z",
    }


# --------------------------------------------------------------------------- #
# ITS storage catalog
# --------------------------------------------------------------------------- #
# ITS routes to its own Storage Backend catalog (``/v1/its/...``) while AOI keeps
# the unprefixed paths — see ``_CATALOG_BY_USE_CASE`` in
# src/backends/deft/storage_backend_client.py. Same handlers and same state: the
# catalog selects a namespace, not a different service. Mirrored rather than
# re-declared so a new storage route cannot be served for AOI but 404 for ITS.
def _mirror_its_catalog() -> None:
    for route in list(app.routes):
        path = getattr(route, "path", "")
        if not path.startswith("/storage/v1/") or path.startswith("/storage/v1/its/"):
            continue
        app.add_api_route(
            path.replace("/storage/v1/", "/storage/v1/its/", 1),
            route.endpoint,
            methods=sorted(route.methods),
            status_code=route.status_code,
        )


_mirror_its_catalog()
