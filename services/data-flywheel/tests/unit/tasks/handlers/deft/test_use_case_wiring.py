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
"""The DEFT pipeline handlers are USE-CASE aware end to end.

Every seam these tests pin (``DEFTConfig.for_use_case`` / ``endpoints_for``, the
provider's ``datastore(use_case=...)``) already existed and was already correct —
and was reached by NOTHING, because the handlers never passed a use case. An ITS
run would therefore have snapshotted from AOI's object store and presigned
against AOI's Storage Backend, without a single error.

So these tests are about REACHABILITY, and each one is paired with an AOI
regression pin: the correct behaviour for a deployment that has not declared
per-use-case endpoints is to be byte-identical to what it did before.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.config.deft import DEFTConfig, DeftSystemConfig
from src.tasks import deft_tasks
from src.tasks.handlers.deft.handlers import SNAPSHOT_POLL_MAX_ATTEMPTS

_FW_ID = "507f1f77bcf86cd799439011"

#: Endpoints for a deployment that runs BOTH use cases in one process. Note the
#: ``aoi`` entry states nothing: under the fail-closed guard, declaring
#: per-use-case endpoints at all means every use case must be named, and an
#: empty entry is how a use case says "inherits, deliberately".
_TWO_STORE_ENDPOINTS = {
    "aoi": {},
    "its": {
        "storage_backend_url": "http://sc-backend:8080",
        "object_store_uri": "s3://sc-artifacts",
    },
}

_AOI_JOB_CONFIG = {
    "use_case": "aoi",
    "model_name": "changenet-aoi",
    "selection": {"mode": "query", "filters": {"created_after": "1970-01-01T00:00:00Z"}},
}

_ITS_JOB_CONFIG = {
    "use_case": "its",
    "model_name": "its-stream-detector",
    "model_format": "tensorrt",
    "selection": {
        "mode": "query",
        # Streaming facets ride the same free-form filters dict as AOI's, and
        # only names the ITS index template defines select anything: `sensorId`
        # is the camera key it carries. An invented one ("cameras") is accepted
        # and matches nothing.
        "filters": {"created_after": "2026-07-24T00:00:00Z", "sensorId": ["cam-1", "cam-2"]},
    },
}


@contextmanager
def _deft_settings(**deft_kwargs):
    """Install a DEFT system config for the duration of a test.

    The unit environment loads the NMP settings (no ``deft_config`` attribute at
    all), which is itself a case the handlers must survive — so this is opt-in
    per test rather than an autouse fixture.
    """
    original = settings.system_config
    settings.system_config = DeftSystemConfig(deft_config=DEFTConfig(**deft_kwargs))
    try:
        yield settings.system_config.deft_config
    finally:
        settings.system_config = original


def _ready_datastore():
    """A datastore double whose snapshot is ready on the create response."""
    datastore = MagicMock()
    datastore.register.return_value = {
        "snapshot_id": "snap-1",
        "status": "ready",
        "manifests": [{"uri": "s3://sc-artifacts/snapshots/snap-1/manifest0.jsonl"}],
    }
    return datastore


def _presign_client(signed: dict[str, str]):
    """A StorageBackendClient stand-in returning a fixed presign map."""
    client = MagicMock()
    client.get_presigned_urls.return_value = {
        "items": [{"input": src, "presigned_url": url} for src, url in signed.items()],
        "failed": [],
    }
    return client


class TestSnapshotResolvesTheStore:
    def test_its_run_snapshots_from_its_own_store(self, patched_sleep):
        # The whole point: the datastore seam is handed the run's use case, so
        # the Storage Backend client is built from the ITS endpoints rather than
        # the process-global (AOI) ones.
        datastore = _ready_datastore()
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {
            "model_name": "its-stream-detector",
            "use_case": "its",
            "job_config": _ITS_JOB_CONFIG,
        }
        with (
            _deft_settings(use_case_endpoints=_TWO_STORE_ENDPOINTS),
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep(),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "error" not in ctx
        provider.datastore.assert_called_once_with("its-stream-detector", use_case="its")

    def test_aoi_run_is_unchanged_with_no_endpoints_declared(self, patched_sleep):
        # Regression pin: a deployment that never configured use_case_endpoints
        # keeps taking exactly one store, and the use case it passes resolves to
        # the SAME config object (for_use_case returns self).
        datastore = _ready_datastore()
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "changenet-aoi", "use_case": "aoi", "job_config": _AOI_JOB_CONFIG}
        with (
            _deft_settings() as deft_config,
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep(),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)
            # Identity, not equality: an unchanged config cannot produce a
            # different object for AOI than it did before this wiring existed.
            assert deft_config.for_use_case("aoi") is deft_config

        assert ctx["snapshot_id"] == "snap-1"
        provider.datastore.assert_called_once_with("changenet-aoi", use_case="aoi")

    def test_undeclared_use_case_fails_closed_before_any_snapshot(self, patched_sleep):
        # Named job configs come from Mongo and never pass the YAML startup
        # checks, so this guard is the only thing between a misconfigured run and
        # a snapshot taken from a sibling's store. It must fire BEFORE the
        # datastore is even resolved — an inherited store would produce a
        # perfectly successful snapshot of the wrong data.
        provider = MagicMock()
        db = MagicMock()

        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "its-stream-detector",
            "use_case": "itss",  # typo'd; no entry in use_case_endpoints
            "job_config": _ITS_JOB_CONFIG,
        }
        with (
            _deft_settings(use_case_endpoints=_TWO_STORE_ENDPOINTS),
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patched_sleep(),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert "use_case_endpoints" in ctx["error"]
        assert "itss" in ctx["error"]
        provider.datastore.assert_not_called()  # nothing was snapshotted
        db.mark_flywheel_run_error.assert_called_once()

    def test_a_declared_but_inheriting_use_case_passes_the_guard(self, patched_sleep):
        # An empty entry is a DELIBERATE statement of "inherits" — the guard
        # demands a declaration, not an override. (AOI is exactly this case in a
        # two-store deployment.)
        datastore = _ready_datastore()
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "changenet-aoi", "use_case": "aoi", "job_config": _AOI_JOB_CONFIG}
        with (
            _deft_settings(use_case_endpoints=_TWO_STORE_ENDPOINTS),
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep(),
        ):
            ctx = deft_tasks.select_and_snapshot(prev)

        assert ctx["snapshot_id"] == "snap-1"


class TestSnapshotPollBudget:
    """The readiness ceiling is per job config, defaulting to today's 60 x 5s."""

    def _run(self, job_config, patched_sleep):
        datastore = MagicMock()
        datastore.register.return_value = {"snapshot_id": "snap-1", "status": "creating"}
        datastore.get_snapshot.return_value = {"snapshot_id": "snap-1", "status": "creating"}
        provider = MagicMock()
        provider.datastore.return_value = datastore

        prev = {"model_name": "m", "use_case": "aoi", "job_config": job_config}
        with (
            _deft_settings(),
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patched_sleep() as sleep,
        ):
            ctx = deft_tasks.select_and_snapshot(prev)
        return ctx, datastore, sleep

    def test_configured_budget_stops_after_exactly_two_polls(self, patched_sleep):
        ctx, datastore, sleep = self._run(
            {**_ITS_JOB_CONFIG, "snapshot_poll_max_attempts": 2, "snapshot_poll_interval_s": 30.0},
            patched_sleep,
        )

        assert datastore.get_snapshot.call_count == 2
        sleep.assert_called_with(30.0)
        # The message reports the elapsed budget, not just a poll count: "2
        # polls" tells an operator nothing about whether to raise the ceiling.
        assert "2 polls" in ctx["error"]
        assert "60s" in ctx["error"]

    def test_absent_budget_reproduces_the_sixty_by_five_default(self, patched_sleep):
        ctx, datastore, sleep = self._run(_AOI_JOB_CONFIG, patched_sleep)

        assert datastore.get_snapshot.call_count == SNAPSHOT_POLL_MAX_ATTEMPTS == 60
        sleep.assert_called_with(5.0)
        assert "60 polls" in ctx["error"]
        assert "300s" in ctx["error"]

    def test_an_unusable_budget_degrades_to_the_default(self, patched_sleep):
        # Belt-and-braces: DeftJobConfig's ge=1 / gt=0 bounds are the real guard,
        # so a 0 can only reach the handler from a hand-built context.
        ctx, datastore, sleep = self._run(
            {**_AOI_JOB_CONFIG, "snapshot_poll_max_attempts": 0, "snapshot_poll_interval_s": 0},
            patched_sleep,
        )

        assert datastore.get_snapshot.call_count == SNAPSHOT_POLL_MAX_ATTEMPTS
        sleep.assert_called_with(5.0)


class TestInitializeFailsFastOnTheUseCase:
    """A typo'd use case dies at initialize, not an entire fine-tune later."""

    def _initialize(self, job_config, provider):
        db = MagicMock()
        with (
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.backends.factory.get_backend_provider", return_value=provider),
        ):
            return deft_tasks.initialize_aoi_workflow(
                workload_id="w1",
                flywheel_run_id=_FW_ID,
                client_id="c1",
                job_type="aoi_job",
                job_config=job_config,
            ), db

    def test_unknown_use_case_stops_the_run_at_initialize(self):
        # The REAL DEFT provider is used so the evaluator registry itself is the
        # thing under test — a double would pin the call, not the behaviour.
        from src.backends.deft.provider import DeftBackendProvider

        ctx, db = self._initialize(
            {**_ITS_JOB_CONFIG, "use_case": "itss"}, DeftBackendProvider(DEFTConfig())
        )

        assert "itss" in ctx["error"]
        assert "use_case" in ctx["error"]
        db.mark_flywheel_run_error.assert_called_once()
        # The context carries no use case, so every later stage skips rather
        # than guessing one.
        assert "use_case" not in ctx

    def test_registered_use_cases_initialize_normally(self):
        from src.backends.deft.provider import DeftBackendProvider

        ctx, _ = self._initialize(_ITS_JOB_CONFIG, DeftBackendProvider(DEFTConfig()))

        assert ctx["use_case"] == "its"
        assert "error" not in ctx


class TestPresignResolvesTheStore:
    """Pull locators are signed by the store that actually holds the artifact."""

    def _start_promotion(self, prev, signed_client, endpoints):
        db = MagicMock()
        promo_db = MagicMock()
        promo_db.__getitem__.return_value.find_one.return_value = None
        # First rollout: neither baseline tier resolves anything, so the record
        # is created without a rollback target (not this test's subject).
        mlflow = MagicMock()
        mlflow.get_serving_version_by_alias.return_value = None
        with (
            _deft_settings(use_case_endpoints=endpoints) as deft_config,
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db", return_value=promo_db),
            patch("src.lib.integration.mlflow_client.MLflowClient", return_value=mlflow),
            patch("src.api.promotion.service.PromotionService") as service,
            patch("src.tasks.promotion.tasks.advance_promotion"),
            patch(
                "src.backends.deft.storage_backend_client.StorageBackendClient",
                return_value=signed_client,
            ) as client_cls,
        ):
            ctx = deft_tasks.start_promotion(prev)
        # ``deft_config`` is captured while the settings swap is still in place —
        # the process config the run saw, for the identity pin below.
        return ctx, service, client_cls, deft_config

    def test_its_artifact_is_presigned_against_the_its_storage_backend(self):
        artifact = "s3://sc-artifacts/runs/its-1/model.engine"
        signed = _presign_client({artifact: "https://its-store/model.engine?X-Amz-Signature=a"})
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "its-stream-detector",
            "use_case": "its",
            "candidate_version": "4",
            "deft_run_id": "its-run-1",
            "artifact_uri": artifact,
            "sha256": "d" * 64,
            "job_config": _ITS_JOB_CONFIG,
        }
        ctx, service, client_cls, _ = self._start_promotion(prev, signed, _TWO_STORE_ENDPOINTS)

        assert "error" not in ctx
        # The presign client was built from the ITS endpoints. Signing against
        # AOI's Storage Backend would not fail here — it would fail at the Model
        # Manager pull, after the promotion record exists.
        used_config = client_cls.call_args.args[0]
        assert used_config.storage_backend_url == "http://sc-backend:8080"
        assert used_config.object_store_uri == "s3://sc-artifacts"

        record = service.return_value.create.call_args.args[0]
        assert record.use_case == "its"
        assert record.model_format == "tensorrt"
        assert record.artifact_uri.startswith("https://its-store/")

    def test_aoi_presign_config_is_the_unmodified_process_config(self):
        # Regression pin for the AOI path: the object handed to the presign
        # helper is the SAME DEFTConfig the process is configured with.
        artifact = "s3://aoi-artifacts/runs/v1/weights.onnx"
        signed = _presign_client({artifact: "https://store/weights.onnx?X-Amz-Signature=a"})
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "changenet-aoi",
            "use_case": "aoi",
            "candidate_version": "2",
            "artifact_uri": artifact,
            "sha256": "d" * 64,
            "job_config": _AOI_JOB_CONFIG,
        }
        ctx, service, client_cls, deft_config = self._start_promotion(prev, signed, {})

        assert "error" not in ctx
        assert client_cls.call_args.args[0] is deft_config
        record = service.return_value.create.call_args.args[0]
        assert record.use_case == "aoi"
        assert record.model_format == "onnx"

    def test_a_context_without_a_use_case_stops_the_stage(self):
        # The old ctx.get("use_case", "aoi") default turned a malformed context
        # into an AOI promotion — which the online beat would then grade with
        # the AOI FAR evaluator on detection data.
        db = MagicMock()
        service = MagicMock()
        prev = {
            "flywheel_run_id": _FW_ID,
            "model_name": "its-stream-detector",
            "candidate_version": "4",
            "sha256": "d" * 64,
            "job_config": _ITS_JOB_CONFIG,
        }
        with (
            _deft_settings(use_case_endpoints=_TWO_STORE_ENDPOINTS),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", return_value=service),
            patch("src.tasks.promotion.tasks.advance_promotion") as adv,
        ):
            ctx = deft_tasks.start_promotion(prev)

        assert "use_case" in ctx["error"]
        service.create.assert_not_called()
        adv.delay.assert_not_called()


@pytest.mark.parametrize("use_case", ["aoi", "its"])
def test_snapshot_logs_the_resolved_storage_url(use_case, caplog, patched_sleep):
    """The resolved endpoint is logged: when a run is later found to have trained
    on the wrong data, this line is the record of which store it actually read."""
    datastore = _ready_datastore()
    provider = MagicMock()
    provider.datastore.return_value = datastore
    job_config = _AOI_JOB_CONFIG if use_case == "aoi" else _ITS_JOB_CONFIG

    prev = {"model_name": "m", "use_case": use_case, "job_config": job_config}
    with (
        _deft_settings(use_case_endpoints=_TWO_STORE_ENDPOINTS),
        patch("src.backends.factory.get_backend_provider", return_value=provider),
        patched_sleep(),
        caplog.at_level("INFO"),
    ):
        deft_tasks.select_and_snapshot(prev)

    expected = "http://sc-backend:8080" if use_case == "its" else DEFTConfig().storage_backend_url
    assert any(expected in message for message in caplog.messages)
