# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import json
import logging
import os
from typing import Any

import osmnx as ox
from leuvenmapmatching import visualization as mmviz
from leuvenmapmatching.map.inmem import InMemMap
from leuvenmapmatching.matcher.distance import DistanceMatcher, DistanceMatching
from lxml import etree
from networkx.classes.multidigraph import MultiDiGraph
from pykml.factory import GX_ElementMaker as GX
from pykml.factory import KML_ElementMaker as KML
from shapely.geometry import Polygon

from mdx.analytics.core.schema.config import (
    AppCoordinateReferenceSystemConfig,
    GraphConfig,
    RoadNetworkConfig,
)
from mdx.analytics.core.schema.models import Intersection, Location, Network, Segment
from mdx.analytics.core.utils.distance_util import bearing, direction, get_point_at_distance, lonlat_to_xy, xy_to_lonlat
from mdx.analytics.core.utils.io_utils import load_json_from_file, validate_file_path

logger = logging.getLogger(__name__)


class RoadNetworkMap(InMemMap):
    """
    A class for in-memory representation of road network maps.

    This class extends InMemMap to provide functionality for:
    1. Storing and managing road network data in memory
    2. Converting between coordinate systems (lat/lon and x/y)
    3. Indexing and searching points on the map
    4. Serializing map data to files

    :ivar str name: Map name (mandatory).
    :ivar bool use_latlon: Whether locations represent lat/lon pairs (True) or y/x coordinates (False).
    :ivar bool use_rtree: Whether to build an rtree index for fast location searching.
    :ivar bool index_edges: Whether to build an index for map edges instead of vertices.
    :ivar str crs_lonlat: Coordinate reference system for lat/lon coordinates.
    :ivar str crs_xy: Coordinate reference system for x/y coordinates.
    :ivar dict graph: Initial graph in format dict[label, tuple[tuple[y,x], list[neighbor]]].
    :ivar str dir: Directory for serialization (if provided, rtree index will be written immediately).
    :ivar bool deserializing: Internal flag indicating object is being built from file.

    Example::
        >>> map = RoadNetworkMap("my_map", use_latlon=True, use_rtree=True)
        >>> map.add_node(1, (37.7749, -122.4194))
        >>> map.add_edge(1, 2)
    """

    def __init__(
        self,
        name,
        use_latlon=True,
        use_rtree=False,
        index_edges=False,
        crs_lonlat=None,
        crs_xy=None,
        graph=None,
        linked_edges=None,
        dir=None,
        deserializing=False,
    ):
        """In-memory representation of a map.

        This is a simple database-like object to perform experiments with map matching.
        For production purposes it is recommended to use your own derived
        class (e.g. to connect to your database instance).

        This class supports:

        - Indexing using rtrees to allow for fast searching of points on the map.
          When using the rtree index, only integer numbers are allowed as node labels.
        - Serializing to write and read from files.
        - Projecting points to a different frame (e.g. GPS to Lambert)

        :param name: Map name (mandatory)
        :param use_latlon: The locations represent latitude-longitude pairs, otherwise y-x coordinates
            are assumed.
        :param use_rtree: Build an rtree index to quickly search for locations.
        :param index_edges: Build an index for the edges in the map instead of the vertices.
        :param crs_lonlat: Coordinate reference system for the latitude-longitude coordinates.
        :param crs_xy: Coordiante reference system for the y-x coordinates.
        :param graph: Initial graph of form dict[label, tuple[tuple[y,x], list[neighbor]]]]
        :param dir: Directory where to serialize to. If given, the rtree index structure will be written
            to a file immediately.
        :param deserializing: Internal variable to indicate that the object is being build from a file.
        """
        super().__init__(
            name=name,
            use_latlon=use_latlon,
            use_rtree=use_rtree,
            index_edges=index_edges,
            crs_lonlat=crs_lonlat,
            crs_xy=crs_xy,
            graph=graph,
            linked_edges=linked_edges,
            dir=dir,
            deserializing=deserializing,
        )

        self.lonlat2xy = lonlat_to_xy
        self.xy2lonlat = xy_to_lonlat


class RoadNetworkGraph:
    """
    A class for creating and managing road network graphs from OpenStreetMap data.

    This class provides functionality to:
    1. Load road network data from OSM using various methods:
    - Point-based: Load network around a specific point
    - Polygon-based: Load network within a polygon area
    - Place-based: Load network for a named place
    - File-based: Load network from a local OSM file
    2. Support different network types (all, bike, drive, walk, etc.)
    3. Handle coordinate transformations
    4. Simplify and process network graphs

    :ivar GraphConfig config: Configuration object containing OSM loading parameters
    :ivar MultiDiGraph | None graph: The road network graph created from OSM data

    Example::
        >>> config = GraphConfig(
        ...     graphFromOSM=True,
        ...     osmLoadMethod="from_point",
        ...     osmType="drive",
        ...     osmQueryPoint=Location(lat=37.7749, lon=-122.4194)
        ... )
        >>> graph = RoadNetworkGraph(config)
        >>> network = graph.graph  # Get the NetworkX graph
    """

    def __init__(self, config: GraphConfig = None) -> None:
        """
        Initialize the RoadNetworkGraph with configuration parameters.

        :param GraphConfig config: Configuration object containing OSM loading parameters
        """
        self.config = config
        self._import_parameters_from_config(config)
        self.graph = self._create_graph_from_osm()

    def _import_parameters_from_config(self, config: GraphConfig = None) -> None:
        """
        Import parameters from the configuration object.

        :param GraphConfig config: Configuration object containing OSM loading parameters
        """
        self.load_methods_supported = ["from_point", "from_polygon", "from_place", "from_file"]
        self.network_types_supported = ["all", "all_public", "bike", "drive", "drive_service", "walk"]

        self.network_type_osmnx_to_pyrosm = {
            "all": "all",
            "all_public": "all",
            "bike": "cycling",
            "drive": "driving",
            "drive_service": "driving+service",
            "walk": "walking",
        }

        if config:
            self.graph_from_osm = config.graphFromOSM
            self.osm_load_method = config.osmLoadMethod
            self.osm_type = config.osmType
            self.osm_simplify = config.osmSimplify
            self.osm_query_point = (config.osmQueryPoint.lat, config.osmQueryPoint.lon)
            self.osm_query_point_dist_meters = config.osmQueryPointDistMeters
            self.osm_query_polygon = Polygon([(point.lon, point.lat) for point in config.osmQueryPolygon])
            self.osm_query_place = config.osmQueryPlace
            self.osm_query_file = config.osmQueryFile

    def _create_graph_from_osm(self) -> MultiDiGraph | None:
        """
        Create a road network graph from OSM data based on configuration.

        :return MultiDiGraph | None: NetworkX MultiDiGraph containing the road network, or None if graph creation is disabled
        """
        graph = None
        if not self.graph_from_osm:
            return None
        else:
            try:
                if self.osm_load_method == "from_point":
                    graph = self._graph_from_point(self.osm_query_point)
                if self.osm_load_method == "from_polygon":
                    graph = self._graph_from_polygon(self.osm_query_polygon)
                if self.osm_load_method == "from_place":
                    graph = self._graph_from_place(self.osm_query_place)
            except Exception as e:
                logger.warning(f"An exception occurred when pulling content from openStreetMap: {e}")

        return graph

    def _graph_from_point(self, point: tuple[float, float]) -> MultiDiGraph:
        """
        Create a road network graph from OSM data around a specific point.

        :param tuple[float, float] point: (latitude, longitude) coordinates
        :return MultiDiGraph: NetworkX MultiDiGraph containing the road network
        """
        logger.info(f"Creating road network with type={self.osm_type} from OSM by the given point: {point}..")
        graph = ox.graph_from_point(
            point, network_type=self.osm_type, dist=self.osm_query_point_dist_meters, simplify=self.osm_simplify
        )
        logger.info("Road network created.")
        return graph

    def _graph_from_polygon(self, polygon: Polygon) -> MultiDiGraph:
        """
        Create a road network graph from OSM data within a polygon.

        :param Polygon polygon: Shapely polygon defining the area
        :return MultiDiGraph: NetworkX MultiDiGraph containing the road network
        """
        logger.info(f"Creating road network with type={self.osm_type} from OSM by the given polygon: {polygon}..")
        graph = ox.graph_from_polygon(polygon, network_type=self.osm_type, simplify=self.osm_simplify)
        logger.info("Road network created.")
        return graph

    def _graph_from_place(self, place: str) -> MultiDiGraph:
        """
        Create a road network graph from OSM data for a named place.

        :param str place: Name of the place (e.g., city, neighborhood)
        :return MultiDiGraph: NetworkX MultiDiGraph containing the road network
        """
        # print(f'Creating road network with type={self.osm_type} from OSM by the given place: {place}..')
        logger.info(f"Creating road network with type={self.osm_type} from OSM by the given place: {place}..")
        graph = ox.graph_from_place(place, network_type=self.osm_type, simplify=self.osm_simplify)
        logger.info("Road network created.")
        return graph


class RoadNetwork:
    """
    A class for managing road networks and performing map matching.

    This class provides functionality to:
    1. Create and manage road network graphs
    2. Perform map matching of trajectories to road networks
    3. Handle coordinate transformations between different systems
    4. Visualize road networks and matched trajectories
    5. Export network data to various formats

    :ivar RoadNetworkConfig config: Configuration object containing road network parameters
    :ivar str road_network_crs_latlon: Coordinate reference system for lat/lon coordinates
    :ivar str road_network_crs_xy: Coordinate reference system for cartesian coordinates
    :ivar MultiDiGraph | None graph: The road network graph
    :ivar RoadNetworkMap map: The road network map for matching
    :ivar DistanceMatcher | None matcher: The distance matcher for map matching

    Example::
        >>> config = RoadNetworkConfig(
        ...     enable=True,
        ...     roadNetworkUseCRSCartesian=True,
        ...     mapMatching=MapMatchingConfig(
        ...         mapMatchingMaxDistMeters=50,
        ...         mapMatchingMinProbNorm=0.001
        ...     )
        ... )
        >>> network = RoadNetwork(config)
        >>> trajectory = [(37.7749, -122.4194), (37.7750, -122.4195)]
        >>> matched = network.map_matching(trajectory)
    """

    def __init__(
        self, config: RoadNetworkConfig = None, crsLatLon: str = "EPSG:4326", crsCartesian: str = "EPSG:26915"
    ) -> None:
        """
        Initialize the RoadNetwork with configuration parameters.

        :param RoadNetworkConfig config: Configuration object containing road network parameters
        :param str crsLatLon: Coordinate reference system for lat/lon coordinates
        :param str crsCartesian: Coordinate reference system for cartesian coordinates
        """
        self.config = config
        self.road_network_crs_latlon = crsLatLon
        self.road_network_crs_xy = crsCartesian
        self._import_parameters_from_config(config)

        self.graph = None
        if self.enable:
            road_network_graph = RoadNetworkGraph(config.graph)
            self.graph = road_network_graph.graph

        self.map = self._create_map(self.graph)
        self.matcher = None

        if self.enable and self.graph:
            self.matcher = self._create_matcher(self.map)

    def _import_parameters_from_config(self, config: RoadNetworkConfig) -> None:
        """
        Import parameters from the road network configuration.

        This method extracts configuration parameters from the provided RoadNetworkConfig
        object and sets them as instance variables. It handles parameters for:
        - Road network enablement
        - Coordinate system settings
        - Map matching parameters
        - Visualization settings

        :param RoadNetworkConfig config: Configuration object containing road network parameters
        """
        if config:

            self.enable = config.enable
            self.use_crs_cartesian = config.roadNetworkUseCRSCartesian

            self.map_matching_max_dist_meters = config.mapMatching.mapMatchingMaxDistMeters
            self.map_matching_max_dist_init_meters = config.mapMatching.mapMatchingMaxDistInitMeters
            self.map_matching_min_prob_norm = config.mapMatching.mapMatchingMinProbNorm
            self.map_matching_non_emitting_length_factor = config.mapMatching.mapMatchingNonEmittingLengthFactor
            self.map_matching_obs_noise_meters = config.mapMatching.mapMatchingObsNoiseMeters
            self.map_matching_obs_noise_ne_meters = config.mapMatching.mapMatchingObsNoiseNonEmittingStatesMeters
            self.map_matching_dist_noise_meters = config.mapMatching.mapMatchingDistNoiseMeters
            self.map_matching_non_emitting_states = config.mapMatching.mapMatchingNonEmittingStates
            self.map_matching_max_lattice_width = config.mapMatching.mapMatchingMaxLatticeWidth

            self.visualize_graph_node_color = config.visualization.visualizationGraphNodeColor
            self.visualize_graph_show_graph = config.visualization.visualizationGraphShowGraph
            self.visualize_map_use_background = config.visualization.visualizationMapUseBackground
            self.visualize_map_zoom_path = config.visualization.visualizationMapZoomPath
            self.visualize_map_show_labels = config.visualization.visualizationMapShowLabels
            self.visualize_map_show_matching = config.visualization.visualizationMapShowMatching

    def map_matching(
        self, trajectory: list[tuple[float, float]], exclude_non_emitting_state: bool = False
    ) -> list[Any]:
        """
        Perform map matching on a trajectory.

        This method matches a trajectory to the road network, handling both
        cartesian and geographic coordinate systems. It supports custom
        coordinate transformations and different matching strategies.

        :param list[tuple[float, float]] trajectory: List of (x,y) or (lat,lon) coordinates
        :param bool exclude_non_emitting_state: Whether to exclude non-emitting states
        :return list[Any]: List of matched points

        Example::
            >>> trajectory = [(37.7749, -122.4194), (37.7750, -122.4195)]
            >>> matched = network.map_matching(trajectory)
            >>> print(f"Matched {len(matched)} points")
        """
        trajectory_map_matched = []
        if self.matcher:
            trajectory_map_matched = []
            states, _ = self.matcher.match(trajectory)
            lattice_selected = self.matcher.lattice_best
            for idx, m in enumerate(lattice_selected):
                coordinate_1, coordinate_2 = m.edge_m.pi[:2]
                if exclude_non_emitting_state:
                    if m.edge_o.is_point():
                        trajectory_map_matched.append((coordinate_1, coordinate_2))
                else:
                    trajectory_map_matched.append((coordinate_1, coordinate_2))
            # """Total distance of the observations."""
            # distance_trajectory = self.matcher.path_distance()
            # """Total distance of the matched path."""
            # distance_trajectory_map_matched = self.matcher.path_pred_distance()

        return trajectory_map_matched

    def map_matching_for_edge(
        self, pointsGeo: list[Location], exclude_non_emitting_state: bool = False, direction_mode: int = 0
    ) -> tuple[list[Segment], list[Location], list[DistanceMatching]]:
        """
        Perform map matching on a trajectory with edge information.

        :param list[Location] pointsGeo: List of geographic points
        :param bool exclude_non_emitting_state: Whether to exclude non-emitting states
        :param int direction_mode: Mode for direction handling
        :return tuple[list[Segment], list[Location], list[DistanceMatching]]: Tuple of
            (matched segments, matched locations, distance matching results)
        """
        edges_map_matched = []
        lattice_selected = []
        edges_ids_unique = []
        trajectory_map_matched = []

        trajectory = [(loc.lat, loc.lon) for loc in pointsGeo]
        if self.use_crs_cartesian:
            trajectory = [self.map.latlon2yx(loc.lat, loc.lon) for loc in pointsGeo]

        if self.matcher:
            states, _ = self.matcher.match(trajectory)
            lattice_selected = self.matcher.lattice_best
            for idx, m in enumerate(lattice_selected):

                coordinate_1, coordinate_2 = m.edge_m.pi[:2]
                if exclude_non_emitting_state:
                    if m.edge_o.is_point():
                        trajectory_map_matched.append((coordinate_1, coordinate_2))
                else:
                    trajectory_map_matched.append((coordinate_1, coordinate_2))

                edge_id = m.edge_m.label
                if edge_id not in edges_ids_unique:
                    p1_coord1 = m.edge_m.p1[0]
                    p1_coord2 = m.edge_m.p1[1]
                    p2_coord1 = m.edge_m.p2[0]
                    p2_coord2 = m.edge_m.p2[1]
                    if self.use_crs_cartesian:
                        p1_coord1, p1_coord2 = self.map.yx2latlon(m.edge_m.p1[0], m.edge_m.p1[1])
                        p2_coord1, p2_coord2 = self.map.yx2latlon(m.edge_m.p2[0], m.edge_m.p2[1])
                    loc1 = Location(lat=p1_coord1, lon=p1_coord2)
                    loc2 = Location(lat=p2_coord1, lon=p2_coord2)
                    brng = bearing(loc1, loc2)
                    direc = direction(brng, mode=direction_mode)
                    osm_segment = Segment(id=edge_id, direction=direc, start=loc1, end=loc2, points=[loc1, loc2])
                    edges_map_matched.append(osm_segment)
                    edges_ids_unique.append(edge_id)
        if self.use_crs_cartesian:
            trajectory_map_matched = [self.map.yx2latlon(pt[0], pt[1]) for pt in trajectory_map_matched]
        pointsGeo_snapped = [Location(lat=pt[0], lon=pt[1]) for pt in trajectory_map_matched]

        return edges_map_matched, pointsGeo_snapped, lattice_selected

    def _create_map(self, road_network_graph: MultiDiGraph | None = None) -> RoadNetworkMap:
        """
        Create a road network map from a graph.

        This method creates a RoadNetworkMap instance and populates it with nodes and edges
        from the provided graph. It handles coordinate system transformations and supports
        both cartesian and geographic coordinate systems.

        :param MultiDiGraph | None road_network_graph: Optional NetworkX graph containing road network data
        :return RoadNetworkMap: RoadNetworkMap instance populated with network data
        """
        road_network_map = RoadNetworkMap(
            "myosm",
            crs_lonlat=self.road_network_crs_latlon,
            crs_xy=self.road_network_crs_xy,
            use_latlon=True,
            use_rtree=True,
            index_edges=True,
        )

        if road_network_graph:
            road_network_nodes, road_network_edges = ox.graph_to_gdfs(road_network_graph, nodes=True, edges=True)
            for nid, row in road_network_nodes[["x", "y"]].iterrows():
                road_network_map.add_node(nid, (row["y"], row["x"]))
            for eid, _ in road_network_edges.iterrows():
                road_network_map.add_edge(eid[0], eid[1])

        if self.use_crs_cartesian:
            road_network_map = road_network_map.to_xy()

        return road_network_map

    def _create_matcher(self, road_network_map: RoadNetworkMap) -> DistanceMatcher:
        """
        Create a distance matcher for map matching.

        This method creates a DistanceMatcher instance configured with parameters from
        the road network configuration. The matcher is used to perform map matching
        operations on trajectories.

        :param RoadNetworkMap road_network_map: Map instance to use for matching
        :return DistanceMatcher: Configured DistanceMatcher instance
        """
        road_network_matcher = DistanceMatcher(
            road_network_map,
            max_dist=self.map_matching_max_dist_meters,
            max_dist_init=self.map_matching_max_dist_init_meters,
            min_prob_norm=self.map_matching_min_prob_norm,
            non_emitting_length_factor=self.map_matching_non_emitting_length_factor,
            obs_noise=self.map_matching_obs_noise_meters,
            obs_noise_ne=self.map_matching_obs_noise_ne_meters,
            dist_noise=self.map_matching_dist_noise_meters,
            non_emitting_states=self.map_matching_non_emitting_states,
            max_lattice_width=self.map_matching_max_lattice_width,
        )

        return road_network_matcher

    def draw_graph(self, output_file: str, dpi: int = 200) -> None:
        """
        Draw and save a visualization of the road network graph.

        This method creates a visual representation of the road network graph using OSMnx's
        plotting capabilities. The graph is drawn with configurable node colors and can be
        displayed interactively or saved to a file.

        :param str output_file: Path where the graph visualization will be saved
        :param int dpi: Resolution of the output image in dots per inch (default: 200)

        Example::
            >>> network.draw_graph('road_network.png', dpi=300)
        """
        show = False
        close = True
        if self.visualize_graph_show_graph:
            show = True
            close = False
        fig, ax = ox.plot_graph(self.graph, node_color=self.visualize_graph_node_color, show=show, close=close)
        fig.savefig(output_file, dpi=dpi, bbox_inches="tight")

    def draw_map(self, output_file: str) -> None:
        """
        Draw and save a visualization of the road network map with matched trajectories.

        This method creates a visual representation of the road network map using the
        Leuven Map Matching visualization tools. It can include background maps, labels,
        and matched trajectories based on configuration settings.

        :param str output_file: Path where the map visualization will be saved

        Example::
            >>> network.draw_map('road_network_map.png')
        """
        use_osm = self.visualize_map_use_background
        if self.use_crs_cartesian:
            use_osm = False

        mmviz.plot_map(
            self.map,
            matcher=self.matcher,
            use_osm=use_osm,
            zoom_path=self.visualize_map_zoom_path,
            show_labels=self.visualize_map_show_labels,
            show_matching=self.visualize_map_show_matching,
            show_graph=True,
            filename=output_file,
        )


class CoordinateReferenceSystem:
    """
    A class for handling coordinate reference system transformations and map matching.

    This class provides functionality to:
    1. Transform coordinates between different reference systems:
    - Geographic (lat/lon) to Cartesian (x/y)
    - Custom coordinate systems with origin offsets
    2. Perform map matching of trajectories to road networks
    3. Create and visualize road networks
    4. Export data to various formats (KML, JSON)
    5. Handle network segment shifting and visualization

    :ivar AppCoordinateReferenceSystemConfig config: Configuration object containing CRS parameters
    :ivar RoadNetwork road_network: The road network for map matching
    :ivar tuple[float, float] | None crs_cartesian_custom_origin_cartesian: Custom origin in cartesian coordinates

    Example::
        >>> config = AppCoordinateReferenceSystemConfig(
        ...     inputDataInCRSCartesian=True,
        ...     crsLatLon="EPSG:4326",
        ...     crsCartesian="EPSG:26915",
        ...     roadNetwork=RoadNetworkConfig(enable=True)
        ... )
        >>> crs = CoordinateReferenceSystem(config)
        >>> point_xy = (100, 200)
        >>> point_latlon = crs.point_xy_to_latlon(point_xy)
        >>> trajectory = [(100, 200), (101, 201)]
        >>> matched = crs.map_matching(trajectory)
    """

    def __init__(self, config: AppCoordinateReferenceSystemConfig = None) -> None:
        """
        Initialize the CoordinateReferenceSystem with configuration parameters.

        :param AppCoordinateReferenceSystemConfig config: Configuration object containing CRS parameters
        """
        self.config = config
        self._import_parameters_from_config(config)
        self.road_network = RoadNetwork(config.roadNetwork, self.crs_latlon, self.crs_cartesian)

        self.crs_cartesian_custom_origin_cartesian = None
        if self.crs_cartesian_custom_origin_enable:
            self.crs_cartesian_custom_origin_cartesian = self.point_latlon_to_xy(
                self.crs_cartesian_custom_origin_latlon
            )

    def _import_parameters_from_config(self, config: AppCoordinateReferenceSystemConfig) -> None:
        """
        Import parameters from the coordinate reference system configuration.

        This method extracts configuration parameters from the provided AppCoordinateReferenceSystemConfig
        object and sets them as instance variables. It handles parameters for:
        - Input data coordinate system
        - CRS definitions for lat/lon and cartesian coordinates
        - Custom origin settings
        - Road network configuration

        :param AppCoordinateReferenceSystemConfig config: Configuration object containing CRS parameters
        """
        if config:

            self.data_in_cartesian = config.inputDataInCRSCartesian
            self.crs_latlon = config.crsLatLon
            self.crs_cartesian = config.crsCartesian
            self.crs_cartesian_custom_origin_enable = config.crsCartesianCustomOrigin.enable
            self.crs_cartesian_custom_origin_latlon = (
                config.crsCartesianCustomOrigin.lat,
                config.crsCartesianCustomOrigin.lon,
            )
            self.road_network_enable = config.roadNetwork.enable
            self.network_shift_meters = config.roadNetwork.segmentShiftDistanceMeters

    def point_custom_xy_to_crs_cartesian(self, point: tuple[float, float]) -> tuple[float, float]:
        """
        Transform a point from custom XY coordinates to CRS cartesian coordinates.

        This method applies the custom origin offset to transform coordinates
        from a local coordinate system to the global CRS cartesian system.

        :param tuple[float, float] point: (x, y) coordinates in custom system
        :return tuple[float, float]: (x, y) coordinates in CRS cartesian system

        Example::
            >>> point = (100, 200)
            >>> cartesian = crs.point_custom_xy_to_crs_cartesian(point)
            >>> print(f"Cartesian coordinates: {cartesian}")
        """
        if self.crs_cartesian_custom_origin_cartesian:
            point_cartesian = (
                point[0] + self.crs_cartesian_custom_origin_cartesian[0],
                point[1] + self.crs_cartesian_custom_origin_cartesian[1],
            )
            return point_cartesian
        else:
            logger.warning(
                f"Not performing the cartesian transformation since crs_cartesian_custom_origin is not provided or not enable"
            )
            return point

    def point_crs_cartesian_to_custom_xy(self, point: tuple[float, float]) -> tuple[float, float]:
        """
        Transform a point from CRS cartesian coordinates to custom XY coordinates.

        This method applies the custom origin offset to transform coordinates
        from the global CRS cartesian system to a local coordinate system.

        :param tuple[float, float] point: (x, y) coordinates in CRS cartesian system
        :return tuple[float, float]: (x, y) coordinates in custom system

        Example::
            >>> point = (100, 200)
            >>> custom_xy = crs.point_crs_cartesian_to_custom_xy(point)
            >>> print(f"Custom coordinates: {custom_xy}")
        """

        if self.crs_cartesian_custom_origin_cartesian:
            point_xy = (
                point[0] - self.crs_cartesian_custom_origin_cartesian[0],
                point[1] - self.crs_cartesian_custom_origin_cartesian[1],
            )
            return point_xy
        else:
            logger.warning(
                f"Not performing the cartesian transformation since crs_cartesian_custom_origin is not provided or not enable"
            )
            return point

    def trajectory_custom_xy_to_crs_cartesian(self, trajectory: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        Transform a trajectory from custom XY coordinates to CRS cartesian coordinates.

        This method applies the custom origin offset to transform each point in the trajectory
        from a local coordinate system to the global CRS cartesian system.

        :param list[tuple[float, float]] trajectory: List of (x,y) coordinates in custom system
        :return list[tuple[float, float]]: List of (x,y) coordinates in CRS cartesian system

        Example::
            >>> trajectory = [(100, 200), (101, 201)]
            >>> cartesian = crs.trajectory_custom_xy_to_crs_cartesian(trajectory)
            >>> print(f"Cartesian coordinates: {cartesian}")
        """

        trajectory_cartesian = [self.point_custom_xy_to_crs_cartesian(point) for point in trajectory]
        return trajectory_cartesian

    def trajectory_crs_cartesian_to_custom_xy(self, trajectory: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        Transform a trajectory from CRS cartesian coordinates to custom XY coordinates.

        This method applies the custom origin offset to transform each point in the trajectory
        from the global CRS cartesian system to a local coordinate system.

        :param list[tuple[float, float]] trajectory: List of (x,y) coordinates in CRS cartesian system
        :return list[tuple[float, float]]: List of (x,y) coordinates in custom system

        Example::
            >>> trajectory = [(100, 200), (101, 201)]
            >>> custom_xy = crs.trajectory_crs_cartesian_to_custom_xy(trajectory)
            >>> print(f"Custom coordinates: {custom_xy}")
        """

        trajectory_cartesian = [self.point_crs_cartesian_to_custom_xy(point) for point in trajectory]
        return trajectory_cartesian

    def map_matching(
        self, trajectory: list[tuple[float, float]], exclude_non_emitting_state: bool = False
    ) -> list[Any]:
        """
        Perform map matching on a trajectory.

        This method matches a trajectory to the road network, handling both
        cartesian and geographic coordinate systems. It supports custom
        coordinate transformations and different matching strategies.

        :param list[tuple[float, float]] trajectory: List of (x,y) or (lat,lon) coordinates
        :param bool exclude_non_emitting_state: Whether to exclude non-emitting states
        :return list[Any]: List of matched points

        Example::
            >>> trajectory = [(37.7749, -122.4194), (37.7750, -122.4195)]
            >>> matched = crs.map_matching(trajectory)
            >>> print(f"Matched {len(matched)} points")
        """
        trajectory_map_matched = []

        if self.data_in_cartesian:
            if self.crs_cartesian_custom_origin_cartesian:
                trajectory_cartesian = self.trajectory_custom_xy_to_crs_cartesian(trajectory)
                trajectory_cartesian_map_matched = self.map_matching_cartesian(
                    trajectory_cartesian, exclude_non_emitting_state=exclude_non_emitting_state
                )
                trajectory_map_matched = self.trajectory_crs_cartesian_to_custom_xy(trajectory_cartesian_map_matched)
            else:
                trajectory_map_matched = self.map_matching_cartesian(
                    trajectory, exclude_non_emitting_state=exclude_non_emitting_state
                )
        else:
            trajectory_map_matched = self.map_matching_latlon(
                trajectory, exclude_non_emitting_state=exclude_non_emitting_state
            )

        return trajectory_map_matched

    def create_network_json_file(self, line_segment_file_path: str, network_file_path: str) -> None:
        """
        Create a network JSON file from line segment data.

        This method reads line segment data from a file, processes it to create a network
        with intersections and segments, and writes the result to a JSON file.

        :param str line_segment_file_path: Path to input line segment file
        :param str network_file_path: Path to output network JSON file

        Example::
            >>> crs.create_network_json_file('segments.json', 'network.json')
        """

        valid_line_segment_path = validate_file_path(line_segment_file_path)
        if not os.path.exists(valid_line_segment_path):
            logger.error(f"ERROR: The indicated config file `{valid_line_segment_path}` does NOT exist.")
            exit(1)

        network_input = Network(**load_json_from_file(valid_line_segment_path))

        intersections_new = []
        segment_ids = []  # for deduplication purpose
        for intersection_input in network_input.intersections:

            # segment_ids = [] # for deduplication purpose
            segments_new = []
            for segment_input in intersection_input.segments:

                pointGeo = [
                    Location(lat=segment_input.start.lat, lon=segment_input.start.lon),
                    Location(lat=segment_input.end.lat, lon=segment_input.end.lon),
                ]

                list_segment_new, _, _ = self.road_network.map_matching_for_edge(pointGeo, direction_mode=0)
                for segment_new in list_segment_new:
                    segment_id = segment_new.id
                    if segment_id not in segment_ids:
                        segments_new.append(segment_new)
                        segment_ids.append(segment_id)

            segments_new = self._shift_overlapped_segments(segments_new)
            intersection_new = Intersection(name=intersection_input.name, segments=segments_new)
            intersections_new.append(intersection_new)

        network_new = Network(intersections=intersections_new)

        with open(network_file_path, "w") as outfile:
            json.dump(network_new.dict(), outfile, indent=4)

    def write_network_json_to_kml(self, network_json_file: str, kml_file: str) -> None:
        """
        Convert a network JSON file to KML format.

        This method reads a network JSON file, extracts trajectories from segments,
        and writes them to a KML file for visualization.

        :param str network_json_file: Path to input network JSON file
        :param str kml_file: Path to output KML file

        Example::
            >>> crs.write_network_json_to_kml('network.json', 'network.kml')
        """

        network = Network(**load_json_from_file(network_json_file))
        trajs = []
        for intersection in network.intersections:
            for segment in intersection.segments:
                traj = [(segment.start.lat, segment.start.lon), (segment.end.lat, segment.end.lon)]
                trajs.append(traj)
        self.write_list_of_trajectory_latlon_to_kml(kml_file, trajs)

    def draw_network_json(self, network_file_path: str, output_file_path: str) -> None:
        """
        Visualize a network from a JSON file.

        This method reads a network JSON file and creates a visual representation
        of the network, including nodes and edges, saving it to an output file.

        :param str network_file_path: Path to input network JSON file
        :param str output_file_path: Path to output visualization file

        Example::
            >>> crs.draw_network_json('network.json', 'network_visualization.png')
        """

        valid_network_file_path = validate_file_path(network_file_path)
        if not os.path.exists(valid_network_file_path):
            logger.error(f"ERROR: The indicated config file `{valid_network_file_path}` does NOT exist.")
            exit(1)

        network_json = Network(**load_json_from_file(valid_network_file_path))

        road_network_map = RoadNetworkMap(
            "mynetwork",
            crs_lonlat=self.crs_latlon,
            crs_xy=self.crs_cartesian,
            use_latlon=True,
            use_rtree=True,
            index_edges=True,
        )

        all_node_ids = []
        all_edge_ids = []
        cnt = 0
        for intersection in network_json.intersections:
            for segment in intersection.segments:
                cnt += 1
                start_id = cnt * 10000 + 1
                end_id = cnt * 10000 + 2
                edge_id = segment.id
                #                 if edge_id:
                #                     start_id = int(edge_id.split('-')[0])
                #                     end_id = int(edge_id.split('-')[0])
                if start_id not in all_node_ids:
                    road_network_map.add_node(start_id, (segment.start.lat, segment.start.lon))
                    all_node_ids.append(start_id)
                if end_id not in all_node_ids:
                    road_network_map.add_node(end_id, (segment.end.lat, segment.end.lon))
                    all_node_ids.append(end_id)
                road_network_map.add_edge(start_id, end_id)

        mmviz.plot_map(
            road_network_map,
            use_osm=True,
            zoom_path=True,
            show_labels=False,
            show_matching=False,
            show_graph=True,
            filename=output_file_path,
        )

    def _shift_overlapped_segments(self, segments: list[Segment]) -> list[Segment]:
        """
        Shift overlapping segments to prevent visual overlap in UI.

        This method identifies segments that have opposite directions and shifts
        them to prevent visual overlap when displayed.

        :param list[Segment] segments: List of segments to process
        :return list[Segment]: List of processed segments with overlapping ones shifted

        Example::
            >>> segments = [Segment(id="1-2", start=loc1, end=loc2),
            ...            Segment(id="2-1", start=loc2, end=loc1)]
            >>> shifted = crs._shift_overlapped_segments(segments)
        """

        segments_new = []

        for segment in segments:
            need_to_shift = False
            segment_id = segment.id
            segment_id_reverse = segment_id.split("-")[1] + "-" + segment_id.split("-")[0]
            # check if opposite direction segment exists, if so then shift so that they don't overlap in UI
            for segment_check in segments:
                segment_id_check = segment_check.id
                if segment_id_reverse == segment_id_check:
                    need_to_shift = True
            if need_to_shift:
                segment_shift = self._shift_segment(segment, self.network_shift_meters)
                segments_new.append(segment_shift)
            else:
                segments_new.append(segment)

        return segments_new

    def _shift_segment(self, segment: Segment, shift_distance: float = 5) -> Segment:
        """
        Shift a segment perpendicular to its direction.

        This method shifts a segment by a specified distance perpendicular to
        its direction to prevent visual overlap with other segments.

        :param Segment segment: Segment to shift
        :param float shift_distance: Distance to shift in meters (default: 5)
        :return Segment: Shifted segment

        Example::
            >>> segment = Segment(id="1-2", start=loc1, end=loc2)
            >>> shifted = crs._shift_segment(segment, shift_distance=10)
        """
        # shift_distance in meters

        b = bearing(segment.start, segment.end)
        shift_direction = (b + 90) % 360

        start_shift = get_point_at_distance(segment.start, shift_distance, shift_direction)
        end_shift = get_point_at_distance(segment.end, shift_distance, shift_direction)
        segment_shift = Segment(
            id=segment.id,
            direction=segment.direction,
            start=start_shift,
            end=end_shift,
            points=[start_shift, end_shift],
        )

        return segment_shift

    def map_matching_location(
        self, pointsGeo: list[Location], exclude_non_emitting_state: bool = True
    ) -> list[Location]:
        """
        Perform map matching on a list of geographic locations.

        This method matches a sequence of geographic locations to the road network,
        converting them to a snapped trajectory that follows the network.

        :param list[Location] pointsGeo: List of geographic locations to match
        :param bool exclude_non_emitting_state: Whether to exclude non-emitting states (default: True)
        :return list[Location]: List of matched locations

        Example::
            >>> locations = [Location(lat=37.7749, lon=-122.4194),
            ...             Location(lat=37.7750, lon=-122.4195)]
            >>> matched = crs.map_matching_location(locations)
        """

        traj_latlon = [(loc.lat, loc.lon) for loc in pointsGeo]
        snapped_trajectory = self.map_matching_latlon(
            traj_latlon, exclude_non_emitting_state=exclude_non_emitting_state
        )
        snapped_trajectory = [Location(lat=pt[0], lon=pt[1]) for pt in snapped_trajectory]

        return snapped_trajectory

    def map_matching_latlon(
        self, trajectory_latlon: list[tuple[float, float]], exclude_non_emitting_state: bool = False
    ) -> list[Any]:
        """
        Perform map matching on a trajectory in lat/lon coordinates.

        :param list[tuple[float, float]] trajectory_latlon: List of (lat,lon) coordinates
        :param bool exclude_non_emitting_state: Whether to exclude non-emitting states
        :return list[Any]: List of matched points
        """
        trajectory_latlon_map_matched = []

        if self.road_network.use_crs_cartesian:
            trajectory_xy = self.trajectory_latlon_to_xy(trajectory_latlon)
            trajectory_yx = self._trajectory_swap_xy(trajectory_xy)
            trajectory_yx_map_matched = self.road_network.map_matching(
                trajectory_yx, exclude_non_emitting_state=exclude_non_emitting_state
            )
            trajectory_xy_map_matched = self._trajectory_swap_xy(trajectory_yx_map_matched)
            trajectory_latlon_map_matched = self.trajectory_xy_to_latlon(trajectory_xy_map_matched)
        else:
            trajectory_latlon_map_matched = self.road_network.map_matching(
                trajectory_latlon, exclude_non_emitting_state=exclude_non_emitting_state
            )

        return trajectory_latlon_map_matched

    def map_matching_cartesian(
        self, trajectory_xy: list[tuple[float, float]], exclude_non_emitting_state: bool = False
    ) -> list[Any]:
        """
        Perform map matching on a trajectory in cartesian coordinates.

        :param list[tuple[float, float]] trajectory_xy: List of (x,y) coordinates
        :param bool exclude_non_emitting_state: Whether to exclude non-emitting states
        :return list[Any]: List of matched points
        """
        trajectory_xy_map_matched = []

        if self.road_network.use_crs_cartesian:
            trajectory_yx = self._trajectory_swap_xy(trajectory_xy)
            trajectory_yx_map_matched = self.road_network.map_matching(
                trajectory_yx, exclude_non_emitting_state=exclude_non_emitting_state
            )
            trajectory_xy_map_matched = self._trajectory_swap_xy(trajectory_yx_map_matched)
        else:
            trajectory_latlon = self.trajectory_xy_to_latlon(trajectory_xy)
            trajectory_latlon_map_matched = self.road_network.map_matching(
                trajectory_latlon, exclude_non_emitting_state=exclude_non_emitting_state
            )
            trajectory_xy_map_matched = self.trajectory_latlon_to_xy(trajectory_latlon_map_matched)

        return trajectory_xy_map_matched

    def point_xy_to_latlon(self, point_xy: tuple[float, float]) -> tuple[float, float]:
        """
        Transform a point from XY coordinates to lat/lon coordinates.

        :param tuple[float, float] point_xy: (x, y) coordinates
        :return tuple[float, float]: (lat, lon) coordinates

        Example::
            >>> point = (100, 200)
            >>> latlon = crs.point_xy_to_latlon(point)
            >>> print(f"Lat/Lon: {latlon}")
        """

        x, y = point_xy
        lat, lon = self.road_network.map.xy2latlon(x, y)
        point_latlon = (lat, lon)

        return point_latlon

    def point_latlon_to_xy(self, point_latlon: tuple[float, float]) -> tuple[float, float]:
        """
        Transform a point from lat/lon coordinates to XY coordinates.

        :param tuple[float, float] point_latlon: (lat, lon) coordinates
        :return tuple[float, float]: (x, y) coordinates

        Example::
            >>> point = (37.7749, -122.4194)
            >>> xy = crs.point_latlon_to_xy(point)
            >>> print(f"XY coordinates: {xy}")
        """

        lat, lon = point_latlon
        x, y = self.road_network.map.latlon2xy(lat, lon)
        point_xy = (x, y)

        return point_xy

    def trajectory_xy_to_latlon(self, trajectory_xy: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        Transform a trajectory from XY coordinates to lat/lon coordinates.

        :param list[tuple[float, float]] trajectory_xy: List of (x,y) coordinates
        :return list[tuple[float, float]]: List of (lat,lon) coordinates

        Example::
            >>> trajectory = [(100, 200), (101, 201)]
            >>> latlon = crs.trajectory_xy_to_latlon(trajectory)
            >>> print(f"Lat/Lon coordinates: {latlon}")
        """

        trajectory_latlon = [self.point_xy_to_latlon(point_xy) for point_xy in trajectory_xy]

        return trajectory_latlon

    def trajectory_latlon_to_xy(self, trajectory_latlon: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        Transform a trajectory from lat/lon coordinates to XY coordinates.

        :param list[tuple[float, float]] trajectory_latlon: List of (lat,lon) coordinates
        :return list[tuple[float, float]]: List of (x,y) coordinates

        Example::
            >>> trajectory = [(37.7749, -122.4194), (37.7750, -122.4195)]
            >>> xy = crs.trajectory_latlon_to_xy(trajectory)
            >>> print(f"XY coordinates: {xy}")
        """

        trajectory_xy = [self.point_latlon_to_xy(point_latlon) for point_latlon in trajectory_latlon]

        return trajectory_xy

    def write_list_of_trajectory_latlon_to_kml(
        self,
        kml_file_name: str,
        list_of_trajectory_latlon: list[list[tuple[float, float]]],
        list_of_line_width: list[int] | None = None,
        list_of_line_color: list[str] | None = None,
    ) -> None:
        """
        Write a list of trajectories to a KML file.

        This method creates a KML file containing multiple trajectories with
        customizable line styles. Each trajectory is represented as a LineString
        with optional styling parameters.

        :param str kml_file_name: Path to output KML file
        :param list[list[tuple[float, float]]] list_of_trajectory_latlon: List of trajectories, each as list of (lat,lon) points
        :param list[int] | None list_of_line_width: Optional list of line widths for each trajectory
        :param list[str] | None list_of_line_color: Optional list of line colors for each trajectory

        Example::
            >>> trajectories = [
            ...     [(37.7749, -122.4194), (37.7750, -122.4195)],
            ...     [(37.7751, -122.4196), (37.7752, -122.4197)]
            ... ]
            >>> colors = ['ff0000ff', 'ff00ff00']  # Red and green
            >>> widths = [3, 2]
            >>> crs.write_list_of_trajectory_latlon_to_kml('output.kml', trajectories, widths, colors)
        """
        kml = self._create_kml_from_list_of_trajectory_latlon(
            list_of_trajectory_latlon, list_of_line_width=list_of_line_width, list_of_line_color=list_of_line_color
        )

        # Write KML to file
        with open(kml_file_name, "wb") as f:
            f.write(etree.tostring(kml, pretty_print=True))

    def _create_kml_from_list_of_trajectory_latlon(
        self,
        list_of_trajectory_latlon: list[list[tuple[float, float]]],
        list_of_line_width: list[int] | None = None,
        list_of_line_color: list[str] | None = None,
    ) -> etree.Element:
        """
        Create a KML document containing multiple trajectories.

        This method creates a KML document structure containing multiple trajectories,
        each represented as a LineString with customizable styling. The trajectories
        can have different line widths and colors.

        :param list[list[tuple[float, float]]] list_of_trajectory_latlon: List of trajectories, each as list of (lat,lon) points
        :param list[int] | None list_of_line_width: Optional list of line widths for each trajectory
        :param list[str] | None list_of_line_color: Optional list of line colors for each trajectory
        :return etree.Element: KML document element containing all trajectories

        Example::
            >>> trajectories = [[(37.7749, -122.4194), (37.7750, -122.4195)]]
            >>> colors = ['ff0000ff']  # Red
            >>> widths = [3]
            >>> kml_doc = crs._create_kml_from_list_of_trajectory_latlon(trajectories, widths, colors)
        """
        default_line_width = 3
        default_line_color = "ff00ff00"  # Green

        # Create root KML document
        doc = KML.kml(KML.Document(KML.name("Trajectories")))

        for i in range(len(list_of_trajectory_latlon)):
            trajectory_latlon = list_of_trajectory_latlon[i]
            line_width = default_line_width if not list_of_line_width else list_of_line_width[i]
            line_color = default_line_color if not list_of_line_color else list_of_line_color[i]

            # Add trajectory to document
            doc = self._add_trajectory_placemark(doc, trajectory_latlon, line_width, line_color, i)

        return doc

    def _add_trajectory_placemark(
        self,
        doc: etree.Element,
        trajectory_latlon: list[tuple[float, float]],
        line_width: int,
        line_color: str,
        index: int,
    ) -> etree.Element:
        """
        Add a trajectory as a placemark to a KML document.

        This method adds a single trajectory to a KML document as a LineString placemark,
        including start and end points. The trajectory is styled according to the provided
        width and color parameters.

        :param etree.Element doc: KML document to add the placemark to
        :param list[tuple[float, float]] trajectory_latlon: List of (lat,lon) points forming the trajectory
        :param int line_width: Width of the trajectory line
        :param str line_color: Color of the trajectory line in KML format (e.g., 'ff0000ff' for red)
        :param int index: Index of the trajectory for naming purposes
        :return etree.Element: Updated KML document with the new placemark

        Example::
            >>> doc = KML.kml(KML.Document())
            >>> trajectory = [(37.7749, -122.4194), (37.7750, -122.4195)]
            >>> doc = crs._add_trajectory_placemark(doc, trajectory, 3, 'ff0000ff', 0)
        """
        # Create coordinates string
        coords = " ".join(f"{lon},{lat},1" for lat, lon in trajectory_latlon)

        # Create placemark with linestring
        placemark_linestring = KML.Placemark(
            KML.name(f"Trajectory {index}"),
            KML.description(f"Trajectory {index}"),
            KML.Style(KML.LineStyle(KML.color(line_color), KML.width(str(line_width)))),
            KML.LineString(KML.extrude("1"), GX.altitudeMode("relativeToGround"), KML.coordinates(coords)),
        )
        doc.Document.append(placemark_linestring)

        # Add points for start/end
        for i, (lat, lon) in enumerate(trajectory_latlon):
            if i == 0 or i == len(trajectory_latlon) - 1:
                point_type = "start" if i == 0 else "end"
                placemark_point = KML.Placemark(
                    KML.description(f"Trajectory {index} {point_type} point"),
                    KML.Point(KML.coordinates(f"{lon},{lat},0")),
                )
                doc.Document.append(placemark_point)

        return doc

    def _point_swap_xy(self, point: tuple[float, float]) -> tuple[float, float]:
        """
        Swap x and y coordinates of a point.

        This method takes a point in (x,y) format and returns it in (y,x) format.
        This is useful for coordinate system transformations where the order of
        coordinates needs to be reversed.

        :param tuple[float, float] point: (x, y) coordinates to swap
        :return tuple[float, float]: (y,x) coordinates

        Example::
            >>> point = (100, 200)
            >>> swapped = crs._point_swap_xy(point)
            >>> print(f"Swapped coordinates: {swapped}")  # (200, 100)
        """
        e1, e2 = point
        return (e2, e1)

    def _trajectory_swap_xy(self, trajectory: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        Swap x and y coordinates for all points in a trajectory.

        This method takes a trajectory (list of points) and swaps the x and y
        coordinates for each point. This is useful for coordinate system
        transformations where the order of coordinates needs to be reversed
        for the entire trajectory.

        :param list[tuple[float, float]] trajectory: List of (x,y) coordinates to swap
        :return list[tuple[float, float]]: List of (y,x) coordinates

        Example::
            >>> trajectory = [(100, 200), (101, 201)]
            >>> swapped = crs._trajectory_swap_xy(trajectory)
            >>> print(f"Swapped coordinates: {swapped}")  # [(200, 100), (201, 101)]
        """
        return [self._point_swap_xy(point) for point in trajectory]
