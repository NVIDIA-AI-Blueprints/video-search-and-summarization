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
"""HTTP-contract tests for the DEFT + Storage Backend clients.

Mocks ``requests.request`` to assert each client builds the correct method / URL
/ body per the OpenAPI contracts and parses responses into the neutral shapes —
plus the retry-safety contract (idempotent calls retry; resource-creating POSTs
are single-attempt so an ambiguous timeout can't double-submit).
"""

from unittest.mock import MagicMock, patch

import pytest
import requests as requests_lib

from src.backends.deft import storage_backend_client
from src.backends.deft.deft_client import DeftClient
from src.backends.deft.storage_backend_client import StorageBackendClient
from src.config.deft import DEFTConfig, SnapshotSelectionConfig


def _resp(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


class TestDeftClient:
    def _client(self):
        return DeftClient(DEFTConfig(deft_base_url="http://deft:8000"))

    def test_create_fine_tune_posts_request(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp({"id": "run-1", "state": "pending"})
            out = client.create_fine_tune({"workflow": "aoi_active_learning"})

        assert out == {"id": "run-1", "state": "pending"}
        args, kwargs = req.call_args
        assert args[0] == "POST"
        assert args[1] == "http://deft:8000/v1/fine-tune"
        assert kwargs["json"] == {"workflow": "aoi_active_learning"}

    def test_get_status_completed_is_terminal_success(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {"id": "run-1", "state": "completed", "progress": {"current_iteration": 3}}
            )
            out = client.get_status("run-1")

        assert out["status"] == "completed"
        assert out["terminal"] is True
        assert out["succeeded"] is True
        assert out["progress"] == {"current_iteration": 3}
        assert req.call_args[0][1] == "http://deft:8000/v1/fine-tune/run-1/status"

    def test_get_status_running_not_terminal(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp({"id": "run-1", "state": "running"})
            out = client.get_status("run-1")

        assert out["terminal"] is False
        assert out["succeeded"] is False

    def test_get_status_failed_is_terminal_not_success(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp({"id": "run-1", "state": "failed"})
            out = client.get_status("run-1")

        assert out["terminal"] is True
        assert out["succeeded"] is False

    def test_get_results_extracts_checkpoint_and_metrics(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "lustre://deft-fs/run-1/iter_3/weights.pt",
                    "best_iteration": 3,
                    "metrics": {"mAP50": 0.775},
                }
            )
            out = client.get_results("run-1")

        assert out["artifact_uri"] == "lustre://deft-fs/run-1/iter_3/weights.pt"
        assert out["checkpoint"] == "lustre://deft-fs/run-1/iter_3/weights.pt"
        assert out["metrics"] == {"mAP50": 0.775}
        assert out["best_iteration"] == 3
        # training_spec absent on older services -> None, never a KeyError.
        assert out["training_spec"] is None
        # threshold absent on older services -> None, never a KeyError.
        assert out["threshold"] is None
        # sha256 absent on older services -> None, never a KeyError.
        assert out["sha256"] is None
        assert req.call_args[0][1] == "http://deft:8000/v1/fine-tune/run-1/results"

    def test_get_results_extracts_checkpoint_sha256(self):
        # The checkpoint digest MUST be surfaced so the controller can hand the
        # Model Manager a verifiable PullRequest.sha256 (else pull 409s at
        # checksum and the promotion dies at the pull gate — the F1 regression).
        client = self._client()
        digest = "b" * 64
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "s3://aoi-artifacts/runs/run-1/weights.onnx",
                    "best_checkpoint_sha256": digest,
                    "metrics": {},
                }
            )
            out = client.get_results("run-1")

        assert out["sha256"] == digest

    def test_get_results_sha256_falls_back_to_legacy_keys(self):
        # Rolling upgrades may emit "checkpoint_sha256" or a bare "sha256";
        # the canonical "best_checkpoint_sha256" wins when present.
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "s3://aoi-artifacts/runs/run-1/weights.onnx",
                    "checkpoint_sha256": "c" * 64,
                    "metrics": {},
                }
            )
            out = client.get_results("run-1")

        assert out["sha256"] == "c" * 64

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            # canonical wins when it coexists with both legacy keys
            (
                {
                    "best_checkpoint_sha256": "b" * 64,
                    "checkpoint_sha256": "c" * 64,
                    "sha256": "d" * 64,
                },
                "b" * 64,
            ),
            # legacy "checkpoint_sha256" used when canonical absent
            ({"checkpoint_sha256": "c" * 64}, "c" * 64),
            # bare "sha256" used as the last fallback
            ({"sha256": "d" * 64}, "d" * 64),
            # none present -> None, never a KeyError
            ({}, None),
        ],
    )
    def test_get_results_sha256_key_precedence(self, payload, expected):
        # Full compatibility contract: canonical best_checkpoint_sha256 wins,
        # then checkpoint_sha256, then bare sha256, else None.
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "s3://aoi-artifacts/runs/run-1/weights.onnx",
                    "metrics": {},
                    **payload,
                }
            )
            out = client.get_results("run-1")

        assert out["sha256"] == expected

    def test_get_results_extracts_threshold(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "s3://aoi-artifacts/runs/run-1/weights.onnx",
                    "metrics": {},
                    "threshold": 0.42,
                }
            )
            out = client.get_results("run-1")

        assert out["threshold"] == 0.42

    def test_get_results_threshold_falls_back_to_best_threshold(self):
        # Older DEFT services emit "best_threshold"; the canonical "threshold"
        # key wins when both are present, else best_threshold is used.
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "s3://aoi-artifacts/runs/run-1/weights.onnx",
                    "metrics": {},
                    "best_threshold": 0.63,
                }
            )
            out = client.get_results("run-1")

        assert out["threshold"] == 0.63

    def test_get_results_threshold_null_falls_back_to_best_threshold(self):
        # During a rolling upgrade a service may send the canonical key as an
        # explicit null alongside the legacy compat field; the fallback must
        # still resolve to best_threshold rather than keeping None.
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "s3://aoi-artifacts/runs/run-1/weights.onnx",
                    "metrics": {},
                    "threshold": None,
                    "best_threshold": 0.63,
                }
            )
            out = client.get_results("run-1")

        assert out["threshold"] == 0.63

    def test_get_results_carries_the_published_training_spec(self):
        # DEFT services that publish artifacts return the spec URI next to the
        # checkpoint; the serving stack stages it as spec.yaml beside the
        # weights, so the client must not drop it.
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "best_checkpoint": "s3://aoi-artifacts/runs/run-1/weights.onnx",
                    "best_iteration": 3,
                    "metrics": {"FAR_pct": 0.5},
                    "training_spec": "s3://aoi-artifacts/runs/run-1/baseline_spec.yaml",
                }
            )
            out = client.get_results("run-1")

        assert out["training_spec"] == "s3://aoi-artifacts/runs/run-1/baseline_spec.yaml"

    def test_get_status_surfaces_error_and_convergence_reason(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "id": "run-1",
                    "state": "failed",
                    "error": "OOM on worker 0",
                    "convergence_reason": None,
                }
            )
            out = client.get_status("run-1")
        assert out["error"] == "OOM on worker 0"

        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp(
                {"id": "run-1", "state": "completed", "convergence_reason": "kpi_met"}
            )
            out = client.get_status("run-1")
        assert out["convergence_reason"] == "kpi_met"

    @staticmethod
    def _http_error_resp(status_code: int):
        err = requests_lib.HTTPError(f"{status_code} error")
        err.response = MagicMock(status_code=status_code)
        resp = MagicMock()
        resp.raise_for_status.side_effect = err
        return resp

    def test_get_results_retries_through_409_lag(self):
        # Status flips terminal slightly before /results materializes (the
        # contract 409: "Run has not completed yet") — bridge it, don't fail.
        client = self._client()
        ok = _resp({"best_checkpoint": "ckpt", "best_iteration": 1, "metrics": {}})
        with (
            patch("src.backends.deft.deft_client.requests.request") as req,
            patch("src.backends.deft.deft_client.time.sleep") as slept,
        ):
            req.side_effect = [self._http_error_resp(409), self._http_error_resp(409), ok]
            out = client.get_results("run-1")
        assert out["checkpoint"] == "ckpt"
        assert req.call_count == 3
        assert slept.call_count == 2

    def test_get_results_non_409_http_error_propagates(self):
        client = self._client()
        with (
            patch("src.backends.deft.deft_client.requests.request") as req,
            patch("src.backends.deft.deft_client.time.sleep"),
        ):
            req.return_value = self._http_error_resp(404)
            with pytest.raises(requests_lib.HTTPError):
                client.get_results("run-1")
        assert req.call_count == 1  # no retry for a real error

    def test_cancel_posts_and_returns_none(self):
        client = self._client()
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.return_value = _resp({"id": "run-1", "state": "cancelled"})
            out = client.cancel("run-1")

        assert out is None
        assert req.call_args[0][0] == "POST"
        assert req.call_args[0][1] == "http://deft:8000/v1/fine-tune/run-1/cancel"


class TestStorageBackendClient:
    def _client(self):
        return StorageBackendClient(DEFTConfig(storage_backend_url="http://storage-backend:8080"))

    def test_get_stats(self):
        client = self._client()
        stats = {"total_records": 100, "records_since_last_snapshot": 10}
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp(stats)
            out = client.get_stats()

        assert out == stats
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1] == "http://storage-backend:8080/v1/snapshot/stats"

    def test_create_snapshot_builds_request_body(self):
        client = self._client()
        selection = SnapshotSelectionConfig(
            mode="query",
            # A facet the AOI index template actually defines: every key here
            # compiles to a term on ``metadata.content_metadata.<key>``, so an
            # invented name (``classes``) is accepted and matches nothing.
            filters={"created_after": "2026-07-01T00:00:00Z", "machineId": ["press-1", "press-2"]},
            limit=100,
        )
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"snapshot_id": "snap_1"})  # contract v2: ID-only
            out = client.create_snapshot(selection, reason="pool_size_threshold")

        assert out["snapshot_id"] == "snap_1"
        args, kwargs = req.call_args
        assert args[0] == "POST"
        assert args[1] == "http://storage-backend:8080/v1/snapshot"
        body = kwargs["json"]
        # Facets are spread FLAT onto the selection (the backend reads
        # ``created_after`` etc. at the top level), not nested under ``filters``.
        assert "filters" not in body["selection"]
        assert body["selection"]["created_after"] == "2026-07-01T00:00:00Z"
        assert body["selection"]["machineId"] == ["press-1", "press-2"]
        assert body["reason"] == "pool_size_threshold"

    def test_create_snapshot_never_sends_limit_as_a_facet(self):
        """A configured ``limit`` is withheld, not relocated.

        The selection object has no cap field: every key inside it is compiled
        into a term on ``metadata.content_metadata.<key>``, so a ``limit`` sent
        there is a facet no record carries and the snapshot comes back READY and
        EMPTY. There is nowhere else on the request to put it — ``SnapshotRequest``
        is {selection, snapshot_id, reason, metadata} — so it goes nowhere.
        """
        client = self._client()
        selection = SnapshotSelectionConfig(
            mode="query", filters={"created_after": "2026-07-01T00:00:00Z"}, limit=100
        )
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"snapshot_id": "snap_3"})
            client.create_snapshot(selection)

        body = req.call_args.kwargs["json"]
        assert "limit" not in body["selection"]
        assert body["selection"] == {"created_after": "2026-07-01T00:00:00Z"}

    def test_create_snapshot_omits_limit_when_none(self):
        client = self._client()
        selection = SnapshotSelectionConfig(
            mode="query", filters={"created_after": "2026-07-01T00:00:00Z"}
        )
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"snapshot_id": "snap_2"})
            client.create_snapshot(selection)

        body = req.call_args.kwargs["json"]
        assert body["selection"]["created_after"] == "2026-07-01T00:00:00Z"
        assert "limit" not in body["selection"]
        assert "reason" not in body

    def test_get_snapshot(self):
        client = self._client()
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "snapshot_id": "snap_1",
                    "status": "ready",
                    "manifests": [
                        {
                            "format": "jsonl",
                            "uri": "s3://aoi-artifacts/snapshots/snap_1/manifest0.jsonl",
                            "size_bytes": 1,
                            "sha256": "0" * 64,
                        }
                    ],
                }
            )
            out = client.get_snapshot("snap_1")

        assert out["status"] == "ready"
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1] == "http://storage-backend:8080/v1/snapshot/snap_1"

    # --- KPI set + eval set (Storage Backend design §4.2 / §4.3) -------------
    def test_create_eval_set_wraps_selection_envelope(self):
        # The CLIENT owns the CreateEvaluationSetRequest envelope — a flat
        # body would 422 on a validating backend (additionalProperties: false).
        client = self._client()
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"eval_id": "eval-1"})  # contract v2: ID-only
            out = client.create_eval_set({"model": "m", "version": "v2", "role": "shadow"})

        assert out["eval_id"] == "eval-1"
        args, kwargs = req.call_args
        assert args[0] == "POST"
        assert args[1] == "http://storage-backend:8080/v1/results"
        assert kwargs["json"] == {"selection": {"model": "m", "version": "v2", "role": "shadow"}}

    def test_create_eval_set_rejects_empty_selection(self):
        client = self._client()
        with pytest.raises(ValueError, match="selection is required"):
            client.create_eval_set({})

    def test_get_eval_stats_uses_detail_path(self):
        client = self._client()
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"eval_id": "eval-1", "status": "ready", "manifests": []})
            out = client.get_eval_stats("eval-1")

        assert out["status"] == "ready"
        args, _ = req.call_args
        assert args[0] == "GET"
        assert args[1] == "http://storage-backend:8080/v1/eval/eval-1"

    def test_derive_storage_prefix_from_manifests(self):
        from src.backends.deft.storage_backend_client import derive_storage_prefix

        detail = {
            "manifests": [
                {"uri": "s3://aoi-artifacts/snapshots/snap_9/manifest0.jsonl"},
            ]
        }
        assert derive_storage_prefix(detail) == "s3://aoi-artifacts/snapshots/snap_9/"
        # Legacy field wins when present; None when nothing usable.
        assert derive_storage_prefix({"storage_prefix": "s3://x/"}) == "s3://x/"
        assert derive_storage_prefix({}) is None

    def test_get_presigned_urls_posts_body_and_parses_items_failed(self):
        # POST /v1/get_presigned_urls with the object-store locators + expires_in;
        # the response's items/failed lists are parsed through verbatim.
        client = self._client()
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp(
                {
                    "items": [
                        {
                            "input": "s3://aoi-artifacts/runs/v1/weights.onnx",
                            "key": "runs/v1/weights.onnx",
                            "presigned_url": "https://store/runs/v1/weights.onnx?X-Amz-Signature=a",
                            "expires_at": "2026-07-22T01:00:00Z",
                        }
                    ],
                    "failed": [{"input": "s3://aoi-artifacts/missing.onnx", "error": "not found"}],
                }
            )
            out = client.get_presigned_urls(
                ["s3://aoi-artifacts/runs/v1/weights.onnx", "s3://aoi-artifacts/missing.onnx"],
                expires_in=1800,
            )

        args, kwargs = req.call_args
        assert args[0] == "POST"
        assert args[1] == "http://storage-backend:8080/v1/get_presigned_urls"
        assert kwargs["json"] == {
            "urls": [
                "s3://aoi-artifacts/runs/v1/weights.onnx",
                "s3://aoi-artifacts/missing.onnx",
            ],
            "expires_in": 1800,
        }
        assert out["items"][0]["presigned_url"].startswith("https://store/")
        assert out["failed"][0]["input"] == "s3://aoi-artifacts/missing.onnx"

    def test_get_presigned_urls_omits_expires_in_when_unset(self):
        client = self._client()
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"items": [], "failed": []})
            client.get_presigned_urls(["s3://b/k"])

        body = req.call_args.kwargs["json"]
        assert body == {"urls": ["s3://b/k"]}
        assert "expires_in" not in body

    def test_create_kpi_set_and_stats(self):
        client = self._client()
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"kpi_id": "kpi-1"})  # contract v2: ID-only
            out = client.create_kpi_set(reason="kpi refresh")
        assert out["kpi_id"] == "kpi-1"
        args, kwargs = req.call_args
        assert args[0] == "POST"
        assert args[1] == "http://storage-backend:8080/v1/kpi"
        # KpiSetSelection is a contract const — always the canonical value.
        assert kwargs["json"] == {"selection": {"tag": "kpi"}, "reason": "kpi refresh"}

    def test_create_kpi_set_rejects_non_canonical_selection(self):
        client = self._client()
        with pytest.raises(ValueError, match="tag"):
            client.create_kpi_set({"mode": "query"})

        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"kpi_id": "kpi-1", "status": "ready", "manifests": []})
            out = client.get_kpi_stats("kpi-1")
        assert out["status"] == "ready"
        assert req.call_args[0][1] == "http://storage-backend:8080/v1/kpi/kpi-1"


class TestRetrySafety:
    """Resource-creating POSTs must NOT retry (double-submit risk); GETs must."""

    def test_create_fine_tune_does_not_retry_on_timeout(self):
        # A timeout is ambiguous — the DEFT service may already have accepted
        # the run. Retrying could launch a duplicate training job.
        client = DeftClient(DEFTConfig(deft_base_url="http://deft:8000"))
        with patch("src.backends.deft.deft_client.requests.request") as req:
            req.side_effect = requests_lib.exceptions.Timeout("timed out")
            with pytest.raises(requests_lib.exceptions.Timeout):
                client.create_fine_tune({"workflow": "aoi_active_learning"})
        assert req.call_count == 1

    def test_create_snapshot_does_not_retry_on_timeout(self):
        client = StorageBackendClient(DEFTConfig(storage_backend_url="http://storage-backend:8080"))
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.side_effect = requests_lib.exceptions.Timeout("timed out")
            with pytest.raises(requests_lib.exceptions.Timeout):
                client.create_snapshot(
                    SnapshotSelectionConfig(
                        mode="query", filters={"created_after": "2026-07-01T00:00:00Z"}
                    )
                )
        assert req.call_count == 1

    def test_get_status_retries_on_transient_error(self):
        # Idempotent GET: transient failure then success -> retried, no error.
        client = DeftClient(DEFTConfig(deft_base_url="http://deft:8000"))
        with (
            patch("src.backends.deft.deft_client.requests.request") as req,
            patch("src.lib.retry.sleep"),
        ):
            req.side_effect = [
                requests_lib.exceptions.ConnectionError("Connection reset"),
                _resp({"id": "run-1", "state": "completed"}),
            ]
            out = client.get_status("run-1")
        assert out["terminal"] is True
        assert req.call_count == 2

    def test_retry_helper_rejects_invalid_bounds(self):
        # max_retries=0 would skip the loop and silently return None.
        from src.lib.retry import retry_on_transient_error

        with pytest.raises(ValueError, match="max_retries"):
            retry_on_transient_error(lambda: 1, "op", max_retries=0)
        with pytest.raises(ValueError, match="backoff_factor"):
            retry_on_transient_error(lambda: 1, "op", backoff_factor=0)

    def test_neutral_retry_helper_has_no_nmp_sdk_dependency(self):
        # The DEFT backend must import cleanly on images without nemo_platform.
        import ast

        src_file = "src/lib/retry.py"
        tree = ast.parse(open(src_file).read())
        imported = {
            n.name if isinstance(node, ast.Import) else node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for n in getattr(node, "names", [None])
        }
        assert not any("nemo_platform" in (m or "") for m in imported)


class TestStorageCatalogRouting:
    """Each shipped use case sends the paths its catalog entry selects.

    The Storage Backend scoped `POST /v1/{catalog}/snapshot` and
    `POST /v1/{catalog}/kpi`, keeping the unprefixed paths as aliases that
    resolve to the AOI catalog. `_CATALOG_BY_USE_CASE` decides per use case:
    AOI maps to `None` because a backend that has not taken that change serves
    only the alias, while ITS names its catalog because its records live in a
    separate index that only the scoped routes address.

    Both branches therefore have a shipped caller. The `None` branch is also
    driven directly by `test_a_use_case_mapped_to_no_catalog_keeps_the_alias`,
    so that inverting the condition in `_catalog_path` would ship a broken route
    with a green suite.
    """

    def _client(self, use_case=None):
        return StorageBackendClient(
            DEFTConfig(storage_backend_url="http://sb:8080"), use_case=use_case
        )

    def _call(self, client, method):
        with patch("src.backends.deft.storage_backend_client.requests.request") as req:
            req.return_value = _resp({"snapshot_id": "s1", "kpi_id": "k1", "status": "ready"})
            method(client)
            return req.call_args[0][1]

    def test_aoi_keeps_every_path_unprefixed(self):
        """Deliberate and TEMPORARY -- see `_CATALOG_BY_USE_CASE`.

        The contract (aoi-api!13) calls the unprefixed paths a legacy alias and
        says new callers should name the catalog. We do not yet, because a
        Storage Backend that has not deployed that change serves only these. When
        it is deployed everywhere this test flips to `/v1/aoi/...` along with the
        one-line map change -- it is not pinning the alias forever.
        """
        c = self._client("aoi")
        assert self._call(c, lambda x: x.get_stats()) == "http://sb:8080/v1/snapshot/stats"
        assert self._call(c, lambda x: x.get_snapshot("s1")) == "http://sb:8080/v1/snapshot/s1"

    def test_no_use_case_keeps_every_path_unprefixed(self):
        """Legacy callers that never routed by use case are unchanged."""
        c = self._client(None)
        assert self._call(c, lambda x: x.get_stats()) == "http://sb:8080/v1/snapshot/stats"

    def test_its_is_scoped_to_its_own_catalog(self):
        """ITS owns a catalog, so every scoped route names it.

        The ITS records live in a separate index that only the catalog-scoped
        routes address; the unprefixed alias resolves to AOI's catalog, so it
        would come back EMPTY rather than erroring.
        """
        c = self._client("its")
        assert self._call(c, lambda x: x.get_stats()) == "http://sb:8080/v1/its/snapshot/stats"
        assert self._call(c, lambda x: x.get_snapshot("s1")) == "http://sb:8080/v1/its/snapshot/s1"

    def test_creation_routes_per_shipped_use_case(self):
        """The POSTs the backend actually scoped -- the GETs alone do not prove it.

        AOI keeps the alias and ITS names its catalog, so the two shipped cases
        exercise BOTH branches of the map on the creating verbs.
        """
        sel = SnapshotSelectionConfig(
            mode="query", filters={"created_after": "1970-01-01T00:00:00Z"}
        )
        aoi = self._client("aoi")
        assert self._call(aoi, lambda x: x.create_snapshot(sel)) == "http://sb:8080/v1/snapshot"
        assert self._call(aoi, lambda x: x.create_kpi_set()) == "http://sb:8080/v1/kpi"

        its = self._client("its")
        assert self._call(its, lambda x: x.create_snapshot(sel)) == "http://sb:8080/v1/its/snapshot"
        assert self._call(its, lambda x: x.create_kpi_set()) == "http://sb:8080/v1/its/kpi"

    def test_a_use_case_mapped_to_no_catalog_keeps_the_alias(self):
        """The other branch, driven directly: an explicit ``None`` sends the alias.

        AOI ships that way deliberately, so a Storage Backend that has not
        deployed the catalog-scoped contract is still served. Driving it through
        ITS keeps the assertion on the MAP rather than on AOI's own entry.
        """
        sel = SnapshotSelectionConfig(
            mode="query", filters={"created_after": "1970-01-01T00:00:00Z"}
        )
        with patch.dict(storage_backend_client._CATALOG_BY_USE_CASE, {"its": None}):
            c = self._client("its")
            assert self._call(c, lambda x: x.get_stats()) == "http://sb:8080/v1/snapshot/stats"
            assert self._call(c, lambda x: x.create_snapshot(sel)) == "http://sb:8080/v1/snapshot"
            assert self._call(c, lambda x: x.create_kpi_set()) == "http://sb:8080/v1/kpi"

    def test_only_the_routes_the_backend_scoped_are_prefixed(self):
        """GET /v1/kpi/{id} and GET /v1/eval/{id} were NOT scoped upstream."""
        c = self._client("its")
        assert self._call(c, lambda x: x.get_kpi_stats("k1")) == "http://sb:8080/v1/kpi/k1"
        assert self._call(c, lambda x: x.get_eval_stats("e1")) == "http://sb:8080/v1/eval/e1"

    def test_an_unknown_use_case_falls_back_to_unprefixed(self):
        """Absent from the map means AOI's catalog, not a catalog of its own.

        Deriving the catalog from the NAME would emit `/v1/<name>/snapshot`,
        which the backend's Literal["aoi", "its"] rejects outright -- a 422 on
        every snapshot rather than the shared catalog the caller expected.
        """
        c = self._client("some_future_aoi_family_case")
        assert self._call(c, lambda x: x.get_stats()) == "http://sb:8080/v1/snapshot/stats"
