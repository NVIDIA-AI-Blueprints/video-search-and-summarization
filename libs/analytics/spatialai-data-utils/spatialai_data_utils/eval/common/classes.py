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
MTMC Object Class Definitions Module

This module defines object classes and class mapping hierarchies for MTMC
(Multi-Target Multi-Camera) tracking evaluation in warehouse and industrial
environments. It provides standardized class lists and mappings between
sub-classes and primary classes.

Key Features:
- Comprehensive object class taxonomy for warehouse/industrial settings
- Sub-class to primary class mapping
- Support for various robot platforms (humanoid, AMR, etc.)
- Container and vehicle classifications
- Hierarchical class structure

Primary Classes:
- Person: Human workers
- NovaCarter: NVIDIA Nova Carter robot platform
- Transporter: Transport vehicles
- Forklift: Forklift vehicles
- Box: Various box types (cardboard, flat, multi-depth, etc.)
- Pallet: Pallet types (export, block, standard)
- Crate: Crate containers (wooden, plastic)
- Basket: Basket containers
- KLTBin: KLT storage bins
- Cone: Traffic/safety cones
- Rack: Storage racks and AMR racks
- Humanoid Robots: Fourier GR1 T2, Agility Digit
- AMR Racks: Fii AMR Bianca, Fii AMR HGX

Sub-Class Mapping:
The module provides a comprehensive dictionary mapping specific sub-class
variants to their primary classes. This allows fine-grained detection while
maintaining compatibility with standard evaluation metrics.

Use Cases:
- Define evaluation class sets for MTMC tracking
- Map model predictions to standard classes
- Handle hierarchical object taxonomies
- Support multi-variant object detection
- Generate class-specific metrics

Integration:
- Used by evaluation scripts for class filtering
- Referenced in detection and tracking pipelines
- Supports both coarse and fine-grained classification
- Compatible with warehouse automation systems

Example:
    >>> map_sub_class_to_primary_class['cardbox']
    'Box'
    >>> map_sub_class_to_primary_class['gr1_t2']
    'Fourier_GR1_T2_Humanoid'
"""

CLASS_LIST = [
    "Person",
    "NovaCarter",
    "Transporter",
    "Forklift",
    "Box",
    "Pallet",
    "Crate",
    "Basket",
    "KLTBin",
    "Cone",
    "Rack",
    "Fourier_GR1_T2_Humanoid",
    "Agility_Digit_Humanoid",
    "Fii_AMR_Bianca_Rack",
    "Fii_AMR_HGX_Rack",
]

map_sub_class_to_primary_class = {
    "person": "Person",
    "human": "Person",
    "transporter": "Transporter",
    "nova_carter": "NovaCarter",
    "novacarter": "NovaCarter",
    "forklift": "Forklift",
    "box": "Box",
    "cardbox": "Box",
    "flatbox": "Box",
    "multidepthbox": "Box",
    "printersbox": "Box",
    "cubebox": "Box",
    "whitecorrugatedbox": "Box",
    "longbox": "Box",
    "basket": "Basket",
    "exportpallet": "Pallet",
    "blockpallet": "Pallet",
    "pallet": "Pallet",
    "crate": "Crate",
    "woodencrate": "Crate",
    "klt_bin": "KLTBin",
    "cone": "Cone",
    "rack": "Rack",
    "fourier_gr1_t2_humanoid": "Fourier_GR1_T2_Humanoid",
    "gr1_t2": "Fourier_GR1_T2_Humanoid",
    "agility_digit_humanoid": "Agility_Digit_Humanoid",
    "agility_digit": "Agility_Digit_Humanoid",
    "fii_amr_bianca_rack": "Fii_AMR_Bianca_Rack",
    "fii_amr_hgx_rack": "Fii_AMR_HGX_Rack",
}
