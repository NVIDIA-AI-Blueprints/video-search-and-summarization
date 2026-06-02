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
Per-key value validators for dynamic-config payloads.

:func:`validate` (from :mod:`config_validator`) runs a three-stage ladder per ``{name, value}`` item:

1. Shape -- ``name`` is a non-empty string, ``value`` is a string.
2. Allowlist -- ``name`` is in :data:`ALLOWED_APP_KEYS` /
   :data:`ALLOWED_SENSOR_KEYS`.
3. **Value -- this module** -- the string ``value`` parses and falls within
   the per-key rule.

Without stage 3, a payload like ``{"name": "behaviorMaxPoints", "value": "abc"}``
lands in ``AppConfig`` and only fails at the next downstream call to
``int(...)``, possibly minutes later, in a worker process. With stage 3, the
listener rejects it up front and the operator sees the failure in the ack
synchronously.

Registry layout
---------------

Two dicts -- :data:`APP_VALUE_VALIDATORS` and :data:`SENSOR_VALUE_VALIDATORS` --
map each allowlisted key to a callable ``(value: str) -> tuple[bool, str]``.
Keys absent from the registry pass stage 3 unconditionally (defensive default
for any future allowlist additions until a rule is written).

Helpers
-------

* :func:`_bool` -- exactly ``"true"`` or ``"false"``.
* :func:`_int` -- ``int(value)`` succeeds, optional inclusive ``min`` / ``max``.
* :func:`_float` -- ``float(value)`` succeeds, optional inclusive ``min`` /
  ``max``.
* :func:`_enum` -- ``value`` is one of a fixed set of strings.
* :func:`_json_list_of_str` -- ``json.loads(value)`` returns a list of strings.
* :func:`_json_dict` -- ``json.loads(value)`` returns a dict.
* :func:`_json_pydantic` -- ``json.loads(value)`` parses cleanly into the
  given Pydantic model via ``model_validate_json``.
* :func:`_datetime_iso8601_z` -- ``value`` parses as an ISO-8601 datetime
  with a ``Z`` (UTC) suffix.
* :func:`_non_empty_str` -- value is a non-empty string (already known true
  by the time we get here, but a min-length-after-strip guard keeps stray
  whitespace from passing).
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from mdx.analytics.core.schema.config import (
    AbnormalMovementConfig,
    FallRiskConfig,
    LackMovementConfig,
    SpeedViolationConfig,
    UnexpectedStopConfig,
)


# Each value-validator returns ``(True, "")`` or ``(False, reason)``.
ValueValidator = Callable[[str], tuple[bool, str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bool() -> ValueValidator:
    """Accept exactly ``"true"`` or ``"false"`` (lowercase)."""
    def check(value: str) -> tuple[bool, str]:
        if value not in ("true", "false"):
            return False, "must be 'true' or 'false'"
        return True, ""
    return check


def _int(min: int | None = None, max: int | None = None) -> ValueValidator:
    """``int(value)`` succeeds and falls in ``[min, max]`` (inclusive, optional)."""
    def check(value: str) -> tuple[bool, str]:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return False, "must be an integer"
        if min is not None and parsed < min:
            return False, f"must be int >= {min}"
        if max is not None and parsed > max:
            return False, f"must be int <= {max}"
        return True, ""
    return check


def _float(min: float | None = None, max: float | None = None) -> ValueValidator:
    """``float(value)`` succeeds and falls in ``[min, max]`` (inclusive, optional)."""
    def check(value: str) -> tuple[bool, str]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return False, "must be a number"
        if min is not None and parsed < min:
            return False, f"must be float >= {min}"
        if max is not None and parsed > max:
            return False, f"must be float <= {max}"
        return True, ""
    return check


def _enum(*allowed: str) -> ValueValidator:
    """``value`` must be one of ``allowed`` (string-equal)."""
    allowed_set = frozenset(allowed)
    rendered = ", ".join(repr(v) for v in allowed)

    def check(value: str) -> tuple[bool, str]:
        if value not in allowed_set:
            return False, f"must be one of ({rendered})"
        return True, ""
    return check


def _json_list_of_str() -> ValueValidator:
    """``value`` must be a JSON list whose elements are all strings."""
    def check(value: str) -> tuple[bool, str]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            return False, f"must be a JSON list of strings (parse error: {exc})"
        if not isinstance(parsed, list):
            return False, "must be a JSON list of strings"
        if not all(isinstance(x, str) for x in parsed):
            return False, "must be a JSON list of strings"
        return True, ""
    return check


def _json_dict() -> ValueValidator:
    """``value`` must be a JSON object (dict). Internal shape is not enforced."""
    def check(value: str) -> tuple[bool, str]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            return False, f"must be a JSON object (parse error: {exc})"
        if not isinstance(parsed, dict):
            return False, "must be a JSON object"
        return True, ""
    return check


def _json_pydantic(model: type[BaseModel]) -> ValueValidator:
    """
    ``value`` must parse cleanly into ``model`` via ``model_validate_json``.

    Used for the anomaly sub-configs (``anomalySpeedViolation`` etc.), whose
    on-the-wire form is a stringified Pydantic model. Catches both JSON
    parse errors and Pydantic schema violations.
    """
    name = model.__name__

    def check(value: str) -> tuple[bool, str]:
        try:
            model.model_validate_json(value)
        except ValidationError as exc:
            # ``ValidationError`` formatting is multi-line; collapse to one.
            errs = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            return False, f"must be JSON matching {name} ({errs})"
        except (TypeError, ValueError) as exc:  # pragma: no cover (Pydantic wraps both in ValidationError; defensive)
            return False, f"must be JSON matching {name} (parse error: {exc})"
        return True, ""
    return check


def _datetime_iso8601_z() -> ValueValidator:
    """``value`` must parse as ISO-8601 with a Z (UTC) suffix."""
    def check(value: str) -> tuple[bool, str]:
        if not value.endswith("Z"):
            return False, "must be ISO-8601 datetime with 'Z' suffix"
        try:
            # ``fromisoformat`` accepts ``+00:00`` but not ``Z`` until Python
            # 3.11; the codebase requires 3.13 so the bare call is safe, but
            # we normalize for older parsers reading back the same value.
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            return False, f"must be ISO-8601 datetime ({exc})"
        return True, ""
    return check


def _non_empty_str() -> ValueValidator:
    """``value`` must contain at least one non-whitespace character."""
    def check(value: str) -> tuple[bool, str]:
        if not value.strip():
            return False, "must be a non-empty string"
        return True, ""
    return check


# ---------------------------------------------------------------------------
# Registries -- one entry per allowlisted key.
#
# Keys absent from these dicts pass stage 3 unconditionally (so adding a new
# allowlisted key without a value rule degrades safely; the next person can
# tighten it).
# ---------------------------------------------------------------------------


APP_VALUE_VALIDATORS: dict[str, ValueValidator] = {
    # Behavior / object processing
    "behaviorMaxPoints": _int(min=1),
    "behaviorStateEndToleranceSec": _float(min=0.0),
    "behaviorStateTimeout": _int(min=0),
    "behaviorStateValidInterval": _int(min=0),
    "behaviorTimeThreshold": _datetime_iso8601_z(),
    "behaviorWatermarkSec": _int(min=0),
    "clusterThreshold": _float(min=0.0, max=1.0),
    "objectConfidenceThreshold": _float(min=0.0, max=1.0),
    "compactFrame": _bool(),
    "useObjectLocation": _bool(),
    "incidentObjectTtl": _int(min=0),
    "stateManagementFilter": _json_list_of_str(),
    "imageLocationMode": _enum("center", "bottom_center"),
    "in3dMode": _bool(),
    "advancedOverlay": _bool(),
    "inSimulationMode": _bool(),

    # Trajectory / map matching
    "trajGeoCoordEnable": _bool(),
    "trajDirectionMode": _enum("0", "1", "2"),
    "trajDirectionClusterMode": _enum("0", "1", "2"),
    "trajDistanceStride": _int(min=1),
    "trajSmoothMinPoints": _int(min=1),
    "trajSmoothWindowSize": _int(min=1),
    "trajSpeedSegmentSize": _int(min=1),
    "mapMatchingClasses": _json_list_of_str(),
    "mapMatchingMaxPoints": _int(min=1),

    # Incidents
    "proximityViolationIncidentEnable": _bool(),
    "proximityViolationIncidentThreshold": _int(min=1),
    "proximityViolationIncidentExpirationWindow": _int(min=1),
    "restrictedAreaViolationIncidentEnable": _bool(),
    "restrictedAreaViolationIncidentThreshold": _int(min=1),
    "restrictedAreaViolationIncidentExpirationWindow": _int(min=1),
    "confinedAreaViolationIncidentEnable": _bool(),
    "confinedAreaViolationIncidentThreshold": _int(min=1),
    "confinedAreaViolationIncidentExpirationWindow": _int(min=1),
    "fovCountViolationIncidentEnable": _bool(),
    "fovCountViolationIncidentThreshold": _int(min=1),
    "fovCountViolationIncidentObjectThreshold": _int(min=1),
    "fovCountViolationIncidentObjectType": _non_empty_str(),
    "fovCountViolationIncidentExpirationWindow": _int(min=1),
}


SENSOR_VALUE_VALIDATORS: dict[str, ValueValidator] = {
    # Trajectory / behavior (per-sensor)
    "tripwireMinPoints": _int(min=1),
    "sensorMinFrames": _int(min=1),

    # Proximity detection (per-sensor)
    "proximityDetectionEnable": _bool(),
    "proximityDetectionThreshold": _float(min=0.0),
    "proximityDetectionCenterClasses": _json_list_of_str(),
    "proximityDetectionSurroundingClasses": _json_list_of_str(),

    # Anomaly detection (per-sensor)
    "anomalyActionThreshold": _float(min=0.0, max=1.0),
    "anomalyClasses": _json_list_of_str(),
    "anomalyIgnoreSensors": _json_list_of_str(),
    "anomalySpeedViolation": _json_pydantic(SpeedViolationConfig),
    "anomalyUnexpectedStop": _json_pydantic(UnexpectedStopConfig),
    "anomalyAbnormalMovement": _json_pydantic(AbnormalMovementConfig),
    "anomalyFallRisk": _json_pydantic(FallRiskConfig),
    "anomalyLackMovement": _json_pydantic(LackMovementConfig),
}


def validate_value(name: str, value: str, registry: dict[str, ValueValidator]) -> tuple[bool, str]:
    """
    Look up ``name`` in ``registry`` and run its validator on ``value``.

    Names absent from the registry pass unconditionally -- this keeps the
    ladder forward-compatible with future allowlist additions until a rule
    is written.

    :param str name: The kv pair's ``name`` (already shape- and allowlist-checked).
    :param str value: The kv pair's ``value`` (already known to be a string).
    :param dict registry: :data:`APP_VALUE_VALIDATORS` or
        :data:`SENSOR_VALUE_VALIDATORS` depending on the calling section.
    :return tuple[bool, str]: ``(True, "")`` if the value passes; otherwise
        ``(False, reason)``.
    """
    rule = registry.get(name)
    if rule is None:
        return True, ""
    return rule(value)
