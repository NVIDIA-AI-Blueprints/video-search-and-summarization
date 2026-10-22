"""End-to-end smoke tests exercising the actual request/response flow
against the running app (not just unit-testing individual functions) —
create, poll, stream, results, ownership, idempotency, bulk delete.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

ALICE = {"Authorization": "Bearer alice"}
BOB = {"Authorization": "Bearer bob"}
ADMIN = {"Authorization": "Bearer admin:root"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _wait_for_terminal(client: TestClient, run_id: str, headers: dict, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/v1/fine-tune/{run_id}/status", headers=headers)
        assert resp.status_code == 200
        status = resp.json()
        if status["state"] in ("completed", "failed", "cancelled"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach a terminal state within {timeout}s")


def test_health_is_public(client):
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_info_requires_auth(client):
    assert client.get("/v1/info").status_code == 401
    resp = client.get("/v1/info", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()["backend"] == "docker"


def test_workflows_and_profiles_are_seeded(client):
    workflows = client.get("/v1/workflows", headers=ALICE).json()
    assert {w["name"] for w in workflows} == {"tmm_only", "aoi_active_learning"}

    profiles = client.get("/v1/profiles", headers=ALICE).json()
    assert {p["name"] for p in profiles} == {"santomas_gdino", "aoi_default"}

    assert client.get("/v1/workflows/does_not_exist", headers=ALICE).status_code == 404
    assert client.get("/v1/profiles/does_not_exist", headers=ALICE).status_code == 404


def test_create_requires_profile_or_config(client):
    resp = client.post("/v1/fine-tune", headers=ALICE, json={})
    assert resp.status_code == 422


def test_unknown_profile_is_422(client):
    resp = client.post("/v1/fine-tune", headers=ALICE, json={"profile": "nope"})
    assert resp.status_code == 422


def test_mining_config_mutual_exclusion_is_422(client):
    resp = client.post(
        "/v1/fine-tune",
        headers=ALICE,
        json={
            "workflow": "tmm_only",
            "config": {
                "model": {"name": "grounding_dino", "checkpoint": "s3://x/y.pth"},
                "data_pool": {"path": "s3://x/pool/"},
                "mining": {"topn": 5, "desired_unique_count": 5000},
            },
        },
    )
    assert resp.status_code == 422


def test_num_gpus_over_ceiling_is_422(client):
    resp = client.post(
        "/v1/fine-tune",
        headers=ALICE,
        json={
            "workflow": "tmm_only",
            "config": {
                "model": {"name": "grounding_dino", "checkpoint": "s3://x/y.pth"},
                "data_pool": {"path": "s3://x/pool/"},
                "training": {"num_gpus": 999},
            },
        },
    )
    assert resp.status_code == 422


def test_full_run_lifecycle_from_profile(client):
    create = client.post("/v1/fine-tune", headers=ALICE, json={"profile": "santomas_gdino"})
    assert create.status_code == 201
    run_id = create.json()["id"]
    assert create.json()["state"] == "pending"

    status = _wait_for_terminal(client, run_id, ALICE)
    assert status["state"] == "completed"
    assert status["created_by"] == "alice"
    assert status["convergence_reason"] in ("kpi_met", "max_iterations_reached")

    details = client.get(f"/v1/fine-tune/{run_id}", headers=ALICE).json()
    assert details["resolved_config"]["model"]["name"] == "grounding_dino"
    assert len(details["per_iteration_metrics"]) > 0

    results = client.get(f"/v1/fine-tune/{run_id}/results", headers=ALICE).json()
    for field in ("best_checkpoint", "best_iteration", "threshold", "far_pct", "backbone", "images_dir"):
        assert field in results

    events = client.get(f"/v1/fine-tune/{run_id}/events", headers=ALICE).json()
    assert events["events"], "expected at least one stage event"
    seqs = [e["seq"] for e in events["events"]]
    assert seqs == sorted(seqs)
    assert events["next_seq"] is None  # terminal run, caller caught up

    artifacts = {a["name"] for a in client.get(f"/v1/fine-tune/{run_id}/artifacts", headers=ALICE).json()["artifacts"]}
    assert {"state_snapshot", "event_log", "report_html", "best_model_metadata", "best_model_inference_spec"} <= artifacts

    logs = client.get(f"/v1/fine-tune/{run_id}/logs", headers=ALICE).json()
    assert logs["logs"]


def test_results_before_completion_is_409(client):
    create = client.post("/v1/fine-tune", headers=ALICE, json={"profile": "aoi_default"})
    run_id = create.json()["id"]
    resp = client.get(f"/v1/fine-tune/{run_id}/results", headers=ALICE)
    assert resp.status_code == 409
    # Let the background task finish so it doesn't leak past the test.
    _wait_for_terminal(client, run_id, ALICE)


def test_ownership_scoping(client):
    create = client.post("/v1/fine-tune", headers=ALICE, json={"profile": "santomas_gdino"})
    run_id = create.json()["id"]

    assert client.get(f"/v1/fine-tune/{run_id}", headers=BOB).status_code == 403
    assert client.get(f"/v1/fine-tune/{run_id}", headers=ADMIN).status_code == 200

    mine = client.get("/v1/fine-tune", headers=ALICE).json()
    assert any(r["id"] == run_id for r in mine["items"])

    bobs = client.get("/v1/fine-tune", headers=BOB).json()
    assert not any(r["id"] == run_id for r in bobs["items"])

    assert client.get("/v1/fine-tune", headers=BOB, params={"owner": "alice"}).status_code == 403
    assert client.get("/v1/fine-tune", headers=ADMIN, params={"owner": "alice"}).status_code == 200

    _wait_for_terminal(client, run_id, ALICE)


def test_idempotency_key_replay_and_conflict(client):
    body = {"profile": "santomas_gdino"}
    headers = {**ALICE, "Idempotency-Key": "key-1"}

    first = client.post("/v1/fine-tune", headers=headers, json=body)
    assert first.status_code == 201
    run_id = first.json()["id"]

    replay = client.post("/v1/fine-tune", headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.json()["id"] == run_id

    conflict = client.post("/v1/fine-tune", headers=headers, json={"profile": "aoi_default"})
    assert conflict.status_code == 409

    _wait_for_terminal(client, run_id, ALICE)


def test_bulk_delete_requires_confirm(client):
    assert client.delete("/v1/fine-tune", headers=ADMIN).status_code == 422
    resp = client.delete("/v1/fine-tune", headers=ADMIN, params={"confirm": "true", "state": "pending"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_cancel_then_resume(client):
    create = client.post("/v1/fine-tune", headers=ALICE, json={"profile": "aoi_default"})
    run_id = create.json()["id"]

    cancel = client.post(f"/v1/fine-tune/{run_id}/cancel", headers=ALICE)
    assert cancel.status_code == 200

    status = _wait_for_terminal(client, run_id, ALICE)
    assert status["state"] == "cancelled"

    resume = client.post(f"/v1/fine-tune/{run_id}/resume", headers=ALICE)
    assert resume.status_code == 201
    assert resume.json()["state"] == "pending"

    final = _wait_for_terminal(client, run_id, ALICE)
    assert final["state"] == "completed"


def test_events_stream_and_logs_stream(client):
    create = client.post("/v1/fine-tune", headers=ALICE, json={"profile": "santomas_gdino"})
    run_id = create.json()["id"]

    with client.stream("GET", f"/v1/fine-tune/{run_id}/events/stream", headers=ALICE) as resp:
        assert resp.status_code == 200
        saw_done = False
        for line in resp.iter_lines():
            if line.startswith("event: done"):
                saw_done = True
                break
        assert saw_done

    _wait_for_terminal(client, run_id, ALICE)
