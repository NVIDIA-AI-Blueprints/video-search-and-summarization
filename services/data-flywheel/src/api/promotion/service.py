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

"""Service layer for promotion (HITL) operations."""

from datetime import datetime

from pymongo.database import Database

from src.api.promotion.models import PromotionRecord
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.service")

PROMOTIONS_COLLECTION = "promotions"

#: Promotion states no transition may leave. Canonical definition — the
#: Promotion Controller (src/tasks/promotion/controller.py) imports and
#: re-exports it, keeping the dependency one-directional (tasks -> api).
TERMINAL_STATES: frozenset[str] = frozenset({"promoted", "rejected", "rolled_back", "failed"})


def close_out_promotions(db: Database, flywheel_run_id: str, note: str) -> int:
    """Force-fail every non-terminal promotion of a flywheel run; return the count.

    Public seam for the cleanup paths (worker shutdown/startup sweeps, cancel):
    once the parent run is cancelled, its in-flight promotions must not be
    resumable by the beats. Idempotent — terminal records are never touched.
    """
    result = db[PROMOTIONS_COLLECTION].update_many(
        {
            "flywheel_run_id": str(flywheel_run_id),
            "state": {"$nin": list(TERMINAL_STATES)},
        },
        {
            "$set": {
                "state": "failed",
                "pending_gate": None,
                "decision_note": note,
            }
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)


class PromotionConflictError(Exception):
    """Raised when a decision is requested on a promotion not awaiting approval."""


class PromotionService:
    """CRUD + approve/reject for promotion records."""

    def __init__(self, db: Database):
        self.db = db

    def list(
        self, state: str | None = None, skip: int = 0, limit: int = 50
    ) -> list[PromotionRecord]:
        """List promotions, optionally filtered by state."""
        filter_query: dict = {}
        if state:
            filter_query["state"] = state
        cursor = (
            self.db[PROMOTIONS_COLLECTION]
            .find(filter_query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        records = []
        for doc in cursor:
            record = PromotionRecord.from_mongo(doc)
            if record:
                records.append(record)
        return records

    def get(self, promotion_id: str) -> PromotionRecord | None:
        """Get a promotion by id."""
        doc = self.db[PROMOTIONS_COLLECTION].find_one({"promotion_id": promotion_id})
        return PromotionRecord.from_mongo(doc) if doc else None

    def create(self, record: PromotionRecord) -> PromotionRecord:
        """Insert a new promotion record (used by the controller / pipeline)."""
        self.db[PROMOTIONS_COLLECTION].insert_one(record.to_mongo())
        logger.info(f"Created promotion {record.promotion_id} for {record.model_name}")
        return record

    def _decide_pending(
        self,
        promotion_id: str,
        actor: str,
        new_state: str,
        note: str | None = None,
    ) -> PromotionRecord | None:
        """Atomically move a ``pending_approval`` record to ``new_state``.

        The state predicate is included in the update filter so exactly one
        concurrent caller wins (``matched_count == 1``); a loser sees
        ``matched_count == 0`` and either gets None (unknown id) or a conflict.
        Returns None if the promotion does not exist.
        """
        update: dict = {
            "state": new_state,
            "decided_by": actor,
            "decided_at": datetime.utcnow(),
        }
        if note is not None:
            update["decision_note"] = note
        result = self.db[PROMOTIONS_COLLECTION].update_one(
            {"promotion_id": promotion_id, "state": "pending_approval"},
            {"$set": update},
        )
        if result.matched_count == 0:
            current = self.get(promotion_id)
            if current is None:
                return None
            raise PromotionConflictError(
                f"Promotion {promotion_id} is not awaiting approval (state={current.state})"
            )
        return self.get(promotion_id)

    def _revert_to_pending(self, promotion_id: str, from_state: str) -> None:
        """Roll a decision back to ``pending_approval`` after an enqueue failure.

        The atomic decision predicate means a record stuck in ``approved`` /
        ``rejected`` can no longer be re-decided, so a broker publish failure
        would otherwise wedge the promotion. Reverting restores the gate.
        """
        self.db[PROMOTIONS_COLLECTION].update_one(
            {"promotion_id": promotion_id, "state": from_state},
            {
                "$set": {"state": "pending_approval"},
                "$unset": {"decided_by": "", "decided_at": "", "decision_note": ""},
            },
        )

    def approve(
        self, promotion_id: str, approved_by: str, note: str | None = None
    ) -> PromotionRecord | None:
        """Approve a candidate at its pending gate (design §4).

        Atomically records the decision, then routes to the controller
        (``on_approval`` → MLflow ``gate.<g>.decision=approved`` tag + enqueue
        ``advance``). Returns None if not found; raises PromotionConflictError if
        the record is not awaiting approval or the decision could not be dispatched.
        """
        record = self._decide_pending(promotion_id, approved_by, "approved", note)
        if not record:
            return None
        self._dispatch_decision(promotion_id, record.pending_gate, "approved", approved_by)
        logger.info(f"Approved promotion {promotion_id} by {approved_by}")
        return record

    def reject(
        self, promotion_id: str, rejected_by: str, reason: str | None = None
    ) -> PromotionRecord | None:
        """Reject a candidate at its pending gate (design §4).

        A candidate that has not served yet is archived; one already serving
        rolls back — the controller's ``on_approval`` decides which, off
        ``serving_role`` rather than the gate name (the first SERVING gate
        depends on the promotion's ``deployment_stages``).
        Returns None if not found; raises PromotionConflictError if the record is
        not awaiting approval or the decision could not be dispatched.
        """
        record = self._decide_pending(promotion_id, rejected_by, "rejected", reason)
        if not record:
            return None
        self._dispatch_decision(promotion_id, record.pending_gate, "rejected", rejected_by)
        logger.info(f"Rejected promotion {promotion_id} by {rejected_by}")
        return record

    def _dispatch_decision(
        self, promotion_id: str, gate: str | None, decision: str, actor: str
    ) -> None:
        """Route an approve/reject to the controller; revert the gate on failure.

        The atomic decision predicate has already moved the record out of
        ``pending_approval``; if dispatch fails (e.g. broker down) we revert so the
        gate is not wedged, mirroring the enqueue-failure recovery pattern.
        """
        from src.tasks.promotion.controller import PromotionController

        try:
            PromotionController().on_approval(promotion_id, gate or "", decision)
        except Exception as e:
            self._revert_to_pending(promotion_id, decision)
            logger.exception(
                f"Failed to dispatch {decision} for promotion {promotion_id}; "
                "reverted to pending_approval"
            )
            raise PromotionConflictError(
                f"Promotion {promotion_id} could not be dispatched ({decision}); retry decision"
            ) from e
