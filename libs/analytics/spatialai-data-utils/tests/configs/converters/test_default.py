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

"""Pin the converter default constants.

These constants drive Sparse4D / NVSchema post-processing defaults
across the toolkit; a silent change here would shift every downstream
converter's behaviour. Test purpose is to catch accidental edits, not
to document the constants.
"""

from spatialai_data_utils.configs.converters import default


def test_default_constants_have_expected_values():
    assert default.box_3d_conf_thresh == 0.8
    assert default.filter_by_bev_boundary is False
    assert default.filter_by_z3d is False
    assert default.set_z3d_to_zero is False


def test_conf_thresh_is_valid_probability():
    assert 0.0 <= default.box_3d_conf_thresh <= 1.0
