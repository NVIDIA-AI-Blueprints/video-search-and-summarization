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

import time
import re
from collections import defaultdict
from typing import Any
from pxr import Semantics, UsdGeom, Usd

def enable_semantics(prim: Usd.Prim, semantic_label: str, semantic_class: str) -> None:
    """Enable semantics for the given prim.

    Args:
        prim: Target USD prim.
        semantic_label: Value for the 'data' field.
        semantic_class: Value for the 'type' field.

    Returns:
        None
    """
    if not prim.HasAPI(Semantics.SemanticsAPI):
        sem = Semantics.SemanticsAPI.Apply(prim, "Semantics")
        sem.CreateSemanticTypeAttr()
        sem.CreateSemanticDataAttr()
    else:
        sem = Semantics.SemanticsAPI.Get(prim, "Semantics")
    
    sem.GetSemanticTypeAttr().Set(semantic_class)
    sem.GetSemanticDataAttr().Set(semantic_label)

def get_stage() -> Any:
    """Get the current USD stage.

    Returns:
        Any: The current USD stage object from omni.usd.
    """
    return omni.usd.get_context().get_stage()

def is_prim_visible(prim: Usd.Prim) -> bool:
    """Recursively check if the given prim or any of its parents is visible.

    Args:
        prim: USD prim to check.

    Returns:
        bool: True if visible (no ancestor is marked invisible), else False.
    """
    while prim:
        visibility = UsdGeom.Imageable(prim).GetVisibilityAttr().Get()
        
        if visibility == "invisible":
            return False  # If any parent is invisible, the object is considered invisible

        prim = prim.GetParent()  # Move to the parent prim
    
    return True  # If no ancestor is invisible, the object is visible

# Define regex patterns for categorization
patterns = {
    'printersbox': re.compile(r'printersbox.*body', re.IGNORECASE),
    'flatbox': re.compile(r'flatbox.*body', re.IGNORECASE),
    'officepaperbox': re.compile(r'officepaperbox.*_box_\d+', re.IGNORECASE),
    'cardbox': None,  # No regex, direct match
    'largecardboardbox': re.compile(r'largecardboardboxe.*/merged$', re.IGNORECASE),
    'cubebox': re.compile(r'cubebox.*body', re.IGNORECASE),
    'whitecorrugatedbox': re.compile(r'whitecorrugatedbox.*body', re.IGNORECASE),
    'multidepthbox': re.compile(r'multidepthbox.*body', re.IGNORECASE),
    'longbox': re.compile(r'longbox.*body', re.IGNORECASE),
    'woodencrate': None
}

excluded_pattern = re.compile(r"heavydutypackingtable_\w+")
# allowed_pattern = re.compile(r"crate_\w+")

# Initialize storage for categorized boxes
categorized_boxes = defaultdict(list)
hidden_boxes = []

# Traverse all prims in the stage
for prim in get_stage().Traverse():
    if prim.GetTypeName() == "Mesh":
        prim_path_str = str(prim.GetPath()).lower()
        if 'box' in prim_path_str:  # Check for "box" in the path
            categorized = False
            for category, pattern in patterns.items():
                if category in prim_path_str:
                    categorized_boxes[category].append(str(prim.GetPath()))
                    # enable_semantics(prim, category, "class")
                    categorized = True
                    break
                
            if not categorized:
                categorized_boxes['other'].append(str(prim.GetPath()))
        
        if 'crate' in prim_path_str  and 'woodencrate' not in prim_path_str and 'box' not in prim_path_str and not excluded_pattern.search(prim_path_str):
            categorized_boxes['basket'].append(str(prim.GetPath()))
            # enable_semantics(prim, 'basket', "class")
        
print("\n📦 **Box Sanity Check Summary** 📦\n")
for category, paths in categorized_boxes.items():
    print(f"✅ {category}: {len(paths)} boxes")

# Write categorized boxes to the output file
output_file = '/path/to/your/output/box_check_full_warehouse.txt'
with open(output_file, "w") as f:
    # Write categorized results
    f.write("\n📦 **Box Sanity Check Summary (Only Visible Boxes)** 📦\n\n")
    for category, paths in categorized_boxes.items():
        f.write(f"✅ {category}: {len(paths)} boxes\n")
        for path in paths:
            f.write(f"  - {path}\n")
        f.write("\n")  # Add spacing between categories

    # Write hidden boxes
    if hidden_boxes:
        f.write(f"\n⚠️ **Hidden Boxes: {len(hidden_boxes)} hidden boxes** ⚠️\n")
        for path in hidden_boxes:
            f.write(f"❌ {path}\n")