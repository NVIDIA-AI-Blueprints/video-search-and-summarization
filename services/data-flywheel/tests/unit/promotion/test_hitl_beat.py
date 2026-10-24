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

"""HITL approval beat: MLflow tag/alias approval detection (design §4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.api.promotion.models import PromotionRecord
from src.api.promotion.service import PromotionService
from src.tasks.promotion import hitl_beat


def _fake_controller(fake_db) -> MagicMock:
    ctrl = MagicMock()
    ctrl.db = fake_db
    ctrl.mlflow = MagicMock()
    return ctrl


def _seed(fake_db, **overrides) -> PromotionRecord:
    fields = {
        "model_name": "m",
        "candidate_version": "v2",
        # use_case is REQUIRED on the record (no default) — two use cases share
        # one promotions collection. These fixtures are AOI promotions.
        "use_case": "aoi",
        "state": "pending_approval",
        "pending_gate": "canary",
    }
    fields.update(overrides)
    record = PromotionRecord(**fields)
    PromotionService(fake_db).create(record)
    return record


def test_tag_decision_approved_triggers_on_approval(fake_db):
    rec = _seed(fake_db)
    ctrl = _fake_controller(fake_db)
    ctrl.mlflow.get_version_by_alias.return_value = "3"
    ctrl.mlflow.get_promotion_tags.return_value = {"gate.canary.decision": "approved"}

    with (
        patch("src.tasks.promotion.controller.PromotionController", return_value=ctrl),
        patch("src.tasks.promotion.controller._get_db", return_value=fake_db),
    ):
        result = hitl_beat.hitl_poll()

    ctrl.on_approval.assert_called_once_with(rec.promotion_id, "canary", "approved")
    assert result["checked"] == 1
    assert result["decided"] == 1


def test_no_signal_leaves_record_untouched(fake_db):
    _seed(fake_db)
    ctrl = _fake_controller(fake_db)
    ctrl.mlflow.get_version_by_alias.return_value = None
    ctrl.mlflow.get_promotion_tags.return_value = {}

    with (
        patch("src.tasks.promotion.controller.PromotionController", return_value=ctrl),
        patch("src.tasks.promotion.controller._get_db", return_value=fake_db),
    ):
        result = hitl_beat.hitl_poll()

    ctrl.on_approval.assert_not_called()
    assert result["decided"] == 0


def test_alias_move_signals_approval(fake_db):
    rec = _seed(fake_db, pending_gate="deploy")
    ctrl = _fake_controller(fake_db)

    # No decision tag, but the approver moved @shadow onto the candidate version.
    def _by_alias(model_name, alias):
        return {"candidate": "3", "shadow": "3"}.get(alias)

    ctrl.mlflow.get_version_by_alias.side_effect = _by_alias
    ctrl.mlflow.get_promotion_tags.return_value = {}

    with (
        patch("src.tasks.promotion.controller.PromotionController", return_value=ctrl),
        patch("src.tasks.promotion.controller._get_db", return_value=fake_db),
    ):
        hitl_beat.hitl_poll()

    ctrl.on_approval.assert_called_once_with(rec.promotion_id, "deploy", "approved")
