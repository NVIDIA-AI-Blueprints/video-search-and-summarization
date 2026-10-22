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

"""MongoDB-backed Celery Beat scheduler for dynamic trigger management.

This scheduler loads trigger schedules from MongoDB instead of static Python code,
allowing triggers to be added/updated/removed without restarting Celery Beat.

Uses SYNCHRONOUS pymongo since Celery Beat is synchronous.
"""

import logging
from datetime import datetime

from celery.beat import ScheduleEntry, Scheduler

from src.api.db import close_db, get_db, init_db
from src.api.triggers.models import RetrainingTrigger
from src.tasks.triggers.cron_utils import crontab_from_string

logger = logging.getLogger(__name__)


class MongoDBScheduler(Scheduler):
    """
    Celery Beat scheduler that loads schedules from MongoDB.

    This allows dynamic trigger management without Celery Beat restarts.
    Uses synchronous pymongo (not motor) since Celery Beat is synchronous.
    """

    def __init__(self, *args, **kwargs):
        """Initialize MongoDB scheduler."""
        self._schedule = {}
        self._last_timestamp = None

        # Refresh interval (seconds) - how often to check MongoDB for changes
        self.refresh_interval = kwargs.pop("refresh_interval", 60)  # Default: 60 seconds

        # Initialize MongoDB connection once (same pattern as Celery workers)
        init_db()
        self.db = get_db()
        logger.info("MongoDB connection initialized")

        super().__init__(*args, **kwargs)

        logger.info("MongoDBScheduler initialized")

    def setup_schedule(self):
        """Load initial schedule from MongoDB."""
        logger.info("Setting up MongoDB schedule...")

        # Load static beat_schedule entries (e.g. AOI promotion poll tasks)
        # configured in src/celery_app.py, then layer dynamic triggers on top.
        self._load_static_schedule()

        # Load triggers from MongoDB
        self._load_triggers_from_db()

        self._last_timestamp = datetime.utcnow()

    def _load_static_schedule(self):
        """Load static entries from ``app.conf.beat_schedule`` (non-trigger tasks).

        Dynamic retraining triggers are keyed ``trigger-*`` and managed from
        MongoDB; any other configured entries (interval-based promotion polls)
        are seeded here and preserved across MongoDB refreshes.
        """
        beat_schedule = self.app.conf.beat_schedule or {}
        for name, entry in beat_schedule.items():
            if isinstance(entry, ScheduleEntry):
                self._schedule[name] = entry
            else:
                self._schedule[name] = ScheduleEntry(
                    name=name,
                    task=entry["task"],
                    schedule=entry["schedule"],
                    args=entry.get("args", ()),
                    kwargs=entry.get("kwargs", {}),
                    options=entry.get("options", {}),
                )
        logger.info(f"Loaded {len(beat_schedule)} static beat_schedule entries")

    def _load_triggers_from_db(self):
        """Load all enabled triggers from MongoDB and create schedule entries."""
        try:
            # Query all enabled triggers (synchronous)
            cursor = self.db.retraining_triggers.find({"enabled": True})

            triggers = []
            for doc in cursor:
                try:
                    trigger = RetrainingTrigger.from_mongo(doc)
                    if trigger:
                        triggers.append(trigger)
                except Exception as e:
                    logger.error(f"Failed to parse trigger {doc.get('_id')}: {e}")

            logger.info(f"Loaded {len(triggers)} enabled triggers from MongoDB")

            # Clear only dynamic trigger entries; preserve static beat_schedule
            # entries (e.g. AOI promotion poll tasks) seeded at setup.
            for key in [k for k in self._schedule if k.startswith("trigger-")]:
                del self._schedule[key]

            # Create schedule entries for each trigger
            for trigger in triggers:
                try:
                    self._add_trigger_to_schedule(trigger)
                except Exception as e:
                    logger.error(f"Failed to add trigger {trigger.name} to schedule: {e}")

            logger.info(f"Schedule updated with {len(self._schedule)} entries")

        except Exception as e:
            logger.error(f"Failed to load triggers from MongoDB: {e}", exc_info=True)

    def _add_trigger_to_schedule(self, trigger: RetrainingTrigger):
        """
        Add a single trigger to the schedule.

        Args:
            trigger: Trigger configuration
        """
        # Convert cron expression to Celery crontab
        try:
            schedule = crontab_from_string(trigger.eval_schedule)
        except Exception:
            logger.error(
                f"Invalid cron expression for trigger {trigger.name}: {trigger.eval_schedule}"
            )
            raise

        # Create schedule entry
        entry = ScheduleEntry(
            name=f"trigger-{trigger.id}",
            task="tasks.execute_trigger",
            schedule=schedule,
            args=(str(trigger.id),),
            kwargs={},
            options={"queue": "celery"},
        )

        self._schedule[f"trigger-{trigger.id}"] = entry
        logger.debug(f"Added trigger to schedule: {trigger.name} ({trigger.eval_schedule})")

    @property
    def schedule(self):
        """Return the current schedule."""
        # Periodically refresh schedule from MongoDB
        now = datetime.utcnow()

        if (
            self._last_timestamp is None
            or (now - self._last_timestamp).total_seconds() > self.refresh_interval
        ):
            logger.debug(f"Refreshing schedule from MongoDB (interval: {self.refresh_interval}s)")
            self._load_triggers_from_db()
            self._last_timestamp = now

        return self._schedule

    def close(self):
        """Close MongoDB connection."""
        close_db()
        super().close()


def get_mongodb_scheduler(app):
    """
    Factory function to create MongoDB scheduler.

    Usage in celery_app.py:
        from src.tasks.triggers.scheduler import get_mongodb_scheduler
        celery_app.conf.beat_scheduler = 'src.tasks.triggers.scheduler:MongoDBScheduler'
    """
    return MongoDBScheduler(app)
