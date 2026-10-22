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
"""ITS deployment e2e driver: an ``its_job`` through the DEPLOYED DFW stack.

Prerequisites, same stack as ``deployment_e2e.py`` plus a real deft-service:
  1. mock services on the host:  MOCK_PORT=9000 uv run python -m tests.mock_services
     (must serve the ITS storage catalog and the Model Manager serving-state
     route — step [0] checks both, since an ITS run needs them and an AOI one
     does not)
  2. deft-service in simulate mode, serving the `smart-city-object-detection`
     profile, at the deft_base_url in
     tests/e2e_scripts/deft/deploy/config.deft-e2e.yaml
  3. the stack:                  BACKEND=deft COMPOSE_PROFILES=deft docker compose \
                                   -f deploy/docker-compose.yaml \
                                   -f tests/e2e_scripts/deft/deploy/docker-compose.deft-e2e.yaml \
                                   up --build -d

Then:  uv run python -m tests.e2e_scripts.deft.its_deployment_e2e [promote|shipped_gate]

The driver only uses the public surfaces — POST /api/jobs, POST /api/configs,
GET /api/jobs/{id} (including its `deft` block), GET /api/promotions,
POST /api/promotions/{id}/approve, the mock stack's /state and /admin/*, and the
MLflow REST alias API — exactly like an operator would. It never reads
deft-service or DFW's database: what DFW sent DEFT is asserted through the
fields DFW publishes about it, and what the profile itself contains belongs to
deft-service's own suite. Scenarios:

  promote      (default) an its_job off the its_job_config block reaches a
               human-gated rollout: ITS writes its own storage catalog, the
               offline gate passes on FAR_pct, the ladder parks at
               pending_approval/full having applied shadow and skipped canary,
               and approving it switches serving to the candidate.
  shipped_gate the SHIPPED gate (macro_ap >= 0.55) submitted as a named config —
               a negative control proving it fails CLOSED with an explicit
               reason=metric-not-reported rather than a genuine metric miss.

Why `promote` gates on FAR_pct while `shipped_gate` gates on macro_ap: see the
eval block header in tests/e2e_scripts/deft/deploy/config.deft-e2e.yaml.

Reported, not asserted (each explained at its print): the MLflow @production
alias, the .onnx/.pth artifact-format mismatch, and the AOI-shaped KPI-set mint.
"""

from __future__ import annotations

import sys
import time

from tests.e2e_scripts.deft.deployment_e2e import MLFLOW, _mlflow_alias, _wait
from tests.e2e_scripts.deft.deployment_scenarios import (
    API,
    MOCK,
    TERMINAL,
    arm,
    audit_job_surface,
    http,
    mock_state,
    submit_job,
    watch_job,
)

PROFILE = "smart-city-object-detection"
MODEL_NAME = "gdino-its"
CONFIG_NAME = "its-shipped-gate"

#: eval_harness is off for ITS, so the ladder chains gate to gate instead of
#: waiting on the 60s online-eval beat; only the 30s HITL poll paces the rollout
#: once the approval lands.
JOB_TIMEOUT_S = 10 * 60
APPROVE_TIMEOUT_S = 5 * 60


def check_mock_its_support(notes: list[str]) -> bool:
    """The two mock routes an ITS rollout needs, checked before a 10-minute run.

    ITS writes its own Storage Backend catalog (/v1/its/*), and
    register_candidate refuses to mint a serving version unless the Model
    Manager can be read for a floor — a 404 on either fails the run minutes in
    with an error that names neither.
    """
    ok = True
    for url, what in (
        (f"{MOCK}/storage/v1/its/snapshot/stats", "ITS storage catalog"),
        (f"{MOCK}/mm/v1/models/{MODEL_NAME}/serving-state", "Model Manager serving-state"),
    ):
        code, _ = http("GET", url)
        if code != 200:
            notes.append(f"mock services do not serve the {what} ({url} -> {code})")
            ok = False
    return ok


def clear_approval_signals() -> None:
    """Drop the MLflow signals hitl_poll reads as a human approval.

    ``_detect_decision`` approves a gate when the candidate carries a
    ``gate.{gate}.decision`` tag OR when the gate's alias (``@production`` for
    ``full``) already sits on it. Both persist in the registry, so a previous
    run's approval silently approves this one within a beat and the pause is
    never observable. Registry-only: the promotion records are untouched.
    """
    for alias in ("production", "shadow", "canary", "candidate"):
        http(
            "DELETE",
            f"{MLFLOW}/api/2.0/mlflow/registered-models/alias?name={MODEL_NAME}&alias={alias}",
        )


def promotion_for(job_id: str) -> dict | None:
    """This run's promotion record. Bound by flywheel_run_id rather than taken as
    the newest, since promotions persist in Mongo across runs and the list pages."""
    _, page = http("GET", f"{API}/promotions?limit=1000")
    return next((p for p in page.get("promotions", []) if p.get("flywheel_run_id") == job_id), None)


def apply_controls(calls: list[dict]) -> list[str]:
    return [
        c["body"]["control"]
        for c in calls
        if c["service"] == "mm" and c["call"].endswith("/v1/models/apply") and c.get("body")
    ]


def run_promote(notes: list[str]) -> bool:
    print("[0] readiness")
    _wait(f"{MOCK}/healthz", "mock services")
    _wait(f"{API}/jobs", "dfw api")
    if not check_mock_its_support(notes):
        return False

    print("[1] arm the mock loop")
    arm()
    clear_approval_signals()

    print("[2] POST /api/jobs (job_type=its_job, no config_name)")
    job_id = submit_job(job_type="its_job")
    print(f"    id={job_id}")

    print("[3] poll to the pending_approval park")

    # Polls GET /api/promotions, not the job's own deft.promotion mirror: that
    # mirror lags the record — it still reads pull/promoting while the promotion
    # is already parked at full — so waiting on it never sees the pause.
    def parked(_detail: dict) -> bool:
        promo = promotion_for(job_id)
        return bool(promo and promo.get("state") == "pending_approval")

    _, detail = watch_job(job_id, JOB_TIMEOUT_S, stop_when=parked)
    if not parked(None):
        # Covers a terminal failure and a timeout alike. A deft-service that does
        # not serve the profile 422s the submit and surfaces here as the error.
        notes.append(
            f"never parked at pending_approval: status={detail.get('status')!r} "
            f"error={detail.get('error')!r}"
        )
        return False

    deft = detail.get("deft") or {}
    gate = deft.get("gate") or {}
    candidate = deft.get("candidate") or {}
    data = deft.get("data") or {}
    if not deft.get("run_id"):
        notes.append("deft.run_id absent — deft-service never accepted the profile-mode request")
        return False
    if deft.get("use_case") != "its":
        notes.append(f"deft.use_case {deft.get('use_case')!r} != 'its'")
    if deft.get("model_name") != MODEL_NAME:
        notes.append(f"deft.model_name {deft.get('model_name')!r} != {MODEL_NAME!r}")
    if not data.get("snapshot_uri"):
        notes.append("deft.data.snapshot_uri absent — nothing was sent as the data_pool override")
    if gate.get("passed") is not True:
        notes.append(f"offline gate did not pass: unmet={gate.get('unmet')}")
    if "FAR_pct" not in (gate.get("metrics") or {}):
        notes.append(f"gate metrics carry no FAR_pct: {gate.get('metrics')}")
    print(f"    deft.run_id={deft['run_id']} snapshot={data.get('snapshot_uri')}")
    print(f"    gate metrics={gate.get('metrics')} candidate={candidate}")
    # The KPI set is minted AOI-shaped (a hardcoded {'tag': 'kpi'} selection), so
    # a green run is NOT evidence that ITS is judged on ITS data. There is no
    # config surface to make it ITS-shaped today.
    print(f"    kpi_set_uri={data.get('kpi_set_uri')!r} (AOI-shaped mint)")

    print("[4] promotion parked at the full gate")
    calls = mock_state()["calls"]
    promo = promotion_for(job_id)
    if promo is None:
        notes.append("no promotion record carries this run's flywheel_run_id")
        return False
    if promo.get("deft_run_id") != deft["run_id"]:
        notes.append(f"promotion deft_run_id {promo.get('deft_run_id')!r} != {deft['run_id']!r}")
    if promo.get("use_case") != "its":
        notes.append(f"promotion use_case {promo.get('use_case')!r} != 'its'")
    if promo["state"] != "pending_approval":
        notes.append(f"promotion state {promo['state']!r} != pending_approval")
    if promo.get("pending_gate") != "full":
        notes.append(f"pending_gate {promo.get('pending_gate')!r} != full")
    # The ITS ladder is shadow then full with canary skipped, where aoi_job_config
    # runs canary_steps [5, 25]; serving_role is DFW's self-report and the mock's
    # recorded apply bodies are the independent witness of what went on the wire.
    if promo.get("serving_role") != "shadow":
        notes.append(f"serving_role {promo.get('serving_role')!r} != shadow")
    if promo.get("canary_step_index") != 0:
        notes.append(f"canary_step_index {promo.get('canary_step_index')!r} != 0")
    if set(apply_controls(calls)) != {"shadow"}:
        notes.append(f"pre-approval apply controls {apply_controls(calls)} != ['shadow']")
    if candidate.get("version") != promo.get("candidate_version"):
        notes.append(
            f"deft.candidate.version {candidate.get('version')!r} != promotion "
            f"candidate_version {promo.get('candidate_version')!r}"
        )
    print(f"    {promo['promotion_id']}: state={promo['state']} gate={promo.get('pending_gate')}")

    print("[5] POST /api/promotions/{id}/approve")
    code, resp = http(
        "POST",
        f"{API}/promotions/{promo['promotion_id']}/approve",
        {"approved_by": "its-e2e", "note": "its promote scenario"},
    )
    if code != 200:
        notes.append(f"approve -> {code}: {resp}")
        return False

    deadline = time.monotonic() + APPROVE_TIMEOUT_S
    final: dict = {}
    while time.monotonic() < deadline:
        final = promotion_for(job_id) or {}
        if final.get("state") in ("promoted", "failed", "rolled_back"):
            break
        time.sleep(4.0)
    else:
        notes.append(f"promotion still {final.get('state')!r} after {APPROVE_TIMEOUT_S}s")
    if final.get("state") != "promoted":
        notes.append(f"final promotion state {final.get('state')!r} != promoted")
    print(f"    -> state={final.get('state')}")

    print("[6] job surface")
    _, detail = watch_job(job_id, APPROVE_TIMEOUT_S)
    if detail.get("status") != "completed":
        notes.append(f"run status {detail.get('status')!r} != completed")
    audit_job_surface(job_id, detail, notes, expect_error=False)

    print("[7] serving state")
    state = mock_state()
    controls = apply_controls(state["calls"])
    if [c for c in controls if c in ("shadow", "full_switch")] != ["shadow", "full_switch"]:
        notes.append(f"rollout did not go shadow -> full_switch: {controls}")
    if "canary" in controls:
        notes.append(f"canary applied despite deployment_stages.canary=false: {controls}")
    if state["default_version"] != promo.get("candidate_version"):
        notes.append(
            f"serving default {state['default_version']!r} != candidate "
            f"{promo.get('candidate_version')!r}"
        )

    # Printed, not asserted equal to the serving version: the alias names an
    # MLflow REGISTRY version, which counts registrations, while the serving
    # default names the minted serving version. Different sequences by design —
    # the registry row carries the serving number as a promotion.version_label
    # tag. Asserting they match would fail on a registry ever written by
    # anything else. deft.candidate.registered is DFW's own answer to "did the
    # alias move", and registration is best effort by design.
    print(
        f"    serving default={state['default_version']}  "
        f"MLflow @production={_mlflow_alias(MODEL_NAME, 'production')}  "
        f"registered={candidate.get('registered')}"
    )

    # Shipped model_format is onnx, but the ITS profile selects a .pth
    # checkpoint. The mock MM accepts anything, so this is reported, not gated.
    for call in state["calls"]:
        if (
            call["service"] == "mm"
            and call["call"].endswith("/v1/models/pull")
            and call.get("body")
        ):
            uri = call["body"].get("artifact_uri", "")
            if uri and not uri.endswith(".onnx"):
                print(f"    ⚠ model_format=onnx but artifact is {uri.rsplit('.', 1)[-1]}: {uri}")

    return not notes


def run_shipped_gate(notes: list[str]) -> bool:
    """The shipped macro_ap gate must fail CLOSED, and say why."""
    print("[0] readiness")
    _wait(f"{MOCK}/healthz", "mock services")
    _wait(f"{API}/jobs", "dfw api")
    if not check_mock_its_support(notes):
        return False

    print("[1] POST /api/configs (shipped its_job_config gate)")
    # Fixed name, recreated each run: a timestamped one accretes a Mongo row per
    # invocation, and reusing a stale one would silently test last run's gate.
    http("DELETE", f"{API}/configs/{CONFIG_NAME}")
    code, resp = http(
        "POST",
        f"{API}/configs",
        {
            "name": CONFIG_NAME,
            "config_type": "its_job",
            "config_data": {
                "use_case": "its",
                "model_name": MODEL_NAME,
                "model_format": "onnx",
                "selection": {
                    "mode": "query",
                    "filters": {"created_after": "1970-01-01T00:00:00Z"},
                },
                "kpi": {"pull_from_storage": True},
                "deft": {"profile": PROFILE},
                "eval": {"kpi_target": [{"metric": "macro_ap", "op": ">=", "threshold": 0.55}]},
                "promotion_policy": {
                    "hitl": {"pull": False, "deploy": False, "canary": True, "full": True},
                    "deployment_stages": {"shadow": True, "canary": False},
                    "eval_harness": {"enabled": False},
                },
            },
        },
    )
    if code != 201:
        notes.append(f"POST /configs -> {code}: {resp}")
        return False
    print(f"    created {CONFIG_NAME}")

    arm()

    print("[2] submit it and poll to terminal")
    job_id = submit_job(config_name=CONFIG_NAME, job_type="its_job")
    seq, detail = watch_job(job_id, JOB_TIMEOUT_S)
    status = detail.get("status")
    print(f"    sequence={seq}")
    if status not in TERMINAL:
        notes.append(f"run never reached a terminal status (last={status!r})")
        return False

    print("[3] gate verdict")
    deft = detail.get("deft") or {}
    gate = deft.get("gate") or {}
    unmet = " ".join(gate.get("unmet") or [])
    error = detail.get("error") or ""
    print(f"    metrics={gate.get('metrics')}")
    print(f"    unmet={gate.get('unmet')}")

    if deft.get("use_case") != "its":
        notes.append(f"deft.use_case {deft.get('use_case')!r} != 'its'")
    if status != "failed":
        notes.append(f"run status {status!r} != failed")
    if "reason=metric-not-reported" not in unmet:
        notes.append(f"gate.unmet lacks the metric-not-reported marker: {unmet!r}")
    if "macro_ap" not in error:
        notes.append(f"job error does not name the unmet constraint: {error!r}")
    if promotion_for(job_id) is not None:
        notes.append("a promotion was minted despite the gate failing")
    if deft.get("candidate") or deft.get("promotion"):
        notes.append(f"a candidate was minted despite the gate failing: {deft.get('candidate')!r}")
    if [c for c in mock_state()["calls"] if c["service"] == "mm"]:
        notes.append("Model Manager was touched despite the gate failing")

    audit_job_surface(job_id, detail, notes, expect_error=True)
    return not notes


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "promote"
    runner = {"promote": run_promote, "shipped_gate": run_shipped_gate}.get(scenario)
    if runner is None:
        print(f"⛔ unknown scenario {scenario!r} — expected promote|shipped_gate")
        return 2

    print(f"→ scenario={scenario}")
    notes: list[str] = []
    ok = runner(notes)

    for note in notes:
        print(f"    ⚠ {note}")
    print(f"\n{'✅ PASS' if ok else '⛔ FAIL'} — scenario={scenario}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
