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
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.models import EvalType, NIMEvaluation, ToolEvalType, WorkloadClassification
from src.backends.nmp.evaluator import Evaluator
from src.config import LLMJudgeConfig

# ---------------------------------------------------------------------------
# Module-wide fixtures
# ---------------------------------------------------------------------------
#
# NMP 26.03 reshaped the evaluation API: ``client.evaluation.jobs.*`` is gone
# and DFW now talks to ``client.evaluation.metric_jobs.*`` instead. ``Evaluator``
# also reads ``settings.system_config.nmp_config.workspace`` and passes the
# workspace + ``name=`` kwarg on every SDK call. These fixtures provide a fully
# configured ``settings`` mock and patched ``NeMoPlatform`` so each test gets
# a freshly minted ``Evaluator`` with a deterministic mock client.


@pytest.fixture(autouse=True)
def mock_sleep_globally():
    """Automatically mock time.sleep for all tests in this module."""
    with patch("src.backends.nmp.evaluator.sleep"):
        yield


@pytest.fixture
def mock_nemo_client():
    """Fixture to create mock NeMoPlatform client."""
    return MagicMock()


@pytest.fixture(autouse=True)
def mock_settings_globally(mock_nemo_client):
    """Automatically mock settings for all tests in this module."""
    with patch("src.backends.nmp.evaluator.settings") as mock_settings:
        # Default mock settings configuration
        mock_settings.system_config.nmp_config.nemo_base_url = "http://test-nemo-url"
        mock_settings.system_config.nmp_config.nmp_namespace = "test-namespace"
        mock_settings.system_config.nmp_config.nim_base_url = "http://test-nim-url"
        # NMP 26.03: source reads ``nmp_config.workspace`` and passes it on every
        # metric_jobs call. Set an explicit string so the resulting fileset URI
        # is deterministic.
        mock_settings.system_config.nmp_config.workspace = "test-namespace"

        # Mock judge config
        mock_judge_cfg = MagicMock()
        mock_judge_cfg.is_remote.return_value = False
        mock_local_nim_cfg = MagicMock()
        mock_local_nim_cfg.model_name = "test-judge-model"
        mock_judge_cfg.get_local_nim_config.return_value = mock_local_nim_cfg
        mock_settings.judge_model_config = mock_judge_cfg

        yield mock_settings


@pytest.fixture
def evaluator(mock_settings_globally, mock_nemo_client) -> Evaluator:
    """Standard evaluator instance with default test configuration."""
    with patch("src.backends.nmp.evaluator.NeMoPlatform", return_value=mock_nemo_client):
        evaluator_instance = Evaluator(judge_model_config={"model": "test-judge"})
        evaluator_instance.nemo_client = mock_nemo_client
        return evaluator_instance


@pytest.fixture
def mock_evaluation() -> NIMEvaluation:
    return NIMEvaluation(
        nim_id=ObjectId(),  # Generate a new ObjectId
        eval_type=EvalType.BASE,
        scores={"base": 0.0},
        started_at=datetime.utcnow(),
        finished_at=None,
        runtime_seconds=0.0,
        progress=0.0,
    )


@pytest.fixture
def sample_flywheel_run_id():
    """Fixture to provide a valid ObjectId string for tests."""
    return str(ObjectId())


# ---------------------------------------------------------------------------
# Helpers for the NMP 26.03 SDK shape
# ---------------------------------------------------------------------------


def _make_status_response(status: str, status_details: dict | None = None) -> MagicMock:
    """Build a mock ``PlatformJobStatusResponse``.

    The source under test calls ``status_response.model_dump()`` (Pydantic
    BaseModel), so we mimic that here.
    """
    response = MagicMock()
    response.model_dump.return_value = {
        "status": status,
        "status_details": status_details or {},
    }
    return response


def _make_aggregate_response(scores: list[dict]) -> MagicMock:
    """Build a mock ``AggregatedMetricResult``.

    The source calls ``agg.model_dump()`` and expects ``{"scores": [...]}``.
    """
    response = MagicMock()
    response.model_dump.return_value = {"scores": scores}
    return response


# ---------------------------------------------------------------------------
# wait_for_evaluation
# ---------------------------------------------------------------------------


class TestWaitForEvaluation:
    """Test class for wait_for_evaluation method and related functionality."""

    def test_wait_for_evaluation_created_state(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test handling of created state in wait_for_evaluation"""
        job_id = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("created", {"progress": 0})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(TimeoutError):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=0.001,
                )

    def test_wait_for_evaluation_created_state_with_callback(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test handling of created state with progress callback in wait_for_evaluation"""
        job_id = "test-job-id"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("created", {"progress": 0})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(TimeoutError):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=0.001,
                    progress_callback=progress_callback,
                )

            # At least one created-state callback + one timeout error callback.
            assert len(progress_updates) >= 2
            assert progress_updates[0]["progress"] == 0.0

    def test_wait_for_evaluation_running_state(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test handling of running state in wait_for_evaluation.

        NMP 26.03 reports ``active`` which the source translates to ``running``.
        """
        job_id = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("active", {"progress": 50})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(TimeoutError):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=0.001,
                )

    def test_wait_for_evaluation_completed_state(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test handling of completed state in wait_for_evaluation"""
        job_id = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("completed", {"progress": 100})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            result = evaluator.wait_for_evaluation(
                job_id=job_id,
                flywheel_run_id=sample_flywheel_run_id,
                polling_interval=1,
                timeout=1,
            )
            assert result["status"] == "completed"

    def test_wait_for_evaluation_error_state(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test handling of error state in wait_for_evaluation.

        26.03 reports ``error`` which the source translates to ``failed``. The
        non-terminal ``failed`` branch falls through to the generic
        ``else`` arm and raises with the status_details payload.
        """
        job_id = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("error", {"error": "Test error"})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(Exception) as exc_info:
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=1,
                )
            assert "Test error" in str(exc_info.value)

    def test_wait_for_evaluation_timeout(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test timeout in wait_for_evaluation"""
        job_id = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("active", {"progress": 50})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(TimeoutError):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=0.001,
                )

    def test_wait_for_evaluation_none_progress(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test handling of None progress in wait_for_evaluation"""
        job_id = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("active", {"progress": None})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(TimeoutError):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=0.001,
                )

    def test_wait_for_evaluation_unknown_status(
        self, evaluator: Evaluator, sample_flywheel_run_id
    ) -> None:
        """Test handling of unknown status in wait_for_evaluation"""
        job_id = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("unknown_status", {"some_detail": "value"})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(Exception, match="Job status: unknown_status"):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=10,
                )


# ---------------------------------------------------------------------------
# Judge configuration
# ---------------------------------------------------------------------------


class TestJudgeConfiguration:
    """Test class for judge model configuration functionality."""

    def make_remote_judge_config(self):
        return LLMJudgeConfig(
            deployment_type="remote",
            url="http://test-remote-url/v1/chat/completions",
            model_name="remote-model-id",
            api_key_env="TEST_API_KEY_ENV",
            api_key="test-api-key",
        )

    def make_local_judge_config(self):
        return LLMJudgeConfig(
            deployment_type="local",
            model_name="local-model-name",
            tag="test-tag",
            context_length=1234,
            gpus=1,
            pvc_size="10Gi",
            registry_base="test-registry",
            customization_enabled=False,
        )

    def test_evaluator_uses_remote_judge_config(self, monkeypatch, mock_settings_globally):
        remote_cfg = self.make_remote_judge_config()
        judge_model_config = remote_cfg.judge_model_config()
        monkeypatch.setattr("src.config.settings.system_config.llm_judge_config", remote_cfg)

        with patch("src.backends.nmp.evaluator.NeMoPlatform") as mock_nemo:
            evaluator = Evaluator(judge_model_config=judge_model_config)
            # NMP 26.03 normalizes the legacy ``api_endpoint`` shape into a flat
            # ``ModelParam`` (``{name, url, format}``) — the API rejects nested
            # ``api_endpoint`` with ``extra_forbidden``.
            assert isinstance(judge_model_config, dict)
            assert evaluator.judge_model_config["url"] == remote_cfg.url
            assert evaluator.judge_model_config["name"] == remote_cfg.model_name
            assert evaluator.judge_model_config["format"] == "openai"
            # The raw api_key is materialized into a workspace secret (named after
            # the model) and referenced via ``api_key_secret`` — a raw key is not a
            # valid ModelParam field in 26.03.
            assert evaluator.judge_model_config["api_key_secret"] == "dfw-judge-remote-model-id"
            mock_nemo.return_value.secrets.create.assert_called_once()

    def test_evaluator_uses_local_judge_config(self, monkeypatch, mock_settings_globally):
        local_cfg = self.make_local_judge_config()
        judge_model_config = local_cfg.judge_model_config()
        monkeypatch.setattr("src.config.settings.system_config.llm_judge_config", local_cfg)

        with patch("src.backends.nmp.evaluator.NeMoPlatform"):
            evaluator = Evaluator(judge_model_config=judge_model_config)
            # NMP 26.03 wraps the bare local model name into a ModelParam that
            # routes through the in-cluster inference gateway.
            assert evaluator.judge_model_config["name"].endswith("local-model-name")
            assert evaluator.judge_model_config["format"] == "openai"
            assert "url" in evaluator.judge_model_config

    def test_evaluator_prefers_explicit_llm_judge_config(self, monkeypatch, mock_settings_globally):
        remote_cfg = self.make_remote_judge_config()
        judge_model_config = remote_cfg.judge_model_config()
        monkeypatch.setattr("src.config.settings.system_config.llm_judge_config", remote_cfg)

        # If you pass an explicit NIMConfig, it should use that model_name
        model_name = "explicit-model"
        judge_model_config["api_endpoint"]["model_id"] = model_name

        with patch("src.backends.nmp.evaluator.NeMoPlatform"):
            evaluator = Evaluator(judge_model_config=judge_model_config)
            # The legacy ``api_endpoint.model_id`` is mapped to ``name`` on the ModelParam.
            assert evaluator.judge_model_config["name"] == "explicit-model"


@pytest.fixture
def evaluator_instance(mock_settings_globally, mock_nemo_client):
    """
    Provides an instance of the Evaluator with default mocked settings.
    """
    with patch("src.backends.nmp.evaluator.NeMoPlatform", return_value=mock_nemo_client):
        evaluator_instance = Evaluator(judge_model_config={"model": "test-judge"})
        evaluator_instance.nemo_client = mock_nemo_client
        return evaluator_instance


@pytest.fixture
def mock_response():
    """Provides a mock successful response for SDK calls."""
    response = MagicMock()
    response.name = "mock-job-id"
    response.id = "mock-job-id"
    response.status = "created"
    response.created_at = "2024-03-20T10:00:00Z"
    return response


# ---------------------------------------------------------------------------
# Evaluator initialization
# ---------------------------------------------------------------------------


class TestEvaluator:
    def test_init_without_nemo_url(self, mock_settings_globally):
        """Test initialization fails without nemo_base_url."""
        mock_settings_globally.system_config.nmp_config.nemo_base_url = None
        with pytest.raises(AssertionError, match="nemo_base_url must be set in config"):
            Evaluator()

    def test_init_with_all_params(self, mock_settings_globally):
        """Test initialization with all parameters."""
        judge_config = {"model": "test-judge"}

        with patch("src.backends.nmp.evaluator.NeMoPlatform"):
            evaluator = Evaluator(
                judge_model_config=judge_config,
                include_tools=True,
                include_tool_choice=True,
                include_nvext=True,
            )

            assert evaluator.nemo_url == "http://test-nemo-url"
            assert evaluator.namespace == "test-namespace"
            assert evaluator.judge_model_config == judge_config
            assert evaluator.include_tools is True
            assert evaluator.include_tool_choice is True
            assert evaluator.include_nvext is True


# ---------------------------------------------------------------------------
# Basic methods (status / results / delete / URI)
# ---------------------------------------------------------------------------


class TestEvaluatorBasicMethods:
    def test_get_job_uri(self, evaluator):
        """Test get_job_uri method.

        NMP 26.03 surfaces metric_jobs under a workspace-scoped path; the URI
        now includes ``/apis/evaluation/v2/workspaces/<workspace>/metric-jobs/<id>``.
        """
        job_id = "test-job-123"
        expected_uri = (
            "http://test-nemo-url/apis/evaluation/v2/workspaces/test-namespace/"
            "metric-jobs/test-job-123"
        )
        assert evaluator.get_job_uri(job_id) == expected_uri

    def test_get_evaluation_status_running(self, evaluator):
        """Test get_evaluation_status with running status.

        26.03 emits ``active``; the source translates it to ``running``.
        """
        job_id = "test-job"
        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("active", {"progress": 75})
        )

        result = evaluator.get_evaluation_status(job_id)
        assert result["status"] == "running"
        assert result["status_details"]["progress"] == 75

    def test_get_evaluation_status_completed(self, evaluator):
        """Test get_evaluation_status with completed status."""
        job_id = "test-job"
        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("completed", {"progress": 100})
        )

        result = evaluator.get_evaluation_status(job_id)
        assert result["status"] == "completed"

    def test_get_evaluation_results_success(self, evaluator):
        """Test successful get_evaluation_results.

        Source synthesises a legacy ``tasks/metrics/scores`` tree from the
        26.03 ``AggregatedMetricResult.scores`` list. Provide a single score
        and verify the synthesized shape.
        """
        job_id = "test-job"
        evaluator.nemo_client.evaluation.metric_jobs.results.aggregate_scores.download.return_value = _make_aggregate_response(
            [{"name": "similarity", "value": 0.85, "stats": {"count": 10}}]
        )

        result = evaluator.get_evaluation_results(job_id)
        assert "tasks" in result
        assert "llm-as-judge" in result["tasks"]
        metric = result["tasks"]["llm-as-judge"]["metrics"]["similarity"]
        assert metric["scores"]["similarity"]["value"] == 0.85

    def test_get_evaluation_results_failure(self, evaluator):
        """Test get_evaluation_results with failure."""
        job_id = "test-job"

        evaluator.nemo_client.evaluation.metric_jobs.results.aggregate_scores.download.side_effect = Exception(
            "Failed to get evaluation results"
        )

        with pytest.raises(Exception, match="Failed to get evaluation results"):
            evaluator.get_evaluation_results(job_id)

    def test_delete_evaluation_job_success(self, evaluator):
        """Test successful delete_evaluation_job.

        Source passes ``name=`` + ``workspace=`` (26.03 SDK signature).
        """
        job_id = "test-job"

        evaluator.delete_evaluation_job(job_id)  # Should not raise

        evaluator.nemo_client.evaluation.metric_jobs.delete.assert_called_once_with(
            name=job_id, workspace="test-namespace"
        )

    def test_delete_evaluation_job_failure(self, evaluator):
        """Test delete_evaluation_job with failure."""
        job_id = "test-job"

        evaluator.nemo_client.evaluation.metric_jobs.delete.side_effect = Exception(
            "Failed to delete evaluation job"
        )

        with pytest.raises(Exception, match="Failed to delete evaluation job"):
            evaluator.delete_evaluation_job(job_id)


# ---------------------------------------------------------------------------
# wait_for_evaluation callbacks
# ---------------------------------------------------------------------------


class TestWaitForEvaluationCallbacks:
    def test_wait_for_evaluation_with_progress_callback(self, evaluator, sample_flywheel_run_id):
        """Test wait_for_evaluation with progress callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        call_count = 0

        def mock_get_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_status_response(
                "active" if call_count == 1 else "completed",
                {"progress": 50 if call_count == 1 else 100},
            )

        evaluator.nemo_client.evaluation.metric_jobs.get_status.side_effect = mock_get_response

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            result = evaluator.wait_for_evaluation(
                job_id=job_id,
                flywheel_run_id=sample_flywheel_run_id,
                polling_interval=1,
                timeout=10,
                progress_callback=progress_callback,
            )

            assert result["status"] == "completed"
            assert len(progress_updates) == 2
            assert progress_updates[0]["progress"] == 50.0
            assert progress_updates[1]["progress"] == 100.0

    def test_wait_for_evaluation_timeout_with_callback(self, evaluator, sample_flywheel_run_id):
        """Test wait_for_evaluation timeout with progress callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("active", {"progress": 25})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(TimeoutError):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=0.001,
                    progress_callback=progress_callback,
                )

            # Should have progress updates including error
            assert len(progress_updates) >= 1
            # Last update should contain error
            assert "error" in progress_updates[-1]
            assert progress_updates[-1]["progress"] == 0.0

    def test_wait_for_evaluation_error_with_callback(self, evaluator, sample_flywheel_run_id):
        """Test wait_for_evaluation error with progress callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        evaluator.nemo_client.evaluation.metric_jobs.get_status.return_value = (
            _make_status_response("error", {"error": "Test error message"})
        )

        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(Exception, match="Test error message"):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=10,
                    progress_callback=progress_callback,
                )

            # Should have error callback
            assert len(progress_updates) == 1
            assert "error" in progress_updates[0]
            assert progress_updates[0]["progress"] == 0.0

    def test_wait_for_evaluation_cancellation(self, evaluator, sample_flywheel_run_id):
        """Test wait_for_evaluation with cancellation."""
        job_id = "test-job"

        with patch("src.backends.nmp.evaluator.check_cancellation") as mock_check_cancellation:
            mock_check_cancellation.side_effect = Exception("Evaluation cancelled")
            with pytest.raises(Exception, match="Evaluation cancelled"):
                evaluator.wait_for_evaluation(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    polling_interval=1,
                    timeout=10,
                )


# ---------------------------------------------------------------------------
# Templates / config builders — these helpers were removed in 26.03.
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="NMP 26.03 removed Evaluator.get_template; templates are now built "
    "inline via _online_template inside run_evaluation."
)
class TestEvaluatorTemplates:
    def test_get_template_with_all_options(self):
        pass

    def test_get_template_tool_call_true(self):
        pass

    def test_get_template_minimal(self):
        pass


# ---------------------------------------------------------------------------
# run_evaluation
# ---------------------------------------------------------------------------


class TestRunEvaluationErrorHandling:
    def test_run_evaluation_request_failure(self, evaluator):
        """Test run_evaluation with request failure."""
        evaluator.nemo_client.evaluation.metric_jobs.create.side_effect = Exception(
            "Failed to launch evaluation job"
        )

        with pytest.raises(Exception, match="Failed to launch evaluation job"):
            evaluator.run_evaluation(
                dataset_name="test-dataset",
                workload_type=WorkloadClassification.GENERIC,
                target_model="test-model",
                test_file="test.jsonl",
            )

    def test_run_evaluation_tool_calling_judge(self, evaluator):
        """Test run_evaluation with tool calling judge.

        ``metric_jobs.create`` returns an object with a ``.name`` attribute in 26.03
        (the legacy ``.id``). DFW returns ``eval_job.name`` as the job_id.
        """
        mock_eval_job = MagicMock()
        mock_eval_job.name = "test-job-id"

        evaluator.nemo_client.evaluation.metric_jobs.create.return_value = mock_eval_job

        job_id = evaluator.run_evaluation(
            dataset_name="test-dataset",
            workload_type=WorkloadClassification.TOOL_CALLING,
            target_model="test-model",
            test_file="test.jsonl",
            tool_eval_type=ToolEvalType.TOOL_CALLING_JUDGE,
            limit=50,
        )

        assert job_id == "test-job-id"


class TestRunEvaluation:
    @pytest.mark.parametrize(
        "test_params",
        [
            # Test case 1: GENERIC workload
            {
                "workload_type": WorkloadClassification.GENERIC,
                "tool_eval_type": None,
                "dataset_name": "test-dataset",
                "target_model": "meta/llama-3.3-70b-instruct",
                "test_file": "test.jsonl",
                "limit": 75,
                "should_raise_error": False,
            },
            # Test case 2: TOOL_CALLING workload with missing tool_eval_type
            {
                "workload_type": WorkloadClassification.TOOL_CALLING,
                "tool_eval_type": None,
                "dataset_name": "test-dataset",
                "target_model": "meta/llama-3.3-70b-instruct",
                "test_file": "test-tools.jsonl",
                "limit": 50,
                "expected_error": ValueError,
                "expected_error_msg": "tool_eval_type must be provided for tool calling workload",
                "should_raise_error": True,
            },
            # Test case 3: TOOL_CALLING workload with TOOL_CALLING_METRIC
            {
                "workload_type": WorkloadClassification.TOOL_CALLING,
                "tool_eval_type": ToolEvalType.TOOL_CALLING_METRIC,
                "dataset_name": "test-tool-dataset",
                "target_model": "custom/tool-model",
                "test_file": "tools-data.jsonl",
                "limit": 99,
                "should_raise_error": False,
            },
        ],
    )
    def test_run_evaluation_scenarios(self, evaluator_instance, test_params):
        """Verify run_evaluation packages a single metric_job spec correctly.

        In NMP 26.03 each metric_job carries a single ``metric`` (not a tasks
        map). We assert the call shape rather than reconstructing the spec via
        helpers that no longer exist.
        """
        mock_eval_job = MagicMock()
        mock_eval_job.name = "mock-job-id"
        mock_eval_job.status = "created"
        mock_eval_job.created_at = "2024-03-20T10:00:00Z"

        evaluator_instance.nemo_client.evaluation.metric_jobs.create.return_value = mock_eval_job

        if test_params["should_raise_error"]:
            with pytest.raises(
                test_params["expected_error"], match=test_params["expected_error_msg"]
            ):
                evaluator_instance.run_evaluation(
                    dataset_name=test_params["dataset_name"],
                    workload_type=test_params["workload_type"],
                    target_model=test_params["target_model"],
                    test_file=test_params["test_file"],
                    tool_eval_type=test_params["tool_eval_type"],
                    limit=test_params["limit"],
                )
            return

        job_id = evaluator_instance.run_evaluation(
            dataset_name=test_params["dataset_name"],
            workload_type=test_params["workload_type"],
            tool_eval_type=test_params["tool_eval_type"],
            target_model=test_params["target_model"],
            test_file=test_params["test_file"],
            limit=test_params["limit"],
        )

        assert job_id == "mock-job-id"

        # Verify the SDK call shape — metric_jobs.create(workspace=, spec=).
        create_mock = evaluator_instance.nemo_client.evaluation.metric_jobs.create
        create_mock.assert_called_once()
        _, kwargs = create_mock.call_args
        assert kwargs["workspace"] == "test-namespace"
        spec = kwargs["spec"]
        # Each metric_job runs exactly one metric (vs. multi-task in 25.x).
        assert "metric" in spec
        assert "dataset" in spec
        assert "model" in spec
        # NMP 26.03 normalizes the model param into a flat ``ModelParam``
        # ({name, url, format}); the API rejects the legacy ``api_endpoint`` shape.
        # Model name is normalized: ``/`` / ``@`` / ``.`` collapse to ``-`` to satisfy
        # the entity-name regex; the workspace prefix is added.
        expected_name = (
            test_params["target_model"].replace("/", "-").replace("@", "-").replace(".", "-")
        )
        assert spec["model"]["name"].endswith(expected_name)
        assert spec["model"]["format"] == "openai"


@pytest.mark.parametrize(
    "limit",
    [
        75,  # Normal positive limit
        0,  # Zero limit
        None,  # No limit
    ],
)
def test_run_evaluation_limit_propagation(evaluator_instance, mock_response, limit):
    """Test that run_evaluation correctly propagates limit through the spec.

    26.03 puts ``limit`` into ``spec["dataset"]["limit"]`` (and additionally
    ``spec["params"]["limit_samples"]`` when non-None). The legacy ``config``
    helpers are gone, so we assert on the SDK call shape instead of mocking
    helper methods.
    """
    evaluator_instance.nemo_client.evaluation.metric_jobs.create.return_value = mock_response

    evaluator_instance.run_evaluation(
        dataset_name="test-dataset",
        workload_type=WorkloadClassification.GENERIC,
        target_model="test-model",
        test_file="test.jsonl",
        limit=limit,
    )

    create_mock = evaluator_instance.nemo_client.evaluation.metric_jobs.create
    create_mock.assert_called_once()
    _, kwargs = create_mock.call_args
    spec = kwargs["spec"]
    # NMP 26.03: ``spec["dataset"]`` is a bare FilesetRef string (workspace/name);
    # sample subsetting is expressed via ``params.limit_samples``.
    assert isinstance(spec["dataset"], str)
    if limit is None:
        assert "limit_samples" not in spec.get("params", {})
    else:
        assert spec["params"]["limit_samples"] == limit


# ---------------------------------------------------------------------------
# Business logic — most of these helpers were removed in 26.03.
# ---------------------------------------------------------------------------


class TestEvaluatorBusinessLogic:
    """Test class for Evaluator business logic methods."""

    @pytest.mark.skip(
        reason="NMP 26.03 removed Evaluator.get_dpo_judge_metrics; DPO is now a "
        "single inline _llm_judge_dpo_metric inside run_evaluation."
    )
    def test_get_dpo_judge_metrics(self):
        pass

    @pytest.mark.skip(
        reason="NMP 26.03 removed Evaluator._create_dataset_config; datasets are "
        "now built inline as FilesetParam in _dataset_param (fileset URI, "
        "not hf:// URI)."
    )
    def test_create_dataset_config_with_test_file(self):
        pass

    @pytest.mark.skip(reason="NMP 26.03 removed Evaluator._create_dataset_config; see above.")
    def test_create_dataset_config_without_test_file(self):
        pass

    @pytest.mark.skip(
        reason="NMP 26.03 removed Evaluator.get_dpo_judge_config; DPO config is "
        "constructed inline in run_evaluation with a single MetricOnlineJobParam."
    )
    def test_get_dpo_judge_config(self):
        pass

    def test_parse_retriever_evaluation_results_success(self, evaluator):
        """Test parsing retriever evaluation results."""
        mock_results = {
            "tasks": {
                "scidocs-task": {
                    "metrics": {
                        "retriever.recall_5": {"scores": {"recall_5": {"value": 0.85}}},
                        "retriever.ndcg_cut_10": {"scores": {"ndcg_cut_10": {"value": 0.72}}},
                    }
                }
            }
        }

        scores = evaluator.parse_retriever_evaluation_results(mock_results)

        assert "recall_5" in scores
        assert scores["recall_5"] == 0.85
        assert "ndcg_cut_10" in scores
        assert scores["ndcg_cut_10"] == 0.72

    def test_parse_retriever_evaluation_results_empty(self, evaluator):
        """Test parsing empty retriever evaluation results."""
        mock_results = {}

        scores = evaluator.parse_retriever_evaluation_results(mock_results)

        assert scores == {}

    def test_parse_retriever_evaluation_results_malformed(self, evaluator):
        """Test parsing malformed retriever evaluation results."""
        mock_results = {"tasks": {"task1": {"metrics": {}}}}

        scores = evaluator.parse_retriever_evaluation_results(mock_results)

        assert scores == {}

    def test_parse_retriever_evaluation_results_with_scalar_scores(self, evaluator):
        """Test parsing results where scores are scalar values instead of dicts."""
        mock_results = {
            "tasks": {
                "test-task": {
                    "metrics": {
                        "retriever.recall_10": {"scores": {"recall_10": 0.90}}  # Scalar
                    }
                }
            }
        }

        scores = evaluator.parse_retriever_evaluation_results(mock_results)

        assert "recall_10" in scores
        assert scores["recall_10"] == 0.90

    @pytest.mark.skip(
        reason="NMP 26.03 removed Evaluator._create_retriever_target_config; the "
        "retriever_pipeline is now built inline inside run_retriever_evaluation "
        "as part of a single MetricRetrieverJobParam spec."
    )
    def test_create_retriever_target_config(self):
        pass


# ---------------------------------------------------------------------------
# Error handling paths
# ---------------------------------------------------------------------------


class TestEvaluatorErrorHandling:
    """Test error handling scenarios in evaluator methods."""

    def test_get_job_status_exception_handling(self, evaluator):
        """Test _get_job_status properly handles and re-raises exceptions."""
        evaluator.nemo_client.evaluation.metric_jobs.get_status.side_effect = Exception("API Error")

        with pytest.raises(Exception, match="API Error"):
            evaluator._get_job_status("test-job-id")

    def test_run_evaluation_dpo_workload_path(self, evaluator):
        """Test run_evaluation correctly handles DPO workload classification."""
        mock_job = MagicMock()
        mock_job.name = "dpo-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.return_value = mock_job

        job_id = evaluator.run_evaluation(
            workload_type=WorkloadClassification.DPO,
            dataset_name="dpo-dataset",
            target_model="test-model",
            test_file="test.jsonl",
            tool_eval_type=None,
            limit=100,
        )

        assert job_id == "dpo-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.assert_called_once()

    def test_retriever_evaluation_with_default_metrics(self, evaluator):
        """Test run_retriever_evaluation packages a default-metric spec.

        26.03 puts each retriever metric in its own job; the source picks
        ``metrics[0]`` as the primary metric for the single job. We verify the
        chosen primary metric belongs to the documented default set.
        """
        mock_job = MagicMock()
        mock_job.name = "retriever-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.return_value = mock_job

        job_id = evaluator.run_retriever_evaluation(
            model_name="embedding-model",
            namespace="test-namespace",
            dataset_name="test-dataset",
            test_file="test.jsonl",
            top_k=10,
            limit=50,
        )

        assert job_id == "retriever-job-id"

        create_mock = evaluator.nemo_client.evaluation.metric_jobs.create
        _, kwargs = create_mock.call_args
        spec = kwargs["spec"]
        # NMP 26.03 renamed retriever metrics to ``retriever-<name>`` (e.g.
        # ``recall_5`` → ``retriever-recall-5``) and switched to ``MetricRef``
        # strings (``system/<name>``). Source picks metrics[0] as the primary.
        assert spec["metric"] in {
            "system/retriever-recall-5",
            "system/retriever-recall-10",
            "system/retriever-ndcg-cut-5",
            "system/retriever-ndcg-cut-10",
        }
        # Retriever pipeline should be populated. ``top_k`` moved off the pipeline
        # to ``SystemMetricParam`` overrides in 26.03.
        assert "retriever_pipeline" in spec
        assert "embeddings_model" in spec["retriever_pipeline"]

    def test_retriever_evaluation_with_benchmark_instead_of_dataset(self, evaluator):
        """Test run_retriever_evaluation uses built-in benchmark when no dataset."""
        mock_job = MagicMock()
        mock_job.name = "benchmark-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.return_value = mock_job

        job_id = evaluator.run_retriever_evaluation(
            model_name="embedding-model",
            namespace="test-namespace",
            dataset_name=None,
            benchmark="msmarco",
            test_file=None,
            top_k=10,
            metrics=["recall_10", "ndcg_cut_10"],
        )

        assert job_id == "benchmark-job-id"

        create_mock = evaluator.nemo_client.evaluation.metric_jobs.create
        _, kwargs = create_mock.call_args
        spec = kwargs["spec"]
        # Without a dataset_name the source falls through to a built-in BEIR
        # reference shaped as ``{"type": "built-in", "name": <benchmark>}``.
        assert spec["dataset"] == {"type": "built-in", "name": "msmarco"}
        # NMP 26.03: retriever metrics renamed to ``retriever-<name>`` and use
        # ``MetricRef`` strings ``system/<name>``.
        assert spec["metric"] == "system/retriever-recall-10"

    def test_retriever_evaluation_exception_handling(self, evaluator):
        """Test run_retriever_evaluation handles exceptions properly."""
        evaluator.nemo_client.evaluation.metric_jobs.create.side_effect = Exception(
            "Failed to create job"
        )

        with pytest.raises(Exception, match="Failed to create job"):
            evaluator.run_retriever_evaluation(
                model_name="embedding-model",
                namespace="test-namespace",
                dataset_name="test-dataset",
                test_file="test.jsonl",
            )

    def test_parse_retriever_results_with_malformed_data(self, evaluator):
        """Test parse_retriever_evaluation_results handles malformed data gracefully."""
        malformed_results = {"tasks": "not-a-dict"}

        scores = evaluator.parse_retriever_evaluation_results(malformed_results)

        assert scores == {}


# ---------------------------------------------------------------------------
# Retriever + DPO workflows (duplicates of the above but kept for coverage).
# ---------------------------------------------------------------------------


class TestEvaluatorRetrieverAndDPOWorkflows:
    """Test retriever evaluation and DPO workflow functionality."""

    def test_get_job_status_exception(self, evaluator):
        """Test _get_job_status handles exceptions."""
        evaluator.nemo_client.evaluation.metric_jobs.get_status.side_effect = Exception("API Error")

        with pytest.raises(Exception, match="API Error"):
            evaluator._get_job_status("test-job-id")

    def test_run_evaluation_dpo_workload(self, evaluator):
        """Test run_evaluation with DPO workload."""
        mock_job = MagicMock()
        mock_job.name = "dpo-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.return_value = mock_job

        job_id = evaluator.run_evaluation(
            workload_type=WorkloadClassification.DPO,
            dataset_name="dpo-dataset",
            target_model="test-model",
            test_file="test.jsonl",
            tool_eval_type=None,
            limit=100,
        )

        assert job_id == "dpo-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.assert_called_once()

    def test_run_retriever_evaluation_with_default_metrics(self, evaluator):
        """Test run_retriever_evaluation with default metrics."""
        mock_job = MagicMock()
        mock_job.name = "retriever-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.return_value = mock_job

        job_id = evaluator.run_retriever_evaluation(
            model_name="embedding-model",
            namespace="test-namespace",
            dataset_name="test-dataset",
            test_file="test.jsonl",
            top_k=10,
            limit=50,
        )

        assert job_id == "retriever-job-id"

        create_mock = evaluator.nemo_client.evaluation.metric_jobs.create
        _, kwargs = create_mock.call_args
        spec = kwargs["spec"]
        # NMP 26.03: retriever metric is a ``MetricRef`` string with the renamed
        # ``retriever-<name>`` vocabulary.
        assert spec["metric"] in {
            "system/retriever-recall-5",
            "system/retriever-recall-10",
            "system/retriever-ndcg-cut-5",
            "system/retriever-ndcg-cut-10",
        }

    def test_run_retriever_evaluation_with_benchmark(self, evaluator):
        """Test run_retriever_evaluation with benchmark instead of dataset."""
        mock_job = MagicMock()
        mock_job.name = "benchmark-job-id"
        evaluator.nemo_client.evaluation.metric_jobs.create.return_value = mock_job

        job_id = evaluator.run_retriever_evaluation(
            model_name="embedding-model",
            namespace="test-namespace",
            dataset_name=None,
            benchmark="msmarco",
            test_file=None,
            top_k=10,
            metrics=["recall_10", "ndcg_cut_10"],
        )

        assert job_id == "benchmark-job-id"

        create_mock = evaluator.nemo_client.evaluation.metric_jobs.create
        _, kwargs = create_mock.call_args
        spec = kwargs["spec"]
        assert spec["dataset"] == {"type": "built-in", "name": "msmarco"}

    def test_run_retriever_evaluation_exception(self, evaluator):
        """Test run_retriever_evaluation handles exceptions."""
        evaluator.nemo_client.evaluation.metric_jobs.create.side_effect = Exception(
            "Failed to create job"
        )

        with pytest.raises(Exception, match="Failed to create job"):
            evaluator.run_retriever_evaluation(
                model_name="embedding-model",
                namespace="test-namespace",
                dataset_name="test-dataset",
                test_file="test.jsonl",
            )

    def test_parse_retriever_evaluation_results_exception(self, evaluator):
        """Test parse_retriever_evaluation_results handles exceptions."""
        malformed_results = {"tasks": "not-a-dict"}

        scores = evaluator.parse_retriever_evaluation_results(malformed_results)

        assert scores == {}
