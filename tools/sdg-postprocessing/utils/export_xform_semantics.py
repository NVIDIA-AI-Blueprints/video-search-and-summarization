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
Export parent Xform rotation data for Mesh prims with authored Semantics from the
currently opened USD stage in Isaac Sim to a JSON file.

How to use in Isaac Sim Script Editor:
1) Set USER_OUTPUT_PATH below, or pass a path to run(output_path).
   - If you pass a DIRECTORY path (existing or not), the file name defaults to 'output_xform.json'.
   - If you pass a FILE path ending with '.json', it will be used as-is.
2) Run this script in the Script Editor. A JSON will be written to the resolved path.

You may also import this module elsewhere and call run(output_path).
"""

from pxr import Semantics, UsdGeom, Gf, Usd
import omni.usd
import json
import os
from typing import Any, Dict, Optional

# User-editable output path. If left empty, you must pass output_path to run().
USER_OUTPUT_PATH = ""

stage = omni.usd.get_context().get_stage()

def to_serializable(val: Any) -> Any:
    """
    Convert USD/Gf types and containers to JSON-serializable Python types.

    Args:
        val (Any): Value to convert.

    Returns:
        Any: Converted JSON-serializable value.
    """
    # Handle USD vector types
    if isinstance(val, (
        Gf.Vec2f, Gf.Vec2d, Gf.Vec2h,
        Gf.Vec3f, Gf.Vec3d, Gf.Vec3h,
        Gf.Vec4f, Gf.Vec4d, Gf.Vec4h,
    )):
        return list(val)
    # Handle containers
    if isinstance(val, (list, tuple)):
        return [to_serializable(v) for v in val]
    if isinstance(val, dict):
        return {k: to_serializable(v) for k, v in val.items()}
    return val

def _find_parent_xform(prim: UsdGeom.Xform) -> Optional[Any]:
    """
    Walk up the prim hierarchy to find the nearest parent Xform.

    Args:
        prim (Usd.Prim): Starting prim.

    Returns:
        Optional[Usd.Prim]: The nearest parent Xform prim if found, else None.
    """
    parent = prim.GetParent()
    xform_prim = None
    while parent and parent.GetPath() != '/':
        if parent.GetTypeName() == "Xform":
            xform_prim = parent
            break
        parent = parent.GetParent()
    return xform_prim

def _get_xform_rotation(xform_prim: Any) -> Optional[Any]:
    """
    Extract a rotation value from a parent Xform prim, if authored.

    Args:
        xform_prim (Usd.Prim): Xform prim to query.

    Returns:
        Optional[list|tuple]: Rotation value if available, otherwise None.
    """
    rotate = None
    for op_type in ["xformOp:rotateXYZ", "xformOp:rotateZYX", "xformOp:rotateYZX"]:
        if xform_prim.HasAttribute(op_type):
            rotate = xform_prim.GetAttribute(op_type).Get()
            break
    return rotate

def collect_semantics(stage: Usd.Stage) -> Dict[str, Dict[str, Any]]:
    """
    Traverse the stage, collecting semantics on Mesh prims and parent Xform rotations.

    Args:
        stage (Usd.Stage): Current USD stage.

    Returns:
        Dict[str, Dict[str, Any]]: Mapping from Mesh prim path to metadata including
            xform_path and rotate.
    """
    output_dict: Dict[str, Dict[str, Any]] = {}
    for prim in stage.Traverse():
        if prim.GetTypeName() == "Mesh":
            sem = Semantics.SemanticsAPI.Get(prim, "Semantics")
            if sem and sem.GetSemanticDataAttr().HasAuthoredValueOpinion():
                xform_prim = _find_parent_xform(prim)
                if xform_prim:
                    rotate = _get_xform_rotation(xform_prim)
                    output_dict[str(prim.GetPath())] = {
                        "xform_path": str(xform_prim.GetPath()),
                        "rotate": to_serializable(rotate) if rotate is not None else None,
                    }
                else:
                    output_dict[str(prim.GetPath())] = {
                        "xform_path": None,
                        "rotate": None,
                    }
    return output_dict

def resolve_output_path(user_path: Optional[str] = None) -> str:
    """
    Resolve the output path using priority: user argument > USER_OUTPUT_PATH.
    If the resolved path is a directory (or looks like one), default file name
    'output_xform.json' will be appended.

    Args:
        user_path (Optional[str]): Explicit path passed into run().

    Returns:
        str: Resolved output path.

    Raises:
        ValueError: If no output path is provided by any means.
    """
    path = None
    if user_path and user_path.strip():
        path = user_path.strip()
    elif USER_OUTPUT_PATH and USER_OUTPUT_PATH.strip():
        path = USER_OUTPUT_PATH.strip()
    else:
        raise ValueError(
            "No output path provided. Set USER_OUTPUT_PATH in the script or pass a path to run()."
        )

    path = os.path.expanduser(path)
    if os.path.isdir(path):
        return os.path.join(path, "output_xform.json")
    if path.endswith(os.sep):
        return os.path.join(path, "output_xform.json")
    root, ext = os.path.splitext(path)
    if ext.lower() == ".json":
        return path
    return os.path.join(path, "output_xform.json")

def run(output_path: Optional[str] = None) -> None:
    """
    Entry point to collect semantics and write them to JSON.

    Args:
        output_path (Optional[str]): Destination JSON path. If None, resolved via
            USER_OUTPUT_PATH.

    Returns:
        None
    """
    stage = omni.usd.get_context().get_stage()
    data = collect_semantics(stage)
    resolved = resolve_output_path(output_path)
    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    with open(resolved, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Xform JSON written to: {resolved}")


if __name__ == "__main__":
    run()


