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
import os

from pymongo import MongoClient
from pymongo.database import Database

# Global database connection
_client: MongoClient | None = None
_db: Database | None = None


def get_db() -> Database:
    """Get the MongoDB database instance."""
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


def init_db():
    """Initialize MongoDB connection."""
    global _client, _db

    # Return existing connection if available
    if _client is not None and _db is not None:
        try:
            # Verify connection is alive
            _client.admin.command("ping")
            return _db
        except Exception:
            # Connection dead, clean up and recreate
            close_db()

    mongodb_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    mongodb_db = os.getenv("MONGODB_DB", "flywheel")

    _client = MongoClient(mongodb_url)
    _db = _client[mongodb_db]

    # Create indexes
    _db.flywheel_runs.create_index("workload_id")
    _db.flywheel_runs.create_index("started_at")
    _db.configurations.create_index("name", unique=True)
    _db.retraining_triggers.create_index("name", unique=True)
    # Compound index supports both "trigger_id" only and "trigger_id + status" queries
    _db.trigger_executions.create_index([("trigger_id", 1), ("status", 1)])

    # Partial unique index: only one running/pending execution per trigger
    # This prevents race conditions when creating executions
    _db.trigger_executions.create_index(
        "trigger_id",
        unique=True,
        partialFilterExpression={"status": {"$in": ["running", "pending"]}},
        name="unique_active_execution_per_trigger",
    )

    # Promotions (HITL) indexes
    _db.promotions.create_index("promotion_id", unique=True)
    _db.promotions.create_index([("created_at", -1)])
    # Compound index serves list() queries that filter by state and sort by created_at.
    _db.promotions.create_index([("state", 1), ("created_at", -1)])
    _db.promotions.create_index("model_name")

    # Model deployments indexes
    _db.model_deployments.create_index("status")
    _db.model_deployments.create_index("platform")
    _db.model_deployments.create_index("created_at")

    # Partial unique index for DataRobot: prevent duplicate active deployments
    # to the same deployment_id (only one pending/deploying at a time)
    _db.model_deployments.create_index(
        [("platform", 1), ("platform_config.deployment_id", 1)],
        unique=True,
        partialFilterExpression={
            "status": {"$in": ["pending", "deploying"]},
            "platform_config.deployment_id": {"$exists": True},
        },
        name="unique_active_datarobot_deployment",
    )

    # Partial unique index for other platforms (NMP, etc.): prevent duplicate
    # active deployments to the same deployment_name
    _db.model_deployments.create_index(
        [("platform", 1), ("platform_config.deployment_name", 1)],
        unique=True,
        partialFilterExpression={
            "status": {"$in": ["pending", "deploying"]},
            "platform_config.deployment_name": {"$exists": True},
        },
        name="unique_active_named_deployment",
    )

    return _db


def close_db():
    """Close the MongoDB connection."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
    # Reset the database reference as well
    _db = None
