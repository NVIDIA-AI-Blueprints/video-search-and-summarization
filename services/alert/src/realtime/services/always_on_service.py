#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Service layer for the always-on VLM alert rule feature.

Given an incoming camera lifecycle event (``camera_streaming`` /
``camera_remove``), this service fans the event out across the rules
declared in the always-on YAML config — creating / stopping one
:class:`RealtimeAlertService` rule per configured entry.

This module owns the whole orchestration:

* rules YAML loading + validation (with manual caching so tests can
  reset it between runs),
* the feature-flag gate (``alert_agent.always_on`` in ``config.yaml``),
* the per-camera sidecar that tracks which rules are currently active
  for each camera,
* the in-flight reservation + flight-done events that keep concurrent
  ``camera_streaming`` / ``camera_remove`` requests from racing each
  other's writes to the sidecar,
* classifying the fan-out outcome into a single
  :class:`AlwaysOnResult` (status code + reason + optional per-rule
  details) that any front-end (REST route, worker, CLI, agent) can
  translate into its own response format.

There is no FastAPI dependency here — the REST router is a thin
wrapper over this service, and the same service can be invoked from
non-REST contexts (agent flows, replay tools, integration tests)
without going through HTTP.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import yaml
from pydantic import ValidationError

from ..config import load_config
from ..schemas import AlertRuleConfig, AlwaysOnRuleEntry, AlwaysOnRulesFile
from .realtime_service import RealtimeAlertService

logger = logging.getLogger(__name__)


class AlwaysOnRulesConfigError(RuntimeError):
    """Raised when the always-on rules YAML is missing or malformed."""


# Reason strings the service returns in ``AlwaysOnResult.reason``. Kept
# as class constants so callers (REST route, etc.) don't have to
# hard-code the literals.
class AlwaysOnReason:
    DISABLED = "ALWAYS_ON_DISABLED"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    CONFIG_ERROR = "CONFIG_ERROR"
    ADD_SUCCESS = "STREAM_ADD_SUCCESS"
    ADD_PARTIAL_SUCCESS = "STREAM_ADD_PARTIAL_SUCCESS"
    ADD_FAILED = "STREAM_ADD_FAILED"
    ADD_ALREADY_ACTIVE = "STREAM_ADD_ALREADY_ACTIVE"
    REMOVE_SUCCESS = "STREAM_REMOVE_SUCCESS"
    REMOVE_FAILED = "STREAM_REMOVE_FAILED"


@dataclass
class AlwaysOnResult:
    """Outcome of a single always-on request.

    Front-end agnostic:

    * ``status_code`` — HTTP-style numeric code the caller can map into
      its transport (200/422/502/503). Picked so REST callers can
      translate directly; non-REST callers can treat it as a success /
      client-error / upstream-error / config-error tri-state.
    * ``reason`` — short machine-readable string taken from
      :class:`AlwaysOnReason`.
    * ``details`` — optional per-rule array for fan-out responses.
      Omitted (``None``) for short-circuit outcomes so callers can
      distinguish fan-out from pre-fan-out rejections.
    """

    status_code: int
    reason: str
    details: Optional[List[Dict[str, Any]]] = None


class AlwaysOnService:
    """Orchestrates always-on alert rule lifecycle across RealtimeAlertService.

    Thread-safety / concurrency model:

    * A single :class:`asyncio.Lock` (``_lock``) guards every read/write
      of the per-camera sidecar, the in-flight set, and the flight-done
      event map. The lock is held across quick dedupe checks and
      reservation writes, but *not* across the RTVI network calls — so
      concurrent events for different cameras never serialize on each
      other's I/O.
    * For a camera with a ``camera_streaming`` handler in flight:

      - A concurrent ``camera_streaming`` for the same camera either
        waits for the in-flight handler to commit and then re-evaluates
        (filling in any missing rule that a partial failure left
        behind), or short-circuits as ``STREAM_ADD_ALREADY_ACTIVE`` if a
        new concurrent fan-out started during the wait.
      - A concurrent ``camera_remove`` waits on the flight-done event
        until the in-flight add commits to the sidecar, then snapshots
        and tears down exactly the rules that were created. This
        prevents the classic race where remove snapshots an empty rule
        set, returns success, and the add commits *after* we respond —
        leaving RTVI sessions running for a camera that is already
        "removed".

    State is intentionally *not* persisted across process restarts — on
    a pod restart SDR will replay ``camera_streaming`` events and the
    fan-out will converge again.
    """

    def __init__(
        self,
        realtime_service: RealtimeAlertService,
        config_file: str = "config.yaml",
        rules_config_env: str = "ALWAYS_ON_RULES_CONFIG",
    ):
        self._realtime = realtime_service
        realtime_service.register_rule_removed_callback(self._on_realtime_rule_removed)
        self._config_file = config_file
        self._rules_config_env = rules_config_env

        # Per-camera record of every always-on rule currently active for
        # that camera: ``camera_id -> {rule_id: alert_rule_uuid}``. The
        # config-level ``rule_id`` (the YAML label) is the key so dedupe
        # can be done per-rule — a partial start failure leaves the
        # *successful* rules in the map and a subsequent
        # ``camera_streaming`` event refills the missing ones instead of
        # being deduped at the camera level. The inner value is the
        # service-assigned UUID used by ``RealtimeAlertService.stop_alert``.
        self._camera_rules: Dict[str, Dict[str, str]] = {}

        # Camera IDs currently being processed by a ``camera_streaming``
        # handler. Used to (1) short-circuit a concurrent
        # ``camera_streaming`` event for the same camera (would
        # otherwise race on partial dedupe) and (2) let a concurrent
        # ``camera_remove`` wait for the fan-out to commit before
        # snapshotting the sidecar.
        self._in_flight: Set[str] = set()

        # Per-camera completion signal for the add-path's fan-out. When
        # a ``camera_streaming`` handler enters the in-flight phase it
        # creates an Event here under the lock; the ``finally`` block
        # sets the event right after the sidecar commit. A concurrent
        # ``camera_remove`` awaits this event so it can see the
        # post-commit sidecar state and tear down every rule the
        # add-path actually created.
        self._flight_done: Dict[str, asyncio.Event] = {}

        self._lock = asyncio.Lock()

        # Manual caches (cleared by ``reset()``). Using plain attrs
        # rather than ``@lru_cache`` keeps the cache scoped to the
        # service instance so tests can reset it between runs without
        # reaching into module globals.
        self._rules_cache: Optional[List[AlwaysOnRuleEntry]] = None
        self._enabled_cache: Optional[bool] = None

    # ------------------------------------------------------------------
    # Test / lifecycle helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Drop all in-memory state. Intended for tests.

        Clears the sidecar, in-flight reservations, flight-done events,
        and the cached rules / feature-flag lookup so the next call
        starts from scratch.
        """
        self._camera_rules.clear()
        self._in_flight.clear()
        self._flight_done.clear()
        self._rules_cache = None
        self._enabled_cache = None

    async def _on_realtime_rule_removed(self, alert_rule_id: str) -> None:
        """Remove a stale sidecar entry when the realtime service drops a rule.

        Called by :meth:`RealtimeAlertService._cleanup_failed_rule` via the
        registered rule-removed callback.  Scans ``_camera_rules`` for the
        given ``alert_rule_id`` and removes it so that a subsequent
        ``camera_streaming`` event can re-add it rather than being deduped as
        still-active.
        """
        async with self._lock:
            for camera_id, rules in list(self._camera_rules.items()):
                for rule_id, uid in list(rules.items()):
                    if uid == alert_rule_id:
                        rules.pop(rule_id)
                        if not rules:
                            self._camera_rules.pop(camera_id)
                        logger.info(
                            "Removed stale always-on sidecar entry after realtime rule removal",
                            extra={
                                "camera_id": camera_id,
                                "rule_id": rule_id,
                                "alert_rule_id": alert_rule_id,
                            },
                        )
                        return

    # ------------------------------------------------------------------
    # Feature flag and config loading
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Return ``alert_agent.always_on`` from the Alert Bridge config.

        Defaults to ``False`` — the endpoint is opt-in so it can be
        rolled back via a config flip instead of a code change. The
        value is cached on the instance; call :meth:`reset` to force a
        re-read (tests do this when they rewrite ``config.yaml``).
        """
        if self._enabled_cache is None:
            config = load_config(self._config_file)
            self._enabled_cache = bool(
                config.get("alert_agent", {}).get("always_on", False)
            )
        return self._enabled_cache

    def load_rules(self) -> List[AlwaysOnRuleEntry]:
        """Load and validate the always-on alert rules YAML.

        The path must be provided via the env var passed to the
        constructor (``ALWAYS_ON_RULES_CONFIG`` by default). There is no
        implicit fallback: if the variable is unset, the file is
        missing, YAML parsing fails, or the file doesn't conform to
        :class:`AlwaysOnRulesFile`, this raises
        :class:`AlwaysOnRulesConfigError` so the caller can surface a
        clear error.

        Schema lives in :mod:`realtime.schemas.always_on_config` and is
        the single source of truth for legal keys, field types, and
        required vs optional fields. Pydantic's ``extra="forbid"``
        rejects unknown keys at every level, so typos like
        ``modle: foo`` fail at load time instead of silently falling
        back to :class:`AlertRuleConfig` defaults at request time.

        Result is cached on the instance; call :meth:`reset` to force a
        reload (tests do this when they rewrite the rules YAML).
        """
        if self._rules_cache is not None:
            return self._rules_cache

        path = os.getenv(self._rules_config_env)
        if not path:
            raise AlwaysOnRulesConfigError(
                f"{self._rules_config_env} env var is not set — "
                "point it at a YAML file containing `always_on_rules:`"
            )

        try:
            with open(path, "r") as fh:
                data = yaml.safe_load(fh) or {}
        except FileNotFoundError as exc:
            raise AlwaysOnRulesConfigError(
                f"{self._rules_config_env} path not found: {path}"
            ) from exc
        except yaml.YAMLError as exc:
            raise AlwaysOnRulesConfigError(
                f"Failed to parse {path}: {exc}"
            ) from exc

        try:
            parsed = AlwaysOnRulesFile.model_validate(data)
        except ValidationError as exc:
            # Build a human-friendly message that surfaces missing required
            # fields (e.g. system_prompt / model, made required in MR !292)
            # and points operators to the sample config for migration guidance.
            missing_fields = sorted(
                {
                    ".".join(str(loc) for loc in e["loc"])
                    for e in exc.errors()
                    if e["type"] == "missing"
                }
            )
            migration_hint = ""
            if missing_fields:
                migration_hint = (
                    f"\n\nMissing required field(s): {missing_fields}."
                    "\nNote: 'system_prompt' and 'model' are now required under"
                    " always_on_params for each rule (added in MR !292)."
                    "\nSee realtime-config-sample.yaml for a complete example."
                )
            raise AlwaysOnRulesConfigError(
                f"Invalid always-on rules config at {path}:\n{exc}"
                f"{migration_hint}"
            ) from exc

        logger.info(
            "Loaded %d always-on alert rule(s) from %s",
            len(parsed.always_on_rules),
            path,
        )
        self._rules_cache = parsed.always_on_rules
        return self._rules_cache

    def validate_config_at_startup(self) -> None:
        """Fail-fast validation for app startup hooks.

        If the ``alert_agent.always_on`` flag is enabled, load and
        validate the always-on rules YAML *now* so any problem (missing
        env var, missing/unreadable/malformed file, unknown/misspelled
        keys, missing required fields) kills app boot with a clear
        error rather than silently waiting for the first camera event
        at runtime to surface it. When the flag is disabled this is a
        no-op.
        """
        if not self.enabled:
            logger.info(
                "alert_agent.always_on is disabled — skipping always-on "
                "rules validation. The always-on entry point will return "
                "%s to callers.",
                AlwaysOnReason.DISABLED,
            )
            return

        self._rules_cache = None
        self.load_rules()
        logger.info(
            "alert_agent.always_on is enabled and rules config validated"
        )

    # ------------------------------------------------------------------
    # Public API: start / stop a camera's always-on rules
    # ------------------------------------------------------------------

    async def start_camera(
        self,
        camera_id: str,
        camera_url: str,
        camera_name: str,
    ) -> AlwaysOnResult:
        """Fan out ``camera_streaming`` across every configured rule.

        Fires one :meth:`RealtimeAlertService.start_alert` per
        configured always-on rule that isn't already tracked for this
        camera, records the resulting UUIDs in the sidecar, and
        classifies the overall outcome.

        Concurrency contract:

        * If a concurrent fan-out for the same camera is still running,
          this coroutine waits for it to commit and then re-evaluates.
          After waiting it either finds everything already active
          (short-circuits as ``STREAM_ADD_ALREADY_ACTIVE``) or starts
          just the rules the previous attempt couldn't — so a replay
          after a partial failure fills in the gaps without
          double-starting the rules that succeeded.
        * Dedupe is per-``rule_id``, not per-``camera_id``: see the
          sidecar docstring for the reasoning.

        Args:
            camera_id: Unique camera identifier from the lifecycle event.
            camera_url: RTSP URL of the live stream.
            camera_name: Human-readable camera label forwarded to RTVI
                as ``sensor_name``.

        Returns:
            An :class:`AlwaysOnResult` carrying a fan-out-style
            response. ``CONFIG_ERROR`` is returned if the rules config
            is unusable; otherwise one of ``STREAM_ADD_SUCCESS``,
            ``STREAM_ADD_PARTIAL_SUCCESS``, ``STREAM_ADD_FAILED``, or
            ``STREAM_ADD_ALREADY_ACTIVE``.
        """
        try:
            rules = self.load_rules()
        except AlwaysOnRulesConfigError as exc:
            logger.error("Always-on rules config error: %s", exc)
            return AlwaysOnResult(
                status_code=503, reason=AlwaysOnReason.CONFIG_ERROR,
            )

        configured_rule_ids = {r.rule_id for r in rules}

        # ── Dedupe under the sidecar lock ──────────────────────────────
        # If a concurrent ``camera_streaming`` fan-out for this camera
        # is already in flight, wait for it to commit before
        # re-evaluating. Returning ALREADY_ACTIVE immediately while the
        # in-flight add is still running is incorrect: if that add
        # later fails every ``start_alert`` call, the caller that saw
        # the 200 stops retrying and the camera is left unmonitored.
        # After waiting we re-check whether every configured rule is
        # now active and fall through to start any that are still
        # missing, so a replay after a partial failure fills in the
        # gaps.
        async with self._lock:
            flight_event = self._flight_done.get(camera_id)

        if flight_event is not None:
            logger.info(
                "camera_streaming for camera_id=%s: concurrent fan-out "
                "in flight — waiting for it to commit before re-evaluating",
                camera_id,
            )
            await flight_event.wait()

        async with self._lock:
            already_active_rule_ids = set(
                self._camera_rules.get(camera_id, {}).keys()
            )
            if configured_rule_ids <= already_active_rule_ids:
                logger.info(
                    "camera_streaming for camera_id=%s: every configured "
                    "rule is already active — skipping duplicate add",
                    camera_id,
                )
                return AlwaysOnResult(
                    status_code=200,
                    reason=AlwaysOnReason.ADD_ALREADY_ACTIVE,
                )
            if camera_id in self._in_flight:
                # A new concurrent add started between our wait and
                # this lock acquisition. Defer to it rather than racing
                # a second fan-out.
                logger.info(
                    "camera_streaming for camera_id=%s: new concurrent "
                    "fan-out started after wait — deferring to it",
                    camera_id,
                )
                return AlwaysOnResult(
                    status_code=200,
                    reason=AlwaysOnReason.ADD_ALREADY_ACTIVE,
                )
            self._in_flight.add(camera_id)
            self._flight_done[camera_id] = asyncio.Event()

        try:
            return await self._fan_out_add(
                camera_id=camera_id,
                camera_url=camera_url,
                camera_name=camera_name,
                rules=rules,
                already_active_rule_ids=already_active_rule_ids,
            )
        finally:
            # Release the in-flight slot and wake any ``camera_remove``
            # that is waiting on our commit. The event-set happens
            # outside the lock so waiters don't try to re-enter it.
            async with self._lock:
                event = self._flight_done.pop(camera_id, None)
                self._in_flight.discard(camera_id)
            if event is not None:
                event.set()

    async def stop_camera(self, camera_id: str) -> AlwaysOnResult:
        """Fan out ``camera_remove`` across every tracked rule for the camera.

        Stops every alert rule previously created for ``camera_id`` by
        delegating to :meth:`RealtimeAlertService.stop_alert`. Treats
        ``stop_alert`` returning 404 as an idempotent success (the rule
        is already gone — which is exactly the end-state we want).

        Concurrency contract: if a ``camera_streaming`` fan-out for
        this camera is still in flight, waits for it to commit before
        snapshotting the sidecar. Otherwise we'd snapshot an empty rule
        set, return ``STREAM_REMOVE_SUCCESS``, and then the add handler
        would commit *after* we returned — leaving RTVI sessions
        running for a camera that has already been removed.

        The sidecar is updated atomically at the end: rule ids whose
        ``stop_alert`` call succeeded are dropped; rule ids whose stop
        failed are kept so SDR's retry can re-drive just those.
        """
        # Wait for any in-flight add-path to commit so we see the rules
        # it actually created.
        async with self._lock:
            flight_event = self._flight_done.get(camera_id)
        if flight_event is not None:
            logger.info(
                "camera_remove for camera_id=%s: waiting for concurrent "
                "camera_streaming fan-out to commit before teardown",
                camera_id,
            )
            await flight_event.wait()

        # Snapshot under the lock — but keep the sidecar entry in place
        # for now. We only commit the pop/rewrite after the stop loop
        # has run, otherwise a partial failure loses the failing rule
        # ids and SDR's retry finds nothing to re-drive.
        async with self._lock:
            tracked = dict(self._camera_rules.get(camera_id, {}))
        logger.info(
            "camera_remove for camera_id=%s — stopping %d rule(s)",
            camera_id,
            len(tracked),
        )

        # ``stop_alert`` returns 200 on clean teardown and 404 when the
        # rule is already gone from the core registry. Both are valid
        # end-states for ``camera_remove``: SDR wants the rule gone, and
        # it is. The core also already tolerates RTVI HTTP failures
        # internally (warns + removes anyway → 200), so any status
        # outside {200, 404} indicates a real core-side issue worth
        # surfacing as 502 so SDR can retry.
        remove_details: List[Dict[str, Any]] = []
        rule_ids_to_drop: List[str] = []
        for rule_id, alert_rule_id in tracked.items():
            logger.info(
                "Stopping rule_id=%s alert_rule_id=%s",
                rule_id, alert_rule_id,
            )
            resp_data, resp_status = await self._realtime.stop_alert(
                alert_rule_id
            )
            entry: Dict[str, Any] = {
                "rule_id": rule_id,
                "alert_rule_id": alert_rule_id,
                "status": resp_status,
            }
            if resp_status == 200:
                entry["result"] = "success"
                rule_ids_to_drop.append(rule_id)
            elif resp_status == 404:
                entry["result"] = "success"
                entry["message"] = "rule already removed (404) — idempotent"
                rule_ids_to_drop.append(rule_id)
                logger.info(
                    "stop_alert: rule %s already removed (404) — "
                    "treating as idempotent success",
                    alert_rule_id,
                )
            else:
                entry["result"] = "error"
                entry["error"] = resp_data
                logger.warning(
                    "stop_alert failed for %s (status=%s): %s",
                    alert_rule_id, resp_status, resp_data,
                )
            remove_details.append(entry)

        # Commit the sidecar update atomically: drop rule ids that are
        # confirmed gone, keep the ones whose stop failed so SDR's
        # retry can re-drive just those.
        async with self._lock:
            inner = self._camera_rules.get(camera_id)
            if inner is not None:
                for rule_id in rule_ids_to_drop:
                    inner.pop(rule_id, None)
                if not inner:
                    self._camera_rules.pop(camera_id, None)

        failed = [e for e in remove_details if e["result"] == "error"]
        if failed:
            return AlwaysOnResult(
                status_code=502,
                reason=AlwaysOnReason.REMOVE_FAILED,
                details=remove_details,
            )
        return AlwaysOnResult(
            status_code=200,
            reason=AlwaysOnReason.REMOVE_SUCCESS,
            details=remove_details,
        )

    # ------------------------------------------------------------------
    # Internal fan-out helper
    # ------------------------------------------------------------------

    async def _fan_out_add(
        self,
        camera_id: str,
        camera_url: str,
        camera_name: str,
        rules: List[AlwaysOnRuleEntry],
        already_active_rule_ids: Set[str],
    ) -> AlwaysOnResult:
        """Start every not-yet-tracked rule and classify the outcome.

        Split from :meth:`start_camera` so the in-flight/flight-done
        bookkeeping has one owner (``start_camera``) and this helper
        stays focused on the per-rule loop + classification.
        """
        results: List[Dict[str, Any]] = []
        created_rules: Dict[str, str] = {}

        # Phase 1: sync pre-flight — dedupe and validate without RTVI calls.
        # Collect rules that need a live start_alert call in to_start.
        to_start: List[tuple] = []  # (rule_id, alert_type, config)
        for rule in rules:
            rule_id = rule.rule_id
            alert_type = rule.alert_type

            # Per-rule dedupe: if the sidecar already has a UUID for
            # this (camera_id, rule_id), skip — don't double-start.
            # This is what lets a replay after a partial failure fill
            # in the missing rules without duplicating the
            # already-running ones.
            if rule_id in already_active_rule_ids:
                existing_uuid = self._camera_rules.get(
                    camera_id, {}
                ).get(rule_id)
                results.append({
                    "rule_id": rule_id,
                    "alert_type": alert_type,
                    "status": 200,
                    "result": "already_active",
                    "alert_rule_id": existing_uuid,
                })
                continue

            # Load-time Pydantic validation has already guaranteed
            # rule_id / alert_type are non-empty and params carries
            # only known keys, so we can read typed attributes and
            # spread the params straight into AlertRuleConfig.
            params = rule.always_on_params.model_dump()

            if not params.get("prompt"):
                # Defensive: Pydantic config-load validation requires
                # ``prompt`` as a key, but an explicitly empty-string
                # prompt still slips through. Record the per-rule
                # failure and move on.
                results.append({
                    "rule_id": rule_id,
                    "alert_type": alert_type,
                    "status": 422,
                    "result": "error",
                    "message": "always_on_params.prompt is required",
                })
                continue

            config = AlertRuleConfig(
                live_stream_url=camera_url,
                alert_type=alert_type,
                sensor_name=camera_name,
                sensor_id=camera_id,
                **params,
            )

            logger.info(
                "Starting always-on rule %s (alert_type=%s model=%s camera_id=%s)",
                rule_id,
                alert_type,
                params.get("model", "<default>"),
                camera_id,
            )
            to_start.append((rule_id, alert_type, config))

        # Phase 2: concurrent RTVI fan-out.
        # start_alert always returns (dict, status_code) and never raises,
        # so gather with default return_exceptions=False is safe.
        if to_start:
            start_results = await asyncio.gather(
                *[self._realtime.start_alert(cfg) for _, _, cfg in to_start]
            )
            for (rule_id, alert_type, _), (response_data, status_code) in zip(
                to_start, start_results
            ):
                entry: Dict[str, Any] = {
                    "rule_id": rule_id,
                    "alert_type": alert_type,
                    "status": status_code,
                }
                if status_code == 201:
                    alert_rule_id = response_data.get("id")
                    entry["result"] = "success"
                    entry["alert_rule_id"] = alert_rule_id
                    if alert_rule_id:
                        created_rules[rule_id] = alert_rule_id
                        logger.info(
                            "always-on rule %r started: alert_rule_id=%s",
                            rule_id, alert_rule_id,
                        )
                else:
                    entry["result"] = "error"
                    # Keep the raw upstream error dict under ``error``
                    # rather than ``details`` to avoid collision with the
                    # top-level ``details`` array the envelope carries.
                    entry["error"] = response_data
                results.append(entry)

        # Merge (not overwrite) newly-created rule ids into the
        # sidecar. Overwriting would lose ``already_active`` rules that
        # the replay legitimately skipped but are still running.
        if created_rules:
            async with self._lock:
                inner = self._camera_rules.setdefault(camera_id, {})
                inner.update(created_rules)

        # Classify the outcome. ``already_active`` rules count toward
        # the camera having rules running but don't count as a fresh
        # start — if every configured rule turned out to be
        # already_active we return ALREADY_ACTIVE (not SUCCESS).
        errors = sum(1 for r in results if r["result"] == "error")
        new_successes = sum(1 for r in results if r["result"] == "success")
        already = sum(1 for r in results if r["result"] == "already_active")
        active_count = new_successes + already

        if errors == 0:
            reason = (
                AlwaysOnReason.ADD_SUCCESS
                if new_successes > 0
                else AlwaysOnReason.ADD_ALREADY_ACTIVE
            )
            return AlwaysOnResult(
                status_code=200, reason=reason, details=results,
            )
        if active_count > 0:
            # 1+ rule failed but 1+ is now (or was already) active.
            # Status stays 200 so SDR doesn't retry the whole camera —
            # per-rule dedupe will pick up the failed rule ids on the
            # next ``camera_streaming`` replay.
            return AlwaysOnResult(
                status_code=200,
                reason=AlwaysOnReason.ADD_PARTIAL_SUCCESS,
                details=results,
            )
        # Every rule failed AND nothing was already active — surface
        # upstream failure so SDR retries.
        return AlwaysOnResult(
            status_code=502,
            reason=AlwaysOnReason.ADD_FAILED,
            details=results,
        )


__all__ = [
    "AlwaysOnReason",
    "AlwaysOnResult",
    "AlwaysOnRulesConfigError",
    "AlwaysOnService",
]
