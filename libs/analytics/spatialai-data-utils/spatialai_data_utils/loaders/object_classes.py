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
Object Class Configuration Loader

Load object class definitions from Python configuration files. Each config
file defines a class taxonomy (CLASS_LIST, SUB_CLASS_DICT, MAP_CLASS_NAMES,
ATTRIBUTE_DICT, CLASS_RANGE_DICT) and the loader derives additional mappings
(class-name-to-ID, sub-class-to-parent).

Built-in configs ship under ``spatialai_data_utils/configs/object_classes/``
and can be loaded by name via :func:`load_object_class_config`.

Example::

    cfg = load_object_class_config("warehouse_v4")
    cfg.class_list          # ['person', 'gr1_t2', ...]
    cfg.class_to_id         # {'person': 0, 'gr1_t2': 1, ...}
    cfg.sub_to_parent       # {'palletjackforklift': 'pallet_truck', ...}

    # Or load from an arbitrary file path:
    cfg = load_object_class_config("/path/to/my_classes.py")
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, List, Optional

_BUILTIN_CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs" / "object_classes"


@dataclass
class ObjectClassConfig:
    """Structured representation of an object class configuration.

    Attributes:
        class_list: Ordered list of class names (index = class ID).
        sub_class_dict: Mapping of parent class -> list of sub-class names.
        map_class_names: Mapping of internal class name -> display name.
        attribute_dict: Mapping of class name -> attribute string.
        class_range_dict: Mapping of class name -> detection range (meters).
        class_to_id: Derived mapping of class name -> integer ID.
        sub_to_parent: Derived mapping of sub-class name -> parent class name.
        extra: Any additional fields found in the config file.
    """

    class_list: List[str] = field(default_factory=list)
    sub_class_dict: Dict[str, List[str]] = field(default_factory=dict)
    map_class_names: Dict[str, str] = field(default_factory=dict)
    attribute_dict: Dict[str, str] = field(default_factory=dict)
    class_range_dict: Dict[str, float] = field(default_factory=dict)
    class_to_id: Dict[str, int] = field(default_factory=dict)
    sub_to_parent: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def num_classes(self) -> int:
        return len(self.class_list)

    @property
    def id_to_class(self) -> Dict[int, str]:
        return {v: k for k, v in self.class_to_id.items()}

    def resolve_class(self, name: str) -> Optional[str]:
        """Resolve a (sub-)class name to its parent class, or return as-is if
        it is already a primary class.  Returns ``None`` if not found."""
        if name in self.class_to_id:
            return name
        return self.sub_to_parent.get(name)

    def is_known_type(self, type_name: str) -> bool:
        """Return True iff *type_name* is recognised by this config.

        Used by the visualization stack to filter detection / GT
        bounding boxes to **only those classes the active config knows
        about** — boxes whose ``type`` falls outside the config are
        dropped before drawing so reviewers don't get a flood of
        unrelated detections.

        A type is "known" when it matches any of:

        1. A primary class name (key in :attr:`class_to_id` /
           :attr:`class_list`) — e.g. ``"person"`` in
           ``warehouse``.
        2. A sub-class name (key in :attr:`sub_to_parent`) — e.g.
           ``"palletjackforklift"`` which rolls up to
           ``"pallet_truck"``.
        3. An NVSchema display name (value in :attr:`map_class_names`)
           — e.g. ``"Fourier_GR1_T2_Humanoid"`` is the display form
           the model emits for the internal class ``"gr1_t2"``.

        :param type_name: Raw ``type`` (NVSchema results) or
            ``"object type"`` (gt_json_aicity) string from a detection /
            annotation dict.
        :type type_name: str
        :return: ``True`` if the type is recognised by any of the
            three lookup paths above; ``False`` otherwise.
        :rtype: bool
        """
        if type_name in self.class_to_id:
            return True
        if type_name in self.sub_to_parent:
            return True
        return type_name in self.map_class_names.values()

    def display_name(self, type_name: str) -> str:
        """Resolve a type name to its human-readable display name.

        Handles both NVSchema-style type names (already the display form,
        e.g. ``"Fourier_GR1_T2_Humanoid"``) and internal / sub-class names
        (e.g. ``"palletjackforklift"`` -> ``"Pallet_Truck"``).

        Lookup order:

        1. Reverse ``map_class_names`` — NVSchema display names map back
           to an internal class; return the display name unchanged.
        2. :meth:`resolve_class` — fall back to sub-class -> parent
           resolution and translate the parent via ``map_class_names``.
        3. If neither matches, return *type_name* unchanged.

        :param type_name: The raw type string (NVSchema type or internal name).
        :returns: The display-friendly class name.
        """
        nvschema_to_internal = {v: k for k, v in self.map_class_names.items()}
        internal = nvschema_to_internal.get(type_name)
        if internal is not None:
            return self.map_class_names.get(internal, type_name)
        resolved = self.resolve_class(type_name)
        if resolved is not None:
            return self.map_class_names.get(resolved, type_name)
        return type_name


def list_builtin_configs() -> List[str]:
    """Return the names of all built-in object class configs."""
    return sorted(
        p.stem for p in _BUILTIN_CONFIG_DIR.glob("*.py") if p.name != "__init__.py"
    )


def _import_config_module(config_path: str) -> dict:
    """Import a Python file as a module and return its public attributes."""
    config_path = os.path.abspath(config_path)
    # Strip the extension via ``os.path.splitext`` so the helper works
    # for arbitrary extensions (``.py``, ``.pyx``, ``.cfg``) and for
    # extension-less filenames.  The pre-fix slice ``[:-3]`` chopped a
    # fixed 3 chars off the basename, which silently mangled
    # non-3-char extensions (``config.pyx`` -> ``"config.p"``) and
    # extension-less names (``config`` -> ``"co"``).
    module_name = os.path.splitext(os.path.basename(config_path))[0]
    if "." in module_name:
        raise ValueError(
            f"Dots are not allowed in config file name: {module_name!r}"
        )
    config_dir = os.path.dirname(config_path)
    sys.path.insert(0, config_dir)
    try:
        mod = import_module(module_name)
    finally:
        sys.path.pop(0)
    return {
        name: value
        for name, value in mod.__dict__.items()
        if not name.startswith("__")
    }


def load_object_class_config(config: str) -> ObjectClassConfig:
    """Load an object class configuration by name or file path.

    :param config: Either the name of a built-in config (e.g. ``"warehouse_v4"``)
        or an absolute/relative path to a Python config file.
    :returns: Populated :class:`ObjectClassConfig`.
    :raises FileNotFoundError: If the config file cannot be found.
    :raises KeyError: If required fields are missing from the config file.
    """
    if os.path.isfile(config):
        config_path = config
    else:
        config_path = str(_BUILTIN_CONFIG_DIR / f"{config}.py")
        if not os.path.isfile(config_path):
            available = list_builtin_configs()
            raise FileNotFoundError(
                f"Object class config {config!r} not found. "
                f"Available built-in configs: {available}"
            )

    raw = _import_config_module(config_path)

    class_list = raw.pop("CLASS_LIST")
    sub_class_dict = raw.pop("SUB_CLASS_DICT")
    map_class_names = raw.pop("MAP_CLASS_NAMES", {})
    attribute_dict = raw.pop("ATTRIBUTE_DICT", {})
    class_range_dict = raw.pop("CLASS_RANGE_DICT", {})

    class_to_id = {name: idx for idx, name in enumerate(class_list)}

    sub_to_parent: Dict[str, str] = {}
    for parent, subs in sub_class_dict.items():
        for sub in subs:
            sub_to_parent[sub] = parent

    return ObjectClassConfig(
        class_list=class_list,
        sub_class_dict=sub_class_dict,
        map_class_names=map_class_names,
        attribute_dict=attribute_dict,
        class_range_dict=class_range_dict,
        class_to_id=class_to_id,
        sub_to_parent=sub_to_parent,
        extra=raw,
    )


def load_class_config_from_file(config_path: str) -> dict:
    """Load object class configuration as a flat dictionary.

    This is the legacy interface matching the sparse4d config_loader. Prefer
    :func:`load_object_class_config` for new code.

    :param config_path: Path to a Python configuration file.
    :returns: Dictionary with all config fields plus derived
        ``CLASS_MAPPING_DICT`` and ``MAP_SUB_CLASS_TO_CLASS_DICT``.
    """
    raw = _import_config_module(config_path)

    raw["CLASS_MAPPING_DICT"] = {
        name: idx for idx, name in enumerate(raw["CLASS_LIST"])
    }

    raw["MAP_SUB_CLASS_TO_CLASS_DICT"] = {}
    for parent, subs in raw["SUB_CLASS_DICT"].items():
        for sub in subs:
            raw["MAP_SUB_CLASS_TO_CLASS_DICT"][sub] = parent

    return raw
