"""Online FAR eval against a contract-faithful Storage Backend, over real HTTP.

This exercises the whole production path — ``POST /v1/results`` (selection by
model/version/role) -> poll ``GET /v1/eval/{id}`` -> presign the ``s3://``
manifests via ``POST /v1/get_presigned_urls`` -> fetch + sha256-verify -> flatten
records into targets -> compute FAR -> verdict — with nothing mocked but the
backend itself, which is implemented straight from
``storage-backend/openapi.json``.

The data carries a planted false-alarm rate, so the assertions are arithmetic:
the number that comes out the far end must equal the number that went in.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.backends.deft.eval import DeftAOIEvaluator
from src.backends.deft.provider import _eval_set_results_source
from src.backends.deft.storage_backend_client import StorageBackendClient
from src.config.deft import DEFTConfig, KpiConstraint

MODEL = "changenet_classify"
CANDIDATE = "2"
BASELINE = "1"


class _StorageBackend:
    """In-process Storage Backend implementing the eval-set + presign contract."""

    def __init__(self, records: list[dict], *, chunk: int = 64):
        self.records = records
        self.chunk = chunk
        self.eval_sets: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.selections: list[dict] = []
        self.presign_calls: list[list[str]] = []
        #: When set, blobs are corrupted AT SERVE TIME, so the bytes no longer
        #: match the sha256 the manifest advertises.
        self.tamper = False
        self._n = 0
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _create(self, selection: dict) -> dict:
        self.selections.append(selection)
        self._n += 1
        eval_id = f"eval-{self._n}"
        # Only records matching the requested model+version+role belong to the set.
        matched = [r for r in self.records if self._matches(r, selection)]
        manifests = []
        for i in range(0, max(len(matched), 1), self.chunk):
            part = matched[i : i + self.chunk]
            payload = ("".join(json.dumps(r) + "\n" for r in part)).encode()
            key = f"s3://aoi-samples/evals/{eval_id}/manifest{len(manifests)}.jsonl"
            self.blobs[key] = payload
            manifests.append(
                {
                    "format": "jsonl",
                    "uri": key,
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        self.eval_sets[eval_id] = {
            "eval_id": eval_id,
            "status": "ready",
            "total_records": len(matched),
            "manifests": manifests,
            "created_at": "2026-08-01T00:00:00Z",
        }
        return {"eval_id": eval_id}

    @staticmethod
    def _matches(record: dict, selection: dict) -> bool:
        summary = (record.get("metadata") or {}).get("inferenceSummary") or {}
        return (
            summary.get("model_name") == selection.get("model")
            and summary.get("model_version") == selection.get("version")
            and summary.get("deployment_type") == selection.get("role")
        )

    def _handler(self):
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep pytest output clean
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/v1/results":
                    self._send(201, backend._create(body["selection"]))
                elif self.path == "/v1/get_presigned_urls":
                    urls = body["urls"]
                    backend.presign_calls.append(urls)
                    items = [
                        {
                            "input": u,
                            "key": u,
                            # A presigned URL is credential-free and short-lived;
                            # here it is just a fetchable HTTP address.
                            "presigned_url": (f"{backend.base_url}/blob?key={u}&sig=deadbeef"),
                            "expires_at": "2026-08-01T01:00:00Z",
                        }
                        for u in urls
                        if u in backend.blobs
                    ]
                    failed = [
                        {"input": u, "error": "not_found"} for u in urls if u not in backend.blobs
                    ]
                    self._send(200, {"items": items, "failed": failed})
                else:
                    self._send(404, {"error": "not found"})

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                if self.path.startswith("/v1/eval/"):
                    eval_id = self.path.split("/v1/eval/", 1)[1].split("?")[0]
                    detail = backend.eval_sets.get(eval_id)
                    self._send(200, detail) if detail else self._send(404, {"error": "no"})
                elif self.path.startswith("/blob"):
                    from urllib.parse import parse_qs, urlsplit

                    key = parse_qs(urlsplit(self.path).query)["key"][0]
                    payload = backend.blobs.get(key)
                    if payload is not None and backend.tamper:
                        payload = b'{"file_path":"tampered"}\n'
                    if payload is None:
                        self._send(404, {"error": "no blob"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self._send(404, {"error": "not found"})

        return Handler


class _Plan:
    """The exact population planted, so the expected FAR is arithmetic."""

    def __init__(self, n_pass: int, n_defect: int, n_false_alarms: int, n_caught: int):
        self.n_pass = n_pass
        self.n_defect = n_defect
        self.n_false_alarms = n_false_alarms
        self.n_caught = n_caught

    @property
    def far_pct(self) -> float:
        return 100.0 * self.n_false_alarms / self.n_pass if self.n_pass else 0.0


def _planted(far_pct: float, *, boards: int = 250, crops: int = 8, role: str = "shadow"):
    """Build board records carrying a known false-alarm rate.

    250x8 targets at a 2% defect rate leaves 1960 good units, so 5.00% is exactly
    representable (98/1960) and the boundary case is a real boundary.
    """
    total = boards * crops
    n_defect = round(total * 0.02)
    n_pass = total - n_defect
    plan = _Plan(n_pass, n_defect, round(n_pass * far_pct / 100.0), round(n_defect * 0.9))

    rng = random.Random(7)
    targets = [(False, i < plan.n_false_alarms) for i in range(plan.n_pass)]
    targets += [(True, i < plan.n_caught) for i in range(plan.n_defect)]
    rng.shuffle(targets)

    records, idx = [], 0
    for b in range(boards):
        crop_list = []
        for c in range(crops):
            if idx >= len(targets):
                break
            is_defect, flagged = targets[idx]
            idx += 1
            cid = f"C{b}_{c}"
            score = round(rng.uniform(0.5, 1.0) if flagged else rng.uniform(0.0, 0.49), 6)
            verdict = "DEFECT" if flagged else "PASS"
            crop_list.append(
                {
                    "id": cid,
                    "aoiDefectInfo": {"defectType": "MISSING" if is_defect else "PASS"},
                    "inference": {
                        "inspectionId": f"insp_{b}_{cid}",
                        "score": score,
                        "verdict": verdict,
                        "threshold": 0.5,
                        "status": "completed",
                        # Deliberately not identical to the candidate, so a mapper
                        # that confused the two slots would be caught.
                        "old_model": {
                            "model_name": MODEL,
                            "version": BASELINE,
                            "score": round(rng.uniform(0, 1), 6),
                            "verdict": "DEFECT" if is_defect else "PASS",
                        },
                        "new_model": {
                            "model_name": MODEL,
                            "version": CANDIDATE,
                            "score": score,
                            "verdict": verdict,
                        },
                    },
                }
            )
        if not crop_list:
            break
        records.append(
            {
                "file_path": f"deft-aoi/synthetic/board_{b:05d}.png",
                "obj_version": "1",
                "sha256": f"{b:064x}",
                "size_byte": 4096,
                "metadata": {
                    "cropInfo": crop_list,
                    "inferenceSummary": {
                        "deployment_type": role,
                        "model_name": MODEL,
                        "model_version": CANDIDATE,
                        "cropCount": len(crop_list),
                        "completedCount": len(crop_list),
                        "failedCount": 0,
                        "status": "completed",
                    },
                },
            }
        )
    return plan, records


def _evaluator(
    backend: _StorageBackend, *, threshold: float, op: str = "<", min_sample_size: int = 100
) -> DeftAOIEvaluator:
    client = StorageBackendClient(DEFTConfig(storage_backend_url=backend.base_url))
    ev = DeftAOIEvaluator(
        results_source=_eval_set_results_source(client), min_sample_size=min_sample_size
    )
    ev.online_target = [KpiConstraint(metric="FAR_pct", op=op, threshold=threshold)]
    return ev


@pytest.fixture
def backend_factory():
    made: list[_StorageBackend] = []

    def _make(records, **kw):
        b = _StorageBackend(records, **kw)
        made.append(b)
        return b

    yield _make
    for b in made:
        b.stop()


@pytest.mark.parametrize(
    "far_pct,threshold,expected",
    [
        (0.0, 0.1, "advance"),
        (2.5, 5.0, "advance"),
        (5.0, 5.0, "rollback"),  # exactly at the bar, op '<'
        (9.0, 5.0, "rollback"),
    ],
)
def test_far_survives_the_full_contract_path(backend_factory, far_pct, threshold, expected):
    plan, records = _planted(far_pct)
    backend = backend_factory(records)
    ev = _evaluator(backend, threshold=threshold)

    samples = ev.collect({"model": MODEL, "version": CANDIDATE}, "shadow")
    result = ev.evaluate(None, None, samples)

    assert result["metrics"]["FAR_pct"] == pytest.approx(plan.far_pct, abs=1e-9)
    assert result["metrics"]["n_pass"] == plan.n_pass
    assert result["metrics"]["n_false_alarms"] == plan.n_false_alarms
    assert result["verdict"] == expected


def test_selection_is_by_model_version_and_role(backend_factory):
    """The eval set must be requested for THIS candidate, per the contract."""
    _, records = _planted(2.0)
    backend = backend_factory(records)
    _evaluator(backend, threshold=5.0).collect({"model": MODEL, "version": CANDIDATE}, "canary")
    assert backend.selections == [{"model": MODEL, "version": CANDIDATE, "role": "canary"}]


def test_manifests_are_presigned_before_fetch(backend_factory):
    """``s3://`` manifests are not fetchable directly — they must be presigned."""
    _, records = _planted(2.0)
    backend = backend_factory(records, chunk=64)
    ev = _evaluator(backend, threshold=5.0)
    ev.collect({"model": MODEL, "version": CANDIDATE}, "shadow")

    assert backend.presign_calls, "no presign was attempted"
    requested = [u for call in backend.presign_calls for u in call]
    assert requested, "presign called with no URIs"
    assert all(u.startswith("s3://") for u in requested)


def test_multiple_manifest_chunks_are_all_read(backend_factory):
    """A set split across manifests must contribute every record, not just the first."""
    plan, records = _planted(4.0)
    backend = backend_factory(records, chunk=10)  # forces many manifests
    ev = _evaluator(backend, threshold=5.0)
    samples = ev.collect({"model": MODEL, "version": CANDIDATE}, "shadow")

    detail = next(iter(backend.eval_sets.values()))
    assert len(detail["manifests"]) > 1
    assert len(samples) == plan.n_pass + plan.n_defect


def test_other_candidates_results_are_not_scored(backend_factory):
    """Mixed-version samples would corrupt the verdict; selection must exclude them."""
    _, mine = _planted(1.0)
    _, theirs = _planted(90.0)
    for r in theirs:  # same model, a DIFFERENT version, terrible FAR
        r["metadata"]["inferenceSummary"]["model_version"] = "99"
    backend = backend_factory(mine + theirs)
    ev = _evaluator(backend, threshold=5.0)

    result = ev.evaluate(None, None, ev.collect({"model": MODEL, "version": CANDIDATE}, "shadow"))
    assert result["verdict"] == "advance"


def test_unreachable_backend_holds_rather_than_advancing(backend_factory):
    _, records = _planted(0.0)
    backend = backend_factory(records)
    ev = _evaluator(backend, threshold=5.0)
    backend.stop()  # the backend dies before the eval runs

    result = ev.evaluate(None, None, ev.collect({"model": MODEL, "version": CANDIDATE}, "shadow"))
    assert result["verdict"] == "hold"


def test_corrupt_manifest_holds_rather_than_advancing(backend_factory):
    """A sha256 mismatch must not silently yield a partial, flattering population."""
    _, records = _planted(0.0)
    backend = backend_factory(records)
    ev = _evaluator(backend, threshold=5.0)
    # Serve bytes that do not match the sha256 the manifest advertises.
    backend.tamper = True

    result = ev.evaluate(None, None, ev.collect({"model": MODEL, "version": CANDIDATE}, "shadow"))
    assert result["verdict"] == "hold"
