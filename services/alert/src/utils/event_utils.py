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
Utility helpers for event normalization and identification.
"""

from __future__ import annotations
from typing import Dict, Any


def normalize_alert_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Inject helper fields for alert messages when nested schema is present.

    Adds:
      - sensorId (from sensor.id)
      - category (from analyticsModule.id)
      - notification_type = 'alert'
      - _normalized_added_fields tracker for later stripping
    No-op if nested fields are absent.
    """
    if not isinstance(message, dict):
        return message

    sensor = message.get('sensor') if isinstance(message.get('sensor'), dict) else None
    am = message.get('analyticsModule') if isinstance(message.get('analyticsModule'), dict) else None
    obj = message.get('object') if isinstance(message.get('object'), dict) else None
    if not sensor and not am and not obj:
        return message

    # Each block is optional: a payload may carry only ``analyticsModule``
    # (with a flat ``sensorId`` already set) or only ``object``. Dereference
    # each one behind its own guard so a partial payload is normalized instead
    # of raising and taking the whole batch down with it.
    sensor_id = sensor.get('id') if sensor else None
    category = am.get('id') if am else None
    object_id = obj.get('id') if obj else None
    if not sensor_id and not category and not object_id:
        return message

    updated = dict(message)
    added: list[str] = []
    if sensor_id and 'sensorId' not in updated:
        updated['sensorId'] = sensor_id
        added.append('sensorId')
    if category and 'category' not in updated:
        updated['category'] = category
        added.append('category')
    # Populate objectIds from object.id when available and objectIds is missing
    if object_id and 'objectIds' not in updated:
        updated['objectIds'] = [object_id]
        added.append('objectIds')
    if 'notification_type' not in updated:
        updated['notification_type'] = 'alert'
    if added:
        existing = updated.get('_normalized_added_fields') or []
        updated['_normalized_added_fields'] = list({*existing, *added})
    return updated


def strip_normalization_fields(message: Dict[str, Any]) -> Dict[str, Any]:
    """Remove temporary helper fields added during normalization.

    Removes notification_type, tracked helper fields, and the tracker key.
    Returns a shallow-copied dict.
    """
    if not isinstance(message, dict):
        return message
    doc = dict(message)
  
    for fld in doc.get('_normalized_added_fields', []) or []:
        doc.pop(fld, None)
    doc.pop('_normalized_added_fields', None)
    return doc


#: Payload field that carries an event's alert/incident classification through
#: to terminal routing. :func:`is_alert` reads only this field.
EVENT_KIND_FIELD = 'notification_type'


def stamp_event_kind(message: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """Record ``kind`` as the event's authoritative classification.

    The source derives the kind from the stream or topic an entry arrived on,
    which is deployment configuration. Terminal routing reads it back through
    :func:`is_alert`, which keys on a payload field — so without this the two
    can disagree: a producer that sets ``notification_type`` publishes an
    incident to the alert stream, and an alert whose nested blocks are absent
    (so normalization never set the field) publishes to the incident stream.
    Neither raises. Writing the transport's answer here is what makes the
    configured stream, rather than the payload, decide where a verdict goes.

    ``incident`` clears the field rather than setting it to ``"incident"``: an
    incident carried none before, and adding one would change every published
    incident document to fix a routing bug in the alert direction.

    Returns a shallow copy when a change is needed, and ``message`` itself
    otherwise, matching :func:`normalize_alert_message`.
    """
    if not isinstance(message, dict):
        return message

    if str(kind).lower() == 'alert':
        if message.get(EVENT_KIND_FIELD) == 'alert':
            return message
        updated = dict(message)
        updated[EVENT_KIND_FIELD] = 'alert'
        return updated

    if message.get(EVENT_KIND_FIELD) is None:
        return message
    updated = dict(message)
    updated.pop(EVENT_KIND_FIELD, None)
    return updated


def is_alert(message: Dict[str, Any]) -> bool:
    """Detect if message is an alert-style payload.

    Heuristic: presence of nested sensor.id or analyticsModule.id, or explicit notification_type == 'alert'.
    """
    if not isinstance(message, dict):
        return False
    if message.get('notification_type') == 'alert':
        return True
    else:
        return False
    


def get_notification_type(message: Dict[str, Any]) -> str:
    """Return 'alert' when identified as alert, else 'incident'."""
    return 'alert' if is_alert(message) else 'incident'


