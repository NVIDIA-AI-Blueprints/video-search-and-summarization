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
import logging
import os
import shutil
from unittest import mock

import osmnx as ox
from PIL import Image
import unittest

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.utils.crs import CoordinateReferenceSystem as crs
from mdx.analytics.core.utils.crs import RoadNetworkGraph
from mdx.analytics.core.utils.io_utils import load_json_from_file, validate_file_path

CONFIG_PATH = "tests/unit/resources/smart_city_config_test.json"
# Pinned road-network graphs. The live Overpass API made these tests fail on unrelated PRs
# (rate-limited/timed out -> graph None -> confusing downstream errors); a saved graph makes
# them deterministic and ~60s faster.
OSM_GRAPH_DIR = "tests/unit/resources/osm_graphs/"
ROUTE_LATLON = [
    (42.491617, -90.720460),
    (42.491007, -90.720042),
    (42.491042, -90.718846),
    (42.490815, -90.716531),
]


class _CrsNetworkTestBase(unittest.TestCase):
    """Shared fixtures. CRS/OSM graph construction is deferred to setUpClass so
    pytest --collect-only does not download road networks at import time.
    """

    suffix = ""
    needs_route_xy = False
    needs_route_xy_customize = False
    # The osmnx entry point this class's osmLoadMethod ends up calling, and the pinned graph the
    # mock returns in its place. Patching at the osmnx boundary rather than pointing the config at
    # ``from_file`` is what keeps _graph_from_point / _graph_from_place / _graph_from_polygon --
    # the code these classes exist to cover -- inside the path under test.
    osm_loader = ""
    osm_graph_file = ""

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        raise NotImplementedError

    @classmethod
    def setUpClass(cls):
        if cls is _CrsNetworkTestBase:
            return
        valid_config_path = validate_file_path(CONFIG_PATH)
        if not os.path.exists(valid_config_path):
            logging.error(
                f"ERROR: The indicated config file `{valid_config_path}` does NOT exist."
            )
            raise FileNotFoundError(valid_config_path)

        config = AppConfig(**load_json_from_file(valid_config_path))
        config.coordinateReferenceSystem.roadNetwork.visualization.visualizationGraphShowGraph = False
        config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistMeters = 100
        config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingMaxDistInitMeters = 25
        config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseMeters = 50
        config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters = 75
        config.coordinateReferenceSystem.roadNetwork.mapMatching.mapMatchingDistNoiseMeters = 50
        cls.configure_crs(config)

        cls.config = config
        cls.route_latlon = ROUTE_LATLON
        # Heavy OSM/network work — only when the class's tests actually run.
        #
        # The graph is built during construction, so the loader has to be mocked around this call
        # rather than in setUp. Serving a pinned graph keeps the result identical from run to run:
        # the live Overpass API rate-limited and timed out on unrelated pull requests, and
        # _create_graph_from_osm turns any such failure into a warning and a None graph, so the
        # symptom appeared far from the cause.
        pinned = ox.load_graphml(OSM_GRAPH_DIR + cls.osm_graph_file)
        with mock.patch(f"mdx.analytics.core.utils.crs.ox.{cls.osm_loader}", return_value=pinned):
            cls.crs_mdx = crs(config.coordinateReferenceSystem)
        if cls.needs_route_xy:
            cls.route_xy = cls.crs_mdx.trajectory_latlon_to_xy(cls.route_latlon)
        if cls.needs_route_xy_customize:
            cls.route_xy_customzie = cls.crs_mdx.trajectory_crs_cartesian_to_custom_xy(cls.route_xy)

    def setUp(self):
        self.output_path = "tests/unit/outputs/"
        os.makedirs(self.output_path, exist_ok=True)

        # draw_map renders a basemap through smopy, which downloads tile images from a public tile
        # server -- a second network dependency, separate from the road-network graph, and the one
        # that surfaced as "LinAlgError: Singular matrix" when the download failed. Serve a blank
        # tile instead: these tests assert that a figure is produced, never what it looks like.
        tile_patch = mock.patch(
            "smopy.fetch_tile", side_effect=lambda *a, **k: Image.new("RGB", (256, 256), (128, 128, 128)))
        tile_patch.start()
        self.addCleanup(tile_patch.stop)

    def tearDown(self):
        if os.path.exists(self.output_path):
            for file in os.listdir(self.output_path):
                file_path = os.path.join(self.output_path, file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f"Error: {e}")
            os.rmdir(self.output_path)


class TestNetworkLatLonFromPointInputLatLon(_CrsNetworkTestBase):
    suffix = "NetworkLatLonFromPointInputLatLon"
    osm_loader = "graph_from_point"
    osm_graph_file = "from_point_drive.graphml.gz"

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_point"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(
            self.route_latlon, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )


class TestNetworkLatLonFromPlaceInputLatLon(_CrsNetworkTestBase):
    suffix = "NetworkLatLonFromPlaceInputLatLon"
    osm_loader = "graph_from_place"
    osm_graph_file = "from_place_drive.graphml.gz"

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_place"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(
            self.route_latlon, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )


class TestNetworkLatLonFromPolygonInputLatLon(_CrsNetworkTestBase):
    suffix = "NetworkLatLonFromPolygonInputLatLon"
    osm_loader = "graph_from_polygon"
    osm_graph_file = "from_polygon_drive.graphml.gz"

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(
            self.route_latlon, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )

    def test_kml_writer(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        list_of_trajectory_latlon = [self.route_latlon, route_latlon_map_matched]
        list_of_line_color = ["ffff0000", "ff00ff00"]
        self.crs_mdx.write_list_of_trajectory_latlon_to_kml(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_routes.kml"),
            list_of_trajectory_latlon,
            list_of_line_color=list_of_line_color,
        )


class TestNetworkLatLonFromPolygonInputCartesian(_CrsNetworkTestBase):
    suffix = "NetworkLatLonFromPolygonInputCartesian"
    osm_loader = "graph_from_polygon"
    osm_graph_file = "from_polygon_drive.graphml.gz"
    needs_route_xy = True

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = True

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_xy_map_matched = self.crs_mdx.map_matching(self.route_xy)
        self.assertEqual(len(route_xy_map_matched), 11)

        route_xy_map_matched = self.crs_mdx.map_matching(
            self.route_xy, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_xy_map_matched), len(self.route_xy))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_xy)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )


class TestNetworkLatLonFromPolygonSimplifyInputLatLon(_CrsNetworkTestBase):
    suffix = "NetworkLatLonFromPolygonSimplifyInputLatLon"
    osm_loader = "graph_from_polygon"
    osm_graph_file = "from_polygon_drive_simplify.graphml.gz"

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = True
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = False
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 4)

        route_latlon_map_matched = self.crs_mdx.map_matching(
            self.route_latlon, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )

    def test_kml_writer(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        list_of_trajectory_latlon = [self.route_latlon, route_latlon_map_matched]
        list_of_line_color = ["ffff0000", "ff00ff00"]
        self.crs_mdx.write_list_of_trajectory_latlon_to_kml(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_routes.kml"),
            list_of_trajectory_latlon,
            list_of_line_color=list_of_line_color,
        )


class TestNetwork26915FromPolygonInputLatLon(_CrsNetworkTestBase):
    suffix = "Network26915FromPolygonInputLatLon"
    osm_loader = "graph_from_polygon"
    osm_graph_file = "from_polygon_drive.graphml.gz"

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(
            self.route_latlon, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )


class TestNetwork26915FromPolygonInputCartesian(_CrsNetworkTestBase):
    suffix = "Network26915FromPolygonInputCartesian"
    osm_loader = "graph_from_polygon"
    osm_graph_file = "from_polygon_drive.graphml.gz"
    needs_route_xy = True

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = True

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_xy_map_matched = self.crs_mdx.map_matching(self.route_xy)
        self.assertEqual(len(route_xy_map_matched), 11)

        route_xy_map_matched = self.crs_mdx.map_matching(
            self.route_xy, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_xy_map_matched), len(self.route_xy))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_xy)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )


class TestNetwork26915FromPolygonInputCartesianCustomize(_CrsNetworkTestBase):
    suffix = "Network26915FromPolygonInputCartesianCustomize"
    osm_loader = "graph_from_polygon"
    osm_graph_file = "from_polygon_drive.graphml.gz"
    needs_route_xy = True
    needs_route_xy_customize = True

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
        config.coordinateReferenceSystem.crsCartesian = "EPSG:26915"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = True
        config.coordinateReferenceSystem.crsCartesianCustomOrigin.enable = True

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_xy_customzie_map_matched = self.crs_mdx.map_matching(self.route_xy_customzie)
        self.assertEqual(len(route_xy_customzie_map_matched), 11)

        route_xy_customzie_map_matched = self.crs_mdx.map_matching(
            self.route_xy_customzie,
            exclude_non_emitting_state=True,
        )
        self.assertEqual(len(route_xy_customzie_map_matched), len(self.route_xy_customzie))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_xy_customzie)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )


class TestNetwork3395FromPolygonInputLatLon(_CrsNetworkTestBase):
    suffix = "Network3395FromPolygonInputLatLon"
    osm_loader = "graph_from_polygon"
    osm_graph_file = "from_polygon_drive.graphml.gz"

    @classmethod
    def configure_crs(cls, config: AppConfig) -> None:
        config.coordinateReferenceSystem.roadNetwork.graph.osmLoadMethod = "from_polygon"
        config.coordinateReferenceSystem.roadNetwork.graph.osmSimplify = False
        config.coordinateReferenceSystem.roadNetwork.roadNetworkUseCRSCartesian = True
        config.coordinateReferenceSystem.crsCartesian = "EPSG:3395"
        config.coordinateReferenceSystem.inputDataInCRSCartesian = False

    def test_draw_graph(self):
        self.crs_mdx.road_network.draw_graph(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_graph.png")
        )

    def test_map_matching(self):
        route_latlon_map_matched = self.crs_mdx.map_matching(self.route_latlon)
        self.assertEqual(len(route_latlon_map_matched), 11)

        route_latlon_map_matched = self.crs_mdx.map_matching(
            self.route_latlon, exclude_non_emitting_state=True
        )
        self.assertEqual(len(route_latlon_map_matched), len(self.route_latlon))

    def test_draw_map(self):
        _ = self.crs_mdx.map_matching(self.route_latlon)
        self.crs_mdx.road_network.draw_map(
            os.path.join(self.output_path, f"TestCRS_{self.suffix}_draw_map.png")
        )


class TestGraphFromFile(unittest.TestCase):
    """``from_file`` reads a saved graph instead of querying Overpass.

    The other classes mock osmnx and so never take this branch; it needs its own coverage because
    it is the one load method whose failure is a configuration error rather than a service outage.
    """

    def _road_network(self, path: str):
        config = AppConfig(**load_json_from_file(validate_file_path(CONFIG_PATH)))
        graph_config = config.coordinateReferenceSystem.roadNetwork.graph
        graph_config.osmLoadMethod = "from_file"
        graph_config.osmQueryFile = path

        return RoadNetworkGraph(graph_config)

    def test_loads_a_pinned_graph(self):
        road_network = self._road_network(OSM_GRAPH_DIR + "from_polygon_drive.graphml.gz")

        self.assertIsNotNone(road_network.graph)
        self.assertGreater(len(road_network.graph.nodes), 0)

    def test_a_missing_graph_raises(self):
        """Not swallowed like an Overpass outage: a bad path fails the same way on every retry, and
        degrading to an empty graph is what made the original failures hard to place."""
        with self.assertRaises(Exception):
            self._road_network(OSM_GRAPH_DIR + "does_not_exist.graphml.gz")


if __name__ == "__main__":
    unittest.main()
