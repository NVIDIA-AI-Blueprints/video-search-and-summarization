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

"""Tests for ``spatialai_data_utils.loaders.object_classes``.

Focused coverage of the :class:`ObjectClassConfig` dataclass — particularly
the :meth:`display_name` method used by the visualization pipeline to
translate raw NVSchema / sub-class type names into display strings.
"""

import pytest

from spatialai_data_utils.loaders.object_classes import (
    ObjectClassConfig,
    load_object_class_config,
)


@pytest.fixture()
def cfg():
    """Load the shipped ``warehouse`` config (sub-classes + display map)."""
    return load_object_class_config("warehouse")


class TestResolveClass:
    """Cover ``ObjectClassConfig.resolve_class`` behaviour."""

    def test_primary_class_is_returned_as_is(self, cfg):
        """A name already in ``class_to_id`` maps to itself."""
        name = next(iter(cfg.class_to_id))
        assert cfg.resolve_class(name) == name

    def test_sub_class_resolves_to_parent(self, cfg):
        """Sub-class names are resolved to their parent class."""
        assert cfg.resolve_class("palletjackforklift") == "pallet_truck"

    def test_unknown_name_returns_none(self, cfg):
        """Unknown names return ``None`` instead of raising."""
        assert cfg.resolve_class("definitely_not_a_class") is None


class TestDisplayName:
    """Cover ``ObjectClassConfig.display_name`` — NVSchema -> display translation."""

    def test_nvschema_display_name_roundtrips(self, cfg):
        """Already-display-named type strings stay unchanged."""
        assert cfg.display_name("Fourier_GR1_T2_Humanoid") == "Fourier_GR1_T2_Humanoid"
        assert cfg.display_name("Person") == "Person"

    def test_internal_class_resolves_to_display(self, cfg):
        """Internal class names resolve to the display form via map_class_names."""
        assert cfg.display_name("gr1_t2") == "Fourier_GR1_T2_Humanoid"
        assert cfg.display_name("person") == "Person"

    def test_sub_class_resolves_to_parent_display(self, cfg):
        """Sub-class names resolve to parent, then through ``map_class_names``."""
        assert cfg.display_name("palletjackforklift") == "Pallet_Truck"

    def test_unknown_type_passes_through(self, cfg):
        """Entirely unknown type names are returned verbatim."""
        assert cfg.display_name("mystery_thing") == "mystery_thing"


class TestDisplayNameWithCustomConfig:
    """``display_name`` behaviour on a synthetic config with full mappings."""

    def _cfg(self):
        """Build an in-memory config with a sub-class + a parent display map."""
        c = ObjectClassConfig(
            class_list=["animal"],
            sub_class_dict={"animal": ["kitten"]},
            map_class_names={"animal": "Animal"},
            class_to_id={"animal": 0},
            sub_to_parent={"kitten": "animal"},
        )
        return c

    def test_sub_class_resolves_through_parent_to_display(self):
        """Sub-class -> parent -> display-name chain resolves fully."""
        assert self._cfg().display_name("kitten") == "Animal"


class TestIsKnownType:
    """Cover ``ObjectClassConfig.is_known_type`` — drives viz class filtering.

    The viz stack uses this method to drop boxes whose type isn't in the
    active config (so reviewers see only the classes the taxonomy cares
    about).  All three lookup paths must accept a recognised type.
    """

    def test_primary_class_is_known(self, cfg):
        """A name in ``class_to_id`` (i.e. ``class_list``) is known."""
        name = next(iter(cfg.class_to_id))
        assert cfg.is_known_type(name) is True

    def test_sub_class_is_known(self, cfg):
        """A sub-class name (key in ``sub_to_parent``) is known."""
        assert cfg.is_known_type("palletjackforklift") is True

    def test_nvschema_display_name_is_known(self, cfg):
        """An NVSchema display name (value in ``map_class_names``) is known.

        This is the path the visualization stack hits most often, since
        upstream models emit ``"Person"`` / ``"Fourier_GR1_T2_Humanoid"``
        directly in NVSchema results.
        """
        assert cfg.is_known_type("Fourier_GR1_T2_Humanoid") is True
        assert cfg.is_known_type("Person") is True

    def test_unknown_name_is_not_known(self, cfg):
        """A name absent from all three lookup paths is rejected."""
        assert cfg.is_known_type("definitely_not_a_class") is False

    def test_empty_string_is_not_known(self, cfg):
        """Empty strings (occasionally seen on malformed inputs) reject cleanly."""
        assert cfg.is_known_type("") is False

    def test_minimal_config_with_only_primary(self):
        """Configs without sub-classes / display-name maps still work.

        Constructs an :class:`ObjectClassConfig` with only ``class_list``
        / ``class_to_id`` populated (legacy / minimal taxonomy) — the
        sub-class and display-name lookup paths must short-circuit
        gracefully without raising.
        """
        c = ObjectClassConfig(
            class_list=["car"], class_to_id={"car": 0},
        )
        assert c.is_known_type("car") is True
        assert c.is_known_type("truck") is False
