# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Stateless validation for dynamic-config payloads.

Splitting validation away from :class:`ConfigApplier` lets both the main-process
listener and the per-worker file monitor run identical validation rules without
either side carrying mutation logic for a contract it does not own (workers do
not ack; the listener does not need to mutate without writing first). Both
sides call :func:`validate` on the raw payload, then hand the filtered
:class:`ValidationResult` to :class:`ConfigApplier`.

Validation ladder
-----------------
1. **Shape** -- payload must be a JSON object (``dict``). Anything else
   (``None``, ``list``, ``str``, ``int``, ...) is a wholesale ``failure``.
2. **Scope** -- only ``app`` and ``sensors`` are mutable; any other top-level
   key (``kafka``, ``redisStream``, ``mqtt``, ``coordinateReferenceSystem``,
   ``inference``) is a read-only section. Each forbidden section is recorded
   as a per-section rejection but does **not** short-circuit -- valid items
   under ``app`` / ``sensors`` are still applied, yielding a
   ``partial-success`` ack so the operator can correct the read-only payload
   without losing the salvageable bits.
3. **Per-item** -- each ``app[*]`` and ``sensors[*].configs[*]`` entry must
   pass three sub-stages, in order:

   a. **Shape** -- ``name`` is a non-empty string, ``value`` is a string.
   b. **Allowlist** -- ``name`` is in :data:`ALLOWED_APP_KEYS` (for ``app[*]``)
      or :data:`ALLOWED_SENSOR_KEYS` (for ``sensors[*].configs[*]``).
   c. **Value** -- the string ``value`` parses and falls within the per-key
      rule registered in
      :mod:`mdx.analytics.core.transform.config.config_value_validators`.

   Sensor entries must additionally carry a non-empty string ``id``. Bad,
   not-allowlisted, and value-invalid items are collected; only items that
   pass all three sub-stages survive.

Allowlist rationale
-------------------
The allowlists capture the keys whose consumers read ``self.config.X`` at
use-time (the change applies on the next access) or recover within one batch
or object rotation (in-flight objects keep their captured values briefly).
Keys outside the allowlist either belong to read-only sections, are captured
at process ``__init__`` (a runtime upsert wouldn't take effect without a
restart), or are consumed only by dev-only ``tools/`` modules. Rejecting
them at validation time keeps operators from silently believing a config
landed when it actually requires a restart.

See ``docs/dynamic-config.md`` for the full consumer classification.

Result semantics
----------------
* All items good and no rejections -> ``success`` (error ``null``).
* Zero items in the input (``{}``, ``{"app": [], "sensors": []}``) ->
  ``success`` (error ``null``). The operator explicitly said
  "no changes" and that's exactly what happened -- a legitimate no-op.
* Some good items + some rejections -> ``partial-success`` (good items
  applied, error enumerates the rejections).
* Items present in the input but every one of them got rejected
  (forbidden sections, bad shape, not-allowlisted names, or ambiguous
  shapes like a sensor entry with empty ``configs``) -> ``failure``
  (error lists the rejections).
* Payload not a dict / malformed shape -> ``failure`` (error
  describes the shape problem).

Note the split between "zero input items" (success no-op) and "input had
items, all rejected" (failure). Conflating the two would mean a heartbeat-
style empty patch looks identical on the ack stream to an operator's
attempt to mutate a restart-required key -- two semantically different
situations that callers may want to monitor separately.

Ambiguous shapes are deliberately rejected: ``{"sensors":[{"id":"x",
"configs":[]}]}`` could mean either "no change for x" (operator should
just omit the entry) or "wipe x's configs" (would need a separate
``delete`` event). Rather than pick one silently, the validator returns
a per-item rejection so the operator disambiguates explicitly.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from mdx.analytics.core.schema.models import ConfigMessage
from mdx.analytics.core.transform.config.config_value_validators import (
    APP_VALUE_VALIDATORS,
    SENSOR_VALUE_VALIDATORS,
    ValueValidator,
    validate_value,
)


# Top-level payload keys that are eligible for dynamic update. Anything outside
# this set is a read-only section and triggers per-section rejection.
ALLOWED_SECTIONS: frozenset[str] = frozenset({"app", "sensors"})


# ---------------------------------------------------------------------------
# Allowlists -- the canonical "what's safe to update at runtime" set.
#
# Add a key here only when its consumer reads ``self.config.X`` at use-time
# or rotates per object/batch within seconds. DO NOT add keys that are
# consumed only at ``__init__`` -- a runtime upsert would silently not take
# effect, leading operators to believe a value landed when it actually
# needs a restart.
# ---------------------------------------------------------------------------

ALLOWED_APP_KEYS: frozenset[str] = frozenset({
    # Behavior / object processing
    "behaviorMaxPoints",
    "behaviorStateEndToleranceSec",
    "behaviorStateTimeout",
    "behaviorStateValidInterval",
    "behaviorTimeThreshold",
    "behaviorWatermarkSec",
    "clusterThreshold",
    "objectConfidenceThreshold",
    "compactFrame",
    "useObjectLocation",
    "incidentObjectTtl",
    "stateManagementFilter",
    "imageLocationMode",
    # ``coordinateSystem`` deliberately excluded -- only referenced by tests
    # and obsolete batch scripts in production code paths.
    "in3dMode",
    "advancedOverlay",
    "inSimulationMode",

    # Trajectory / map matching
    "trajGeoCoordEnable",
    "trajDirectionMode",
    "trajDirectionClusterMode",
    "trajDistanceStride",
    "trajSmoothMinPoints",
    "trajSmoothWindowSize",
    "trajSpeedSegmentSize",
    "mapMatchingClasses",
    "mapMatchingMaxPoints",

    # Incidents
    "proximityViolationIncidentEnable",
    "proximityViolationIncidentThreshold",
    "proximityViolationIncidentExpirationWindow",
    "restrictedAreaViolationIncidentEnable",
    "restrictedAreaViolationIncidentThreshold",
    "restrictedAreaViolationIncidentExpirationWindow",
    "confinedAreaViolationIncidentEnable",
    "confinedAreaViolationIncidentThreshold",
    "confinedAreaViolationIncidentExpirationWindow",
    "fovCountViolationIncidentEnable",
    "fovCountViolationIncidentThreshold",
    "fovCountViolationIncidentObjectThreshold",
    "fovCountViolationIncidentObjectType",
    "fovCountViolationIncidentExpirationWindow",
})


ALLOWED_SENSOR_KEYS: frozenset[str] = frozenset({
    # Trajectory / behavior (per-sensor)
    "tripwireMinPoints",
    "sensorMinFrames",

    # Proximity detection (per-sensor)
    "proximityDetectionEnable",
    "proximityDetectionThreshold",
    "proximityDetectionCenterClasses",
    "proximityDetectionSurroundingClasses",

    # Anomaly detection (per-sensor)
    # Note: ``anomalyCollisionDetection`` is intentionally excluded -- the Smart City
    # app captures it at BaseApp.__init__, so a runtime upsert would not take
    # effect without a process restart.
    "anomalyActionThreshold",
    "anomalyClasses",
    "anomalyIgnoreSensors",
    "anomalySpeedViolation",
    "anomalyUnexpectedStop",
    "anomalyAbnormalMovement",
    "anomalyFallRisk",
    "anomalyLackMovement",
})


ValidationStatus = Literal["success", "partial-success", "failure"]


@dataclass
class ValidationResult:
    """
    Outcome of validating a dynamic-config payload.

    :ivar ValidationStatus status: ``"success"``, ``"partial-success"``, or
        ``"failure"``. See module-level "Result semantics" for the rules.
    :ivar list[dict[str, str]] applied_app: ``app`` items that passed
        validation, ready for :meth:`ConfigApplier.apply` to merge.
    :ivar list[dict[str, Any]] applied_sensors: Sensor entries whose every
        ``configs`` item passed validation. A sensor with any bad item is
        dropped wholesale (its sub-rejections are still listed in
        ``error``).
    :ivar str | None error: ``None`` on ``success``; otherwise a
        human-readable summary of rejections suitable for the ack payload.
    """

    status: ValidationStatus
    applied_app: list[dict[str, str]] = field(default_factory=list)
    applied_sensors: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def validate(payload: Any) -> ValidationResult:
    """
    Run the validation ladder on a raw payload.

    Pure function; no AppConfig dependency, no mutation. Used identically
    by :class:`ConfigListener` (main process) and :class:`ConfigFileMonitor`
    (per-worker). The listener uses the result to drive the ack and the
    file write; the worker uses it to filter the already-vetted-by-main
    payload before applying.

    :param Any payload: Whatever ``value.config`` decoded into on the wire,
        or whatever ``json.load`` returned from a file. Often a dict but
        may be ``None`` / ``list`` / etc. on a malformed input -- caught
        here.
    :return ValidationResult: Status + filtered applied items + error message.
    """
    if not isinstance(payload, dict):
        return ValidationResult(status="failure", error="payload is not a JSON object")

    # Forbidden sections become per-section rejections rather than a
    # wholesale failure -- valid items under app/sensors still apply.
    forbidden_rejections: list[tuple[str, str]] = [
        (section, "read-only section, ignored")
        for section in sorted(set(payload.keys()) - ALLOWED_SECTIONS)
    ]

    applied_app, rejected_app = _verify_app_items(payload.get("app", []))
    applied_sensors, rejected_sensors = _verify_sensor_items(payload.get("sensors", []))
    rejections = forbidden_rejections + rejected_app + rejected_sensors
    applied_count = len(applied_app) + sum(len(s["configs"]) for s in applied_sensors)

    if applied_count == 0 and rejections:
        # Operator tried to change something, every item was refused.
        # Genuine failure -- error enumerates the per-item rejections.
        return ValidationResult(
            status="failure",
            error=_format_rejections(rejections),
        )
    if rejections:
        # Mix of applied + rejected items.
        return ValidationResult(
            status="partial-success",
            applied_app=applied_app,
            applied_sensors=applied_sensors,
            error=_format_rejections(rejections, applied_count=applied_count),
        )
    # Either all items applied cleanly, OR the payload had zero items to
    # apply in the first place (e.g. ``{}``, ``{"app": [], "sensors": []}``,
    # or a sensor entry with an empty ``configs`` list). The latter is a
    # legitimate no-op success -- the operator explicitly said "no changes"
    # and we did exactly that. ``error`` stays ``null`` so the ack is
    # not confused with an actionable failure.
    return ValidationResult(
        status="success",
        applied_app=applied_app,
        applied_sensors=applied_sensors,
    )


# Source-of-truth for envelope keys -- must match :class:`ConfigMessage`'s
# pydantic fields plus the header-derived ones layered on by
# :func:`deserialize_config_message`. Any other top-level key in the
# envelope is a producer bug worth surfacing as a structured failure ack.
_EXPECTED_ENVELOPE_FIELDS: frozenset[str] = frozenset(
    {"event_type", "reference_id", "timestamp", "config", "status", "error"}
)

# Hardcoded mirror of ``EVENT_TYPE_UPSERT`` in :mod:`config_listener`. We
# avoid importing from the listener to break the cycle (listener imports
# us). The value is part of the public wire protocol and stable.
_UPSERT_EVENT_TYPE: str = "upsert"


def validate_envelope(envelope: dict[str, Any]) -> tuple[ConfigMessage | None, str | None]:
    """
    Envelope-level structural gate for a deserialized wire envelope.

    Catches the structural mistakes the producer can make at the envelope
    level and turns them into a clear failure string the listener can
    fold into a ``failure`` ack:

    * **Unrecognized keys** -- the producer sent a top-level field outside
      ``{event_type, reference_id, timestamp, config, status, error}``.
      Either a typo or a wire-contract drift; either way the operator
      needs a clear signal rather than the listener silently dropping
      the unknown field.
    * **Null / missing ``config`` for ``upsert``** -- an upsert with no
      config to apply is not a no-op, it's a producer error worth
      flagging. (``upsert-all`` with ``config=null`` is a legitimate
      "video-analytics-api had no config to send" signal handled by
      ``_handle_upsert_all`` -- not rejected here.)
    * **Pydantic typing** -- missing required field, wrong type on
      ``status`` / ``error`` / ``event_type`` / ``reference_id`` /
      ``timestamp`` (``config`` is ``Any``, deliberately permissive --
      :func:`validate` catches non-dict configs and returns failure with
      a per-section error message).

    Per-section / per-item filtering on ``msg.config`` is a separate
    concern handled by :func:`validate`, which the listener's handlers
    call on their scoped subset (the full ``msg.config`` for
    ``upsert``; just the ``app`` / ``sensors`` slice for the bootstrap
    ``upsert-all`` reply).

    :param dict envelope: Decoded wire envelope from
        :func:`deserialize_config_message`. Must carry the keys
        :class:`ConfigMessage` expects (``event_type``, ``reference_id``,
        ``timestamp``, ``config``, ``status``, ``error``).
    :return tuple[ConfigMessage | None, str | None]:
        ``(msg, None)`` on success; ``(None, error_string)`` on any of
        the failure modes above.
    """
    # 1. Extras check. Catches typos and contract drift early so the
    #    operator gets a precise ack rather than a silent ignore.
    extras = sorted(set(envelope.keys()) - _EXPECTED_ENVELOPE_FIELDS)
    if extras:
        return None, f"unrecognized envelope keys: {extras}"

    # 2. Null-config check (upsert only). ``upsert-all`` with null config
    #    is the bootstrap-failure signal -- legitimate, handled downstream.
    if envelope.get("event_type") == _UPSERT_EVENT_TYPE and envelope.get("config") is None:
        return None, "no config to update"

    # 3. Pydantic typing (status / error / event_type / reference_id /
    #    timestamp). ``config: Any`` is intentionally permissive here.
    try:
        return ConfigMessage(**envelope), None
    except ValidationError as e:
        return None, f"invalid envelope shape: {e}"


def _verify_app_items(items: Any) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """Validate an ``app[*]`` list against :data:`ALLOWED_APP_KEYS`
    plus the per-key rules in :data:`APP_VALUE_VALIDATORS`."""
    applied: list[dict[str, str]] = []
    rejected: list[tuple[str, str]] = []
    if not isinstance(items, list):
        rejected.append(("app", "must be a list of {name, value} pairs"))
        return applied, rejected
    for index, item in enumerate(items):
        ok, reason = _verify_kv_pair(item, ALLOWED_APP_KEYS, APP_VALUE_VALIDATORS)
        if ok:
            applied.append({"name": item["name"], "value": item["value"]})
        else:
            rejected.append((f"app[{index}]", reason))
    return applied, rejected


def _verify_sensor_items(items: Any) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """
    Validate a ``sensors[*]`` list against :data:`ALLOWED_SENSOR_KEYS`.

    A sensor entry survives only if its ``id`` is a non-empty string and
    every ``(name, value)`` in its ``configs`` validates. If any sub-item
    rejects, the whole sensor entry is dropped (sub-rejections are still
    recorded under the per-config-item path).
    """
    applied: list[dict[str, Any]] = []
    rejected: list[tuple[str, str]] = []
    if not isinstance(items, list):
        rejected.append(("sensors", "must be a list of sensor entries"))
        return applied, rejected
    for index, sensor in enumerate(items):
        if not isinstance(sensor, dict):
            rejected.append((f"sensors[{index}]", "must be a dict"))
            continue
        sensor_id = sensor.get("id")
        if not isinstance(sensor_id, str) or not sensor_id:
            rejected.append((f"sensors[{index}].id", "missing or empty"))
            continue
        configs = sensor.get("configs", [])
        if not isinstance(configs, list):
            rejected.append((f"sensors[{index}].configs", "must be a list"))
            continue
        if not configs:
            # Ambiguous semantics: an explicit ``"configs": []`` could
            # mean "no change for this sensor" (operator should just omit
            # the entry instead) or "wipe all of this sensor's configs"
            # (would need a separate delete event we don't support yet).
            # Reject so the operator disambiguates rather than picking
            # one interpretation silently.
            rejected.append((
                f"sensors[{index}/{sensor_id}].configs",
                "empty sensor configs not allowed",
            ))
            continue
        valid_pairs: list[dict[str, str]] = []
        sensor_failed = False
        for cfg_index, cfg in enumerate(configs):
            ok, reason = _verify_kv_pair(cfg, ALLOWED_SENSOR_KEYS, SENSOR_VALUE_VALIDATORS)
            if ok:
                valid_pairs.append({"name": cfg["name"], "value": cfg["value"]})
            else:
                rejected.append((f"sensors[{index}/{sensor_id}].configs[{cfg_index}]", reason))
                sensor_failed = True
        if not sensor_failed:
            applied.append({"id": sensor_id, "configs": valid_pairs})
    return applied, rejected


def _verify_kv_pair(
    item: Any,
    allowlist: frozenset[str],
    value_validators: dict[str, ValueValidator],
) -> tuple[bool, str]:
    """
    Verify one ``{name, value}`` dict against the supplied allowlist
    and per-key value rules.

    Three-stage gate:

    1. Shape -- ``item`` must be a dict; ``name`` non-empty string;
       ``value`` string.
    2. Allowlist -- ``name`` must appear in ``allowlist`` (either
       :data:`ALLOWED_APP_KEYS` or :data:`ALLOWED_SENSOR_KEYS`
       depending on the calling section).
    3. Value -- ``value`` must satisfy the per-key rule in
       ``value_validators`` (either :data:`APP_VALUE_VALIDATORS` or
       :data:`SENSOR_VALUE_VALIDATORS`). Names absent from the registry
       pass unconditionally so future allowlist additions degrade safely.

    :param Any item: The on-the-wire ``{name, value}`` dict.
    :param frozenset[str] allowlist: Set of names this item is being
        checked against.
    :param dict value_validators: Per-key value-rule registry.
    :return tuple[bool, str]: ``(True, "")`` if the item passes all
        three stages; otherwise ``(False, reason)`` for the caller to
        record.
    """
    if not isinstance(item, dict):
        return False, "must be a dict with 'name' and 'value' keys"
    name = item.get("name")
    value = item.get("value")
    if not isinstance(name, str) or not name:
        return False, "'name' must be a non-empty string"
    if not isinstance(value, str):
        return False, "'value' must be a string"
    if name not in allowlist:
        return False, f"name={name!r} is not allowlisted for dynamic update"
    ok, reason = validate_value(name, value, value_validators)
    if not ok:
        return False, f"name={name!r} value={value!r} invalid: {reason}"
    return True, ""

def _format_rejections(rejections: list[tuple[str, str]], applied_count: int = 0) -> str:
    """Build the ``error`` for partial-success / failure cases."""
    details = "; ".join(f"{path}: {reason}" for path, reason in rejections)
    if applied_count > 0:
        return f"applied {applied_count}; rejected: {details}"
    return f"rejected: {details}"
