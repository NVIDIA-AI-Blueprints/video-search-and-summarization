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

from dataclasses import dataclass
from typing import Any, Literal

from mdx.analytics.core.schema.config import AppConfig


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
            self._config.set_app_config(item["name"], item["value"])
        for sensor in applied_sensors:
            for item in sensor["configs"]:
                self._config.set_sensor_config(item["name"], item["value"], sensor_id=sensor["id"])
        self._config.invalidate_caches()
