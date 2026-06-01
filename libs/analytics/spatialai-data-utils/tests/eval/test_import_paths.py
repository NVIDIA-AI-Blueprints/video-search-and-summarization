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

"""Smoke tests pinning the canonical import paths exposed by the
evaluation modules."""


# ===================================================================
# Module reorganization: import path tests
# ===================================================================
class TestModuleImportPaths:
    """Verify that canonical import paths work."""

    def test_canonical_common_utils(self):
        """Canonical eval.common.* paths import successfully."""
        from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
        from spatialai_data_utils.utils.filesystem_utils import validate_file_path
        from spatialai_data_utils.loaders.calibration import fetch_fps_from_calibration
        from spatialai_data_utils.eval.common.classes import CLASS_LIST, map_sub_class_to_primary_class
        assert len(CLASS_LIST) > 0
        assert isinstance(map_sub_class_to_primary_class, dict)
        assert callable(validate_file_path)
        assert callable(fetch_fps_from_calibration)
        assert callable(split_files_by_sensor)

    def test_canonical_hota_path(self):
        """Canonical eval.tracking.hota.* paths import successfully."""
        from spatialai_data_utils.eval.tracking.hota.hota_eval import evaluate_hota
        from spatialai_data_utils.eval.tracking.hota.evaluate import Evaluator
        from spatialai_data_utils.eval.tracking.hota.metrics.hota import HOTA
        from spatialai_data_utils.eval.tracking.hota.datasets._base_dataset import _BaseDataset
        assert callable(evaluate_hota)
        assert Evaluator is not None
        assert HOTA is not None
        assert _BaseDataset is not None

    def test_canonical_loaders_calibration(self):
        """Calibration functions are importable from loaders.calibration."""
        from spatialai_data_utils.loaders.calibration import (
            get_camera_name_to_bev_name_map,
            fetch_fps_from_calibration,
        )
        assert callable(get_camera_name_to_bev_name_map)
        assert callable(fetch_fps_from_calibration)
