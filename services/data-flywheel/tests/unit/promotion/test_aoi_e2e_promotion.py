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
"""End-to-end AOI pipeline test through the REAL Promotion Controller logic.

Drives the full flow a ``POST /jobs`` (job_type=aoi_job) triggers — the 7-stage
chain (initialize → snapshot → DEFT train → offline gate → register → promote →
finalize) followed by the promotion state machine (pull → deploy(shadow) →
canary steps → full) — with only the EXTERNAL surfaces faked, per their
contracts:

- Storage Backend / DEFT service: provider-seam fakes returning contract shapes.
- Model Manager: a stateful in-process HTTP fake behind the REAL
  ``ModelManagerBackend`` client (``requests`` patched), so request bodies,
  idempotency keys and response parsing are exercised for every gate.
- MLflow: a stateful alias/tag fake (the alias trail is asserted).
- Mongo: the shared ``FakeDB`` double (promotion records).
- Celery: ``advance_promotion.delay`` / ``rollback_promotion.delay`` captured
  and drained by a worker-loop simulator; the two beats
  (``online_eval_poll`` / ``hitl_poll``) are invoked as beat ticks.

The offline gate and online verdicts run the REAL ``DeftAOIEvaluator`` metric
math (structured ``kpi_target`` constraints; collected-sample verdicts).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.tasks.promotion.controller import TERMINAL_STATES, PromotionController
from src.tasks.promotion.online_eval_beat import online_eval_poll

# The fakes, the harness and the fixture live in `_e2e_fakes` so the ITS suite
# can drive the SAME harness; everything below is a pure re-export.
from ._e2e_fakes import (
    BASELINE,
    CANDIDATE,
    CANDIDATE_RUN_ID,
    FAILING_METRICS,
    MODEL,
    PASSING_METRICS,
    E2EHarness,
    FakeModelManagerHTTP,
    FakeStorageBackendHTTP,
    _aoi_samples,
    _run,
)
from ._e2e_fakes import harness_factory as _harness_factory

# Bound by ASSIGNMENT: an imported fixture makes every
# `def test_x(harness_factory)` parameter read as F811, and the binding is what
# registers the fixture for this module.
harness_factory = _harness_factory

# -- Scenarios --------------------------------------------------------------- #


class TestHappyPathToPromoted:
    def test_full_rollout(self, harness_factory):
        h = harness_factory()
        ctx, record = _run(h)

        assert ctx["gate_passed"] is True
        assert ctx["finalized"] is True
        assert ctx["promotion_id"] == record.promotion_id
        # RUNNING -> PROMOTING -> COMPLETED, the last step the controller's to
        # take once the promotion is terminal.
        from src.api.schemas import FlywheelRunStatus

        statuses = [c.args[1] for c in h.flywheel_db.update_flywheel_run_status.call_args_list]
        assert statuses == [FlywheelRunStatus.RUNNING, FlywheelRunStatus.PROMOTING]
        h.flywheel_db.mark_flywheel_run_completed.assert_called_once()

        assert record.state == "promoted"
        assert record.serving_role == "default"
        assert record.pending_gate is None
        # F3: both are serving integers — the baseline from the Mongo records (or
        # @production on a first rollout), the candidate minted by register.
        # Neither is the DEFT run id, which is lineage only.
        assert record.baseline_version == BASELINE  # the serving integer "1"
        assert record.candidate_version == CANDIDATE  # the serving integer "2"
        assert record.deft_run_id == CANDIDATE_RUN_ID  # lineage: the DEFT run id

        assert int(record.candidate_version) and int(record.baseline_version)

        pulls = h.mm.bodies("POST /v1/models/pull")
        assert [p["version"] for p in pulls] == [CANDIDATE]
        assert pulls[0]["idempotency_key"] == f"{record.promotion_id}:pull"

        applies = h.mm.bodies("POST /v1/models/apply")
        assert [(a["control"], a.get("canary_weight")) for a in applies] == [
            ("shadow", None),
            ("canary", 0.05),
            ("canary", 0.25),
            ("full_switch", None),
        ]
        assert applies[1]["idempotency_key"] == f"{record.promotion_id}:canary:0"

        # Unload, not cleanup: the baseline's weights stay on the volume, so a
        # rollback is a routing change rather than a re-download.
        unloads = h.mm.bodies("POST /v1/models/unload")
        assert [(c["model_name"], c["version"]) for c in unloads] == [(MODEL, BASELINE)]
        assert h.mm.bodies("POST /v1/models/cleanup") == []
        assert h.mm.default_version == CANDIDATE

        assert [a for (a, _v) in h.mlflow.alias_history if a != "production"] == [
            "candidate",
            "shadow",
            "canary",
        ]
        assert h.mlflow.aliases["production"] == CANDIDATE

    def test_offline_gate_blocks_bad_candidate(self, harness_factory):
        # NMP parity: the gate stamps the error and register/promotion skip, but
        # no task raises out of the chain.
        h = harness_factory(metrics=FAILING_METRICS)
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()

        assert "gate failed" in ctx["error"]
        assert "registered" not in ctx and "promotion_id" not in ctx
        h.flywheel_db.mark_flywheel_run_error.assert_called_once()
        assert h.mlflow.aliases.get("candidate") is None  # never registered
        assert h.mm.calls == []  # promotion never started


class TestMLflowBestEffort:
    def test_promotion_proceeds_when_mlflow_unavailable(self, harness_factory):
        # F3: both the serving version and the baseline come from Mongo, so an
        # unavailable MLflow never blocks a promotion.
        from src.api.promotion.service import PROMOTIONS_COLLECTION

        h = harness_factory()

        h.db[PROMOTIONS_COLLECTION].insert_one(
            {
                "promotion_id": "prev-promo",
                "model_name": MODEL,
                "state": "promoted",
                "candidate_version": BASELINE,
            }
        )
        # MLflow down: the tracking write no-ops and no alias is readable.
        h.mlflow.register_candidate = lambda **kwargs: False
        h.mlflow.get_version_by_alias = lambda model_name, alias: None

        ctx, record = _run(h)

        assert record.state == "promoted"

        assert ctx["candidate_version"] == CANDIDATE
        assert ctx["registered"] is False  # MLflow recorded nothing

        assert record.baseline_version == BASELINE

        assert record.deft_run_id == CANDIDATE_RUN_ID

        assert [p["version"] for p in h.mm.bodies("POST /v1/models/pull")] == [CANDIDATE]


class TestOnlineRegressionRollsBack:
    def test_shadow_regression_rolls_back_to_baseline(self, harness_factory):
        # Passes offline, then floods false alarms on good units online.
        h = harness_factory(online_samples=_aoi_samples(200, candidate_correct=False))
        _ctx, record = _run(h)

        assert record.state == "rolled_back"
        assert record.serving_role == "default"
        assert record.verdict == "rollback"

        applies = h.mm.bodies("POST /v1/models/apply")

        assert applies[0]["control"] == "shadow"
        assert applies[-1] == {
            "model_name": MODEL,
            "version": BASELINE,
            "control": "full_switch",
            "idempotency_key": f"{record.promotion_id}:rollback",
        }
        # Evicted only after that full_switch converges: until it lands the
        # candidate is still the shadow arm. The whole list, so an unload of the
        # baseline cannot hide behind a membership test.
        assert all(a["control"] != "canary" for a in applies)
        assert h.mm.bodies("POST /v1/models/unload") == [
            {"model_name": MODEL, "version": CANDIDATE}
        ]
        # Unload touches no files: the weights wait for the reclaim sweep.
        assert h.mm.bodies("POST /v1/models/cleanup") == []
        assert BASELINE in h.mm.loaded
        assert CANDIDATE not in h.mm.loaded
        assert record.evicted_versions == [CANDIDATE]

        assert h.mlflow.aliases["production"] == BASELINE
        # Rolled back is not a successful flywheel iteration.
        h.flywheel_db.mark_flywheel_run_completed.assert_not_called()
        h.flywheel_db.mark_flywheel_run_error.assert_called_once()
        assert "rolled_back" in h.flywheel_db.mark_flywheel_run_error.call_args.args[1]


class TestSwapFailureAutoRollback:
    def test_canary_swap_failure_marks_failed_and_rolls_back(self, harness_factory):
        h = harness_factory()
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()
            pid = ctx["promotion_id"]
            # pull+deploy succeed; the first canary swap fails.
            h._drain_queues()
            h.mm.fail_next_apply = True
            record = h.drive(pid)

        assert record.state == "rolled_back"
        applies = h.mm.bodies("POST /v1/models/apply")
        assert applies[-1]["version"] == BASELINE
        assert applies[-1]["control"] == "full_switch"


class TestOnlineEvalDisabled:
    """A use case with no live feedback promotes on the offline gate alone."""

    def test_disabled_gate_promotes_without_any_online_verdict(self, harness_factory):
        h = harness_factory(
            policy_overrides={
                "promotion_policy": {
                    "hitl": {"pull": False, "deploy": False, "canary": False, "full": False},
                    "canary_steps": {"steps": [5, 25]},
                    "eval_harness": {"enabled": False},
                    "poll_interval_s": 0.0,
                    "poll_max_attempts": 1,
                    "auto_rollback_on_regression": True,
                }
            },
            # No samples at all: the point is that none are needed.
            online_samples=[],
        )
        _ctx, record = _run(h)

        assert record.state == "promoted"
        assert record.serving_role == "default"

        assert record.verdict is None
        # Not eval-gated, so the gates chain rather than waiting a beat interval
        # each: one tick starts the whole rollout.
        assert (
            h.beat_ticks <= 1
        ), f"disabled rollout took {h.beat_ticks} beat ticks; it should chain"
        applies = h.mm.bodies("POST /v1/models/apply")
        assert applies[0]["control"] == "shadow"
        assert applies[-1]["control"] == "full_switch"
        assert h.mlflow.aliases["production"] == CANDIDATE


class TestObservationClock:
    """serving_since is what makes a max_observation_window meaningful."""

    def test_serving_since_is_stamped_and_restarted_per_phase(self, harness_factory):
        h = harness_factory(online_samples=_aoi_samples(200, candidate_correct=True))
        stamps: list = []

        from src.api.promotion.service import PROMOTIONS_COLLECTION

        original = PromotionController._update

        def _capture(self, promotion_id, **fields):
            if "serving_since" in fields:
                stamps.append((fields.get("serving_role"), fields["serving_since"]))
            return original(self, promotion_id, **fields)

        with patch.object(PromotionController, "_update", _capture):
            _ctx, record = _run(h)

        assert record.state == "promoted"
        roles = [r for r, _ in stamps]
        # One clock per phase: shadow, then each canary step.
        assert roles[0] == "shadow"
        assert roles.count("canary") == len(
            h.job_config["promotion_policy"]["canary_steps"]["steps"]
        )
        # NOT asserting uniqueness: consecutive writes can land in the same
        # microsecond and would flake. The per-role counts above already prove a
        # fresh stamp per phase; what is left is that time moves forward.
        timestamps = [ts for _, ts in stamps]
        assert timestamps == sorted(timestamps), "the observation clock went backwards"
        doc = h.db[PROMOTIONS_COLLECTION].find_one({"promotion_id": record.promotion_id})
        assert doc.get("serving_since") is not None


class TestHitlApprovalGate:
    def test_full_gate_pauses_then_alias_approval_promotes(self, harness_factory):
        h = harness_factory(
            policy_overrides={
                "promotion_policy": {
                    "hitl": {"pull": False, "deploy": False, "canary": False, "full": True},
                    "canary_steps": {"steps": [5]},
                    "eval_harness": {
                        "min_sample_size": 10,
                        "online_target": [{"metric": "FAR_pct", "op": "<", "threshold": 5.0}],
                    },
                    "poll_interval_s": 0.0,
                    "poll_max_attempts": 1,
                    "auto_rollback_on_regression": True,
                }
            }
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()
            pid = ctx["promotion_id"]

            record = h.drive(pid, hitl=False)
            assert record.state == "pending_approval"
            assert record.pending_gate == "full"
            assert all(a["control"] != "full_switch" for a in h.mm.bodies("POST /v1/models/apply"))

            # Signalled via MLflow: the hitl beat sees @production move and the
            # machine resumes.
            h.mlflow.set_alias(MODEL, "production", CANDIDATE)
            record = h.drive(pid, hitl=True)

        assert record.state == "promoted"
        assert h.mm.bodies("POST /v1/models/apply")[-1]["control"] == "full_switch"

        assert h.mlflow.tags[CANDIDATE]["gate.full.decision"] == "approved"


class TestStarvedPromotionEscalates:
    """The Gap 3 claim, proven end to end: a hold can be rescued by a human.

    Before this, a starved promotion was unreachable by BOTH escape routes —
    ``advance()`` returns on a non-decisive verdict before the HITL pause can
    fire, and the approve API is atomically guarded on
    ``state == "pending_approval"``. Asserting only that the record *becomes*
    approvable would not show that approving it actually moves the rollout.
    """

    def test_timed_out_hold_escalates_and_a_human_approval_completes_it(self, harness_factory):
        h = harness_factory(
            policy_overrides={
                "promotion_policy": {
                    "hitl": {"pull": False, "deploy": False, "canary": False, "full": False},
                    "canary_steps": {"steps": [5]},
                    "eval_harness": {
                        # Only 3 samples exist, so 100 good units never arrive.
                        "min_sample_size": 100,
                        "online_target": [{"metric": "FAR_pct", "op": "<", "threshold": 5.0}],
                        # An hour, not a second: measured against the REAL clock,
                        # and the first `drive()`'s ticks outran a one-second
                        # deadline on slow CI. `_age_the_clock` should cross it.
                        "max_observation_window_s": 3600,
                        "on_timeout": "escalate",
                    },
                    "poll_interval_s": 0.0,
                    "poll_max_attempts": 1,
                    "auto_rollback_on_regression": True,
                }
            },
            online_samples=_aoi_samples(3, candidate_correct=True),
        )
        from contextlib import ExitStack

        from src.api.promotion.service import PROMOTIONS_COLLECTION

        def _age_the_clock(pid):
            """Pretend this serving phase has outlived its observation window."""
            h.db[PROMOTIONS_COLLECTION].update_one(
                {"promotion_id": pid},
                {"$set": {"serving_since": datetime.now(UTC) - timedelta(hours=2)}},
            )

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()
            pid = ctx["promotion_id"]

            # Starved, so the gate holds.
            record = h.drive(pid, hitl=False)
            assert record.state == "promoting"
            assert record.verdict == "hold"
            assert [a["control"] for a in h.mm.bodies("POST /v1/models/apply")] == ["shadow"]

            # Canary gate: escalate, then a human approves.
            _age_the_clock(pid)
            record = h.drive(pid, hitl=False)
            assert record.state == "pending_approval", "the hold was never escalated"
            assert record.pending_gate == "canary"

            # The approval signal is the alias for THE GATE THAT IS STUCK.
            h.mlflow.set_alias(MODEL, "canary", CANDIDATE)
            record = h.drive(pid, hitl=True)
            # The hold is rescued: the canary apply the verdict blocked happened.
            assert "canary" in [a["control"] for a in h.mm.bodies("POST /v1/models/apply")]

            # Full gate: still starved, so it escalates again.
            _age_the_clock(pid)
            record = h.drive(pid, hitl=False)
            assert record.state == "pending_approval"
            assert record.pending_gate == "full"

            h.mlflow.set_alias(MODEL, "production", CANDIDATE)
            record = h.drive(pid, hitl=True)

        assert record.state == "promoted"
        assert h.mm.bodies("POST /v1/models/apply")[-1]["control"] == "full_switch"

        assert h.mlflow.tags[CANDIDATE]["gate.canary.decision"] == "approved"
        assert h.mlflow.tags[CANDIDATE]["gate.full.decision"] == "approved"


class TestTimeoutAdvancePromotes:
    """`on_timeout: advance` must actually promote, not merely enqueue.

    The beat-level test mocks the task, so the controller never runs — and the
    verdict gate in ``advance()`` rejects a record still carrying ``hold``.
    Only driving the real state machine shows whether the rollout moves.
    """

    def test_timed_out_hold_promotes_on_offline_signal_alone(self, harness_factory):
        h = harness_factory(
            policy_overrides={
                "promotion_policy": {
                    "hitl": {"pull": False, "deploy": False, "canary": False, "full": False},
                    "canary_steps": {"steps": [5]},
                    "eval_harness": {
                        # Starved: 100 good units will never arrive.
                        "min_sample_size": 100,
                        "online_target": [{"metric": "FAR_pct", "op": "<", "threshold": 5.0}],
                        "max_observation_window_s": 1,
                        "on_timeout": "advance",
                    },
                    "poll_interval_s": 0.0,
                    "poll_max_attempts": 1,
                    "auto_rollback_on_regression": True,
                }
            },
            online_samples=_aoi_samples(3, candidate_correct=True),
        )
        from contextlib import ExitStack

        from src.api.promotion.service import PROMOTIONS_COLLECTION

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()
            pid = ctx["promotion_id"]

            # Age every phase past its window so each hold times out at once.
            def _age(*_a, **_k):
                h.db[PROMOTIONS_COLLECTION].update_one(
                    {"promotion_id": pid},
                    {"$set": {"serving_since": datetime.now(UTC) - timedelta(hours=2)}},
                )

            for _ in range(8):
                _age()
                record = h.drive(pid, hitl=False, max_ticks=4)
                if record.state in TERMINAL_STATES:
                    break

        assert (
            record.state == "promoted"
        ), f"on_timeout=advance did not promote (state={record.state}, verdict={record.verdict})"
        assert h.mm.bodies("POST /v1/models/apply")[-1]["control"] == "full_switch"
        # The verdict stays honestly 'hold' — nothing fabricates an 'advance'.
        doc = h.db[PROMOTIONS_COLLECTION].find_one({"promotion_id": pid})
        assert doc.get("timed_out_at") is not None, "the timeout is not on the audit trail"


class TestDeftHttpE2E:
    """E2E through the REAL DEFT backend: provider → wrappers → HTTP clients.

    Unlike the seam-level tests above, the Storage Backend and DEFT service are
    faked at the HTTP layer, so the real ``DeftDatastore`` snapshot polling, the
    real ``DeftTraining`` FineTuneRequest assembly (deft.config passthrough +
    snapshot/model injection), the real status normalization and results
    extraction are all on the tested path — then the promotion controller
    carries the candidate to promoted as before.
    """

    def test_full_rollout_through_real_deft_clients(self, harness_factory):
        h = harness_factory(real_deft_backend=True)
        ctx, record = _run(h)

        (snap_body,) = h.storage_http.bodies("POST", "/v1/snapshot")
        # Facets ride FLAT on the selection. `mode` is a DFW selection-strategy
        # marker, not a data facet, so it is not sent. `created_after` is required
        # by the Storage Backend snapshot contract.
        assert snap_body["selection"] == {"created_after": "1970-01-01T00:00:00Z"}
        assert "filters" not in snap_body["selection"]
        assert "limit" not in snap_body["selection"]

        assert ctx["snapshot_id"] == FakeStorageBackendHTTP.SNAPSHOT_ID
        assert ctx["snapshot_uri"] == FakeStorageBackendHTTP.PREFIX

        (ft_body,) = h.deft_http.bodies("POST", "/v1/fine-tune")
        assert ft_body["workflow"] == "aoi_active_learning"
        config = ft_body["config"]

        assert config["data_pool"]["path"] == FakeStorageBackendHTTP.PREFIX
        assert config["model"]["name"] == MODEL
        # Iterative fine-tuning: a @production version is deployed, so training
        # baselines on its checkpoint and the config's day-0 seed is superseded.
        assert config["model"]["checkpoint"] == f"s3://aoi-artifacts/runs/{BASELINE}/weights.onnx"
        # VERBATIM, including DEFT's own string convergence target, which is not
        # DFW's structured eval.kpi_target.
        assert config["pipeline"]["kpi_target"] == "FAR < 0.1% at recall=100%"
        assert config["training"]["epochs"] == 3

        # F3: deft_run_id and candidate_version are distinct identities.
        assert ctx["deft_run_id"] == CANDIDATE_RUN_ID
        assert ctx["candidate_version"] == CANDIDATE
        assert ctx["artifact_uri"] == f"s3://aoi-artifacts/runs/{CANDIDATE_RUN_ID}/weights.onnx"
        assert ctx["eval_metrics"] == PASSING_METRICS

        assert ctx["gate_passed"] is True
        assert record.state == "promoted"
        assert h.mm.default_version == CANDIDATE
        assert h.mlflow.aliases["production"] == CANDIDATE

    def test_offline_gate_blocks_bad_candidate_via_real_clients(self, harness_factory):
        # The real clients deliver DEFT's sub-target metrics faithfully, and DFW's
        # gate blocks the candidate before register/promotion.
        h = harness_factory(real_deft_backend=True, metrics=FAILING_METRICS)
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()

        assert "gate failed" in ctx["error"]
        # Training genuinely ran, so this is a gate decision.
        assert h.deft_http.bodies("POST", "/v1/fine-tune")
        assert h.mm.calls == []
        h.flywheel_db.mark_flywheel_run_error.assert_called_once()

    @pytest.mark.parametrize("final_state", ["failed", "cancelled"])
    def test_failed_deft_run_ends_the_run(self, harness_factory, final_state):
        # A non-successful terminal DEFT state fails the run and the rest skip,
        # while the chain itself still completes.
        h = harness_factory(real_deft_backend=True)
        h.deft_http.final_state = final_state
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()

        assert "non-successfully" in ctx["error"]
        assert not h.deft_http.bodies("GET", f"/v1/fine-tune/{CANDIDATE_RUN_ID}/results")
        assert h.mm.calls == []


class TestOnlineBeatPolicyThreading:
    def test_record_eval_harness_policy_reaches_the_evaluator(self, fake_db):
        """The record's ``promotion_policy.eval_harness`` must configure the evaluator.

        Three good units, none flagged (FAR = 0%). The evaluator's DEFAULT
        min_sample_size (100) would hold and its default criteria are empty, so a
        decisive ``advance`` proves the record's policy (min_sample_size=3 plus an
        ``online_target`` FAR constraint) actually reached the evaluator.
        """
        from src.api.promotion.service import PROMOTIONS_COLLECTION
        from src.backends.deft.eval import DeftAOIEvaluator

        samples = [
            {
                "target_id": f"t{i}",
                "ai_classification": label,
                "ai_confidence": 0.95,
                "confirmed_label": label,
                "baseline_classification": label,
            }
            for i, label in enumerate(["Good", "Good", "Good"])
        ]
        provider = MagicMock()
        provider.evaluator.side_effect = lambda use_case: DeftAOIEvaluator(
            results_source=lambda window, role: samples
        )
        fake_db[PROMOTIONS_COLLECTION].insert_one(
            {
                "promotion_id": "p-policy",
                "state": "promoting",
                "serving_role": "shadow",
                "use_case": "aoi",
                "model_name": MODEL,
                "candidate_version": CANDIDATE,
                "baseline_version": BASELINE,
                "pending_gate": "canary",
                "promotion_policy": {
                    "eval_harness": {
                        "min_sample_size": 3,
                        "online_target": [{"metric": "FAR_pct", "op": "<", "threshold": 50.0}],
                    }
                },
            }
        )
        with (
            patch("src.backends.factory.get_backend_provider", return_value=provider),
            patch("src.tasks.promotion.controller._get_db", return_value=fake_db),
            patch("src.tasks.promotion.tasks.advance_promotion") as adv,
        ):
            online_eval_poll()

        doc = fake_db[PROMOTIONS_COLLECTION].find_one({"promotion_id": "p-policy"})
        assert doc["verdict"] == "advance"
        adv.delay.assert_called_once_with("p-policy")


class TestOnlineBeatRecovery:
    def test_lost_decisive_verdict_is_re_driven_each_tick(self, harness_factory):
        # While a decisive verdict sits unconsumed — the advance was lost to a
        # crash, purge or exhausted retries — every beat tick re-enqueues exactly
        # one advance. Duplicates are safe: gates re-check state and verdict, and
        # applies are idempotency-keyed.
        h = harness_factory()
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            h.run_chain()
            h._drain_queues()  # pull + deploy -> serving_role=shadow, verdict=None
            baseline_enqueues = h.adv.delay.call_count

            online_eval_poll()  # fresh advance verdict: enqueues
            assert h.adv.delay.call_count == baseline_enqueues + 1
            # Never processed, so each later tick re-drives it via the sweep.
            online_eval_poll()
            online_eval_poll()
            assert h.adv.delay.call_count == baseline_enqueues + 3
            # One per tick: the verdict loop and the sweep do not stack.

            h._drain_queues()  # canary step runs; controller resets verdict=None
            online_eval_poll()  # fresh verdict for the next step -> enqueues

            assert h.adv.delay.call_count == baseline_enqueues + 4

    def test_stalled_pre_serving_gate_is_swept(self, harness_factory):
        # A `promoting` record at pull/deploy has no other re-drive path: with
        # serving_role unset the verdict loop cannot see it.
        h = harness_factory()
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = h.run_chain()  # start_promotion enqueued the first advance
            baseline_enqueues = h.adv.delay.call_count
            # Not drained: the advance is lost at the pull gate.
            record = h.record(ctx["promotion_id"])
            assert record.pending_gate == "pull" and record.state == "promoting"

            result = online_eval_poll()
            assert result["swept"] == 1
            assert h.adv.delay.call_count == baseline_enqueues + 1

            final = h.drive(ctx["promotion_id"])
            assert final.state == "promoted"


class TestApiToEnd:
    """The untrimmed path: REAL ``POST /jobs`` → dispatcher → chain → promoted.

    Compared to the stage-level tests above, this additionally exercises the
    API edge (backend gate, FlywheelRun insert, Celery enqueue), the dispatcher
    (PENDING check, default-config resolution via ``settings.job_configs``,
    provider capability check, ``AOIPipeline`` selection out of
    ``_DEFT_PIPELINES``) and the REAL Celery chain link-passing (the 7 ``.s()`` signatures executed eagerly in-process).
    """

    def _run(self, h: E2EHarness):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in [*h.patches(), *h.api_patches()]:
                stack.enter_context(p)
            # The chain ends with wait_for_promotion, polling in-process until
            # terminal, so the promotion must resolve WHILE the chain runs.
            h.resolve_promotion_synchronously()
            response, ctx = h.run_from_api()
            # Terminal by now; drive() only reads it back.
            record = h.drive(ctx["promotion_id"]) if ctx else None
            return response, ctx, record

    def test_post_jobs_to_promoted(self, harness_factory):
        h = harness_factory()
        response, ctx, record = self._run(h)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["id"] == "507f1f77bcf86cd799439011"
        h.api_mongo.flywheel_runs.insert_one.assert_called_once()

        assert ctx["flywheel_run_id"] == body["id"]
        assert ctx["gate_passed"] is True
        assert ctx["finalized"] is True
        assert ctx["candidate_version"] == CANDIDATE

        assert record.state == "promoted"
        assert h.mm.default_version == CANDIDATE
        assert h.mlflow.aliases["production"] == CANDIDATE
        assert [a["control"] for a in h.mm.bodies("POST /v1/models/apply")] == [
            "shadow",
            "canary",
            "canary",
            "full_switch",
        ]

    def test_post_jobs_rejects_unsupported_job_type(self, harness_factory):
        # Rejected before any DB write or Celery enqueue.
        from contextlib import ExitStack

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        h = harness_factory()
        with ExitStack() as stack:
            for p in [*h.patches(), *h.api_patches()]:
                stack.enter_context(p)
            from src.api.endpoints import router

            app = FastAPI()
            app.include_router(router)
            response = TestClient(app).post(
                "/jobs",
                json={"workload_id": "w1", "client_id": "c1", "job_type": "llm_job"},
            )

        assert response.status_code == 400
        assert "not supported by the active backend 'deft'" in response.json()["detail"]
        h.api_mongo.flywheel_runs.insert_one.assert_not_called()
        h.enqueued_dag.delay.assert_not_called()

    def test_offline_gate_failure_propagates_from_dispatcher(self, harness_factory):
        # NMP parity: the dispatcher's get() still RETURNS. A raised stage would
        # block the parent worker forever.
        h = harness_factory(metrics=FAILING_METRICS)
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in [*h.patches(), *h.api_patches()]:
                stack.enter_context(p)
            _response, ctx = h.run_from_api()

        assert "gate failed" in ctx["error"]
        h.flywheel_db.mark_flywheel_run_error.assert_called_once()
        assert h.mm.calls == []


class TestCandidateVersionClearsTheLiveStack:
    """The register stage must mint past what the serving stack actually holds.

    The counter alone cannot see the stack. On a real box the incumbent was found
    serving version 2 while a counter that had been reset would have re-minted 2 —
    staging over the incumbent's weights, then a routing apply rejected 422
    ``model_routing_duplicate_target``.
    """

    def _mint(self, h, model=MODEL):
        from contextlib import ExitStack

        from src.tasks.handlers.deft.handlers import RegisterCandidateHandler

        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            ctx = RegisterCandidateHandler().execute(
                {"model_name": model, "deft_run_id": CANDIDATE_RUN_ID, "use_case": "aoi"}
            )
        return ctx["candidate_version"]

    def test_stale_counter_is_lifted_past_the_live_version(self, harness_factory):
        h = harness_factory()
        h.mm.serves_state = True

        h.mm.loaded = ["1", "2"]
        h.mm.known_versions = ["1", "2"]
        h.mm.default_version = "2"
        h.db.serving_version_counters.docs.clear()
        h.db.serving_version_counters.insert_one({"_id": MODEL, "seq": 1})

        assert self._mint(h) == "3"

    def test_unloaded_versions_still_occupy_their_slot(self, harness_factory):
        """A version present but not routed must still raise the floor.

        On the live box both versions reported ``state: UNAVAILABLE`` while their
        directories held real weights. Minting only past what is *routed* would
        land straight on the retired one.
        """
        h = harness_factory()
        h.mm.serves_state = True
        # 2 and 3 are unloaded but still own their directory slots: the whole
        # point of the `known_versions` breadth.
        h.mm.loaded = ["1"]
        h.mm.known_versions = ["1", "2", "3"]
        h.mm.default_version = "1"  # rolled back: routing is BELOW the repo max
        h.db.serving_version_counters.docs.clear()
        h.db.serving_version_counters.insert_one({"_id": MODEL, "seq": 1})

        assert self._mint(h) == "4"

    def test_older_model_manager_falls_back_to_the_counter(self, harness_factory):
        """A Model Manager without the endpoint must not break registration."""
        h = harness_factory()
        h.mm.serves_state = False
        h.db.serving_version_counters.docs.clear()
        h.db.serving_version_counters.insert_one({"_id": MODEL, "seq": 4})

        assert self._mint(h) == "5"

    def test_no_counter_and_no_live_read_refuses_to_guess(self, harness_factory):
        """The dangerous case: nothing knows anything. Fail closed."""
        from src.tasks.promotion.serving_version import ServingVersionUnknownError

        h = harness_factory()
        h.mm.serves_state = False
        h.db.serving_version_counters.docs.clear()

        with pytest.raises(ServingVersionUnknownError):
            self._mint(h)


class TestTheModelManagerFakeMatchesTheRealContract:
    """Guards on the FAKE itself, driven through the REAL backend client.

    ``FakeModelManagerHTTP`` is what every e2e scenario in this file and in the
    ITS suite believes the Model Manager does. Nothing else asserted these two
    properties, so the fake was free to drift away from the Model Promotion API
    and silently weaken every test built on it. Both go through the real
    ``ModelManagerBackend`` so the client's parsing is on the hook too.
    """

    def _backend(self, mm):
        from src.lib.promotion.deployment_backend.model_manager_client import (
            ModelManagerBackend,
        )

        backend = ModelManagerBackend({"base_url": mm.base_url})
        return backend, patch.multiple(
            "src.lib.promotion.deployment_backend.model_manager_client.requests",
            post=mm.post,
            get=mm.get,
        )

    def test_a_full_switch_reports_the_version_it_switched_away_from(self):
        """``from_version`` is the OUTGOING default, not the incoming one.

        Reading ``default_version`` after the switch had already overwritten it
        made the fake answer ``from_version == to_version`` on every successful
        promotion — a transition the real API never reports, and one that would
        make a controller that mixed the two look correct here.
        """
        from src.lib.promotion.deployment_backend.base import PromotionControl

        mm = FakeModelManagerHTTP(BASELINE)
        backend, requests_patch = self._backend(mm)
        with requests_patch:
            result = backend.apply(MODEL, CANDIDATE, PromotionControl.FULL_SWITCH)

        assert result.from_version == BASELINE
        assert result.to_version == CANDIDATE
        assert result.from_version != result.to_version

        assert mm.default_version == CANDIDATE

    def test_a_shadow_apply_leaves_the_default_where_it_was(self):
        """The non-regression half: a non-switching control moves nothing."""
        from src.lib.promotion.deployment_backend.base import PromotionControl

        mm = FakeModelManagerHTTP(BASELINE)
        backend, requests_patch = self._backend(mm)
        with requests_patch:
            result = backend.apply(MODEL, CANDIDATE, PromotionControl.SHADOW)

        assert result.from_version == BASELINE
        assert mm.default_version == BASELINE

    def test_unload_frees_the_gpu_without_freeing_the_version_slot(self):
        """``known_versions`` is the collision floor and unload must not shrink it: an
        evicted version's directory still owns the slot. Deriving ``known_versions``
        from the loaded set let the floor drop back after an eviction and would hand a
        later mint an already-taken number.
        """
        mm = FakeModelManagerHTTP(BASELINE, serves_state=True)
        backend, requests_patch = self._backend(mm)
        with requests_patch:
            backend.pull(MODEL, CANDIDATE, "s3://bucket/candidate", "onnx", "sha-abc")
            assert CANDIDATE in backend.serving_state(MODEL).known_versions

            backend.unload(MODEL, CANDIDATE)
            state = backend.serving_state(MODEL)

        assert CANDIDATE not in state.serving_versions
        assert CANDIDATE not in mm.loaded
        # Evicted, but the slot is still taken.
        assert CANDIDATE in state.known_versions
        assert BASELINE in state.known_versions

    def test_unload_stops_a_version_being_ready_without_freeing_its_slot(self):
        """Unload must move ``ready_versions``, not just the fake's private ``loaded``
        list, which is not part of ``_state_body`` -- an unload that touched only
        ``loaded`` was invisible in every byte the client can read. BASELINE is first
        displaced by a full switch because only the exclusive-routing branch leaves a
        version ready but unrouted, and a routed version is refused (409).

        Mutation: restore ``if json["version"] in self.loaded: self.loaded.remove(...)``
        as the whole unload body -> BASELINE stays in ``ready_versions`` -> red.
        """
        from src.lib.promotion.deployment_backend.base import PromotionControl

        mm = FakeModelManagerHTTP(BASELINE, serves_state=True)
        backend, requests_patch = self._backend(mm)
        with requests_patch:
            backend.pull(MODEL, CANDIDATE, "s3://bucket/candidate", "onnx", "sha-abc")
            backend.apply(MODEL, CANDIDATE, PromotionControl.SHADOW)
            backend.apply(MODEL, CANDIDATE, PromotionControl.FULL_SWITCH)
            assert BASELINE in mm.ready_versions  # precondition, not the assertion

            backend.unload(MODEL, BASELINE)
            state = backend.serving_state(MODEL)

        assert BASELINE not in mm.ready_versions
        assert BASELINE not in mm.loaded
        assert BASELINE not in [r.version for r in state.routing]
        # Out of memory, but the directory and its collision slot survive.
        assert BASELINE in state.known_versions

    def test_unload_of_a_routed_version_is_refused(self):
        """``ModelManager._evict`` 409s any version routing still names, and both
        unload and cleanup pass through it. A fake that happily evicts the live default
        models a serving stack that cannot exist.

        Mutation: drop the ``_is_serving`` guard from the unload branch -> the fake
        returns 200 and no ``BackendError`` is raised -> red.
        """
        from src.lib.promotion.deployment_backend.base import BackendError

        mm = FakeModelManagerHTTP(BASELINE, serves_state=True)
        backend, requests_patch = self._backend(mm)
        with requests_patch, pytest.raises(BackendError) as excinfo:
            backend.unload(MODEL, BASELINE)

        assert excinfo.value.http_status == 409
        # The refusal left the stack exactly as it was.
        assert mm.default_version == BASELINE
        assert BASELINE in mm.ready_versions

    def test_a_reclaim_shrinks_the_observed_floor_and_minting_still_does_not_collide(self):
        """Once something actually reclaims, the floor the serving stack reports DOES
        drop. Minting must not follow it down: the monotonic Mongo counter dominates
        the observed floor, so a re-mint gets a fresh number, not the reclaimed one.
        """
        from src.tasks.promotion.serving_version import ObservedServing, next_serving_version
        from tests.unit.promotion.conftest import FakeDB

        def _floor(state) -> ObservedServing:
            numeric = [int(v) for v in state.known_versions if v.isdigit()]
            return ObservedServing(floor=max(numeric) if numeric else None)

        db = FakeDB()
        mm = FakeModelManagerHTTP(BASELINE, serves_state=True)
        backend, requests_patch = self._backend(mm)
        with requests_patch:
            backend.pull(MODEL, CANDIDATE, "s3://bucket/candidate", "onnx", "sha-abc")
            before = backend.serving_state(MODEL)
            minted = next_serving_version(db, MODEL, observed=_floor(before))
            # The sweep's verb: this one DOES delete the directory.
            backend.cleanup(MODEL, CANDIDATE)
            after = backend.serving_state(MODEL)
            assert CANDIDATE not in after.known_versions  # the floor really dropped
            assert _floor(after).floor < _floor(before).floor
            again = next_serving_version(db, MODEL, observed=_floor(after))

        assert int(again) > int(minted)


class TestDegradedServingReadsAreVisible:
    """`routing_available` and `loaded_available` fail independently.

    Routing comes from the AOI Client, the repo view from AOI Inference. The repo
    view is authoritative for the floor, so an unreadable routing lookup must not
    block a promotion — but it must not vanish either: routing is in the union
    precisely to cover versions the repo index has not caught up to, and that
    cover is gone while it is unreadable.
    """

    def test_unreadable_routing_still_mints_from_the_repo_view(self, monkeypatch, caplog):
        import logging

        from src.lib.promotion.deployment_backend.base import ServingState
        from src.tasks.handlers.deft.handlers import _observed_serving

        state = ServingState(
            model_name=MODEL,
            known_versions=["1", "2", "3"],
            known_available=True,
            routing=[],  # empty because UNKNOWN, not because nothing routes
            routing_available=False,
        )
        monkeypatch.setattr(
            "src.lib.promotion.deployment_backend.build_deployment_backend",
            lambda *a, **k: type("B", (), {"serving_state": lambda self, n: state})(),
        )

        with caplog.at_level(logging.WARNING):
            observed = _observed_serving(MODEL)

        assert observed is not None and observed.floor == 3
        assert any("Routing state unavailable" in r.message for r in caplog.records)

    def test_a_healthy_read_does_not_warn(self, monkeypatch, caplog):
        import logging

        from src.lib.promotion.deployment_backend.base import RoutingEntry, ServingState
        from src.tasks.handlers.deft.handlers import _observed_serving

        state = ServingState(
            model_name=MODEL,
            known_versions=["1", "2"],
            known_available=True,
            routing=[RoutingEntry(version="2", role="default", weight=1.0)],
            routing_available=True,
        )
        monkeypatch.setattr(
            "src.lib.promotion.deployment_backend.build_deployment_backend",
            lambda *a, **k: type("B", (), {"serving_state": lambda self, n: state})(),
        )

        with caplog.at_level(logging.WARNING):
            observed = _observed_serving(MODEL)

        assert observed.floor == 2
        assert not any("Routing state unavailable" in r.message for r in caplog.records)

    def test_routing_can_lift_the_floor_above_the_repo_view(self, monkeypatch):
        """The reason routing is in the union at all.

        A version routing knows about but the repo index has not indexed yet
        still has to raise the floor.
        """
        from src.lib.promotion.deployment_backend.base import RoutingEntry, ServingState
        from src.tasks.handlers.deft.handlers import _observed_serving

        state = ServingState(
            model_name=MODEL,
            known_versions=["1", "2"],
            known_available=True,
            routing=[RoutingEntry(version="7", role="canary", weight=0.05)],
            routing_available=True,
        )
        monkeypatch.setattr(
            "src.lib.promotion.deployment_backend.build_deployment_backend",
            lambda *a, **k: type("B", (), {"serving_state": lambda self, n: state})(),
        )

        assert _observed_serving(MODEL).floor == 7


class TestTheControlPlaneMayRestartMidPromotion:
    """End to end: a Model Manager outage is survivable.

    The fake raises if anything polls a swap id, so the rest of the suite
    already covers the passive half. These tests inject faults into the polls.
    """

    def test_a_model_manager_that_restarts_mid_promotion_still_promotes(self, harness_factory):
        """Kill the control plane between apply and the first answered poll.

        A 404 and a refused connection are both a control plane we could not
        reach. Neither is evidence about the backend, so neither may end the
        promotion. The widened poll budget is what makes room for the faults.
        """
        # The suite default with only the poll budget widened, restated whole
        # because policy_overrides replaces the block: dropping
        # eval_harness.online_target leaves the online gate fail-closed.
        h = harness_factory(
            policy_overrides={
                "promotion_policy": {
                    "hitl": {"pull": False, "deploy": False, "canary": False, "full": False},
                    "canary_steps": {"steps": [5, 25]},
                    "eval_harness": {
                        "min_sample_size": 10,
                        "online_target": [{"metric": "FAR_pct", "op": "<", "threshold": 5.0}],
                    },
                    "poll_interval_s": 0.0,
                    "poll_max_attempts": 6,
                    "auto_rollback_on_regression": True,
                }
            }
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for patch_ in h.patches():
                stack.enter_context(patch_)
            ctx = h.run_chain()
            pid = ctx["promotion_id"]
            h._drain_queues()
            # Two, not three: three consecutive unreadable polls escape as a
            # retryable error (TestConvergencePolling covers that). max_retries=1
            # here, so the client does not mask these — what is measured is the
            # controller's own tolerance.
            h.mm.convergence_faults = [
                404,  # a replica that never saw the apply
                requests.exceptions.ConnectionError("connection refused"),  # none up yet
            ]
            record = h.drive(pid)

        assert (
            record.state == "promoted"
        ), "a Model Manager restart between apply and poll must not fail the promotion"
        assert not h.mm.convergence_faults, "the injected faults were never actually hit"

    def test_the_expectation_is_echoed_back_exactly_as_it_was_minted(self, harness_factory):
        """Round-trip identity, checked end to end.

        ApplyResponse.applied and ConvergenceRequest.expectation are the same
        object, which is what lets both ends forbid extra fields.
        """
        h = harness_factory()
        _ctx, record = _run(h)

        assert record.state == "promoted"
        minted = h.mm.applied_returned
        polled = [b["expectation"] for b in h.mm.bodies("POST /v1/models/convergence")]
        assert minted, "no apply ever minted a token"
        assert polled, "the controller never polled convergence"
        for token in polled:
            assert token in minted, (
                "the controller polled an expectation the Model Manager never minted; "
                "DFW must echo the token, never construct one"
            )

    def test_no_swap_id_is_ever_polled(self, harness_factory):
        """The retired surface stays retired.

        The fake also raises on a swap-id poll; this states the invariant where
        a reader will look for it.
        """
        h = harness_factory()
        _ctx, record = _run(h)

        assert record.state == "promoted"
        polls = [call for call, _ in h.mm.calls if call.startswith("GET /v1/models/status/")]
        assert not polls, f"something still polls by swap id: {polls}"
