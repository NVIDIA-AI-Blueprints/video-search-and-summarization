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

"""/api/promotions approve/reject service logic (design §4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api.promotion.models import PromotionRecord
from src.api.promotion.service import PromotionConflictError, PromotionService


def _seed(service: PromotionService, **overrides) -> PromotionRecord:
    fields = {
        "model_name": "m",
        "candidate_version": "v2",
        "baseline_version": "v1",
        # use_case is REQUIRED on the record (no default): sibling use cases
        # share one promotions collection, so every writer states the one it
        # means. These fixtures are AOI promotions.
        "use_case": "aoi",
        "state": "pending_approval",
        "pending_gate": "canary",
    }
    fields.update(overrides)
    record = PromotionRecord(**fields)
    service.create(record)
    return record


def _service(fake_db) -> PromotionService:
    return PromotionService(fake_db)


def test_approve_moves_to_approved_and_dispatches(fake_db):
    service = _service(fake_db)
    rec = _seed(service)
    with patch("src.tasks.promotion.controller.PromotionController") as ctrl_cls:
        result = service.approve(rec.promotion_id, "alice", note="lgtm")
    assert result is not None
    assert result.pending_gate == "canary"
    doc = fake_db["promotions"].find_one({"promotion_id": rec.promotion_id})
    assert doc["state"] == "approved"
    assert doc["decided_by"] == "alice"
    ctrl_cls.return_value.on_approval.assert_called_once_with(
        rec.promotion_id, "canary", "approved"
    )


def test_reject_dispatches_rejected(fake_db):
    service = _service(fake_db)
    rec = _seed(service, pending_gate="deploy")
    with patch("src.tasks.promotion.controller.PromotionController") as ctrl_cls:
        service.reject(rec.promotion_id, "bob", reason="nope")
    ctrl_cls.return_value.on_approval.assert_called_once_with(
        rec.promotion_id, "deploy", "rejected"
    )
    assert fake_db["promotions"].find_one({"promotion_id": rec.promotion_id})["state"] == "rejected"


def test_decision_conflict_when_not_pending(fake_db):
    service = _service(fake_db)
    rec = _seed(service, state="promoting")
    with pytest.raises(PromotionConflictError):
        service.approve(rec.promotion_id, "alice")


def test_decision_returns_none_for_unknown(fake_db):
    service = _service(fake_db)
    assert service.approve("promo-missing", "alice") is None


def test_dispatch_failure_reverts_to_pending(fake_db):
    service = _service(fake_db)
    rec = _seed(service)
    with patch("src.tasks.promotion.controller.PromotionController") as ctrl_cls:
        ctrl_cls.return_value.on_approval.side_effect = RuntimeError("broker down")
        with pytest.raises(PromotionConflictError):
            service.approve(rec.promotion_id, "alice")
    # Reverted so the gate is not wedged.
    assert (
        fake_db["promotions"].find_one({"promotion_id": rec.promotion_id})["state"]
        == "pending_approval"
    )


def test_create_and_get(fake_db):
    service = _service(fake_db)
    rec = _seed(service, use_case="aoi_family_sibling")
    got = service.get(rec.promotion_id)
    assert got is not None
    assert got.use_case == "aoi_family_sibling"


def test_raw_locators_round_trip_through_mongo():
    # The durable raw locators the controller re-presigns from must survive a
    # to_mongo/from_mongo round-trip.
    rec = PromotionRecord(
        model_name="m",
        candidate_version="v2",
        use_case="aoi",
        artifact_locator="s3://aoi-artifacts/runs/v2/weights.onnx",
        spec_locator="s3://aoi-artifacts/runs/v2/spec.yaml",
    )
    doc = rec.to_mongo()
    assert doc["artifact_locator"] == "s3://aoi-artifacts/runs/v2/weights.onnx"
    assert doc["spec_locator"] == "s3://aoi-artifacts/runs/v2/spec.yaml"
    loaded = PromotionRecord.from_mongo(doc)
    assert loaded is not None
    assert loaded.artifact_locator == "s3://aoi-artifacts/runs/v2/weights.onnx"
    assert loaded.spec_locator == "s3://aoi-artifacts/runs/v2/spec.yaml"


def test_legacy_doc_without_raw_locators_still_loads():
    # Backward compat: an old promotion doc written before artifact_locator /
    # spec_locator existed must still deserialize, defaulting the new fields None.
    legacy_doc = {
        "_id": "abc123",
        "promotion_id": "promo-legacy",
        "model_name": "m",
        "candidate_version": "v1",
        # Stated explicitly: this test is about the LOCATOR fields being
        # optional, and use_case is required with no read-side shim, so a doc
        # omitting it would fail here for an unrelated reason.
        "use_case": "aoi",
        "artifact_uri": "https://store/runs/v1/weights.onnx?sig=old",
        "state": "promoting",
        "pending_gate": "pull",
    }
    loaded = PromotionRecord.from_mongo(legacy_doc)
    assert loaded is not None
    assert loaded.artifact_locator is None
    assert loaded.spec_locator is None
    assert loaded.artifact_uri == "https://store/runs/v1/weights.onnx?sig=old"


def test_cleanup_closes_out_non_terminal_promotions(fake_db):
    """Worker restart/shutdown cleanup must close a run's in-flight promotions
    so the beats / reconciliation sweep don't resume a cancelled run's rollout."""
    from bson import ObjectId

    from src.lib.flywheel.cleanup_manager import CleanupManager

    run_id = ObjectId()
    service = _service(fake_db)
    live = _seed(service, state="promoting", pending_gate="deploy")
    fake_db["promotions"].update_one(
        {"promotion_id": live.promotion_id}, {"$set": {"flywheel_run_id": str(run_id)}}
    )
    done = _seed(service, state="promoted")
    fake_db["promotions"].update_one(
        {"promotion_id": done.promotion_id}, {"$set": {"flywheel_run_id": str(run_id)}}
    )

    db_manager = MagicMock()
    db_manager.db = fake_db  # the injected manager's handle is used directly
    manager = CleanupManager(db_manager)
    manager._close_out_promotions(run_id)

    assert fake_db["promotions"].find_one({"promotion_id": live.promotion_id})["state"] == "failed"
    # Terminal records untouched.
    assert (
        fake_db["promotions"].find_one({"promotion_id": done.promotion_id})["state"] == "promoted"
    )


def test_online_eval_beat_uses_provider_seam(fake_db):
    """The online-eval beat obtains the evaluator via the provider seam."""
    from src.tasks.promotion import online_eval_beat as beat

    service = _service(fake_db)
    rec = _seed(service, state="promoting", serving_role="shadow", pending_gate="canary")

    fake_evaluator = MagicMock()
    provider = MagicMock()
    provider.evaluator.return_value = fake_evaluator

    online_eval = MagicMock()
    online_eval.evaluate.return_value = {"metrics": {"n": 5}, "verdict": "advance"}

    with (
        patch("src.tasks.promotion.controller._get_db", return_value=fake_db),
        patch("src.backends.factory.get_backend_provider", return_value=provider),
        patch("src.lib.eval_harness.OnlineEval", return_value=online_eval),
        # A decisive verdict enqueues advance; keep the Celery broker out of it.
        patch("src.tasks.promotion.tasks.advance_promotion") as adv,
    ):
        result = beat.online_eval_poll()

    provider.evaluator.assert_called_once_with(use_case="aoi")
    assert result["checked"] == 1
    doc = fake_db["promotions"].find_one({"promotion_id": rec.promotion_id})
    assert doc["verdict"] == "advance"
    assert doc["eval_metrics"] == {"n": 5}
    # Fresh decisive verdict (record had none) -> exactly one advance enqueued.
    adv.delay.assert_called_once_with(rec.promotion_id)
