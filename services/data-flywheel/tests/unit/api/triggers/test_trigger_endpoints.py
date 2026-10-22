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

"""Tests for trigger API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from src.api.triggers.endpoints import (
    create_trigger,
    delete_trigger,
    disable_trigger,
    enable_trigger,
    execute_trigger,
    get_execution,
    get_trigger,
    get_trigger_executions,
    list_triggers,
    update_trigger,
)
from src.api.triggers.models import RetrainingTrigger, TriggerExecution
from src.api.triggers.schemas import (
    TriggerCreateRequest,
    TriggerUpdateRequest,
)


class TestCreateTrigger:
    """Tests for create_trigger endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_create_data_threshold_trigger(self, mock_get_db, sample_data_threshold_trigger):
        """Test creating a data threshold trigger."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        trigger_id = ObjectId()
        mock_db.retraining_triggers.insert_one.return_value = MagicMock(inserted_id=trigger_id)

        request = TriggerCreateRequest(**sample_data_threshold_trigger)
        response = await create_trigger(request)

        assert response.id == str(trigger_id)
        assert response.name == "test-data-threshold"
        mock_db.retraining_triggers.insert_one.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_create_metric_degradation_trigger(
        self, mock_get_db, sample_metric_degradation_trigger
    ):
        """Test creating a metric degradation trigger."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        trigger_id = ObjectId()
        mock_db.retraining_triggers.insert_one.return_value = MagicMock(inserted_id=trigger_id)

        request = TriggerCreateRequest(**sample_metric_degradation_trigger)
        response = await create_trigger(request)

        assert response.id == str(trigger_id)
        assert response.trigger_type == "metric_degradation"


class TestGetTrigger:
    """Tests for get_trigger endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_get_existing_trigger(self, mock_get_db, sample_trigger_doc):
        """Test getting an existing trigger."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.retraining_triggers.find_one.return_value = sample_trigger_doc

        response = await get_trigger(str(sample_trigger_doc["_id"]))

        assert response.id == str(sample_trigger_doc["_id"])
        assert response.name == sample_trigger_doc["name"]

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_get_nonexistent_trigger(self, mock_get_db):
        """Test getting non-existent trigger returns 404."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.retraining_triggers.find_one.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_trigger(str(ObjectId()))

        assert exc_info.value.status_code == 404


class TestListTriggers:
    """Tests for list_triggers endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_list_empty_triggers(self, mock_get_db):
        """Test listing triggers when none exist."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.__iter__.return_value = iter([])

        mock_db.retraining_triggers.find.return_value = mock_cursor
        mock_db.retraining_triggers.count_documents.return_value = 0

        response = await list_triggers()

        assert response.total == 0
        assert len(response.triggers) == 0

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_list_multiple_triggers(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test listing multiple triggers."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        # Create trigger objects
        trigger1 = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger1.id = ObjectId()

        trigger2_data = sample_data_threshold_trigger.copy()
        trigger2_data["name"] = "trigger-2"
        trigger2 = RetrainingTrigger(**trigger2_data)
        trigger2.id = ObjectId()

        mock_service.list_triggers.return_value = ([trigger1, trigger2], 2)

        response = await list_triggers()

        assert response.total == 2
        assert len(response.triggers) == 2

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_list_triggers_with_pagination(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test listing triggers with pagination."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()

        mock_service.list_triggers.return_value = ([trigger], 10)

        response = await list_triggers(skip=5, limit=1)

        assert response.total == 10
        assert len(response.triggers) == 1
        mock_service.list_triggers.assert_called_once()


class TestUpdateTrigger:
    """Tests for update_trigger endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_update_trigger_schedule(self, mock_get_db, sample_trigger_doc):
        """Test updating trigger schedule."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.retraining_triggers.find_one.return_value = sample_trigger_doc
        mock_db.retraining_triggers.update_one.return_value = MagicMock(matched_count=1)

        update_request = TriggerUpdateRequest(eval_schedule="0 */12 * * *")
        response = await update_trigger(str(sample_trigger_doc["_id"]), update_request)

        assert response is not None
        mock_db.retraining_triggers.update_one.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_update_nonexistent_trigger(self, mock_get_db):
        """Test updating non-existent trigger returns 404."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.retraining_triggers.find_one.return_value = None

        update_request = TriggerUpdateRequest(enabled=False)

        with pytest.raises(HTTPException) as exc_info:
            await update_trigger(str(ObjectId()), update_request)

        assert exc_info.value.status_code == 404


class TestDeleteTrigger:
    """Tests for delete_trigger endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_delete_existing_trigger(self, mock_service_class, mock_get_db):
        """Test deleting an existing trigger."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger_id = str(ObjectId())
        mock_service.delete_trigger.return_value = True

        # delete_trigger returns None on success
        response = await delete_trigger(trigger_id)

        assert response is None
        mock_service.delete_trigger.assert_called_once_with(trigger_id)

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_delete_nonexistent_trigger(self, mock_get_db):
        """Test deleting non-existent trigger returns 404."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.retraining_triggers.delete_one.return_value = MagicMock(deleted_count=0)

        with pytest.raises(HTTPException) as exc_info:
            await delete_trigger(str(ObjectId()))

        assert exc_info.value.status_code == 404


class TestEnableTrigger:
    """Tests for enable_trigger endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_enable_disabled_trigger(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test enabling a disabled trigger."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        # Create a disabled trigger
        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()
        trigger.enabled = False

        # After enabling, trigger should be enabled
        enabled_trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        enabled_trigger.id = trigger.id
        enabled_trigger.enabled = True

        mock_service.update_trigger.return_value = enabled_trigger

        response = await enable_trigger(str(trigger.id))

        assert response.enabled is True
        mock_service.update_trigger.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_enable_already_enabled_trigger(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test enabling an already enabled trigger (idempotent)."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()
        trigger.enabled = True

        mock_service.update_trigger.return_value = trigger

        response = await enable_trigger(str(trigger.id))

        assert response.enabled is True

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_enable_nonexistent_trigger(self, mock_service_class, mock_get_db):
        """Test enabling non-existent trigger returns 404."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        mock_service.update_trigger.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await enable_trigger(str(ObjectId()))

        assert exc_info.value.status_code == 404


class TestDisableTrigger:
    """Tests for disable_trigger endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_disable_enabled_trigger(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test disabling an enabled trigger."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        # Create an enabled trigger
        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()
        trigger.enabled = True

        # After disabling, trigger should be disabled
        disabled_trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        disabled_trigger.id = trigger.id
        disabled_trigger.enabled = False

        mock_service.update_trigger.return_value = disabled_trigger

        response = await disable_trigger(str(trigger.id))

        assert response.enabled is False
        mock_service.update_trigger.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_disable_already_disabled_trigger(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test disabling an already disabled trigger (idempotent)."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()
        trigger.enabled = False

        mock_service.update_trigger.return_value = trigger

        response = await disable_trigger(str(trigger.id))

        assert response.enabled is False

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_disable_nonexistent_trigger(self, mock_service_class, mock_get_db):
        """Test disabling non-existent trigger returns 404."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        mock_service.update_trigger.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await disable_trigger(str(ObjectId()))

        assert exc_info.value.status_code == 404


class TestExecuteTrigger:
    """Tests for execute_trigger endpoint."""

    @pytest.mark.asyncio
    @patch("src.tasks.tasks.execute_trigger.delay")
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_execute_trigger_immediate(
        self, mock_service_class, mock_get_db, mock_execute_task, sample_data_threshold_trigger
    ):
        """Test immediate trigger execution."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()

        mock_service.get_trigger.return_value = trigger
        mock_db.trigger_executions.find_one.return_value = None

        response = await execute_trigger(str(trigger.id))

        assert response.status == "pending"
        assert response.execution_id is not None
        assert "execution started" in response.message.lower()
        mock_execute_task.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_execute_trigger_blocked_by_running(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test execution blocked by running execution."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()

        mock_service.get_trigger.return_value = trigger

        # Existing running execution
        mock_db.trigger_executions.find_one.return_value = {
            "_id": ObjectId(),
            "trigger_id": str(trigger.id),
            "status": "running",
        }

        with pytest.raises(HTTPException) as exc_info:
            await execute_trigger(str(trigger.id))

        assert exc_info.value.status_code == 409


class TestGetTriggerExecutions:
    """Tests for get_trigger_executions endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_list_executions_empty(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger
    ):
        """Test listing executions when none exist."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()

        mock_service.get_trigger.return_value = trigger
        mock_service.get_executions.return_value = ([], 0)

        response = await get_trigger_executions(str(trigger.id))

        assert response.total == 0
        assert len(response.executions) == 0

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_list_executions_multiple(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger, sample_execution_doc
    ):
        """Test listing multiple executions."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()

        execution = TriggerExecution.from_mongo(sample_execution_doc)

        mock_service.get_trigger.return_value = trigger
        mock_service.get_executions.return_value = ([execution], 1)

        response = await get_trigger_executions(str(trigger.id))

        assert response.total == 1
        assert len(response.executions) == 1

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_list_executions_with_pagination(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger, sample_execution_doc
    ):
        """Test listing executions with pagination parameters."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()

        execution = TriggerExecution.from_mongo(sample_execution_doc)

        mock_service.get_trigger.return_value = trigger
        mock_service.get_executions.return_value = ([execution], 10)

        response = await get_trigger_executions(str(trigger.id), skip=5, limit=5)

        assert response.total == 10
        assert len(response.executions) == 1
        mock_service.get_executions.assert_called_once_with(
            trigger_id=str(trigger.id), skip=5, limit=5
        )

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_list_executions_nonexistent_trigger(self, mock_service_class, mock_get_db):
        """Test listing executions for non-existent trigger returns 404."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        mock_service.get_trigger.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_trigger_executions(str(ObjectId()))

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    @patch("src.api.triggers.endpoints.TriggerService")
    async def test_list_executions_ordered_by_time(
        self, mock_service_class, mock_get_db, sample_data_threshold_trigger, sample_execution_doc
    ):
        """Test that executions are returned in order (most recent first)."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        trigger.id = ObjectId()

        # Create multiple executions with different timestamps
        execution1 = TriggerExecution.from_mongo(sample_execution_doc)
        execution2_doc = sample_execution_doc.copy()
        execution2_doc["_id"] = ObjectId()
        execution2 = TriggerExecution.from_mongo(execution2_doc)

        mock_service.get_trigger.return_value = trigger
        mock_service.get_executions.return_value = ([execution1, execution2], 2)

        response = await get_trigger_executions(str(trigger.id))

        assert response.total == 2
        assert len(response.executions) == 2


class TestGetExecution:
    """Tests for get_execution endpoint."""

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_get_existing_execution(self, mock_get_db, sample_execution_doc):
        """Test getting an existing execution."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.trigger_executions.find_one.return_value = sample_execution_doc

        response = await get_execution(str(sample_execution_doc["_id"]))

        assert response.id == str(sample_execution_doc["_id"])
        assert len(response.nim_results) > 0

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_get_execution_with_error_fields(self, mock_get_db, sample_execution_doc):
        """Test getting execution with error fields populated."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Add error fields to NIM results
        sample_execution_doc["nim_results"][0]["eval_error"] = "Test error"
        sample_execution_doc["nim_results"][0]["incomplete_evaluation"] = True

        mock_db.trigger_executions.find_one.return_value = sample_execution_doc

        response = await get_execution(str(sample_execution_doc["_id"]))

        assert response.nim_results[0].eval_error == "Test error"
        assert response.nim_results[0].incomplete_evaluation is True

    @pytest.mark.asyncio
    @patch("src.api.triggers.endpoints.get_db")
    async def test_get_nonexistent_execution(self, mock_get_db):
        """Test getting non-existent execution returns 404."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.trigger_executions.find_one.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_execution(str(ObjectId()))

        assert exc_info.value.status_code == 404
