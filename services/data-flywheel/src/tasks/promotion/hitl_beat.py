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

"""Periodic HITL approval poll (Celery beat task, design §4).

Scans promotions in ``state=pending_approval`` and looks for an out-of-band
approver decision in MLflow — either the expected next alias moved onto the
candidate (``@shadow``/``@canary``/``@production``) or a ``gate.<g>.decision``
tag. On a decision it drives ``PromotionController.on_approval`` (approve →
advance, reject → archive/rollback). The push alternative is
``POST /api/promotions/{id}/approve|reject``, which calls the same controller.
"""

from src.celery_app import celery_app
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.hitl_beat")

# Gate -> alias whose presence on the candidate signals approval of that gate.
# (pull is pre-serving and has no alias; it is approved only via tag or the API.)
_GATE_APPROVAL_ALIAS: dict[str, str] = {
    "deploy": "shadow",
    "canary": "canary",
    "full": "production",
}


@celery_app.task(name="tasks.promotion.hitl_poll")
def hitl_poll() -> dict:
    """Scan promotions awaiting approval and check MLflow for an approval signal."""
    from src.api.promotion.service import PROMOTIONS_COLLECTION
    from src.tasks.promotion.controller import PromotionController, _get_db

    # Cheap no-op when nothing awaits approval: query FIRST, and only build the
    # controller (deployment-backend + MLflow clients) when there is work —
    # this beat fires every 30s for the lifetime of the deployment.
    db = _get_db()
    pending = list(db[PROMOTIONS_COLLECTION].find({"state": "pending_approval"}))
    if not pending:
        return {"task": "hitl_poll", "checked": 0, "decided": 0}

    controller = PromotionController()

    checked = 0
    decided = 0
    for doc in pending:
        checked += 1
        promotion_id = doc.get("promotion_id")
        gate = doc.get("pending_gate")
        model_name = doc.get("model_name")
        candidate_version = doc.get("candidate_version")
        if not gate or not model_name:
            continue

        decision = _detect_decision(controller, model_name, candidate_version, gate)
        if decision is None:
            continue
        try:
            controller.on_approval(promotion_id, gate, decision)
            decided += 1
            logger.info("hitl_poll: %s gate=%s decision=%s", promotion_id, gate, decision)
        except Exception:
            logger.exception("hitl_poll: on_approval failed for %s", promotion_id)

    return {"task": "hitl_poll", "checked": checked, "decided": decided}


def _detect_decision(controller, model_name: str, candidate_version: str, gate: str) -> str | None:
    """Return 'approved'/'rejected' from MLflow signals, or None if undecided."""
    # 1. Explicit gate-decision tag on the candidate model version.
    try:
        version = controller.mlflow.get_version_by_alias(model_name, "candidate")
        if version is not None:
            tags = controller.mlflow.get_promotion_tags(model_name, version)
            tag_decision = tags.get(f"gate.{gate}.decision")
            if tag_decision in ("approved", "rejected"):
                return tag_decision
    except Exception:
        logger.exception("hitl_poll: reading MLflow tags failed for %s", model_name)

    # 2. Alias-move approval: an approver moved the expected next alias onto the
    #    candidate version (e.g. @shadow for the deploy gate).
    alias = _GATE_APPROVAL_ALIAS.get(gate)
    if alias is not None:
        try:
            aliased = controller.mlflow.get_version_by_alias(model_name, alias)
            candidate_mlflow = controller.mlflow.get_version_by_alias(model_name, "candidate")
            if aliased is not None and aliased in (candidate_version, candidate_mlflow):
                return "approved"
        except Exception:
            logger.exception("hitl_poll: reading MLflow alias failed for %s", model_name)

    return None
