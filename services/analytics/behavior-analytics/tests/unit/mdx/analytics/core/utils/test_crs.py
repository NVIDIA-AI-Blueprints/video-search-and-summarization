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


class TestNetworkLatLonFromPointInputLatLon(unittest.TestCase):

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

    suffix = 'NetworkLatLonFromPointInputLatLon'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_point"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon, exclude_non_emitting_state=True)
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))


class TestNetworkLatLonFromPlaceInputLatLon(unittest.TestCase):

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

    suffix = 'NetworkLatLonFromPlaceInputLatLon'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
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
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon, exclude_non_emitting_state=True)
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))


class TestNetworkLatLonFromPolygonInputLatLon(unittest.TestCase):

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

    suffix = 'NetworkLatLonFromPolygonInputLatLon'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon, exclude_non_emitting_state=True)
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))

    def test_kml_writer(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        list_of_trajectory_latlon = [self.route_latlon, route_latlon_map_matched]
        list_of_line_color = ['ffff0000', 'ff00ff00']
        self.crs_mdx.write_list_of_trajectory_latlon_to_kml(
            os.path.join(self.output_path, f'TestCRS_{self.suffix}_routes.kml'),
            list_of_trajectory_latlon,
            list_of_line_color=list_of_line_color
        )


class TestNetworkLatLonFromPolygonInputCartesian(unittest.TestCase):

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

    suffix = 'NetworkLatLonFromPolygonInputCartesian'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = True

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]
    route_xy = crs_mdx.trajectory_latlon_to_xy(route_latlon)

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_xy_map_matched = self.crs_mdx.map_matching(self.route_xy)
        self.assertEqual(len(route_xy_map_matched), 11)

        route_xy_map_matched = self.crs_mdx.map_matching(self.route_xy, exclude_non_emitting_state=True)
        self.assertEqual(len(route_xy_map_matched), len(self.route_xy))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_xy)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))


class TestNetworkLatLonFromPolygonSimplifyInputLatLon(unittest.TestCase):

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

    suffix = 'NetworkLatLonFromPolygonSimplifyInputLatLon'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = True
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 4)

        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon, exclude_non_emitting_state=True)
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))

    def test_kml_writer(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        list_of_trajectory_latlon = [self.route_latlon, route_latlon_map_matched]
        list_of_line_color = ['ffff0000', 'ff00ff00']
        self.crs_mdx.write_list_of_trajectory_latlon_to_kml(
            os.path.join(self.output_path, f'TestCRS_{self.suffix}_routes.kml'),
            list_of_trajectory_latlon,
            list_of_line_color=list_of_line_color
        )


class TestNetwork26915FromPolygonInputLatLon(unittest.TestCase):

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

    suffix = 'Network26915FromPolygonInputLatLon'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):

        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon, exclude_non_emitting_state=True)
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))


class TestNetwork26915FromPolygonInputCartesian(unittest.TestCase):

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

    suffix = 'Network26915FromPolygonInputCartesian'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = True

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]
    route_xy = crs_mdx.trajectory_latlon_to_xy(route_latlon)

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_xy_map_matched = self.crs_mdx.map_matching(self.route_xy)
        self.assertEqual(len(route_xy_map_matched), 11)

        route_xy_map_matched = self.crs_mdx.map_matching(self.route_xy, exclude_non_emitting_state=True)
        self.assertEqual(len(route_xy_map_matched), len(self.route_xy))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_xy)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))


class TestNetwork26915FromPolygonInputCartesianCustomize(unittest.TestCase):

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

    suffix = 'Network26915FromPolygonInputCartesianCustomize'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
    config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = True
    config.coordinateReferenceSystem.crsCartesianCustomOrigin.enable = True

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]
    route_xy = crs_mdx.trajectory_latlon_to_xy(route_latlon)
    route_xy_customzie = crs_mdx.trajectory_crs_cartesian_to_custom_xy(route_xy)

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_xy_customzie_map_matched = self.crs_mdx.map_matching(self.route_xy_customzie)
        self.assertEqual(len(route_xy_customzie_map_matched), 11)

        route_xy_customzie_map_matched = self.crs_mdx.map_matching(
            self.route_xy_customzie,
            exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_xy_customzie_map_matched), len(self.route_xy_customzie))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_xy_customzie)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))


class TestNetwork3395FromPolygonInputLatLon(unittest.TestCase):

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

    suffix = 'Network3395FromPolygonInputLatLon'
    config_path = 'tests/unit/resources/smart_city_config_test.json'
    valid_config_path = validate_file_path(config_path)
    if not os.path.exists(valid_config_path):
        logging.error(
            f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
        exit(1)
    config = AppConfig(**load_json_from_file(valid_config_path))
    config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
    # test case parameters
    config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
    config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
    config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
    config.coordinateReferenceSystem.crsCartesian = "EPSG:3395"
    config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
    config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50

    crs_mdx = crs(config.coordinateReferenceSystem)
    route_latlon = [(42.491617, -90.720460), (42.491007, -90.720042), (42.491042, -90.718846), (42.490815, -90.716531)]

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_graph.png'))

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon, exclude_non_emitting_state=True)
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(os.path.join(self.output_path, f'TestCRS_{self.suffix}_draw_map.png'))


if __name__ == '__main__':
    unittest.main()
