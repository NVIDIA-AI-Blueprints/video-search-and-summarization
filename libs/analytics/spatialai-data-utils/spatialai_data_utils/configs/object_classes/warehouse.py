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

CLASS_LIST = [
    "person",
    "gr1_t2",
    "agility_digit",
    "nova_carter",
    "transporter",
    "forklift",
    "pallet_truck",
    # "box",
    # "pallet",
    # "crate",
    # "basket",
]

SUB_CLASS_DICT = {
    "pallet_truck": [
        "palletjackforklift",
        "pallettruck",
        "forklift_1195",
    ],
    # "pallet": [
    #     "pallet",
    #     "blockpallet",
    #     # "wooddrumpallet",
    #     # "rackablepallet",
    #     "exportpallet",
    # ],
}

MAP_CLASS_NAMES = {
    "person": "Person",
    "gr1_t2": "Fourier_GR1_T2_Humanoid",
    "agility_digit": "Agility_Digit_Humanoid",
    "nova_carter": "Nova_Carter",
    "transporter": "Transporter",
    "forklift": "Forklift",
    "pallet_truck": "Pallet_Truck",
    # "box": "Box",
    # "pallet": "Pallet",
    # "crate": "Crate",
    # "basket": "Basket",
}

ATTRIBUTE_DICT = {
    "person": "person.moving",
    "gr1_t2": "gr1_t2.moving",
    "agility_digit": "agility_digit.moving",
    "nova_carter": "nova_carter.moving",
    "transporter": "transporter.moving",
    "forklift": "forklift.moving",
    "pallet_truck": "pallet_truck.moving",
    # "box": "box.static",
    # "pallet": "pallet.static",
    # "crate": "crate.static",
    # "basket": "basket.static",
}

CLASS_RANGE_DICT = {
    "person": 40,
    "gr1_t2": 40,
    "agility_digit": 40,
    "nova_carter": 40,
    "transporter": 40,
    "forklift": 40,
    "pallet_truck": 40,
}
