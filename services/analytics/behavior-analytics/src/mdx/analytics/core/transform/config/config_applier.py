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
Mutate an :class:`AppConfig` instance with already-validated dynamic-config items.

The applier deliberately holds **no validation logic**: callers (the main-process
listener and the per-worker file monitor) run :func:`validate` (from :mod:`config_validator`) first,
then hand the filtered :class:`ValidationResult.applied_app` /
``applied_sensors`` lists to :meth:`apply`.

A single :meth:`apply` method services both flows:

* Flow A (``upsert``) -- partial patch from the video analytics api; new items overwrite
  existing keys and unknown keys are added.
* Flow B (``upsert-all``) -- bootstrap reply carrying the video analytics api's view of the
  full config; treated as an additive merge as well. Removing items via
  bootstrap is intentionally **not** supported in this version (would require
  a separate ``delete`` event type).

After mutation the applier calls :meth:`AppConfig.invalidate_caches` so
consumers reading ``self.config.X`` at use-time pick up the new values on
next access. Consumers that capture config values at ``__init__`` still
require a process restart -- by design.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

from mdx.analytics.core.schema.config import AppConfig

logger = logging.getLogger(__name__)


# Keys whose change alters what the pipeline *does*, not just a number it carries.
#
# An applied update is otherwise invisible: the value lands in AppConfig and the next read at
# use-time picks it up, with nothing in the log to correlate against a change in the output stream.
# When someone later asks why the behavior topic went quiet, or why incidents stopped, the answer is
# often an upsert that landed hours earlier. These get their consequence spelled out at INFO so the
# log carries the "why", not just the "what".
#
# Deliberately not exhaustive -- a key absent from here still logs its old -> new transition, it just
# does not claim to explain the effect. Add an entry when the effect is not obvious from the name.
MAJOR_APP_KEY_IMPACT: dict[str, str] = {
    "behaviorEmitOnce":
        "changes when behaviors are written: 'true' holds each track and writes it once, "
        "behaviorStateValidInterval seconds after it goes quiet; 'false' writes every batch. "
        "Switching off hands over anything still held, once.",
    "behaviorStateValidInterval":
        "sets how long a track may be silent before it is considered ended -- and so, under "
        "behaviorEmitOnce, how long before its behavior is emitted.",
    "stateManagementFilter":
        "changes which object types are tracked at all; types outside the filter produce no "
        "behaviors, events or incidents.",
    "proximityViolationIncidentEnable":
        "turns proximity incidents on/off.",
    "restrictedAreaViolationIncidentEnable":
        "turns restricted-area incidents on/off.",
    "confinedAreaViolationIncidentEnable":
        "turns confined-area incidents on/off.",
    "fovCountViolationIncidentEnable":
        "turns FOV-count incidents on/off.",
}


# Wire-format outcome of an apply attempt, used by the listener to construct
# the outgoing ``ack`` payload. Lives here for now because callers import it
# alongside ``ConfigApplier``; could move to ``config_publisher`` if the
# publisher ever grows its own message-shape module.
ApplyStatus = Literal["success", "partial-success", "failure"]


@dataclass
class ApplyResult:
    """
    Wire payload for the ``ack`` reply (Flow A only).

    Built by :class:`ConfigListener` from a :class:`ValidationResult` plus an
    :meth:`AppConfig.to_mutable_snapshot` taken after :meth:`ConfigApplier.apply`
    runs.

    :ivar ApplyStatus status: ``"success"``, ``"partial-success"``, or
        ``"failure"``.
    :ivar dict[str, Any] | None config: ``app`` + ``sensors`` snapshot of
        main's live config after apply, with applied changes baked in.
        ``None`` when ``status == "failure"`` (nothing to confirm).
    :ivar str | None error: Human-readable summary of rejections;
        ``None`` when ``status == "success"``.
    """

    status: ApplyStatus
    config: dict[str, Any] | None = None
    error: str | None = None


class ConfigApplier:
    """
    Apply already-validated items to an :class:`AppConfig` instance.

    No validation, no return value. The caller is expected to have run
    :func:`validate` (from :mod:`config_validator`) first and pass the filtered ``applied_app`` /
    ``applied_sensors`` lists.

    :ivar AppConfig _config: Live config; mutated in place on apply.
    """

    def __init__(self, config: AppConfig) -> None:
        """
        :param AppConfig config: Shared config instance that downstream
            consumers also hold a reference to. Mutations land here.
        :return: None
        """
        self._config = config

    def apply(
        self,
        applied_app: list[dict[str, str]],
        applied_sensors: list[dict[str, Any]],
    ) -> None:
        """
        Merge already-validated items into the live config.

        Iterates the filtered lists from :class:`ValidationResult` and routes
        each entry through the existing :meth:`AppConfig.set_app_config` and
        :meth:`AppConfig.set_sensor_config` setters, which handle insert vs
        overwrite of existing keys. Finally invalidates caches so the next
        next read at use-time sees the new values.

        :param list[dict[str, str]] applied_app: Validated ``app`` items;
            each is ``{"name": str, "value": str}``.
        :param list[dict[str, Any]] applied_sensors: Validated sensor
            entries; each is ``{"id": str, "configs": [{"name", "value"}]}``.
        :return: None
        """
        for item in applied_app:
            name, new_value = item["name"], item["value"]
            # Read before writing so the log can show the transition, not just the destination.
            old_value = self._config.get_app_config(name)
            self._config.set_app_config(name, new_value)
            self._log_app_change(name, old_value, new_value)

        for sensor in applied_sensors:
            for item in sensor["configs"]:
                self._config.set_sensor_config(item["name"], item["value"], sensor_id=sensor["id"])
                logger.info(
                    f"Dynamic config applied: sensor '{sensor['id']}' {item['name']} -> '{item['value']}'")

        self._config.invalidate_caches()

    @staticmethod
    def _log_app_change(name: str, old_value: str, new_value: str) -> None:
        """
        Record one applied ``app`` item, and what it means when that is not obvious.

        A re-applied identical value is logged at debug: bootstrap replays the whole config on every
        restart, so treating those as changes would bury the real ones.

        :param str name: Config key.
        :param str old_value: Value before the write (empty string if the key was absent).
        :param str new_value: Value after the write.
        :return: None
        """
        if old_value == new_value:
            logger.debug(f"Dynamic config: {name} unchanged ('{new_value}')")
            return

        origin = f"'{old_value}' -> '{new_value}'" if old_value else f"set to '{new_value}' (was unset)"
        impact = MAJOR_APP_KEY_IMPACT.get(name)
        if impact:
            logger.info(f"Dynamic config applied: {name} {origin} -- {impact}")
        else:
            logger.info(f"Dynamic config applied: {name} {origin}")
