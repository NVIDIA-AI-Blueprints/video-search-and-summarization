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
AICity Challenge 2025 Object Class Configuration Loader Module

This module provides utilities for loading and processing object class configurations
from Python configuration files. It handles class lists, class mappings, and sub-class
hierarchies used in AICity Challenge 2025 object detection and tracking tasks.

Key Features:
- Load class configuration from Python config files
- Generate class ID to name mappings
- Build sub-class to main class hierarchies
- Support hierarchical class structures
- Dynamic configuration loading

Main Functions:
- load_class_config_from_file: Load complete class configuration from file

Configuration Structure:
The configuration file should define:
- CLASS_LIST: List of main object classes (ordered by class ID)
- SUB_CLASS_DICT: Dictionary mapping main classes to sub-class variants
- Other dataset-specific parameters

Generated Mappings:
- CLASS_MAPPING_DICT: {class_name: class_id}
  Maps each main class to its integer ID (0-indexed)
- MAP_SUB_CLASS_TO_CLASS_DICT: {sub_class_name: main_class_name}
  Maps each sub-class variant to its parent main class

Example Configuration:
```python
CLASS_LIST = ["person", "vehicle", "box"]

SUB_CLASS_DICT = {
    "person": ["worker", "visitor", "security"],
    "vehicle": ["forklift", "truck", "cart"],
    "box": ["small_box", "large_box", "crate"]
}
```

Generated Outputs:
```python
CLASS_MAPPING_DICT = {
    "person": 0,
    "vehicle": 1,
    "box": 2
}

MAP_SUB_CLASS_TO_CLASS_DICT = {
    "worker": "person",
    "visitor": "person",
    "security": "person",
    "forklift": "vehicle",
    "truck": "vehicle",
    ...
}
```

Use Cases:
- Load class definitions for detection models
- Map model predictions to standard class names
- Handle fine-grained sub-class predictions
- Generate evaluation configurations
- Create class-specific visualizations

Typical Workflow:
1. Create Python config file with CLASS_LIST and SUB_CLASS_DICT
2. Load configuration using load_class_config_from_file
3. Use CLASS_MAPPING_DICT for model training/inference
4. Use MAP_SUB_CLASS_TO_CLASS_DICT for prediction mapping
5. Apply configurations to detection and tracking pipelines

Integration:
This module integrates with:
- Detection models (class ID assignment)
- Evaluation metrics (class-specific performance)
- Data loaders (class filtering)
- Visualization tools (class-specific rendering)
"""

from spatialai_data_utils.loaders.object_classes import (  # noqa: F401
    load_class_config_from_file,
)
