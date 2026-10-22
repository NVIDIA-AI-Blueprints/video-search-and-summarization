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
from unittest.mock import ANY, Mock, patch

import pytest
from bson import ObjectId

from src.api.db_manager import TaskDBManager, get_db_manager
from src.api.models import (
    EvalType,
    FlywheelRunStatus,
    LLMJudgeRun,
    NIMCustomization,
    NIMEvaluation,
    NIMRun,
    NIMRunStatus,
)
from src.api.schemas import DeploymentStatus


@pytest.fixture
def db_manager():
    with patch("src.api.db_manager.get_db") as mock_get_db:
        # Create mock collections
        mock_db = Mock()
        mock_db.flywheel_runs = Mock()
        mock_db.nims = Mock()
        mock_db.evaluations = Mock()
        mock_db.customizations = Mock()
        mock_db.llm_judge_runs = Mock()

        mock_get_db.return_value = mock_db
        return TaskDBManager()


class TestGetDbManager:
    """Test cases for the get_db_manager function."""

    def test_get_db_manager_singleton(self):
        """Test get_db_manager returns singleton instance."""
        with patch("src.api.db_manager.TaskDBManager") as mock_task_db:
            mock_instance = Mock()
            mock_task_db.return_value = mock_instance

            # Save the current singleton state
            import src.api.db_manager

            original_db_manager = src.api.db_manager._db_manager

            try:
                # Reset the global singleton for this test only
                src.api.db_manager._db_manager = None

                # First call should create instance
                result1 = get_db_manager()
                mock_task_db.assert_called_once()
                assert result1 == mock_instance

                # Second call should return same instance
                result2 = get_db_manager()
                # Should not call TaskDBManager again
                mock_task_db.assert_called_once()
                assert result2 == mock_instance
                assert result1 is result2
            finally:
                # Always restore the original singleton state
                src.api.db_manager._db_manager = original_db_manager


class TestTaskDBManager:
    def test_db_property(self, db_manager):
        """Test the db property returns the database instance."""
        result = db_manager.db
        assert result == db_manager._db

    def test_update_flywheel_run_status(self, db_manager):
        """Test updating flywheel run status."""
        run_id = "507f1f77bcf86cd799439011"
        status = FlywheelRunStatus.RUNNING

        db_manager.update_flywheel_run_status(run_id, status)

        db_manager._flywheel_runs.update_one.assert_called_once_with(
            {"_id": ObjectId(run_id), "error": None},
            {"$set": {"status": status}},
        )

    def test_mark_flywheel_run_completed(self, db_manager):
        run_id = "507f1f77bcf86cd799439011"
        finished_at = datetime.utcnow()

        db_manager.mark_flywheel_run_completed(run_id, finished_at)

        db_manager._flywheel_runs.update_one.assert_called_once_with(
            {"_id": ObjectId(run_id), "error": None},
            {"$set": {"finished_at": finished_at, "status": FlywheelRunStatus.COMPLETED}},
        )

    def test_mark_flywheel_run_error(self, db_manager):
        run_id = "507f1f77bcf86cd799439011"
        error_msg = "Test error"
        finished_at = datetime.utcnow()
        db_manager.mark_flywheel_run_error(run_id, error_msg, finished_at=finished_at)

        db_manager._flywheel_runs.update_one.assert_called_once_with(
            {"_id": ObjectId(run_id), "error": None},
            {
                "$set": {
                    "error": error_msg,
                    "status": FlywheelRunStatus.FAILED,
                    "finished_at": finished_at,
                }
            },
        )

    def test_mark_flywheel_run_cancelled(self, db_manager):
        """Test marking flywheel run as cancelled."""
        run_id = "507f1f77bcf86cd799439011"
        error_msg = "Cancelled by user"

        with patch("src.api.db_manager.datetime") as mock_datetime:
            mock_time = datetime.utcnow()
            mock_datetime.utcnow.return_value = mock_time

            db_manager.mark_flywheel_run_cancelled(run_id, error_msg)

            expected_update = {
                "status": FlywheelRunStatus.CANCELLED,
                "finished_at": mock_time,
                "error": error_msg,
            }

            db_manager._flywheel_runs.update_one.assert_called_once_with(
                {"_id": ObjectId(run_id), "finished_at": None},
                {"$set": expected_update},
            )

    def test_is_flywheel_run_cancelled_true(self, db_manager):
        """Test is_flywheel_run_cancelled returns True when cancelled."""
        run_id = "507f1f77bcf86cd799439011"
        db_manager._flywheel_runs.find_one.return_value = {"status": FlywheelRunStatus.CANCELLED}

        result = db_manager.is_flywheel_run_cancelled(run_id)

        assert result is True
        db_manager._flywheel_runs.find_one.assert_called_once_with(
            {"_id": ObjectId(run_id)}, {"status": 1}
        )

    def test_is_flywheel_run_cancelled_false(self, db_manager):
        """Test is_flywheel_run_cancelled returns False when not cancelled."""
        run_id = "507f1f77bcf86cd799439011"
        db_manager._flywheel_runs.find_one.return_value = {"status": FlywheelRunStatus.RUNNING}

        result = db_manager.is_flywheel_run_cancelled(run_id)

        assert result is False

    def test_is_flywheel_run_cancelled_no_document(self, db_manager):
        """Test is_flywheel_run_cancelled returns falsy value when document not found."""
        run_id = "507f1f77bcf86cd799439011"
        db_manager._flywheel_runs.find_one.return_value = None

        result = db_manager.is_flywheel_run_cancelled(run_id)
        # The method returns None (falsy) when document is not found, not False
        assert not result

    def test_create_nim_run(self, db_manager):
        nim_run = NIMRun(
            flywheel_run_id=ObjectId(),
            model_name="test-model",
            started_at=datetime.utcnow(),
            runtime_seconds=0.0,
            status=NIMRunStatus.RUNNING,
        )
        mock_result = Mock()
        mock_result.inserted_id = ObjectId()
        db_manager._nims.insert_one.return_value = mock_result

        result = db_manager.create_nim_run(nim_run)

        assert isinstance(result, ObjectId)
        db_manager._nims.insert_one.assert_called_once()

    def test_set_nim_status(self, db_manager):
        nim_id = ObjectId()
        status = NIMRunStatus.RUNNING

        # Test without optional parameters
        db_manager.set_nim_status(nim_id, status)
        db_manager._nims.update_one.assert_called_with(
            {"_id": nim_id}, {"$set": {"status": status}}
        )

        # Test with error
        db_manager.set_nim_status(nim_id, status, error="Test error")
        db_manager._nims.update_one.assert_called_with(
            {"_id": nim_id}, {"$set": {"status": status, "error": "Test error"}}
        )

        # Test with deployment status
        db_manager.set_nim_status(nim_id, status, deployment_status=DeploymentStatus.RUNNING)
        db_manager._nims.update_one.assert_called_with(
            {"_id": nim_id},
            {"$set": {"status": status, "deployment_status": DeploymentStatus.RUNNING}},
        )

    def test_update_nim_deployment_status(self, db_manager):
        nim_id = ObjectId()
        deployment_status = DeploymentStatus.RUNNING
        runtime_seconds = 123.45

        db_manager.update_nim_deployment_status(nim_id, deployment_status, runtime_seconds)

        db_manager._nims.update_one.assert_called_once_with(
            {"_id": nim_id},
            {"$set": {"deployment_status": deployment_status, "runtime_seconds": runtime_seconds}},
        )

    def test_mark_nim_completed(self, db_manager):
        nim_id = ObjectId()
        started_at = datetime.utcnow()

        with patch("src.api.db_manager.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value = started_at

            db_manager.mark_nim_completed(nim_id, started_at)

            db_manager._nims.update_one.assert_called_once()

    def test_mark_nim_completed_with_none_started_at(self, db_manager):
        """Test mark_nim_completed with None started_at."""
        nim_id = ObjectId()

        with patch("src.api.db_manager.datetime") as mock_datetime:
            mock_time = datetime.utcnow()
            mock_datetime.utcnow.return_value = mock_time

            db_manager.mark_nim_completed(nim_id, None)

            expected_update = {
                "status": NIMRunStatus.COMPLETED,
                "deployment_status": DeploymentStatus.COMPLETED,
                "finished_at": mock_time,
                "runtime_seconds": 0.0,
            }

            db_manager._nims.update_one.assert_called_once_with(
                {"_id": nim_id, "error": None},
                {"$set": expected_update},
            )

    def test_mark_nim_cancelled(self, db_manager):
        """Test marking NIM as cancelled."""
        nim_id = ObjectId()
        error_msg = "Cancelled by user"

        with patch("src.api.db_manager.datetime") as mock_datetime:
            mock_time = datetime.utcnow()
            mock_datetime.utcnow.return_value = mock_time

            db_manager.mark_nim_cancelled(nim_id, error_msg)

            expected_update = {
                "status": NIMRunStatus.CANCELLED,
                "deployment_status": DeploymentStatus.CANCELLED,
                "finished_at": mock_time,
                "error": error_msg,
            }

            db_manager._nims.update_one.assert_called_once_with(
                {"_id": nim_id, "error": None, "finished_at": None},
                {"$set": expected_update},
            )

    def test_mark_nim_error(self, db_manager):
        nim_id = ObjectId()
        error_msg = "Test error"

        # Setup initial database state - document exists with no error
        initial_doc = {"_id": nim_id, "error": None, "status": NIMRunStatus.RUNNING}

        # Setup final state after update
        final_doc = {
            "_id": nim_id,
            "error": error_msg,
            "status": NIMRunStatus.FAILED,
            "deployment_status": DeploymentStatus.FAILED,
        }

        # Mock find_one to return different results before and after update
        db_manager._nims.find_one.side_effect = [initial_doc, final_doc]

        # Get state before update
        before_state = db_manager._nims.find_one({"_id": nim_id})
        assert before_state["error"] is None

        db_manager.mark_nim_error(nim_id, error_msg)

        # Verify the update query was correct
        db_manager._nims.update_one.assert_called_once_with(
            {
                "_id": nim_id,
                "error": None,  # Only update if error is None
            },
            {
                "$set": {
                    "error": error_msg,
                    "status": NIMRunStatus.FAILED,
                    "deployment_status": DeploymentStatus.FAILED,
                    "finished_at": ANY,
                }
            },
        )

        # Get state after update
        after_state = db_manager._nims.find_one({"_id": nim_id})

        # Verify the document was actually modified
        assert after_state["error"] == error_msg
        assert after_state["status"] == NIMRunStatus.FAILED
        assert after_state["deployment_status"] == DeploymentStatus.FAILED

    def test_mark_nim_error_when_error_exists(self, db_manager):
        nim_id = ObjectId()
        existing_error = "Existing error"
        new_error_msg = "New error"

        # Setup initial database state - document exists with error
        doc_with_error = {
            "_id": nim_id,
            "error": existing_error,
            "status": NIMRunStatus.FAILED,
            "deployment_status": DeploymentStatus.FAILED,
        }

        # Mock find_one to return same document before and after (no change)
        db_manager._nims.find_one.return_value = doc_with_error

        # Get state before update
        before_state = db_manager._nims.find_one({"_id": nim_id})
        assert before_state["error"] == existing_error

        db_manager.mark_nim_error(nim_id, new_error_msg)

        # Verify the update was attempted with correct query
        db_manager._nims.update_one.assert_called_once_with(
            {
                "_id": nim_id,
                "error": None,  # This ensures we only update if no error exists
            },
            {
                "$set": {
                    "error": new_error_msg,
                    "status": NIMRunStatus.FAILED,
                    "deployment_status": DeploymentStatus.FAILED,
                    "finished_at": ANY,
                }
            },
        )

        # Get state after update attempt
        after_state = db_manager._nims.find_one({"_id": nim_id})

        # Verify that document was not modified
        assert after_state["error"] == existing_error  # Error should remain unchanged
        assert after_state["status"] == NIMRunStatus.FAILED
        assert after_state["deployment_status"] == DeploymentStatus.FAILED

    def test_find_nim_run(self, db_manager):
        flywheel_run_id = "507f1f77bcf86cd799439011"
        model_name = "test-model"
        expected_result = {"_id": ObjectId(), "model_name": model_name}
        db_manager._nims.find_one.return_value = expected_result

        result = db_manager.find_nim_run(flywheel_run_id, model_name)

        assert result == expected_result
        db_manager._nims.find_one.assert_called_once_with(
            {"flywheel_run_id": ObjectId(flywheel_run_id), "model_name": model_name}
        )

    def test_mark_all_nims_status(self, db_manager):
        """Test marking all NIMs status for a flywheel run."""
        flywheel_run_id = "507f1f77bcf86cd799439011"
        status = NIMRunStatus.FAILED
        error_msg = "Test error"

        with patch("src.api.db_manager.datetime") as mock_datetime:
            mock_time = datetime.utcnow()
            mock_datetime.utcnow.return_value = mock_time

            db_manager.mark_all_nims_status(flywheel_run_id, status, error_msg)

            expected_update = {
                "error": error_msg,
                "status": status,
                "finished_at": mock_time,
            }

            db_manager._nims.update_many.assert_called_once_with(
                {
                    "flywheel_run_id": ObjectId(flywheel_run_id),
                    "error": None,
                },
                {"$set": expected_update},
            )

    def test_mark_all_nims_status_without_error(self, db_manager):
        """Test marking all NIMs status without error message."""
        flywheel_run_id = ObjectId()
        status = NIMRunStatus.CANCELLED

        with patch("src.api.db_manager.datetime") as mock_datetime:
            mock_time = datetime.utcnow()
            mock_datetime.utcnow.return_value = mock_time

            db_manager.mark_all_nims_status(flywheel_run_id, status)

            expected_update = {
                "error": None,
                "status": status,
                "finished_at": mock_time,
            }

            db_manager._nims.update_many.assert_called_once_with(
                {
                    "flywheel_run_id": flywheel_run_id,
                    "error": None,
                },
                {"$set": expected_update},
            )

    def test_insert_evaluation(self, db_manager):
        evaluation = NIMEvaluation(
            flywheel_run_id=ObjectId(),
            nim_id=ObjectId(),
            model_name="test-model",
            eval_type=EvalType.BASE,
            dataset="test_dataset",
            scores={"accuracy": 0.95},
            started_at=datetime.utcnow(),
            runtime_seconds=0.0,
            progress=0.0,
        )

        db_manager.insert_evaluation(evaluation)

        db_manager._evaluations.insert_one.assert_called_once()

    def test_update_evaluation(self, db_manager):
        eval_id = ObjectId()
        update_fields = {"status": "completed"}

        db_manager.update_evaluation(eval_id, update_fields)

        db_manager._evaluations.update_one.assert_called_once_with(
            {"_id": eval_id}, {"$set": update_fields}
        )

    def test_insert_customization(self, db_manager):
        customization = NIMCustomization(
            flywheel_run_id=ObjectId(),
            nim_id=ObjectId(),
            customized_model="test-model",
            workload_id="test-workload",
            base_model="base-model",
            started_at=datetime.utcnow(),
        )

        db_manager.insert_customization(customization)

        db_manager._customizations.insert_one.assert_called_once()

    def test_update_customization(self, db_manager):
        custom_id = ObjectId()
        update_fields = {"status": "completed"}

        db_manager.update_customization(custom_id, update_fields)

        db_manager._customizations.update_one.assert_called_once_with(
            {"_id": custom_id}, {"$set": update_fields}
        )

    def test_find_customization(self, db_manager):
        workload_id = "test-workload"
        model_name = "test-model"
        expected_result = {"_id": ObjectId(), "model_name": model_name}
        db_manager._customizations.find_one.return_value = expected_result

        result = db_manager.find_customization(workload_id, model_name)

        assert result == expected_result
        db_manager._customizations.find_one.assert_called_once_with(
            {"workload_id": workload_id, "customized_model": model_name}
        )

    def test_create_llm_judge_run(self, db_manager):
        llm_judge_run = LLMJudgeRun(
            flywheel_run_id=ObjectId(), model_name="test-model", deployment_type="remote"
        )
        mock_result = Mock()
        mock_result.inserted_id = ObjectId()
        db_manager.llm_judge_runs.insert_one.return_value = mock_result

        result = db_manager.create_llm_judge_run(llm_judge_run)

        assert isinstance(result, ObjectId)
        db_manager.llm_judge_runs.insert_one.assert_called_once()

    def test_update_llm_judge_deployment_status(self, db_manager):
        llm_judge_id = ObjectId()
        deployment_status = DeploymentStatus.RUNNING

        db_manager.update_llm_judge_deployment_status(llm_judge_id, deployment_status)

        db_manager.llm_judge_runs.update_one.assert_called_once_with(
            {"_id": llm_judge_id}, {"$set": {"deployment_status": deployment_status}}
        )

    def test_mark_llm_judge_error(self, db_manager):
        llm_judge_id = ObjectId()
        error_msg = "Test error"

        db_manager.mark_llm_judge_error(llm_judge_id, error_msg)

        db_manager.llm_judge_runs.update_one.assert_called_once_with(
            {"_id": llm_judge_id},
            {
                "$set": {
                    "error": error_msg,
                    "deployment_status": DeploymentStatus.FAILED,
                }
            },
        )

    def test_mark_llm_judge_cancelled(self, db_manager):
        """Test marking LLM judge as cancelled."""
        flywheel_run_id = ObjectId()
        error_msg = "Cancelled by user"

        db_manager.mark_llm_judge_cancelled(flywheel_run_id, error_msg)

        expected_update = {
            "deployment_status": DeploymentStatus.CANCELLED.value,
            "error": error_msg,
        }

        db_manager.llm_judge_runs.update_one.assert_called_once_with(
            {"flywheel_run_id": flywheel_run_id},
            {"$set": expected_update},
        )

    def test_find_llm_judge_run(self, db_manager):
        flywheel_run_id = "507f1f77bcf86cd799439011"
        expected_result = {"_id": ObjectId(), "model_name": "test-model"}
        db_manager.llm_judge_runs.find_one.return_value = expected_result

        result = db_manager.find_llm_judge_run(flywheel_run_id)

        assert result == expected_result
        db_manager.llm_judge_runs.find_one.assert_called_once_with(
            {"flywheel_run_id": ObjectId(flywheel_run_id)}
        )

    def test_get_flywheel_run(self, db_manager):
        job_id = "507f1f77bcf86cd799439011"
        expected_result = {"_id": ObjectId(job_id)}
        db_manager._flywheel_runs.find_one.return_value = expected_result

        result = db_manager.get_flywheel_run(job_id)

        assert result == expected_result
        db_manager._flywheel_runs.find_one.assert_called_once_with({"_id": ObjectId(job_id)})

    def test_find_running_flywheel_runs(self, db_manager):
        """Test finding all running flywheel runs."""
        expected_results = [
            {"_id": ObjectId(), "status": FlywheelRunStatus.RUNNING},
            {"_id": ObjectId(), "status": FlywheelRunStatus.PENDING},
        ]
        db_manager._flywheel_runs.find.return_value = expected_results

        results = db_manager.find_running_flywheel_runs()

        assert results == expected_results
        # PROMOTING (AOI mid-rollout) counts as non-terminal for cleanup too.
        running_statuses = [
            FlywheelRunStatus.PENDING.value,
            FlywheelRunStatus.RUNNING.value,
            FlywheelRunStatus.PROMOTING.value,
        ]
        db_manager._flywheel_runs.find.assert_called_once_with(
            {"status": {"$in": running_statuses}}
        )

    def test_find_running_nims_for_flywheel(self, db_manager):
        """Test finding running NIMs for a flywheel run."""
        flywheel_run_id = ObjectId()
        expected_results = [
            {"_id": ObjectId(), "status": NIMRunStatus.RUNNING},
            {"_id": ObjectId(), "status": NIMRunStatus.PENDING},
        ]
        db_manager._nims.find.return_value = expected_results

        results = db_manager.find_running_nims_for_flywheel(flywheel_run_id)

        assert results == expected_results
        expected_query = {
            "flywheel_run_id": flywheel_run_id,
            "status": {
                "$in": [
                    NIMRunStatus.RUNNING.value,
                    NIMRunStatus.PENDING.value,
                ]
            },
        }
        db_manager._nims.find.assert_called_once_with(expected_query)

    def test_record_deft_progress_merges_rather_than_replacing(self, db_manager):
        # Must not $set the whole `deft` subdocument. Per-iteration metrics are
        # meant to land as siblings under `deft`, and a whole-block replace
        # would silently clobber them on the next progress write -- which is
        # every ~15s for the hours a training runs.
        fw_id = ObjectId()
        now = datetime.utcnow()
        progress = {"current_iteration": 1, "current_step": "train"}

        db_manager.record_deft_progress(fw_id, "run-9", progress, now)

        _, update = db_manager._flywheel_runs.update_one.call_args.args
        # A pipeline, not a plain $set: dotted paths die on the explicit
        # `deft: null` that to_mongo() writes, and a whole-subdocument replace
        # drops siblings. See _deft_merge.
        assert isinstance(update, list), "must be an update pipeline"
        merged = update[0]["$set"]["deft"]["$mergeObjects"]
        assert merged[0] == {"$cond": [{"$eq": [{"$type": "$deft"}, "object"]}, "$deft", {}]}
        assert merged[1] == {
            "$literal": {"run_id": "run-9", "progress": progress, "progress_at": now}
        }

    def test_record_deft_stage_writes_stage_alone(self, db_manager):
        # Must merge, not replace: a stage transition happens while progress is
        # already recorded, and must not cost it.
        fw_id = ObjectId()
        now = datetime.utcnow()

        db_manager.record_deft_stage(fw_id, "run_deft_training", now)

        query, update = db_manager._flywheel_runs.update_one.call_args.args
        assert query == {"_id": fw_id}
        assert isinstance(update, list), "must be an update pipeline"
        merged = update[0]["$set"]["deft"]["$mergeObjects"]
        assert merged[1] == {"$literal": {"stage": "run_deft_training", "stage_started_at": now}}

    def test_record_deft_run_id_writes_identity_alone(self, db_manager):
        # Identity must be recordable without any progress: a run that dies
        # before reporting is exactly when the id is needed.
        fw_id = ObjectId()

        db_manager.record_deft_run_id(fw_id, "run-9")

        query, update = db_manager._flywheel_runs.update_one.call_args.args
        assert query == {"_id": fw_id}
        assert isinstance(update, list), "must be an update pipeline"
        merged = update[0]["$set"]["deft"]["$mergeObjects"]
        # writes ONLY run_id, so an existing progress survives
        assert merged[1] == {"$literal": {"run_id": "run-9"}}

    def test_find_nims_for_job(self, db_manager):
        job_id = ObjectId()
        expected_results = [{"_id": ObjectId(), "model_name": "test-model"}]
        db_manager._nims.find.return_value = expected_results

        results = db_manager.find_nims_for_job(job_id)

        assert results == expected_results
        db_manager._nims.find.assert_called_once_with({"flywheel_run_id": job_id})

    def test_find_customizations_for_nim(self, db_manager):
        nim_id = ObjectId()
        expected_results = [{"_id": ObjectId(), "model_name": "test-model"}]
        db_manager._customizations.find.return_value = expected_results

        results = db_manager.find_customizations_for_nim(nim_id)

        assert results == expected_results
        db_manager._customizations.find.assert_called_once_with({"nim_id": nim_id})

    def test_find_evaluations_for_nim(self, db_manager):
        nim_id = ObjectId()
        expected_results = [{"_id": ObjectId(), "model_name": "test-model"}]
        db_manager._evaluations.find.return_value = expected_results

        results = db_manager.find_evaluations_for_nim(nim_id)

        assert results == expected_results
        db_manager._evaluations.find.assert_called_once_with({"nim_id": nim_id})

    def test_delete_job_records(self, db_manager):
        job_id = ObjectId()

        # Mock the nim records that would be found
        mock_nim_records = [
            {"_id": ObjectId()},
            {"_id": ObjectId()},
        ]
        nim_ids = [nim["_id"] for nim in mock_nim_records]

        # Set up the mock to return the nim records when find is called
        db_manager._nims.find.return_value = mock_nim_records

        db_manager.delete_job_records(job_id)

        # Verify the find operation was called to get nim IDs
        db_manager._nims.find.assert_called_once_with({"flywheel_run_id": job_id}, {"_id": 1})

        # Verify all delete operations were called with correct parameters
        db_manager._evaluations.delete_many.assert_called_once_with({"nim_id": {"$in": nim_ids}})
        db_manager._customizations.delete_many.assert_called_once_with({"nim_id": {"$in": nim_ids}})
        db_manager._nims.delete_many.assert_called_once_with({"flywheel_run_id": job_id})
        db_manager.llm_judge_runs.delete_many.assert_called_once_with({"flywheel_run_id": job_id})
        db_manager._flywheel_runs.delete_one.assert_called_once_with({"_id": job_id})

    def test_delete_job_records_no_nims(self, db_manager):
        """Test delete_job_records when no NIMs are found for the job."""
        job_id = ObjectId()

        # Set up the mock to return empty list (no nim records found)
        db_manager._nims.find.return_value = []

        db_manager.delete_job_records(job_id)

        # Verify the find operation was called to get nim IDs
        db_manager._nims.find.assert_called_once_with({"flywheel_run_id": job_id}, {"_id": 1})

        # Verify evaluations and customizations delete_many are NOT called when no nim_ids
        db_manager._evaluations.delete_many.assert_not_called()
        db_manager._customizations.delete_many.assert_not_called()

        # Verify remaining delete operations were still called
        db_manager._nims.delete_many.assert_called_once_with({"flywheel_run_id": job_id})
        db_manager.llm_judge_runs.delete_many.assert_called_once_with({"flywheel_run_id": job_id})
        db_manager._flywheel_runs.delete_one.assert_called_once_with({"_id": job_id})

    def test_init_with_uninitialized_db(self, db_manager):
        with (
            patch("src.api.db_manager.get_db", side_effect=RuntimeError),
            patch("src.api.db_manager.init_db") as mock_init_db,
        ):
            mock_db = Mock()
            mock_db.flywheel_runs = Mock()
            mock_db.nims = Mock()
            mock_db.evaluations = Mock()
            mock_db.customizations = Mock()
            mock_db.llm_judge_runs = Mock()
            mock_init_db.return_value = mock_db

            # This should not raise an exception
            TaskDBManager()

            # Verify init_db was called
            mock_init_db.assert_called_once()
