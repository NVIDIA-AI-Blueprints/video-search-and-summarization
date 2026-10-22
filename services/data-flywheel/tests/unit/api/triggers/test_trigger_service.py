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

"""Tests for trigger service layer."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from src.api.schemas import FlywheelRunStatus
from src.api.triggers.models import RetrainingTrigger
from src.api.triggers.schemas import TriggerCreateRequest, TriggerUpdateRequest
from src.api.triggers.service import (
    TriggerService,
    is_execution_stale,
    mark_execution_as_failed,
    validate_config_for_iterative_finetuning,
)


class TestTriggerService:
    """Tests for TriggerService class."""

    def test_create_trigger_success(self, mock_trigger_db, sample_data_threshold_trigger):
        """Test successful trigger creation."""
        service = TriggerService(mock_trigger_db)
        request = TriggerCreateRequest(**sample_data_threshold_trigger)

        trigger_id = ObjectId()
        mock_trigger_db.retraining_triggers.insert_one.return_value = MagicMock(
            inserted_id=trigger_id
        )

        result = service.create_trigger(request)

        assert isinstance(result, RetrainingTrigger)
        assert result.name == request.name
        assert result.client_id == request.client_id
        mock_trigger_db.retraining_triggers.insert_one.assert_called_once()

    def test_create_trigger_duplicate_name(self, mock_trigger_db, sample_data_threshold_trigger):
        """Test trigger creation with duplicate name."""
        service = TriggerService(mock_trigger_db)
        request = TriggerCreateRequest(**sample_data_threshold_trigger)

        mock_trigger_db.retraining_triggers.insert_one.side_effect = DuplicateKeyError(
            "duplicate key error"
        )

        with pytest.raises(ValueError) as exc_info:
            service.create_trigger(request)

        assert "already exists" in str(exc_info.value)

    def test_get_trigger_by_id(self, mock_trigger_db, sample_trigger_doc):
        """Test getting trigger by ID."""
        service = TriggerService(mock_trigger_db)

        mock_trigger_db.retraining_triggers.find_one.return_value = sample_trigger_doc

        trigger = service.get_trigger(str(sample_trigger_doc["_id"]))

        assert trigger is not None
        assert trigger.id == sample_trigger_doc["_id"]
        assert trigger.name == sample_trigger_doc["name"]

    def test_get_trigger_not_found(self, mock_trigger_db):
        """Test getting non-existent trigger."""
        service = TriggerService(mock_trigger_db)

        mock_trigger_db.retraining_triggers.find_one.return_value = None

        trigger = service.get_trigger(str(ObjectId()))

        assert trigger is None

    def test_update_trigger(self, mock_trigger_db, sample_trigger_doc):
        """Test updating trigger."""
        service = TriggerService(mock_trigger_db)

        mock_trigger_db.retraining_triggers.find_one.return_value = sample_trigger_doc
        mock_trigger_db.retraining_triggers.update_one.return_value = MagicMock(matched_count=1)

        update_request = TriggerUpdateRequest(
            enabled=False,
            metric_thresholds={"accuracy": 0.90},
        )

        updated = service.update_trigger(str(sample_trigger_doc["_id"]), update_request)

        assert updated is not None
        mock_trigger_db.retraining_triggers.update_one.assert_called_once()

    def test_delete_trigger(self, mock_trigger_db):
        """Test deleting trigger."""
        service = TriggerService(mock_trigger_db)

        trigger_id = str(ObjectId())
        mock_trigger_db.retraining_triggers.delete_one.return_value = MagicMock(deleted_count=1)

        result = service.delete_trigger(trigger_id)

        assert result is True
        mock_trigger_db.retraining_triggers.delete_one.assert_called_once()

    def test_delete_trigger_not_found(self, mock_trigger_db):
        """Test deleting non-existent trigger."""
        service = TriggerService(mock_trigger_db)

        mock_trigger_db.retraining_triggers.delete_one.return_value = MagicMock(deleted_count=0)

        result = service.delete_trigger(str(ObjectId()))

        assert result is False

    def test_list_triggers(self, mock_trigger_db, sample_trigger_doc):
        """Test listing triggers."""
        service = TriggerService(mock_trigger_db)

        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.__iter__.return_value = iter([sample_trigger_doc])

        mock_trigger_db.retraining_triggers.find.return_value = mock_cursor
        mock_trigger_db.retraining_triggers.count_documents.return_value = 1

        triggers, total = service.list_triggers(skip=0, limit=10)

        assert len(triggers) == 1
        assert total == 1
        assert triggers[0].id == sample_trigger_doc["_id"]


class TestMarkExecutionAsFailed:
    """Tests for mark_execution_as_failed utility."""

    def test_mark_execution_failed(self, mock_trigger_db):
        """Test marking execution as failed."""
        execution = {
            "_id": ObjectId(),
            "trigger_id": str(ObjectId()),
            "execution_time": datetime.utcnow() - timedelta(seconds=100),
            "status": "running",
        }

        mark_execution_as_failed(mock_trigger_db, execution, "Test error")

        mock_trigger_db.trigger_executions.update_one.assert_called_once()
        call_args = mock_trigger_db.trigger_executions.update_one.call_args

        assert call_args[0][0] == {"_id": execution["_id"]}
        assert call_args[0][1]["$set"]["status"] == "failed"
        assert call_args[0][1]["$set"]["error"] == "Test error"
        assert "completed_at" in call_args[0][1]["$set"]
        assert "total_duration_seconds" in call_args[0][1]["$set"]

    def test_mark_execution_failed_calculates_duration(self, mock_trigger_db):
        """Test duration calculation when marking failed."""
        start_time = datetime.utcnow() - timedelta(seconds=150)
        execution = {
            "_id": ObjectId(),
            "trigger_id": str(ObjectId()),
            "execution_time": start_time,
            "status": "running",
        }

        mark_execution_as_failed(mock_trigger_db, execution, "Timeout")

        call_args = mock_trigger_db.trigger_executions.update_one.call_args
        duration = call_args[0][1]["$set"]["total_duration_seconds"]

        assert duration is not None
        assert duration >= 150


class TestIsExecutionStale:
    """Tests for is_execution_stale utility."""

    def test_execution_with_completed_job_is_stale(self, mock_trigger_db):
        """Test execution with completed eval job is stale."""
        eval_job_id = str(ObjectId())
        execution = {
            "_id": ObjectId(),
            "trigger_id": str(ObjectId()),
            "execution_time": datetime.utcnow() - timedelta(hours=1),
            "status": "running",
            "eval_job_id": eval_job_id,
        }

        # Mock completed job
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(eval_job_id),
            "status": FlywheelRunStatus.COMPLETED,
        }

        result = is_execution_stale(mock_trigger_db, execution)

        assert result is True

    def test_execution_with_failed_job_is_stale(self, mock_trigger_db):
        """Test execution with failed training job is stale."""
        training_job_id = str(ObjectId())
        execution = {
            "_id": ObjectId(),
            "trigger_id": str(ObjectId()),
            "execution_time": datetime.utcnow() - timedelta(hours=2),
            "status": "running",
            "training_job_id": training_job_id,
        }

        # Mock failed job
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(training_job_id),
            "status": FlywheelRunStatus.FAILED,
        }

        result = is_execution_stale(mock_trigger_db, execution)

        assert result is True

    def test_execution_pending_over_3_hours_is_stale(self, mock_trigger_db):
        """Test execution pending for over 3 hours is stale."""
        execution = {
            "_id": ObjectId(),
            "trigger_id": str(ObjectId()),
            "execution_time": datetime.utcnow() - timedelta(hours=4),
            "status": "pending",
        }

        mock_trigger_db.flywheel_runs.find_one.return_value = None

        result = is_execution_stale(mock_trigger_db, execution)

        assert result is True

    def test_execution_with_running_job_not_stale(self, mock_trigger_db):
        """Test execution with running job is not stale."""
        eval_job_id = str(ObjectId())
        execution = {
            "_id": ObjectId(),
            "trigger_id": str(ObjectId()),
            "execution_time": datetime.utcnow() - timedelta(minutes=30),
            "status": "running",
            "eval_job_id": eval_job_id,
        }

        # Mock running job
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(eval_job_id),
            "status": FlywheelRunStatus.RUNNING,
        }

        result = is_execution_stale(mock_trigger_db, execution)

        assert result is False

    def test_recent_pending_execution_not_stale(self, mock_trigger_db):
        """Test recent pending execution is not stale."""
        execution = {
            "_id": ObjectId(),
            "trigger_id": str(ObjectId()),
            "execution_time": datetime.utcnow() - timedelta(minutes=30),
            "status": "pending",
        }

        mock_trigger_db.flywheel_runs.find_one.return_value = None

        result = is_execution_stale(mock_trigger_db, execution)

        assert result is False


class TestValidateConfigForIterativeFinetuning:
    """Tests for validate_config_for_iterative_finetuning function."""

    def test_valid_config_with_all_weights(self, mock_trigger_db):
        """Test validation passes for embedding_job with all_weights finetuning_type."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = {
            "name": "test-config",
            "config_data": {"training_config": {"finetuning_type": "all_weights"}},
        }

        # Should not raise any exception
        validate_config_for_iterative_finetuning(mock_trigger_db, "test-config", "embedding_job")

        # Verify database was queried
        mock_trigger_db.configurations.find_one.assert_called_once_with({"name": "test-config"})

    def test_invalid_job_type_llm_job(self, mock_trigger_db):
        """Test validation fails for llm_job (only embedding_job supported)."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()

        # Should raise ValueError for llm_job
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(mock_trigger_db, "test-config", "llm_job")

        assert "embedding_job" in str(exc_info.value)
        assert "llm_job" in str(exc_info.value)
        # Should not query database for invalid job_type
        mock_trigger_db.configurations.find_one.assert_not_called()

    def test_config_not_found(self, mock_trigger_db):
        """Test validation fails when configuration doesn't exist."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = None

        # Should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(
                mock_trigger_db, "non-existent-config", "embedding_job"
            )

        assert "not found" in str(exc_info.value).lower()
        assert "non-existent-config" in str(exc_info.value)
        mock_trigger_db.configurations.find_one.assert_called_once_with(
            {"name": "non-existent-config"}
        )

    def test_invalid_finetuning_type_lora(self, mock_trigger_db):
        """Test validation fails for lora finetuning_type."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = {
            "name": "test-config",
            "config_data": {"training_config": {"finetuning_type": "lora"}},
        }

        # Should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(
                mock_trigger_db, "test-config", "embedding_job"
            )

        assert "all_weights" in str(exc_info.value)
        assert "lora" in str(exc_info.value)
        mock_trigger_db.configurations.find_one.assert_called_once_with({"name": "test-config"})

    def test_invalid_finetuning_type_lora_merged(self, mock_trigger_db):
        """Test validation fails for lora_merged finetuning_type."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = {
            "name": "test-config",
            "config_data": {"training_config": {"finetuning_type": "lora_merged"}},
        }

        # Should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(
                mock_trigger_db, "test-config", "embedding_job"
            )

        assert "all_weights" in str(exc_info.value)
        assert "lora_merged" in str(exc_info.value)

    def test_missing_finetuning_type(self, mock_trigger_db):
        """Test validation fails when finetuning_type is missing."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = {
            "name": "test-config",
            "config_data": {"training_config": {}},
        }

        # Should raise ValueError (finetuning_type will be None)
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(
                mock_trigger_db, "test-config", "embedding_job"
            )

        assert "all_weights" in str(exc_info.value)

    def test_missing_training_config(self, mock_trigger_db):
        """Test validation fails when training_config is missing."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = {
            "name": "test-config",
            "config_data": {},
        }

        # Should raise ValueError (finetuning_type will be None)
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(
                mock_trigger_db, "test-config", "embedding_job"
            )

        assert "all_weights" in str(exc_info.value)

    def test_missing_config_data(self, mock_trigger_db):
        """Test validation fails when config_data is missing."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = {"name": "test-config"}

        # Should raise ValueError (finetuning_type will be None)
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(
                mock_trigger_db, "test-config", "embedding_job"
            )

        assert "all_weights" in str(exc_info.value)

    def test_empty_config_data(self, mock_trigger_db):
        """Test validation fails when config_data is None."""
        # Setup: Create a fresh mock for this test
        mock_trigger_db.configurations = MagicMock()
        mock_trigger_db.configurations.find_one.return_value = {
            "name": "test-config",
            "config_data": None,
        }

        # Should raise ValueError (config_data.get will fail, but we need to handle None)
        # Actually, the function should handle None config_data gracefully
        with pytest.raises(ValueError) as exc_info:
            validate_config_for_iterative_finetuning(
                mock_trigger_db, "test-config", "embedding_job"
            )

        # Either ValueError for missing finetuning_type or AttributeError for None config_data
        assert "all_weights" in str(exc_info.value)
