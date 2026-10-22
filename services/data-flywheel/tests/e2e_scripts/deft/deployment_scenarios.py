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
"""Extended deployment scenario battery for the DEFT/AOI stack.

Runs against the SAME deployed stack + mock services as
``tests/e2e_scripts/deft/deployment_e2e.py`` (see that module's prerequisites), but
covers the long tail: training failures, online HOLD behavior, HITL
approve/reject through the public promotion API (with a named config created
via POST /configs), second-generation baseline enforcement, a mid-run DEFT
outage, a worker restart mid-promotion, and the job-cancel API. (No concurrent
-jobs scenario by design: AOI runs are serialized by the parent queue —
concurrency=1 + blocking chain result.)

Every scenario also audits the JOB-STATUS SURFACE: GET /jobs/{id} field
integrity (status/finished_at/runtime), the status *sequence* observed, the
error message on failures, presence in GET /jobs, and the promotion-record
delta.

Run all:      uv run python -m tests.e2e_scripts.deft.deployment_scenarios
Run some:     uv run python -m tests.e2e_scripts.deft.deployment_scenarios s1 s5 ...
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API = "http://localhost:8000/api"
MOCK = "http://localhost:9000"

TERMINAL = ("completed", "failed", "cancelled")
BEAT_S = 60.0  # online-eval beat cadence


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #


def http(method: str, url: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # — localhost driver
            payload = resp.read()
        return resp.status, (json.loads(payload) if payload else {})
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def mock_scenario(**knobs) -> None:
    http("POST", f"{MOCK}/admin/scenario", knobs)


def mock_reset() -> None:
    http("POST", f"{MOCK}/admin/reset")


def mock_state() -> dict:
    return http("GET", f"{MOCK}/state")[1]


def promotions() -> list[dict]:
    return http("GET", f"{API}/promotions")[1].get("promotions", [])


def submit_job(config_name: str | None = None, job_type: str = "aoi_job") -> str:
    body: dict = {
        "workload_id": "scenario-battery",
        "client_id": "scenario-battery",
        "job_type": job_type,
    }
    if config_name:
        body["config_name"] = config_name
    code, job = http("POST", f"{API}/jobs", body)
    assert code == 200, f"POST /jobs -> {code}: {job}"
    return job["id"]


def watch_job(job_id: str, timeout_s: float, stop_when=None) -> tuple[list[str], dict]:
    """Poll GET /jobs/{id}; return (observed status sequence, final detail)."""
    seq: list[str] = []
    detail: dict = {}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        code, detail = http("GET", f"{API}/jobs/{job_id}")
        assert code == 200, f"GET /jobs/{job_id} -> {code}: {detail}"
        status = detail["status"]
        if not seq or seq[-1] != status:
            seq.append(status)
            print(f"      {time.strftime('%H:%M:%S')} {job_id[-6:]}: {status}")
        if status in TERMINAL or (stop_when and stop_when(detail)):
            return seq, detail
        time.sleep(4.0)
    return seq, detail


def audit_job_surface(job_id: str, detail: dict, notes: list[str], *, expect_error: bool) -> None:
    """Validate the observable job surface 'in all aspects'."""
    status = detail.get("status")
    if status in TERMINAL:
        if not detail.get("finished_at"):
            notes.append(f"AUDIT: terminal run {job_id} has no finished_at")
        if detail.get("runtime_seconds", -1) < 0:
            notes.append(f"AUDIT: negative runtime_seconds on {job_id}")
    if expect_error and not detail.get("error"):
        notes.append(f"AUDIT: failed run {job_id} carries no error message")
    if not expect_error and detail.get("error"):
        notes.append(f"AUDIT: successful run {job_id} carries error: {detail['error']!r}")
    # The run must appear in the list endpoint with the same status.
    code, listing = http("GET", f"{API}/jobs")
    listed = next((j for j in listing.get("jobs", []) if j["id"] == job_id), None)
    if listed is None:
        notes.append(f"AUDIT: {job_id} missing from GET /jobs")
    elif listed["status"] != status:
        notes.append(f"AUDIT: list status {listed['status']} != detail status {status}")


def new_promos(pre: set[str]) -> list[dict]:
    return [p for p in promotions() if p["promotion_id"] not in pre]


def arm(**knobs) -> set[str]:
    """Reset the mock loop, apply scenario knobs, snapshot promotion ids."""
    mock_reset()
    defaults = {
        "training_outcome": "completed",
        "metrics": "pass",
        "online_behavior": "advance",
        "online_samples": 200,
        "deft_outage": False,
    }
    defaults.update(knobs)
    mock_scenario(**defaults)
    return {p["promotion_id"] for p in promotions()}


# --------------------------------------------------------------------------- #
# scenarios — each returns (ok, notes)
# --------------------------------------------------------------------------- #


def s1_training_failed():
    """DEFT run ends 'failed' → run FAILED with a clear error; nothing promoted."""
    pre = arm(training_outcome="failed")
    job_id = submit_job()
    seq, detail = watch_job(job_id, 180)
    notes: list[str] = [f"sequence={seq}"]
    audit_job_surface(job_id, detail, notes, expect_error=True)
    err = detail.get("error") or ""
    ok = (
        detail.get("status") == "failed"
        and not new_promos(pre)
        and not [c for c in mock_state()["calls"] if c["service"] == "mm"]
    )
    if "non-successfully" not in err:
        notes.append(f"error message: {err!r}")
    return ok, notes


def s2_training_cancelled():
    """DEFT run ends 'cancelled' → same failure semantics."""
    pre = arm(training_outcome="cancelled")
    job_id = submit_job()
    seq, detail = watch_job(job_id, 180)
    notes = [f"sequence={seq}", f"error={detail.get('error')!r}"]
    audit_job_surface(job_id, detail, notes, expect_error=True)
    ok = detail.get("status") == "failed" and not new_promos(pre)
    return ok, notes


def s3_hold_then_advance():
    """Insufficient online samples → promotion HOLDS in shadow; more samples → completes."""
    pre = arm(online_samples=10)  # < min_sample_size(100) -> verdict hold
    job_id = submit_job()
    seq, detail = watch_job(job_id, 240, stop_when=lambda d: d["status"] == "promoting")
    notes = [f"sequence={seq}"]
    if detail.get("status") != "promoting":
        return False, [*notes, "never reached promoting"]

    # Two beat windows: the promotion must still be holding in shadow.
    time.sleep(2 * BEAT_S + 15)
    promo = new_promos(pre)[0]
    notes.append(
        f"after 2 beats: state={promo['state']} role={promo.get('serving_role')} "
        f"verdict={promo.get('verdict')}"
    )
    held = promo["state"] == "promoting" and promo.get("serving_role") == "shadow"
    if not held:
        return False, [*notes, "did not hold on insufficient samples"]

    # Samples arrive -> verdicts advance -> full rollout completes.
    mock_scenario(online_samples=200)
    seq2, detail = watch_job(job_id, 420)
    notes.append(f"post-samples sequence={seq2}")
    audit_job_surface(job_id, detail, notes, expect_error=False)
    promo = new_promos(pre)[0]
    ok = detail.get("status") == "completed" and promo["state"] == "promoted"
    return ok, notes


def s4_gen2_worse_still_promotes():
    """Gen-2 candidate meets the KPI but scores below the promoted baseline → PROMOTES.

    The offline gate is a KPI-only confirmation (the DEFT contract returns
    candidate-only metrics; baseline consideration happens inside DEFT's own
    loop) — a DFW-side worse-than-baseline block was deliberately removed, so
    a KPI-meeting candidate must roll out even with lower absolute numbers
    than the previous generation.
    """
    # Relies on a previously PROMOTED model with PASS metrics (s3 or a promote run).
    pre = arm(metrics="worse")
    job_id = submit_job()
    seq, detail = watch_job(job_id, 420)
    notes = [f"sequence={seq}", f"error={detail.get('error')!r}"]
    audit_job_surface(job_id, detail, notes, expect_error=False)
    promo = new_promos(pre)
    ok = detail.get("status") == "completed" and bool(promo) and promo[0]["state"] == "promoted"
    notes.append(f"promo={promo[0]['state'] if promo else None}")
    return ok, notes


def _hitl_config_name() -> str:
    """Create (once) a named config with HITL required at the full gate."""
    name = "scenario-battery-hitl"
    code, _ = http("GET", f"{API}/configs/{name}")
    if code == 200:
        return name
    code, resp = http(
        "POST",
        f"{API}/configs",
        {
            "name": name,
            "config_type": "aoi_job",
            "config_data": {
                "use_case": "aoi",
                "model_name": "changenet-aoi",
                "selection": {
                    "mode": "query",
                    "filters": {"created_after": "1970-01-01T00:00:00Z"},
                },
                "eval": {
                    # Match DEFT's returned metric contract: FAR_pct is a
                    # percentage measured at recall=100% (deft kpi.py). "FAR <
                    # 0.1% at recall=100%" == FAR_pct < 0.1.
                    "kpi_target": [
                        {"metric": "FAR_pct", "op": "<", "threshold": 0.1},
                    ]
                },
                "deft": {"config": {"pipeline": {"kpi_target": "FAR < 0.1% at recall=100%"}}},
                "promotion_policy": {
                    "hitl": {"pull": False, "deploy": False, "canary": False, "full": True},
                    "canary_steps": {"steps": [5]},
                    "poll_interval_s": 2.0,
                    "poll_max_attempts": 30,
                    "auto_rollback_on_regression": True,
                },
            },
        },
    )
    assert code in (200, 201), f"POST /configs -> {code}: {resp}"
    return name


def _run_to_pending_approval(pre: set[str], notes: list[str]):
    job_id = submit_job(config_name=_hitl_config_name())
    watch_job(job_id, 240, stop_when=lambda d: d["status"] == "promoting")

    deadline = time.monotonic() + 5 * BEAT_S
    promo = None
    while time.monotonic() < deadline:
        candidates = [p for p in new_promos(pre) if p["state"] == "pending_approval"]
        if candidates:
            promo = candidates[0]
            break
        time.sleep(5.0)
    if promo:
        notes.append(f"paused at gate={promo.get('pending_gate')} for approval")
    return job_id, promo


def s5_hitl_approve():
    """HITL at the full gate (named config via /configs) → approve API → promoted."""
    pre = arm()
    notes: list[str] = []
    job_id, promo = _run_to_pending_approval(pre, notes)
    if promo is None or promo.get("pending_gate") != "full":
        return False, [*notes, "never paused at the full gate"]

    code, resp = http(
        "POST",
        f"{API}/promotions/{promo['promotion_id']}/approve",
        {"approved_by": "scenario-battery", "note": "scenario s5"},
    )
    notes.append(f"approve -> {code}")
    if code != 200:
        return False, [*notes, f"approve response: {resp}"]

    seq, detail = watch_job(job_id, 420)
    audit_job_surface(job_id, detail, notes, expect_error=False)
    promo = next(p for p in promotions() if p["promotion_id"] == promo["promotion_id"])
    notes.append(f"final: run={detail.get('status')} promo={promo['state']}")
    return detail.get("status") == "completed" and promo["state"] == "promoted", notes


def s6_hitl_reject():
    """HITL at the full gate → reject API → rollback; run FAILED."""
    pre = arm()
    notes: list[str] = []
    job_id, promo = _run_to_pending_approval(pre, notes)
    if promo is None:
        return False, [*notes, "never paused for approval"]

    code, resp = http(
        "POST",
        f"{API}/promotions/{promo['promotion_id']}/reject",
        {"rejected_by": "scenario-battery", "reason": "scenario s6"},
    )
    notes.append(f"reject -> {code}")
    if code != 200:
        return False, [*notes, f"reject response: {resp}"]

    seq, detail = watch_job(job_id, 300)
    audit_job_surface(job_id, detail, notes, expect_error=True)
    promo = next(p for p in promotions() if p["promotion_id"] == promo["promotion_id"])
    notes.append(f"final: run={detail.get('status')} promo={promo['state']}")
    ok = detail.get("status") == "failed" and promo["state"] in ("rolled_back", "rejected")
    return ok, notes


def s8_deft_outage_mid_training():
    """DEFT goes down mid-training → the run must FAIL cleanly, not hang."""
    pre = arm()
    job_id = submit_job()
    watch_job(job_id, 120, stop_when=lambda d: d["status"] == "running")
    mock_scenario(deft_outage=True)
    notes = ["outage injected during training poll"]
    seq, detail = watch_job(job_id, 300)
    mock_scenario(deft_outage=False)
    notes.append(f"sequence={seq}")
    notes.append(f"error={detail.get('error')!r}")
    audit_job_surface(job_id, detail, notes, expect_error=True)
    ok = detail.get("status") == "failed" and not new_promos(pre)
    return ok, notes


def s9_cancel_api():
    """POST /jobs/{id}/cancel on a running AOI job — what actually happens?"""
    pre = arm()
    job_id = submit_job()
    watch_job(job_id, 120, stop_when=lambda d: d["status"] == "running")
    code, resp = http("POST", f"{API}/jobs/{job_id}/cancel")
    notes = [f"cancel -> {code}: {resp.get('message', resp)}"]
    seq, detail = watch_job(job_id, 300)
    notes.append(f"sequence={seq}, final={detail.get('status')}, error={detail.get('error')!r}")
    # The chain must actually STOP: give the in-flight stage time to hit the
    # next cancellation checkpoint, then assert nothing was promoted and the
    # run status was not clobbered afterwards.
    time.sleep(45)
    promos = new_promos(pre)
    notes.append(f"promotions created after cancel: {[p['state'] for p in promos]}")
    code, detail2 = http("GET", f"{API}/jobs/{job_id}")
    notes.append(f"status 45s later: {detail2.get('status')}")
    ok = detail.get("status") == "cancelled" and not promos and detail2.get("status") == "cancelled"
    return ok, notes


def s10_worker_restart_mid_promotion():
    """Worker restart mid-promotion → run CANCELLED, promotion closed out (NMP parity).

    A restart means the in-flight work died with the worker: the startup sweep
    marks the interrupted run cancelled and the cleanup manager closes out its
    non-terminal promotions — nothing may linger RUNNING/PROMOTING.

    KNOWN FOLLOW-UP (next MR): the interrupted run's dispatcher still holds
    the concurrency-1 parent slot (its unbounded chain get() never returns
    once the chain's queued stages were purged), so this scenario does NOT
    assert a follow-up rollout — the stack needs a parent-worker restart to
    accept new runs after this scenario. Run s10 LAST.
    """
    pre = arm()
    job_id = submit_job()
    watch_job(job_id, 240, stop_when=lambda d: d["status"] == "promoting")
    notes = ["restarting celery_worker while promotion is in flight"]
    docker_bin = shutil.which("docker")  # absolute path (S607)
    if not docker_bin:
        return False, [*notes, "docker CLI not found on PATH"]
    subprocess.run(
        [docker_bin, "restart", "deploy-celery_worker-1"], check=True, capture_output=True
    )
    seq, detail = watch_job(job_id, 300)
    notes.append(f"post-restart sequence={seq}")
    promo = new_promos(pre)
    promo_state = promo[0]["state"] if promo else None
    notes.append(f"interrupted run status={detail.get('status')} promo={promo_state}")
    ok = detail.get("status") == "cancelled" and promo_state in ("failed", "rejected")
    return ok, notes


SCENARIOS = {
    "s1": ("training failed", s1_training_failed),
    "s2": ("training cancelled", s2_training_cancelled),
    "s3": ("online hold → advance", s3_hold_then_advance),
    "s4": (
        "gen-2 worse-than-baseline still promotes (KPI-only gate)",
        s4_gen2_worse_still_promotes,
    ),
    "s5": ("HITL approve via API (named config)", s5_hitl_approve),
    "s6": ("HITL reject via API → rollback", s6_hitl_reject),
    "s8": ("DEFT outage mid-training", s8_deft_outage_mid_training),
    "s9": ("cancel API on AOI run (exploratory)", s9_cancel_api),
    "s10": ("worker restart mid-promotion", s10_worker_restart_mid_promotion),
}


def main() -> int:
    picks = sys.argv[1:] or list(SCENARIOS)
    results = []
    for key in picks:
        title, fn = SCENARIOS[key]
        print(f"\n════ {key}: {title} ════")
        started = time.monotonic()
        try:
            ok, notes = fn()
        except Exception as e:  # — battery must survive one bad scenario
            ok, notes = False, [f"EXCEPTION: {type(e).__name__}: {e}"]
        took = time.monotonic() - started
        for n in notes:
            print(f"    · {n}")
        print(f"    {'✅ PASS' if ok else '⛔ FAIL'} ({took:.0f}s)")
        results.append((key, title, ok, took, notes))

    print("\n════ SUMMARY ════")
    for key, title, ok, took, _ in results:
        print(f"  {'✅' if ok else '⛔'} {key:<4} {title} ({took:.0f}s)")
    return 0 if all(r[2] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
