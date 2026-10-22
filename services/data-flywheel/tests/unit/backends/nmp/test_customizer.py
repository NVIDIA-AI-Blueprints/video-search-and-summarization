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
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from bson.objectid import ObjectId

from src.backends.nmp.customizer import Customizer
from src.config import CustomizerConfig, LoRAConfig, NIMConfig, TrainingConfig

# Mock time.sleep globally for all tests in this module
pytestmark = [pytest.mark.usefixtures("mock_sleep_globally")]


@pytest.fixture(autouse=True)
def mock_sleep_globally():
    """Automatically mock time.sleep for all tests in this module."""
    with patch("time.sleep"):
        yield


@pytest.fixture
def mock_nemo_client():
    """Fixture to create mock NeMoPlatform client."""
    return Mock()


@pytest.fixture
def customizer(mock_nemo_client):
    """Fixture to create a Customizer instance with mocked dependencies."""
    with (
        patch("src.backends.nmp.customizer.settings") as mock_settings,
        patch("src.backends.nmp.customizer.check_cancellation"),
        patch("src.backends.nmp.customizer.NeMoPlatform", return_value=mock_nemo_client),
    ):
        mock_settings.system_config.nmp_config.nemo_base_url = "http://test-nemo-url"
        mock_settings.system_config.nmp_config.nmp_namespace = "test-namespace"
        mock_settings.system_config.nmp_config.nim_base_url = "http://test-nim-url"
        # In NMP 26.03 the Customizer reads `workspace` from settings, which is a
        # property defaulting to nmp_namespace. We set it explicitly on the Mock so
        # the customizer stores a real string rather than a Mock object.
        mock_settings.system_config.nmp_config.workspace = "test-namespace"
        customizer_instance = Customizer()
        customizer_instance.nemo_client = mock_nemo_client
        # Mock wait_for_target_ready to avoid hanging in tests
        customizer_instance.wait_for_target_ready = Mock(return_value=Mock(status="ready"))
        return customizer_instance


@pytest.fixture
def customizer_config():
    """Fixture to create a CustomizerConfig instance."""
    return CustomizerConfig(
        gpus=1,
        tensor_parallel_size=1,
        data_parallel_size=1,
        use_sequence_parallel=False,
        micro_batch_size=1,
        training_precision="bf16-mixed",
        max_seq_length=4096,
        target="test-model@2.0",
    )


@pytest.fixture
def nim_config(customizer_config):
    """Fixture to create a NIMConfig instance with customizer configs."""
    return NIMConfig(
        model_name="test-model",
        context_length=2048,
        gpus=1,
        pvc_size="10Gi",
        tag="latest",
        registry_base="nvcr.io/nim",
        customization_enabled=True,
        customizer_configs=customizer_config,
    )


@pytest.fixture
def training_config():
    return TrainingConfig(
        training_type="sft",
        finetuning_type="lora",
        epochs=2,
        batch_size=8,
        learning_rate=1e-4,
        lora=LoRAConfig(adapter_dim=32, adapter_dropout=0.1),
    )


@pytest.fixture
def sample_flywheel_run_id():
    """Fixture to provide a valid ObjectId string for tests."""
    return str(ObjectId())


class TestCustomizer:
    def test_init_without_nemo_url(self):
        """Test initialization fails without nemo_base_url."""
        with patch("src.backends.nmp.customizer.settings") as mock_settings:
            mock_settings.system_config.nmp_config.nemo_base_url = None
            with pytest.raises(AssertionError, match="nemo_base_url must be set in config"):
                Customizer()

    def test_start_training_job_success(
        self, customizer, training_config, nim_config, sample_flywheel_run_id
    ):
        mock_customization = Mock()
        mock_customization.name = "job-123"

        customizer.nemo_client.customization.jobs.create.return_value = mock_customization

        with patch.object(
            customizer, "create_customizer_config", return_value="test-namespace/test-model"
        ):
            job_id, model_name = customizer.start_training_job(
                name="test-job",
                base_model="test-namespace/test-model",
                output_model_name="output-model",
                dataset_name="test-dataset",
                training_config=training_config,
                nim_config=nim_config,
                flywheel_run_id=sample_flywheel_run_id,
            )

        # In NMP 26.03 the job is keyed by `name` (returned as job_id) and the
        # customized model is f"{workspace}/{output_model_name}".
        assert job_id == "job-123"
        assert model_name == "test-namespace/output-model"

        # Verify the SDK call
        customizer.nemo_client.customization.jobs.create.assert_called_once()

    def test_start_training_job_failure(
        self, customizer, training_config, nim_config, sample_flywheel_run_id
    ):
        customizer.nemo_client.customization.jobs.create.side_effect = Exception(
            "Failed to start training job"
        )

        with patch.object(
            customizer, "create_customizer_config", return_value="test-namespace/test-model"
        ):
            with pytest.raises(Exception, match="Failed to start training job"):
                customizer.start_training_job(
                    name="test-job",
                    base_model="test-namespace/test-model",
                    output_model_name="output-model",
                    dataset_name="test-dataset",
                    training_config=training_config,
                    nim_config=nim_config,
                    flywheel_run_id=sample_flywheel_run_id,
                )

    def test_get_job_uri(self, customizer):
        job_id = "test-job-123"
        # NMP 26.03 jobs are workspace-scoped under /apis/customization/v2/workspaces/...
        expected_uri = (
            "http://test-nemo-url/apis/customization/v2/workspaces/test-namespace/jobs/test-job-123"
        )
        assert customizer.get_job_uri(job_id) == expected_uri

    def test_get_job_status_success(self, customizer):
        # In 26.03 the SDK returns a Pydantic model exposing `model_dump()`.
        mock_status = Mock()
        mock_status.model_dump.return_value = {
            "status": "active",  # 26.03 vocabulary; customizer translates to "running"
            "steps": [
                {
                    "status": "active",
                    "status_details": {"message": "training"},
                    "tasks": [
                        {
                            "status_details": {
                                "progress_pct": 50,
                                "epoch": 1,
                                "step": 25,
                            }
                        }
                    ],
                }
            ],
        }

        customizer.nemo_client.customization.jobs.get_status.return_value = mock_status

        status = customizer.get_job_status("test-job-123")
        assert status["status"] == "running"  # translated from active
        assert status["percentage_done"] == 50.0
        assert status["epochs_completed"] == 1
        assert status["steps_completed"] == 25

    def test_get_job_status_failure(self, customizer):
        customizer.nemo_client.customization.jobs.get_status.side_effect = Exception(
            "Failed to get job status"
        )

        with pytest.raises(Exception, match="Failed to get job status"):
            customizer.get_job_status("test-job-123")

    def test_get_customized_model_info_success(self, customizer):
        mock_model = Mock()
        mock_model.model_dump.return_value = {"name": "test-model", "status": "ready"}

        customizer.nemo_client.models.retrieve.return_value = mock_model

        info = customizer.get_customized_model_info("test-model")
        assert info == {"name": "test-model", "status": "ready"}

    def test_get_customized_model_info_failure(self, customizer):
        customizer.nemo_client.models.retrieve.side_effect = Exception("Failed to get model info")

        with pytest.raises(Exception, match="Failed to get model info"):
            customizer.get_customized_model_info("test-model")

    def test_wait_for_model_sync_success(self, customizer, sample_flywheel_run_id):
        """Test successful model sync wait."""
        model_name = "test-model"
        mock_models = Mock()
        mock_models.data = [Mock(id=model_name)]

        customizer.nemo_client.inference.models.list.return_value = mock_models

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            result = customizer.wait_for_model_sync(
                customized_model=model_name,
                flywheel_run_id=sample_flywheel_run_id,
                check_interval=1,
                timeout=1,
            )
            assert result["status"] == "synced"
            assert result["model_id"] == model_name

    def test_wait_for_model_sync_timeout(self, customizer, sample_flywheel_run_id):
        """Test model sync wait timeout."""
        model_name = "test-model"
        # Return an iterable empty list directly — the source uses
        # `getattr(models, "data", None) or list(models)`, which falls through
        # to list(models) when data is falsy. Using a real list satisfies both.
        customizer.nemo_client.inference.models.list.return_value = []

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(TimeoutError):
                customizer.wait_for_model_sync(
                    customized_model=model_name,
                    flywheel_run_id=sample_flywheel_run_id,
                    check_interval=1,
                    timeout=0.001,  # Keep tiny timeout to trigger immediately
                )

    def test_wait_for_model_sync_cancellation(self, customizer, sample_flywheel_run_id):
        """Test model sync wait with cancellation."""
        model_name = "test-model"

        # Mock cancellation to raise an exception immediately
        with patch("src.backends.nmp.customizer.check_cancellation") as mock_check_cancellation:
            mock_check_cancellation.side_effect = Exception("Run cancelled")
            with pytest.raises(Exception, match="Run cancelled"):
                customizer.wait_for_model_sync(
                    customized_model=model_name,
                    flywheel_run_id=sample_flywheel_run_id,
                    check_interval=1,
                    timeout=10,
                )

    def test_wait_for_model_sync_request_failure(self, customizer, sample_flywheel_run_id):
        """Test model sync wait with request failure."""
        model_name = "test-model"
        customizer.nemo_client.inference.models.list.side_effect = Exception(
            "Failed to get models list"
        )

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with pytest.raises(Exception, match="Failed to get models list"):
                customizer.wait_for_model_sync(
                    customized_model=model_name,
                    flywheel_run_id=sample_flywheel_run_id,
                    check_interval=1,
                    timeout=10,
                )

    def test_wait_for_customization_success(self, customizer, sample_flywheel_run_id):
        """Test successful customization wait."""
        job_id = "test-job"

        # Mock get_db_manager to prevent actual DB calls
        with (
            patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager,
            patch.object(customizer, "get_job_status") as mock_get_job_status,
        ):
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            mock_get_job_status.return_value = {
                "status": "completed",
                "epochs_completed": 10,
                "steps_completed": 100,
            }
            result = customizer.wait_for_customization(
                job_id=job_id,
                flywheel_run_id=sample_flywheel_run_id,
                check_interval=1,
                timeout=1,
            )
            assert result["status"] == "completed"

    def test_wait_for_customization_success_with_callback(self, customizer, sample_flywheel_run_id):
        """Test successful customization wait with progress callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        # Mock get_db_manager to prevent actual DB calls
        with (
            patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager,
            patch.object(customizer, "get_job_status") as mock_get_job_status,
        ):
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            mock_get_job_status.return_value = {
                "status": "completed",
                "epochs_completed": 10,
                "steps_completed": 100,
            }
            result = customizer.wait_for_customization(
                job_id=job_id,
                flywheel_run_id=sample_flywheel_run_id,
                check_interval=1,
                timeout=1,
                progress_callback=progress_callback,
            )
            assert result["status"] == "completed"
            assert len(progress_updates) == 1
            assert progress_updates[0]["progress"] == 100.0

    def test_wait_for_customization_failure(self, customizer, sample_flywheel_run_id):
        """Test customization wait failure."""
        job_id = "test-job"

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                mock_get_job_status.return_value = {
                    "status": "failed",
                    "status_logs": [{"detail": "Test error"}],
                }
                with pytest.raises(Exception) as exc_info:
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=1,
                    )
                assert "Test error" in str(exc_info.value)

    def test_wait_for_customization_failure_with_callback(self, customizer, sample_flywheel_run_id):
        """Test customization wait failure with progress callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                mock_get_job_status.return_value = {
                    "status": "failed",
                    "status_logs": [{"detail": "Callback test error"}],
                }
                with pytest.raises(Exception) as exc_info:
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=1,
                        progress_callback=progress_callback,
                    )

                assert "Callback test error" in str(exc_info.value)
                assert len(progress_updates) == 1
                assert progress_updates[0]["progress"] == 0.0
                assert "error" in progress_updates[0]

    def test_wait_for_customization_failed_no_detail(self, customizer, sample_flywheel_run_id):
        """Test customization wait failure without error detail."""
        job_id = "test-job"

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                mock_get_job_status.return_value = {
                    "status": "failed",
                    "status_logs": [{"message": "Some message without detail"}],
                }
                with pytest.raises(Exception) as exc_info:
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=1,
                    )

                assert "No error details available" in str(exc_info.value)

    def test_wait_for_customization_timeout(self, customizer, sample_flywheel_run_id):
        """Test customization wait timeout."""
        job_id = "test-job"

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                # Always return running status to trigger timeout
                mock_get_job_status.return_value = {
                    "status": "running",
                    "percentage_done": 50,
                    "epochs_completed": 5,
                    "steps_completed": 50,
                }
                with pytest.raises(TimeoutError):
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=0.001,  # Very short timeout to trigger immediately
                    )

    def test_wait_for_customization_timeout_with_callback(self, customizer, sample_flywheel_run_id):
        """Test customization wait timeout with progress callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                mock_get_job_status.return_value = {
                    "status": "running",
                    "percentage_done": 75,
                    "epochs_completed": 7,
                    "steps_completed": 75,
                }
                with pytest.raises(TimeoutError):
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=0.001,  # Very short timeout to trigger immediately
                        progress_callback=progress_callback,
                    )
                # Should have at least one progress update before timeout
                assert (
                    len(progress_updates) >= 2
                )  # At least one running update + one timeout update
                # Check that we have a progress update with 75% (not the timeout one)
                progress_values = [update["progress"] for update in progress_updates]
                assert 75.0 in progress_values
                # The last update should be the timeout callback with 0.0 progress
                assert progress_updates[-1]["progress"] == 0.0
                assert "error" in progress_updates[-1]

    def test_wait_for_customization_not_enough_resources(self, customizer, sample_flywheel_run_id):
        """Test customization wait with not enough resources."""
        job_id = "test-job"

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                mock_get_job_status.return_value = {
                    "status": "running",
                    "status_logs": [{"message": "NotEnoughResources"}],
                }
                with pytest.raises(Exception) as exc_info:
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=1,
                    )

                assert "insufficient resources" in str(exc_info.value)

    def test_wait_for_customization_not_enough_resources_with_callback(
        self, customizer, sample_flywheel_run_id
    ):
        """Test customization wait with not enough resources and callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                # Mock job status to be running initially, then check for NotEnoughResources after timeout
                mock_get_job_status.return_value = {
                    "status": "running",
                    "status_logs": [{"message": "NotEnoughResources"}],
                }
                with pytest.raises(Exception) as exc_info:
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=0.001,
                        progress_callback=progress_callback,
                    )

                # Should get the NotEnoughResources error after timeout, not the generic timeout error
                assert "insufficient resources" in str(exc_info.value)
                assert progress_updates[-1]["progress"] == 0.0
                assert "error" in progress_updates[-1]

    def test_wait_for_customization_running_with_progress_update(
        self, customizer, sample_flywheel_run_id
    ):
        """Test customization wait running status with progress callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        call_count = 0

        def mock_get_job_status_side_effect(job_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "status": "running",
                    "percentage_done": 30,
                    "epochs_completed": 3,
                    "steps_completed": 30,
                }
            else:
                return {
                    "status": "completed",
                    "epochs_completed": 10,
                    "steps_completed": 100,
                }

        # Mock get_db_manager to prevent actual DB calls
        with (
            patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager,
            patch.object(customizer, "get_job_status", side_effect=mock_get_job_status_side_effect),
        ):
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            result = customizer.wait_for_customization(
                job_id=job_id,
                flywheel_run_id=sample_flywheel_run_id,
                check_interval=1,
                timeout=10,
                progress_callback=progress_callback,
            )
            assert result["status"] == "completed"
            # Should have progress updates for running and completed states
            assert len(progress_updates) >= 2

    def test_wait_for_customization_pending_status(self, customizer, sample_flywheel_run_id):
        """Test customization wait with pending status."""
        job_id = "test-job"

        call_count = 0

        def mock_get_job_status_side_effect(job_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"status": "pending"}
            else:
                return {"status": "completed", "epochs_completed": 10, "steps_completed": 100}

        # Mock get_db_manager to prevent actual DB calls
        with (
            patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager,
            patch.object(customizer, "get_job_status", side_effect=mock_get_job_status_side_effect),
        ):
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            result = customizer.wait_for_customization(
                job_id=job_id,
                flywheel_run_id=sample_flywheel_run_id,
                check_interval=1,
                timeout=10,
            )
            assert result["status"] == "completed"

    def test_wait_for_customization_created_status(self, customizer, sample_flywheel_run_id):
        """Test customization wait with created status."""
        job_id = "test-job"

        call_count = 0

        def mock_get_job_status_side_effect(job_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"status": "created"}
            else:
                return {"status": "completed", "epochs_completed": 10, "steps_completed": 100}

        # Mock get_db_manager to prevent actual DB calls
        with (
            patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager,
            patch.object(customizer, "get_job_status", side_effect=mock_get_job_status_side_effect),
        ):
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            result = customizer.wait_for_customization(
                job_id=job_id,
                flywheel_run_id=sample_flywheel_run_id,
                check_interval=1,
                timeout=10,
            )
            assert result["status"] == "completed"

    def test_wait_for_customization_unknown_status(self, customizer, sample_flywheel_run_id):
        """Test customization wait with unknown status."""
        job_id = "test-job"

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                mock_get_job_status.return_value = {"status": "unknown_status"}
                with pytest.raises(Exception) as exc_info:
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=1,
                    )

                assert "Unknown job status 'unknown_status'" in str(exc_info.value)

    def test_wait_for_customization_unknown_status_with_callback(
        self, customizer, sample_flywheel_run_id
    ):
        """Test customization wait with unknown status and callback."""
        job_id = "test-job"
        progress_updates = []

        def progress_callback(progress_data):
            progress_updates.append(progress_data)

        # Mock get_db_manager to prevent actual DB calls
        with patch("src.lib.flywheel.cancellation.get_db_manager") as mock_get_db_manager:
            mock_db_manager = mock_get_db_manager.return_value
            mock_db_manager.is_flywheel_run_cancelled.return_value = False
            with patch.object(customizer, "get_job_status") as mock_get_job_status:
                mock_get_job_status.return_value = {"status": "weird_status"}
                with pytest.raises(Exception) as exc_info:
                    customizer.wait_for_customization(
                        job_id=job_id,
                        flywheel_run_id=sample_flywheel_run_id,
                        check_interval=1,
                        timeout=1,
                        progress_callback=progress_callback,
                    )

                assert "Unknown job status 'weird_status'" in str(exc_info.value)
                assert len(progress_updates) == 1
                assert progress_updates[0]["progress"] == 0.0
                assert "error" in progress_updates[0]

    def test_wait_for_customization_cancellation(self, customizer, sample_flywheel_run_id):
        """Test customization wait with cancellation."""
        job_id = "test-job"

        # Mock cancellation to raise an exception immediately
        with patch("src.backends.nmp.customizer.check_cancellation") as mock_check_cancellation:
            mock_check_cancellation.side_effect = Exception("Customization cancelled")
            with pytest.raises(Exception, match="Customization cancelled"):
                customizer.wait_for_customization(
                    job_id=job_id,
                    flywheel_run_id=sample_flywheel_run_id,
                    check_interval=1,
                    timeout=10,
                )

    def test_delete_customized_model_success(self, customizer):
        """Test successful model deletion."""
        model_name = "test-model"

        # Test with just model name (no namespace) — _parse_model_path defaults
        # to self.workspace ("test-namespace").
        with patch.object(
            customizer, "get_customized_model_info", return_value={"model": "exists"}
        ):
            customizer.delete_customized_model(model_name)

            customizer.nemo_client.models.delete.assert_called_once_with(
                name=model_name, workspace="test-namespace"
            )

    def test_delete_customized_model_not_found(self, customizer):
        """Test model deletion when model not found."""
        model_name = "test-model"

        with patch.object(
            customizer, "get_customized_model_info", side_effect=Exception("Model not found")
        ):
            with pytest.raises(Exception) as exc_info:
                customizer.delete_customized_model(model_name)
            assert "Model not found" in str(exc_info.value)

    def test_delete_customized_model_deletion_failure(self, customizer):
        """Test model deletion failure."""
        model_name = "test-model"

        with patch.object(
            customizer, "get_customized_model_info", return_value={"model": "exists"}
        ):
            customizer.nemo_client.models.delete.side_effect = Exception("Failed to delete model")

            with pytest.raises(Exception) as exc_info:
                customizer.delete_customized_model(model_name)
            assert "Failed to delete model" in str(exc_info.value)

    def test_cancel_job_success(self, customizer):
        """Test successful job cancellation."""
        job_id = "test-job-123"

        customizer.cancel_job(job_id)

        # NMP 26.03 cancel takes (name=, workspace=)
        customizer.nemo_client.customization.jobs.cancel.assert_called_once_with(
            name=job_id, workspace="test-namespace"
        )

    def test_cancel_job_failure(self, customizer):
        """Test job cancellation failure."""
        job_id = "test-job-789"

        customizer.nemo_client.customization.jobs.cancel.side_effect = Exception(
            "Failed to cancel job"
        )

        with pytest.raises(Exception) as exc_info:
            customizer.cancel_job(job_id)
        assert "Failed to cancel job" in str(exc_info.value)

    # Tests for generate_config_name method
    def test_generate_config_name_valid(self):
        """Test generate_config_name with valid base model format."""
        base_model = "test-namespace/test-model"
        config_name = NIMConfig.generate_config_name(base_model)
        assert config_name == "test-model@v1.0.0+dfw"

    def test_generate_config_name_invalid_single_part(self):
        """Test generate_config_name with invalid single part format."""
        base_model = "test-model"
        with pytest.raises(ValueError, match="Invalid base model format"):
            NIMConfig.generate_config_name(base_model)

    def test_generate_config_name_invalid_three_parts(self):
        """Test generate_config_name with invalid three part format."""
        base_model = "a/b/c"
        with pytest.raises(ValueError, match="Invalid base model format"):
            NIMConfig.generate_config_name(base_model)

    def test_generate_config_name_empty_string(self):
        """Test generate_config_name with empty string."""
        base_model = ""
        with pytest.raises(ValueError, match="Invalid base model format"):
            NIMConfig.generate_config_name(base_model)

    def test_generate_config_name_none(self):
        """Test generate_config_name with None."""
        base_model = None
        with pytest.raises(ValueError, match="Invalid base model format"):
            NIMConfig.generate_config_name(base_model)

    def test_generate_config_name_special_characters(self):
        """Test generate_config_name with special characters in model name."""
        base_model = "test-namespace/model-name_123"
        config_name = NIMConfig.generate_config_name(base_model)
        assert config_name == "model-name_123@v1.0.0+dfw"

    # ------------------------------------------------------------------
    # Tests for create_customizer_config method
    #
    # In NMP 26.03 customization configs were REMOVED as a separate entity —
    # training params are inlined into the job spec, and create_customizer_config
    # is now a thin shim that just resolves the model entity (formerly Target)
    # path. The following tests exercise the model-entity-resolution behaviour
    # since the old config-object lifecycle no longer exists.
    # ------------------------------------------------------------------
    def test_create_customizer_config_success(
        self, customizer, customizer_config, training_config, sample_flywheel_run_id
    ):
        """create_customizer_config now returns the resolved model entity path."""
        base_model = "test-namespace/test-model"

        # Mock _manage_target to return the expected target path.
        with patch.object(customizer, "_manage_target", return_value="test-namespace/test-model"):
            target_path = customizer.create_customizer_config(
                base_model, customizer_config, training_config, sample_flywheel_run_id
            )

        assert target_path == "test-namespace/test-model"

    @pytest.mark.skip(
        reason=(
            "Customization configs were REMOVED as a separate entity in NMP 26.03; "
            "there is no longer an idempotent create-or-409-then-delete path to test."
        )
    )
    def test_create_customizer_config_already_exists(self):
        pass

    def test_create_customizer_config_invalid_base_model(
        self, customizer, customizer_config, training_config, sample_flywheel_run_id
    ):
        """create_customizer_config propagates invalid target path errors."""
        # In NMP 26.03 base_model is no longer parsed by create_customizer_config
        # itself; the target path comes from customizer_config.target. Drive the
        # error by giving the target an invalid form.
        customizer_config.target = "invalid/"  # parse_model_path requires non-empty parts

        with pytest.raises(ValueError, match="Invalid model path format"):
            customizer.create_customizer_config(
                "test-namespace/test-model",
                customizer_config,
                training_config,
                sample_flywheel_run_id,
            )

    @pytest.mark.skip(
        reason="No separate config object exists in NMP 26.03; configs.create was removed."
    )
    def test_create_customizer_config_server_error(self):
        pass

    @pytest.mark.skip(
        reason="No separate config object exists in NMP 26.03; configs.create payload no longer applies."
    )
    def test_create_customizer_config_payload_structure(self):
        pass

    @pytest.mark.skip(
        reason="pod_spec is no longer plumbed via configs.create in NMP 26.03 (no config object)."
    )
    def test_create_customizer_config_with_pod_spec(self):
        pass

    @pytest.mark.skip(
        reason="No separate config object exists in NMP 26.03; behaviour without pod_spec is moot."
    )
    def test_create_customizer_config_without_pod_spec(self):
        pass

    # Enhanced tests for start_training_job method
    def test_start_training_job_no_customizer_config(self, customizer, training_config):
        """Test that NIMConfig validation prevents creating configs with customization enabled but no customizer_configs."""
        # Test that Pydantic validation catches the missing customizer_configs
        with pytest.raises(
            ValueError,
            match="customizer_configs is required when customization_enabled is set to True",
        ):
            NIMConfig(
                model_name="test-model",
                context_length=2048,
                gpus=1,
                pvc_size="10Gi",
                tag="latest",
                registry_base="nvcr.io/nim",
                customization_enabled=True,
                customizer_configs=None,  # No customizer configs
            )

    def test_start_training_job_invalid_base_model(
        self, customizer, training_config, nim_config, sample_flywheel_run_id
    ):
        """Test start_training_job with invalid base model format."""
        mock_customization = Mock()
        mock_customization.name = "job-123"

        customizer.nemo_client.customization.jobs.create.return_value = mock_customization

        with patch.object(
            customizer,
            "create_customizer_config",
            side_effect=ValueError("Invalid base model format"),
        ):
            with pytest.raises(ValueError, match="Invalid base model format"):
                customizer.start_training_job(
                    name="test-job",
                    base_model="invalid-format",
                    output_model_name="output-model",
                    dataset_name="test-dataset",
                    training_config=training_config,
                    nim_config=nim_config,
                    flywheel_run_id=sample_flywheel_run_id,
                )

    def test_start_training_job_verify_config_creation(
        self, customizer, training_config, nim_config, sample_flywheel_run_id
    ):
        """Test that start_training_job calls create_customizer_config correctly."""
        mock_customization = Mock()
        mock_customization.name = "job-123"

        customizer.nemo_client.customization.jobs.create.return_value = mock_customization

        with patch.object(
            customizer, "create_customizer_config", return_value="test-namespace/test-model"
        ) as mock_create_config:
            customizer.start_training_job(
                name="test-job",
                base_model="test-namespace/test-model",
                output_model_name="output-model",
                dataset_name="test-dataset",
                training_config=training_config,
                nim_config=nim_config,
                flywheel_run_id=sample_flywheel_run_id,
            )

            # Verify create_customizer_config was called with correct parameters
            mock_create_config.assert_called_once_with(
                "test-namespace/test-model",
                nim_config.customizer_configs,
                training_config,
                sample_flywheel_run_id,
            )

    def test_start_training_job_verify_training_params(
        self, customizer, training_config, nim_config, sample_flywheel_run_id
    ):
        """Verify start_training_job sends the correct NMP 26.03 spec."""
        mock_customization = Mock()
        mock_customization.name = "job-123"

        customizer.nemo_client.customization.jobs.create.return_value = mock_customization

        with patch.object(
            customizer, "create_customizer_config", return_value="test-namespace/test-model"
        ):
            customizer.start_training_job(
                name="test-job",
                base_model="test-namespace/test-model",
                output_model_name="output-model",
                dataset_name="test-dataset",
                training_config=training_config,
                nim_config=nim_config,
                flywheel_run_id=sample_flywheel_run_id,
            )

            # Verify the training job request was made
            customizer.nemo_client.customization.jobs.create.assert_called_once()
            call_kwargs = customizer.nemo_client.customization.jobs.create.call_args.kwargs

            # NMP 26.03 signature: name=, workspace=, spec={...}
            assert call_kwargs["name"] == "test-job"
            assert call_kwargs["workspace"] == "test-namespace"

            spec = call_kwargs["spec"]
            assert spec["model"] == "test-namespace/test-model"
            assert spec["dataset"] == "fileset://test-namespace/test-dataset"
            assert spec["output"] == {"name": "output-model"}

            # Verify inlined training params
            training = spec["training"]
            assert training["type"] == training_config.training_type
            assert training["epochs"] == training_config.epochs
            assert training["batch_size"] == training_config.batch_size
            assert training["learning_rate"] == training_config.learning_rate
            # NMP 26.03 narrowed precision vocabulary — ``bf16-mixed`` is normalized to ``bf16``.
            assert training["precision"] == "bf16"
            assert training["max_seq_length"] == nim_config.customizer_configs.max_seq_length

            # Parallelism is now a nested block
            parallelism = training["parallelism"]
            assert parallelism["num_gpus_per_node"] == nim_config.customizer_configs.gpus
            assert parallelism["tensor_parallel_size"] == training_config.tensor_parallel_size
            assert parallelism["data_parallel_size"] == training_config.data_parallel_size

            # PEFT block carries LoRA params now
            assert training["peft"]["type"] == "lora"
            assert training["peft"]["rank"] == training_config.lora.adapter_dim
            assert training["peft"]["dropout"] == training_config.lora.adapter_dropout

    def test_delete_customization_config_success(self, customizer):
        """delete_customization_config is a no-op in NMP 26.03 — verify it doesn't error."""
        # The method should log and return without raising or calling the SDK.
        customizer.delete_customization_config("test-config")

    def test_delete_customization_config_failure(self, customizer):
        """delete_customization_config is a no-op — it never raises in 26.03."""
        # Even though we set up the SDK to error, the no-op method should not touch it.
        customizer.nemo_client.customization.configs.delete.side_effect = Exception(
            "Failed to delete customization config"
        )

        # Should NOT raise — the method is a no-op in 26.03.
        customizer.delete_customization_config("test-config")


class TestCustomizerBusinessLogic:
    """Test class for Customizer business logic methods.

    In NMP 26.03 the spec layout changed: there's no separate
    _build_training_hyperparameters/ _get_dataset_parameters helpers anymore;
    the equivalent logic lives in _build_training_spec. The renamed tests below
    exercise that helper directly.
    """

    def test_build_training_hyperparameters_basic(
        self, customizer, training_config, customizer_config
    ):
        """Verify _build_training_spec emits the correct top-level training block."""
        result = customizer._build_training_spec(training_config, customizer_config)

        assert result["type"] == "sft"
        assert result["epochs"] == 2
        assert result["batch_size"] == 8
        assert result["learning_rate"] == 1e-4
        # LoRA finetuning => peft block
        assert "peft" in result
        assert result["peft"]["type"] == "lora"
        assert result["peft"]["rank"] == 32

    def test_build_training_hyperparameters_with_optional_params(
        self, customizer, customizer_config
    ):
        """Verify optional training params are placed correctly."""
        training_config = TrainingConfig(
            training_type="sft",
            finetuning_type="full",
            epochs=5,
            batch_size=16,
            learning_rate=2e-4,
            warmup_steps=100,
            weight_decay=0.01,
            min_learning_rate=1e-6,
        )

        result = customizer._build_training_spec(training_config, customizer_config)

        assert result["warmup_steps"] == 100
        assert result["weight_decay"] == 0.01
        assert result["min_learning_rate"] == 1e-6
        # No LoRA when finetuning_type=full
        assert "peft" not in result

    def test_build_training_hyperparameters_with_dpo(self, customizer, customizer_config):
        """For DPO training, DPO fields live directly on the training dict in 26.03."""
        from src.config import DPOConfig

        training_config = TrainingConfig(
            training_type="dpo",
            finetuning_type="lora",
            epochs=3,
            batch_size=8,
            learning_rate=1e-4,
            lora=LoRAConfig(adapter_dim=16, adapter_dropout=0.05, alpha=16),
            dpo=DPOConfig(
                ref_policy_kl_penalty=0.1,
                preference_loss_weight=1.0,
                preference_average_log_probs=False,
                sft_loss_weight=0.5,
                sft_average_log_probs=True,
            ),
        )

        result = customizer._build_training_spec(training_config, customizer_config)

        assert result["type"] == "dpo"
        # NMP 26.03: DPO fields are TOP-LEVEL on the training dict, not nested under "dpo".
        assert result["ref_policy_kl_penalty"] == 0.1
        assert result["preference_loss_weight"] == 1.0
        assert result["preference_average_log_probs"] is False
        assert result["sft_loss_weight"] == 0.5
        assert result["sft_average_log_probs"] is True

    def test_build_training_hyperparameters_lora_merged(self, customizer, customizer_config):
        """lora_merged finetuning still produces a peft block."""
        training_config = TrainingConfig(
            training_type="sft",
            finetuning_type="lora_merged",
            epochs=2,
            batch_size=8,
            learning_rate=1e-4,
            lora=LoRAConfig(adapter_dim=64, adapter_dropout=0.1, alpha=32),
        )

        result = customizer._build_training_spec(training_config, customizer_config)

        assert "peft" in result
        assert result["peft"]["type"] == "lora"
        assert result["peft"]["rank"] == 64
        assert result["peft"]["alpha"] == 32

    def test_get_dataset_parameters_with_params(
        self, customizer, training_config, nim_config, sample_flywheel_run_id
    ):
        """dataset_parameters now lives at spec[top]; verify it is set when provided."""
        from src.config import DatasetParametersConfig

        training_config.dataset_parameters = DatasetParametersConfig(
            num_hard_negatives=5, in_batch_negatives=True
        )

        mock_customization = Mock()
        mock_customization.name = "job-123"
        customizer.nemo_client.customization.jobs.create.return_value = mock_customization

        with patch.object(
            customizer, "create_customizer_config", return_value="test-namespace/test-model"
        ):
            customizer.start_training_job(
                name="test-job",
                base_model="test-namespace/test-model",
                output_model_name="output-model",
                dataset_name="test-dataset",
                training_config=training_config,
                nim_config=nim_config,
                flywheel_run_id=sample_flywheel_run_id,
            )

        call_kwargs = customizer.nemo_client.customization.jobs.create.call_args.kwargs
        assert call_kwargs["spec"]["dataset_parameters"] == {
            "num_hard_negatives": 5,
            "in_batch_negatives": True,
        }

    def test_get_dataset_parameters_none(
        self, customizer, training_config, nim_config, sample_flywheel_run_id
    ):
        """When dataset_parameters is not set, it should be absent from spec."""
        # Default fixture has no dataset_parameters set
        training_config.dataset_parameters = None

        mock_customization = Mock()
        mock_customization.name = "job-123"
        customizer.nemo_client.customization.jobs.create.return_value = mock_customization

        with patch.object(
            customizer, "create_customizer_config", return_value="test-namespace/test-model"
        ):
            customizer.start_training_job(
                name="test-job",
                base_model="test-namespace/test-model",
                output_model_name="output-model",
                dataset_name="test-dataset",
                training_config=training_config,
                nim_config=nim_config,
                flywheel_run_id=sample_flywheel_run_id,
            )

        call_kwargs = customizer.nemo_client.customization.jobs.create.call_args.kwargs
        assert "dataset_parameters" not in call_kwargs["spec"]

    def test_parse_model_path_with_namespace(self, customizer):
        """Test parsing model path with namespace."""
        namespace, model_name = customizer._parse_model_path("test-ns/test-model")

        assert namespace == "test-ns"
        assert model_name == "test-model"

    def test_parse_model_path_without_namespace(self, customizer):
        """Test parsing model path without namespace (uses default workspace)."""
        namespace, model_name = customizer._parse_model_path("test-model")

        # 26.03: bare names default to self.workspace, which the fixture sets to "test-namespace"
        assert namespace == "test-namespace"
        assert model_name == "test-model"

    def test_parse_model_path_empty_namespace(self, customizer):
        """Test parsing model path with empty namespace."""
        with pytest.raises(ValueError, match="Invalid model path format"):
            customizer._parse_model_path("/model-name")

    def test_parse_model_path_empty_model(self, customizer):
        """Test parsing model path with empty model name."""
        with pytest.raises(ValueError, match="Invalid model path format"):
            customizer._parse_model_path("namespace/")

    def test_get_target_info_success(self, customizer):
        """Test getting target (model entity) info successfully via client.models.retrieve."""
        # Build a fake entity exposing the attributes the customizer reads.
        spec_obj = Mock()
        spec_obj.model_dump.return_value = {
            "base_model": "meta/llama-3-8b",
            "model_uri": "hf://m",
            "num_parameters": 8000000000,
            "precision": "fp8",
            "tokenizer": {"type": "custom"},
        }
        mock_entity = SimpleNamespace(
            id="entity-id-123",
            name="target",
            workspace="test-ns",
            description="desc",
            spec=spec_obj,
        )

        customizer.nemo_client.models.retrieve.return_value = mock_entity

        result = customizer.get_target_info("test-ns/target")

        assert result is not None
        assert result.status == "ready"  # populated spec => ready
        assert result.enabled is True  # model entities are always enabled
        customizer.nemo_client.models.retrieve.assert_called_once_with(
            name="target", workspace="test-ns"
        )

    def test_get_target_info_not_found(self, customizer):
        """get_target_info should return None on any retrieve failure."""
        customizer.nemo_client.models.retrieve.side_effect = Exception("Model entity not found")

        result = customizer.get_target_info("test-ns/target")

        assert result is None


class TestCustomizerTargetManagement:
    """Test class for target management methods (now backed by client.models.*)."""

    def test_create_target_with_minimal_params(self, customizer):
        """Test target creation with minimal required parameters via models.create."""
        from src.config import CustomizerTarget

        target_params = CustomizerTarget(
            name="test-target@v1",
            base_model="meta/llama-3-8b",
        )

        mock_entity = Mock()
        mock_entity.name = "test-target@v1"

        customizer.nemo_client.models.create.return_value = mock_entity

        customizer.create_target(
            namespace="test-ns",
            target_name="test-target@v1",
            params=target_params,
        )

        create_call = customizer.nemo_client.models.create
        create_call.assert_called_once()
        call_args = create_call.call_args.kwargs

        assert call_args["workspace"] == "test-ns"
        # NMP entity names must match ``^[a-z](?!.*--)[a-z0-9\-@.+_]{1,62}(?<!-)$`` —
        # ``@`` collapses to ``-`` in the sanitized fileset/entity name.
        assert call_args["name"] == "test-target-v1"
        # When model_uri is unset, fileset falls back to f"{namespace}/{base_model}".
        assert call_args["fileset"] == "test-ns/meta/llama-3-8b"

    def test_create_target_failure(self, customizer):
        """Test target creation failure surfaces wrapped exception."""
        from src.config import CustomizerTarget

        target_params = CustomizerTarget(
            name="test-target@v1",
            base_model="meta/llama-3-8b",
        )

        customizer.nemo_client.models.create.side_effect = Exception("Failed to create target")

        with pytest.raises(Exception, match="Failed to create model entity"):
            customizer.create_target(
                namespace="test-ns",
                target_name="test-target@v1",
                params=target_params,
            )

    def test_enable_target_success(self, customizer):
        """enable_target is a no-op in NMP 26.03 — verify it doesn't error."""
        # Model entities have no enable/disable flag — the method just logs.
        customizer.enable_target("test-ns", "test-target")

        # Verify no SDK call was made.
        customizer.nemo_client.models.update.assert_not_called()
        customizer.nemo_client.customization.targets.update.assert_not_called()

    def test_enable_target_failure(self, customizer):
        """enable_target is a no-op — even if an SDK side-effect is set, it doesn't fire."""
        # The no-op method should never raise.
        customizer.enable_target("test-ns", "test-target")

    def test_manage_target_with_string_not_found(self, customizer, sample_flywheel_run_id):
        """_manage_target with a string target that doesn't exist raises ValueError."""
        target = "test-namespace/non-existent-target"

        # NMP 26.03 _manage_target falls back to a fanout search across workspace + system
        # via ``client.models.retrieve``. Make every candidate miss so the fallback fails too.
        customizer.nemo_client.models.retrieve.side_effect = Exception("Model entity not found")

        with patch.object(customizer, "get_target_info", return_value=None):
            with pytest.raises(ValueError, match="Model entity .* not found"):
                customizer._manage_target(target, sample_flywheel_run_id)

    def test_manage_target_with_none(self, customizer, sample_flywheel_run_id):
        """Test _manage_target with None target."""
        with pytest.raises(ValueError, match="No target provided"):
            customizer._manage_target(None, sample_flywheel_run_id)

    def test_manage_target_with_target_object_creates_new(self, customizer, sample_flywheel_run_id):
        """Test _manage_target creates new target when it doesn't exist."""
        from src.config import CustomizerTarget

        target_obj = CustomizerTarget(
            name="test-namespace/new-target@v1",
            base_model="meta/llama-3-8b",
        )

        # NMP 26.03 fallback search must miss as well so we reach the create branch.
        customizer.nemo_client.models.retrieve.side_effect = Exception("Model entity not found")

        # Mock target doesn't exist
        with (
            patch.object(customizer, "get_target_info", return_value=None),
            patch.object(customizer, "create_target") as mock_create,
            patch.object(customizer, "wait_for_target_ready"),
        ):
            result = customizer._manage_target(target_obj, sample_flywheel_run_id)

            mock_create.assert_called_once()
            # ``create_target`` produces an entity at ``{namespace}/{sanitized_name}``
            # — ``@`` collapses to ``-`` to satisfy the NMP entity-name regex.
            assert result == "test-namespace/new-target-v1"

    def test_manage_target_enables_disabled_target(self, customizer, sample_flywheel_run_id):
        """Existing model entity should be returned as-is (enable is a no-op in 26.03)."""
        target = "test-namespace/disabled-target"

        mock_target_info = Mock()
        mock_target_info.status = "ready"
        mock_target_info.enabled = False  # ignored — model entities have no enable flag

        with (
            patch.object(customizer, "get_target_info", return_value=mock_target_info),
            patch.object(customizer, "wait_for_target_ready"),
        ):
            result = customizer._manage_target(target, sample_flywheel_run_id)

            assert result == target

    def test_wait_for_target_ready_success(self, customizer, sample_flywheel_run_id):
        """Test wait_for_target_ready with successful ready state."""
        target = "test-namespace/test-target"
        mock_target_info = Mock()
        mock_target_info.status = "ready"

        # The fixture already swaps wait_for_target_ready with a Mock — drop that
        # so we exercise the real method here.
        del customizer.wait_for_target_ready

        with (
            patch.object(customizer, "get_target_info", return_value=mock_target_info),
            patch("src.backends.nmp.customizer.check_cancellation"),
        ):
            result = customizer.wait_for_target_ready(
                target, sample_flywheel_run_id, timeout=10, check_interval=1
            )

            assert result.status == "ready"

    def test_create_target_missing_base_model(self, customizer, sample_flywheel_run_id):
        """Test create_target with missing base_model raises ValueError."""
        from src.config import CustomizerTarget

        # The validation happens in the Customizer method, not Pydantic
        target_params = CustomizerTarget(
            name="test-target@v1",
            base_model="meta/llama-3-8b",  # Must provide base_model for Pydantic
        )

        # Now test the validation inside create_target
        target_params.base_model = None  # Set to None after creation

        with pytest.raises(Exception, match="base_model is required"):
            customizer.create_target(
                namespace="test-ns",
                target_name="test-target@v1",
                params=target_params,
            )

    def test_create_target_with_all_optional_params(self, customizer, sample_flywheel_run_id):
        """Test target creation with all optional parameters maps onto models.create()."""
        from src.config import CustomizerTarget

        target_params = CustomizerTarget(
            name="test-target@v1",
            base_model="meta/llama-3-8b",
            model_uri="hf://model/path",
            hf_endpoint="https://hf.endpoint",
            description="Custom description",
        )

        mock_entity = Mock()
        mock_entity.name = "test-target@v1"

        customizer.nemo_client.models.create.return_value = mock_entity

        customizer.create_target(
            namespace="test-ns",
            target_name="test-target@v1",
            params=target_params,
        )

        create_call = customizer.nemo_client.models.create
        create_call.assert_called_once()
        call_args = create_call.call_args.kwargs

        # NMP 26.03: ``hf://`` model_uri provisions a workspace fileset and
        # ``models.create`` references that fileset, not the raw HF URI.
        assert call_args["fileset"] == "test-ns/test-target-v1"
        assert call_args["description"] == "Custom description"

    def test_build_training_hyperparameters_with_pipeline_parallel(
        self, customizer, sample_flywheel_run_id
    ):
        """pipeline_parallel_size now goes under spec.training.parallelism."""
        training_config = TrainingConfig(
            training_type="sft",
            finetuning_type="full",
            epochs=2,
            batch_size=8,
            learning_rate=1e-4,
            pipeline_parallel_size=2,
        )

        customizer_config = CustomizerConfig(
            gpus=4,
            tensor_parallel_size=2,
            data_parallel_size=1,
            use_sequence_parallel=False,
            micro_batch_size=2,
            training_precision="bf16-mixed",
            max_seq_length=4096,
            target="test-model@2.0",
            num_nodes=2,
        )

        result = customizer._build_training_spec(training_config, customizer_config)

        assert result["parallelism"]["pipeline_parallel_size"] == 2
        assert result["parallelism"]["num_nodes"] == 2

    def test_start_training_job_no_customizer_config_in_nim_config(
        self, customizer, training_config, sample_flywheel_run_id
    ):
        """Test start_training_job when customizer_configs is None."""
        nim_config = NIMConfig(
            model_name="test-model",
            context_length=2048,
            gpus=1,
            pvc_size="10Gi",
            tag="latest",
            registry_base="nvcr.io/nim",
            customization_enabled=False,
            customizer_configs=None,
        )

        with pytest.raises(ValueError, match="No customizer config found"):
            customizer.start_training_job(
                name="test-job",
                base_model="test-namespace/test-model",
                output_model_name="output-model",
                dataset_name="test-dataset",
                training_config=training_config,
                nim_config=nim_config,
                flywheel_run_id=sample_flywheel_run_id,
            )

    @pytest.mark.skip(
        reason=(
            "Customization configs were removed in NMP 26.03; there is no 422 path "
            "through configs.create anymore."
        )
    )
    def test_create_customizer_config_422_error(self):
        pass


class TestCustomizerConfigAPIErrorHandling:
    """Customization config error paths are obsolete in NMP 26.03."""

    @pytest.mark.skip(
        reason=(
            "Customization configs were removed in NMP 26.03; create_customizer_config no longer "
            "calls a configs.create endpoint, so there's no API error path to test here."
        )
    )
    def test_create_customizer_config_non_422_api_error(self):
        pass


class TestCustomizerWaitForTargetReady:
    """Test create_target with various CustomizerTarget params.

    In NMP 26.03 ``create_target`` is a thin wrapper around
    ``client.models.create(workspace=, name=, fileset=, description=)`` — many of
    the per-field params from 25.12 (tokenizer, num_parameters, precision) no
    longer have a direct slot on models.create. The tests below verify that
    create_target accepts these CustomizerTarget params without error and forwards
    the fields it does map (fileset, description).
    """

    def test_create_target_with_tokenizer_param(self, customizer, sample_flywheel_run_id):
        """Test target creation with tokenizer parameter — params accepted, models.create called."""
        from src.config import CustomizerTarget

        target_params = CustomizerTarget(
            name="test-target@v1",
            base_model="meta/llama-3-8b",
            model_uri="hf://model/path",
            tokenizer={"type": "custom", "path": "/tokenizer/path"},
        )

        mock_entity = Mock()
        mock_entity.name = "test-target@v1"
        customizer.nemo_client.models.create.return_value = mock_entity

        customizer.create_target(
            namespace="test-ns",
            target_name="test-target@v1",
            params=target_params,
        )

        call_args = customizer.nemo_client.models.create.call_args.kwargs
        # NMP 26.03: ``hf://`` model_uri provisions a workspace fileset and
        # ``models.create`` references that fileset, not the raw HF URI.
        assert call_args["fileset"] == "test-ns/test-target-v1"

    def test_create_target_with_num_parameters_param(self, customizer, sample_flywheel_run_id):
        """Test target creation with num_parameters parameter — accepted, models.create called."""
        from src.config import CustomizerTarget

        target_params = CustomizerTarget(
            name="test-target@v1",
            base_model="meta/llama-3-8b",
            model_uri="hf://model/path",
            num_parameters=8000000000,  # 8B parameters
        )

        mock_entity = Mock()
        mock_entity.name = "test-target@v1"
        customizer.nemo_client.models.create.return_value = mock_entity

        customizer.create_target(
            namespace="test-ns",
            target_name="test-target@v1",
            params=target_params,
        )

        call_args = customizer.nemo_client.models.create.call_args.kwargs
        assert call_args["fileset"] == "test-ns/test-target-v1"
        # num_parameters is not part of models.create — verified by passing without error.

    def test_create_target_with_precision_param(self, customizer, sample_flywheel_run_id):
        """Test target creation with precision parameter — accepted, models.create called."""
        from src.config import CustomizerTarget

        target_params = CustomizerTarget(
            name="test-target@v1",
            base_model="meta/llama-3-8b",
            model_uri="hf://model/path",
            precision="fp8",
        )

        mock_entity = Mock()
        mock_entity.name = "test-target@v1"
        customizer.nemo_client.models.create.return_value = mock_entity

        customizer.create_target(
            namespace="test-ns",
            target_name="test-target@v1",
            params=target_params,
        )

        call_args = customizer.nemo_client.models.create.call_args.kwargs
        assert call_args["fileset"] == "test-ns/test-target-v1"
        # precision is not part of models.create — verified by passing without error.
