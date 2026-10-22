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
"""Deployment e2e driver: a real AOI job through the DEPLOYED DFW stack.

Prerequisites (see tests/e2e_scripts/deft/deploy/docker-compose.deft-e2e.yaml header):
  1. mock services on the host:  MOCK_PORT=9000 uv run python -m tests.mock_services
  2. the stack:                  BACKEND=deft COMPOSE_PROFILES=deft docker compose \
                                   -f deploy/docker-compose.yaml \
                                   -f tests/e2e_scripts/deft/deploy/docker-compose.deft-e2e.yaml up --build -d

Then:  uv run python -m tests.e2e_scripts.deft.deployment_e2e [promote|regress|gate_fail]

The driver only uses the public surfaces — POST /api/jobs, GET /api/jobs/{id},
GET /api/promotions, the mock /state, and the MLflow REST alias API — exactly
like an operator would. Scenarios:
  promote   (default) mock DEFT hits the KPI, online verdicts advance
            → run COMPLETED, promotion promoted, @production = candidate
  regress   candidate passes offline but regresses online
            → rollback to baseline, run FAILED
  gate_fail mock DEFT reports sub-target metrics
            → offline gate blocks, run FAILED, Model Manager untouched
"""

from __future__ import annotations

import json
import sys
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "http://localhost:8000/api"
MOCK = "http://localhost:9000"
MLFLOW = "http://localhost:5000"

#: Promotion advances on the online-eval beat (60s cadence) — allow a few beats
#: per gate plus training/snapshot polling inside the chain.
JOB_TIMEOUT_S = 15 * 60
POLL_S = 5.0


def _http(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as resp:  # — localhost test driver
        payload = resp.read()
    return json.loads(payload) if payload else {}


def _wait(url: str, name: str, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _http("GET", url)
            print(f"  ✓ {name} up ({url})")
            return
        except (URLError, OSError):
            time.sleep(2.0)
    raise SystemExit(f"⛔ {name} not reachable at {url}")


def _mlflow_alias(model: str, alias: str) -> str | None:
    try:
        q = urlencode({"name": model, "alias": alias})
        data = _http("GET", f"{MLFLOW}/api/2.0/mlflow/registered-models/alias?{q}")
        return (data.get("model_version") or {}).get("version")
    except (URLError, OSError):
        return None


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "promote"
    knobs = {
        "promote": {"metrics": "pass", "online_behavior": "advance"},
        "regress": {"metrics": "pass", "online_behavior": "regress"},
        "gate_fail": {"metrics": "fail", "online_behavior": "advance"},
    }[scenario]

    print(f"→ scenario={scenario}")
    print("[0] readiness")
    _wait(f"{MOCK}/healthz", "mock services")
    _wait(f"{API}/jobs", "dfw api")

    print("[1] arm the mock loop")
    _http("POST", f"{MOCK}/admin/reset")
    _http("POST", f"{MOCK}/admin/scenario", knobs)

    # Promotions persist in Mongo across scenario runs — remember what already
    # exists so this run's record (or its absence) is judged by the DELTA.
    pre_existing = {
        p["promotion_id"] for p in _http("GET", f"{API}/promotions").get("promotions", [])
    }

    print("[2] POST /api/jobs (job_type=aoi_job)")
    job = _http(
        "POST",
        f"{API}/jobs",
        {"workload_id": "deploy-e2e", "client_id": "deploy-e2e", "job_type": "aoi_job"},
    )
    job_id = job["id"]
    print(f"    id={job_id} status={job['status']}")

    print("[3] poll the run to a terminal state (promotion advances on the beats)")
    deadline = time.monotonic() + JOB_TIMEOUT_S
    last = None
    while time.monotonic() < deadline:
        detail = _http("GET", f"{API}/jobs/{job_id}")
        if detail["status"] != last:
            last = detail["status"]
            print(f"    {time.strftime('%H:%M:%S')} run status: {last}")
        if last in ("completed", "failed", "cancelled"):
            break
        time.sleep(POLL_S)
    else:
        print("⛔ timed out waiting for a terminal run status")
        return 1

    print("[4] promotion record")
    promotions = _http("GET", f"{API}/promotions").get("promotions", [])
    new_promos = [p for p in promotions if p["promotion_id"] not in pre_existing]
    promo = new_promos[0] if new_promos else None
    if promo is None:
        print("    (no promotion created by this run)")
    if promo:
        print(
            f"    {promo['promotion_id']}: state={promo['state']} "
            f"candidate={promo.get('candidate_version')} baseline={promo.get('baseline_version')}"
        )

    print("[5] mock-loop trace")
    mock_state = _http("GET", f"{MOCK}/state")
    for call in mock_state["calls"]:
        if call["service"] in ("mm",) or "fine-tune" in call["call"] or "snapshots" in call["call"]:
            print(f"    [{call['service']:^7}] {call['call']}")
    print(f"    serving default: {mock_state['default_version']}")

    prod_alias = _mlflow_alias("changenet-aoi", "production")
    print(f"    MLflow @production -> version {prod_alias}")

    # DEFT's best threshold should ride the pull contract all the way to the
    # Model Manager (which persists it as a metadata.json sidecar); the mock MM
    # records the request body, so a promote run must show a non-null threshold.
    pull_thresholds = [
        c["body"].get("threshold")
        for c in mock_state["calls"]
        if c["service"] == "mm" and c["call"].endswith("/v1/models/pull") and c.get("body")
    ]
    if pull_thresholds:
        print(f"    pull threshold(s): {pull_thresholds}")

    print("[6] verdict")
    ok = False
    if scenario == "promote":
        ok = (
            last == "completed"
            and promo is not None
            and promo["state"] == "promoted"
            and mock_state["default_version"] == promo.get("candidate_version")
            # threshold must have been threaded through the pull contract
            and bool(pull_thresholds)
            and all(t is not None for t in pull_thresholds)
        )
    elif scenario == "regress":
        ok = (
            last == "failed"
            and promo is not None
            and promo["state"] == "rolled_back"
            and mock_state["default_version"] == promo.get("baseline_version")
        )
    elif scenario == "gate_fail":
        # No NEW promotion for this run, and the Model Manager never touched.
        mm_calls = [c for c in mock_state["calls"] if c["service"] == "mm"]
        ok = last == "failed" and promo is None and not mm_calls

    print(f"\n{'✅ PASS' if ok else '⛔ FAIL'} — scenario={scenario}, run={last}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
