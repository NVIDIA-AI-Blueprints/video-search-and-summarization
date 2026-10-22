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

"""Tests for MongoDBScheduler."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from bson import ObjectId
from celery.beat import ScheduleEntry

from src.tasks.triggers.scheduler import MongoDBScheduler


class TestMongoDBScheduler:
    """Tests for MongoDBScheduler class."""

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_scheduler_initialization(self, mock_get_db, mock_init_db):
        """Test scheduler initializes correctly."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Create minimal app mock
        app = MagicMock()

        scheduler = MongoDBScheduler(app=app)

        assert scheduler.db == mock_db
        assert scheduler.refresh_interval == 60
        mock_init_db.assert_called_once()

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_scheduler_custom_refresh_interval(self, mock_get_db, mock_init_db):
        """Test scheduler with custom refresh interval."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        app = MagicMock()

        scheduler = MongoDBScheduler(app=app, refresh_interval=120)

        assert scheduler.refresh_interval == 120

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_setup_schedule_loads_triggers(self, mock_get_db, mock_init_db):
        """Test setup_schedule loads triggers from MongoDB."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Mock trigger documents
        trigger_docs = [
            {
                "_id": ObjectId(),
                "name": "test-trigger-1",
                "eval_schedule": "0 0 * * *",
                "enabled": True,
                "trigger_type": "data_threshold",
                "workload_id": "test",
                "client_id": "test",
                "job_type": "llm_job",
                "model_source": "base",
                "config_name": "test",
                "min_new_records": 100,
                "enable_data_ingestion": True,
                "max_consecutive_failures": 3,
                "latest_fine_tuned_models": {},
                "consecutive_failures": 0,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        ]

        mock_db.retraining_triggers.find.return_value = trigger_docs

        app = MagicMock()
        scheduler = MongoDBScheduler(app=app)
        scheduler.setup_schedule()

        # Should have created schedule entries
        assert len(scheduler._schedule) > 0

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_load_triggers_only_enabled(self, mock_get_db, mock_init_db):
        """Test only enabled triggers are loaded."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        app = MagicMock()
        _scheduler = MongoDBScheduler(app=app)

        # Should query for enabled=True triggers
        mock_db.retraining_triggers.find.assert_called()
        call_args = mock_db.retraining_triggers.find.call_args
        assert call_args[0][0] == {"enabled": True}

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_add_trigger_to_schedule(self, mock_get_db, mock_init_db):
        """Test adding a trigger to the schedule."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        trigger_doc = {
            "_id": ObjectId(),
            "name": "test-trigger",
            "eval_schedule": "0 0 * * *",
            "enabled": True,
            "trigger_type": "data_threshold",
            "workload_id": "test",
            "client_id": "test",
            "job_type": "llm_job",
            "model_source": "base",
            "config_name": "test",
            "min_new_records": 100,
            "enable_data_ingestion": True,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        mock_db.retraining_triggers.find.return_value = [trigger_doc]

        app = MagicMock()
        scheduler = MongoDBScheduler(app=app)
        scheduler.setup_schedule()

        # Check schedule entry was created
        trigger_id = str(trigger_doc["_id"])
        schedule_key = f"trigger-{trigger_id}"

        assert schedule_key in scheduler._schedule
        entry = scheduler._schedule[schedule_key]

        assert isinstance(entry, ScheduleEntry)
        assert entry.name == schedule_key
        assert entry.task == "tasks.execute_trigger"
        assert entry.args == (trigger_id,)

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_schedule_refresh_after_interval(self, mock_get_db, mock_init_db):
        """Test schedule refreshes after interval."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        mock_db.retraining_triggers.find.return_value = []

        app = MagicMock()
        scheduler = MongoDBScheduler(app=app, refresh_interval=0)
        scheduler.setup_schedule()

        # First access
        _ = scheduler.schedule

        # Should refresh (interval=0 means always refresh)
        _ = scheduler.schedule

        # find should be called multiple times due to refresh
        assert mock_db.retraining_triggers.find.call_count > 1

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_invalid_cron_skips_trigger(self, mock_get_db, mock_init_db):
        """Test invalid cron expression skips trigger."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Trigger with invalid cron
        trigger_doc = {
            "_id": ObjectId(),
            "name": "test-trigger",
            "eval_schedule": "invalid cron",
            "enabled": True,
            "trigger_type": "data_threshold",
            "workload_id": "test",
            "client_id": "test",
            "job_type": "llm_job",
            "model_source": "base",
            "config_name": "test",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        mock_db.retraining_triggers.find.return_value = [trigger_doc]

        app = MagicMock()
        scheduler = MongoDBScheduler(app=app)

        # Should not crash, just skip the trigger
        scheduler.setup_schedule()

        # Schedule should be empty (trigger skipped)
        assert len(scheduler._schedule) == 0

    @patch("src.tasks.triggers.scheduler.close_db")
    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_scheduler_close(self, mock_get_db, mock_init_db, mock_close_db):
        """Test scheduler closes database connection."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        app = MagicMock()
        scheduler = MongoDBScheduler(app=app)

        scheduler.close()

        mock_close_db.assert_called_once()


class TestSchedulerEdgeCases:
    """Tests for edge cases in scheduler."""

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_parse_error_logged_and_skipped(self, mock_get_db, mock_init_db):
        """Test trigger with parse error is logged and skipped."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Trigger document missing required fields
        bad_trigger_doc = {
            "_id": ObjectId(),
            "name": "bad-trigger",
            # Missing required fields
        }

        mock_db.retraining_triggers.find.return_value = [bad_trigger_doc]

        app = MagicMock()
        scheduler = MongoDBScheduler(app=app)

        # Should not crash
        scheduler.setup_schedule()

        # Schedule should be empty (trigger skipped)
        assert len(scheduler._schedule) == 0

    @patch("src.tasks.triggers.scheduler.init_db")
    @patch("src.tasks.triggers.scheduler.get_db")
    def test_multiple_triggers_loaded(self, mock_get_db, mock_init_db):
        """Test multiple triggers are loaded correctly."""
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        # Multiple trigger documents
        trigger_docs = [
            {
                "_id": ObjectId(),
                "name": f"test-trigger-{i}",
                "eval_schedule": "0 0 * * *",
                "enabled": True,
                "trigger_type": "data_threshold",
                "workload_id": "test",
                "client_id": "test",
                "job_type": "llm_job",
                "model_source": "base",
                "config_name": "test",
                "min_new_records": 100,
                "enable_data_ingestion": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            for i in range(5)
        ]

        mock_db.retraining_triggers.find.return_value = trigger_docs

        app = MagicMock()
        scheduler = MongoDBScheduler(app=app)
        scheduler.setup_schedule()

        # Should have 5 schedule entries
        assert len(scheduler._schedule) == 5
