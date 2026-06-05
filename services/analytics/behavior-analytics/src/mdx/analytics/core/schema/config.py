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
import os
from datetime import datetime
from enum import StrEnum, auto
from functools import cached_property, cache

from omegaconf import MISSING
from pydantic import BaseModel, computed_field, ConfigDict, Field

from mdx.analytics.core.utils.util import str_to_bool


# Default values, for any other default values not listed below, check Config classes
EMPTY_STR = ""
FALSE_STR = "false"
DEFAULT_SENSOR_ID = "default"
IN_3D_MODE = "false"
IN_SIMULATION_MODE = "false"


# Default values for Calibration config
COMPACT_FRAME = "false"
USE_OBJECT_LOCATION = "false"
IMAGE_LOCATION_MODE = "bottom_center"

# Default values for anomaly action config
ANOMALY_ACTION_THRESHOLD = "0.5"

# Default values for anomaly config, for other anomaly config default values, check AnomalyConfig class
ANOMALY_IGNORE_SENSORS = "[]"
ANOMALY_CLASSES = "[]"

# Default values for behavior config
STATE_MANAGEMENT_FILTER = "[]"
BEHAVIOR_STATE_TIMEOUT = "10"
BEHAVIOR_STATE_VALID_INTERVAL = "6"
BEHAVIOR_WATERMARK_SEC = "30"
BEHAVIOR_TIME_THRESHOLD = "1970-01-01T00:00:00.000Z"
BEHAVIOR_MAX_POINTS = "200"
BEHAVIOR_STATE_END_TOLERANCE_SEC = "0.1"
CLUSTER_THRESHOLD = "0.9"
OBJECT_CONFIDENCE_THRESHOLD = "0.5"

# Default values for API config
API_RETRY_MAX_CNT = "30"
API_RETRY_MAX_TIME_SEC = "30"
API_MDX_BASE_URL = "http://localhost:8081"

# Default values for USD config
API_AGS_BASE_URL = "None"
USD_FILE_PATH = "None"

# Default values for sink and source config
DEFAULT_SINK = "kafka"
DEFAULT_SOURCE = "kafka"

# Default values for trajectory config
GEO_COORD_ENABLE = "true"
DIRECTION_MODE = "0"
DIRECTION_CLUSTER_MODE = "1"
SMOOTH_MIN_POINTS = "20"
SMOOTH_WINDOW_SIZE = "5"
DISTANCE_STRIDE = "5"
SPEED_SEGMENT_SIZE = "10"

# Default values for trajectory map matching config
MAP_MATCHING_MAX_POINTS = "5"
MAP_MATCHING_CLASSES = "[]"

# Default values for space analytics config
INTERVAL_SEC = "5.0"
GRID_SIZE = "0.2"
UNSAFE_SIZE = "0.5"
TARGET_CLASSES = "[\"Box\", \"Pallet\"]"
USE_GA = "false"
POPULATION_SIZE_GA = "200"
NUM_GENERATIONS_GA = "300"

# Default values for video embed config
EMBED_DOWNSAMPLE_ENABLE = "false"
EMBED_SENSOR_TTL_SEC = "3600"
EMBED_DOWNSAMPLER_TYPE = "window"
EMBED_DOWNSAMPLE_TOLERANCE_MODE = "cosine"
EMBED_DOWNSAMPLE_SIMILARITY_THRESHOLD = "0.90"
EMBED_DOWNSAMPLE_DISTANCE_THRESHOLD = "0.15"
EMBED_DOWNSAMPLE_MAX_INTERVAL_SEC = "60"
EMBED_DOWNSAMPLE_WINDOW_SIZE = "60"
EMBED_DOWNSAMPLE_MIN_NEIGHBOURS = "3"

# Default values for sensor proximity detection config
PROXIMITY_DETECTION_ENABLE = "false"
PROXIMITY_DETECTION_THRESHOLD = "1.8"
PROXIMITY_DETECTION_CENTER_CLASSES = "[]"
PROXIMITY_DETECTION_SURROUNDING_CLASSES = "[]"

# Default values for sensor config
TRIPWIRE_MIN_POINTS = "5"
SENSOR_MIN_FRAMES = "5"

# Default values for incident state management (applies to all violation types)
INCIDENT_OBJECT_TTL = "3600"  # 1 hour in seconds

# Default values for proximity violation detection
PROXIMITY_VIOLATION_INCIDENT_ENABLE = "false"
PROXIMITY_VIOLATION_INCIDENT_THRESHOLD = "1"
PROXIMITY_VIOLATION_INCIDENT_EXPIRATION_WINDOW = "1"

# Default values for restricted area violation detection
RESTRICTED_AREA_VIOLATION_INCIDENT_ENABLE = "false"
RESTRICTED_AREA_VIOLATION_INCIDENT_THRESHOLD = "1"
RESTRICTED_AREA_VIOLATION_INCIDENT_EXPIRATION_WINDOW = "1"

# Default values for confined area violation detection
CONFINED_AREA_VIOLATION_INCIDENT_ENABLE = "false"
CONFINED_AREA_VIOLATION_INCIDENT_THRESHOLD = "1"
CONFINED_AREA_VIOLATION_INCIDENT_EXPIRATION_WINDOW = "1"

# Default values for FOV count violation detection
FOV_COUNT_VIOLATION_INCIDENT_ENABLE = "false"
FOV_COUNT_VIOLATION_INCIDENT_OBJECT_THRESHOLD = "1"
FOV_COUNT_VIOLATION_INCIDENT_THRESHOLD = "1"
FOV_COUNT_VIOLATION_INCIDENT_EXPIRATION_WINDOW = "1"
FOV_COUNT_VIOLATION_INCIDENT_OBJECT_TYPE = "Person"

# Default values for playback config
PLAYBACK_SENSORS = "[]"
PLAYBACK_LOOP = "1"
FILTER_EMPTY_OBJECTS = "true"
STARTUP_DELAY_SEC = "0"
SIMULATION_BASE_TIME = ""
SIMULATION_TIMEDELTA_MIN = "60"
SIMULATION_FPS = "1"


class KeyValuePair(BaseModel):
    """
    Configuration class for key-value pairs.

    This class represents a simple key-value pair configuration structure.

    :ivar str name: The key name
    :ivar str value: The value associated with the key

    Examples::
        >>> kv_pair = KeyValuePair(name="threshold", value="0.5")
        >>> print(kv_pair.name)  # "threshold"
        >>> print(kv_pair.value)  # "0.5"
    """

    name: str = ""
    value: str = ""


class KafkaConsumerConfig(BaseModel):
    """
    Configuration class for Kafka consumer settings with field validation.

    This class defines the configuration parameters for a Kafka consumer,
    including offset reset behavior, commit settings, and polling parameters.

    :ivar str autoOffsetReset: Strategy for resetting offsets when no offset is available
    :ivar bool enableAutoCommit: Whether to enable automatic offset commits
    :ivar int maxPollIntervalMs: Maximum time between poll() calls
    :ivar int maxPartitionFetchBytes: Maximum amount of data per-partition to fetch
    :ivar int fetchMaxBytes: Maximum amount of data to fetch in a single request
    :ivar int maxPollRecords: Maximum number of records to poll at once
    :ivar float timeout: Maximum time in seconds to wait for data in consume()
    :ivar int retryMaxAttempts: Maximum number of attempts to create a consumer and
        reach partition assignment before giving up.
    :ivar float retryIntervalSec: Seconds to wait between consumer creation attempts
        after a recoverable failure (close succeeded).

    Examples::
        >>> consumer_config = KafkaConsumerConfig(
        ...     autoOffsetReset="latest",
        ...     enableAutoCommit=False,
        ...     maxPollIntervalMs=900000
        ... )
        >>> print(consumer_config.autoOffsetReset)  # "latest"
        >>> print(consumer_config.enableAutoCommit)  # False
    """

    autoOffsetReset: str = "latest"
    enableAutoCommit: bool = False
    maxPollIntervalMs: int = Field(default=900000, ge=1, le=86400000)
    maxPartitionFetchBytes: int = Field(default=10485760, ge=1, le=1000000000)
    fetchMaxBytes: int = Field(default=104857600, ge=0, le=2147483135)
    maxPollRecords: int = Field(default=1000, ge=1, le=1000000)
    timeout: float = Field(default=0.01, ge=-1)
    retryMaxAttempts: int = Field(default=3, ge=1, le=100)
    retryIntervalSec: float = Field(default=30.0, ge=0.0, le=3600.0)

class KafkaProducerConfig(BaseModel):
    """
    Configuration class for Kafka producer settings with field validation.

    This class defines the configuration parameters for a Kafka producer,
    including message batching and size settings.

    :ivar int lingerMs: Time to wait before sending messages (0 or greater)
    :ivar int messageMaxBytes: Maximum size of a message (must be positive)
    :ivar int batchSize: Maximum bytes for batching multiple messages together (soft limit, single messages can exceed this)

    Examples::
        >>> producer_config = KafkaProducerConfig(
        ...     lingerMs=100,
        ...     messageMaxBytes=10485760,
        ...     batchSize=16384
        ... )
        >>> print(producer_config.lingerMs)  # 100
    """

    lingerMs: int = Field(default=0, ge=0, le=900000)
    messageMaxBytes: int = Field(default=10485760, ge=1000, le=1000000000)
    batchSize: int = Field(default=16384, ge=1, le=2147483647)


class AppKafkaConfig(BaseModel):
    """
    Configuration class for application Kafka settings.

    This class combines consumer and producer configurations with broker
    and topic settings for the application.

    :ivar str brokers: Comma-separated list of Kafka broker addresses
    :ivar list[KeyValuePair] topics: List of topic configurations
    :ivar KafkaConsumerConfig consumer: Consumer configuration
    :ivar KafkaProducerConfig producer: Producer configuration
    :ivar str group: Consumer group ID

    Examples::
        >>> kafka_config = AppKafkaConfig(
        ...     brokers="localhost:9092",
        ...     group="my-group",
        ...     topics=[KeyValuePair(name="input", value="input-topic")]
        ... )
        >>> print(kafka_config.brokers)  # "localhost:9092"
        >>> print(kafka_config.get_kafka_topic("input"))  # "input-topic"
    """

    brokers: str = "localhost:9092"
    topics: list[KeyValuePair] = list()
    consumer: KafkaConsumerConfig = KafkaConsumerConfig()
    producer: KafkaProducerConfig = KafkaProducerConfig()
    group: str = MISSING


class RedisStreamConsumerConfig(BaseModel):
    """
    Configuration class for Redis stream consumer settings.

    This class defines the configuration parameters for a Redis stream consumer,
    including read count, blocking timeout, and stream creation settings.

    :ivar int readCount: Maximum number of messages to read from the stream at once
    :ivar int readBlockMs: Time in milliseconds to block waiting for new messages
    :ivar bool mkstream: Whether to create the stream if it doesn't exist
    :ivar int retryMaxAttempts: Maximum number of attempts to set up the Redis consumer
        group on transient connection failures before giving up.
    :ivar float retryIntervalSec: Seconds to wait between connection attempts.

    Examples::
        >>> consumer_config = RedisStreamConsumerConfig(
        ...     readCount=16,
        ...     readBlockMs=4,
        ...     mkstream=False
        ... )
        >>> print(consumer_config.readCount)  # 16
        >>> print(consumer_config.mkstream)  # False
    """

    readCount: int = 1000
    readBlockMs: int = 5
    mkstream: bool = True
    retryMaxAttempts: int = Field(default=3, ge=1, le=100)
    retryIntervalSec: float = Field(default=30.0, ge=0.0, le=3600.0)


class RedisStreamProducerConfig(BaseModel):
    """
    Configuration class for Redis stream producer settings.

    This class defines the configuration parameters for a Redis stream producer,
    including maximum stream length for automatic trimming.

    :ivar int maxLen: Maximum number of entries to keep in the stream (older entries are automatically trimmed)

    Examples::
        >>> producer_config = RedisStreamProducerConfig(
        ...     maxLen=10000
        ... )
        >>> print(producer_config.maxLen)  # 10000
    """

    maxLen: int = 10000


class AppRedisStreamConfig(BaseModel):
    """
    Configuration class for application Redis stream settings.

    This class combines connection settings, stream configurations, and consumer/producer
    settings for Redis stream communication in the application.

    :ivar str host: Redis server hostname or IP address
    :ivar int port: Redis server port number
    :ivar int db: Redis database number to use
    :ivar list[KeyValuePair] streams: List of stream name mappings (key-value pairs)
    :ivar RedisStreamConsumerConfig consumer: Consumer configuration settings
    :ivar RedisStreamProducerConfig producer: Producer configuration settings
    :ivar str group: Consumer group name for the Redis stream

    Examples::
        >>> redis_config = AppRedisStreamConfig(
        ...     host="localhost",
        ...     port=6379,
        ...     db=1,
        ...     group="my-consumer-group",
        ...     streams=[KeyValuePair(name="input", value="input-stream")]
        ... )
        >>> print(redis_config.host)  # "localhost"
        >>> print(redis_config.port)  # 6379
        >>> print(redis_config.consumer.readCount)  # 16 (default)
    """

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    streams: list[KeyValuePair] = list()
    consumer: RedisStreamConsumerConfig = RedisStreamConsumerConfig()
    producer: RedisStreamProducerConfig = RedisStreamProducerConfig()
    group: str = MISSING


class MQTTConsumerConfig(BaseModel):
    """
    Configuration class for MQTT consumer settings.

    This class defines the configuration parameters for an MQTT consumer,
    including quality of service settings, polling behavior, and timeout values.

    :ivar int qos: Quality of Service level (0, 1, or 2)
    :ivar int maxPollCount: Maximum number of messages to poll at once
    :ivar float pollTimeoutSec: Timeout in seconds for polling operations
    :ivar int retryMaxAttempts: Maximum number of attempts to connect to the MQTT
        broker on transient connection failures before giving up.
    :ivar float retryIntervalSec: Seconds to wait between connection attempts.

    Examples::
        >>> consumer_config = MQTTConsumerConfig(
        ...     qos=1,
        ...     maxPollCount=32,
        ...     pollTimeoutSec=5
        ... )
        >>> print(consumer_config.qos)  # 1
        >>> print(consumer_config.maxPollCount)  # 32
    """

    qos: int = 1
    maxPollCount: int = 1000
    pollTimeoutSec: float = 0.01
    retryMaxAttempts: int = Field(default=3, ge=1, le=100)
    retryIntervalSec: float = Field(default=30.0, ge=0.0, le=3600.0)


class MQTTProducerConfig(BaseModel):
    """
    Configuration class for MQTT producer settings.

    This class defines the configuration parameters for an MQTT producer,
    including quality of service settings and message retention behavior.

    :ivar int qos: Quality of Service level (0, 1, or 2)
    :ivar bool retain: Whether to retain messages on the broker

    Examples::
        >>> producer_config = MQTTProducerConfig(
        ...     qos=1,
        ...     retain=False
        ... )
        >>> print(producer_config.qos)  # 1
        >>> print(producer_config.retain)  # False
    """

    qos: int = 1
    retain: bool = True


class AppMQTTConfig(BaseModel):
    """
    Configuration class for application MQTT settings.

    This class combines connection settings, topic configurations, and consumer/producer
    settings for MQTT communication in the application.

    :ivar str host: MQTT broker hostname or IP address
    :ivar int port: MQTT broker port number
    :ivar str clientId: Unique client identifier for MQTT connection
    :ivar int keepAliveSec: Keep-alive interval in seconds
    :ivar list[KeyValuePair] topics: List of topic name mappings (key-value pairs)
    :ivar MQTTConsumerConfig consumer: Consumer configuration settings
    :ivar MQTTProducerConfig producer: Producer configuration settings

    Examples::
        >>> mqtt_config = AppMQTTConfig(
        ...     host="localhost",
        ...     port=1883,
        ...     clientId="analytics-client",
        ...     keepAliveSec=30,
        ...     topics=[KeyValuePair(name="input", value="sensor/data")]
        ... )
        >>> print(mqtt_config.host)  # "localhost"
        >>> print(mqtt_config.port)  # 1883
        >>> print(mqtt_config.consumer.qos)  # 1 (default)
    """

    host: str = MISSING
    port: int = MISSING
    clientId: str = MISSING
    keepAliveSec: int = 60
    topics: list[KeyValuePair] = list()
    consumer: MQTTConsumerConfig = MQTTConsumerConfig()
    producer: MQTTProducerConfig = MQTTProducerConfig()


class AppSensorConfig(BaseModel):
    """
    Configuration class for application sensor settings.

    This class defines the configuration for individual sensors in the application.

    :ivar str id: Unique identifier for the sensor
    :ivar list[KeyValuePair] configs: List of sensor-specific configurations

    Examples::
        >>> sensor_config = AppSensorConfig(
        ...     id="camera1",
        ...     configs=[
        ...         KeyValuePair(name="resolution", value="1920x1080"),
        ...         KeyValuePair(name="fps", value="30")
        ...     ]
        ... )
        >>> print(sensor_config.id)  # "camera1"
        >>> print(len(sensor_config.configs))  # 2
    """

    id: str = MISSING
    configs: list[KeyValuePair] = list()



class MapMatchingConfig(BaseModel):
    """
    Configuration class for map matching settings.

    This class defines parameters for matching GPS points to road networks.

    :ivar float mapMatchingMaxDistMeters: Maximum distance for matching points
    :ivar float mapMatchingMaxDistInitMeters: Maximum initial distance for matching
    :ivar float mapMatchingMinProbNorm: Minimum probability threshold
    :ivar float mapMatchingNonEmittingLengthFactor: Factor for non-emitting states
    :ivar float mapMatchingObsNoiseMeters: Observation noise in meters
    :ivar float mapMatchingObsNoiseNonEmittingStatesMeters: Noise for non-emitting states
    :ivar float mapMatchingDistNoiseMeters: Distance noise in meters
    :ivar bool mapMatchingNonEmittingStates: Whether to use non-emitting states
    :ivar int mapMatchingMaxLatticeWidth: Maximum width of the matching lattice

    Examples::
        >>> map_matching = MapMatchingConfig(
        ...     mapMatchingMaxDistMeters=100.0,
        ...     mapMatchingObsNoiseMeters=50.0
        ... )
        >>> print(map_matching.mapMatchingMaxDistMeters)  # 100.0
        >>> print(map_matching.mapMatchingObsNoiseMeters)  # 50.0
    """

    mapMatchingMaxDistMeters: float = 100.0
    mapMatchingMaxDistInitMeters: float = 25.0
    mapMatchingMinProbNorm: float = 0.001
    mapMatchingNonEmittingLengthFactor: float = 0.75
    mapMatchingObsNoiseMeters: float = 50.0
    mapMatchingObsNoiseNonEmittingStatesMeters: float = 75.0
    mapMatchingDistNoiseMeters: float = 50.0
    mapMatchingNonEmittingStates: bool = True
    mapMatchingMaxLatticeWidth: int = 5


class RoadNetworkPointConfig(BaseModel):
    """
    Configuration class for road network point coordinates.

    This class represents a point in the road network with latitude and longitude.

    :ivar float lat: Latitude coordinate
    :ivar float lon: Longitude coordinate

    Examples::
        >>> point = RoadNetworkPointConfig(lat=42.5, lon=-90.7)
        >>> print(point.lat)  # 42.5
        >>> print(point.lon)  # -90.7
    """

    lat: float = 0.0
    lon: float = 0.0


class GraphConfig(BaseModel):
    """
    Configuration class for road network graph settings.

    This class defines parameters for loading and processing road network graphs.

    :ivar bool graphFromOSM: Whether to load graph from OpenStreetMap
    :ivar str osmLoadMethod: Method to load OSM data
    :ivar str osmType: Type of OSM data to load
    :ivar bool osmSimplify: Whether to simplify the graph
    :ivar RoadNetworkPointConfig osmQueryPoint: Center point for OSM query
    :ivar float osmQueryPointDistMeters: Distance radius for OSM query
    :ivar list[RoadNetworkPointConfig] osmQueryPolygon: Polygon for OSM query
    :ivar str osmQueryPlace: Place name for OSM query
    :ivar str osmQueryFile: Path to OSM data file

    Examples::
        >>> graph_config = GraphConfig(
        ...     graphFromOSM=True,
        ...     osmType="drive",
        ...     osmQueryPlace="Dubuque, Iowa, USA"
        ... )
        >>> print(graph_config.osmType)  # "drive"
        >>> print(graph_config.osmQueryPlace)  # "Dubuque, Iowa, USA"
    """

    graphFromOSM: bool = True
    osmLoadMethod: str = "from_polygon"
    osmType: str = "drive"
    osmSimplify: bool = False
    osmQueryPoint: RoadNetworkPointConfig = RoadNetworkPointConfig()
    osmQueryPointDistMeters: float = 500.0
    osmQueryPolygon: list[RoadNetworkPointConfig] = list()
    osmQueryPlace: str = "Dubuque, Iowa, USA"
    osmQueryFile: str = "sample_data/iowa-latest.osm.pbf"


class VisualizationConfig(BaseModel):
    """
    Configuration class for visualization settings.

    This class defines parameters for visualizing road networks and maps.

    :ivar str visualizationGraphNodeColor: Color for graph nodes
    :ivar bool visualizationGraphShowGraph: Whether to show the graph
    :ivar bool visualizationMapUseBackground: Whether to use map background
    :ivar bool visualizationMapZoomPath: Whether to zoom to path
    :ivar bool visualizationMapShowLabels: Whether to show labels
    :ivar bool visualizationMapShowMatching: Whether to show matching results

    Examples::
        >>> viz_config = VisualizationConfig(
        ...     visualizationGraphNodeColor="blue",
        ...     visualizationMapShowLabels=True
        ... )
        >>> print(viz_config.visualizationGraphNodeColor)  # "blue"
        >>> print(viz_config.visualizationMapShowLabels)  # True
    """

    visualizationGraphNodeColor: str = "r"
    visualizationGraphShowGraph: bool = False
    visualizationMapUseBackground: bool = True
    visualizationMapZoomPath: bool = False
    visualizationMapShowLabels: bool = False
    visualizationMapShowMatching: bool = True


class CRSCartesianCustomOriginConfig(BaseModel):
    """
    Configuration class for custom Cartesian coordinate system origin.

    This class defines parameters for a custom origin in Cartesian coordinates.

    :ivar bool enable: Whether to use custom origin
    :ivar float lat: Latitude of custom origin
    :ivar float lon: Longitude of custom origin

    Examples::
        >>> origin_config = CRSCartesianCustomOriginConfig(
        ...     enable=True,
        ...     lat=42.5,
        ...     lon=-90.7
        ... )
        >>> print(origin_config.enable)  # True
        >>> print(origin_config.lat)  # 42.5
    """

    enable: bool = False
    lat: float = 0.0
    lon: float = 0.0


class RoadNetworkConfig(BaseModel):
    """
    Configuration class for road network settings.

    This class combines various road network related configurations.

    :ivar bool enable: Whether to enable road network processing
    :ivar bool roadNetworkUseCRSCartesian: Whether to use Cartesian coordinates
    :ivar float segmentShiftDistanceMeters: Distance to shift segments
    :ivar MapMatchingConfig mapMatching: Map matching configuration
    :ivar GraphConfig graph: Graph configuration
    :ivar VisualizationConfig visualization: Visualization configuration

    Examples::
        >>> road_network = RoadNetworkConfig(
        ...     enable=True,
        ...     segmentShiftDistanceMeters=5.0
        ... )
        >>> print(road_network.enable)  # True
        >>> print(road_network.segmentShiftDistanceMeters)  # 5.0
    """

    enable: bool = True
    roadNetworkUseCRSCartesian: bool = False
    segmentShiftDistanceMeters: float = 5
    mapMatching: MapMatchingConfig = MapMatchingConfig()
    graph: GraphConfig = GraphConfig()
    visualization: VisualizationConfig = VisualizationConfig()


class AppCoordinateReferenceSystemConfig(BaseModel):
    """
    Configuration class for coordinate reference system settings.

    This class defines parameters for handling different coordinate systems.

    :ivar bool inputDataInCRSCartesian: Whether input data is in Cartesian coordinates
    :ivar str crsLatLon: CRS for latitude/longitude coordinates
    :ivar str crsCartesian: CRS for Cartesian coordinates
    :ivar bool crsCartesianEnablePerSensorOrigin: Whether to use per-sensor origins
    :ivar CRSCartesianCustomOriginConfig crsCartesianCustomOrigin: Custom origin configuration
    :ivar RoadNetworkConfig roadNetwork: Road network configuration

    Examples::
        >>> crs_config = AppCoordinateReferenceSystemConfig(
        ...     inputDataInCRSCartesian=False,
        ...     crsLatLon="EPSG:4326"
        ... )
        >>> print(crs_config.crsLatLon)  # "EPSG:4326"
        >>> print(crs_config.inputDataInCRSCartesian)  # False
    """

    inputDataInCRSCartesian: bool = False
    crsLatLon: str = "EPSG:4326"
    crsCartesian: str = "EPSG:26915"
    crsCartesianEnablePerSensorOrigin: bool = False
    crsCartesianCustomOrigin: CRSCartesianCustomOriginConfig = CRSCartesianCustomOriginConfig()
    roadNetwork: RoadNetworkConfig = RoadNetworkConfig()


class SpeedViolationConfig(BaseModel):
    """
    Configuration class for speed violation detection.

    This class defines parameters for detecting speed violations.

    :ivar bool enable: Whether to enable speed violation detection
    :ivar float mphThreshold: Speed threshold in miles per hour
    :ivar float timeIntervalSecThreshold: Time interval threshold in seconds

    Examples::
        >>> speed_violation = SpeedViolationConfig(
        ...     enable=True,
        ...     mphThreshold=90.0
        ... )
        >>> print(speed_violation.enable)  # True
        >>> print(speed_violation.mphThreshold)  # 90.0
    """

    enable: bool = True
    mphThreshold: float = 90.0
    timeIntervalSecThreshold: float = 5.0


class UnexpectedStopConfig(BaseModel):
    """
    Configuration class for unexpected stop detection.

    This class defines parameters for detecting unexpected stops.

    :ivar bool enable: Whether to enable unexpected stop detection
    :ivar float mphThreshold: Speed threshold in miles per hour
    :ivar float timeIntervalSecThreshold: Time interval threshold in seconds

    Examples::
        >>> stop_config = UnexpectedStopConfig(
        ...     enable=True,
        ...     mphThreshold=5.0,
        ...     timeIntervalSecThreshold=120.0
        ... )
        >>> print(stop_config.enable)  # True
        >>> print(stop_config.timeIntervalSecThreshold)  # 120.0
    """

    enable: bool = True
    mphThreshold: float = 5.0
    timeIntervalSecThreshold: float = 120.0


class AbnormalMovementConfig(BaseModel):
    """
    Configuration class for abnormal movement detection.

    This class defines parameters for detecting abnormal movements.

    :ivar bool enable: Whether to enable abnormal movement detection
    :ivar bool useLinearDistance: Whether to use linear distance
    :ivar float distanceMetersThreshold: Distance threshold in meters
    :ivar float timeIntervalSecThreshold: Time interval threshold in seconds
    :ivar int strideDeviationComputation: Number of points for deviation computation
    :ivar float distanceMetersThresholdDeviationComputation: Distance threshold for deviation
    :ivar float abnormalRelativeThreshold: Relative threshold for abnormality
    :ivar float abnormalRelativeMaxThreshold: Maximum relative threshold
    :ivar bool abnormalRelativeDirectionChangeEnable: Whether to enable direction change detection
    :ivar float changeInDirectionDegree: Direction change threshold in degrees

    Examples::
        >>> movement_config = AbnormalMovementConfig(
        ...     enable=True,
        ...     distanceMetersThreshold=20.0,
        ...     changeInDirectionDegree=30.0
        ... )
        >>> print(movement_config.enable)  # True
        >>> print(movement_config.distanceMetersThreshold)  # 20.0
    """

    enable: bool = True
    useLinearDistance: bool = False
    distanceMetersThreshold: float = 20.0
    timeIntervalSecThreshold: float = 5.0
    strideDeviationComputation: int = 5
    distanceMetersThresholdDeviationComputation: float = 10
    abnormalRelativeThreshold: float = 2.0
    abnormalRelativeMaxThreshold: float = 10.0
    abnormalRelativeDirectionChangeEnable: bool = True
    changeInDirectionDegree: float = 30.0


class CollisionDetectionConfig(BaseModel):
    """
    Configuration class for collision detection settings.

    This class defines parameters for detecting potential collisions between objects.

    :ivar bool enable: Whether to enable collision detection
    :ivar list[str] targetClasses: List of classes to detect collisions for
    :ivar float stopSpeedThreshold: Speed threshold below which an object is considered stopped
    :ivar float stopTimeThreshold: Time threshold (in seconds) an object must be stopped to trigger detection
    :ivar float distanceMetersThreshold: Distance threshold in meters between objects
    :ivar float distancePixelsThreshold: Distance threshold in pixels between objects
    :ivar int alertTimeWindow: Time window (in seconds) for generating collision alerts
    :ivar int alertListTimeoutThreshold: Time window (in seconds) for clearing alert list (The time for an object ID to not be alerted again)
    :ivar int maxNumberPastFrames: Maximum number of past frames to consider for collision detection
    :ivar int ghostTimeThresholdMs: Time threshold in milliseconds for identifying ghost objects
    :ivar float ghostDistanceMetersThreshold: Distance threshold in meters for identifying ghost objects
    :ivar float ghostDistancePixelsThreshold: Distance threshold in pixels for identifying ghost objects
    :ivar bool useAbnormalMovementCondition: Whether to use abnormal movement condition

    Examples::
        >>> collision_config = CollisionDetectionConfig(
        ...     enable=True,
        ...     stopSpeedThreshold=3.0,
        ...     distanceMetersThreshold=5.0
        ... )
        >>> print(collision_config.enable)  # True
        >>> print(collision_config.distanceMetersThreshold)  # 5.0

    Note:
        If a potential collision object is detected in time T0, a collision event will be generated in time interval [T0 - maxNumberPastFrames//FPS, T0 + alertTimeWindow].
    """

    enable: bool = True
    targetClasses: list[str] = ["Vehicle"]
    # Stop by distance
    useDistanceStopCondition: bool = False
    stopTimeByDistanceThreshold: float = 100
    ## GEO coordinate system
    stopDistanceGEOThreshold: float = 0.5 # 0.5 meter
    ## Image coordinate system
    stopDistanceImageThreshold: float = 8 # 8 pixels

    # Stop by speed
    useSpeedStopCondition: bool = True
    stopSpeedThreshold: float = 3
    stopTimeBySpeedThreshold: float = 120

    # Abnormal Movement
    useAbnormalMovementCondition: bool = True
    
    # Distance
    ## GEO coordinate system
    distanceMetersThreshold: float = 5
    ## Image coordinate system
    iouThreshold: float = 0.02

    alertTimeWindow: int = 10
    alertListTimeoutThreshold: int = 24 * 60 * 60
    maxNumberPastFrames: int = 30 * 2 * 60  # 2 minutes with fps 30
    
    # Ghost time threshold in milliseconds
    ghostTimeThresholdMs: int = 1000 * 15 # 15 seconds
    # Ghost object distance threshold in GEO coordinate system
    ghostDistanceMetersThreshold: float = 2.0 # 2 meters
    # Ghost object distance threshold in Image coordinate system
    ghostDistancePixelsThreshold: float = 20.0 # 20 pixels


class FallRiskConfig(BaseModel):
    """
    Configuration class for fall risk detection.

    This class defines parameters for detecting fall risks.

    :ivar bool enable: Whether to enable fall risk detection
    :ivar int nConfirmStart: Number of times confirm to start fall risk
    :ivar int nConfirmEnd: Number of times confirm to end fall risk

    Examples::
        >>> fall_risk_config = FallRiskConfig(
        ...     enable=True,
        ...     nConfirmStart=10,
        ...     nConfirmEnd=5*10
        ... )
        >>> print(fall_risk_config.enable)  # True
    """

    enable: bool = True
    nConfirmStart: int = 10
    nConfirmEnd: int = 50  # 5 × 10


class LackMovementConfig(BaseModel):
    """
    Configuration class for lack movement detection.

    This class defines parameters for detecting lack movements.

    :ivar bool enable: Whether to enable lack movement detection
    :ivar float durationThreshold: Duration threshold in seconds

    Examples::
        >>> lack_movement_config = LackMovementConfig(
        ...     enable=True,
        ...     durationThreshold=120.0
        ... )
        >>> print(lack_movement_config.enable)  # True
        >>> print(lack_movement_config.durationThreshold)  # 120.0
    """

    enable: bool = True
    durationThreshold: float = 120.0


class AnomalyActionConfig(BaseModel):
    """
    Configuration class for anomaly action detection.

    This class defines parameters for detecting anomaly actions.

    :ivar float ACTION_THRESHOLD: Threshold for anomaly action detection
    :ivar set[str] ignoreSensors: Set of sensors to ignore
    :ivar set[str] classes: Set of classes to detect anomalies for
    :ivar FallRiskConfig fallRisk: Fall risk configuration
    :ivar LackMovementConfig lackMovement: Lack movement configuration

    Examples::
        >>> anomaly_action_config = AnomalyActionConfig(
        ...     actionThreshold=0.5,
        ...     fallRisk=FallRiskConfig(
        ...         enable=True,
        ...         nConfirmStart=10,
        ...         nConfirmEnd=50
        ...     ),
        ...     lackMovement=LackMovementConfig(
        ...         enable=True,
        ...         durationThreshold=120.0
        ...     )
        ... )
        >>> print(anomaly_action_config.actionThreshold)  # 0.5
        >>> print(anomaly_action_config.fallRisk.enable)  # True
    """

    actionThreshold: float = 0.5
    ignoreSensors: set[str] = set()
    classes: set[str] = set()
    fallRisk: FallRiskConfig = FallRiskConfig()
    lackMovement: LackMovementConfig = LackMovementConfig()


class AnomalyConfig(BaseModel):
    """
    Configuration class for anomaly detection settings.

    This class combines various anomaly detection configurations.

    :ivar set[str] ignoreSensors: Set of sensors to ignore
    :ivar set[str] classes: Set of classes to detect anomalies for
    :ivar SpeedViolationConfig speedViolation: Speed violation configuration
    :ivar UnexpectedStopConfig unexpectedStop: Unexpected stop configuration
    :ivar AbnormalMovementConfig abnormalMovement: Abnormal movement configuration
    :ivar CollisionDetectionConfig collisionDetection: Collision detection configuration

    Examples::
        >>> anomaly_config = AnomalyConfig(
        ...     speedViolation=SpeedViolationConfig(
        ...         enable=True,
        ...         mphThreshold=90.0
        ...     )
        ... )
        >>> print(anomaly_config.speedViolation.enable)  # True
        >>> print(anomaly_config.speedViolation.mphThreshold)  # 90.0
    """

    ignoreSensors: set[str] = set()
    classes: set[str] = set()
    speedViolation: SpeedViolationConfig = SpeedViolationConfig()
    unexpectedStop: UnexpectedStopConfig = UnexpectedStopConfig()
    abnormalMovement: AbnormalMovementConfig = AbnormalMovementConfig()
    collisionDetection: CollisionDetectionConfig = CollisionDetectionConfig()


class ProximityDetectionConfig(BaseModel):
    """
    Configuration class for proximity detection settings.

    This class defines parameters for detecting proximity violations.

    :ivar bool enable: Whether to enable proximity detection
    :ivar float threshold: Threshold for proximity detection (distance in meters)
    :ivar set[str] centerClasses: Set of center object types (e.g., AMR, humanoid, etc.)
    :ivar set[str] surroundingClasses: Set of surrounding object types to check for proximity
    """

    enable: bool = True
    threshold: float = 1.8
    centerClasses: set[str] = set()
    surroundingClasses: set[str] = set()


class PlaybackConfig(BaseModel):
    """
    Configuration class for playback settings.

    This class defines parameters for data playback.

    :ivar set[str] sensors: Set of sensors to include in playback, if empty, all sensors will be used
    :ivar int loop: Number of times to loop the playback
    :ivar bool filterEmptyObjects: Whether to filter out empty objects
    :ivar bool inSimulationMode: Whether to use simulated time
    :ivar int startUpDelaySec: Delay before playback starts
    :ivar int simulationTimedeltaInMin: Time delta in minutes for simulation
    :ivar int simulationFps: Frames per second for simulation
    :ivar str simulationBaseTime: Base time for simulation

    Examples::
        >>> playback_config = PlaybackConfig(
        ...     loop=1,
        ...     filterEmptyObjects=True,
        ...     inSimulationMode=False,
        ...     startUpDelaySec=90,
        ...     simulationTimedeltaInMin=60,
        ...     simulationFps=1,
        ...     simulationBaseTime="2025-01-01T00:00:00.000000Z"
        ... )
        >>> print(playback_config.loop)  # 1
    """

    sensors: set[str] = set()
    loop: int = 1
    filterEmptyObjects: bool = True
    inSimulationMode: bool = False
    startUpDelaySec: int = 90
    simulationTimedeltaInMin: int = 60
    simulationFps: int = 1
    simulationBaseTime: str = ""


class InferenceConfig(BaseModel):
    """
    Configuration class for inference server settings.

    This class defines the configuration parameters for connecting to and
    using an inference server (such as Triton Inference Server).

    :ivar bool enable: Whether inference server integration is enabled
    :ivar str url: URL of the inference server

    Examples::
        >>> inference_config = InferenceConfig(
        ...     enable=True,
        ...     url="localhost:8001"
        ... )
        >>> print(inference_config.enable)  # True
        >>> print(inference_config.url)  # "localhost:8001"
    """

    enable: bool = False
    url: str = "localhost:8000"


class SpaceAnalyticsConfig(BaseModel):
    """
    Configuration class for space analytics settings.

    This class defines parameters for space analytics processing.

    :ivar float invocationIntervalSec: Interval between analytics invocations
    :ivar float gridSize: Size of the analysis grid
    :ivar float unsafeSize: Size threshold for unsafe areas
    :ivar set[str] targetObjects: Set of target object types
    :ivar bool useGA: Whether to use genetic algorithm
    :ivar int populationSizeGA: Size of genetic algorithm population
    :ivar int numGenerationsGA: Number of genetic algorithm generations

    Examples::
        >>> space_analytics = SpaceAnalyticsConfig(
        ...     invocationIntervalSec=5.0,
        ...     gridSize=0.2,
        ...     targetObjects=["person", "car"]
        ... )
        >>> print(space_analytics.gridSize)  # 0.2
        >>> print(space_analytics.targetObjects)  # ["person", "car"]
    """

    invocationIntervalSec: float = 5.0
    gridSize: float = 0.2
    unsafeSize: float = 0.5
    targetObjects: set[str] = set()
    useGA: bool = False
    populationSizeGA: int = 200
    numGenerationsGA: int = 300


class VideoEmbeddingConfig(BaseModel):
    """
    Configuration class for video embedding filtering and downsampling.

    This class defines parameters for per-sensor embedding downsampling (e.g. SDT or
    sliding window), including enableDownsampling flag, algorithm type, sensor TTL, and
    tolerance/window settings.

    :ivar bool enable_downsampling: Whether to run embedding downsampling (True) or pass-through (False); config name enableDownsampling
    :ivar str downsampler_type: Downsampler algorithm ("sdt" or "window")
    :ivar int sensor_ttl_sec: Sensor time-to-live in seconds before purge
    :ivar str downsample_tolerance_mode: Similarity metric ("distance" or "cosine")
    :ivar float downsample_similarity_threshold: Cosine similarity threshold (cosine mode)
    :ivar float downsample_distance_threshold: Euclidean distance threshold (distance mode)
    :ivar int downsample_max_interval_sec: Maximum interval in seconds before forced save
    :ivar int downsample_window_size: Sliding window size in points (window downsampler)
    :ivar int downsample_min_neighbours: Minimum consecutive similar neighbours to skip (window downsampler)

    Examples::
        >>> video_embed_config = VideoEmbeddingConfig(
        ...     enable_downsampling=True,
        ...     downsampler_type="window",
        ...     sensor_ttl_sec=3600
        ... )
        >>> print(video_embed_config.enable_downsampling)  # True
    """

    enable_downsampling: bool = False
    downsampler_type: str = EMBED_DOWNSAMPLER_TYPE
    sensor_ttl_sec: int = int(EMBED_SENSOR_TTL_SEC)
    downsample_tolerance_mode: str = EMBED_DOWNSAMPLE_TOLERANCE_MODE
    downsample_similarity_threshold: float = float(EMBED_DOWNSAMPLE_SIMILARITY_THRESHOLD)
    downsample_distance_threshold: float = float(EMBED_DOWNSAMPLE_DISTANCE_THRESHOLD)
    downsample_max_interval_sec: int = int(EMBED_DOWNSAMPLE_MAX_INTERVAL_SEC)
    downsample_window_size: int = int(EMBED_DOWNSAMPLE_WINDOW_SIZE)
    downsample_min_neighbours: int = int(EMBED_DOWNSAMPLE_MIN_NEIGHBOURS)


class AppConfig(BaseModel):
    """
    Main configuration class for the application. Once the config is set, it will be cached, and should not be changed.

    Note: This class uses @cache decorators on instance methods for performance. Since the number of config keys and sensor IDs is limited,
    memory usage is bounded. Creating multiple short-lived instances is supported but not recommended for long-running services.

    This class combines all configuration components for the application.

    :ivar AppKafkaConfig kafka: Kafka configuration
    :ivar list[AppSensorConfig] sensors: List of sensor configurations
    :ivar AppCoordinateReferenceSystemConfig coordinateReferenceSystem: Coordinate system configuration
    :ivar list[KeyValuePair] app: List of general application configurations
    :ivar InferenceConfig inference: Triton inference server configuration

    Examples::
        >>> config = AppConfig()
        >>> print(config.in_3d_mode)  # True
    """
    
    model_config = ConfigDict(extra="forbid")
    
    kafka: AppKafkaConfig = AppKafkaConfig()
    redisStream: AppRedisStreamConfig = AppRedisStreamConfig()
    mqtt: AppMQTTConfig = AppMQTTConfig()
    sensors: list[AppSensorConfig] = list()
    coordinateReferenceSystem: AppCoordinateReferenceSystemConfig = AppCoordinateReferenceSystemConfig()
    app: list[KeyValuePair] = list()
    inference: InferenceConfig = InferenceConfig()


    def __hash__(self) -> int:
        """
        Make AppConfig hashable based on object identity.
        
        This allows using @cache on instance methods.
        The hash is based on the object's identity (memory address),
        which remains constant for the object's lifetime.
        """
        return hash(id(self))

    def __eq__(self, other) -> bool:
        """Equality based on object identity for consistent hashing."""
        if not isinstance(other, AppConfig):
            return False
        return id(self) == id(other)

    @computed_field
    @cached_property
    def in_3d_mode(self) -> bool:
        """
        Get the 3D mode flag.

        :return bool: Whether the app is in 3D mode

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("in3dMode", "true")
            >>> print(config.in_3d_mode)  # True
        """
        in_3d_mode = self.get_app_config("in3dMode", IN_3D_MODE)
        if in_3d_mode.startswith("$") and (env_var_value := os.getenv(in_3d_mode[1:])):
            in_3d_mode = env_var_value
        return str_to_bool(in_3d_mode)

    @computed_field
    @cached_property
    def in_simulation_mode(self) -> bool:
        """Get the in simulation mode flag."""
        return self.get_bool_app_config("inSimulationMode", IN_SIMULATION_MODE)

    @computed_field
    @cached_property
    def use_object_location(self) -> bool:
        """
        Get the use object location flag.

        :return bool: Whether to use object location

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("useObjectLocation", "true")
            >>> print(config.use_object_location)  # True
        """
        return self.get_bool_app_config("useObjectLocation", USE_OBJECT_LOCATION)

    @computed_field
    @cached_property
    def compact_frame(self) -> bool:
        """
        Get the compact frame flag.

        :return bool: Whether to use compact frame format

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("compactFrame", "true")
            >>> print(config.compact_frame)  # True
        """
        return self.get_bool_app_config("compactFrame", COMPACT_FRAME)

    @computed_field
    @cached_property
    def image_location_mode(self) -> str:
        """
        Get the image location point calculation mode for CalibrationI (image coordinate system).

        This determines which point from the bounding box (px, py) is used to calculate the location.
        The calculated coordinate is then converted to latitude/longitude.

        :return str: The mode for calculating the location point from bbox in image coordinates. 
            - "center": Use center of bbox (center X, center Y)
            - "bottom_center": Use center of bbox bottom edge (center X, bottom Y)

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("imageLocationMode", "center")
            >>> print(config.image_location_mode)  # "center"
        """
        return self.get_app_config("imageLocationMode", IMAGE_LOCATION_MODE)

    @computed_field
    @cached_property
    def state_mgmt_filter(self) -> set[str]:
        """
        Get the object types to be filtered out in state management.

        :return set[str]: Set of object types to be filtered out

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("stateManagementFilter", '["car", "truck"]')
            >>> print(config.state_mgmt_filter)  # {"car", "truck"}
        """
        return set(json.loads(self.get_app_config("stateManagementFilter", default_value=STATE_MANAGEMENT_FILTER)))

    @computed_field
    @cached_property
    def behavior_state_timeout(self) -> int:
        """Get the behavior state timeout."""
        return int(self.get_app_config("behaviorStateTimeout", BEHAVIOR_STATE_TIMEOUT))

    @computed_field
    @cached_property
    def behavior_state_valid_interval(self) -> int:
        """Get the behavior state valid interval."""
        return int(self.get_app_config("behaviorStateValidInterval", BEHAVIOR_STATE_VALID_INTERVAL))

    @computed_field
    @cached_property
    def behavior_water_mark(self) -> int:
        """Get the behavior water mark."""
        return int(self.get_app_config("behaviorWatermarkSec", BEHAVIOR_WATERMARK_SEC))

    @computed_field
    @cached_property
    def behavior_time_threshold(self) -> datetime:
        """
        Get the behavior time threshold.

        Default is 1970-01-01T00:00:00.000Z. Only timestamps later than this won't be filtered out.

        :return datetime: Behavior time threshold

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("behaviorTimeThreshold", "2024-01-01T00:00:00.000Z")
            >>> print(config.behavior_time_threshold)  # 2024-01-01 00:00:00
        """
        return datetime.fromisoformat(
            self.get_app_config("behaviorTimeThreshold", BEHAVIOR_TIME_THRESHOLD).replace("Z", "+00:00")
        )

    @computed_field
    @cached_property
    def behavior_max_points(self) -> int:
        """Get the max behavior points stored."""
        return int(self.get_app_config("behaviorMaxPoints", BEHAVIOR_MAX_POINTS))

    @computed_field
    @cached_property
    def behavior_state_end_tolerance_sec(self) -> float:
        """Grace window (seconds) past state end within which late messages are accepted."""
        return float(self.get_app_config("behaviorStateEndToleranceSec", BEHAVIOR_STATE_END_TOLERANCE_SEC))

    @computed_field
    @cached_property
    def incident_object_ttl(self) -> float:
        """Get the TTL (time-to-live) in seconds for object presence data.
        
        Data older than this TTL will be cleaned up to prevent memory leaks.
        Objects with no data within the TTL window will be removed from 
        object_presence and object_ids.
        """
        return float(self.get_app_config("incidentObjectTtl", INCIDENT_OBJECT_TTL))

    @computed_field
    @cached_property
    def proximity_violation_incident_enable(self) -> bool:
        """Get the proximity violation incident enable."""
        return self.get_bool_app_config("proximityViolationIncidentEnable", PROXIMITY_VIOLATION_INCIDENT_ENABLE)

    @computed_field
    @cached_property
    def proximity_violation_incident_threshold(self) -> float:
        """Get the proximity violation incident threshold."""
        return float(self.get_app_config("proximityViolationIncidentThreshold", PROXIMITY_VIOLATION_INCIDENT_THRESHOLD))

    @computed_field
    @cached_property
    def proximity_violation_incident_expiration_window(self) -> float:
        """Get the proximity violation incident expiration window."""
        return float(self.get_app_config("proximityViolationIncidentExpirationWindow", PROXIMITY_VIOLATION_INCIDENT_EXPIRATION_WINDOW))

    @computed_field
    @cached_property
    def restricted_area_violation_incident_enable(self) -> bool:
        """Get the restricted area violation incident enable."""
        return self.get_bool_app_config("restrictedAreaViolationIncidentEnable", RESTRICTED_AREA_VIOLATION_INCIDENT_ENABLE)

    @computed_field
    @cached_property
    def restricted_area_violation_incident_threshold(self) -> float:
        """Get the restricted area violation incident threshold."""
        return float(self.get_app_config("restrictedAreaViolationIncidentThreshold", RESTRICTED_AREA_VIOLATION_INCIDENT_THRESHOLD))

    @computed_field
    @cached_property
    def restricted_area_violation_incident_expiration_window(self) -> float:
        """Get the restricted area violation incident expiration window."""
        return float(self.get_app_config("restrictedAreaViolationIncidentExpirationWindow", RESTRICTED_AREA_VIOLATION_INCIDENT_EXPIRATION_WINDOW))

    @computed_field
    @cached_property
    def confined_area_violation_incident_enable(self) -> bool:
        """Get the confined area violation incident enable."""
        return self.get_bool_app_config("confinedAreaViolationIncidentEnable", CONFINED_AREA_VIOLATION_INCIDENT_ENABLE)

    @computed_field
    @cached_property
    def confined_area_violation_incident_threshold(self) -> float:
        """Get the confined area violation incident threshold."""
        return float(self.get_app_config("confinedAreaViolationIncidentThreshold", CONFINED_AREA_VIOLATION_INCIDENT_THRESHOLD))
    
    @computed_field
    @cached_property
    def confined_area_violation_incident_expiration_window(self) -> float:
        """Get the confined area violation incident expiration window."""
        return float(self.get_app_config("confinedAreaViolationIncidentExpirationWindow", CONFINED_AREA_VIOLATION_INCIDENT_EXPIRATION_WINDOW))
    
    @computed_field
    @cached_property
    def fov_count_violation_incident_enable(self) -> bool:
        """Get the FOV count violation incident enable."""
        return self.get_bool_app_config("fovCountViolationIncidentEnable", FOV_COUNT_VIOLATION_INCIDENT_ENABLE)

    @computed_field
    @cached_property
    def fov_count_violation_incident_object_threshold(self) -> int:
        """Get the FOV count violation incident object threshold."""
        return int(self.get_app_config("fovCountViolationIncidentObjectThreshold", FOV_COUNT_VIOLATION_INCIDENT_OBJECT_THRESHOLD))

    @computed_field
    @cached_property
    def fov_count_violation_incident_threshold(self) -> float:
        """Get the FOV count violation incident threshold."""
        return float(self.get_app_config("fovCountViolationIncidentThreshold", FOV_COUNT_VIOLATION_INCIDENT_THRESHOLD))

    @computed_field
    @cached_property
    def fov_count_violation_incident_expiration_window(self) -> float:
        """Get the FOV count violation incident expiration window."""
        return float(self.get_app_config("fovCountViolationIncidentExpirationWindow", FOV_COUNT_VIOLATION_INCIDENT_EXPIRATION_WINDOW))

    @computed_field
    @cached_property
    def fov_count_violation_incident_object_type(self) -> str:
        """Get the FOV count violation incident object type."""
        return self.get_app_config("fovCountViolationIncidentObjectType", FOV_COUNT_VIOLATION_INCIDENT_OBJECT_TYPE)

    @computed_field
    @cached_property
    def cluster_threshold(self) -> float:
        """Get the cluster threshold."""
        return float(self.get_app_config("clusterThreshold", CLUSTER_THRESHOLD))

    @computed_field
    @cached_property
    def object_confidence_threshold(self) -> float:
        """Get the object confidence threshold."""
        return float(self.get_app_config("objectConfidenceThreshold", OBJECT_CONFIDENCE_THRESHOLD))

    @computed_field
    @cached_property
    def traj_geo_coord_enable(self) -> bool:
        """Get the app trajectory configuration."""
        return self.get_bool_app_config("trajGeoCoordEnable", GEO_COORD_ENABLE)
    
    @computed_field
    @cached_property
    def traj_direction_mode(self) -> int:
        """Get the app trajectory direction mode."""
        return int(self.get_app_config("trajDirectionMode", DIRECTION_MODE))
    
    @computed_field
    @cached_property
    def traj_direction_cluster_mode(self) -> int:
        """Get the app trajectory direction cluster mode."""
        return int(self.get_app_config("trajDirectionClusterMode", DIRECTION_CLUSTER_MODE))
    
    @computed_field
    @cached_property
    def traj_smooth_min_points(self) -> int:
        """Get the app trajectory smooth min points."""
        return int(self.get_app_config("trajSmoothMinPoints", SMOOTH_MIN_POINTS))
    
    @computed_field
    @cached_property
    def traj_smooth_window_size(self) -> int:
        """Get the app trajectory smooth window size."""
        return int(self.get_app_config("trajSmoothWindowSize", SMOOTH_WINDOW_SIZE))

    @computed_field
    @cached_property
    def traj_distance_stride(self) -> int:
        """Get the app trajectory distance stride."""
        return int(self.get_app_config("trajDistanceStride", DISTANCE_STRIDE))

    @computed_field
    @cached_property
    def traj_speed_segment_size(self) -> int:
        """Get the app trajectory speed segment size."""
        return int(self.get_app_config("trajSpeedSegmentSize", SPEED_SEGMENT_SIZE))

    @computed_field
    @cached_property
    def map_matching_max_points(self) -> int:
        """Get the app trajectory map matching max points."""
        return int(self.get_app_config("mapMatchingMaxPoints", MAP_MATCHING_MAX_POINTS))

    @computed_field
    @cached_property
    def map_matching_classes(self) -> set[str]:
        """Get the app map matching classes."""
        return set(json.loads(self.get_app_config("mapMatchingClasses", MAP_MATCHING_CLASSES)))

    @computed_field
    @cached_property
    def api_retry_max_count(self) -> float:
        """
        Get the maximum retry count for API calls.
        
        :return float: Maximum number of retries for API calls
        """
        return float(self.get_app_config("apiRetryMaxCnt", API_RETRY_MAX_CNT))
    
    @computed_field
    @cached_property
    def api_retry_max_time_seconds(self) -> float:
        """
        Get the maximum retry time in seconds for API calls.
        
        :return float: Maximum time in seconds for API retries
        """
        return float(self.get_app_config("apiRetryMaxTimeSec", API_RETRY_MAX_TIME_SEC))
    
    @computed_field
    @cached_property
    def api_mdx_base_url(self) -> str:
        """
        Get the base URL for MDX API.
        
        :return str: Base URL for MDX API
        """
        return self.get_app_config("apiMDX", API_MDX_BASE_URL)
    
    @computed_field
    @cached_property
    def api_ags_base_url(self) -> str:
        """Get the base URL for AGS API."""
        return self.get_app_config("apiAGS", API_AGS_BASE_URL)
    
    @computed_field
    @cached_property
    def usd_file_path(self) -> str:
        """Get the path to the USD file."""
        return self.get_app_config("usdFilePath", USD_FILE_PATH)

    @computed_field
    @cached_property
    def sink_type(self) -> str:
        """Get the sink type."""
        return self.get_app_config("sinkType", DEFAULT_SINK)

    @computed_field
    @cached_property
    def source_type(self) -> str:
        """Get the source type."""
        return self.get_app_config("sourceType", DEFAULT_SOURCE)

    @computed_field
    @cached_property
    def space_analytics(self) -> SpaceAnalyticsConfig:
        """Get space analytics configuration."""
        return SpaceAnalyticsConfig(
            invocationIntervalSec=float(self.get_app_config("spaceAnalyticsIntervalSec", INTERVAL_SEC)),
            gridSize=float(self.get_app_config("spaceAnalyticsGridSize", GRID_SIZE)),
            unsafeSize=float(self.get_app_config("spaceAnalyticsUnsafeSize", UNSAFE_SIZE)),
            targetObjects=set(json.loads(self.get_app_config("spaceAnalyticsTargetClasses", TARGET_CLASSES))),
            useGA=self.get_bool_app_config("spaceAnalyticsUseGA", USE_GA),
            populationSizeGA=int(self.get_app_config("spaceAnalyticsPopulationSizeGA", POPULATION_SIZE_GA)),
            numGenerationsGA=int(self.get_app_config("spaceAnalyticsNumGenerationsGA", NUM_GENERATIONS_GA))
        )

    @computed_field
    @cached_property
    def video_embedding(self) -> VideoEmbeddingConfig:
        """Get video embedding (downsampling) configuration."""
        conf_prefix = "embed"

        return VideoEmbeddingConfig(
            enable_downsampling=self.get_bool_app_config(f"{conf_prefix}EnableDownsampling", EMBED_DOWNSAMPLE_ENABLE),
            downsampler_type=self.get_app_config(f"{conf_prefix}DownsamplerType", EMBED_DOWNSAMPLER_TYPE),
            sensor_ttl_sec=int(self.get_app_config(f"{conf_prefix}SensorTTLSec", EMBED_SENSOR_TTL_SEC)),
            downsample_tolerance_mode=self.get_app_config(f"{conf_prefix}DownsampleToleranceMode", EMBED_DOWNSAMPLE_TOLERANCE_MODE),
            downsample_similarity_threshold=float(self.get_app_config(f"{conf_prefix}DownsampleSimilarityThreshold", EMBED_DOWNSAMPLE_SIMILARITY_THRESHOLD)),
            downsample_distance_threshold=float(self.get_app_config(f"{conf_prefix}DownsampleDistanceThreshold", EMBED_DOWNSAMPLE_DISTANCE_THRESHOLD)),
            downsample_max_interval_sec=int(self.get_app_config(f"{conf_prefix}DownsampleMaxIntervalSec", EMBED_DOWNSAMPLE_MAX_INTERVAL_SEC)),
            downsample_window_size=int(self.get_app_config(f"{conf_prefix}DownsampleWindowSize", EMBED_DOWNSAMPLE_WINDOW_SIZE)),
            downsample_min_neighbours=int(self.get_app_config(f"{conf_prefix}DownsampleMinNeighbours", EMBED_DOWNSAMPLE_MIN_NEIGHBOURS))
        )

    @computed_field
    @cached_property
    def playback(self) -> PlaybackConfig:
        """Get playback configuration."""
        return PlaybackConfig(
            sensors=set(json.loads(self.get_app_config("playbackSensors", PLAYBACK_SENSORS))),
            loop=int(self.get_app_config("playbackLoop", PLAYBACK_LOOP)),
            filterEmptyObjects=self.get_bool_app_config("playbackFilterEmptyObjects", FILTER_EMPTY_OBJECTS),
            inSimulationMode=self.get_bool_app_config("playbackInSimulationMode", IN_SIMULATION_MODE),
            startUpDelaySec=int(self.get_app_config("playbackStartUpDelaySec", STARTUP_DELAY_SEC)),
            simulationTimedeltaInMin=int(self.get_app_config("playbackSimulationTimedeltaInMin", SIMULATION_TIMEDELTA_MIN)),
            simulationFps=int(self.get_app_config("playbackSimulationFps", SIMULATION_FPS)),
            simulationBaseTime=self.get_app_config("playbackSimulationBaseTime", SIMULATION_BASE_TIME),
        )

    def _get_value_from_kv_pair_list(self, kv_pair_list: list[KeyValuePair], key: str, default_value: str = EMPTY_STR) -> str:
        """
        Get the value if we know the key from list of KeyValuePair.

        :param list[KeyValuePair] kv_pair_list: List of key-value pairs to search
        :param str key: Key to look up
        :param str default_value: Default value to return if key not found
        :return str: Value associated with the key, or default value if not found

        Examples::
            >>> config = AppConfig()
            >>> kv_pairs = [KeyValuePair(name="key1", value="value1")]
            >>> print(config._get_value_from_kv_pair_list(kv_pairs, "key1"))  # "value1"
            >>> print(config._get_value_from_kv_pair_list(kv_pairs, "key2", "default"))  # "default"
        """
        for kv_pair in kv_pair_list:
            if kv_pair.name == key:
                return kv_pair.value
        return default_value

    @cache
    def get_app_config(self, key: str, default_value: str = EMPTY_STR) -> str:
        """
        Get the value if we know the key from AppConfig.app.
        
        :param str key: Key to look up
        :param str default_value: Default value to return if key not found
        :return str: Value associated with the key, or default value if not found
        
        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("myKey", "myValue")
            >>> print(config.get_app_config("myKey"))  # "myValue"
            >>> print(config.get_app_config("nonexistent", "default"))  # "default"
        """
        return self._get_value_from_kv_pair_list(self.app, key, default_value)

    @cache
    def get_bool_app_config(self, key: str, default_value: str = FALSE_STR) -> bool:
        """
        Get a boolean value by key.

        :param str key: The key to look up
        :param str default_value: Default value if key not found
        :return bool: The boolean value or default

        Examples::
            >>> config = AppConfig()
            >>> print(config.get_bool_app_config("myKey", "false"))  # False
        """
        return str_to_bool(self.get_app_config(key, default_value))

    def set_app_config(self, key: str, value: str) -> None:
        """
        Set or update a configuration value in AppConfig.app. This method is used for test purpose only.

        :param str key: The configuration key to set
        :param str value: The value to set for the key
        :return: None

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("numProcesses", "4")
            >>> print(config.get_app_config("numProcesses"))  # "4"
            >>> config.set_app_config("numProcesses", "8")
            >>> print(config.get_app_config("numProcesses"))  # "8"
        """
        for kv_pair in self.app:
            if kv_pair.name == key:
                kv_pair.value = value
                return
        self.app.append(KeyValuePair(name=key, value=value))

    @cache
    def get_kafka_topic(self, key: str, default_value: str = EMPTY_STR) -> str:
        """
        Get a Kafka topic value by key.

        :param str key: The topic key to look up
        :param str default_value: Default value if key not found
        :return str: The topic value or default

        Examples::
            >>> config = AppConfig()
            >>> topic = config.get_kafka_topic("analyticsBehavior", "default-topic")
        """
        return self._get_value_from_kv_pair_list(self.kafka.topics, key, default_value)

    @cache
    def get_redis_stream(self, key: str, default_value: str = EMPTY_STR) -> str:
        """
        Get a Redis stream value by key.

        :param str key: The stream key to look up
        :param str default_value: Default value if key not found
        :return str: The stream value or default

        Examples::
            >>> config = AppConfig()
            >>> stream = config.get_redis_stream("analyticsBehavior", "default-stream")
        """
        return self._get_value_from_kv_pair_list(self.redisStream.streams, key, default_value)

    @cache
    def get_mqtt_topic(self, key: str, default_value: str = "") -> str:
        """
        Get the topic/topic pattern if we know the key of the mqtt topic config.

        :param str key: Key to look up
        :param str default_value: Default value to return if key not found
        :return str: Topic name associated with the key, or default value if not found

        Examples::
            >>> config = AppConfig()
            >>> config.mqtt.topics.append(KeyValuePair(name="input", value="input-topic"))
            >>> print(config.get_mqtt_topic("input"))  # "input-topic"
            >>> print(config.get_mqtt_topic("output", "default-topic"))  # "default-topic"
        """
        return self._get_value_from_kv_pair_list(self.mqtt.topics, key, default_value)

    @cache
    def get_sensor_config(self, key: str, default_value: str = EMPTY_STR, sensor_id: str = DEFAULT_SENSOR_ID) -> str:
        """
        Get the sensor config if we know the key of the sensor config and sensor_id.

        :param str key: Key to look up
        :param str default_value: Default value to return if key not found
        :param str sensor_id: ID of the sensor to get config for
        :return str: Configuration value associated with the key, or default value if not found

        Examples::
            >>> config = AppConfig()
            >>> config.set_sensor_config("resolution", "1920x1080", "camera1")
            >>> print(config.get_sensor_config("resolution", sensor_id="camera1"))  # "1920x1080"
            >>> print(config.get_sensor_config("fps", "30", "camera1"))  # "30"
        """
        default_config_value = EMPTY_STR
        for sensor_config in self.sensors:
            if sensor_config.id == DEFAULT_SENSOR_ID:
                default_config_value = self._get_value_from_kv_pair_list(sensor_config.configs, key, EMPTY_STR)
                break
        default_value = default_config_value if default_config_value else default_value
        for sensor_config in self.sensors:
            if sensor_config.id == sensor_id:
                return self._get_value_from_kv_pair_list(sensor_config.configs, key, default_value)
        return default_value

    def set_sensor_config(self, key: str, value: str, sensor_id: str = DEFAULT_SENSOR_ID) -> None:
        """
        Set or update a configuration value for a specific sensor. This method is used for test purpose only.

        :param str key: The configuration key to set
        :param str value: The value to set for the key
        :param str sensor_id: The ID of the sensor to set the config for
        :return: None

        Examples::
            >>> config = AppConfig()
            >>> config.set_sensor_config("resolution", "1920x1080", "camera1")
            >>> print(config.get_sensor_config("resolution", sensor_id="camera1"))  # "1920x1080"
            >>> config.set_sensor_config("resolution", "3840x2160", "camera1")
            >>> print(config.get_sensor_config("resolution", sensor_id="camera1"))  # "3840x2160"
        """
        sensor_config = None
        for existing_config in self.sensors:
            if existing_config.id == sensor_id:
                sensor_config = existing_config
                break

        if not sensor_config:
            sensor_config = AppSensorConfig(id=sensor_id)
            self.sensors.append(sensor_config)

        for kv_pair in sensor_config.configs:
            if kv_pair.name == key:
                kv_pair.value = value
                return

        sensor_config.configs.append(KeyValuePair(name=key, value=value))

    @cache
    def get_sensor_anomaly_config(self, sensor_id: str = DEFAULT_SENSOR_ID) -> AnomalyConfig:
        """
        Get anomaly detection configuration for a specific sensor.

        :param str sensor_id: ID of the sensor to get configuration for
        :return AnomalyConfig: Anomaly detection configuration for the sensor

        Examples::
            >>> config = AppConfig()
            >>> config = config.get_sensor_anomaly_config("sensor1")
            >>> print(f"Speed violation threshold: {config.speedViolation.mphThreshold}")
        """
        ignore_sensors = set(
            json.loads(
                self.get_sensor_config(key="anomalyIgnoreSensors", default_value=ANOMALY_IGNORE_SENSORS, sensor_id=sensor_id)
            )
        )
        classes = set(
            json.loads(
                self.get_sensor_config(key="anomalyClasses", default_value=ANOMALY_CLASSES, sensor_id=sensor_id)
            )
        )

        speed_violation = self.get_sensor_config(
            key="anomalySpeedViolation",
            default_value=SpeedViolationConfig().model_dump_json(),
            sensor_id=sensor_id,
        )

        unexpected_stop = self.get_sensor_config(
            key="anomalyUnexpectedStop",
            default_value=UnexpectedStopConfig().model_dump_json(),
            sensor_id=sensor_id,
        )

        abnormal_movement = self.get_sensor_config(
            key="anomalyAbnormalMovement",
            default_value=AbnormalMovementConfig().model_dump_json(),
            sensor_id=sensor_id,
        )

        collision_detection = self.get_sensor_config(
            key="anomalyCollisionDetection",
            default_value=CollisionDetectionConfig().model_dump_json(),
            sensor_id=sensor_id,
        )

        return AnomalyConfig(
            ignoreSensors=ignore_sensors,
            classes=classes,
            speedViolation=SpeedViolationConfig.model_validate_json(speed_violation),
            unexpectedStop=UnexpectedStopConfig.model_validate_json(unexpected_stop),
            abnormalMovement=AbnormalMovementConfig.model_validate_json(abnormal_movement),
            collisionDetection=CollisionDetectionConfig.model_validate_json(collision_detection),
        )

    @cache
    def get_sensor_anomaly_action_config(self, sensor_id: str = DEFAULT_SENSOR_ID) -> AnomalyActionConfig:
        """
        Get anomaly action detection configuration for a specific sensor.

        :param str sensor_id: ID of the sensor to get configuration for
        :return AnomalyActionConfig: Anomaly detection configuration for the sensor

        Examples::
            >>> config = AppConfig()
            >>> config = config.get_sensor_anomaly_action_config("sensor1")
            >>> print(f"Fall risk enable: {config.fallRisk.enable}")
        """
        ignore_sensors = set(
            json.loads(
                self.get_sensor_config(key="anomalyIgnoreSensors", default_value=ANOMALY_IGNORE_SENSORS, sensor_id=sensor_id)
            )
        )
        classes = set(
            json.loads(
                self.get_sensor_config(key="anomalyClasses", default_value=ANOMALY_CLASSES, sensor_id=sensor_id)
            )
        )
        action_threshold = float(
            self.get_sensor_config(key="anomalyActionThreshold", default_value=ANOMALY_ACTION_THRESHOLD, sensor_id=sensor_id)
        )
        fall_risk = self.get_sensor_config(
            key="anomalyFallRisk",
            default_value=FallRiskConfig().model_dump_json(),
            sensor_id=sensor_id,
        )
        lack_movement = self.get_sensor_config(
            key="anomalyLackMovement",
            default_value=LackMovementConfig().model_dump_json(),
            sensor_id=sensor_id,
        )
        return AnomalyActionConfig(
            ignoreSensors=ignore_sensors,
            classes=classes,
            actionThreshold=action_threshold,
            fallRisk=FallRiskConfig.model_validate_json(fall_risk),
            lackMovement=LackMovementConfig.model_validate_json(lack_movement),
        )

    @cache
    def get_sensor_proximity_detection_config(self, sensor_id: str) -> ProximityDetectionConfig:
        """
        Get the proximity detection configuration values from the config file.

        This method retrieves various configuration parameters for a sensor including:
        - Proximity detection settings
        - Object type restrictions

        :param str sensor_id: The sensor ID to get configuration for
        :return ProximityDetectionConfig: Proximity detection configuration

        Examples::
            >>> config = AppConfig()
            >>> proximity_config = config.get_sensor_proximity_detection_config("sensor1")
            >>> print(f"Proximity detection enabled: {proximity_config.enable}")
            >>> print(f"Proximity detection threshold: {proximity_config.threshold}")
            >>> print(f"Proximity detection center objects: {proximity_config.centerClasses}")
            >>> print(f"Proximity detection surrounding objects: {proximity_config.surroundingClasses}")
        """
        enable = str_to_bool(
            self.get_sensor_config(key="proximityDetectionEnable", default_value=PROXIMITY_DETECTION_ENABLE, sensor_id=sensor_id)
        )
        threshold = float(
            self.get_sensor_config(key="proximityDetectionThreshold", default_value=PROXIMITY_DETECTION_THRESHOLD, sensor_id=sensor_id)
        )
        center_classes = set(
            json.loads(
                self.get_sensor_config(key="proximityDetectionCenterClasses", default_value=PROXIMITY_DETECTION_CENTER_CLASSES, sensor_id=sensor_id)
            )
        )
        surrounding_classes = set(
            json.loads(
                self.get_sensor_config(key="proximityDetectionSurroundingClasses", default_value=PROXIMITY_DETECTION_SURROUNDING_CLASSES, sensor_id=sensor_id)
            )
        )
        return ProximityDetectionConfig(
            enable=enable,
            threshold=threshold,
            centerClasses=center_classes,
            surroundingClasses=surrounding_classes,
        )

    @cache
    def sensor_tripwire_min_points(self, sensor_id: str) -> int:
        """
        Get the tripwire min points for a specific sensor.

        :param str sensor_id: The ID of the sensor to get the tripwire min points for
        :return int: Minimum number of points required for tripwire

        Examples::
            >>> config = AppConfig()
            >>> config.set_sensor_config("tripwireMinPoints", "10", "camera1")
            >>> print(config.sensor_tripwire_min_points("camera1"))  # 10
            >>> print(config.sensor_tripwire_min_points("camera2"))  # 5 (default value)
        """
        return int(self.get_sensor_config(key="tripwireMinPoints", default_value=TRIPWIRE_MIN_POINTS, sensor_id=sensor_id))

    @cache
    def sensor_min_frames(self, sensor_id: str) -> int:
        """
        Get the sensor min frames.

        :param str sensor_id: Sensor ID to get the configuration for
        :return int: Minimum frames for the sensor

        Examples::
            >>> config = AppConfig()
            >>> min_frames = config.sensor_min_frames("camera1")
        """
        return int(self.get_sensor_config(key="sensorMinFrames", default_value=SENSOR_MIN_FRAMES, sensor_id=sensor_id))

    def invalidate_caches(self) -> None:
        """
        Clear every cached/derived value on this AppConfig instance.

        Call after mutating ``app`` or ``sensors`` so subsequent reads of cached
        properties or ``@cache``-wrapped methods re-evaluate against the new
        underlying state. Used by the dynamic-config flow when an upsert lands.

        Auto-detects three caching layers so future cached fields are picked up
        without updating an allow-list:

        * ``@cache`` / ``@lru_cache`` wrapped class-level callables -- cleared via
          their ``cache_clear()`` method.
        * Plain ``@cached_property`` descriptors -- their computed value lives in
          the instance ``__dict__`` and is popped.
        * Pydantic ``@computed_field`` (which decorates ``@cached_property``)
          -- cleared by name from ``model_computed_fields``.

        :return: None

        Examples::
            >>> config = AppConfig()
            >>> _ = config.in_3d_mode  # primes the cache
            >>> config.app.append(KeyValuePair(name="in3dMode", value="true"))
            >>> config.invalidate_caches()
            >>> print(config.in_3d_mode)  # True (re-evaluated)
        """

        cls = type(self)

        # Clear @cache / @lru_cache wrapped class-level callables.
        for name in dir(cls):
            attr = getattr(cls, name, None)
            if callable(getattr(attr, "cache_clear", None)):
                attr.cache_clear()

        # Clear plain @cached_property values from instance __dict__.
        for klass in cls.__mro__:
            for name, value in vars(klass).items():
                if isinstance(value, cached_property):
                    self.__dict__.pop(name, None)

        # Clear Pydantic @computed_field values (share cached_property semantics).
        for name in getattr(cls, "model_computed_fields", {}):
            self.__dict__.pop(name, None)

    def to_mutable_snapshot(self) -> dict[str, list[dict]]:
        """
        Return ``app`` + ``sensors`` as plain JSON-friendly dicts.

        Used by the dynamic-config listener to populate the ``config`` field
        of the outgoing ``ack`` payload (Flow A). Read-only sections
        (``kafka``, ``redisStream``, ``mqtt``, ``coordinateReferenceSystem``,
        ``inference``) are intentionally excluded -- the ack only confirms
        the dynamically-mutable subset.

        :return dict[str, list[dict]]: ``{"app": [...], "sensors": [...]}``.

        Examples::
            >>> config = AppConfig()
            >>> config.set_app_config("foo", "1")
            >>> snap = config.to_mutable_snapshot()
            >>> print(snap["app"])  # [{"name": "foo", "value": "1"}]
        """
        return {
            "app": [{"name": kv.name, "value": kv.value} for kv in self.app],
            "sensors": [
                {
                    "id": s.id,
                    "configs": [{"name": kv.name, "value": kv.value} for kv in s.configs],
                }
                for s in self.sensors
            ],
        }
