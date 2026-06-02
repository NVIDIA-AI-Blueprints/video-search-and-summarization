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
Auto-coloring helpers for 3D-bbox visualizations.

Centralises the per-detection BGR-colour assignment used by both
the NVSchema renderer (:func:`draw_bev_objects_bbox_in_image`) and the
AICity-GT renderer (:func:`process_frame_gt_json_aicity`).  Two
modes are supported, exposed via the ``color_by`` knob:

* ``"track_id"`` — cycle :data:`COLOR_MAP` by per-detection integer
  track id (modulo palette length).  Each track / object id gets its
  own colour, stable across frames within one process run.
* ``"class"`` — FIFO palette walk over the raw ``type`` field: the
  first distinct class seen claims slot 0, the next claims slot 1,
  and so on (see :func:`_fifo_palette_slots`).  Picked when reviewers
  want all instances of a class to render in the same colour within
  one frame; the binding is **per-call**, not global.

Pre-extraction these helpers lived in ``visualization/render.py``
alongside the per-frame and scene-level drivers.  They have **zero
dependency on rendering** (just on :data:`COLOR_MAP` + dict/list
ops) and are now split out so the renderer module stays focused on
the iterate-frames-and-write-images flow, and so any future
non-render consumers (analysis notebooks, debug tools) can reuse the
same auto-coloring conventions.

All names start with ``_`` because they're considered internal to
the visualization package.  External callers should pass an
explicit ``color=`` to the public renderers; the auto-coloring path
is reserved for the convenience defaults.
"""

from typing import Dict, List, Tuple

from spatialai_data_utils.constants import KEY_NVSCHEMA_ID
from spatialai_data_utils.visualization import COLOR_MAP

__all__ = [
    "_VALID_COLOR_BY",
    "_validate_color_by",
    "_track_id_to_color_key",
    "_fifo_palette_slots",
    "_assign_colors",
]

_VALID_COLOR_BY = ("track_id", "class")


def _validate_color_by(color_by: str) -> None:
    """Reject unknown ``color_by`` values with a consistent message.

    Centralised so :func:`draw_bev_objects_bbox_in_image` and
    :func:`process_frame_gt_json_aicity` (the two entry points whose
    callers can pick the auto-coloring mode) raise the same
    ``ValueError`` text instead of duplicating the check inline.
    """
    if color_by not in _VALID_COLOR_BY:
        raise ValueError(
            f"color_by must be one of {_VALID_COLOR_BY!r}; got {color_by!r}"
        )


def _track_id_to_color_key(det: Dict) -> int:
    """Map a raw NVSchema object's ``id`` (string or int) to an int colour key."""
    raw_id = det.get(KEY_NVSCHEMA_ID, 0)
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return hash(str(raw_id)) & 0xFFFFFFFF


def _fifo_palette_slots(type_names: List[str]) -> List[int]:
    """Assign each unique object type its own ``COLOR_MAP`` slot in FIFO order.

    Walks *type_names* once; the first type encountered claims slot 0,
    the next distinct type claims slot 1, and so on.  Repeated
    occurrences of the same type re-use its already-assigned slot,
    so every box sharing a ``type`` renders in the same colour.

    When more than ``len(COLOR_MAP)`` distinct types show up the
    assignment wraps (modulo the palette length) — the 51st unique
    type reuses slot 0.  The palette was hand-curated for maximal
    visual separation across its first entries, so walking it in
    order gives better discrimination for small class counts than a
    hash-based scheme where two types can collide mod 50.

    Caveat: the binding is **per-call**, not global — if the first
    detection in Frame A is a ``Person`` but in Frame B is a
    ``Transporter``, their palette slots will swap between frames.
    Callers that need cross-frame stable colours should either
    pre-sort detections by type or inject an explicit ``color=``
    list into :func:`draw_bev_objects_bbox_in_image`.

    :param type_names: Raw type strings in the order they appear in
        the detection list.
    :return: One palette-slot index per input type, same length and
        ordering as *type_names*.
    """
    type_to_slot: Dict[str, int] = {}
    n_palette = len(COLOR_MAP)
    slots: List[int] = []
    for name in type_names:
        if name not in type_to_slot:
            type_to_slot[name] = len(type_to_slot) % n_palette
        slots.append(type_to_slot[name])
    return slots


def _assign_colors(
    *,
    color_by: str,
    type_names: List[str],
    track_ids: List,
) -> List[Tuple[int, int, int]]:
    """Pick BGR colours for a parallel list of detections.

    Centralises the auto-coloring logic that used to be duplicated in
    :func:`draw_bev_objects_bbox_in_image` (NVSchema path) and
    :func:`process_frame_gt_json_aicity` (gt_json_aicity path).

    * ``color_by="class"``: walk ``COLOR_MAP`` in FIFO order over
      *type_names* (see :func:`_fifo_palette_slots`).  Repeats reuse
      the already-assigned slot, so every box of a given class
      renders in the same colour within one call.
    * ``color_by="track_id"``: cycle ``COLOR_MAP`` by integer track
      id (modulo palette length).

    Both inputs must be the same length so the returned colour list
    aligns 1-to-1 with the detection list.

    :param color_by: ``"class"`` or ``"track_id"``.  Caller is
        responsible for prior validation (the public-facing
        renderers run :func:`_validate_color_by` once at entry).
    :param type_names: Per-detection raw ``type`` (NVSchema) /
        ``"object type"`` (gt_json_aicity) string.  Only consulted
        when *color_by* is ``"class"``.
    :param track_ids: Per-detection integer track id.  Only consulted
        when *color_by* is ``"track_id"``.
    :return: Per-detection BGR tuple list, same length as the inputs.
    """
    if color_by == "class":
        slots = _fifo_palette_slots(type_names)
        return [COLOR_MAP[s] for s in slots]
    n_palette = len(COLOR_MAP)
    return [COLOR_MAP[int(tid) % n_palette] for tid in track_ids]
