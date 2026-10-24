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
"""Backend job-type gating.

The LLM and embedding pipelines are NMP-only; a DEFT image must not run them.
A backend declares ``supported_job_types`` and the edges (the ``/jobs`` and
``/configs`` APIs, and the task dispatcher) reject anything the active backend
can't run. These tests use a real ``BackendProvider`` subclass with restricted
capabilities (standing in for DEFT) so the actual gating logic is exercised.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from src.api.backend_gate import assert_supported_type
from src.api.endpoints import create_job
from src.api.schemas import JobRequest
from src.api.triggers.endpoints import create_trigger
from src.api.triggers.schemas import TriggerCreateRequest
from src.backends.base.provider import BackendProvider
from src.backends.nmp.provider import NmpBackendProvider
from src.job_types import JobType
from src.tasks.tasks import run_nim_workflow_dag


class _DeftLikeProvider(BackendProvider):
    """A backend that supports no LLM/embedding job types (stands in for DEFT)."""

    kind = "deft"
    supported_job_types = frozenset()

    def training(self):  # pragma: no cover - stub
        return None

    def evaluator(self, judge_model_config=None):  # pragma: no cover - stub
        return None

    def datastore(self, dataset_name):  # pragma: no cover - stub
        return None

    def deployment_client(self, nim):  # pragma: no cover - stub
        return None

    def embedding(self, endpoint_url=None, model_name=None, api_key=None):  # pragma: no cover
        return None


# --------------------------------------------------------------------------- #
# 1. Capability declaration on the provider                                    #
# --------------------------------------------------------------------------- #
class TestProviderCapability:
    def test_base_default_is_fail_closed(self):
        assert BackendProvider.supported_job_types == frozenset()

    def test_nmp_supports_llm_and_embedding(self):
        provider = NmpBackendProvider()
        assert provider.supports_job_type(JobType.LLM)
        assert provider.supports_job_type("embedding_job")  # raw string also works
        # The DEFT family must never leak into the NMP allowlist — both members.
        assert not provider.supports_job_type("aoi_job")
        assert not provider.supports_job_type("its_job")

    def test_restricted_backend_supports_nothing(self):
        provider = _DeftLikeProvider()
        assert not provider.supports_job_type(JobType.LLM)
        assert not provider.supports_job_type(JobType.EMBEDDING)


# --------------------------------------------------------------------------- #
# 2. API edge — POST /jobs                                                     #
# --------------------------------------------------------------------------- #
class TestCreateJobGate:
    @pytest.mark.asyncio
    @patch("src.api.endpoints.run_nim_workflow_dag")
    @patch("src.api.endpoints.get_db")
    @patch("src.api.backend_gate.get_backend_provider")
    async def test_unsupported_job_type_rejected(self, mock_provider, mock_get_db, mock_dag):
        mock_provider.return_value = _DeftLikeProvider()
        mock_dag.delay = Mock()

        request = JobRequest(workload_id="w1", client_id="c1", job_type=JobType.LLM)
        with pytest.raises(HTTPException) as exc:
            await create_job(request)

        assert exc.value.status_code == 400
        assert "not supported by the active backend 'deft'" in exc.value.detail
        # Fail fast: no DB write, no Celery enqueue.
        mock_get_db.assert_not_called()
        mock_dag.delay.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.api.endpoints.run_nim_workflow_dag")
    @patch("src.api.endpoints.get_db")
    @patch("src.api.backend_gate.get_backend_provider")
    async def test_supported_job_type_passes_gate(self, mock_provider, mock_get_db, mock_dag):
        mock_provider.return_value = NmpBackendProvider()
        mock_db = Mock()
        mock_get_db.return_value = mock_db
        result = Mock()
        result.inserted_id = ObjectId()
        mock_db.flywheel_runs.insert_one.return_value = result
        mock_dag.delay = Mock()

        request = JobRequest(workload_id="w1", client_id="c1", job_type=JobType.LLM)
        response = await create_job(request)

        assert response.status == "queued"
        mock_dag.delay.assert_called_once()


# --------------------------------------------------------------------------- #
# 3. API edge — POST/PUT /configs (config_type shares the job_type vocabulary) #
# --------------------------------------------------------------------------- #
class TestConfigGate:
    @patch("src.api.backend_gate.get_backend_provider")
    def test_unsupported_config_type_rejected(self, mock_provider):
        mock_provider.return_value = _DeftLikeProvider()
        with pytest.raises(HTTPException) as exc:
            assert_supported_type("llm_job", field_name="config_type")
        assert exc.value.status_code == 400
        assert "config_type 'llm_job' is not supported" in exc.value.detail

    @patch("src.api.backend_gate.get_backend_provider")
    def test_supported_config_type_allowed(self, mock_provider):
        mock_provider.return_value = NmpBackendProvider()
        # Should not raise.
        assert_supported_type("embedding_job", field_name="config_type")


# --------------------------------------------------------------------------- #
# 3b. API edge — POST /triggers (a trigger fires a job of its job_type)        #
# --------------------------------------------------------------------------- #
class TestTriggerGate:
    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.backend_gate.get_backend_provider")
    async def test_unsupported_trigger_job_type_rejected(self, mock_provider, mock_get_db):
        mock_provider.return_value = _DeftLikeProvider()
        request = TriggerCreateRequest(
            name="t1",
            workload_id="w1",
            client_id="c1",
            job_type=JobType.LLM,
            trigger_type="schedule",
            eval_schedule="0 0 * * 0",
            config_name="cfg",
        )
        with pytest.raises(HTTPException) as exc:
            await create_trigger(request)

        assert exc.value.status_code == 400
        assert "not supported by the active backend 'deft'" in exc.value.detail
        # Gate runs before any DB access.
        mock_get_db.assert_not_called()


# --------------------------------------------------------------------------- #
# 4. Dispatcher — defense in depth (covers the trigger path)                   #
# --------------------------------------------------------------------------- #
class TestDispatcherGate:
    @patch("src.backends.factory.get_backend_provider")
    @patch("src.tasks.tasks.load_job_configuration")
    @patch("src.tasks.tasks.db_manager")
    def test_unsupported_job_type_marks_error_and_raises(
        self, mock_db_manager, mock_load_cfg, mock_provider
    ):
        from src.api.schemas import FlywheelRunStatus

        flywheel_run_id = str(ObjectId())
        mock_db_manager.get_flywheel_run.return_value = {
            "status": FlywheelRunStatus.PENDING,
            "_id": ObjectId(flywheel_run_id),
        }
        job_cfg = MagicMock()
        job_cfg.ingestion_config = None
        job_cfg.data_split_config = None
        mock_load_cfg.return_value = job_cfg
        mock_provider.return_value = _DeftLikeProvider()

        with pytest.raises(ValueError, match="does not support job_type 'llm_job'"):
            run_nim_workflow_dag(
                workload_id="w1",
                flywheel_run_id=flywheel_run_id,
                client_id="c1",
                job_type="llm_job",
            )

        mock_db_manager.mark_flywheel_run_error.assert_called_once()


# --------------------------------------------------------------------------- #
# 6. Submission edge — which job types must supply workload_id / client_id     #
# --------------------------------------------------------------------------- #
class TestJobRequestWorkloadRequirement:
    """``JobRequest._require_workload_and_client_for_nmp_jobs``.

    Previously unpinned in BOTH directions: nothing asserted that aoi_job was
    exempt, and nothing asserted that llm_job was not. An inverted predicate
    would have shipped green, silently making ``workload_id`` optional for the
    NMP pipelines that genuinely index their data by it.
    """

    @pytest.mark.parametrize("job_type", [JobType.AOI, JobType.ITS, "aoi_job", "its_job"])
    def test_deft_family_may_omit_workload_and_client(self, job_type):
        # The design's two-field submission: DEFT data comes from an
        # object-store snapshot addressed by the job config, so there is
        # nothing for workload_id/client_id to identify.
        req = JobRequest(job_type=job_type)
        assert req.workload_id is None
        assert req.client_id is None

    @pytest.mark.parametrize("job_type", [JobType.LLM, JobType.EMBEDDING])
    def test_nmp_job_types_still_require_both(self, job_type):
        with pytest.raises(ValueError, match="workload_id, client_id required for job_type"):
            JobRequest(job_type=job_type)

    @pytest.mark.parametrize("job_type", [JobType.LLM, JobType.EMBEDDING])
    def test_nmp_job_types_still_require_client_id_alone(self, job_type):
        with pytest.raises(ValueError, match="client_id required for job_type"):
            JobRequest(job_type=job_type, workload_id="w1")

    def test_nmp_job_types_accept_both(self):
        req = JobRequest(job_type=JobType.LLM, workload_id="w1", client_id="c1")
        assert req.workload_id == "w1"


class TestDeftFamilyPredicate:
    """``src.job_types.DEFT_JOB_TYPES`` is the single list every gate consumes.

    ITS-R-05 widens six predicates across five modules. Nothing links them at
    runtime beyond this constant, so pin the constant AND every consumer here:
    a site that goes back to hand-listing job types fails loudly at one place
    rather than silently at whichever edge it guards.
    """

    def test_family_membership(self):
        from src.job_types import DEFT_JOB_TYPES, is_deft_job_type

        assert DEFT_JOB_TYPES == frozenset({JobType.AOI, JobType.ITS})
        # Raw wire strings match too — trigger documents come out of Mongo as str.
        assert is_deft_job_type("aoi_job")
        assert is_deft_job_type("its_job")
        assert is_deft_job_type(JobType.ITS)
        assert not is_deft_job_type("llm_job")
        assert not is_deft_job_type("embedding_job")
        assert not is_deft_job_type(None)

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_every_gate_admits_the_whole_family(self, job_type):
        from unittest.mock import MagicMock as _MagicMock

        from src.api.triggers.models import RetrainingTrigger
        from src.backends.deft.provider import DeftBackendProvider
        from src.tasks.tasks import _DEFT_PIPELINES
        from src.tasks.triggers.executor import TriggerExecutor

        # 1. backend capability (gates /jobs, /configs, /triggers, dispatcher)
        assert DeftBackendProvider().supports_job_type(job_type)
        # 2. pipeline registration (BasePipeline.validate_config). One pipeline
        #    class PER job type since the AOI/ITS split, so the gate is "the
        #    dispatcher has a pipeline mapped for this type, and that pipeline
        #    admits it" — not "AOIPipeline admits it", which is now false for
        #    its_job by design.
        assert job_type in _DEFT_PIPELINES
        assert job_type in _DEFT_PIPELINES[job_type](_MagicMock()).get_supported_job_types()
        # 3. API-edge workload exemption
        assert JobRequest(job_type=job_type).workload_id is None
        # 4. trigger request schema accepts it with no config_name
        assert (
            TriggerCreateRequest(
                name="t",
                workload_id="w1",
                client_id="c1",
                job_type=job_type,
                trigger_type="schedule",
                eval_schedule="0 0 * * 0",
            ).config_name
            is None
        )
        # 5. persisted trigger model, and the executor's DEFT-branch predicate
        trigger = RetrainingTrigger(
            name="t",
            workload_id="w1",
            client_id="c1",
            job_type=job_type,
            trigger_type="data_threshold",
            eval_schedule="0 0 * * 0",
            min_new_records=100,
        )
        assert trigger.enable_data_ingestion is False
        assert TriggerExecutor._is_deft(trigger)
