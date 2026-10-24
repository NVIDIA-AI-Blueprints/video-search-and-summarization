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

"""Celery wrappers delegating to the PromotionController (mirrors deployment tasks)."""

from src.celery_app import celery_app
from src.log_utils import setup_logging
from src.tasks.promotion.controller import PromotionController

logger = setup_logging("data_flywheel.promotion.tasks")


# Transition tasks retry with exponential backoff: transient failures
# (control-plane blips, registry hiccups, retryable BackendErrors re-raised by
# the controller) must not strand a promotion — backend applies are
# idempotency-keyed, so re-execution is safe. A record that exhausts retries is
# picked up again by the online beat's reconciliation sweep.
_RETRY_KWARGS = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_backoff_max": 300,
    "retry_jitter": True,
    "max_retries": 5,
}


@celery_app.task(name="tasks.promotion.advance", **_RETRY_KWARGS)
def advance_promotion(promotion_id: str) -> dict:
    """Advance a promotion past its current gate."""
    PromotionController().advance(promotion_id)
    return {"promotion_id": promotion_id, "action": "advance"}


@celery_app.task(name="tasks.promotion.rollback", **_RETRY_KWARGS)
def rollback_promotion(promotion_id: str) -> dict:
    """Roll a promotion back to baseline."""
    PromotionController().rollback(promotion_id)
    return {"promotion_id": promotion_id, "action": "rollback"}
