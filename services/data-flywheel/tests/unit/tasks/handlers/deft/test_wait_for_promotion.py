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
"""Unit tests for the ``wait_for_promotion`` DEFT stage.

Stage 7 of the shared 8-stage DEFT DAG, so it runs identically for every member
of the family (``aoi_job`` / ``its_job``) — nothing here branches on job type.

This stage blocks the chain until the promotion reaches a terminal state,
which — because the parent dispatcher blocks on the whole chain via a
concurrency-1 slot — serializes entire flywheel runs. The tests drive
``PromotionService.get`` via ``side_effect`` and patch ``time.sleep`` so the
(unbounded) poll loop runs instantly, exercising: immediate-terminal return,
looping until terminal, the missing-``promotion_id`` skip, the vanished-record
guard, cancellation, and transient-vs-persistent DB error tolerance.
"""

from unittest.mock import MagicMock, patch

from src.tasks import deft_tasks

_FW_ID = "507f1f77bcf86cd799439011"
_PROMO_ID = "promo-123"


def _record(state: str) -> MagicMock:
    """A promotion record stand-in exposing just the ``.state`` the stage reads."""
    rec = MagicMock()
    rec.state = state
    return rec


def _patched_service(get_side_effect):
    """Patch the PromotionService seam so ``.get`` follows ``get_side_effect``.

    Returns the (service_ctx_managers, service_mock) so a test can assert on how
    ``.get`` was called. ``_get_db`` is patched too so the stage never touches a
    real Mongo during ``PromotionService(_get_db())``.
    """
    service_instance = MagicMock()
    service_instance.get.side_effect = get_side_effect
    service_cls = MagicMock(return_value=service_instance)
    return service_cls, service_instance


class TestWaitForPromotion:
    def test_returns_immediately_when_already_terminal(self, patched_sleep):
        service_cls, service = _patched_service([_record("promoted")])
        prev = {"flywheel_run_id": _FW_ID, "promotion_id": _PROMO_ID}
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patched_sleep() as sleep,
        ):
            ctx = deft_tasks.wait_for_promotion(prev)

        assert ctx["promotion_state"] == "promoted"
        service.get.assert_called_once_with(_PROMO_ID)
        sleep.assert_not_called()  # terminal on the first read — never waited

    def test_loops_until_terminal(self, patched_sleep):
        # [promoting, promoting, promoted] -> two sleeps, then returns.
        service_cls, service = _patched_service(
            [_record("promoting"), _record("promoting"), _record("promoted")]
        )
        prev = {"flywheel_run_id": _FW_ID, "promotion_id": _PROMO_ID}
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patched_sleep() as sleep,
        ):
            ctx = deft_tasks.wait_for_promotion(prev)

        assert ctx["promotion_state"] == "promoted"
        assert service.get.call_count == 3
        assert sleep.call_count == 2

    def test_missing_promotion_id_passes_through_without_polling(self, patched_sleep):
        service_cls, service = _patched_service([_record("promoted")])
        prev = {"flywheel_run_id": _FW_ID}  # no promotion_id
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patched_sleep() as sleep,
        ):
            ctx = deft_tasks.wait_for_promotion(prev)

        assert "promotion_state" not in ctx
        service.get.assert_not_called()
        sleep.assert_not_called()

    def test_missing_record_returns_unknown(self, patched_sleep):
        # The record vanished under us -> don't wedge the chain; end the wait.
        service_cls, service = _patched_service([None])
        prev = {"flywheel_run_id": _FW_ID, "promotion_id": _PROMO_ID}
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patched_sleep() as sleep,
        ):
            ctx = deft_tasks.wait_for_promotion(prev)

        assert ctx["promotion_state"] == "unknown"
        sleep.assert_not_called()

    def test_cancellation_stops_the_wait(self, patched_sleep):
        # A cancel landing DURING the (unbounded) wait must abort the loop, not
        # spin forever. _run_stage turns FlywheelCancelledError into a skip with
        # `error` stamped; the cancel path already force-failed the promotion.
        from src.lib.flywheel.cancellation import FlywheelCancelledError

        # First honor_cancellation call is _run_stage's stage-entry check (pass);
        # the second is the handler's first in-loop check (cancel).
        checks = iter([None, FlywheelCancelledError(_FW_ID)])

        def _honor(_run_id):
            result = next(checks)
            if result:
                raise result

        service_cls, service = _patched_service([_record("promoting")])
        prev = {"flywheel_run_id": _FW_ID, "promotion_id": _PROMO_ID}
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patch("src.tasks.handlers.deft.handlers.honor_cancellation", side_effect=_honor),
            patched_sleep(),
        ):
            ctx = deft_tasks.wait_for_promotion(prev)

        assert "cancelled" in ctx["error"]
        service.get.assert_not_called()  # cancelled before the first read

    def test_transient_read_errors_are_tolerated(self, patched_sleep):
        # A brief DB blip during a long wait must not fail the stage.
        service_cls, service = _patched_service(
            [ConnectionError("blip"), ConnectionError("blip"), _record("rolled_back")]
        )
        prev = {"flywheel_run_id": _FW_ID, "promotion_id": _PROMO_ID}
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patched_sleep(),
        ):
            ctx = deft_tasks.wait_for_promotion(prev)

        assert ctx["promotion_state"] == "rolled_back"

    def test_persistent_read_failure_marks_run_failed(self, monkeypatch, patched_sleep):
        from src.tasks.handlers.deft import handlers

        monkeypatch.setattr(handlers, "PROMOTION_STATUS_ERROR_TOLERANCE", 2)
        service_cls, _ = _patched_service([ConnectionError("down"), ConnectionError("down")])
        db = MagicMock()
        prev = {"flywheel_run_id": _FW_ID, "promotion_id": _PROMO_ID}
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patch("src.api.db_manager.get_db_manager", return_value=db),
            patched_sleep(),
        ):
            ctx = deft_tasks.wait_for_promotion(prev)

        assert "consecutive" in ctx["error"]
        db.mark_flywheel_run_error.assert_called_once()

    def test_skip_on_error_passes_through_without_polling(self, patched_sleep):
        # NMP-parity: a ctx already carrying `error` is a graceful pass-through.
        service_cls, service = _patched_service([_record("promoted")])
        errored = {"flywheel_run_id": _FW_ID, "error": "DEFT stage 'x' failed: boom"}
        with (
            patch("src.tasks.promotion.controller._get_db"),
            patch("src.api.promotion.service.PromotionService", service_cls),
            patched_sleep(),
        ):
            ctx = deft_tasks.wait_for_promotion(dict(errored))

        assert ctx["error"] == errored["error"]
        assert "promotion_state" not in ctx
        service.get.assert_not_called()
