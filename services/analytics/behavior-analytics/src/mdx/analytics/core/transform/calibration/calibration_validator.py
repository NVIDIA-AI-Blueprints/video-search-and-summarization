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
Schema validator for calibration notification payloads.

We vendor the AJV schema that ``web-api-core`` uses pre-publish
(``schemas/calibration.schema.json``, derived from
``web-apis/web-api-core/schemas/ajv/calibration.json`` with the AJV-only
``errorMessage`` keywords stripped). Validating on the worker side gives us
defense-in-depth against schema drift between the publisher and consumer.

Per-action policy (action prefix is parsed from the notification filename --
see :func:`CalibrationBase.reload_data`):

* ``upsert-all`` / ``upsert``: validate against the **full** schema.
  Web-api enforces the same schema before publishing, so a violation here
  signals either a schema drift or a non-web-api producer.
* ``delete``: validate against a **minimal** schema (sensors array,
  each item carrying an ``id``). Web-api builds the delete payload from
  existing stored sensors; those records may pre-date strict schema
  tightening, so a full check would falsely reject legitimate deletes.
"""

import json
import logging
from importlib import resources
from typing import Any

from jsonschema import Draft7Validator

from mdx.analytics.core.constants import (
    CALIBRATION_ACTION_DELETE,
    CALIBRATION_ACTION_UPSERT,
    CALIBRATION_ACTION_UPSERT_ALL,
)

logger = logging.getLogger(__name__)

_FULL_SCHEMA_RESOURCE = resources.files(__package__).joinpath(
    "schemas/calibration.schema.json"
)

# Lazy-load the (sizeable) full schema once per process.
_FULL_VALIDATOR: Draft7Validator | None = None


def _full_validator() -> Draft7Validator:
    global _FULL_VALIDATOR
    if _FULL_VALIDATOR is None:
        schema = json.loads(_FULL_SCHEMA_RESOURCE.read_text())
        _FULL_VALIDATOR = Draft7Validator(schema)
    return _FULL_VALIDATOR


# Minimal schema for delete notifications. Web-api emits
# ``{version, osmURL, calibrationType, sensors: [<deleted sensors>]}``,
# with each sensor copied verbatim from the existing store. Those stored
# sensors may legitimately omit fields the strict schema now requires
# (legacy data, hand-edited entries, etc.), so we accept anything that
# carries enough structure to act on -- ``sensors`` must be a non-empty
# array of objects each with a non-empty ``id``.
_DELETE_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["sensors"],
    "properties": {
        "sensors": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "minLength": 1}},
            },
        },
    },
}
_DELETE_VALIDATOR = Draft7Validator(_DELETE_SCHEMA)


# How many individual schema errors to include in the exception message
# before truncating. The full list still appears in the structured log.
_MAX_REPORTED_ERRORS = 5


class CalibrationValidationError(ValueError):
    """Raised when a calibration payload fails schema validation."""


def validate(calibration: dict[str, Any], action: str) -> None:
    """
    Validate ``calibration`` against the schema appropriate for ``action``.

    Strict-mode: raises :class:`CalibrationValidationError` on the first
    failure. Callers must NOT apply a partial / malformed payload to
    running state -- the watcher's outer ``try/except`` (in
    :class:`CalibrationBase.CalibrationFileMonitor.on_moved`) logs the
    failure and keeps the previously-good calibration loaded.

    :param dict calibration: Parsed JSON body from the notification file.
    :param str action: Action prefix derived from the filename
        (one of :data:`CALIBRATION_ACTION_UPSERT_ALL`,
        :data:`CALIBRATION_ACTION_UPSERT`, :data:`CALIBRATION_ACTION_DELETE`).
    :raises CalibrationValidationError: If ``action`` is unknown or the
        payload fails the chosen schema.
    """
    if action in (CALIBRATION_ACTION_UPSERT_ALL, CALIBRATION_ACTION_UPSERT):
        validator = _full_validator()
    elif action == CALIBRATION_ACTION_DELETE:
        validator = _DELETE_VALIDATOR
    else:
        raise CalibrationValidationError(f"unknown calibration action: {action!r}")

    errors = sorted(validator.iter_errors(calibration), key=lambda e: list(e.absolute_path))
    if not errors:
        return

    # Log every violation for ops triage, but cap the raised message to keep
    # it readable in trace tools.
    for e in errors:
        path = "/".join(map(str, e.absolute_path)) or "<root>"
        logger.error(f"calibration schema violation ({action} at {path}): {e.message}")

    head = errors[: _MAX_REPORTED_ERRORS]
    summary = "; ".join(
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in head
    )
    if len(errors) > _MAX_REPORTED_ERRORS:
        summary += f" (+ {len(errors) - _MAX_REPORTED_ERRORS} more -- see logs)"
    raise CalibrationValidationError(
        f"calibration {action!r} payload failed schema validation: {summary}"
    )
