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
import unittest
import logging
import os
import shutil
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.utils.io_utils import validate_file_path, load_json_from_file
from mdx.analytics.core.utils.crs import CoordinateReferenceSystem as crs


class TestNetworkJsonGeneration(unittest.TestCase):

    def setUp(self):
        # Create output directory if it doesn't exist
        self.output_path = 'tests/unit/outputs/'
        os.makedirs(self.output_path, exist_ok=True)

    def tearDown(self):
        # Clean up the output directory
        if os.path.exists(self.output_path):
            for file in os.listdir(self.output_path):
                file_path = os.path.join(self.output_path, file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f'Error: {e}')
            os.rmdir(self.output_path)

    suffix = 'NetworkJsonGeneration'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    line_segment_path = 'tests/unit/resources/line_segment_v0.3.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_place"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
    config.coordinateReferenceSystem.roadNetwork.segmentShiftDistanceMeters = 5

    crs_mdx = crs(config.coordinateReferenceSystem)

    def test_network_json_generation(self):
        network_json_path = os.path.join(self.output_path, f'TestCRS_{self.suffix}_network.json')
        network_png_path = os.path.join(self.output_path, f'TestCRS_{self.suffix}_network.png')
        network_kml_path = os.path.join(self.output_path, f'TestCRS_{self.suffix}_network.kml')

        self.crs_mdx.create_network_json_file(self.line_segment_path, network_json_path)
        self.crs_mdx.draw_network_json(network_json_path, network_png_path)
        self.crs_mdx.write_network_json_to_kml(network_json_path, network_kml_path)


if __name__ == '__main__':
    unittest.main()
