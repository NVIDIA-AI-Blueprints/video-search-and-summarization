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

"""DEFT-family (``aoi_job`` / ``its_job``) trigger support: validation + executor.

Was ``test_aoi_triggers.py``, when ``aoi_job`` was the only DEFT job type and the
executor's branch predicate was called ``_is_aoi``. The predicate is now
``_is_deft`` and the branch serves the whole family, so the coverage is
parametrized over both types: every assertion here that held for AOI holds for
ITS for the SAME reason, because the code under test dispatches on
``is_deft_job_type(job_type)``, never on ``== "aoi_job"``.

That parametrization is the point, not decoration. The executor's DEFT branch
picks the DEFT data plane over the NMP one, and getting it wrong for ``its_job``
fails SILENTLY: ``_execute_data_threshold`` would count ITS records in
Elasticsearch, which holds none, so the trigger reports "skipped" forever
instead of ever retraining.

DEFT triggers have no ingestion path — data_threshold reads the Storage
Backend live catalog stats (records_since_last_snapshot) and both trigger
types dispatch the DEFT workflow WITHOUT waiting for completion.

The executor's internals are still spelled ``_execute_aoi_*`` /
``_get_aoi_available_records`` / ``_dispatch_aoi_job``; those names are
historical and serve the whole family (see ``src/tasks/triggers/executor.py``).
"""

from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.triggers.models import RetrainingTrigger
from src.api.triggers.schemas import TriggerCreateRequest
from src.tasks.triggers.executor import TriggerExecutor

#: Every DEFT-family job type. Sourced from the registry rather than hardcoded so
#: a new family member joins this file's coverage automatically instead of
#: quietly inheriting AOI-only testing.
DEFT_TYPES = ["aoi_job", "its_job"]


def _deft_trigger(job_type: str = "aoi_job", **overrides) -> RetrainingTrigger:
    fields = {
        "name": f"{job_type}-data-threshold",
        "workload_id": f"{job_type}-line-1",
        "client_id": "factory-a",
        "job_type": job_type,
        "trigger_type": "data_threshold",
        "eval_schedule": "0 * * * *",
        "min_new_records": 100,
        "enabled": True,
    }
    fields.update(overrides)
    return RetrainingTrigger(**fields)


def test_the_parametrized_family_is_the_whole_family():
    """``DEFT_TYPES`` must not drift from the registry.

    If a third DEFT job type is added and this list is not updated, every test
    in this file silently keeps testing only the two it knows — the failure mode
    that left ``its_job`` untested here in the first place.
    """
    from src.job_types import DEFT_JOB_TYPES

    assert set(DEFT_TYPES) == {jt.value for jt in DEFT_JOB_TYPES}


class TestDeftTriggerValidation:
    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_data_threshold_without_ingestion_is_valid(self, job_type):
        trigger = _deft_trigger(job_type)
        # enable_data_ingestion defaults True but is coerced off for the family.
        assert trigger.enable_data_ingestion is False
        assert trigger.config_name is None

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_schedule_is_valid(self, job_type):
        trigger = _deft_trigger(job_type, trigger_type="schedule", min_new_records=None)
        assert trigger.trigger_type == "schedule"

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_min_new_records_still_required_for_data_threshold(self, job_type):
        with pytest.raises(ValueError, match="min_new_records is required"):
            _deft_trigger(job_type, min_new_records=None)

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_metric_degradation_rejected(self, job_type):
        # The message names the job type, so this also pins that the refusal is
        # reported against the type the user actually submitted.
        with pytest.raises(ValueError, match=f"metric_degradation .* not supported for {job_type}"):
            _deft_trigger(
                job_type,
                trigger_type="metric_degradation",
                min_new_records=None,
                metric_thresholds={"FAR_pct": 1.0},
            )

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_ingestion_config_rejected(self, job_type):
        from src.lib.ingestion import IngestionConfig

        with pytest.raises(ValueError, match="ingestion_config is not supported"):
            _deft_trigger(
                job_type,
                ingestion_config=IngestionConfig(
                    plugin_name="p", dataset_type="bug_pairs_separated", config={}
                ),
            )

    def test_config_name_still_required_for_nmp_jobs(self):
        with pytest.raises(ValueError, match="config_name is required"):
            RetrainingTrigger(
                name="llm-sched",
                workload_id="w",
                client_id="c",
                job_type="llm_job",
                trigger_type="schedule",
                eval_schedule="0 * * * *",
            )

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_create_request_accepts_deft_without_config_name(self, job_type):
        req = TriggerCreateRequest(
            name=f"{job_type}-sched",
            workload_id="w",
            client_id="c",
            job_type=job_type,
            trigger_type="schedule",
            eval_schedule="0 0 * * *",
        )
        assert req.config_name is None

    def test_create_request_rejects_nmp_without_config_name(self):
        with pytest.raises(ValueError, match="config_name is required"):
            TriggerCreateRequest(
                name="llm-sched",
                workload_id="w",
                client_id="c",
                job_type="llm_job",
                trigger_type="schedule",
                eval_schedule="0 0 * * *",
            )

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_create_request_rejects_deft_ingestion_config(self, job_type):
        # API edge must 422 (not 500 deeper) on a DEFT type + ingestion_config.
        from src.lib.ingestion import IngestionConfig

        with pytest.raises(ValueError, match="ingestion_config is not supported"):
            TriggerCreateRequest(
                name=f"{job_type}-sched",
                workload_id="w",
                client_id="c",
                job_type=job_type,
                trigger_type="schedule",
                eval_schedule="0 0 * * *",
                ingestion_config=IngestionConfig(
                    plugin_name="p", dataset_type="bug_pairs_separated", config={}
                ),
            )

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_create_request_rejects_deft_metric_degradation(self, job_type):
        # API edge must 422 (not 500 deeper) on a DEFT type + metric_degradation.
        with pytest.raises(ValueError, match=f"metric_degradation .* not supported for {job_type}"):
            TriggerCreateRequest(
                name=f"{job_type}-md",
                workload_id="w",
                client_id="c",
                job_type=job_type,
                trigger_type="metric_degradation",
                eval_schedule="0 0 * * *",
                metric_thresholds={"FAR_pct": 1.0},
            )


class TestTheExecutorTakesTheDeftBranchForTheWholeFamily:
    """Branch SELECTION, isolated from what the branch then does.

    ``_is_deft`` is the predicate the three ``_execute_*`` entry points consult
    to choose the DEFT data plane over the NMP one. Pinning it directly (rather
    than only inferring it from a successful dispatch) is what catches a
    regression narrowing it back to ``trigger.job_type == "aoi_job"`` — under
    which an ITS trigger would fall through to the NMP path and skip forever
    while still reporting a healthy "skipped" execution.
    """

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_every_deft_type_takes_the_deft_branch(self, job_type):
        assert TriggerExecutor._is_deft(_deft_trigger(job_type)) is True

    @pytest.mark.parametrize("job_type", ["llm_job", "embedding_job"])
    def test_nmp_types_do_not(self, job_type):
        trigger = RetrainingTrigger(
            name="nmp",
            workload_id="w",
            client_id="c",
            job_type=job_type,
            trigger_type="schedule",
            eval_schedule="0 * * * *",
            config_name="cfg",
        )
        assert TriggerExecutor._is_deft(trigger) is False


class TestDeftDataThresholdExecution:
    """The data_threshold branch, end to end through ``executor.execute``.

    Parametrized over the family where the job type can change the OUTCOME
    (which pipeline the dispatch names, which message the user sees); left on a
    single type for the mechanics below the branch — the TOCTOU guard, stale-run
    reclaim, broker-failure rollback and the stats counter are byte-identical
    code paths for every DEFT type, and running them twice would buy coverage of
    ``dict.update`` rather than of the split.
    """

    def _executor(self, mock_trigger_db) -> TriggerExecutor:
        mock_trigger_db.trigger_executions.find_one.return_value = None
        mock_trigger_db.flywheel_runs.find_one.return_value = None
        mock_trigger_db.flywheel_runs.insert_one.return_value = MagicMock(inserted_id=ObjectId())
        return TriggerExecutor(mock_trigger_db)

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_fires_when_catalog_count_meets_threshold(self, mock_dag, mock_trigger_db, job_type):
        """The dispatch carries the trigger's OWN job type to the dispatcher.

        This is the seam the AOI/ITS pipeline split turned load-bearing: the
        dispatcher now routes on ``job_type`` through ``_DEFT_PIPELINES``, so a
        trigger that hardcoded (or dropped) the type would run an ITS retrain
        down the AOI pipeline. Asserting ``kwargs["job_type"] == job_type``
        rather than a constant is what makes the ITS case a real case.
        """
        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(job_type, min_new_records=100)

        with patch.object(executor, "_get_aoi_available_records", return_value=150):
            execution = executor.execute(trigger)

        assert execution.status == "completed"
        assert execution.skip_reason is None
        assert execution.triggered_training is True
        assert execution.available_records_since_last_training == 150
        assert "Data threshold met" in execution.decision_reason
        # Dispatched fire-and-forget with the trigger's identity, no overrides.
        mock_dag.delay.assert_called_once()
        kwargs = mock_dag.delay.call_args.kwargs
        assert kwargs["workload_id"] == f"{job_type}-line-1"
        assert kwargs["client_id"] == "factory-a"
        assert kwargs["job_type"] == job_type
        assert kwargs["config_name"] is None
        assert "runtime_overrides" not in kwargs
        # FlywheelRun persisted before dispatch, under the same job type.
        mock_trigger_db.flywheel_runs.insert_one.assert_called_once()
        assert mock_trigger_db.flywheel_runs.insert_one.call_args.args[0]["job_type"] == job_type

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_reads_the_storage_backend_catalog_not_elasticsearch(
        self, mock_dag, mock_trigger_db, job_type
    ):
        """The silent failure the ``_is_deft`` predicate exists to prevent.

        The NMP branch counts ingested records in Elasticsearch, which holds
        none for a DEFT run — a family member routed there reports "skipped"
        forever rather than failing. So assert the DEFT counter was the one
        consulted, and that the Elasticsearch counter never was.
        """
        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(job_type, min_new_records=100)

        deft_counter = MagicMock(return_value=150)
        with (
            patch.object(executor, "_get_aoi_available_records", deft_counter),
            patch("src.tasks.triggers.executor.get_available_records_count") as es_counter,
        ):
            execution = executor.execute(trigger)

        assert execution.triggered_training is True
        deft_counter.assert_called_once()
        es_counter.assert_not_called()

    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_skips_below_threshold(self, mock_dag, mock_trigger_db):
        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(min_new_records=100)

        with patch.object(executor, "_get_aoi_available_records", return_value=42):
            execution = executor.execute(trigger)

        assert execution.status == "skipped"
        assert "Insufficient new catalog records: 42 < 100" in execution.skip_reason
        mock_dag.delay.assert_not_called()
        mock_trigger_db.flywheel_runs.insert_one.assert_not_called()

    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_skips_while_a_run_is_in_flight(self, mock_dag, mock_trigger_db):
        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(min_new_records=100)
        # An AOI run is mid-rollout for this workload.
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(),
            "status": "promoting",
        }

        stats = MagicMock(return_value=500)
        with patch.object(executor, "_get_aoi_available_records", stats):
            execution = executor.execute(trigger)

        assert execution.status == "skipped"
        assert "still in flight" in execution.skip_reason
        mock_dag.delay.assert_not_called()
        # Guard runs BEFORE the Storage Backend call: a rollout in flight must
        # not generate stats traffic (or outage-induced failed executions).
        stats.assert_not_called()

    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_dispatch_failure_marks_run_failed_leaving_no_orphan(self, mock_dag, mock_trigger_db):
        # If the broker enqueue raises, the freshly-inserted PENDING run must
        # be marked failed — otherwise it wedges the in-flight guard for every
        # future fire of this trigger.
        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(min_new_records=100)
        run_oid = ObjectId()
        mock_trigger_db.flywheel_runs.insert_one.return_value = MagicMock(inserted_id=run_oid)
        mock_dag.delay.side_effect = RuntimeError("broker unreachable")

        with patch.object(executor, "_get_aoi_available_records", return_value=500):
            execution = executor.execute(trigger)

        assert execution.status == "failed"
        assert "broker unreachable" in execution.error
        update_filter, update_doc = mock_trigger_db.flywheel_runs.update_one.call_args.args
        assert update_filter == {"_id": run_oid}
        assert update_doc["$set"]["status"] == "failed"
        assert "enqueue" in update_doc["$set"]["error"]

    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_lost_active_run_race_skips(self, mock_dag, mock_trigger_db):
        # TOCTOU: the pre-insert guard saw no active run, but between find and
        # insert another fire started an earlier run. The insert-then-verify
        # guard must roll our run back and skip rather than double-dispatch.
        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(min_new_records=100)
        my_oid = ObjectId()
        earlier = {"_id": ObjectId(), "status": "pending"}
        mock_trigger_db.flywheel_runs.insert_one.return_value = MagicMock(inserted_id=my_oid)
        # 1st find_one = pre-insert guard (clear); 2nd = _earlier_active_aoi_run (race)
        mock_trigger_db.flywheel_runs.find_one.side_effect = [None, earlier]

        with patch.object(executor, "_get_aoi_available_records", return_value=500):
            execution = executor.execute(trigger)

        assert execution.status == "skipped"
        assert "Lost the active-run race" in execution.skip_reason
        mock_dag.delay.assert_not_called()
        mock_trigger_db.flywheel_runs.delete_one.assert_called_once_with({"_id": my_oid})

    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_stale_active_run_is_reclaimed_and_dispatch_proceeds(self, mock_dag, mock_trigger_db):
        # A run wedged past the active ceiling must be reclaimed (marked failed)
        # so it stops blocking the trigger — then this fire dispatches.
        from datetime import UTC, datetime, timedelta

        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(min_new_records=100)
        stale_oid = ObjectId.from_datetime(datetime.now(UTC) - timedelta(days=10))
        stale_run = {"_id": stale_oid, "status": "promoting"}
        mock_trigger_db.flywheel_runs.insert_one.return_value = MagicMock(inserted_id=ObjectId())
        mock_trigger_db.flywheel_runs.update_one.return_value = MagicMock(modified_count=1)
        # 1st find_one = guard (stale run → reclaimed → None); 2nd = verify (clear)
        mock_trigger_db.flywheel_runs.find_one.side_effect = [stale_run, None]

        with patch.object(executor, "_get_aoi_available_records", return_value=500):
            execution = executor.execute(trigger)

        # Stale run was reclaimed as FAILED...
        reclaim_filter, reclaim_doc = mock_trigger_db.flywheel_runs.update_one.call_args_list[
            0
        ].args
        assert reclaim_filter["_id"] == stale_oid
        assert reclaim_doc["$set"]["status"] == "failed"
        assert "Reclaimed" in reclaim_doc["$set"]["error"]
        # ...and this fire dispatched a fresh run.
        assert execution.triggered_training is True
        mock_dag.delay.assert_called_once()

    def test_stats_failure_fails_the_execution_visibly(self, mock_trigger_db):
        executor = self._executor(mock_trigger_db)
        trigger = _deft_trigger(min_new_records=100)

        with patch.object(
            executor,
            "_get_aoi_available_records",
            side_effect=RuntimeError("storage backend unreachable"),
        ):
            execution = executor.execute(trigger)

        assert execution.status == "failed"
        assert "storage backend unreachable" in execution.error

    def test_counter_reads_records_since_last_snapshot(self, mock_trigger_db):
        executor = self._executor(mock_trigger_db)
        fake_client = MagicMock()
        fake_client.get_stats.return_value = {
            "total_records": 9000,
            "records_since_last_snapshot": 321,
        }
        with patch(
            "src.backends.deft.storage_backend_client.StorageBackendClient",
            return_value=fake_client,
        ):
            assert executor._get_aoi_available_records(_deft_trigger()) == 321

    def test_counter_defaults_to_zero_when_field_missing(self, mock_trigger_db):
        executor = self._executor(mock_trigger_db)
        fake_client = MagicMock()
        fake_client.get_stats.return_value = {"total_records": 9000}
        with patch(
            "src.backends.deft.storage_backend_client.StorageBackendClient",
            return_value=fake_client,
        ):
            assert executor._get_aoi_available_records(_deft_trigger()) == 0


class TestDeftCounterStoreRouting:
    """The counter must be read from THIS trigger's store, not a default one.

    ``StorageBackendClient()`` with no config falls back to ``DEFTConfig()``
    FIELD DEFAULTS, not the deployment's ``deft_config`` — so once
    ``use_case_endpoints`` is populated the ITS trigger would decide off AOI's
    record count while the ITS run it dispatches snapshots the ITS store. The
    decision and the run must read the same store.
    """

    #: aoi declares-and-inherits; its overrides. Both are DECLARED, which is what
    #: the fail-closed rule keys on.
    @staticmethod
    def _deft_config():
        from src.config.deft import DEFTConfig, UseCaseEndpoints

        return DEFTConfig(
            storage_backend_url="http://storage-aoi:8080",
            use_case_endpoints={
                "aoi": UseCaseEndpoints(),
                "its": UseCaseEndpoints(storage_backend_url="http://storage-its:9090"),
            },
        )

    @staticmethod
    def _read(executor, trigger, deft_config, use_case=..., loader_error=None):
        """Run the counter, returning (count, config the client was built with)."""
        from types import SimpleNamespace

        seen = {}

        class _FakeClient:
            def __init__(self, config=None, use_case=None):
                seen["config"] = config
                # The catalog is selected from the use case, so routing is only
                # correct if the resolved config AND the use case both arrive.
                seen["use_case"] = use_case

            def get_stats(self):
                return {"records_since_last_snapshot": 7}

        loader = (
            {"side_effect": loader_error}
            if loader_error
            else {"return_value": SimpleNamespace(use_case=use_case)}
        )
        with (
            patch("src.backends.deft.storage_backend_client.StorageBackendClient", _FakeClient),
            patch(
                "src.config.settings",
                SimpleNamespace(system_config=SimpleNamespace(deft_config=deft_config)),
            ),
            patch("src.tasks.tasks.load_job_configuration", **loader),
        ):
            count = executor._get_aoi_available_records(trigger)
        return count, seen["config"], seen.get("use_case")

    def test_its_trigger_reads_the_its_store(self, mock_trigger_db):
        count, config, seen_use_case = self._read(
            TriggerExecutor(mock_trigger_db),
            _deft_trigger("its_job"),
            self._deft_config(),
            use_case="its",
        )
        assert count == 7
        assert config.storage_backend_url == "http://storage-its:9090"
        # The use_case half of the routing. Without this the argument can be
        # dropped entirely and the suite stays green -- and dropping it sends the
        # ITS trigger back to AOI's catalog, which is the exact failure this
        # change exists to prevent.
        assert seen_use_case == "its"

    def test_aoi_trigger_reads_the_aoi_store(self, mock_trigger_db):
        _, config, seen_use_case = self._read(
            TriggerExecutor(mock_trigger_db),
            _deft_trigger("aoi_job"),
            self._deft_config(),
            use_case="aoi",
        )
        assert config.storage_backend_url == "http://storage-aoi:8080"

    def test_undeclared_use_case_fails_closed(self, mock_trigger_db):
        # Same rule as SelectAndSnapshotHandler._resolve_store: with a NON-EMPTY
        # map, an undeclared use case would silently inherit AOI's endpoints.
        with pytest.raises(ValueError, match="no entry in"):
            self._read(
                TriggerExecutor(mock_trigger_db),
                _deft_trigger("its_job"),
                self._deft_config(),
                use_case="surprise",
            )

    def test_unloadable_config_fails_closed_whether_or_not_stores_are_declared(
        self, mock_trigger_db
    ):
        """A renamed/deleted named config makes the loader RAISE, and that is now fatal.

        It used to be fatal ONLY where per-use-case stores were declared: with
        the empty map every use case shared one store, so reading without a use
        case cost nothing. Catalog routing changed that. The use case selects the
        Storage Backend CATALOG as well as the endpoints, so an unresolvable one
        on an empty map would read `/v1/snapshot/stats` -- AOI's catalog -- and
        decide an ITS retrain off another use case's record count, reporting
        success throughout.

        Both maps now refuse with the SAME message, deliberately. "Absent from
        the map" would be the wrong diagnosis here: the use case is not
        undeclared, it is UNKNOWN, and pointing an operator at
        `use_case_endpoints` would send them to add an entry when the actual
        fault is a renamed or deleted job configuration.
        """
        from src.config.deft import DEFTConfig

        gone = ValueError("Configuration 'renamed' not found")
        with pytest.raises(ValueError, match="could not resolve a use_case"):
            self._read(
                TriggerExecutor(mock_trigger_db),
                _deft_trigger("its_job"),
                self._deft_config(),
                loader_error=gone,
            )

        with pytest.raises(ValueError, match="could not resolve a use_case"):
            self._read(
                TriggerExecutor(mock_trigger_db),
                _deft_trigger("its_job"),
                DEFTConfig(storage_backend_url="http://storage-aoi:8080"),
                loader_error=gone,
            )

    def test_no_deft_config_leaves_the_client_default(self, mock_trigger_db):
        # Bare/NMP settings have no DEFT block at all: exactly one store, so
        # nothing to route — behave exactly as before this routing existed.
        _, config, seen_use_case = self._read(
            TriggerExecutor(mock_trigger_db), _deft_trigger("its_job"), None, use_case="its"
        )
        assert config is None


class TestDeftScheduleExecution:
    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_dispatches_unconditionally(self, mock_dag, mock_trigger_db, job_type):
        mock_trigger_db.trigger_executions.find_one.return_value = None
        mock_trigger_db.flywheel_runs.find_one.return_value = None
        mock_trigger_db.flywheel_runs.insert_one.return_value = MagicMock(inserted_id=ObjectId())
        executor = TriggerExecutor(mock_trigger_db)
        trigger = _deft_trigger(job_type, trigger_type="schedule", min_new_records=None)

        execution = executor.execute(trigger)

        assert execution.status == "completed"
        assert execution.triggered_training is True
        # Still spelled "AOI" for every family member: the branch is shared and
        # the string is not per-type. Asserted verbatim rather than loosened so
        # that if it is ever made type-aware, this is where that shows up.
        assert execution.decision_reason == "Scheduled AOI retraining"
        assert execution.training_job_id == execution.eval_job_id
        mock_dag.delay.assert_called_once()
        assert mock_dag.delay.call_args.kwargs["job_type"] == job_type

    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    @patch("src.tasks.tasks.run_nim_workflow_dag")
    def test_skips_while_a_run_is_in_flight(self, mock_dag, mock_trigger_db, job_type):
        mock_trigger_db.trigger_executions.find_one.return_value = None
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(),
            "status": "running",
        }
        executor = TriggerExecutor(mock_trigger_db)
        trigger = _deft_trigger(job_type, trigger_type="schedule", min_new_records=None)

        execution = executor.execute(trigger)

        assert execution.status == "skipped"
        assert "still in flight" in execution.skip_reason
        mock_dag.delay.assert_not_called()


class TestDeftMetricDegradationGuard:
    @pytest.mark.parametrize("job_type", DEFT_TYPES)
    def test_executor_rejects_deft_metric_degradation(self, mock_trigger_db, job_type):
        """Defense in depth: a doc edited behind the model's back still fails loudly."""
        mock_trigger_db.trigger_executions.find_one.return_value = None
        executor = TriggerExecutor(mock_trigger_db)
        trigger = _deft_trigger(job_type, trigger_type="schedule", min_new_records=None)
        # Simulate a stored doc with an unsupported combination.
        trigger.__dict__["trigger_type"] = "metric_degradation"

        execution = executor.execute(trigger)

        assert execution.status == "failed"
        assert f"not supported for {job_type}" in execution.error
