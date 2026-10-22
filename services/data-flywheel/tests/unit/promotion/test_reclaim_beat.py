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

"""Disk-reclaim beat: the positive-knowledge enumeration rule (design §6).

Tests here have passed for the wrong reason before -- an unrelated guard refused
first -- so every negative is paired with a control showing the sweep DOES nominate
the same fixture once the guard under test is out of the way.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.config.deft import DiskReclaimConfig
from src.lib.promotion.deployment_backend.base import (
    BackendError,
    RoutingEntry,
    ServingState,
)
from src.tasks.promotion import reclaim_beat

MODEL = "changenet-aoi"
COUNTERS = "serving_version_counters"


# -- fixtures / helpers ------------------------------------------------------ #
def _ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def _state(
    *,
    known=("1", "2", "3", "4"),
    serving=("4",),
    default="4",
    routing=(("4", "default", 1.0),),
    routing_available=True,
    known_available=True,
) -> ServingState:
    return ServingState(
        model_name=MODEL,
        default_version=default,
        routing=[RoutingEntry(version=v, role=r, weight=w) for v, r, w in routing],
        serving_versions=list(serving),
        known_versions=list(known),
        ready=True,
        routing_available=routing_available,
        known_available=known_available,
    )


def _record(**overrides) -> dict:
    doc = {
        "promotion_id": f"promo-{overrides.get('candidate_version', 'x')}",
        "model_name": MODEL,
        "use_case": "aoi",
        # Not 'promoted': a promoted record pins its own candidate and baseline,
        # so the pin would refuse the version, not the guard under test.
        "state": "rejected",
        "candidate_version": "2",
        "baseline_version": None,
        "created_at": _ago(500),
        "settled_at": _ago(500),
    }
    doc.update(overrides)
    return doc


def _evicted(version: str, **overrides) -> dict:
    """The record that unloaded ``version`` at its full switch, stamp and all.

    Every nomination needs one (design §4.2): the sweep no longer unloads, so
    this stamp is the only thing saying the version left serving memory. Seeded
    ``rolled_back`` with a candidate no stack here knows, so it can neither pin
    the version under test nor nominate one of its own.
    """
    doc = _record(
        promotion_id=f"promo-evicted-{version}",
        state="rolled_back",
        candidate_version=f"successor-of-{version}",
        baseline_version=version,
        evicted_at=_ago(900),
        evicted_versions=[version],
        settled_at=_ago(900),
    )
    doc.update(overrides)
    return doc


def _seed(db, models=(MODEL,), records=(), evicted=()):
    """Seed counters, promotion records, and an eviction stamp per ``evicted``."""
    for name in models:
        db[COUNTERS].insert_one({"_id": name, "seq": 9})
    for rec in records:
        db["promotions"].insert_one(rec)
    for version in evicted:
        db["promotions"].insert_one(_evicted(version))


def _backend(state_by_model=None, *, state=None) -> MagicMock:
    backend = MagicMock()
    if state_by_model is not None:
        backend.serving_state.side_effect = lambda name: state_by_model.get(name)
    else:
        backend.serving_state.return_value = _state() if state is None else state
    backend.cleanup.return_value = {"version": "x", "status": "removed", "disk_reclaimed": True}
    return backend


def _cfg(**overrides) -> DiskReclaimConfig:
    fields = {"enabled": True, "dry_run": False, "rollback_retention_hours": 168.0}
    fields.update(overrides)
    return DiskReclaimConfig(**fields)


def _run(db, backend, cfg):
    """Run the beat with ``cfg``, ``db`` and ``backend`` injected.

    Returns ``(result, build_backend_mock)``: the mock is how a test asserts the
    backend was never CONSTRUCTED, which the result dict cannot show.
    """
    fake_settings = MagicMock()
    fake_settings.system_config.disk_reclaim = cfg
    build = MagicMock(return_value=backend)
    with (
        patch("src.config.settings", fake_settings),
        patch("src.lib.promotion.deployment_backend.build_deployment_backend", build),
        patch("src.tasks.promotion.controller._get_db", return_value=db),
    ):
        return reclaim_beat.disk_reclaim(), build


def _targets(backend) -> list[tuple[str, str]]:
    """(model, version) of every cleanup the sweep actually issued."""
    return [(c.args[0], c.args[1]) for c in backend.cleanup.call_args_list]


# -- the off switches -------------------------------------------------------- #
def test_disabled_is_a_noop(fake_db):
    """Asserts on CONSTRUCTION: the result dict looks the same whether or not the
    beat built an HTTP client and queried Mongo before noticing it was off.

    Mutation: move the ``enabled`` check below ``build_deployment_backend()`` -> red.
    """
    _seed(fake_db, records=[_record()])
    backend = _backend()
    result, build = _run(fake_db, backend, _cfg(enabled=False))

    build.assert_not_called()
    assert result == {"task": "disk_reclaim", "enabled": False}


def test_a_non_deft_system_config_is_a_noop(fake_db):
    """``disk_reclaim`` is a DeftSystemConfig field. On an nmp build -- the shipped
    default -- ``settings.system_config`` has no such attribute and is
    ``extra="forbid"``, so a direct read raises once an hour, forever.

    ``_seed`` inserts a counter so the build is genuinely reached; ``_run`` is no
    use here, its ``MagicMock`` settings auto-vivify ``disk_reclaim``.
    """
    from src.config.base import SystemConfig

    _seed(fake_db, records=[_record()])
    backend = _backend()
    build = MagicMock(return_value=backend)
    fake_settings = MagicMock()
    fake_settings.system_config = SystemConfig()  # a real object, NOT a MagicMock

    with (
        patch("src.config.settings", fake_settings),
        patch("src.lib.promotion.deployment_backend.build_deployment_backend", build),
        patch("src.tasks.promotion.controller._get_db", return_value=fake_db),
    ):
        result = reclaim_beat.disk_reclaim()

    build.assert_not_called()
    assert result == {"task": "disk_reclaim", "enabled": False}


def test_no_counters_is_a_noop(fake_db):
    """Query first, build the client only when there is work.

    Mutation: construct the backend before the counters query -> red.
    """
    backend = _backend()
    result, build = _run(fake_db, backend, _cfg())

    build.assert_not_called()
    assert result["models"] == 0


# -- that it drains AT ALL: the positive control the negatives lean on ------- #
def test_sweep_reclaims_a_settled_retired_candidate(fake_db):
    """Without this the feature can pass every negative test and still reclaim
    nothing, while reporting healthy counters.

    Mutation: pin ``candidate_version`` from EVERY record rather than only the
    latest promoted one -> the sweep becomes a permanent no-op.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(200)),
            _record(
                promotion_id="promo-live",
                candidate_version="4",
                baseline_version="3",
                state="promoted",
                settled_at=_ago(1),
            ),
        ],
        evicted=("2",),
    )
    backend = _backend()
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert _targets(backend) == [(MODEL, "2")]
    assert result["reclaimed"] == 1


def test_dry_run_forwards_the_flag_and_counts_withheld(fake_db):
    """Mutation: drop the ``dry_run`` kwarg -> the "dry run" deletes for real."""
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(200)),
            _record(
                promotion_id="promo-live",
                candidate_version="4",
                baseline_version="3",
                state="promoted",
                settled_at=_ago(1),
            ),
        ],
        evicted=("2",),
    )
    backend = _backend()
    backend.cleanup.return_value = {"version": "2", "status": "removed", "disk_reclaimed": False}
    result, _ = _run(fake_db, backend, _cfg(dry_run=True, keep_recent_versions=0))

    assert backend.cleanup.call_args.kwargs["dry_run"] is True
    assert backend.cleanup.call_args.kwargs["min_age_s"] == 24.0 * 3600.0
    assert result["withheld"] == 1
    assert result["reclaimed"] == 0


# -- the negatives, each paired with a control that proves the guard RAN ----- #
def test_empty_promotion_records_nominate_nothing(fake_db):
    """Absence is UNKNOWN, never permission. After a Mongo restore -- counter
    seeded, zero promotion records, real versions on disk -- ``known_versions``
    minus exclusions would delete every one of them, live incumbent included.

    Mutation: switch eligibility to ``known_versions - excluded`` -> red
    (versions 1, 2, 3 get nominated).
    """
    _seed(fake_db, records=[])
    backend = _backend()
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    backend.cleanup.assert_not_called()
    # Not skipped for some other reason: the model really was inspected.
    assert result["skipped_models"] == 0
    backend.serving_state.assert_called_once_with(MODEL)


@pytest.mark.parametrize("state", ["pending_approval", "approved", "promoting"])
def test_skips_a_model_with_a_non_terminal_promotion(fake_db, state):
    """The only check anywhere that can see "about to be routed".

    The fixture is the one proven nominated above plus one in-flight record, so
    the in-flight check is the only thing that can be refusing.

    Mutation: check only ``promoting`` -> red for the other two parameters.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", baseline_version="1", settled_at=_ago(200)),
            _record(
                promotion_id="promo-inflight", candidate_version="9", state=state, settled_at=None
            ),
        ],
        evicted=("2",),
    )
    backend = _backend()
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    backend.cleanup.assert_not_called()
    assert result["skipped_models"] == 1


def test_skips_a_model_whose_serving_state_is_unknown(fake_db):
    """``None`` means "I could not find out", never "the stack holds nothing".

    The fixture is the proven-nominating one, so only the fail-closed serving
    read can be refusing here.

    Mutation: treat ``None`` as an empty ServingState -> red.
    """
    _seed(fake_db, records=[_record(candidate_version="2", settled_at=_ago(200))], evicted=("2",))
    backend = _backend(state_by_model={})  # serving_state -> None
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    backend.cleanup.assert_not_called()
    assert result["skipped_models"] == 1


@pytest.mark.parametrize("flag", ["routing_available", "known_available"])
def test_skips_a_model_whose_serving_read_is_degraded(fake_db, flag):
    """An empty list under a False availability flag is unknown, not empty.

    Mutation: drop either flag from the skip condition -> red for that parameter.
    """
    _seed(fake_db, records=[_record(candidate_version="2", settled_at=_ago(200))], evicted=("2",))
    backend = _backend(state=_state(**{flag: False}))
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    backend.cleanup.assert_not_called()
    assert result["skipped_models"] == 1


def test_never_nominates_the_current_live_primary_or_rollback_target(fake_db):
    """The two permanent pins on the latest promoted record. Every record here is
    800-900h old, so the retention clause cannot be what refuses 3 and 4.

    Mutation: release the pins on the retention clock -> red, and version 4 (the
    live primary) and version 3 (the rollback target) are both deleted.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(900)),
            # Ancient and terminal in its own right, so version 3 is nominated
            # and then withheld only as the latest promoted's baseline.
            _record(promotion_id="promo-3", candidate_version="3", settled_at=_ago(900)),
            _record(
                promotion_id="promo-live",
                candidate_version="4",
                baseline_version="3",
                state="promoted",
                settled_at=_ago(800),
            ),
        ],
        # Stamped, so a missing stamp cannot be refusing 3 and 4.
        evicted=("2", "3", "4"),
    )
    # Names nothing, so serving/default/routing cannot protect 4 either. The pins
    # are the only guard left.
    backend = _backend(state=_state(serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    nominated = {v for _m, v in _targets(backend)}
    assert "4" not in nominated  # live primary
    assert "3" not in nominated  # rollback target
    assert nominated == {"2"}  # ...and it did nominate, so the enumeration ran


def test_a_routed_version_is_never_nominated(fake_db):
    """A version carrying traffic, named by an ancient settled record. Version 2 is
    terminal, 900h old and neither candidate nor baseline of the latest promoted
    record, so only the routing/serving subtraction can be refusing it.

    Mutation: drop ``serving_versions`` and ``routing`` from ``excluded`` -> red,
    and the sweep deletes the directory of a version taking live traffic.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(900)),
            _record(promotion_id="promo-1", candidate_version="1", settled_at=_ago(900)),
            # Pins 9/8, which the stack does not know, so the pins cannot cover
            # version 2 by accident.
            _record(
                promotion_id="promo-latest",
                candidate_version="9",
                baseline_version="8",
                state="promoted",
                settled_at=_ago(10),
            ),
        ],
        evicted=("1", "2"),
    )
    backend = _backend(
        state=_state(
            known=("1", "2", "3", "4"),
            serving=("2",),
            default=None,
            routing=(("2", "canary", 0.25),),
        )
    )
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    nominated = {v for _m, v in _targets(backend)}
    assert "2" not in nominated
    assert nominated == {"1"}  # version 1 proves the enumeration ran at all


def test_releases_a_superseded_baseline_only_outside_the_window(fake_db):
    """Both directions, because there are two ways to break it: no ``settled_at``
    clause deletes a baseline retired an hour ago, a permanent pin never drains.
    """

    def _sweep(decided_hours_ago: float) -> set[str]:
        db = type(fake_db)()
        _seed(
            db,
            records=[
                _record(candidate_version="2", settled_at=_ago(decided_hours_ago)),
                _record(
                    promotion_id="promo-latest",
                    candidate_version="9",
                    baseline_version="8",
                    state="promoted",
                    settled_at=_ago(0.5),
                ),
            ],
            evicted=("2",),
        )
        backend = _backend(state=_state(serving=(), default=None, routing=()))
        _run(db, backend, _cfg(keep_recent_versions=0))
        return {v for _m, v in _targets(backend)}

    assert "2" not in _sweep(1)  # inside the window: still pinned
    assert "2" in _sweep(200)  # outside it: released


def test_an_undated_terminal_record_is_not_settled(fake_db):
    """An unknown settle time must read as "not settled yet": undated must never
    become a delete. Version 5 is otherwise fully eligible -- terminal, known,
    unrouted, unpinned -- so nothing else can be refusing it.

    Mutation: ``settled_at is None or settled_at > cutoff`` ->
    ``settled_at is not None and settled_at > cutoff`` (undated = old enough) -> red.
    """
    _seed(
        fake_db,
        records=[
            _record(
                promotion_id="promo-undated",
                candidate_version="5",
                created_at=None,
                settled_at=None,
            ),
            _record(candidate_version="2", settled_at=_ago(900)),
        ],
        # 5 is stamped, so only its missing settle time can refuse it.
        evicted=("2", "5"),
    )
    backend = _backend(state=_state(known=("1", "2", "5"), serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    nominated = {v for _m, v in _targets(backend)}
    assert "5" not in nominated
    assert nominated == {"2"}  # the dated record proves the enumeration ran


def test_an_approval_time_is_not_a_settle_time(fake_db):
    """An approver's ``decided_at`` must never start the retention clock. On the
    HITL path it is stamped when approval was GRANTED, which can be days before
    the rollout ends: approved 200h ago, finished an hour ago, window 168 hours --
    reading ``decided_at`` would release a version that settled an hour ago.

    Mutation: point ``_settled_at`` back at the chain it used to read
    (``decided_at or created_at``) -> version 5 clears the cutoff -> red.
    """
    _seed(
        fake_db,
        records=[
            _record(
                promotion_id="promo-hitl",
                candidate_version="5",
                settled_at=_ago(1),
                decided_at=_ago(200),
            ),
            _record(candidate_version="2", settled_at=_ago(900)),
        ],
        # 5 is stamped and terminal, so only its settle time can refuse it.
        evicted=("2", "5"),
    )
    backend = _backend(state=_state(known=("1", "2", "5"), serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    nominated = {v for _m, v in _targets(backend)}
    assert "5" not in nominated
    assert nominated == {"2"}


def test_the_pin_ranks_undated_promoted_records_by_created_at(fake_db):
    """Ranking fails the other way round from eligibility: guessing a settle time
    deletes early, but picking too OLD a promoted record leaves the true latest
    one's baseline -- the live rollback target -- unpinned. So undated promoted
    records still rank by ``created_at``, and the newer one is inserted FIRST here.

    Mutation: point ``_latest_promoted`` at ``_settled_at`` instead of
    ``_ranked_at`` -> both undated records tie, document order picks the OLDER
    one, its baseline pin swallows version 1 -> red.
    """
    _seed(
        fake_db,
        records=[
            _record(
                promotion_id="promo-new",
                state="promoted",
                candidate_version="3",
                baseline_version="2",
                created_at=_ago(10),
                settled_at=None,
            ),
            _record(
                promotion_id="promo-old",
                state="promoted",
                candidate_version="2",
                baseline_version="1",
                created_at=_ago(1000),
                settled_at=None,
            ),
            _record(promotion_id="promo-1", candidate_version="1", settled_at=_ago(900)),
        ],
        evicted=("1",),
    )
    backend = _backend(
        state=_state(
            known=("1", "2", "3"), serving=("3",), default="3", routing=(("3", "default", 1.0),)
        )
    )
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert {v for _m, v in _targets(backend)} == {"1"}


def test_an_unstamped_version_is_never_nominated(fake_db):
    """The sweep stopped unloading, so the eviction stamp is the only thing that
    says a version left serving memory. Without it a nomination could delete the
    directory of a version still resident on a GPU. Version 2 is otherwise fully
    eligible; version 1, identical but stamped, proves the enumeration ran.

    Mutation: drop the ``nominated &= _evicted_versions(records)`` line -> red.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(900)),
            _record(promotion_id="promo-1", candidate_version="1", settled_at=_ago(900)),
        ],
        evicted=("1",),
    )
    backend = _backend(state=_state(serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert _targets(backend) == [(MODEL, "1")]


def test_a_rejected_candidate_is_collectable_only_once_its_own_record_stamps_it(fake_db):
    """Rejected candidates are the flywheel's dominant output. The controller evicts
    one that ends terminal and unrouted and stamps that on the candidate's OWN
    record, so the stamp echoes ``candidate_version``, not ``baseline_version``. Both
    halves come from one record, so the eviction is all that changed the answer.

    Mutation: compare the echo against ``baseline_version`` alone (the shape the
    stamp had when only the full switch evicted) -> red on the second half, and
    every rejected candidate stays uncollectable for the rest of time.
    """
    rejected = _record(candidate_version="2", state="rejected", settled_at=_ago(900))
    _seed(fake_db, records=[rejected])
    backend = _backend(state=_state(serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))
    assert _targets(backend) == []

    # The same record, plus the one fact the controller now stamps on it.
    fake_db["promotions"].update_one(
        {"promotion_id": rejected["promotion_id"]},
        {"$set": {"evicted_at": _ago(900), "evicted_versions": ["2"]}},
    )
    backend = _backend(state=_state(serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))
    assert _targets(backend) == [(MODEL, "2")]


def test_a_stamp_whose_version_echo_disagrees_is_ignored(fake_db):
    """``evicted_versions`` repeats versions the record already names, so that a
    hand-edited or half-migrated record cannot retire some OTHER version: a stamp
    naming v3 must never retire v4. Below, the stamp says "2" on a record whose
    baseline is "7" and candidate is "successor-of-2".

    Mutation: drop the echo comparison in ``_evicted_versions`` -> red. Version 1
    (stamped consistently) proves the enumeration ran.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(900)),
            _record(promotion_id="promo-1", candidate_version="1", settled_at=_ago(900)),
            _evicted("2", baseline_version="7"),  # the echo still says "2"
        ],
        evicted=("1",),
    )
    backend = _backend(state=_state(serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert _targets(backend) == [(MODEL, "1")]


def test_a_record_that_evicted_twice_nominates_both_versions():
    """A full switch stamps the baseline; a later converged rollback stamps the
    candidate on the SAME record. Both directories are retired and both must be
    collectable -- a single stamp slot lost the first, forever.

    Mutation: read only ``evicted_versions[-1]`` -> red on the baseline.
    """
    both = _record(
        promotion_id="promo-twice",
        state="rolled_back",
        baseline_version="1",
        candidate_version="2",
        evicted_at=_ago(900),
        evicted_versions=["1", "2"],
    )
    assert reclaim_beat._evicted_versions([both]) == {"1", "2"}


def test_a_stamp_written_before_the_field_became_a_list_still_counts():
    """Records stamped by the singular ``evicted_version`` are already in Mongo, and
    treating them as unstamped strands their directories. The echo check is unchanged,
    so the old shape gains no authority the new one lacks.

    Mutation: drop the singular read -> red, and everything retired pre-migration leaks.
    """
    legacy = _record(
        promotion_id="promo-legacy",
        state="rolled_back",
        baseline_version="1",
        candidate_version="2",
        evicted_at=_ago(900),
        evicted_version="1",
    )
    assert reclaim_beat._evicted_versions([legacy]) == {"1"}
    # Still only what the record names: a migrated-wrong singular stamp is inert.
    assert reclaim_beat._evicted_versions([{**legacy, "evicted_version": "9"}]) == set()


def test_a_stamp_on_a_record_in_an_unrecognised_state_does_not_count():
    """The two state sets are complements today, so an in-flight stamp is already
    caught by the whole-model skip. The hazard is a new ``PromotionState`` in
    neither set: neither skipped nor terminal, so its stamp would authorise a
    delete for a promotion that has not finished. Asserted on the helper -- the
    beat cannot express a state the enum does not have.

    Mutation: drop the ``_TERMINAL_STATES`` check in ``_evicted_versions`` -> red.
    """
    stamped = _evicted("2")
    assert reclaim_beat._evicted_versions([stamped]) == {"2"}
    assert reclaim_beat._evicted_versions([{**stamped, "state": "quarantining"}]) == set()


def test_the_sweep_asserts_the_recorded_eviction(fake_db):
    """On a backend that DROPS the status row on unload, the caller's assertion is
    the only thing left that can affirm the eviction, now that cleanup performs
    none. Without it the sweep reclaims nothing there and the volume fills quietly.

    Mutation: drop the kwarg, or send it from somewhere without a stamp -> red.
    """
    _seed(
        fake_db,
        records=[_record(candidate_version="2", settled_at=_ago(900))],
        evicted=("2",),
    )
    backend = _backend(state=_state(serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert _targets(backend) == [(MODEL, "2")]
    assert backend.cleanup.call_args.kwargs["evicted_by_caller"] is True


def test_never_probes_versions_the_stack_does_not_know(fake_db):
    """The counter is a high-water mark with gaps; ``known_versions`` is the truth.

    Mutation: drop the ``& known`` intersection -> red, and the sweep makes an
    HTTP round trip, with synchronous retry sleeps, per phantom version.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(200)),
            _record(promotion_id="promo-gap", candidate_version="7", settled_at=_ago(200)),
        ],
        evicted=("2", "7"),
    )
    backend = _backend(state=_state(known=("1", "2"), serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert _targets(backend) == [(MODEL, "2")]


def test_keep_recent_versions_withholds_the_newest(fake_db):
    """Defence in depth: being eligible is not enough if the version is recent.

    Mutation: ignore ``keep_recent_versions`` -> red.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version=v, baseline_version=None, settled_at=_ago(900))
            for v in ("1", "2", "3", "4")
        ],
        evicted=("1", "2", "3", "4"),
    )
    backend = _backend(state=_state(serving=(), default=None, routing=()))
    _run(fake_db, backend, _cfg(keep_recent_versions=2))

    assert {v for _m, v in _targets(backend)} == {"1", "2"}


# -- the invariant that must never break ------------------------------------- #
def test_never_writes_to_serving_version_counters(fake_db):
    """Reclaiming lowers the observed floor, so the monotonic Mongo counter is the
    only thing left stopping DFW re-minting onto an occupied directory. Tidying up
    the counter of "a model with no versions left" is unrecoverable: the next mint
    silently overwrites a live version's weights.

    Mutation: add ANY write to that collection (``delete_one``, ``update_one``,
    ``$set``) inside the sweep -> red.
    """
    _seed(
        fake_db,
        records=[
            _record(candidate_version="2", settled_at=_ago(900)),
            _record(
                promotion_id="promo-live",
                candidate_version="4",
                baseline_version="3",
                state="promoted",
                settled_at=_ago(1),
            ),
        ],
        evicted=("2",),
    )
    counters = fake_db[COUNTERS]
    before = [dict(d) for d in counters.docs]
    for verb in ("insert_one", "update_one", "update_many", "find_one_and_update"):
        setattr(counters, verb, MagicMock(side_effect=AssertionError(f"wrote {verb} to counters")))
    counters.delete_one = MagicMock(side_effect=AssertionError("deleted from counters"))
    counters.delete_many = MagicMock(side_effect=AssertionError("deleted from counters"))

    backend = _backend()
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    # An inert sweep would pass this vacuously.
    assert result["reclaimed"] == 1
    assert [dict(d) for d in counters.docs] == before


def test_marks_the_record_when_a_reclaimed_version_was_a_baseline(fake_db):
    """Mutation: drop the ``_mark_version_reclaimed`` call -> red, and a record
    keeps advertising a rollback target whose weights are gone."""
    _seed(
        fake_db,
        records=[
            _record(promotion_id="promo-old", candidate_version="1", settled_at=_ago(900)),
            # Stamped inline: this record IS the one that evicted version 1.
            _record(
                promotion_id="promo-uses-1",
                candidate_version="5",
                baseline_version="1",
                state="rolled_back",
                settled_at=_ago(900),
                evicted_at=_ago(900),
                evicted_versions=["1"],
            ),
            _record(
                promotion_id="promo-live",
                candidate_version="7",
                baseline_version="6",
                state="promoted",
                settled_at=_ago(1),
            ),
        ],
    )
    backend = _backend(state=_state(known=("0", "1", "4"), serving=(), default=None, routing=()))
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert (MODEL, "1") in _targets(backend)
    # A ``_mark_...`` returning None on success would keep the abort test green.
    assert result["aborted"] is False
    assert result["errors"] == 0
    flagged = {
        d["promotion_id"] for d in fake_db["promotions"].docs if d.get("rollback_target_reclaimed")
    }
    assert flagged == {"promo-uses-1"}


def test_a_failed_rollback_target_mark_is_counted_and_aborts_the_tick(fake_db):
    """The delete cannot be undone, but the tick must not report clean, and it must
    stop -- the next target shares this handle. The dependent record is ``failed``
    because ``rollback`` returns on a rolled-back one without reading the flag.

    Mutation: restore the bare ``except Exception: logger.exception`` / ``-> None``
    in ``_mark_version_reclaimed`` -> both asserts red. A ``caplog``-at-ERROR
    variant would be worthless: ``logger.exception`` already fires today.
    """
    _seed(
        fake_db,
        records=[
            _record(promotion_id="promo-old", candidate_version="1", settled_at=_ago(900)),
            _record(
                promotion_id="promo-uses-1",
                candidate_version="5",
                baseline_version="1",
                state="failed",
                settled_at=_ago(900),
                evicted_at=_ago(900),
                evicted_versions=["1"],
            ),
        ],
    )
    fake_db["promotions"].update_many = MagicMock(side_effect=RuntimeError("stepdown"))
    backend = _backend(state=_state(known=("0", "1", "4"), serving=(), default=None, routing=()))

    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    # Cleanup really was issued, so the asserts below are about the mark alone.
    assert (MODEL, "1") in _targets(backend)
    assert result["reclaimed"] == 1
    assert result["errors"] == 1
    assert result["aborted"] is True


# -- retiring a nomination: the loop that never ended ------------------------ #
def test_a_reclaimed_version_is_never_offered_again(fake_db):
    """``known_versions`` is the serving stack's loaded-model list, not a directory
    listing, so a reclaimed version keeps its row and stays nominated. Only the
    record of the reclaim retires it, which also keeps the tally honest:
    ``reclaimed`` cannot tell a directory this call deleted from one already gone.

    Mutation: drop the ``candidate_reclaimed`` skip in ``_eligible_versions`` ->
    the second tick offers version 1 again and counts it twice -> red. The
    per-tick cap makes tick two its own control: version 2 IS offered, so a sweep
    that had merely gone inert fails here too.
    """
    _seed(
        fake_db,
        records=[
            _record(promotion_id="promo-1", candidate_version="1", settled_at=_ago(900)),
            _record(promotion_id="promo-2", candidate_version="2", settled_at=_ago(900)),
        ],
        evicted=("1", "2"),
    )
    cfg = _cfg(keep_recent_versions=0, max_versions_per_model_per_tick=1)
    backend = _backend(state=_state(known=("1", "2"), serving=(), default=None, routing=()))

    first, _ = _run(fake_db, backend, cfg)
    second, _ = _run(fake_db, backend, cfg)

    assert _targets(backend) == [(MODEL, "1"), (MODEL, "2")]
    assert first["reclaimed"] == 1
    assert second["reclaimed"] == 1
    assert first["attempted"] == 1
    assert second["attempted"] == 1


def test_a_reclaim_marks_the_nominating_record_without_failing_its_rollback(fake_db):
    """Two flags, only one of them dangerous. ``rollback_target_reclaimed`` belongs
    to the record whose BASELINE went, and the rollback precheck turns it into
    ``state="failed"``; ``candidate_reclaimed`` belongs to the record that NOMINATED
    the version and is read by nothing but this sweep.

    Mutation: have the candidate write set ``rollback_target_reclaimed`` too --
    the shape a single combined write would produce -> red, and in production the
    nominating record is flagged as having lost a rollback target it never lost,
    so its own rollback fails on a baseline still on the volume.
    """
    _seed(
        fake_db,
        records=[
            _record(promotion_id="promo-1", candidate_version="1", settled_at=_ago(900)),
            # Stamped inline: this record evicted version 1 and names it as its
            # BASELINE only.
            _record(
                promotion_id="promo-uses-1",
                candidate_version="5",
                baseline_version="1",
                state="rolled_back",
                settled_at=_ago(900),
                evicted_at=_ago(900),
                evicted_versions=["1"],
            ),
        ],
    )
    backend = _backend(state=_state(known=("1",), serving=(), default=None, routing=()))

    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert _targets(backend) == [(MODEL, "1")]
    assert result["reclaimed"] == 1
    docs = fake_db["promotions"].docs
    assert {d["promotion_id"] for d in docs if d.get("candidate_reclaimed")} == {"promo-1"}
    assert {d["promotion_id"] for d in docs if d.get("rollback_target_reclaimed")} == {
        "promo-uses-1"
    }


def test_a_200_that_reclaimed_nothing_retires_no_nomination(fake_db):
    """``disk_reclaimed: false`` is overloaded -- a refusal that did not raise, and
    ``dry_run``, which SHIPS True -- and both leave the directory in place.
    Retiring on it would strand real directories uncollected and flag a live
    rollback target as gone, which the precheck turns into a failed promotion.

    Mutation: call ``_mark_version_reclaimed`` from the withheld branch too, so
    every 200 retires -> red on the flags, and the version stops being offered
    while its directory is still on the volume.
    """
    _seed(
        fake_db,
        records=[_record(promotion_id="promo-2", candidate_version="2", settled_at=_ago(900))],
        evicted=("2",),
    )
    backend = _backend()
    # ``kept``: the contract's word for a directory that is still on the volume.
    backend.cleanup.return_value = {"version": "2", "status": "kept", "disk_reclaimed": False}
    cfg = _cfg(keep_recent_versions=0)

    result, _ = _run(fake_db, backend, cfg)
    _run(fake_db, backend, cfg)

    assert result["withheld"] == 1
    assert result["reclaimed"] == 0
    docs = fake_db["promotions"].docs
    assert not [d for d in docs if d.get("candidate_reclaimed")]
    assert not [d for d in docs if d.get("rollback_target_reclaimed")]
    # Still on the volume, so still nominated: tick two offers it again.
    assert _targets(backend) == [(MODEL, "2"), (MODEL, "2")]


# -- it must never fail a promotion, and must stay bounded ------------------- #
def test_a_409_is_counted_as_refused_and_logged_at_info(fake_db, caplog):
    """A 409 recurs every tick for the life of the deployment. Asserted on
    ``levelno``, not on substrings.

    Mutation: ``logger.exception`` on the 409 branch -> red (a record at ERROR),
    and in production a stack trace an hour, forever.
    """
    _seed(fake_db, records=[_record(candidate_version="2", settled_at=_ago(900))], evicted=("2",))
    backend = _backend()
    backend.cleanup.side_effect = BackendError(
        code="model_in_use", message="routed", retryable=False, http_status=409
    )
    with caplog.at_level(logging.DEBUG):
        result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert result["refused"] == 1
    assert result["errors"] == 0
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_a_retryable_error_aborts_the_tick(fake_db):
    """The client's retry sleeps SYNCHRONOUSLY inside this worker.

    Mutation: ``continue`` instead of returning -> red; model B is attempted and
    the tick burns worker time on a control plane that cannot answer.
    """
    for name in ("a-model", "b-model"):
        fake_db[COUNTERS].insert_one({"_id": name, "seq": 9})
        fake_db["promotions"].insert_one(
            _record(
                promotion_id=f"promo-{name}",
                model_name=name,
                candidate_version="2",
                settled_at=_ago(900),
            )
        )
        fake_db["promotions"].insert_one(_evicted("2", model_name=name))
    backend = _backend()
    backend.serving_state.side_effect = lambda name: _state(serving=(), default=None, routing=())
    backend.cleanup.side_effect = BackendError(
        code="internal", message="down", retryable=True, http_status=503
    )
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert result["aborted"] is True
    assert result["attempted"] == 1
    assert [c.args[0] for c in backend.cleanup.call_args_list] == ["a-model"]


def test_one_models_eligibility_failure_does_not_skip_the_rest(fake_db):
    """Mutation: hoist the eligibility ``try`` outside the model loop -> red, and
    one unreadable model silently skips every model behind it."""
    for name in ("a-model", "b-model"):
        fake_db[COUNTERS].insert_one({"_id": name, "seq": 9})
        fake_db["promotions"].insert_one(
            _record(
                promotion_id=f"promo-{name}",
                model_name=name,
                candidate_version="2",
                settled_at=_ago(900),
            )
        )
        fake_db["promotions"].insert_one(_evicted("2", model_name=name))

    def _state_or_boom(name):
        if name == "a-model":
            raise RuntimeError("serving read exploded")
        return _state(serving=(), default=None, routing=())

    backend = _backend()
    backend.serving_state.side_effect = _state_or_boom
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert result["errors"] == 1
    assert _targets(backend) == [("b-model", "2")]


def test_a_cleanup_that_raises_a_non_backend_error_does_not_escape(fake_db):
    """A response missing ``version``/``status`` raises KeyError, not BackendError.

    Mutation: remove the bare ``except Exception`` around the cleanup call -> the
    task raises instead of returning a counter dict.
    """
    _seed(fake_db, records=[_record(candidate_version="2", settled_at=_ago(900))], evicted=("2",))
    backend = _backend()
    backend.cleanup.side_effect = KeyError("version")
    result, _ = _run(fake_db, backend, _cfg(keep_recent_versions=0))

    assert result["errors"] == 1
    assert result["aborted"] is False


def test_respects_the_per_tick_caps(fake_db):
    """Blast radius. Mutation: drop either slice -> red.

    Arguments are asserted, not just the call count: ``cleanup`` on a MagicMock
    accepts anything, so "called N times" is green whether the targets are
    retired versions or the live primary.
    """
    versions = [str(i) for i in range(1, 11)]
    for i in range(4):
        name = f"model-{i}"
        fake_db[COUNTERS].insert_one({"_id": name, "seq": 99})
        for v in versions:
            fake_db["promotions"].insert_one(
                _record(
                    promotion_id=f"promo-{name}-{v}",
                    model_name=name,
                    candidate_version=v,
                    baseline_version=None,
                    settled_at=_ago(900),
                )
            )
            fake_db["promotions"].insert_one(
                _evicted(v, promotion_id=f"promo-evicted-{name}-{v}", model_name=name)
            )
    backend = _backend()
    backend.serving_state.side_effect = lambda name: ServingState(
        model_name=name,
        default_version=None,
        routing=[],
        serving_versions=[],
        known_versions=versions,
        ready=True,
        routing_available=True,
        known_available=True,
    )
    result, _ = _run(
        fake_db,
        backend,
        _cfg(keep_recent_versions=0, max_models_per_tick=2, max_versions_per_model_per_tick=3),
    )

    assert _targets(backend) == [
        ("model-0", "1"),
        ("model-0", "2"),
        ("model-0", "3"),
        ("model-1", "1"),
        ("model-1", "2"),
        ("model-1", "3"),
    ]
    assert result["attempted"] == 6


def test_returns_json_serializable_counters(fake_db):
    """``result_serializer`` is json. Mutation: put a BackendError or an ObjectId
    in the returned dict -> red."""
    _seed(fake_db, records=[_record(candidate_version="2", settled_at=_ago(900))], evicted=("2",))
    result, _ = _run(fake_db, _backend(), _cfg(keep_recent_versions=0))
    assert json.loads(json.dumps(result))["task"] == "disk_reclaim"


def test_the_beat_entry_resolves_to_a_registered_task():
    """Schedule and task name are coupled only by a string.

    Mutation: typo either side -> red. Asserting mere key presence would pass for
    a schedule pointing at a task that does not exist.
    """
    from src.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["promotion-disk-reclaim"]
    assert entry["task"] in celery_app.tasks
    assert entry["schedule"].run_every.total_seconds() == 3600.0


def test_the_config_ships_inert():
    """Upgrading DFW must not silently start a deleter, and enabling it must not
    immediately delete. Mutation: flip either default -> red."""
    cfg = DiskReclaimConfig()
    assert cfg.enabled is False
    assert cfg.dry_run is True
    assert cfg.min_age_hours == 24.0
    assert cfg.keep_recent_versions == 2
    assert cfg.rollback_retention_hours == 168.0
    assert cfg.max_models_per_tick == 25
    assert cfg.max_versions_per_model_per_tick == 5
