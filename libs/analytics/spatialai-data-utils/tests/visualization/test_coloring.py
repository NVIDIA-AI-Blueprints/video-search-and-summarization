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

"""Coverage supplement for ``visualization.coloring`` — pins
``_track_id_to_color_key``'s hash-fallback branch when the raw id is
neither an int nor an int-coercible string."""

from spatialai_data_utils.constants import KEY_NVSCHEMA_ID
from spatialai_data_utils.visualization import COLOR_MAP
from spatialai_data_utils.visualization.coloring import (
    _assign_colors,
    _fifo_palette_slots,
    _track_id_to_color_key,
    _validate_color_by,
)


def test_track_id_to_color_key_hashes_non_int_coercible_ids():
    """A track id that can't be turned into an ``int`` (e.g. a UUID
    string or a list) falls back to ``hash(str(raw)) & 0xFFFFFFFF``
    so the colour key is still deterministic."""
    det = {KEY_NVSCHEMA_ID: "uuid-abc-123"}
    key = _track_id_to_color_key(det)
    assert isinstance(key, int)
    assert key == hash("uuid-abc-123") & 0xFFFFFFFF
    # Same input gives same key (deterministic within one process).
    assert _track_id_to_color_key(det) == key


def test_track_id_to_color_key_hashes_unhashable_to_int_style_input():
    """A non-string non-int value (e.g. a list) also falls through
    the TypeError branch in ``int(raw_id)``."""
    det = {KEY_NVSCHEMA_ID: ["nested", "value"]}
    key = _track_id_to_color_key(det)
    assert key == hash(str(["nested", "value"])) & 0xFFFFFFFF


def test_track_id_to_color_key_default_when_id_missing():
    """When the dict has no id field the default 0 is used (covers
    the ``det.get(..., 0)`` lookup, not a fallback branch)."""
    assert _track_id_to_color_key({}) == 0


def test_assign_colors_class_mode_uses_fifo_slots():
    out = _assign_colors(
        color_by="class",
        type_names=["A", "B", "A", "C"],
        track_ids=[1, 2, 3, 4],
    )
    # First-occurrence ordering: A=slot0, B=slot1, C=slot2.
    assert out[0] == COLOR_MAP[0]
    assert out[1] == COLOR_MAP[1]
    assert out[2] == COLOR_MAP[0]
    assert out[3] == COLOR_MAP[2]


def test_assign_colors_track_id_mode_cycles_palette():
    n = len(COLOR_MAP)
    out = _assign_colors(
        color_by="track_id",
        type_names=["A"] * 3,
        track_ids=[0, 1, n],  # 0 and n collide modulo palette length
    )
    assert out[0] == COLOR_MAP[0]
    assert out[1] == COLOR_MAP[1]
    assert out[2] == COLOR_MAP[0]
