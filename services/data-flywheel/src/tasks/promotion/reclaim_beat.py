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

"""Periodic disk-reclaim sweep (Celery beat task, design §6).

Promotion evicts a retired baseline with ``unload``, not ``cleanup``, so its
weights stay on the volume and no rollback ever re-downloads. This beat drains
the disk backlog the unload leaves behind, a bounded bite per tick.

Enumeration is positive-only: nominated only when a terminal, settled record
names the version as its candidate AND some record stamped its eviction. Absence
means UNKNOWN, never permission -- a Mongo restore leaves counters, no promotion
records and a full repository. For a full switch those are two DIFFERENT
records: the newer promotion STAMPS the baseline's eviction, while the record
that NOMINATES it is the older promotion that first put that version in front of
traffic. Both must exist. The incumbent DFW inherited rather than promoted has
no such record, so draining it stays an operator verb.

The sweep never writes ``serving_version_counters``. Reclaiming a directory
lowers the floor the serving stack observes; only the monotonic Mongo counter
stops DFW re-minting a version onto an occupied directory.

Housekeeping: never changes routing, never advances a gate, never raises.
"""

# Everything expensive -- config, the backend, the Mongo handle -- is imported
# inside the task body so a disabled deployment pays nothing per tick.
from datetime import UTC

from src.celery_app import celery_app
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.reclaim_beat")

#: Still routable at any moment. The Model Manager cannot see this: after the
#: pull gate a candidate is loaded, READY and routed nowhere, so its guards pass.
_NON_TERMINAL_STATES: frozenset[str] = frozenset({"pending_approval", "approved", "promoting"})

#: Finished. Its candidate becomes a nominee once the retention window elapses.
_TERMINAL_STATES: frozenset[str] = frozenset({"promoted", "rejected", "rolled_back", "failed"})

#: :func:`_eligible_versions` returns this for "skip this model". Not an empty
#: list: "I refuse to answer" and "nothing is eligible" count differently.
_SKIP_MODEL = None


@celery_app.task(name="tasks.promotion.disk_reclaim")
def disk_reclaim() -> dict:
    """Drain the backlog of retired version directories, one bounded bite per tick.

    Returns a counter dict of JSON-serializable primitives. Never raises.

    ``reclaimed`` is not a count of disk freed: the Model Manager reports a
    directory that was already gone exactly like one this call deleted. It is
    honest only because recording the reclaim retires the nomination, so a
    version can be counted once.
    """
    from src.config import settings

    # ``disk_reclaim`` exists only on DeftSystemConfig; the shipped default is
    # NmpSystemConfig, which lacks the field and is extra="forbid", so a plain
    # attribute read would raise every tick. Must stay above
    # build_deployment_backend(), which also reads deft-only config.
    cfg = getattr(settings.system_config, "disk_reclaim", None)
    if cfg is None or not cfg.enabled:
        return {"task": "disk_reclaim", "enabled": False}

    from src.lib.promotion.deployment_backend import BackendError, build_deployment_backend
    from src.tasks.promotion.controller import _get_db

    tally = {
        "reclaimed": 0,
        "withheld": 0,
        "refused": 0,
        "errors": 0,
        "skipped_models": 0,
        "attempted": 0,
    }

    db = _get_db()
    # Read-only, and the ONLY thing this beat ever does to this collection.
    models = [doc["_id"] for doc in db.serving_version_counters.find({}, {"_id": 1})]
    if not models:
        # Before build_deployment_backend(): an idle deployment opens no client.
        return {"task": "disk_reclaim", "enabled": True, "models": 0, "aborted": False, **tally}

    backend = build_deployment_backend()

    for model_name in sorted(models)[: cfg.max_models_per_tick]:
        try:
            eligible = _eligible_versions(db, backend, model_name, cfg)
        except Exception:
            # Per model: one unreadable record must not skip every model behind it.
            logger.exception("disk_reclaim: eligibility failed for %s", model_name)
            tally["errors"] += 1
            continue
        if eligible is _SKIP_MODEL:
            tally["skipped_models"] += 1
            continue

        for version in eligible[: cfg.max_versions_per_model_per_tick]:
            tally["attempted"] += 1
            try:
                outcome = backend.cleanup(
                    model_name,
                    version,
                    min_age_s=cfg.min_age_hours * 3600.0,
                    dry_run=cfg.dry_run,
                    # Safe: step 4b already required an eviction stamp. Satisfies
                    # only the absent-row clause -- routing, residency, streams
                    # and staging still refuse on the Model Manager's own reads.
                    # A restored Mongo defeats it: the record both nominates
                    # and affirms, and every Model Manager read is about NOW.
                    evicted_by_caller=True,
                )
            except BackendError as exc:
                if exc.http_status == 409:
                    # Expected, not exceptional: routed, resident, streamed,
                    # staging, building or too young. Recurs every tick, so INFO.
                    logger.info(
                        "disk_reclaim: %s@%s refused (%s): %s",
                        model_name,
                        version,
                        exc.code,
                        exc.message,
                    )
                    tally["refused"] += 1
                    continue
                logger.warning(
                    "disk_reclaim: %s@%s failed (%s http=%s): %s",
                    model_name,
                    version,
                    exc.code,
                    exc.http_status,
                    exc.message,
                )
                tally["errors"] += 1
                if exc.retryable:
                    # Every remaining target will answer the same, and the retry
                    # sleeps synchronously in this worker on each one.
                    logger.warning("disk_reclaim: aborting tick after a retryable error")
                    return {
                        "task": "disk_reclaim",
                        "enabled": True,
                        "models": len(models),
                        "aborted": True,
                        **tally,
                    }
                continue
            except Exception:
                # ``cleanup`` raises a bare KeyError on a body missing
                # ``version``/``status``, which the clause above would let escape.
                logger.exception("disk_reclaim: %s@%s raised", model_name, version)
                tally["errors"] += 1
                continue

            # Not gated on ``status``: a dry run over an already-gone directory
            # reports "removed" with disk_reclaimed False, and dry_run ships True.
            if outcome.get("disk_reclaimed"):
                logger.info("disk_reclaim: reclaimed %s@%s", model_name, version)
                tally["reclaimed"] += 1
                if not _mark_version_reclaimed(db, model_name, version):
                    # The delete cannot be undone, but a record still nominating
                    # this version -- or advertising it as a rollback target --
                    # is a lie, and the next target shares this Mongo handle.
                    tally["errors"] += 1
                    logger.warning("disk_reclaim: aborting tick after a failed reclaim mark")
                    return {
                        "task": "disk_reclaim",
                        "enabled": True,
                        "models": len(models),
                        "aborted": True,
                        **tally,
                    }
            else:
                logger.info(
                    "disk_reclaim: %s@%s NOT reclaimed (dry_run=%s)",
                    model_name,
                    version,
                    cfg.dry_run,
                )
                tally["withheld"] += 1

    logger.info("disk_reclaim: %s", tally)
    return {
        "task": "disk_reclaim",
        "enabled": True,
        "models": len(models),
        "aborted": False,
        **tally,
    }


def _eligible_versions(db, backend, model_name: str, cfg) -> list[str] | None:
    """Versions of ``model_name`` DFW positively knows are retired (design §6.2).

    Returns ``None`` (``_SKIP_MODEL``) to skip the model. Not an empty list, and
    must not be ``in``-tested.
    """
    from datetime import datetime, timedelta

    from src.api.promotion.service import PROMOTIONS_COLLECTION

    # 1. ``None`` or a False availability flag means "could not find out", never
    #    "nothing": without a truthful read we cannot subtract what is live.
    state = backend.serving_state(model_name)
    if state is None or not state.routing_available or not state.known_available:
        logger.info("disk_reclaim: %s skipped, serving state unknown", model_name)
        return _SKIP_MODEL

    records = list(db[PROMOTIONS_COLLECTION].find({"model_name": model_name}))

    # 2. A promotion in flight takes the whole model off limits: its candidate
    #    may be loaded but unrouted, and the shadow deploy that follows routes
    #    without reloading, so evicting it fails the promotion.
    if any(rec.get("state") in _NON_TERMINAL_STATES for rec in records):
        logger.info("disk_reclaim: %s skipped, a promotion is in flight", model_name)
        return _SKIP_MODEL

    now = datetime.now(UTC)
    retention_cutoff = now - timedelta(hours=cfg.rollback_retention_hours)

    # 3. The latest promoted record permanently pins its candidate (live primary)
    #    and its baseline (rollback target). Only a later promoted record
    #    releases them -- never the retention clock.
    pinned: set[str] = set()
    latest_promoted = _latest_promoted(records)
    if latest_promoted is not None:
        for key in ("candidate_version", "baseline_version"):
            value = latest_promoted.get(key)
            if value:
                pinned.add(str(value))

    # 4. Nominate only what a TERMINAL, SETTLED record names as its candidate.
    nominated: set[str] = set()
    for rec in records:
        if rec.get("state") not in _TERMINAL_STATES:
            continue
        if rec.get("candidate_reclaimed"):
            # Retires the nomination; ``known_versions`` is the stack's
            # loaded-model list, not a directory listing, so without this a
            # reclaimed version is re-offered every tick forever.
            continue
        settled_at = _settled_at(rec)
        if settled_at is None or settled_at > retention_cutoff:
            # Unknown settle time reads as "not settled yet", never as a delete.
            continue
        candidate = rec.get("candidate_version")
        if candidate:
            nominated.add(str(candidate))

    # 4b. ...and only versions a terminal record recorded EVICTING -- the only
    #     thing that says a version left serving memory. Intersect, not "any
    #     stamp is a licence": a stamp for v3 must not collect v4. This also
    #     closes the re-pull case: a DFW-driven re-pull opens a NEW non-terminal
    #     record, and the whole-model skip at step 2 then makes a stamp unable to
    #     authorise deleting the directory that re-pull is writing into.
    nominated &= _evicted_versions(records)

    excluded = set(pinned)
    if state.default_version:
        excluded.add(str(state.default_version))
    excluded.update(str(v) for v in state.serving_versions)
    excluded.update(str(v) for v in _routed_versions(state))
    excluded.update(_newest_known(state.known_versions, cfg.keep_recent_versions))

    # 5. NEVER probe 1..seq: the counter is a high-water mark with permitted
    #    gaps, and each probe is an HTTP round trip with synchronous retry sleeps.
    known = {str(v) for v in state.known_versions}
    eligible = sorted(nominated & known - excluded, key=_version_sort_key)
    return eligible


def _evicted_versions(records: list[dict]) -> set[str]:
    """Versions a terminal record positively says were unloaded (design §4.1).

    A promotion evicts either its baseline (full switch) or its own candidate
    (converged rollback, retired never-routed candidate) or both, so a stamp
    counts only when it names one of the two versions its record is about -- else
    a record migrated into the wrong shape could delete v4 on a stamp for v3.
    """
    evicted: set[str] = set()
    for rec in records:
        if rec.get("state") not in _TERMINAL_STATES:
            continue
        if not rec.get("evicted_at"):
            continue
        own = {str(rec[key]) for key in ("baseline_version", "candidate_version") if rec.get(key)}
        # Singular ``evicted_version`` is the pre-list shape. Read, never
        # written: treating those records as unstamped strands their directories.
        stamps = list(rec.get("evicted_versions") or [])
        if rec.get("evicted_version"):
            stamps.append(rec["evicted_version"])
        for echo in stamps:
            if str(echo) in own:
                evicted.add(str(echo))
                continue
            logger.warning(
                "disk_reclaim: promotion %s carries an eviction stamp for %r, which is neither "
                "version it names (%s); ignoring the stamp",
                rec.get("promotion_id"),
                echo,
                sorted(own) or "none",
            )
    return evicted


def _routed_versions(state) -> list[str]:
    """Versions the stack currently routes any traffic to, at any weight."""
    return [str(entry.version) for entry in state.routing]


def _latest_promoted(records: list[dict]) -> dict | None:
    """The newest ``promoted`` record, by ``_ranked_at`` then insertion order."""
    promoted = [rec for rec in records if rec.get("state") == "promoted"]
    if not promoted:
        return None
    # Undated records sort oldest, so a dated record always wins the pin.
    return max(
        enumerate(promoted),
        key=lambda pair: (_ranked_at(pair[1]) is not None, _ranked_at(pair[1]), pair[0]),
        default=(0, None),
    )[1]


def _settled_at(record: dict):
    """When this promotion last became terminal, as an aware datetime, or ``None``.

    A record that is promoted and later rolled back settles at the ROLLBACK: the
    window this measures is what keeps that rollback's own target on disk.

    No fallback on purpose: ``decided_at`` is the approver's decision time,
    stamped hours or days before the rollout ends, and ``created_at`` is older
    still -- either would start the retention clock early and retire a version
    still live. Records predating the field carry no ``evicted_at`` either, and
    step 4b already refused those.
    """
    return _as_aware(record.get("settled_at"))


def _ranked_at(record: dict):
    """Best-effort ordering stamp for :func:`_latest_promoted`. NOT a settle time.

    Ranking fails the other way round from eligibility: too OLD a record leaves
    the true latest one's baseline -- the live rollback target -- unpinned, so a
    rough answer beats none, hence the ``created_at`` fallback. It can only
    choose what is WITHHELD.
    """
    return _settled_at(record) or _as_aware(record.get("created_at"))


def _as_aware(raw):
    """A Mongo or ISO-8601 timestamp as an aware UTC datetime, or ``None``."""
    from datetime import datetime

    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if not isinstance(raw, datetime):
        return None
    # Mongo hands back naive UTC; comparing one to an aware ``now`` raises
    # TypeError inside the eligibility try, which would skip the model.
    return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw


def _version_sort_key(version: str) -> tuple[int, float, str]:
    """Numeric order for digit versions, lexical for the rest, digits first."""
    return (0, float(version), "") if version.isdigit() else (1, 0.0, version)


def _newest_known(known_versions, keep: int) -> set[str]:
    """The ``keep`` numerically-highest known versions.

    Defence in depth only, not the live-primary guard. ``isdigit``-guarded: a
    version is constrained to be path-safe, not to be a number.
    """
    if keep <= 0:
        return set()
    numeric = sorted((str(v) for v in known_versions if str(v).isdigit()), key=lambda v: int(v))
    return set(numeric[-keep:])


def _mark_version_reclaimed(db, model_name: str, version: str) -> bool:
    """Record, on both sides, that this version's directory is gone.

    The version needs it in both roles: as a ``baseline_version`` it is a
    rollback target that must stop advertising weights that are gone; as a
    ``candidate_version`` it is a nomination that must retire.

    Two writes, not one ``$or``: the rollback precheck turns
    ``rollback_target_reclaimed`` into a failed promotion, so setting it on a
    record that merely nominated this version would fail a rollback that could
    have succeeded.

    Returns ``False`` if either write did not land. Call it only on a positive
    "the directory is gone": a 200 that reclaimed nothing is normal.
    """
    from src.api.promotion.service import PROMOTIONS_COLLECTION

    try:
        db[PROMOTIONS_COLLECTION].update_many(
            {"model_name": model_name, "baseline_version": str(version)},
            {"$set": {"rollback_target_reclaimed": True}},
        )
        db[PROMOTIONS_COLLECTION].update_many(
            {"model_name": model_name, "candidate_version": str(version)},
            {"$set": {"candidate_reclaimed": True}},
        )
    except Exception:
        logger.exception(
            "disk_reclaim: could not record the reclaim of %s@%s; any record naming it as "
            "baseline_version now advertises a rollback target whose weights are gone, and "
            "any record naming it as candidate_version will nominate it again next tick",
            model_name,
            version,
        )
        return False
    return True
