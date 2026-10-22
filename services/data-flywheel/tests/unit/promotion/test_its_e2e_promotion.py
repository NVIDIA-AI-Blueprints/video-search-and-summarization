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

"""End-to-end ITS pipeline test — the SAME chain, controller and beats as AOI.

ITS is a JOB TYPE (``its_job``) on the existing DEFT pipeline, not a second
pipeline; ``use_case="its"`` is the orthogonal runtime key. To make that claim
mean something this module imports the AOI suite's harness verbatim
(``_e2e_fakes``) and changes only the two axes::

    axis          aoi                        its
    job type      aoi_job                    its_job          (same pipeline)
    use case      aoi                        its
    object store  storage-backend            sc-backend       (endpoints map)
    evaluator     DeftAOIEvaluator (FAR)     DeftITSEvaluator (offline only)
    DEFT body     workflow + config          profile mode, no config block
    KPI set       pulled from storage        a static, frozen uri
    ladder        pull->shadow->canary->full pull->[shadow]->HUMAN->full

Two spellings of ITS, and swapping them re-points the routing silently: the
CONTROL plane is ``its`` (job type, use case, evaluator key, endpoints key), the
DATA plane is ``sc-`` (bucket, storage host, artifact and KPI prefixes).

The ITS posture is the human gate. As shipped (``shadow: false``) that is
``pull -> pending_approval -> approve -> ONE full_switch``; this file drives the
superset, with the shadow arm on. No canary stage (RTVI-CV has no
partial-traffic routing) and no online gate: the candidate is validated offline
by ``eval.kpi_target``, then soaks until a human decides.

A ladder bug that inserts a stage still reaches ``full_switch`` and still passes
a happy-path test, so scenarios also assert negatively — see
:func:`_assert_no_canary_was_ever_issued`.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any

import pytest

from src.api.promotion.service import PROMOTIONS_COLLECTION
from src.config import settings
from src.config.deft import DEFTConfig, DeftSystemConfig, ModelManagerConfig
from src.job_types import JobType

from ._e2e_fakes import CANDIDATE, MODEL, E2EHarness
from ._e2e_fakes import harness_factory as _harness_factory

# Assigned, not imported, so no test parameter reads as a redefinition.
harness_factory = _harness_factory


# -- The ITS deployment, as config.deft.yaml ships it ------------------------ #

# CONTROL plane: these strings route.
ITS_JOB_TYPE = JobType.ITS  # "its_job" — the submission edge
ITS_USE_CASE = "its"  # evaluator + store + control plane + policy

# DATA plane: `sc-` (smart city). These name where the bytes live.
ITS_STORAGE_HOST = "sc-backend"
ITS_ARTIFACT_ROOT = "s3://sc-artifacts"

ITS_MODEL = "gdino-its"  # the MLflow registry key
ITS_MODEL_FORMAT = "onnx"  # RTVI-CV builds the engine; DFW ships the graph
ITS_PROFILE = "smart-city-object-detection"
ITS_KPI_URI = f"{ITS_ARTIFACT_ROOT}/kpi/{ITS_MODEL}/testing_set/"
ITS_RUN_ID = "its-run-1"  # DEFT run id — lineage only, NOT the serving version
# Not 1: a counter seeded at 9 mints "10", where a zero-padded or lexicographic
# implementation ("09", "010", "9.1") shows up and nowhere else.
ITS_BASELINE = "9"
ITS_CANDIDATE = "10"
ITS_MM_LABEL = "its-mm"  # a label for this run's fake; one MM fronts both use cases

#: Facets ride FLAT inside ``selection.filters``, as shipped. ``created_after``
#: is the whole set: ``use_case: its`` already routes to the ITS catalogue, so a
#: ``jobType`` term has no AOI rows there to separate — and the field is not in
#: that index template anyway.
ITS_FILTERS: dict[str, Any] = {
    "created_after": "1970-01-01T00:00:00Z",
    # No media-retention facet, as shipped: the name is unconfirmed and
    # SnapshotSelection is additionalProperties:true, so a guess is accepted and
    # matches NOTHING. See config.deft.yaml.
}

#: DEFT's offline metrics for a detector, never AOI's `far`. ``recall`` is
#: reported but NOT gated, so the gate is seen judging what the policy asked for.
ITS_PASSING_METRICS = {"mAP50": 0.72, "recall": 0.88}
#: mAP50 below target, recall ABOVE it — a gate that passed here could only be
#: ignoring mAP50.
ITS_FAILING_METRICS = {"mAP50": 0.41, "recall": 0.92}
#: A good detector reporting under a different key. The shipped key `macro_ap`
#: is confirmed only against a draft MR, so this risk is real.
ITS_MISNAMED_KEY_METRICS = {"AP50": 0.91, "recall": 0.93}


def _its_job_config_overrides(**extra: Any) -> dict[str, Any]:
    """The SHIPPED v1 ITS body of ``DeftJobConfig`` (the ``its_job_config:`` block).

    Whole top-level blocks, not tweaks to AOI's, which is why the harness takes
    a job-config override rather than a pile of booleans.

    Three values differ from the shipped ones: ``poll_interval_s`` /
    ``poll_max_attempts`` are scaffolding (no real sleeps in a unit test), and
    ``deployment_stages.shadow`` is a posture change explained where it is set.
    """
    data: dict[str, Any] = {
        "selection": {"mode": "query", "filters": dict(ITS_FILTERS)},
        # DFW's POST /v1/kpi carries a hardcoded AOI-shaped {'tag': 'kpi'}
        # selection that means nothing to an ITS store.
        "kpi": {"pull_from_storage": False, "uri": ITS_KPI_URI},
        # PROFILE mode: the profile owns architecture, classes and
        # hyper-parameters, and a partial `config` would fight it.
        "deft": {"profile": ITS_PROFILE},
        "eval": {
            # Each constraint bets that DEFT emits a key by that exact name, and
            # each can reject every candidate forever on its own.
            "kpi_target": [{"metric": "mAP50", "op": ">=", "threshold": 0.55}]
        },
        "promotion_policy": {
            # `canary: true` is dead config on a disabled stage, kept as shipped.
            "hitl": {"pull": False, "deploy": False, "canary": True, "full": True},
            # RTVI-CV has no partial-traffic routing, so the ladder is
            # pull -> shadow -> full and no `canary_steps` block ships.
            #
            # `shadow: True` deviates from the shipped `false`, which is there
            # because a FRESH deployment has nothing to shadow and the Model
            # Manager refuses the routing write. This harness seeds
            # `baseline_version`, so it models the posture after the first
            # promotion. First-run ladder: test_shipped_its_config.py.
            "deployment_stages": {"shadow": True, "canary": False},
            # `enabled` is tri-state so "unstated" cannot mean "opted out".
            "eval_harness": {"enabled": False},
            "poll_interval_s": 0.0,  # test scaffolding: no real sleeps
            "poll_max_attempts": 1,  # test scaffolding
            # No online verdict exists, so an auto-rollback has nothing to act on.
            "auto_rollback_on_regression": False,
        },
        # An ITS snapshot DECODES VIDEO before it reports ready; 120 x 5s = 600s,
        # double the AOI-shaped default.
        "snapshot_poll_max_attempts": 120,
    }
    data.update(extra)
    return data


def _its_harness_kwargs(**extra: Any) -> dict[str, Any]:
    """Everything that makes an ``E2EHarness`` an ITS harness.

    Every key is a seam the harness defaults to its AOI value, so this function
    is the diff between the two job types. Built fresh per call.

    ``job_type`` and ``use_case`` are both stated because they are separate
    axes; passing only the second would drive ITS through the AOI submission
    edge. The harness refuses to derive either from the other.
    """
    kwargs: dict[str, Any] = {
        "job_type": ITS_JOB_TYPE,
        "use_case": ITS_USE_CASE,
        "model_name": ITS_MODEL,
        "model_format": ITS_MODEL_FORMAT,
        "baseline_version": ITS_BASELINE,
        "run_id": ITS_RUN_ID,
        "artifact_root": ITS_ARTIFACT_ROOT,
        "storage_host": ITS_STORAGE_HOST,
        "mm_name": ITS_MM_LABEL,
        "metrics": dict(ITS_PASSING_METRICS),
        "policy_overrides": _its_job_config_overrides(),
        "online_samples": _its_observations(),
    }
    kwargs.update(extra)
    return kwargs


@contextmanager
def _deployment_settings(*, use_case_endpoints: dict | None = None):
    """Install a two-use-case DEFT system config for the duration of a test.

    The unit environment loads the NMP settings (no ``deft_config``), under
    which ``for_use_case`` returns one set of endpoints for everyone — ITS would
    read and write AOI's store silently. Installed explicitly rather than
    inherited.

    ``aoi: {}`` overrides nothing on purpose: it means "inherits", keeping
    ``for_use_case("aoi")`` returning ``self`` while still satisfying the
    snapshot stage's fail-closed "every use case must be declared" guard.
    """
    endpoints = (
        use_case_endpoints
        if use_case_endpoints is not None
        else {
            "aoi": {},
            ITS_USE_CASE: {
                "storage_backend_url": f"http://{ITS_STORAGE_HOST}:8080",
                "object_store_uri": ITS_ARTIFACT_ROOT,
                # The SAME env var as the top level, as shipped: the stores are
                # isolated by address, not by token.
                "storage_api_key_env": "STORAGE_BACKEND_API_KEY",
                "presign_expires_s": 3600,
            },
        }
    )
    original = settings.system_config
    settings.system_config = DeftSystemConfig(
        deft_config=DEFTConfig(use_case_endpoints=endpoints),
        model_manager=ModelManagerConfig(base_url="http://mm:8090"),
    )
    try:
        yield settings.system_config.deft_config
    finally:
        settings.system_config = original


@contextmanager
def _its_stack(harness_factory, *, endpoints_override: dict | None = None, **harness_kwargs):
    """An ITS harness with every seam patched, inside the DEFT settings block.

    Order matters: the settings swap must precede building the harness (a real
    ``DeftBackendProvider`` resolves endpoints at construction) and ``patches()``
    (it patches an attribute OF ``settings.system_config``).
    """
    with _deployment_settings(use_case_endpoints=endpoints_override):
        harness = harness_factory(**_its_harness_kwargs(**harness_kwargs))
        with ExitStack() as stack:
            for patcher in harness.patches():
                stack.enter_context(patcher)
            yield harness


def _approve_the_full_switch(harness: E2EHarness) -> None:
    """The human's approval signal, exactly as an approver sends it.

    Moving ``@production`` onto the candidate is one of two out-of-band signals
    ``hitl_poll`` recognises (the other is a ``gate.full.decision`` tag).
    """
    harness.mlflow.set_alias(ITS_MODEL, "production", ITS_CANDIDATE)


def _deny_the_full_switch(harness: E2EHarness) -> None:
    """The human's REJECTION, via the gate-decision tag ``hitl_poll`` reads."""
    version = harness.mlflow.get_version_by_alias(ITS_MODEL, "candidate")
    harness.mlflow.set_promotion_tags(ITS_MODEL, version, {"gate.full.decision": "rejected"})


def _applies(harness: E2EHarness) -> list[str]:
    """The routing ladder actually issued, in order."""
    return [a["control"] for a in harness.mm.bodies("POST /v1/models/apply")]


def _assert_no_canary_was_ever_issued(harness: E2EHarness) -> None:
    """THE NEGATIVE ASSERTION.

    RTVI-CV has no partial-traffic routing, so a canary apply is not a slower
    rollout for ITS; it is a request the serving stack cannot honour.

    Swept over every recorded body (not just the applies) and over four
    independent tells, because a canary can arrive mislabelled: ``control``, a
    ``canary_weight`` field on any verb, the ``:canary:<n>`` idempotency key, and
    the ``@canary`` alias move the controller makes on a canary transition.
    """
    bodies = [body for (_endpoint, body) in harness.mm.calls if body is not None]
    assert bodies, "the Model Manager was never called at all"
    assert all(b.get("control") != "canary" for b in bodies), _applies(harness)
    assert all("canary_weight" not in b for b in bodies)
    assert all(":canary:" not in str(b.get("idempotency_key", "")) for b in bodies)
    assert [alias for (alias, _v) in harness.mlflow.alias_history if alias == "canary"] == []


# -- Observation records: POST-V1 ------------------------------------------- #
# Nothing in v1 reads these. They let a scenario hand a promotion "the fleet is
# reporting" evidence and still assert no verdict came out of it.


def _its_observations(
    *,
    windows: int = 12,
    cameras: tuple[str, ...] = ("cam-0", "cam-1"),
    frames: int = 60,
    tp: int = 90,
    fp: int = 5,
    fn: int = 10,
    ap50: float = 0.71,
    version: str = ITS_CANDIDATE,
    role: str = "shadow",
) -> list[dict]:
    """Scored observation windows in the CANONICAL ObservationRecord spelling.

    Field names follow ``docs/contracts/its-object-store.openapi.json`` —
    ``observation_id`` / ``window_start`` / ``window_end`` / ``gt_instances`` /
    ``ap50``, not the pre-contract ``window_id`` / ``from`` / ``to`` /
    ``instances`` / ``mAP50``.

    ``frames`` and ``support`` carry the same value, as the mock object store
    does; the contract has both and they mean the same thing.

    Defaults are a HEALTHY fleet: 720 frames across 2 cameras, 94.7% precision
    and 90.0% recall if anything ever scored them.
    """
    out: list[dict] = []
    for i in range(windows):
        camera = cameras[i % len(cameras)]
        # Split across the two trained classes, so the per-class micro sums
        # reconcile with the window totals.
        car_tp, person_tp = tp - tp // 2, tp // 2
        car_fp, person_fp = fp - fp // 2, fp // 2
        car_fn, person_fn = fn - fn // 2, fn // 2
        out.append(
            {
                "observation_id": f"obs-{i:04d}",
                "model": ITS_MODEL,
                "version": version,
                "role": role,
                "camera_id": camera,
                "site_id": "site-north-gate",
                "window_start": f"2026-07-24T00:{i:02d}:00Z",
                "window_end": f"2026-07-24T00:{i:02d}:30Z",
                "frames": frames,
                "support": frames,
                "gt_instances": tp + fn,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "ap50": ap50,
                "per_class": {
                    "car": {"tp": car_tp, "fp": car_fp, "fn": car_fn},
                    "person": {"tp": person_tp, "fp": person_fp, "fn": person_fn},
                },
            }
        )
    return out


# -- 1. THE v1 SHADOW SOAK: pull -> shadow -> a human -> one full_switch ----- #


def _model_named_by(body: dict) -> str:
    """The model a Model Manager request is about, wherever it names it.

    Most bodies carry ``model_name`` at the top level; a convergence poll names
    it inside the opaque ``expectation`` instead, which is what lets the control
    plane attribute a poll with no swap registry to look the id up in.
    """
    if "model_name" in body:
        return body["model_name"]
    expectation = body.get("expectation")
    if isinstance(expectation, dict) and "model_name" in expectation:
        return expectation["model_name"]
    raise AssertionError(f"Model Manager body names no model: {body!r}")


class TestTheV1ShadowSoak:
    """The whole ITS flywheel, through the REAL clients and the REAL controller.

    The shipped ladder and nothing else: no canary stage, no online verdict, and
    one human decision between shadow and production traffic.
    """

    def test_the_ladder_pauses_at_full_and_one_approval_promotes(self, harness_factory):
        """pull -> apply(shadow) -> pending_approval -> approve -> ONE full_switch.

        The pause is asserted BEFORE the approval, with the applies snapshotted
        then. End state alone cannot tell "the machine waited for a human" from
        "the machine promoted and a human approved afterwards".
        """
        with _its_stack(harness_factory, real_deft_backend=True) as h:
            ctx = h.run_chain()
            promotion_id = ctx["promotion_id"]

            paused = h.drive(promotion_id, hitl=False)
            assert paused.state == "pending_approval"
            assert paused.pending_gate == "full"
            assert _applies(h) == ["shadow"]
            assert paused.serving_role == "shadow"
            assert h.mm.default_version == ITS_BASELINE
            assert h.mlflow.aliases["production"] == ITS_BASELINE
            # Skipping canary did not skip the pull: a soak needs the artifact.
            assert len(h.mm.bodies("POST /v1/models/pull")) == 1

            _approve_the_full_switch(h)
            record = h.drive(promotion_id, hitl=True)

            _assert_no_canary_was_ever_issued(h)

        assert record.state == "promoted"
        assert record.serving_role == "default"
        assert _applies(h) == ["shadow", "full_switch"]
        # A re-driven gate that re-applied would be a second traffic rewrite.
        switches = [
            a for a in h.mm.bodies("POST /v1/models/apply") if a["control"] == "full_switch"
        ]
        assert len(switches) == 1
        assert switches[0]["version"] == ITS_CANDIDATE
        assert h.mm.default_version == ITS_CANDIDATE
        assert h.mlflow.tags[ITS_CANDIDATE]["gate.full.decision"] == "approved"
        # Unloaded, never cleaned up, so rollback stays a routing change.
        unloads = h.mm.bodies("POST /v1/models/unload")
        assert [(c["model_name"], c["version"]) for c in unloads] == [(ITS_MODEL, ITS_BASELINE)]
        assert h.mm.bodies("POST /v1/models/cleanup") == []
        assert [a for (a, _v) in h.mlflow.alias_history if a != "production"] == [
            "candidate",
            "shadow",
        ]
        assert h.mlflow.aliases["production"] == ITS_CANDIDATE

    def test_a_denied_soak_rolls_the_shadow_arm_back(self, harness_factory):
        """The other half of a human gate: the human can say no.

        The approver is v1's entire answer to "the shadow arm misbehaves", so a
        denial must unwind the rollout, not merely fail to advance it.

        The rollback issues a ``full_switch`` BACK to the baseline — for a
        candidate that never left shadow, a re-assertion of existing routing
        rather than a traffic move. What frees the shadow arm is the
        ``model/unload`` issued AFTER that switch converges: until the switch
        lands, routing is not yet exclusive to the baseline.
        """
        with _its_stack(harness_factory) as h:
            ctx = h.run_chain()
            promotion_id = ctx["promotion_id"]

            assert h.drive(promotion_id, hitl=False).state == "pending_approval"
            _deny_the_full_switch(h)
            record = h.drive(promotion_id, hitl=True)

            _assert_no_canary_was_ever_issued(h)

        assert record.state == "rolled_back"
        assert h.mlflow.tags[ITS_CANDIDATE]["gate.full.decision"] == "rejected"
        assert h.mlflow.aliases["production"] == ITS_BASELINE
        assert h.mm.default_version == ITS_BASELINE
        # Whole list, not membership: unloading the baseline would evict what
        # production is serving.
        assert h.mm.bodies("POST /v1/models/unload") == [
            {"model_name": ITS_MODEL, "version": ITS_CANDIDATE}
        ]
        # Unload touches no files, so the weights wait for the reclaim sweep.
        assert h.mm.bodies("POST /v1/models/cleanup") == []
        assert ITS_BASELINE in h.mm.loaded
        assert ITS_CANDIDATE not in h.mm.loaded
        # The stamp is the only thing that will ever let the sweep reclaim it.
        assert record.evicted_versions == [ITS_CANDIDATE]
        assert record.evicted_at is not None
        h.flywheel_db.mark_flywheel_run_completed.assert_not_called()

    def test_no_online_verdict_is_ever_produced_or_recorded(self, harness_factory):
        """``eval_harness.enabled: false`` must mean MEASURED NOTHING.

        A disabled gate must not write a verdict it did not measure: a
        synthesised ``advance`` would have an operator reading ``eval_metrics``
        as evidence about a candidate nobody graded.

        Observation records are fed in on purpose, so this proves the gate is OFF
        rather than merely starved.

        ``eval_metrics`` is asserted by CONTENT, not emptiness: the field is
        written twice — ``start_promotion`` seeds the offline metrics and the
        online beat overwrites them — so in v1 it must still hold exactly what
        DEFT reported, with no online key beside it.
        """
        online_only = {"precision_pct", "recall_pct", "n_frames", "n_cameras", "mAP50_wavg"}

        with _its_stack(harness_factory) as h:
            h.set_online_samples(_its_observations())
            ctx = h.run_chain()
            promotion_id = ctx["promotion_id"]

            paused = h.drive(promotion_id, hitl=False)
            assert paused.state == "pending_approval"
            assert paused.verdict is None
            assert paused.eval_metrics == ITS_PASSING_METRICS
            assert not online_only & set(paused.eval_metrics)

            _approve_the_full_switch(h)
            record = h.drive(promotion_id, hitl=True)

            # The gate really was off, not merely never reached.
            assert h.policy.eval_harness.enabled is False
            assert h.policy.eval_harness.online_enabled is False
            assert h.policy.auto_rollback_on_regression is False

        assert record.state == "promoted"
        assert record.verdict is None

        assert record.eval_metrics == ITS_PASSING_METRICS
        assert not online_only & set(record.eval_metrics)

    def test_the_dead_canary_hitl_flag_never_pauses_anything(self, harness_factory):
        """``hitl.canary: true`` on a disabled stage is inert, and stays inert.

        The shipped config carries it so re-enabling the stage restores the
        approval that guarded it — safe only if a flag on a skipped gate cannot
        pause a rollout.
        """
        with _its_stack(harness_factory) as h:
            assert h.policy.hitl.canary is True  # the dead flag, as shipped
            assert h.policy.deployment_stages.canary is False
            ctx = h.run_chain()
            promotion_id = ctx["promotion_id"]

            paused = h.drive(promotion_id, hitl=False)
            assert paused.pending_gate == "full", "the rollout paused at a stage it does not run"
            assert paused.canary_step_index == 0

            _approve_the_full_switch(h)
            record = h.drive(promotion_id, hitl=True)

        assert record.state == "promoted"
        assert "gate.canary.decision" not in h.mlflow.tags[ITS_CANDIDATE]
        assert h.mlflow.tags[ITS_CANDIDATE]["gate.full.decision"] == "approved"

    def test_the_run_reads_and_writes_only_the_smart_city_data_plane(self, harness_factory):
        """Snapshot, KPI set, checkpoint and artifacts, all under ``sc-*``.

        The only store in this stack answers on ``sc-backend:8080`` and the
        router falls through to the DEFT fake (which raises on anything but a
        fine-tune) for every other host, so a call to AOI's ``storage-backend``
        blows up at the seam rather than landing quietly.
        """
        with _its_stack(harness_factory, real_deft_backend=True) as h:
            ctx = h.run_chain()
            _approve_the_full_switch(h)
            record = h.drive(ctx["promotion_id"], hitl=True)

        assert ctx["use_case"] == ITS_USE_CASE
        assert ctx["gate_passed"] is True
        assert ctx["finalized"] is True
        assert record.state == "promoted"

        # ITS owns a catalog, so it resolves to the SCOPED path. The unprefixed
        # alias is asserted absent because it resolves to AOI's catalog — an
        # empty pool that reads as success rather than erroring.
        (snap_body,) = h.storage_http.bodies("POST", "/v1/its/snapshot")
        assert h.storage_http.bodies("POST", "/v1/snapshot") == []
        # Facets ride FLAT. `mode` is absent on purpose: a DFW selection-STRATEGY
        # marker would compile into a metadata term no record carries.
        assert snap_body["selection"] == ITS_FILTERS
        assert "mode" not in snap_body["selection"]
        assert "filters" not in snap_body["selection"]
        assert ctx["snapshot_uri"] == h.storage_http.PREFIX
        assert ctx["snapshot_uri"].startswith(ITS_ARTIFACT_ROOT)

        # Zero /v1/kpi calls: the static-uri branch was taken. Over every CALL,
        # so a create-then-poll that lost its body still shows up.
        assert [c for c in h.storage_http.calls if c[1].startswith("/v1/kpi")] == []

        (ft_body,) = h.deft_http.bodies("POST", "/v1/fine-tune")
        assert ft_body["profile"] == ITS_PROFILE
        # A partial ExperimentConfig fails validation rather than merging with the
        # profile, so DFW's refs ride on `overrides`.
        assert "config" not in ft_body
        assert "workflow" not in ft_body
        overrides = ft_body["overrides"]
        assert overrides["data_pool"]["path"] == h.storage_http.PREFIX
        assert overrides["model"]["name"] == ITS_MODEL
        # A KPI prefix left in the old bucket is a cross-store read nothing else
        # here would catch.
        assert overrides["kpi"]["path"] == ITS_KPI_URI
        assert overrides["kpi"]["path"].startswith(f"{ITS_ARTIFACT_ROOT}/")

        assert overrides["model"]["checkpoint"] == (
            f"{ITS_ARTIFACT_ROOT}/runs/{ITS_BASELINE}/weights.onnx"
        )

        # The ONNX graph, not an engine: a prebuilt engine is pinned to the exact
        # GPU/driver/TensorRT triple it was built on.
        (pull,) = h.mm.bodies("POST /v1/models/pull")
        assert pull["format"] == ITS_MODEL_FORMAT == "onnx"
        assert pull["version"] == ITS_CANDIDATE
        assert pull["sha256"] == "a" * 64

    def test_the_record_carries_its_own_identity_and_an_integer_version(self, harness_factory):
        with _its_stack(harness_factory, real_deft_backend=True) as h:
            ctx = h.run_chain()
            _approve_the_full_switch(h)
            record = h.drive(ctx["promotion_id"], hitl=True)

            # Seeded at 9, so a correct mint crosses into two digits — where a
            # zero-padded or lexicographic mint breaks.
            assert record.candidate_version == h.candidate_version == ITS_CANDIDATE
            assert record.candidate_version.isdigit()
            assert str(int(record.candidate_version)) == record.candidate_version
            assert record.baseline_version == ITS_BASELINE

            assert record.deft_run_id == ITS_RUN_ID
            assert record.candidate_version != record.deft_run_id

            # The RUNTIME key, not the job type: the job type did its work at the
            # submission edge, and readers downstream resolve from `use_case`.
            assert record.use_case == ITS_USE_CASE
            assert record.model_format == ITS_MODEL_FORMAT
            assert record.model_name == ITS_MODEL

            all_bodies = [body for (_endpoint, body) in h.mm.calls if body is not None]
            assert all_bodies, "the Model Manager was never called"
            assert {_model_named_by(b) for b in all_bodies} == {ITS_MODEL}


# -- 2. Offline gate failure — v1's ONLY automatic gate --------------------- #


class TestOfflineGateBlocksBeforeAnythingIsMinted:
    """With the online gate deferred, this is the only automatic judgement left.

    It is the single thing standing between a bad fine-tune and a human being
    asked to approve it, so its diagnostics matter more, not less.
    """

    def test_map50_below_target_blocks_before_a_version_or_a_pull(self, harness_factory, caplog):
        """A bad candidate must cost nothing downstream.

        The gate runs before the serving version is minted and before the Model
        Manager is touched, so a failure burns no serving integer (a permanent
        hole in the sequence) and stages no weights to clean up.
        """
        import logging

        with (
            caplog.at_level(logging.INFO, logger="data_flywheel.backends.deft.eval"),
            _its_stack(harness_factory, real_deft_backend=True, metrics=ITS_FAILING_METRICS) as h,
        ):
            ctx = h.run_chain()

        assert "gate failed" in ctx["error"]
        # A bare `actual=<number>` with no `reason=`: a comparison the candidate
        # lost, not a metric key that missed.
        unmet = [r.message for r in caplog.records if "constraints not met" in r.message]
        assert unmet, "the gate did not report which constraint it rejected on"
        assert "mAP50 >= 0.55 (actual=0.41)" in unmet[0]
        assert "reason=" not in unmet[0]
        # `recall` was reported (0.92) and is not gated, so it must not appear.
        assert "recall" not in unmet[0]

        assert "candidate_version" not in ctx
        assert "promotion_id" not in ctx
        assert h.db.serving_version_counters.find_one({"_id": ITS_MODEL})["seq"] == int(
            ITS_BASELINE
        )
        assert h.mm.calls == []
        assert h.mlflow.aliases.get("candidate") is None
        assert h.mlflow.aliases["production"] == ITS_BASELINE
        assert h.db[PROMOTIONS_COLLECTION].find({}) == []
        # Training genuinely ran — a gate decision, not a broken chain.
        assert h.deft_http.bodies("POST", "/v1/fine-tune")
        h.flywheel_db.mark_flywheel_run_error.assert_called_once()

    def test_an_unemitted_metric_key_does_not_masquerade_as_a_bad_model(
        self, harness_factory, caplog
    ):
        """The shipped key is confirmed only against a DRAFT MR — that risk, driven.

        DEFT returns a good detector reporting under a different key (``AP50``).
        The fail-closed gate rejects it, which is right; what must not happen is
        that the rejection reads like every other rejection. A key that does not
        match DEFT verbatim rejects EVERY candidate FOREVER while every stage
        reports success — indistinguishable from a healthy flywheel that has not
        got lucky yet, unless the message says so the first time.
        """
        import logging

        with (
            caplog.at_level(logging.INFO, logger="data_flywheel.backends.deft.eval"),
            _its_stack(
                harness_factory, real_deft_backend=True, metrics=ITS_MISNAMED_KEY_METRICS
            ) as h,
        ):
            ctx = h.run_chain()

        assert "gate failed" in ctx["error"]
        unmet = [r.message for r in caplog.records if "constraints not met" in r.message]
        assert unmet
        assert "mAP50 >= 0.55 (actual=None, reason=metric-not-reported)" in unmet[0]
        # The two failures cost the same, so only the message tells them apart.
        assert "promotion_id" not in ctx
        assert h.mm.calls == []


class TestAnEmptySnapshotNeverReachesDeft:
    """The shipped ITS failure, end to end: a selection that matched nothing.

    ``tag: "its-small-200b"`` shipped as a facet VALUE no producer stamps. The
    name is real, so the Storage Backend compiled it, matched zero records and
    answered READY — and DEFT fine-tuned on an empty pool while every stage
    reported success. Nothing downstream can tell that from a healthy run, so
    the count is checked where it is first knowable.
    """

    def test_a_ready_but_empty_snapshot_stops_the_chain_at_selection(self, harness_factory):
        with _its_stack(harness_factory, real_deft_backend=True) as h:
            # READY with nothing in it.
            h.storage_http.snapshot_records = 0
            ctx = h.run_chain()

            # The snapshot WAS taken: emptiness is only visible afterwards.
            assert h.storage_http.calls
            assert "0 records" in ctx["error"]
            assert h.storage_http.SNAPSHOT_ID in ctx["error"]
            assert "snapshot_id" not in ctx

            # Nothing downstream happened.
            assert h.deft_http.bodies("POST", "/v1/fine-tune") == []
            assert "candidate_version" not in ctx
            assert h.db.serving_version_counters.find_one({"_id": ITS_MODEL})["seq"] == int(
                ITS_BASELINE
            )
            assert h.mlflow.aliases.get("candidate") is None
            assert h.mlflow.aliases["production"] == ITS_BASELINE
            assert "promotion_id" not in ctx
            assert h.db[PROMOTIONS_COLLECTION].find({}) == []
            assert h.mm.calls == []
            h.flywheel_db.mark_flywheel_run_error.assert_called_once()


# -- 3. An undeclared use case fails closed --------------------------------- #


class TestUndeclaredUseCaseFailsClosed:
    def test_a_use_case_with_no_endpoint_entry_never_snapshots(self, harness_factory):
        """The guard that stands between a misconfigured run and the wrong store.

        Declaring ``use_case_endpoints`` states that use cases do NOT share
        storage. From then on an undeclared use case would silently INHERIT the
        top-level endpoints — an ITS run training on AOI's inspection images and
        graded on AOI's records. Not a crash, a wrong number that gates a
        promotion, so the stage refuses to start.

        This is a USE-CASE guard: ``its_job`` buys a dispatcher branch and a YAML
        block, not an object store.
        """
        with _its_stack(
            harness_factory,
            # ITS missing but the map non-empty, so the guard is armed.
            endpoints_override={"aoi": {}, "aoi_family_sibling": {}},
        ) as h:
            ctx = h.run_chain()

            # NMP parity: the stage stamps the error, no task raises out.
            assert "use_case_endpoints" in ctx["error"]
            assert "'its'" in ctx["error"]
            # The message names what IS declared, so the fix is one edit away.
            assert "aoi_family_sibling" in ctx["error"]

            assert h.storage_http.calls == []
            assert h.deft_http.calls == []
            assert "snapshot_id" not in ctx
            assert "promotion_id" not in ctx
            assert h.mm.calls == []
            h.flywheel_db.mark_flywheel_run_error.assert_called_once()


# -- 4. ACCEPTANCE: two flywheels, one process ------------------------------ #


class _RoutingTraining:
    """ONE DEFT fine-tune service for the whole deployment.

    ``deft_base_url`` is not a ``UseCaseEndpoints`` field — sibling use cases
    share one DEFT service and ``training()`` takes no ``use_case`` — so this
    routes by the identity the call carries: the model name on ``start_run``,
    then the run id it handed back.
    """

    def __init__(self, by_model: dict[str, Any]):
        self._by_model = by_model
        self._by_run: dict[str, Any] = {}

    def start_run(self, **kwargs):
        model_name = kwargs["model_name"]
        sub = self._by_model[model_name]
        handle = sub.start_run(**kwargs)
        self._by_run[handle["id"]] = sub
        return handle

    def get_status(self, run_id):
        return self._by_run[run_id].get_status(run_id)

    def fetch_artifact(self, run_id):
        return self._by_run[run_id].fetch_artifact(run_id)

    def cancel(self, run_id):
        return self._by_run[run_id].cancel(run_id)


class TestTwoFlywheelsOneProcess:
    """THE ACCEPTANCE TEST for "a second job type, not a second backend".

    ``src/backends/factory.py`` caches exactly ONE provider per process, so
    putting ITS on the existing DEFT backend is only sound if one provider
    object can serve both use cases at once, routing every seam it owns.

    So: one REAL ``DeftBackendProvider``, both runs, ONE worker/beat loop, one
    Mongo, one MLflow registry. If this cannot pass, the fix is NOT a harness per
    use case — the process-wide provider is the wrong seam and it needs
    escalating.

    The two rollouts also run visibly different ladders off their own policies,
    which is the strongest available statement that policy is resolved per
    promotion and not per process.
    """

    def _build(self, harness_factory):
        from src.backends.deft.eval import get_evaluator
        from src.backends.deft.provider import DeftBackendProvider

        # ONE registry: `_build_mlflow()` takes no argument, so production could
        # never ask for a per-use-case one.
        registry: dict[str, dict] = {}
        aoi = harness_factory(mlflow_registry=registry)
        its = harness_factory(**_its_harness_kwargs(mlflow_registry=registry))

        sources = {"aoi": aoi.online_samples, ITS_USE_CASE: its.online_samples}
        training = _RoutingTraining(
            {MODEL: aoi.provider.training_mock, ITS_MODEL: its.provider.training_mock}
        )

        class _OneProviderTwoUseCases(DeftBackendProvider):
            """The real provider, resolving BOTH use cases from one object."""

            def training(self):
                return training

            def evaluator(self, use_case=None):
                if use_case is None:
                    return None
                # Only the sample source is injected.
                return get_evaluator(
                    use_case, results_source=lambda window, role: sources[use_case]
                )

        return aoi, its, _OneProviderTwoUseCases()

    def test_an_aoi_run_and_an_its_run_do_not_see_each_other(self, harness_factory):
        with _deployment_settings():
            # Inside the settings block: endpoints resolve at construction.
            aoi, its, provider = self._build(harness_factory)

            with ExitStack() as stack:
                # ONE stack for both: nested stacks are last-writer-wins and the
                # first harness would lose its fakes to the second.
                for patcher in aoi.patches(also=[its], provider=provider):
                    stack.enter_context(patcher)

                aoi_ctx = aoi.run_chain()
                its_ctx = its.run_chain()
                # One beat loop over BOTH promotions, so a job type resolving the
                # wrong evaluator or policy corrupts its sibling here.
                aoi_record = aoi.drive(aoi_ctx["promotion_id"])
                its_record = aoi.drive(its_ctx["promotion_id"])

                # The two ladders diverge in ONE process, off their own policies.
                assert aoi_record.state == "promoted"
                assert its_record.state == "pending_approval"
                assert its_record.pending_gate == "full"

                _approve_the_full_switch(its)
                its_record = aoi.drive(its_ctx["promotion_id"], hitl=True)

                # Same object served both runs — the claim under test.
                from src.backends.factory import get_backend_provider

                assert get_backend_provider() is provider

        assert aoi_record.state == "promoted", "the AOI rollout did not complete"
        assert its_record.state == "promoted", "the ITS rollout did not complete"
        assert (aoi_record.use_case, its_record.use_case) == ("aoi", ITS_USE_CASE)

        # Each from its OWN store. AOI keeps the unprefixed alias, ITS names its
        # catalog, and each one's opposite is asserted absent, so inverting the
        # map fails here rather than returning an empty pool. The fakes answer on
        # different hosts too, so a call to the wrong store lands in the wrong
        # list. No facet separates the rows — routing already did.
        (aoi_snap,) = aoi.storage_http.bodies("POST", "/v1/snapshot")
        (its_snap,) = its.storage_http.bodies("POST", "/v1/its/snapshot")
        assert aoi.storage_http.bodies("POST", "/v1/aoi/snapshot") == []
        assert its.storage_http.bodies("POST", "/v1/snapshot") == []
        assert aoi_snap["selection"] == {"created_after": "1970-01-01T00:00:00Z"}
        assert its_snap["selection"] == ITS_FILTERS
        # AOI pulls a KPI set from storage; ITS uses a static uri.
        assert aoi.storage_http.bodies("POST", "/v1/kpi") != []
        assert its.storage_http.bodies("POST", "/v1/kpi") == []
        # Neither store was asked to presign the other's artifact.
        aoi_presign = aoi.storage_http.bodies("POST", "/v1/get_presigned_urls")
        its_presign = its.storage_http.bodies("POST", "/v1/get_presigned_urls")
        assert all(
            uri.startswith("s3://aoi-artifacts") for body in aoi_presign for uri in body["urls"]
        )
        assert all(
            uri.startswith(ITS_ARTIFACT_ROOT) for body in its_presign for uri in body["urls"]
        )

        # One control plane, two models, told apart by the model they name. The
        # router raises on a model it does not know, so a request naming the
        # wrong one cannot be absorbed silently.
        aoi_bodies = [b for (_e, b) in aoi.mm.calls if b is not None]
        its_bodies = [b for (_e, b) in its.mm.calls if b is not None]
        assert {_model_named_by(b) for b in aoi_bodies} == {MODEL}
        assert {_model_named_by(b) for b in its_bodies} == {ITS_MODEL}
        # The ladders differ, per each run's own deployment_stages.
        assert _applies(aoi) == ["shadow", "canary", "canary", "full_switch"]
        assert _applies(its) == ["shadow", "full_switch"]
        _assert_no_canary_was_ever_issued(its)

        # Each minted off its own counter.
        assert (aoi_record.candidate_version, its_record.candidate_version) == (
            CANDIDATE,
            ITS_CANDIDATE,
        )
        assert aoi.mm.default_version == CANDIDATE
        assert its.mm.default_version == ITS_CANDIDATE

        # One MLflow registry, two models, neither clobbered.
        assert aoi.mlflow.registry is its.mlflow.registry
        assert aoi.mlflow.aliases["production"] == CANDIDATE
        assert its.mlflow.aliases["production"] == ITS_CANDIDATE

        docs = aoi.db[PROMOTIONS_COLLECTION].find({})
        assert len(docs) == 2
        assert sorted(d["use_case"] for d in docs) == ["aoi", ITS_USE_CASE]

        # Only ONE was graded online: the POLICY, not the process, decides which
        # gates run.
        assert "FAR_pct" in (aoi_record.eval_metrics or {})
        assert its_record.eval_metrics == ITS_PASSING_METRICS
        assert "FAR_pct" not in its_record.eval_metrics
        assert "precision_pct" not in its_record.eval_metrics
        assert its_record.verdict is None


# -- 5. Guards on the fixtures themselves ----------------------------------- #


class TestTheHarnessIsGenuinelyShared:
    """If these fail, the scenarios above are testing a fork, not the pipeline."""

    def test_its_and_aoi_run_the_same_harness_class(self, harness_factory):
        aoi = harness_factory()
        its = harness_factory(**_its_harness_kwargs())
        assert type(aoi) is type(its) is E2EHarness

    def test_the_its_harness_really_carries_the_its_job_type(self, harness_factory):
        """The two axes, stated separately and asserted separately.

        If the harness derived one from the other, every ITS scenario would be
        an AOI submission wearing an ITS use case. Building the harness also runs
        the mapped pipeline's ``validate_config`` (see
        ``_e2e_fakes._assert_pipeline_accepts``), so this is the submission-edge
        gate too — a real ``POST /jobs`` meets the same method.
        """
        its = harness_factory(**_its_harness_kwargs())
        assert its.job_type == "its_job" == JobType.ITS.value
        assert its.use_case == ITS_USE_CASE
        assert harness_factory().job_type == "aoi_job"

    @pytest.mark.parametrize("bad_job_type", ["its", "llm_job"])
    def test_a_typoed_job_type_is_refused_before_a_single_stage_runs(
        self, harness_factory, bad_job_type
    ):
        """The harness must not be a place where a rejected submission goes green.

        ``"its"`` is the plausible typo — the use case where the job type
        belongs; ``llm_job`` is a real job type outside the DEFT family. The
        pipeline gate refuses both exactly as ``POST /jobs`` would.
        """
        with pytest.raises(ValueError):
            harness_factory(**_its_harness_kwargs(job_type=bad_job_type))

    def test_an_unregistered_use_case_is_refused_at_initialize(self, harness_factory):
        """A typo'd use case must die in seconds, not a fine-tune later.

        Initialize resolves the evaluator before any DB write. Without that the
        run would snapshot, train, and fail at the offline gate — after the GPU
        time is spent.
        """
        with _its_stack(
            harness_factory,
            use_case="itz",  # typo
            endpoints_override={"aoi": {}, "itz": {}},
        ) as h:
            ctx = h.run_chain()

        assert "itz" in ctx["error"]
        assert h.storage_http.calls == []
        assert h.deft_http.calls == []
