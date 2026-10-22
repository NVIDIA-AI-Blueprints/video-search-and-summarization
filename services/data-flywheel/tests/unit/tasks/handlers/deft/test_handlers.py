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
"""Unit tests for the DEFT-loop pipeline stage tasks (``src/tasks/deft_tasks.py``).

Mocks the neutral provider seam (``get_backend_provider``) and the db_manager so
each stage is exercised in isolation: correct context threading, bounded
polling, the failed-gate stop mechanism, and MLflow candidate registration.

Named ``test_handlers.py`` to mirror the module it covers the handlers of,
``src/tasks/handlers/deft/handlers.py`` — whose AOI-prefixed names (``AOIStageError``,
``_AOIHandler``, ``FinalizeAOIRunHandler``) are historical and serve the whole
DEFT family, NOT an ``aoi_job`` restriction. The stages here are the same eight
an ``its_job`` run executes; everything that actually differs between family
members is resolved downstream from ``use_case``, never from ``job_type``.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.config.base import MLflowConfig
from src.tasks import deft_tasks
from src.tasks.handlers.deft import handlers as _deft_handlers


@contextmanager
def _mlflow_enabled(enabled: bool = True):
    """Toggle the global MLflow config for the AOI register stage (F3).

    ``RegisterCandidateHandler`` mints the serving version from the Mongo
    counter and records MLflow BEST-EFFORT (MLflow is not the version
    authority). This just controls the ``enabled`` flag the handler reads when
    constructing the client; the promotion proceeds either way.
    """
    original = settings.system_config.mlflow_config
    settings.system_config.mlflow_config = MLflowConfig(enabled=enabled)
    try:
        yield
    finally:
        settings.system_config.mlflow_config = original


def _counter_db(seq: int = 1):
    """A db double whose serving-version counter mints ``seq`` for any model."""
    db = MagicMock()
    db.serving_version_counters.find_one_and_update.return_value = {"seq": seq}
    return db


_FW_ID = "507f1f77bcf86cd799439011"


def _fake_presign_client(signed: dict[str, str], failed: list[dict] | None = None):
    """A StorageBackendClient stand-in whose get_presigned_urls returns a fixed
    ``{items, failed}`` map (input locator -> presigned HTTPS URL)."""
    client = MagicMock()
    client.get_presigned_urls.return_value = {
        "items": [
            {"input": src, "key": src, "presigned_url": url, "expires_at": "2026-07-22T01:00:00Z"}
            for src, url in signed.items()
        ],
        "failed": failed or [],
    }
    return client


_JOB_CONFIG = {
    "use_case": "aoi",
    "model_name": "changenet-aoi",
    "selection": {"mode": "query", "filters": {"created_after": "1970-01-01T00:00:00Z"}},
    "deft": {"config": {"model": {"checkpoint": "s3://aoi-models/base/model.pt"}}},
    "eval": {},
    "promotion_policy": {},
}


class TestInitialize:
    def test_marks_running_and_builds_context(self):
        db = MagicMock()
        # initialize now resolves the evaluator through the provider seam to
        # fail fast on an unknown use case, so the seam has to be stood up here.
        provider = MagicMock()
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.backends.factory.get_backend_provider", return_value=provider),
        ):
            ctx = deft_tasks.initialize_aoi_workflow(
                workload_id="w1",
                flywheel_run_id=_FW_ID,
                client_id="c1",
                job_type="aoi_job",
                job_config=_JOB_CONFIG,
            )

        db.update_flywheel_run_status.assert_called_once()
        db.set_flywheel_started.assert_called_once()
        provider.evaluator.assert_called_once_with(use_case="aoi")
        assert ctx["flywheel_run_id"] == _FW_ID
        assert ctx["use_case"] == "aoi"
        assert ctx["model_name"] == "changenet-aoi"
        assert ctx["job_config"] == _JOB_CONFIG


class TestSelectAndSnapshot:
    def test_polls_until_ready_and_threads_context(self):
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "creating",
            "storage_prefix": "s3://x/snap_1/",
        }
        datastore.get_snapshot.side_effect = [
            {"snapshot_id": "snap_1", "status": "creating", "storage_prefix": "s3://x/snap_1/"},
            {
                "snapshot_id": "snap_1",
                "status": "ready",
                "storage_prefix": "s3://x/snap_1/",
                "total_records": 12,
            },
        ]
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "changenet-aoi", "use_case": "aoi", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        # The datastore is resolved against the run's use case (one process
        # serves several, and they do not share object stores).
        provider.datastore.assert_called_once_with("changenet-aoi", use_case="aoi")
        datastore.register.assert_called_once()
        assert ctx["snapshot_id"] == "snap_1"
        assert ctx["snapshot_uri"] == "s3://x/snap_1/"

    def test_snapshot_failure_stamps_error_without_breaking_the_chain(self):
        # NMP-parity: a failed stage returns the ctx with `error` stamped (so
        # later stages skip); it never raises out of the Celery chain.
        datastore = MagicMock()
        datastore.register.return_value = {"snapshot_id": "snap_1", "status": "failed"}
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "m", "use_case": "aoi", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)
        assert "did not become ready" in ctx["error"]

    def test_deleted_snapshot_fails_fast_without_polling_to_timeout(self):
        # "deleted" is terminal (snapshot GC'd under us) — fail immediately
        # instead of burning SNAPSHOT_POLL_MAX_ATTEMPTS polls.
        datastore = MagicMock()
        datastore.register.return_value = {"snapshot_id": "snap_1", "status": "deleted"}
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "m", "use_case": "aoi", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)
        assert "deleted" in ctx["error"]
        datastore.get_snapshot.assert_not_called()

    def _empty_snapshot_run(self, snapshot, job_config, patched_sleep):
        """Run select_and_snapshot then run_deft_training over one provider.

        Both stages are driven because the gate's job is not to raise — it is to
        keep DEFT from being started on a pool with nothing in it, and that is
        only observable one stage later.
        """
        datastore = MagicMock()
        datastore.register.return_value = snapshot
        training = MagicMock()
        provider = MagicMock()
        provider.datastore.return_value = datastore
        provider.training.return_value = training

        prev = {"model_name": "m", "use_case": "aoi", "job_config": job_config}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep(),
        ):
            ctx = deft_tasks.run_deft_training(deft_tasks.select_and_snapshot(prev))
        return ctx, training

    def test_empty_ready_snapshot_blocks_the_run_before_training(self, patched_sleep):
        # The shipped failure: a selection whose facet VALUES match nothing is
        # accepted, the snapshot reports ready, and the pool is empty. The error
        # names the snapshot and the filters because the filters are the fix.
        snapshot = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "total_records": 0,
            "manifests": [],
        }
        ctx, training = self._empty_snapshot_run(snapshot, _JOB_CONFIG, patched_sleep)

        assert "snap_1" in ctx["error"]
        assert "0 records" in ctx["error"]
        assert "created_after" in ctx["error"]
        assert "snapshot_id" not in ctx
        training.start_run.assert_not_called()

    def test_no_total_records_and_no_manifests_is_a_definite_zero(self, patched_sleep):
        # A ready snapshot with nothing to retrieve is empty whether or not the
        # backend bothered to count it.
        snapshot = {"snapshot_id": "snap_1", "status": "ready", "manifests": []}
        ctx, training = self._empty_snapshot_run(snapshot, _JOB_CONFIG, patched_sleep)

        assert "0 records" in ctx["error"]
        training.start_run.assert_not_called()

    def test_a_pool_under_the_configured_minimum_is_refused(self, patched_sleep):
        # min_snapshot_records is a floor, not just an emptiness check: a use
        # case with a known minimum viable pool raises it above the default 1.
        snapshot = {"snapshot_id": "snap_1", "status": "ready", "total_records": 3}
        job_config = {**_JOB_CONFIG, "min_snapshot_records": 10}
        ctx, training = self._empty_snapshot_run(snapshot, job_config, patched_sleep)

        assert "3 records" in ctx["error"]
        assert "min_snapshot_records=10" in ctx["error"]
        training.start_run.assert_not_called()

    def test_manifest_counts_stand_in_for_an_absent_total(self, patched_sleep):
        # total_records is optional in the contract; the manifests are the other
        # place the count is stated, and a run backed by real records proceeds.
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "storage_prefix": "s3://x/snap_1/",
            "manifests": [{"record_count": 4}, {"record_count": 7}],
        }
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "m", "use_case": "aoi", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep(),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "error" not in ctx
        assert ctx["snapshot_id"] == "snap_1"

    def test_manifests_with_no_counts_at_all_are_unknown_not_empty(self, patched_sleep):
        # The decided behaviour, pinned: manifests that state no count leave the
        # size UNKNOWN, and the gate does not fail a snapshot it cannot measure.
        # Summing a list of nulls to 0 would reject a perfectly good pool.
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "storage_prefix": "s3://x/snap_1/",
            "manifests": [{"uri": "s3://x/snap_1/manifest0.jsonl", "record_count": None}],
        }
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "m", "use_case": "aoi", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep(),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "error" not in ctx
        assert ctx["snapshot_id"] == "snap_1"

    def test_an_absent_manifests_key_is_unknown_not_empty(self, patched_sleep):
        # An omitted key is not the same statement as `manifests: []`: the first
        # says nothing, the second says there is nothing to retrieve.
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "storage_prefix": "s3://x/snap_1/",
        }
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "m", "use_case": "aoi", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep(),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "error" not in ctx
        assert ctx["snapshot_id"] == "snap_1"

    def test_pull_kpi_set_threads_kpi_uri_when_enabled(self):
        # kpi.pull_from_storage=True → create a KPI set, poll to ready, and thread its
        # derived prefix as ctx["kpi_set_uri"] (DEFT uses it as kpi/testing).
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "storage_prefix": "s3://x/snap_1/",
            "total_records": 12,
        }
        datastore.register_kpi_set.return_value = {"kpi_id": "kpi_1", "status": "creating"}
        datastore.get_kpi_set.side_effect = [
            {
                "kpi_id": "kpi_1",
                "status": "ready",
                "manifests": [{"uri": "s3://x/kpi/kpi_1/manifest0.jsonl"}],
            },
        ]
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {
            "model_name": "changenet-aoi",
            "use_case": "aoi",
            "job_config": {**_JOB_CONFIG, "kpi": {"pull_from_storage": True}},
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        datastore.register_kpi_set.assert_called_once()
        assert ctx["kpi_set_uri"] == "s3://x/kpi/kpi_1/"  # trailing-slash prefix DEFT converts

    def test_no_kpi_pull_when_disabled_or_absent(self):
        # The handler pulls only when the resolved job_config carries a truthy
        # kpi.pull_from_storage. Both an absent-from-dict flag and an explicit
        # False skip the pull (DEFT carves kpi). NB: the config-model default is
        # now True, so a rendered config pulls unless explicitly disabled.
        for jc in (_JOB_CONFIG, {**_JOB_CONFIG, "kpi": {"pull_from_storage": False}}):
            datastore = MagicMock()
            datastore.register.return_value = {
                "snapshot_id": "snap_1",
                "status": "ready",
                "storage_prefix": "s3://x/snap_1/",
                "total_records": 12,
            }
            provider = MagicMock()
            provider.datastore.return_value = datastore
            prev = {"model_name": "m", "use_case": "aoi", "job_config": jc}
            with (
                patch("src.backends.factory.get_backend_provider", return_value=provider),
                patch("src.tasks.handlers.deft.handlers.time.sleep"),
            ):
                ctx = deft_tasks.select_and_snapshot(prev)
            datastore.register_kpi_set.assert_not_called()
            assert "kpi_set_uri" not in ctx

    def test_kpi_set_registration_without_id_fails_fast(self):
        # A malformed register_kpi_set payload (no kpi_id) must fail fast, not
        # poll get_kpi_set(None) and surface a misleading error.
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "storage_prefix": "s3://x/snap_1/",
            "total_records": 12,
        }
        datastore.register_kpi_set.return_value = {"status": "error"}  # no kpi_id
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {
            "model_name": "m",
            "use_case": "aoi",
            "job_config": {**_JOB_CONFIG, "kpi": {"pull_from_storage": True}},
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "no kpi_id" in ctx["error"]
        datastore.get_kpi_set.assert_not_called()

    def test_kpi_set_not_ready_stamps_error(self):
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "storage_prefix": "s3://x/snap_1/",
            "total_records": 12,
        }
        datastore.register_kpi_set.return_value = {"kpi_id": "kpi_1", "status": "failed"}
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {
            "model_name": "m",
            "use_case": "aoi",
            "job_config": {**_JOB_CONFIG, "kpi": {"pull_from_storage": True}},
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "did not become ready" in ctx["error"]

    def test_kpi_set_poll_times_out(self):
        # stays 'creating' forever → exercises the poll loop to the attempt cap
        # (test_kpi_set_not_ready uses 'failed', which returns before polling).
        datastore = MagicMock()
        datastore.register.return_value = {
            "snapshot_id": "snap_1",
            "status": "ready",
            "storage_prefix": "s3://x/snap_1/",
            "total_records": 12,
        }
        datastore.register_kpi_set.return_value = {"kpi_id": "kpi_1", "status": "creating"}
        datastore.get_kpi_set.return_value = {
            "kpi_id": "kpi_1",
            "status": "creating",
        }  # never ready
        provider = MagicMock()
        provider.datastore.return_value = datastore
        prev = {
            "model_name": "m",
            "use_case": "aoi",
            "job_config": {**_JOB_CONFIG, "kpi": {"pull_from_storage": True}},
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)
        assert "did not become ready" in ctx["error"]
        assert datastore.get_kpi_set.call_count >= 1  # the poll loop ran


class TestRunDeftTraining:
    def _kpi_uri_forwarded(self, *, ctx_uri=None, kpi_cfg=None):
        """Run run_deft_training and return the kpi_set_uri passed to start_run."""
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {
            "terminal": True,
            "succeeded": True,
            "status": "completed",
        }
        training.fetch_artifact.return_value = {
            "artifact_uri": "s3://ck",
            "checkpoint": "s3://ck",
            "sha256": "d" * 64,
            "metrics": {},
        }
        provider = MagicMock()
        provider.training.return_value = training
        jc = {**_JOB_CONFIG, "kpi": kpi_cfg} if kpi_cfg is not None else _JOB_CONFIG
        prev = {
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "snapshot_id": "snap_1",
            "job_config": jc,
        }
        if ctx_uri is not None:
            prev["kpi_set_uri"] = ctx_uri
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)
        return training.start_run.call_args.kwargs["kpi_set_uri"]

    def test_kpi_set_uri_precedence_forwarded_to_start_run(self):
        # (a) pulled-this-run (ctx) wins over the static kpi.uri
        assert (
            self._kpi_uri_forwarded(ctx_uri="s3://pulled/", kpi_cfg={"uri": "s3://static/"})
            == "s3://pulled/"
        )
        # (b) only static kpi.uri → used
        assert self._kpi_uri_forwarded(kpi_cfg={"uri": "s3://static/"}) == "s3://static/"
        # (c) neither → None (DEFT carves kpi from the snapshot)
        assert self._kpi_uri_forwarded() is None
        # (d) empty-string kpi.uri → treated as unset (not injected as empty path)
        assert self._kpi_uri_forwarded(kpi_cfg={"uri": ""}) is None

    def test_starts_polls_and_fetches_artifact(self):
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "succeeded": False, "status": "running"},
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {
            "artifact_uri": "s3://ck",
            "checkpoint": "s3://ck",
            "sha256": "d" * 64,
            "metrics": {"mAP50": 0.9},
            "training_spec": "s3://ck-spec.yaml",
            "threshold": 0.37,
        }
        provider = MagicMock()
        provider.training.return_value = training

        prev = {
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "snapshot_id": "snap_1",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)

        training.start_run.assert_called_once()
        assert ctx["deft_run_id"] == "run-9"
        # F3: training records ONLY the run id as lineage. candidate_version is
        # the MLflow integer, assigned later by register_candidate — the
        # training stage must NOT set it.
        assert "candidate_version" not in ctx
        assert ctx["artifact_uri"] == "s3://ck"
        # The checkpoint digest is threaded so the controller hands MM a
        # verifiable sha256 on pull (F1 regression guard).
        assert ctx["sha256"] == "d" * 64
        assert ctx["training_spec_uri"] == "s3://ck-spec.yaml"
        assert ctx["threshold"] == 0.37
        assert ctx["eval_metrics"] == {"mAP50": 0.9}

    def test_convergence_reason_is_surfaced_in_context(self):
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {
            "terminal": True,
            "succeeded": True,
            "status": "completed",
            "convergence_reason": "kpi_met",
        }
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)
        assert ctx["convergence_reason"] == "kpi_met"

    def test_failure_message_includes_deft_error_detail(self):
        # The run error must carry DEFT's own reason, not a bare state string.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {
            "terminal": True,
            "succeeded": False,
            "status": "failed",
            "error": "OOM on worker 0",
        }
        provider = MagicMock()
        provider.training.return_value = training

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)
        assert "OOM on worker 0" in ctx["error"]

    def test_resolves_baseline_checkpoint_from_mlflow_when_unset(self):
        # deft.config.model.checkpoint absent -> the handler resolves the
        # deployed baseline's artifact source from MLflow @production so the
        # FineTuneRequest satisfies the ModelConfig contract.
        # Scope: proves the resolved checkpoint reaches start_run(); that it
        # then lands in the request BODY is covered by TestDeftTrainingStartRun
        # (tests/unit/backends/test_deft_provider.py) against the real builder.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {"terminal": True, "succeeded": True}
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = "3"
        mlflow.get_version_source.return_value = "s3://aoi-artifacts/runs/run-base/weights.onnx"

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "changenet-aoi",
            "snapshot_uri": "s3://snap",
            "job_config": {**_JOB_CONFIG, "deft": {}},  # no explicit checkpoint
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        kwargs = training.start_run.call_args.kwargs
        assert kwargs["baseline_checkpoint"] == "s3://aoi-artifacts/runs/run-base/weights.onnx"
        mlflow.get_version_source.assert_called_once_with("changenet-aoi", "3")

    def test_promoted_baseline_supersedes_explicit_config_checkpoint(self):
        # Iterative fine-tuning: once a @production version exists, run N+1
        # trains FROM IT — the config's pinned checkpoint is only the day-0
        # seed and must not freeze the loop on the original model.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {"terminal": True, "succeeded": True}
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = "7"
        mlflow.get_version_source.return_value = "s3://aoi-artifacts/runs/gen-6/weights.onnx"

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "changenet-aoi",
            "snapshot_uri": "s3://snap",
            "job_config": {
                **_JOB_CONFIG,
                "deft": {"config": {"model": {"checkpoint": "s3://aoi-models/day0/model.onnx"}}},
            },
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        kwargs = training.start_run.call_args.kwargs
        assert kwargs["baseline_checkpoint"] == "s3://aoi-artifacts/runs/gen-6/weights.onnx"

    def test_config_seed_used_when_no_production_version(self):
        # First rollout: no @production alias AND no promoted record — the
        # resolver defers to the config's day-0 checkpoint (passes None; the
        # builder keeps the seed).
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {"terminal": True, "succeeded": True}
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "changenet-aoi",
            "snapshot_uri": "s3://snap",
            "job_config": {
                **_JOB_CONFIG,
                "deft": {"config": {"model": {"checkpoint": "s3://aoi-models/day0/model.onnx"}}},
            },
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        kwargs = training.start_run.call_args.kwargs
        assert kwargs["baseline_checkpoint"] is None
        mlflow.get_version_source.assert_not_called()

    def test_promotion_record_fallback_when_mlflow_unavailable(self):
        # MLflow disabled/unreachable: baseline continuity survives via the
        # promotion system of record — the latest promoted record's
        # artifact_uri (the same locator the Promotion API pulled for
        # deployment) becomes the training baseline.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {"terminal": True, "succeeded": True}
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        mlflow = MagicMock()
        mlflow.get_version_by_alias.side_effect = RuntimeError("mlflow down")
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = {
            "promotion_id": "promo-42",
            "model_name": "changenet-aoi",
            "state": "promoted",
            "artifact_uri": "s3://aoi-artifacts/runs/gen-5/weights.onnx",
        }

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "changenet-aoi",
            "snapshot_uri": "s3://snap",
            "job_config": {
                **_JOB_CONFIG,
                "deft": {"config": {"model": {"checkpoint": "s3://aoi-models/day0/model.onnx"}}},
            },
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        kwargs = training.start_run.call_args.kwargs
        assert kwargs["baseline_checkpoint"] == "s3://aoi-artifacts/runs/gen-5/weights.onnx"
        # Queried the promoted records for this model that actually carry a
        # locator (a newest record missing artifact_uri must NOT silently drop
        # us to the day-0 seed), newest first with a deterministic tie-breaker.
        find_kwargs = promo_db.__getitem__.return_value.find_one.call_args
        assert find_kwargs.args[0] == {
            "model_name": "changenet-aoi",
            "state": "promoted",
            "artifact_uri": {"$nin": [None, ""]},
        }
        assert find_kwargs.kwargs["sort"] == [("created_at", -1), ("_id", -1)]

    def test_no_progress_timeout_cancels_remote_and_fails(self, monkeypatch):
        # NO hard duration cap: the timeout is progress-based. A run reporting
        # no new progress for the window is failed AND remotely cancelled.
        from src.tasks.handlers.deft import handlers

        monkeypatch.setattr(handlers, "TRAINING_NO_PROGRESS_TIMEOUT_S", 0.0)
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {
            "terminal": False,
            "status": "running",
            "progress": {"current_iteration": 1},
        }
        provider = MagicMock()
        provider.training.return_value = training
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)
        # The message must attribute the decision to DFW and name the knob:
        # the old wording ("DEFT run ... reported no progress") read as a DEFT
        # hang and sent an operator to the wrong component for an hour.
        err = ctx["error"]
        assert "Data Flywheel cancelled" in err
        assert "DFW_TRAINING_NO_PROGRESS_TIMEOUT_S" in err
        assert "DEFT last reported" in err
        training.cancel.assert_called_once_with("run-9")

    def test_deft_progress_is_persisted_on_change_only(self):
        # Visibility: an operator should be able to see iteration/step from
        # GET /api/jobs/<id> instead of going to the DEFT service directly.
        # Written on CHANGE only -- the loop polls every 15s for hours, so a
        # write per poll would be thousands of no-op updates per run.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        p1 = {"current_iteration": 1, "current_step": "train"}
        p2 = {"current_iteration": 2, "current_step": "train"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running", "progress": p1},
            {"terminal": False, "status": "running", "progress": p1},  # unchanged
            {"terminal": False, "status": "running", "progress": p2},  # changed
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        calls = db.record_deft_progress.call_args_list
        # initial p1, then only the CHANGE to p2. The repeated p1 must not write,
        # and the terminal status (which carries no `progress`) must not write a
        # None over the last known value.
        assert len(calls) == 2, [c.args for c in calls]
        assert all(c.args[1] == "run-9" for c in calls)
        assert [c.args[2] for c in calls] == [p1, p2]

    def test_run_id_is_recorded_before_any_progress_is_reported(self):
        # The case this visibility work exists for: a run that hangs and is
        # killed by the watchdog never reports a dict progress, so the progress
        # writer never fires. If identity rode on that writer, `deft` would be
        # null and the run id would live only in a worker log -- exactly the
        # grep this was meant to remove.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running"},  # no progress key
            {"terminal": True, "succeeded": False, "status": "failed"},
        ]
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)

        # The run failed, and the id is still recoverable from the API rather
        # than only from a worker log.
        assert ctx["error"]
        db.record_deft_run_id.assert_called_once_with(_FW_ID, "run-9")
        # and nothing wrote a progress payload, so the guard still holds
        assert db.record_deft_progress.call_args_list == []

    def test_run_id_persistence_failure_does_not_fail_the_run(self):
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running", "progress": {"current_iteration": 1}},
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        db.record_deft_run_id.side_effect = RuntimeError("mongo down")
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)  # must not raise

    def test_progress_persistence_failure_does_not_fail_the_run(self):
        # Observability must never break control flow: a Mongo blip during a
        # multi-hour training must not kill a healthy run.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running", "progress": {"current_iteration": 1}},
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        db.record_deft_progress.side_effect = RuntimeError("mongo down")
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)
        assert not ctx.get("error"), ctx.get("error")

    def test_no_progress_timeout_is_env_configurable(self, monkeypatch):
        # The window has to be sized per deployment: DEFT reports progress per
        # STEP, so it must exceed the longest single step, which scales with
        # epochs x pool size x GPU count. Read at import, so reload the module.
        import importlib

        from src.tasks.handlers.deft import handlers

        monkeypatch.setenv("DFW_TRAINING_NO_PROGRESS_TIMEOUT_S", "7200")
        reloaded = importlib.reload(handlers)
        try:
            assert reloaded.TRAINING_NO_PROGRESS_TIMEOUT_S == 7200.0
        finally:
            monkeypatch.delenv("DFW_TRAINING_NO_PROGRESS_TIMEOUT_S", raising=False)
            importlib.reload(handlers)

    def test_no_progress_timeout_defaults_to_three_hours(self, monkeypatch):
        # Generous by design: too tight discards completed GPU work (a 50-epoch
        # AOI iteration was killed at the old 3600s default AFTER finishing every
        # epoch); too loose only delays noticing a genuinely hung run.
        # Clear the env first: otherwise a value set in CI or a developer shell
        # fails this test while the code is correct.
        import importlib

        from src.tasks.handlers.deft import handlers

        monkeypatch.delenv("DFW_TRAINING_NO_PROGRESS_TIMEOUT_S", raising=False)
        reloaded = importlib.reload(handlers)
        try:
            assert reloaded.TRAINING_NO_PROGRESS_TIMEOUT_S == 10800.0
        finally:
            importlib.reload(handlers)

    @pytest.mark.parametrize(
        "bad",
        [
            "not-a-number",
            "",
            "0",
            "-1",
            # nan/inf parse fine and would DISABLE the watchdog, not shorten it:
            # comparisons against nan are always False and `elapsed > inf` never
            # holds, so a hung run would poll forever. nan also slips past <= 0.
            "nan",
            "inf",
            "-inf",
        ],
    )
    def test_no_progress_timeout_rejects_invalid_values(self, monkeypatch, bad):
        # Fail at startup, not three hours into a run: an operator sets this
        # because their training outlasts the default, so silently falling back
        # would kill the very runs it exists to protect.
        import importlib

        from src.tasks.handlers.deft import handlers

        monkeypatch.setenv("DFW_TRAINING_NO_PROGRESS_TIMEOUT_S", bad)
        try:
            with pytest.raises(ValueError, match="DFW_TRAINING_NO_PROGRESS_TIMEOUT_S"):
                importlib.reload(handlers)
        finally:
            monkeypatch.delenv("DFW_TRAINING_NO_PROGRESS_TIMEOUT_S", raising=False)
            importlib.reload(handlers)

    def test_unknown_progress_fields_pass_through_untouched(self):
        """Forward compatibility: DFW must not need a change when DEFT adds fields.

        deft-service !16 adds `updated_at` and `detail` to its progress payload.
        Because DFW stores the dict VERBATIM and never parses it, those appear on
        GET /api/jobs/<id> the day that lands -- no schema change, no migration.
        This pins that so a future "let's model the progress shape properly"
        refactor cannot silently drop unrecognised keys.
        """
        future = {
            "current_iteration": 1,
            "current_step": "train",
            # fields DFW has never heard of
            "updated_at": "2026-08-18T18:24:11Z",
            "detail": {"epoch": 12, "max_epoch": 29, "eta": "0:19:47"},
        }
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running", "progress": future},
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        stored = db.record_deft_progress.call_args_list[0].args[2]
        assert stored == future, "progress must be stored verbatim, unknown keys included"

    def test_intra_step_liveness_resets_the_timeout(self, monkeypatch):
        """Once DEFT reports intra-step liveness, a long step stops being killed.

        This is the whole point of deft-service !16, verified from DFW's side:
        the two payloads differ ONLY in `updated_at` -- every step/iteration
        field is identical, exactly as it is during a multi-hour train. Without
        the pass-through, the second timeout check reads 180s against a 100s
        window and cancels a healthy run, which is the bug that discarded 50
        completed epochs.
        """
        from src.tasks.handlers.deft import handlers

        monkeypatch.setattr(handlers, "TRAINING_NO_PROGRESS_TIMEOUT_S", 100.0)
        clock = iter([0.0, 90.0, 95.0, 180.0, 185.0])
        same_step = {"current_iteration": 1, "current_step": "train", "completed_steps": 4}
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {
                "terminal": False,
                "status": "running",
                "progress": {**same_step, "updated_at": "2026-08-18T18:24:11Z"},
            },
            {
                "terminal": False,
                "status": "running",
                "progress": {**same_step, "updated_at": "2026-08-18T18:25:47Z"},
            },
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            # Swap the handler's OWN `time` binding, not attributes of the
            # global module: `handlers.time.monotonic` IS `time.monotonic`, so
            # pymongo's background monitor threads (left alive by earlier tests)
            # would draw ticks from this clock and exhaust it (bare StopIteration).
            patch("src.tasks.handlers.deft.handlers.time") as fake_time,
        ):
            fake_time.monotonic.side_effect = clock
            ctx = deft_tasks.run_deft_training(prev)
        assert not ctx.get("error"), ctx.get("error")
        training.cancel.assert_not_called()

    def test_progress_resets_the_timeout(self, monkeypatch):
        # A run whose TOTAL duration exceeds the no-progress window must NOT
        # be timed out as long as progress keeps arriving. The injected clock
        # makes this load-bearing: without the timer-reset branch, the second
        # timeout check reads 180s against a 100s window and kills the run.
        from src.tasks.handlers.deft import handlers

        monkeypatch.setattr(handlers, "TRAINING_NO_PROGRESS_TIMEOUT_S", 100.0)
        # monotonic() call sites in order: init snapshot; iter1 timeout check;
        # iter1 reset (progress 1->2); iter2 timeout check; final reset
        # (progress 2 -> None on the terminal status).
        clock = iter([0.0, 90.0, 95.0, 180.0, 185.0])
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running", "progress": {"current_iteration": 1}},
            {"terminal": False, "status": "running", "progress": {"current_iteration": 2}},
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        # Baseline resolution must be mocked here: a real MLflow/Mongo attempt
        # inside the resolver would make the run depend on live services.
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            # Swap the handler's OWN `time` binding, not attributes of the
            # global module: `handlers.time.monotonic` IS `time.monotonic`, so
            # pymongo's background monitor threads (left alive by earlier tests)
            # would draw ticks from this clock and exhaust it (bare StopIteration).
            patch("src.tasks.handlers.deft.handlers.time") as fake_time,
        ):
            fake_time.monotonic.side_effect = clock
            ctx = deft_tasks.run_deft_training(prev)
        assert ctx["deft_run_id"] == "run-9"  # survived 180s of wall clock

    def test_transient_status_errors_are_tolerated(self):
        # A brief status-poll blip during a healthy training must not kill it.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running", "progress": {"current_iteration": 1}},
            ConnectionError("blip"),
            ConnectionError("blip"),
            {"terminal": True, "succeeded": True, "status": "completed"},
        ]
        training.fetch_artifact.return_value = {"metrics": {}}
        provider = MagicMock()
        provider.training.return_value = training
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)
        assert ctx["deft_run_id"] == "run-9"

    def test_persistent_status_failure_fails_and_cancels(self, monkeypatch):
        from src.tasks.handlers.deft import handlers

        monkeypatch.setattr(handlers, "TRAINING_STATUS_ERROR_TOLERANCE", 2)
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running", "progress": None},
            ConnectionError("down"),
            ConnectionError("down"),
        ]
        provider = MagicMock()
        provider.training.return_value = training
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)
        assert "consecutive" in ctx["error"]
        training.cancel.assert_called_once_with("run-9")

    def test_non_successful_terminal_stamps_error(self):
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9"}
        training.get_status.return_value = {
            "terminal": True,
            "succeeded": False,
            "status": "failed",
        }
        provider = MagicMock()
        provider.training.return_value = training

        prev = {"model_name": "m", "snapshot_uri": "s3://snap", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)
        assert "non-successfully" in ctx["error"]


class TestOfflineEvalGate:
    def _prev(self):
        return {
            "flywheel_run_id": _FW_ID,
            "model_name": "changenet-aoi",
            "use_case": "aoi",
            "candidate_version": "run-9",
            "eval_metrics": {"mAP50": 0.9},
            "artifact_uri": "s3://ck",
            "job_config": _JOB_CONFIG,
        }

    def test_pass_threads_offline_metrics(self):
        provider = MagicMock()
        provider.evaluator.return_value = MagicMock()
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.eval_harness.OfflineEval") as mock_offline,
        ):
            mock_offline.return_value.run.return_value = {"metrics": {"mAP50": 0.9}, "passed": True}
            ctx = deft_tasks.run_offline_eval_gate(self._prev())

        assert ctx["gate_passed"] is True
        assert ctx["offline_metrics"] == {"mAP50": 0.9}

    def test_failed_gate_marks_run_and_stamps_error(self):
        provider = MagicMock()
        provider.evaluator.return_value = MagicMock()
        db = MagicMock()
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.lib.eval_harness.OfflineEval") as mock_offline,
        ):
            mock_offline.return_value.run.return_value = {"metrics": {}, "passed": False}
            ctx = deft_tasks.run_offline_eval_gate(self._prev())

        assert "gate failed" in ctx["error"]  # later stages (register/promote) skip
        db.mark_flywheel_run_error.assert_called_once()

    def test_gate_verifies_constraints_only_no_baseline_comparison(self):
        # The gate is a CONFIRMATION of DEFT's candidate-only metrics against
        # DFW's structured constraints — no MLflow/baseline involvement (the
        # DEFT contract returns no baseline metrics; DEFT's own workflow runs
        # baseline_eval inside its loop).
        from src.backends.deft.eval import DeftAOIEvaluator

        provider = MagicMock()
        provider.evaluator.return_value = DeftAOIEvaluator()
        prev = self._prev()
        prev["eval_metrics"] = {"far": 0.0005, "recall": 1.0}
        prev["job_config"] = {
            **_JOB_CONFIG,
            "eval": {"kpi_target": [{"metric": "far", "op": "<", "threshold": 0.001}]},
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient") as mlflow,
        ):
            ctx = deft_tasks.run_offline_eval_gate(prev)

        assert ctx["gate_passed"] is True
        mlflow.assert_not_called()  # gate never touches the registry


class TestRegisterCandidate:
    def test_allocates_serving_version_from_mongo_counter(self):
        # F3: the register stage mints candidate_version from DFW's Mongo
        # serving-version counter (NOT MLflow), and passes the DEFT run id +
        # that integer to MLflow best-effort. candidate_version is NOT in ctx
        # before this stage.
        prev = {
            "model_name": "m",
            "deft_run_id": "run-231b-003",
            "artifact_uri": "s3://a",
            "snapshot_id": "snap_1",
            "use_case": "aoi",
            "eval_metrics": {"mAP50": 0.9},
        }
        db = _counter_db(seq=2)
        with (
            _mlflow_enabled(),
            patch("src.tasks.promotion.controller._get_db", return_value=db),
            patch("src.lib.integration.mlflow_client.MLflowClient") as mock_client,
        ):
            instance = mock_client.return_value
            instance.register_candidate.return_value = True
            ctx = deft_tasks.register_candidate(prev)

        # The counter was consulted for this model.
        db.serving_version_counters.find_one_and_update.assert_called_once()
        instance.register_candidate.assert_called_once()
        kwargs = instance.register_candidate.call_args.kwargs
        assert kwargs["model_name"] == "m"
        # The run id is passed as lineage, not as the version.
        assert kwargs["deft_run_id"] == "run-231b-003"
        # The Mongo integer is passed to MLflow as the serving version (label).
        assert kwargs["serving_version"] == "2"
        assert kwargs["artifact_uri"] == "s3://a"
        assert kwargs["tags"]["snapshot_id"] == "snap_1"
        # No spec published -> no spec_uri tag (older DEFT services).
        assert "spec_uri" not in kwargs["tags"]
        # No digest/threshold in ctx -> those provenance tags are absent too.
        assert "checkpoint_sha256" not in kwargs["tags"]
        assert "threshold" not in kwargs["tags"]
        # The Mongo integer becomes the candidate/serving version.
        assert ctx["candidate_version"] == "2"
        assert ctx["registered"] is True
        # Lineage is preserved.
        assert ctx["deft_run_id"] == "run-231b-003"

    def test_mlflow_disabled_still_proceeds_with_serving_version(self):
        # Best-effort MLflow (reverting the !561 fail-closed behavior): a
        # disabled/failed registry does NOT block the promotion — the serving
        # version is minted from Mongo regardless (MR !550 continuity).
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "deft_run_id": "run-9",
            "artifact_uri": "s3://a",
            "snapshot_id": "snap_1",
            "use_case": "aoi",
            "eval_metrics": {},
        }
        db = _counter_db(seq=1)
        with (
            _mlflow_enabled(False),
            patch("src.tasks.promotion.controller._get_db", return_value=db),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.lib.integration.mlflow_client.MLflowClient") as mock_client,
        ):
            # Disabled registry reports it recorded nothing.
            mock_client.return_value.register_candidate.return_value = False
            ctx = deft_tasks.register_candidate(prev)

        assert "error" not in ctx  # NOT fail-closed
        assert ctx["candidate_version"] == "1"  # minted from Mongo (mocked counter)
        assert ctx["registered"] is False  # MLflow recorded nothing

    def test_registration_failure_still_proceeds(self):
        # MLflow raising is swallowed (best-effort): the run proceeds on the
        # Mongo-assigned serving version.
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "deft_run_id": "run-9",
            "artifact_uri": "s3://a",
            "snapshot_id": "snap_1",
            "use_case": "aoi",
            "eval_metrics": {},
        }
        db = _counter_db(seq=3)
        with (
            _mlflow_enabled(),
            patch("src.tasks.promotion.controller._get_db", return_value=db),
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.lib.integration.mlflow_client.MLflowClient") as mock_client,
        ):
            mock_client.return_value.register_candidate.side_effect = RuntimeError("registry down")
            ctx = deft_tasks.register_candidate(prev)

        assert "error" not in ctx
        assert ctx["candidate_version"] == "3"
        assert ctx["registered"] is False

    def test_training_spec_registered_as_spec_uri_tag(self):
        prev = {
            "model_name": "m",
            "deft_run_id": "run-9",
            "artifact_uri": "s3://a",
            "training_spec_uri": "s3://aoi-artifacts/runs/v1/baseline_spec.yaml",
            "snapshot_id": "snap_1",
            "use_case": "aoi",
            "eval_metrics": {},
        }
        db = _counter_db(seq=1)
        with (
            _mlflow_enabled(),
            patch("src.tasks.promotion.controller._get_db", return_value=db),
            patch("src.lib.integration.mlflow_client.MLflowClient") as mock_client,
        ):
            instance = mock_client.return_value
            instance.register_candidate.return_value = True
            deft_tasks.register_candidate(prev)

        tags = instance.register_candidate.call_args.kwargs["tags"]
        assert tags["spec_uri"] == "s3://aoi-artifacts/runs/v1/baseline_spec.yaml"

    def test_checkpoint_sha256_and_threshold_registered_as_tags(self):
        # Provenance: the checkpoint digest + decision threshold ride the MLflow
        # tags (same registry-trail role as spec_uri) so a registered version is
        # self-verifiable from the registry alone, without joining back to the
        # Mongo promotion record.
        prev = {
            "model_name": "m",
            "deft_run_id": "run-9",
            "artifact_uri": "s3://a",
            "sha256": "e" * 64,
            "threshold": 0.42,
            "snapshot_id": "snap_1",
            "use_case": "aoi",
            "eval_metrics": {},
        }
        db = _counter_db(seq=1)
        with (
            _mlflow_enabled(),
            patch("src.tasks.promotion.controller._get_db", return_value=db),
            patch("src.lib.integration.mlflow_client.MLflowClient") as mock_client,
        ):
            instance = mock_client.return_value
            instance.register_candidate.return_value = True
            deft_tasks.register_candidate(prev)

        tags = instance.register_candidate.call_args.kwargs["tags"]
        assert tags["checkpoint_sha256"] == "e" * 64
        assert tags["threshold"] == "0.42"


class TestStageFailureDiscipline:
    """Deployment-test findings: every stage failure must mark the run FAILED,
    and cancellation must stop the chain between stages."""

    def test_stage_failure_marks_run_failed(self):
        training = MagicMock()
        training.start_run.side_effect = ConnectionError("DEFT down")
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        prev = {"flywheel_run_id": _FW_ID, "model_name": "m", "job_config": _JOB_CONFIG}
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
        ):
            ctx = deft_tasks.run_deft_training(prev)

        assert "DEFT down" in ctx["error"]
        db.mark_flywheel_run_error.assert_called_once()
        msg = db.mark_flywheel_run_error.call_args.args[1]
        assert "run_deft_training" in msg and "DEFT down" in msg

    def test_later_stages_skip_after_a_stage_error(self):
        # NMP-parity: the chain always propagates — a stage that receives a
        # ctx carrying `error` is a graceful pass-through (no handler work).
        provider = MagicMock()
        # Spelled in the format _run_stage actually produces ("DEFT stage ..."),
        # so this literal stays a readable sample of a real upstream error
        # rather than a fossil of the pre-rename wording.
        errored = {"flywheel_run_id": _FW_ID, "error": "DEFT stage 'x' failed: boom"}
        with patch("src.backends.factory.get_backend_provider", return_value=provider):
            ctx = deft_tasks.register_candidate(dict(errored))
            ctx2 = deft_tasks.finalize_aoi_run(dict(errored))

        assert ctx["error"] == errored["error"]
        assert "registered" not in ctx  # handler never ran
        assert "finalized" not in ctx2
        provider.training.assert_not_called()

    def test_cancellation_stops_the_chain(self):
        from src.lib.flywheel.cancellation import FlywheelCancelledError

        db = MagicMock()
        prev = {"flywheel_run_id": _FW_ID, "model_name": "m", "job_config": _JOB_CONFIG}
        with (
            patch(
                "src.lib.flywheel.cancellation.check_cancellation",
                side_effect=FlywheelCancelledError(_FW_ID),
            ),
            patch("src.api.db_manager.get_db_manager", return_value=db),
        ):
            ctx = deft_tasks.run_deft_training(prev)

        # The cancel is recorded on the ctx (so later stages skip) but the run
        # doc keeps its user-cancelled status — no failure overwrite.
        assert "cancelled" in ctx["error"]
        db.mark_flywheel_run_error.assert_not_called()

    def test_cancel_mid_training_poll_stops_and_cancels_remote_run(self):
        # NMP-style wait-loop discipline: a cancel landing DURING the (up to
        # 1h) training poll must abort mid-loop AND cancel the remote DEFT run.
        from src.lib.flywheel.cancellation import FlywheelCancelledError

        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.return_value = {"terminal": False, "status": "running"}
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()

        # Not cancelled at stage entry; cancelled by the first in-loop check.
        checks = iter([None, FlywheelCancelledError(_FW_ID)])

        def _check(_run_id):
            result = next(checks)
            if result:
                raise result

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.flywheel.cancellation.check_cancellation", side_effect=_check),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.run_deft_training(prev)

        assert "cancelled" in ctx["error"]
        training.cancel.assert_called_once_with("run-9")
        db.mark_flywheel_run_error.assert_not_called()

    def test_cancel_mid_snapshot_poll_stops(self):
        from src.lib.flywheel.cancellation import FlywheelCancelledError

        datastore = MagicMock()
        datastore.register.return_value = {"snapshot_id": "snap_1", "status": "creating"}
        provider = MagicMock()
        provider.datastore.return_value = datastore
        db = MagicMock()

        checks = iter([None, FlywheelCancelledError(_FW_ID)])

        def _check(_run_id):
            result = next(checks)
            if result:
                raise result

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "use_case": "aoi",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.flywheel.cancellation.check_cancellation", side_effect=_check),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "cancelled" in ctx["error"]
        db.mark_flywheel_run_error.assert_not_called()

    def test_unavailable_cancellation_check_does_not_kill_the_run(self):
        # Fail-open: if the cancellation flag cannot be read (no DB), the run
        # proceeds rather than dying on the check itself.
        from src.tasks.handlers.deft.handlers import honor_cancellation

        with patch(
            "src.lib.flywheel.cancellation.check_cancellation",
            side_effect=ConnectionError("mongo down"),
        ):
            honor_cancellation(_FW_ID)  # must not raise


class TestStartPromotionRunStatus:
    def test_moves_run_to_promoting_before_enqueue(self):
        # The run enters the intermediate PROMOTING status at handoff; it is
        # completed/failed later by the Promotion Controller, not the chain.
        from src.api.schemas import FlywheelRunStatus

        db = MagicMock()
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "v1",
            "baseline_version": "v0",
            "use_case": "aoi",
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion") as adv,
        ):
            ctx = deft_tasks.start_promotion(prev)

        db.update_flywheel_run_status.assert_called_once_with(_FW_ID, FlywheelRunStatus.PROMOTING)
        record = service.return_value.create.call_args.args[0]
        assert record.flywheel_run_id == _FW_ID  # controller finalizes via this
        adv.delay.assert_called_once_with(record.promotion_id)
        assert ctx["promotion_state"] == "promoting"

    def test_training_spec_lands_on_the_promotion_record(self):
        # The published spec locator rides the promotion record so the serving
        # side can stage spec.yaml next to the weights — resolved to a presigned
        # HTTPS URL before the pull.
        db = MagicMock()
        artifact = "s3://aoi-artifacts/runs/v1/weights.onnx"
        spec = "s3://aoi-artifacts/runs/v1/baseline_spec.yaml"
        signed = _fake_presign_client(
            {
                artifact: "https://store/runs/v1/weights.onnx?X-Amz-Signature=a",
                spec: "https://store/runs/v1/baseline_spec.yaml?X-Amz-Signature=b",
            }
        )
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "v1",
            "baseline_version": "v0",
            "use_case": "aoi",
            "artifact_uri": artifact,
            "sha256": "d" * 64,
            "training_spec_uri": spec,
            "threshold": 0.55,
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
            patch(
                "src.backends.deft.storage_backend_client.StorageBackendClient",
                return_value=signed,
            ),
        ):
            deft_tasks.start_promotion(prev)

        record = service.return_value.create.call_args.args[0]
        assert record.artifact_uri == "https://store/runs/v1/weights.onnx?X-Amz-Signature=a"
        assert record.spec_uri == "https://store/runs/v1/baseline_spec.yaml?X-Amz-Signature=b"
        assert record.threshold == 0.55

    def test_stores_durable_raw_locators_on_the_record(self):
        # The controller re-presigns from these on every pull attempt, so the
        # DURABLE raw object-store locators (NOT the ephemeral presigned URL)
        # must land on the promotion record alongside the initial presigned URL.
        db = MagicMock()
        artifact = "s3://aoi-artifacts/runs/v1/weights.onnx"
        spec = "s3://aoi-artifacts/runs/v1/baseline_spec.yaml"
        signed = _fake_presign_client(
            {
                artifact: "https://store/runs/v1/weights.onnx?X-Amz-Signature=a",
                spec: "https://store/runs/v1/baseline_spec.yaml?X-Amz-Signature=b",
            }
        )
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "v1",
            "use_case": "aoi",
            "artifact_uri": artifact,
            "training_spec_uri": spec,
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
            patch(
                "src.backends.deft.storage_backend_client.StorageBackendClient",
                return_value=signed,
            ),
        ):
            deft_tasks.start_promotion(prev)

        record = service.return_value.create.call_args.args[0]
        # Raw locators are the durable s3:// keys (re-presign source), while the
        # served artifact_uri/spec_uri hold the initial presigned URL.
        assert record.artifact_locator == artifact
        assert record.spec_locator == spec
        assert record.artifact_uri.startswith("https://store/")
        assert record.spec_uri.startswith("https://store/")

    def test_presigns_s3_artifact_and_spec_before_pull(self):
        # An object-store (s3://) artifact + spec are resolved to short-lived
        # presigned HTTPS URLs (via POST /v1/get_presigned_urls) right before the
        # record is built; the durable s3:// stays in MLflow (unchanged here).
        db = MagicMock()
        artifact = "s3://aoi-artifacts/runs/v1/weights.onnx"
        signed = _fake_presign_client(
            {artifact: "https://store/runs/v1/weights.onnx?X-Amz-Signature=a"}
        )
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "v1",
            "use_case": "aoi",
            "artifact_uri": artifact,
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
            patch(
                "src.backends.deft.storage_backend_client.StorageBackendClient",
                return_value=signed,
            ),
        ):
            deft_tasks.start_promotion(prev)

        record = service.return_value.create.call_args.args[0]
        assert record.artifact_uri.startswith("https://store/")
        # Object-store locator was sent for signing (expires_in from config).
        assert signed.get_presigned_urls.call_args.args[0] == [artifact]

    def test_passes_through_http_and_file_locators_unchanged(self):
        # A direct http(s)/file locator (dev/test) is NOT presigned — it must
        # ride the record unchanged, with no Storage Backend round-trip.
        db = MagicMock()
        signed = _fake_presign_client({})
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "v1",
            "use_case": "aoi",
            "artifact_uri": "https://weights.example.com/model.onnx",
            "training_spec_uri": "file:///specs/baseline_spec.yaml",
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
            patch(
                "src.backends.deft.storage_backend_client.StorageBackendClient",
                return_value=signed,
            ),
        ):
            deft_tasks.start_promotion(prev)

        record = service.return_value.create.call_args.args[0]
        assert record.artifact_uri == "https://weights.example.com/model.onnx"
        assert record.spec_uri == "file:///specs/baseline_spec.yaml"
        signed.get_presigned_urls.assert_not_called()  # nothing to sign

    def test_unsigned_artifact_fails_closed_before_promotion(self):
        # Fail-closed: if the Storage Backend cannot sign the object-store
        # artifact (listed in failed / missing from items) the stage aborts —
        # the Model Manager cannot pull without a signed URL.
        db = MagicMock()
        service = MagicMock()
        artifact = "s3://aoi-artifacts/runs/v1/weights.onnx"
        signed = _fake_presign_client({}, failed=[{"input": artifact, "error": "object not found"}])
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "v1",
            "use_case": "aoi",
            "artifact_uri": artifact,
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", return_value=service),
            patch("src.tasks.promotion.tasks.advance_promotion") as adv,
            patch(
                "src.backends.deft.storage_backend_client.StorageBackendClient",
                return_value=signed,
            ),
        ):
            ctx = deft_tasks.start_promotion(prev)

        assert "presign" in ctx["error"]  # stage failed on the unsigned artifact
        service.create.assert_not_called()  # no promotion record created
        adv.delay.assert_not_called()  # nothing enqueued

    def test_baseline_from_last_promoted_mongo_record(self):
        # F3: the PRIMARY baseline source is the last promoted promotion record
        # in Mongo — its candidate_version is already the serving integer. MLflow
        # is NOT consulted when Mongo has a promoted record (MR !550 continuity).
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "2",
            "deft_run_id": "run-2",
            "use_case": "aoi",
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = {
            "promotion_id": "promo-1",
            "model_name": "m",
            "state": "promoted",
            "candidate_version": "1",  # the serving integer of the live baseline
        }
        mlflow = MagicMock()
        with (
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
        ):
            deft_tasks.start_promotion(prev)

        record = service.return_value.create.call_args.args[0]
        # The last promoted record's serving integer is the baseline.
        assert record.baseline_version == "1"
        # MLflow was never consulted (Mongo is the system of record).
        mlflow.get_version_by_alias.assert_not_called()
        # The promotion-record query targets promoted records carrying a version.
        find_call = promo_db.__getitem__.return_value.find_one.call_args
        assert find_call.args[0] == {
            "model_name": "m",
            "state": "promoted",
            "candidate_version": {"$nin": [None, ""]},
        }
        assert find_call.kwargs["sort"] == [("created_at", -1), ("_id", -1)]
        # deft_run_id lineage lands on the record.
        assert record.deft_run_id == "run-2"

    def test_baseline_falls_back_to_mlflow_when_no_promoted_record(self):
        # SECONDARY / observability: with no promoted record in Mongo (first
        # rollout), fall back to the MLflow @production alias's SERVING integer.
        # The serving integer comes from get_serving_version_by_alias (the
        # promotion.version_label tag), NOT the raw MLflow alias number — see
        # test_baseline_mlflow_resolves_serving_integer_not_alias_number.
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "3",
            "deft_run_id": "run-3",
            "use_case": "aoi",
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        mlflow = MagicMock()
        mlflow.get_serving_version_by_alias.return_value = "1"
        with (
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
        ):
            deft_tasks.start_promotion(prev)

        record = service.return_value.create.call_args.args[0]
        assert record.baseline_version == "1"
        mlflow.get_serving_version_by_alias.assert_called_once_with("m", "production")

    def test_baseline_mlflow_resolves_serving_integer_not_alias_number(self):
        # Regression (Faaran review on !561): MLflow aliases bind to MLflow's own
        # auto-assigned version NUMBER, but the serving/Triton integer lives on
        # the promotion.version_label tag — and they diverge (e.g. once the Mongo
        # counter is seeded). The baseline must be the SERVING integer ("2"), not
        # the alias number ("5"), or the controller rolls back to the wrong dir.
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "6",
            "deft_run_id": "run-6",
            "use_case": "aoi",
            "sha256": "d" * 64,
            "job_config": _JOB_CONFIG,
        }
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        mlflow = MagicMock()
        # Alias number is "5"; the serving integer (version_label tag) is "2".
        mlflow.get_version_by_alias.return_value = "5"
        mlflow.get_serving_version_by_alias.return_value = "2"
        with (
            patch("src.api.db_manager.get_db_manager", return_value=MagicMock()),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
        ):
            deft_tasks.start_promotion(prev)

        record = service.return_value.create.call_args.args[0]
        assert record.baseline_version == "2"  # serving integer, not the "5" alias number

    def test_missing_sha256_fails_fast_before_creating_promotion(self):
        # F1 guard: MM requires PullRequest.sha256 and verifies it after
        # download, so a candidate with no digest could only 409 late at the
        # pull gate. StartPromotion must abort up front, never create the
        # promotion record, and never enqueue an advance.
        db = MagicMock()
        service = MagicMock()
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "candidate_version": "v1",
            "baseline_version": "v0",
            "use_case": "aoi",
            # deliberately NO sha256 / checkpoint_sha256
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", return_value=service),
            patch("src.tasks.promotion.tasks.advance_promotion") as adv,
        ):
            ctx = deft_tasks.start_promotion(prev)

        assert "sha256" in ctx["error"]  # stage failed on the missing digest
        service.create.assert_not_called()  # no promotion record created
        adv.delay.assert_not_called()  # nothing enqueued
        db.mark_flywheel_run_error.assert_called_once()


class TestFinalize:
    def test_chain_finalize_does_not_complete_the_run(self):
        # The chain's finalize is bookkeeping only — the run stays 'promoting'
        # until the Promotion Controller reaches a terminal state.
        db = MagicMock()
        prev = {"flywheel_run_id": _FW_ID, "candidate_version": "v1", "promotion_id": "promo-1"}
        with patch("src.api.db_manager.get_db_manager", return_value=db):
            ctx = deft_tasks.finalize_aoi_run(prev)

        db.mark_flywheel_run_completed.assert_not_called()
        assert ctx["finalized"] is True


class TestStageVisibility:
    """`stage` is the only thing that distinguishes four identical-looking waits.

    snapshot, training, the offline gate and registration all render as
    `running`, so without this an operator cannot tell a run wedged in
    snapshotting from one wedged in promotion.
    """

    def test_each_stage_is_recorded_on_entry(self):
        db = MagicMock()
        provider = MagicMock()
        prev = {"flywheel_run_id": _FW_ID, "model_name": "m", "job_config": _JOB_CONFIG}

        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
        ):
            handler = MagicMock()
            handler.execute.return_value = dict(prev)
            deft_tasks._run_stage("run_deft_training", handler, dict(prev))

        (stage,) = (c.args[1] for c in db.record_deft_stage.call_args_list)
        assert stage == "run_deft_training"

    def test_stage_is_recorded_even_when_the_stage_then_fails(self):
        # Recorded on ENTRY, not completion: a stage that never finishes is
        # exactly the case where knowing where the run stopped matters.
        db = MagicMock()
        provider = MagicMock()
        prev = {"flywheel_run_id": _FW_ID, "model_name": "m", "job_config": _JOB_CONFIG}

        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
        ):
            handler = MagicMock()
            handler.execute.side_effect = RuntimeError("boom")
            ctx = deft_tasks._run_stage("run_offline_eval_gate", handler, dict(prev))

        assert ctx["error"]
        db.record_deft_stage.assert_called_once()
        assert db.record_deft_stage.call_args.args[1] == "run_offline_eval_gate"

    def test_a_skipped_stage_is_not_recorded(self):
        # A pass-through after an upstream failure never ran, so claiming it as
        # the current stage would point an operator at the wrong place.
        db = MagicMock()
        errored = {"flywheel_run_id": _FW_ID, "error": "DEFT stage 'x' failed: boom"}

        with patch("src.api.db_manager.get_db_manager", return_value=db):
            deft_tasks._run_stage("register_candidate", MagicMock(), errored)

        db.record_deft_stage.assert_not_called()

    def test_stage_persistence_failure_does_not_break_the_stage(self):
        db = MagicMock()
        db.record_deft_stage.side_effect = RuntimeError("mongo down")
        provider = MagicMock()
        prev = {"flywheel_run_id": _FW_ID, "model_name": "m", "job_config": _JOB_CONFIG}

        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
        ):
            handler = MagicMock()
            handler.execute.return_value = {**prev, "ok": True}
            ctx = deft_tasks._run_stage("select_and_snapshot", handler, dict(prev))

        assert ctx["ok"] is True
        assert not ctx.get("error")


class TestGateVerdictVisibility:
    """A blocked promotion must say WHICH constraint failed.

    `eval.py` computes `unmet` and the handler discarded it, so a gate failure
    reported neither in the API nor the log which KPI missed or by how much --
    leaving "the model got worse" and "DEFT renamed a metric" indistinguishable.
    """

    def _ctx(self):
        return {
            "flywheel_run_id": _FW_ID,
            "use_case": "aoi",
            "model_name": "m",
            "job_config": _JOB_CONFIG,
            "eval_metrics": {"FAR_pct": 240.0},
            "deft_run_id": "run-9",
        }

    def test_unmet_is_recorded_and_named_when_the_gate_blocks(self):
        db = MagicMock()
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.lib.eval_harness.OfflineEval") as offline,
            patch("src.backends.factory.get_backend_provider", return_value=MagicMock()),
        ):
            offline.return_value.run.return_value = {
                "passed": False,
                "metrics": {"FAR_pct": 240.0},
                "unmet": ["FAR_pct < 200.0 (actual=240.0)"],
            }
            handler = _deft_handlers.RunOfflineEvalGateHandler(db)
            with pytest.raises(_deft_handlers.AOIStageError) as exc:
                handler.execute(self._ctx())

        # named in the error an operator actually reads
        assert "FAR_pct < 200.0 (actual=240.0)" in str(exc.value)
        # and recorded BEFORE the raise, since the stage is about to abort
        recorded = [c.args[1] for c in db.record_deft_fields.call_args_list]
        gate = next(r["gate"] for r in recorded if "gate" in r)
        assert gate["passed"] is False
        assert gate["unmet"] == ["FAR_pct < 200.0 (actual=240.0)"]


class TestLineageRecordsEffectiveValues:
    """Lineage must record what the run USED, not what ctx happened to carry.

    Both fields have a config fallback applied downstream of the ctx value, so
    recording the ctx value alone reports null in precisely the cases the fields
    exist to explain: a static-KPI run, and a first rollout off the config seed.
    """

    def test_kpi_set_uri_falls_back_to_the_static_config_uri(self):
        """Driven through execute(), not a hand-built payload.

        A hand-built call only proves the expression I wrote is the expression I
        wrote; it cannot catch the handler passing something else.
        """
        db = MagicMock()
        job_config = {**_JOB_CONFIG, "kpi": {"uri": "s3://static/kpi/"}}
        datastore = MagicMock()
        datastore.get_uri.return_value = "s3://snap/"
        datastore.create_snapshot.return_value = {"id": "snap-1", "status": "ready"}
        datastore.get_snapshot.return_value = {
            "id": "snap-1",
            "status": "ready",
            "total_records": 12,
        }
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {
            "flywheel_run_id": _FW_ID,
            "use_case": "aoi",
            "model_name": "m",
            "job_config": job_config,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.select_and_snapshot(prev)

        data_writes = [
            c.args[1]["data"] for c in db.record_deft_fields.call_args_list if "data" in c.args[1]
        ]
        assert data_writes, "snapshot stage never recorded lineage"
        assert (
            data_writes[-1]["kpi_set_uri"] == "s3://static/kpi/"
        ), "static config KPI uri was not recorded as the effective value"

    def test_baseline_falls_back_to_the_config_seed_on_first_rollout(self):
        # _resolve_baseline_checkpoint returns None when nothing is promoted
        # yet; the request builder then applies the seed. The recorded value
        # must be the seed, not null.
        seed = "s3://aoi-samples/models/baseline/1.0.0/model.pth"
        job_config = {
            **_JOB_CONFIG,
            "deft": {"config": {"model": {"checkpoint": seed}}},
        }
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": True, "succeeded": False, "status": "failed"},
        ]
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        mlflow = MagicMock()
        mlflow.get_version_by_alias.return_value = None
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": job_config,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        baselines = [
            c.args[1]["baseline"]
            for c in db.record_deft_fields.call_args_list
            if "baseline" in c.args[1]
        ]
        assert baselines, "baseline was never recorded"
        assert baselines[0]["checkpoint"] == seed

    def test_poll_health_advances_without_any_progress_change(self):
        # The whole point of last_poll_at: a DEFT run reporting nothing still
        # shows a live worker. Riding the progress-change branch would make it
        # identical to progress_at and useless.
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running"},  # no progress key at all
            {"terminal": False, "status": "running"},
            {"terminal": True, "succeeded": False, "status": "failed"},
        ]
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        polls = [
            c.args[1] for c in db.record_deft_fields.call_args_list if "last_poll_at" in c.args[1]
        ]
        assert len(polls) >= 2, "poll health did not advance on unchanged progress"
        assert all(p["poll_errors"] == 0 for p in polls)

    def test_poll_errors_is_published_when_polls_actually_fail(self):
        """The field is only useful if it can be non-zero.

        Written beside the success-path reset it would be pinned at 0 forever,
        leaving a degrading DEFT service exactly as invisible as before.
        """
        training = MagicMock()
        training.start_run.return_value = {"id": "run-9", "state": "pending"}
        training.get_status.side_effect = [
            {"terminal": False, "status": "running"},
            RuntimeError("deft unreachable"),
            RuntimeError("deft unreachable"),
            {"terminal": True, "succeeded": False, "status": "failed"},
        ]
        provider = MagicMock()
        provider.training.return_value = training
        db = MagicMock()
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "m",
            "snapshot_uri": "s3://snap",
            "job_config": _JOB_CONFIG,
        }
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.handlers.deft.handlers.time.sleep"),
        ):
            deft_tasks.run_deft_training(prev)

        counts = [
            c.args[1]["poll_errors"]
            for c in db.record_deft_fields.call_args_list
            if "poll_errors" in c.args[1]
        ]
        assert max(counts) >= 2, f"poll_errors never rose above 0: {counts}"
        # A failed poll must not advance last_poll_at -- it is not evidence the
        # run is being watched successfully.
        failures = [
            c.args[1]
            for c in db.record_deft_fields.call_args_list
            if c.args[1].get("poll_errors", 0) > 0
        ]
        assert all("last_poll_at" not in f for f in failures)
