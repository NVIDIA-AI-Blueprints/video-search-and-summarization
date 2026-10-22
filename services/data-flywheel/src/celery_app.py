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

"""Shared Celery app configuration for all tasks."""

import os

from celery import Celery
from celery.schedules import schedule

# Initialize Celery
celery_app = Celery(
    "llm_api",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Prevent Redis from re-queuing long-running tasks
    broker_transport_options={
        "visibility_timeout": 36000,  # 10 hours in seconds (longer than max workflow time)
    },
    # Tell Celery where to find tasks
    imports=(
        "src.tasks.tasks",
        "src.tasks.deft_tasks",
        "src.tasks.deployment.tasks",
        "src.tasks.promotion.tasks",
        "src.tasks.promotion.hitl_beat",
        "src.tasks.promotion.online_eval_beat",
        "src.tasks.promotion.reclaim_beat",
    ),
    # Celery Beat configuration for automatic triggers
    beat_scheduler="src.tasks.triggers.scheduler:MongoDBScheduler",
    beat_schedule={
        # Dynamic retraining triggers are loaded from MongoDB by MongoDBScheduler.
        # The two promotion poll tasks are static, interval-based entries.
        "promotion-hitl-poll": {
            "task": "tasks.promotion.hitl_poll",
            "schedule": schedule(run_every=30.0),
        },
        "promotion-online-eval-poll": {
            "task": "tasks.promotion.online_eval_poll",
            "schedule": schedule(run_every=60.0),
        },
        # Hourly, not minutely: this is backlog drainage, not a control loop.
        # The task's FIRST action is to read `disk_reclaim.enabled` and return,
        # so an off deployment costs one config read an hour. Cadence is not a
        # knob -- what bounds this thing is volume per tick, not how often we look.
        "promotion-disk-reclaim": {
            "task": "tasks.promotion.disk_reclaim",
            "schedule": schedule(run_every=3600.0),
        },
    },
    # How often Beat checks for due tasks (default 300s = 5min, set to 30s for responsive scheduling)
    beat_max_loop_interval=30,
)
