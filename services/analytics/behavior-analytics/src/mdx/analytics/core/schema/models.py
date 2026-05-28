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

from datetime import datetime
from typing import Any
from collections.abc import Mapping
from enum import StrEnum

import matplotlib.path as mplPath
from pydantic import BaseModel, Field, computed_field, ConfigDict


class IncidentCategory(StrEnum):
    """Categories of incidents that can be detected."""
    PROXIMITY_VIOLATION = "Proximity Violation"
    RESTRICTED_AREA_VIOLATION = "Restricted Area Violation"
    CONFINED_AREA_VIOLATION = "Confined Area Violation"
    FOV_COUNT_VIOLATION = "FOV Count Violation"

from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.utils.crp import Model
from mdx.analytics.core.utils.util import convert_datetime_to_iso_8601_with_z_suffix


class Location(BaseModel):
    """
    The geographical location of a point, mapping of protobuf Location.

    :ivar float lat: Latitude of the location
    :ivar float lon: Longitude of the location
    :ivar float alt: Altitude of the location

    Examples::
        # Create a location with latitude and longitude
        loc = Location(lat=40.7128, lon=-74.0060)  # New York City

        # Create a location with altitude
        loc_with_alt = Location(lat=40.7128, lon=-74.0060, alt=10.5)

        # Access location data
        print(f"Latitude: {loc.lat}, Longitude: {loc.lon}")
    """

    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0


class Segment(BaseModel):
    """
    Represents a segment of a road or path with start and end points, mapping of protobuf Segment.

    :ivar str | None id: Unique identifier for the segment
    :ivar str | None direction: Direction of movement along the segment
    :ivar Location start: Starting location of the segment
    :ivar Location end: Ending location of the segment
    :ivar list[Location] | None points: List of intermediate points along the segment

    Examples::

        # Create a segment between two locations
        start_loc = Location(lat=40.7128, lon=-74.0060)  # New York
        end_loc = Location(lat=40.7306, lon=-73.9352)    # Brooklyn
        segment = Segment(
            id="segment1",
            direction="north",
            start=start_loc,
            end=end_loc
        )

        # Add intermediate points
        mid_loc = Location(lat=40.7213, lon=-73.9706)
        segment.points.append(mid_loc)
    """

    id: str | None = None
    direction: str | None = None
    start: Location
    end: Location
    points: list[Location] | None = Field(default_factory=list)


class Intersection(BaseModel):
    """
    Represents an intersection with multiple segments, mapping of protobuf Intersection.

    :ivar str name: Name of the intersection
    :ivar list[Segment] segments: List of segments that meet at the intersection

    Examples::

        # Create an intersection with multiple segments
        segment1 = Segment(
            start=Location(lat=40.7128, lon=-74.0060),
            end=Location(lat=40.7306, lon=-73.9352)
        )
        segment2 = Segment(
            start=Location(lat=40.7128, lon=-74.0060),
            end=Location(lat=40.7589, lon=-73.9851)
        )

        intersection = Intersection(
            name="Times Square",
            segments=[segment1, segment2]
        )
    """

    name: str
    segments: list[Segment]


class Network(BaseModel):
    """
    Represents a network of intersections and segments, mapping of protobuf Network.

    :ivar list[Intersection] intersections: List of intersections in the network

    Examples::

        # Create a network with multiple intersections
        intersection1 = Intersection(
            name="Times Square",
            segments=[segment1, segment2]
        )
        intersection2 = Intersection(
            name="Union Square",
            segments=[segment3, segment4]
        )

        network = Network(
            intersections=[intersection1, intersection2]
        )

        # Access network information
        print(f"Network has {len(network.intersections)} intersections")
    """

    intersections: list[Intersection]


class Coordinate(BaseModel):
    """
    3D coordinate system representation, mapping of protobuf Coordinate.

    :ivar float x: X-coordinate
    :ivar float y: Y-coordinate
    :ivar float z: Z-coordinate, default is 0.0

    Examples::
        # Create a 2D coordinate (z=0)
        coord_2d = Coordinate(x=10.5, y=20.3)

        # Create a 3D coordinate
        coord_3d = Coordinate(x=10.5, y=20.3, z=5.0)

        # Access coordinates
        print(f"X: {coord_3d.x}, Y: {coord_3d.y}, Z: {coord_3d.z}")
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Point(BaseModel):
    """
    Represents a point, optionally as a list of coordinates, mapping of protobuf Point.

    :ivar list[float] point: List of coordinates representing a point

    Examples::
        # Create a 2D point
        point_2d = Point(point=[10.5, 20.3])

        # Create a 3D point
        point_3d = Point(point=[10.5, 20.3, 5.0])

        # Access point coordinates
        print(f"Coordinates: {point_3d.point}")
    """

    point: list[float] = Field(default_factory=list)


class Place(BaseModel):
    """
    Represents a named place with optional metadata, mapping of protobuf Place.

    :ivar str id: Unique identifier for the place
    :ivar str name: Name of the place
    :ivar str type: Type of place (e.g., "building", "park", "intersection")
    :ivar Location | None location: Geographic location of the place
    :ivar Coordinate | None coordinate: Coordinate position of the place
    :ivar dict[str, str] info: Additional place metadata

    Examples::

        # Create a place with location
        place = Place(
            id="nyc1",
            name="Times Square",
            type="intersection",
            location=Location(lat=40.7580, lon=-73.9855)
        )

        # Add metadata
        place.info["description"] = "Major commercial intersection"
        place.info["area"] = "Manhattan"
    """

    id: str = ""
    name: str = ""
    type: str = ""
    location: Location | None = None
    coordinate: Coordinate | None = None
    info: dict[str, str] = Field(default_factory=dict)


class GeoLocation(BaseModel):
    """
    Represents locations array, mapping of protobuf GeoLocation.
    For geo coordinates Array[Array[lon, lat]], for cartesian coordinates Array[Array[x,y]].

    :ivar str type: Type of coordinates ("linestring" for trajectory)
    :ivar list[Point] coordinates: List of points

    Examples::
        # Create a geographic trajectory
        geo_loc = GeoLocation(type="linestring")
        point1 = Point(point=[-74.0060, 40.7128])  # New York
        point2 = Point(point=[-73.9352, 40.7306])  # Brooklyn
        geo_loc.coordinates.extend([point1, point2])

        # Create a cartesian trajectory
        cart_loc = GeoLocation(type="linestring")
        point1 = Point(point=[0, 0])
        point2 = Point(point=[1, 1])
        cart_loc.coordinates.extend([point1, point2])
    """

    type: str = ""
    coordinates: list[Point] = Field(default_factory=list)


class Embedding(BaseModel):
    """
    Represents an embedding vector, mapping of protobuf Embedding.

    :ivar list[float] vector: The embedding vector values
    :ivar dict[str, str] info: Extra info if needed

    Examples::

        # Create a face embedding
        face_embedding = Embedding(
            vector=[0.1, 0.2, 0.3, 0.4, 0.5]
        )

        # Access embedding properties
        print(f"Vector length: {len(face_embedding.vector)}")
    """

    vector: list[float] = Field(default_factory=list)
    info: dict[str, str] = Field(default_factory=dict)


class Bbox(BaseModel):
    """
    Represents a bounding box in a 2D space, mapping of protobuf Bbox.

    :ivar float leftX: Left X-coordinate of the bounding box
    :ivar float topY: Top Y-coordinate of the bounding box
    :ivar float rightX: Right X-coordinate of the bounding box
    :ivar float bottomY: Bottom Y-coordinate of the bounding box

    Examples::

        # Create a bounding box
        bbox = Bbox(
            leftX=100,
            topY=100,
            rightX=200,
            bottomY=200
        )

        # Calculate width and height
        width = bbox.rightX - bbox.leftX
        height = bbox.bottomY - bbox.topY
        print(f"Width: {width}, Height: {height}")
    """

    leftX: float = 0.0
    topY: float = 0.0
    rightX: float = 0.0
    bottomY: float = 0.0


class Bbox3d(BaseModel):
    """
    Represents a bounding box in 3D space with embeddings and confidence, mapping of protobuf Bbox3d.

    :ivar list[float] coordinates: Values in order: x, y, z, width, length, height, pitch, roll, yaw, vx, vy, vz
    :ivar list[Embedding] embeddings: Embeddings representing the characteristics as seen in 3d bbox
    :ivar float confidence: Confidence score of the bounding box
    :ivar dict[str, str] info: Extra info if needed

    Examples::

        # Create a 3D bounding box
        bbox3d = Bbox3d(
            coordinates=[10.0, 20.0, 5.0,  # x, y, z
                        2.0, 3.0, 1.5,     # width, length, height
                        0.0, 0.0, 45.0,    # pitch, roll, yaw
                        1.0, 0.0, 0.0],    # vx, vy, vz
            confidence=0.95
        )

        # Add an embedding
        embedding = Embedding(vector=[0.1, 0.2, 0.3])
        bbox3d.embeddings.append(embedding)
    """

    coordinates: list[float] = Field(default_factory=list)
    embeddings: list[Embedding] = Field(default_factory=list)
    confidence: float = 0.0
    info: dict[str, str] = Field(default_factory=dict)


class Event(BaseModel):
    """
    Represents an event with type and metadata, mapping of protobuf Event.

    :ivar str id: Unique identifier for the event
    :ivar str type: Type of event (e.g., "movement", "parking", "interaction")
    :ivar dict[str, str] info: Additional event metadata

    Examples::

        # Create a movement event
        event = Event(
            id="event1",
            type="movement",
            info={
                "speed": "5.0",
                "direction": "north"
            }
        )

        # Create a parking event
        parking_event = Event(
            id="park1",
            type="parking",
            info={
                "duration": "2h",
                "location": "lot1"
            }
        )
    """

    id: str = ""
    type: str = ""
    info: dict[str, str] = Field(default_factory=dict)


class AnalyticsModule(BaseModel):
    """
    Represents a module performing analytics, mapping of protobuf AnalyticsModule.

    :ivar str id: Unique identifier for the module
    :ivar str description: Description of the module's functionality
    :ivar str source: Source of the module
    :ivar str version: Version of the module
    :ivar dict[str, str] info: Additional module metadata

    Examples::

        # Create an analytics module
        module = AnalyticsModule(
            id="face_detection",
            description="Detects and tracks faces in video streams",
            source="internal",
            version="1.2.0"
        )

        # Add module metadata
        module.info["input_type"] = "video"
        module.info["output_type"] = "bounding_boxes"
        module.info["performance"] = "30fps"
    """

    id: str = ""
    description: str = ""
    source: str = ""
    version: str = ""
    info: dict[str, str] = Field(default_factory=dict)


class Action(BaseModel):
    """
    Represents an action with its type and confidence level, mapping of protobuf Action.

    :ivar str type: Type of action (e.g., "walking", "running", "standing")
    :ivar float confidence: Confidence score of the action prediction

    Examples::

        # Create an action
        action = Action(
            type="walking",
            confidence=0.85
        )

        # Check if action confidence is above threshold
        if action.confidence > 0.8:
            print(f"Detected {action.type} with high confidence")
    """

    type: str = ""
    confidence: float = 0.0


class Keypoint(BaseModel):
    """
    Keypoint + quaternion, each representing a body part, mapping of protobuf Keypoint.

    :ivar str name: Name of the keypoint
    :ivar list[float] coordinates: List of (x, y, z, confidence)
    :ivar list[float] quaternion: List of (qx, qy, qz, qw)

    Examples::

        # Create a 2D keypoint
        keypoint_2d = Keypoint(
            name="head",
            coordinates=[100, 100, 0.0, 0.9]  # x, y, z, confidence
        )

        # Create a 3D keypoint with quaternion
        keypoint_3d = Keypoint(
            name="shoulder",
            coordinates=[100, 100, 50, 0.8],  # x, y, z, confidence
            quaternion=[0.0, 0.0, 0.0, 1.0]   # qx, qy, qz, qw
        )
    """

    name: str = ""
    coordinates: list[float] = Field(default_factory=list)
    quaternion: list[float] = Field(default_factory=list)


class Pose(BaseModel):
    """
    Represents the nature of a pose, mapping of protobuf Pose.

    :ivar str type: Type of pose: pose2D, pose3D, or pose25D
    :ivar list[Keypoint] keypoints: A list of keypoints representing body parts
    :ivar list[Action] actions: Actions associated with the pose and confidence levels
    :ivar dict[str, str] info: Additional information about the pose

    Examples::

        # Create a 2D pose with keypoints
        pose = Pose(
            type="pose2D",
            keypoints=[
                Keypoint(
                    name="head",
                    coordinates=[100, 100, 0.9]  # x, y, confidence
                ),
                Keypoint(
                    name="left_shoulder",
                    coordinates=[90, 120, 0.8]
                )
            ]
        )

        # Add an action
        action = Action(type="standing", confidence=0.95)
        pose.actions.append(action)
    """

    type: str = ""
    keypoints: list[Keypoint] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    info: dict[str, str] = Field(default_factory=dict)


class Gaze(BaseModel):
    """
    Gaze point of reference, mapping of protobuf Gaze.

    :ivar float x: X-coordinate
    :ivar float y: Y-coordinate
    :ivar float z: Z-coordinate
    :ivar float theta: Horizontal angle
    :ivar float phi: Vertical angle

    Examples::

        # Create a gaze point
        gaze = Gaze(
            x=100,      # X-coordinate
            y=100,      # Y-coordinate
            z=0,        # Z-coordinate
            theta=45.0, # Horizontal angle in degrees
            phi=30.0    # Vertical angle in degrees
        )

        # Access gaze information
        print(f"Gaze direction: {gaze.theta}° horizontal, {gaze.phi}° vertical")
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    theta: float = 0.0
    phi: float = 0.0


class LipActivity(BaseModel):
    """
    Represents lip activity information, mapping of protobuf LipActivity.

    :ivar str classLabel: Type of lip activity (e.g., "speaking", "silent")

    Examples::

        # Create a lip activity detection
        lip_activity = LipActivity(
            classLabel="speaking"
        )

        # Access lip activity information
        print(f"Detected {lip_activity.classLabel}")
    """

    classLabel: str = ""


class Object(BaseModel):
    """
    Represents an object detected in a frame, mapping of protobuf Object.

    This class encapsulates all information about a detected object, including its spatial properties,
    visual characteristics, and behavioral attributes.

    :ivar str id: Unique identifier for the object
    :ivar str type: Type of object (e.g., "person", "car", "bicycle")
    :ivar float confidence: Confidence score of the detection
    :ivar Bbox bbox: 2D bounding box of the object
    :ivar Bbox3d bbox3d: 3D bounding box of the object
    :ivar Pose | None pose: Pose information of the object
    :ivar Gaze | None gaze: Gaze information of the object
    :ivar Embedding | None embedding: Embedding vector associated with the object
    :ivar LipActivity | None lipActivity: Lip activity information
    :ivar float speed: Speed of the object
    :ivar list[float] dir: Direction vector of the object
    :ivar Coordinate coordinate: 3D coordinate position
    :ivar Location | None location: Geographic location
    :ivar dict[str, str] info: Additional object metadata

    Examples::

        # Create a person object with 2D bbox
        person = Object(
            id="person1",
            type="person",
            confidence=0.95,
            bbox=Bbox(leftX=100, topY=100, rightX=200, bottomY=200)
        )

        # Add 3D information
        person.bbox3d = Bbox3d(
            coordinates=[10.0, 20.0, 5.0, 2.0, 3.0, 1.5, 0.0, 0.0, 45.0, 1.0, 0.0, 0.0],
            confidence=0.9
        )

        # Add pose information
        person.pose = Pose(
            type="pose2D",
            keypoints=[
                Keypoint(name="head", coordinates=[100, 100, 0.9])
            ]
        )

        # Add movement information
        person.speed = 2.5
        person.dir = [0.707, 0.707]  # 45-degree direction vector
        person.coordinate = Coordinate(x=10.0, y=20.0, z=5.0)
    """

    id: str
    confidence: float = 0.0
    bbox: Bbox = Field(default_factory=Bbox)
    bbox3d: Bbox3d = Field(default_factory=Bbox3d)
    type: str = ""
    info: dict[str, str] = Field(default_factory=dict)
    embedding: Embedding | None = None
    pose: Pose | None = None
    gaze: Gaze | None = None
    lipActivity: LipActivity | None = None
    speed: float = 0.0
    dir: list[float] = Field(default_factory=list)
    coordinate: Coordinate = Field(default_factory=Coordinate)
    location: Location | None = None


class Sensor(BaseModel):
    """
    Represents a sensor with location and metadata information, mapping of protobuf Sensor.

    :ivar str id: Unique identifier for the sensor
    :ivar str type: Type of sensor (e.g., "camera", "lidar", "radar")
    :ivar str description: Description of the sensor
    :ivar Location | None location: Geographic location of the sensor
    :ivar Coordinate | None coordinate: Coordinate position of the sensor
    :ivar dict[str, str] info: Additional sensor metadata

    Examples::

        # Create a camera sensor
        sensor = Sensor(
            id="cam1",
            type="camera",
            description="Front entrance camera",
            location=Location(lat=40.7128, lon=-74.0060),
            coordinate=Coordinate(x=100, y=200, z=10)
        )

        # Add metadata
        sensor.info["resolution"] = "1920x1080"
        sensor.info["fps"] = "30"
    """

    id: str
    type: str = ""
    description: str = ""
    location: Location | None = None
    coordinate: Coordinate | None = None
    info: dict[str, str] = Field(default_factory=dict)


class Message(BaseModel):
    """
    Represents a single object detection message, mapping of protobuf Message.

    :ivar str messageid: Unique identifier for the message
    :ivar datetime timestamp: Timestamp of the message
    :ivar Sensor sensor: Sensor that generated the message
    :ivar Object | None object: Detected object in the message
    :ivar str mdsversion: Version of the message format
    :ivar Place place: Place information associated with the message
    :ivar AnalyticsModule | None analyticsModule: Analytics module that processed the message
    :ivar Event | None event: Event information associated with the message
    :ivar str videoPath: Path to related video file

    Examples::

        # Create a message with object detection
        sensor = Sensor(
            id="cam1",
            type="camera",
            location=Location(lat=40.7128, lon=-74.0060)
        )

        person = Object(
            id="person1",
            type="person",
            confidence=0.95,
            bbox=Bbox(leftX=100, topY=100, rightX=200, bottomY=200)
        )

        message = Message(
            messageid="msg1",
            timestamp=datetime.now(),
            sensor=sensor,
            object=person,
            mdsversion="1.0",
            place=Place(name="Entrance"),
            videoPath="video1.mp4"
        )

        # Access message information
        print(f"Message from {message.sensor.id} at {message.timestamp}")
        print(f"Detected {message.object.type} with confidence {message.object.confidence}")
    """

    messageid: str
    timestamp: datetime
    sensor: Sensor
    object: Object | None = None
    mdsversion: str = ""
    place: Place = Field(default_factory=Place)
    analyticsModule: AnalyticsModule | None = None
    event: Event | None = None
    videoPath: str = ""

    class Config:
        # Custom encoder for datetime
        json_encoders = {datetime: convert_datetime_to_iso_8601_with_z_suffix}


class TypeMetrics(BaseModel):
    """
    Represents metrics for a specific type of object, mapping of protobuf TypeMetrics.

    :ivar str id: Unique identifier for the metrics
    :ivar str type: Type of object being measured
    :ivar int count: Count of objects of this type
    :ivar list[Coordinate] coordinates: List of coordinates associated with the metrics
    :ivar list[str] objectIds: List of object IDs associated with the metrics
    :ivar dict[str, str] info: Additional metrics information

    Examples::

        # Create metrics for people in a region of interest
        roi_metrics = TypeMetrics(
            id="roi1",
            type="person",
            count=5,
            coordinates=[
                Coordinate(x=100, y=100),
                Coordinate(x=150, y=150)
            ],
            objectIds=["person1", "person2", "person3"]
        )

        # Access metrics information
        print(f"Found {roi_metrics.count} {roi_metrics.type}s in {roi_metrics.id}")
    """

    id: str = ""
    type: str = ""
    count: int = 0
    coordinates: list[Coordinate] = Field(default_factory=list)
    objectIds: list[str] = Field(default_factory=list)
    info: dict[str, str] = Field(default_factory=dict)


class Interaction(BaseModel):
    """
    Represents an interaction between objects, mapping of protobuf Interaction.

    This class encapsulates information about interactions between objects, including their spatial relationships,
    temporal characteristics, and additional metadata about the interaction.

    :ivar str id: Unique identifier for the interaction
    :ivar list[str] objectIds: List of object IDs involved in the interaction
    :ivar list[Coordinate] coordinates: List of coordinates where the interaction occurred
    :ivar str description: Description of the interaction
    :ivar dict[str, str] info: Additional interaction metadata

    Examples::

        # Create an interaction between two people
        interaction = Interaction(
            id="interaction1",
            objectIds=["person1", "person2"],
            coordinates=[
                Coordinate(x=100, y=100, z=0),
                Coordinate(x=150, y=150, z=0)
            ],
            description="Two people meeting at the entrance"
        )

        # Add metadata about the interaction
        interaction.info["duration"] = "30s"
        interaction.info["type"] = "meeting"

        # Access interaction information
        print(f"Interaction {interaction.id} between {len(interaction.objectIds)} objects")
        print(f"Description: {interaction.description}")
    """

    id: str = ""
    objectIds: list[str] = Field(default_factory=list)
    coordinates: list[Coordinate] = Field(default_factory=list)
    description: str = ""
    info: dict[str, str] = Field(default_factory=dict)


class Point2D(BaseModel):
    """
    Represents a 2D point with x and y coordinates, mapping of protobuf Point2D.

    :ivar float x: X-coordinate of the point
    :ivar float y: Y-coordinate of the point

    Examples::
        # Create a 2D point
        point = Point2D(x=100.5, y=200.3)

        # Create a point for ROI coordinates
        roi_point = Point2D(x=150, y=175)

        # Access point coordinates
        print(f"Point at ({point.x}, {point.y})")
    """

    x: float
    y: float


class Cluster(BaseModel):
    """
    Represents a cluster of points in space, mapping of protobuf Cluster.

    :ivar list[Point2D] points: List of 2D points in the cluster

    Examples::

        # Create a cluster of points
        cluster = Cluster(
            points=[
                Point2D(x=100, y=100),
                Point2D(x=110, y=110),
                Point2D(x=120, y=120)
            ]
        )

        # Calculate cluster size
        print(f"Cluster has {len(cluster.points)} points")
    """

    points: list[Point2D] = Field(default_factory=list)


class SD(BaseModel):
    """
    Represents social distancing metrics, mapping of protobuf SD.

    :ivar float threshold: Distance threshold for social distancing
    :ivar int proximityDetections: Number of proximity detections
    :ivar list[Cluster] clusters: List of clusters representing groups

    Examples::

        # Create social distancing metrics
        sd = SD(
            threshold=1.5,  # meters
            proximityDetections=3,
            clusters=[
                Cluster(
                    points=[Point2D(x=100, y=100), Point2D(x=110, y=110)],
                    density=0.9
                )
            ]
        )

        # Access social distancing information
        print(f"Found {sd.proximityDetections} violations of {sd.threshold}m threshold")
        print(f"Number of clusters: {len(sd.clusters)}")
    """

    threshold: float = 0.0
    proximityDetections: int = 0
    clusters: list[Cluster] = Field(default_factory=list)


class Segmentation(BaseModel):
    """
    Represents panoptic segmentation information, mapping of protobuf Segmentation.

    :ivar list[int] mask: Segmentation mask data
    :ivar dict[str, str] info: Additional segmentation information

    Examples::

        # Create a panoptic segmentation mask
        segmentation = Segmentation(
            mask=[0, 0, 1, 1, 2, 2, 1, 1]  # Simplified mask data
        )
    """

    mask: list[int] = Field(default_factory=list)
    info: dict[str, str] = Field(default_factory=dict)


class Congestion(BaseModel):
    """
    Represents congestion information in a region, mapping of protobuf Congestion.

    This class encapsulates information about congestion in a specific region, including the objects involved,
    the spatial extent of the congestion, and additional metadata about the congestion state.

    :ivar str id: Unique identifier for the congestion region
    :ivar list[str] objectIds: List of object IDs involved in the congestion
    :ivar float amount: Amount or severity of congestion (e.g., density, count, or percentage)
    :ivar dict[str, str] info: Additional congestion metadata

    Examples::

        # Create a congestion region with objects
        congestion = Congestion(
            id="zone1",
            objectIds=["person1", "person2", "person3"],
            amount=0.75  # 75% congestion
        )

        # Add metadata about the congestion
        congestion.info["type"] = "pedestrian"
        congestion.info["duration"] = "30s"
        congestion.info["area"] = "entrance"

        # Access congestion information
        print(f"Congestion in {congestion.id}")
        print(f"Number of objects: {len(congestion.objectIds)}")
        print(f"Congestion amount: {congestion.amount}")
    """

    id: str = ""
    objectIds: list[str] = Field(default_factory=list)
    amount: float = 0.0
    info: dict[str, str] = Field(default_factory=dict)


class Frame(BaseModel):
    """
    Represents a frame in a video sequence, mapping of protobuf Frame.

    This class encapsulates all information about a single frame, including detected objects, field of view metrics,
    region of interest analysis, social distancing measurements, segmentation data, object interactions, and congestion
    information.

    :ivar str version: Version of the frame data format
    :ivar str id: Unique identifier for the frame
    :ivar datetime timestamp: Timestamp of the frame
    :ivar str sensorId: ID of the sensor that captured the frame
    :ivar list[Object] objects: List of objects detected in the frame
    :ivar list[TypeMetrics] fov: Field of view metrics for different object types
    :ivar list[TypeMetrics] rois: Region of interest based object counts
    :ivar SD | None socialDistancing: Social distancing metrics
    :ivar Segmentation | None segmentation: Panoptic segmentation mask
    :ivar list[Interaction] interactions: List of interactions between objects
    :ivar list[Congestion] congestions: List of congestion information
    :ivar dict[str, str] info: Additional frame metadata

    Examples::

        # Create a frame with basic information
        frame = Frame(
            version="1.0",
            id="frame1",
            timestamp=datetime.now(),
            sensorId="cam1"
        )

        # Add detected objects
        person = Object(
            id="person1",
            type="person",
            confidence=0.95,
            bbox=Bbox(leftX=100, topY=100, rightX=200, bottomY=200)
        )
        frame.objects.append(person)

        # Add FOV metrics
        fov_metrics = TypeMetrics(
            type="person",
            count=5,
            coordinates=[Coordinate(x=100, y=100)]
        )
        frame.fov.append(fov_metrics)

        # Add ROI metrics
        roi_metrics = TypeMetrics(
            type="vehicle",
            count=3,
            coordinates=[Coordinate(x=200, y=200)]
        )
        frame.rois.append(roi_metrics)

        # Add social distancing information
        frame.socialDistancing = SD(
            threshold=1.5,
            proximityDetections=2
        )

        # Add congestion information
        congestion = Congestion(
            id="zone1",
            objectIds=["person1", "person2"],
            amount=0.6
        )
        frame.congestions.append(congestion)

        # Access frame information
        print(f"Frame {frame.id} from sensor {frame.sensorId}")
        print(f"Detected {len(frame.objects)} objects")
        print(f"Found {len(frame.congestions)} congestion zones")
    """

    version: str
    id: str
    timestamp: datetime
    sensorId: str
    objects: list[Object] = Field(default_factory=list)
    fov: list[TypeMetrics] = Field(default_factory=list)
    rois: list[TypeMetrics] = Field(default_factory=list)
    socialDistancing: SD | None = None
    segmentation: Segmentation | None = None
    interactions: list[Interaction] = Field(default_factory=list)
    congestions: list[Congestion] = Field(default_factory=list)
    info: dict[str, str] = Field(default_factory=dict)


class ObjectState(BaseModel):
    """
    Represents the state of an object over time, including its coordinates, type scores, etc.

    :ivar str id: Unique identifier for the object state
    :ivar datetime start: Start time of the state
    :ivar datetime end: End time of the state
    :ivar list[Coordinate] points: List of coordinate points
    :ivar int sampling: Sampling stride (1-in-N kept from raw observations)
    :ivar int sample_phase: Phase for the next incoming raw observation, carried across batches for true 1-in-N sampling
    :ivar list[datetime] tail_ts: Timestamps aligned to points[-len(tail_ts):]; used to bisect-insert tolerance-window messages while sampling == 1
    :ivar list[Coordinate] lastXpoints: Last X points for trajectory analysis
    :ivar Object | None object: Object associated with this state
    :ivar Model | None model: Model for clustering embeddings

    Examples::

        # Create an object state with trajectory points
        points = [
            Coordinate(x=0, y=0, z=0),
            Coordinate(x=1, y=1, z=0),
            Coordinate(x=2, y=2, z=0)
        ]

        person = Object(
            id="person1",
            type="person",
            confidence=0.95
        )

        state = ObjectState(
            id="state1",
            start=datetime.now(),
            end=datetime.now() + timedelta(seconds=2),
            points=points,
            sampling=1,
            object=person,
        )

        # Access state information
        print(f"Object {state.id} moved {len(state.points)} points")
        print(f"Time interval: {state.time_interval} seconds")
    """

    id: str
    start: datetime
    end: datetime
    points: list[Coordinate] = Field(default_factory=list)
    sampling: int = 1
    sample_phase: int = 0
    tail_ts: list[datetime] = Field(default_factory=list)
    lastXpoints: list[Coordinate] = Field(default_factory=list)
    object: Object | None = None
    model: Model | None = None

    @computed_field
    @property
    def time_interval(self) -> float:
        """The time interval (in seconds) between start and end."""
        return (self.end - self.start).total_seconds()

    class Config:
        # Allows the Pydantic BaseModel to include fields with custom types
        arbitrary_types_allowed = True
        # Custom encoder for datetime
        json_encoders = {datetime: convert_datetime_to_iso_8601_with_z_suffix}


class IncidentState(BaseModel):
    """
    State of an incident (violation).

    :ivar str sensor_id: ID of the sensor that detected the incident.
    :ivar str primary_object_id: ID of the primary object involved in the incident.
    :ivar list[str] object_ids: List of object IDs involved in the incident.
    :ivar dict[str, list[dict[str, datetime]]] object_presence: Map of object IDs to their presence intervals.
    :ivar datetime start: Start time of the incident.
    :ivar datetime end: End time of the incident.
    :ivar IncidentCategory category: Category of the incident.
    :ivar dict[str, Any] info: Additional information about the incident.
    """
    sensor_id: str
    primary_object_id: str
    object_ids: list[str]
    # Map each object to one or more (start, end) intervals when it was present during the violation.
    object_presence: dict[str, list[dict[str, datetime]]] = Field(default_factory=dict)
    start: datetime
    end: datetime
    category: IncidentCategory
    info: dict[str, Any] = Field(default_factory=dict)

    @computed_field(return_type=float)
    @property
    def time_interval(self) -> float:
        return (self.end - self.start).total_seconds()


class AmrState(BaseModel):
    """
    State of AMR states over time.

    :ivar str id: OCR ID for AMR tracking.
    :ivar str roi_id: ROI ID for AMR tracking.
    :ivar str sensor_id: Sensor ID for AMR tracking.
    :ivar str object_id: Object ID for AMR tracking.
    :ivar bool | None mute: Mute state for AMR. True means AMR running in high speed.
    :ivar bool mute_state_changed: Flag to indicate if the mute state has changed.

    Examples::
        >>> amr_state = AmrState(id="AMR001", roi_id="roi1", sensor_id="sensor1", object_id="obj1")
        >>> print(f"Created AMR state with {amr_state.id}")
    """

    id: str  # ocr id
    roi_id: str
    sensor_id: str
    object_id: str  # object id
    mute: bool | None = None
    mute_state_changed: bool = False


class RoiState(BaseModel):
    """
    State of ROI states over time.

    :ivar str id: ROI ID for ROI tracking.
    :ivar str sensor_id: Sensor ID for ROI tracking.
    :ivar bool | None mute: Mute state for ROI. True means AMRs in the ROI running in high speed.
    :ivar bool mute_state_changed: Flag to indicate if the mute state has changed.

    Examples::
        >>> roi_state = RoiState(id="roi1", sensor_id="sensor1")
        >>> print(f"Created ROI state with {roi_state.id}")
    """

    id: str  # roi id
    sensor_id: str
    mute: bool | None = None
    mute_state_changed: bool = False


class FrameState(BaseModel):
    """
    State of sensor frames over time.

    This class provides functionality for:
    - Frame state management and tracking
    - Frame history maintenance (keeps last N frames per sensor)
    - Incident state tracking for various violation types

    :ivar str id: Sensor ID for frame tracking.
    :ivar list[nvSchema.Frame] last_x_frames: List of the last X frames for the sensor.
    :ivar dict[str, IncidentState] safety_violation_states: Proximity violations, keyed by primary_object_id.
    :ivar dict[str, IncidentState] restricted_area_violation_states: Restricted area violations, keyed by primary_object_id.
    :ivar dict[str, IncidentState] confined_area_violation_states: Confined area violations, keyed by primary_object_id.
    :ivar IncidentState | None fov_count_violation_state: FOV count violation state for this sensor.
    :ivar dict[str, AmrState] amr_states: AMR states, keyed by ocr_id.
    :ivar dict[str, RoiState] roi_states: ROI states, keyed by roi_id.

    Examples::
        >>> frame_state = FrameState(id="sensor1", last_x_frames=[frame1, frame2])
        >>> print(f"Created frame state with {len(frame_state.last_x_frames)} frames")
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    last_x_frames: list[nvSchema.Frame] = Field(default_factory=list)
    safety_violation_states: dict[str, IncidentState] = Field(default_factory=dict)
    restricted_area_violation_states: dict[str, IncidentState] = Field(default_factory=dict)
    confined_area_violation_states: dict[str, IncidentState] = Field(default_factory=dict)
    fov_count_violation_state: IncidentState | None = None
    amr_states: dict[str, AmrState] = Field(default_factory=dict)
    roi_states: dict[str, RoiState] = Field(default_factory=dict)


class Behavior(BaseModel):
    """
    Represents the behavior of an object over time, including its trajectory, speed, and other metrics.

    :ivar str id: Unique identifier for the behavior
    :ivar datetime timestamp: Start timestamp of the behavior
    :ivar datetime end: End timestamp of the behavior
    :ivar Sensor sensor: Sensor that observed the behavior
    :ivar Object object: Object exhibiting the behavior
    :ivar GeoLocation | None locations: Raw trajectory points
    :ivar GeoLocation | None smoothLocations: Smoothed trajectory points
    :ivar list[str] edges: Road network edges (for geographic coordinates)
    :ivar float distance: Total distance traveled
    :ivar float speed: Average speed
    :ivar list[float] speedOverTime: Speed measurements over time
    :ivar float timeInterval: Duration of the behavior
    :ivar float bearing: Direction of movement
    :ivar str | None direction: Cardinal direction
    :ivar int length: Number of trajectory points
    :ivar Place place: Location where behavior occurred
    :ivar AnalyticsModule analyticsModule: Module that analyzed the behavior
    :ivar Event | None event: Associated event
    :ivar str videoPath: Path to related video
    :ivar list[Pose] poses: Sequence of poses
    :ivar list[Gaze] gazes: Sequence of gaze directions
    :ivar list[LipActivity] lipActivities: Sequence of lip activities
    :ivar list[Embedding] embeddings: Object embeddings
    :ivar dict[str, str] info: Additional behavior information

    Examples::

        # Create a behavior with trajectory and metrics
        sensor = Sensor(id="cam1", type="camera")
        person = Object(id="person1", type="person")

        behavior = Behavior(
            id="behavior1",
            timestamp=datetime.now(),
            end=datetime.now() + timedelta(seconds=5),
            sensor=sensor,
            object=person,
            distance=10.5,
            speed=2.1,
            timeInterval=5.0,
            bearing=45.0,
            direction="NE"
        )

        # Add trajectory points
        geo_loc = GeoLocation(type="linestring")
        geo_loc.coordinates.extend([
            Point(point=[-74.0060, 40.7128]),  # New York
            Point(point=[-73.9352, 40.7306])   # Brooklyn
        ])
        behavior.locations = geo_loc

        # Add pose information
        behavior.poses.append(Pose(
            type="pose2D",
            keypoints=[Keypoint(name="head", coordinates=[100, 100, 0.9])]
        ))
    """

    id: str
    timestamp: datetime
    end: datetime
    sensor: Sensor
    object: Object
    locations: GeoLocation | None = None
    smoothLocations: GeoLocation | None = None
    edges: list[str] = Field(default_factory=list)
    distance: float = 0.0
    speed: float = 0.0
    speedOverTime: list[float] = Field(default_factory=list)
    timeInterval: float
    bearing: float = 0.0
    direction: str | None = None
    length: int = 0
    place: Place = Field(default_factory=Place)
    analyticsModule: AnalyticsModule = Field(default_factory=AnalyticsModule)
    event: Event | None = None
    videoPath: str = ""
    poses: list[Pose] = Field(default_factory=list)
    gazes: list[Gaze] = Field(default_factory=list)
    lipActivities: list[LipActivity] = Field(default_factory=list)
    embeddings: list[Embedding] = Field(default_factory=list)
    info: dict[str, str] = Field(default_factory=dict)

    class Config:
        # Custom encoder for datetime
        json_encoders = {datetime: convert_datetime_to_iso_8601_with_z_suffix}


class Incident(BaseModel):
    """
    Represents an incident with its details and metadata.

    :ivar datetime timestamp: Start timestamp
    :ivar datetime end: End timestamp
    :ivar str sensorId: Sensor id
    :ivar list[str] objectIds: List of object IDs involved in the incident
    :ivar list[str] | None frameIds: List of frame IDs when the incident took place
    :ivar Place place: Where object was seen by the sensor
    :ivar AnalyticsModule analyticsModule: Module that generated the incident
    :ivar str category: Type of the incident
    :ivar list[Embedding] embeddings: Embeddings can be related to object appearance or it could be representative of a scene description provided by VLM
    :ivar bool isAnomaly: Whether the incident is an anomaly
    :ivar dict[str, str] info: Additional info if needed

    Examples::

        # Create an incident
        incident = Incident(
            timestamp=datetime.now(),
            end=datetime.now() + timedelta(minutes=5),
            sensorId="cam1",
            objectIds=["person1", "person2"],
            category="collision"
        )

        # Add metadata
        incident.info["severity"] = "high"
        incident.info["location"] = "intersection1"
    """

    timestamp: datetime
    end: datetime
    sensorId: str
    objectIds: list[str] = Field(default_factory=list)
    frameIds: list[str] | None = Field(default_factory=list)
    place: Place = Field(default_factory=Place)
    analyticsModule: AnalyticsModule = Field(default_factory=AnalyticsModule)
    category: str
    embeddings: list[Embedding] = Field(default_factory=list)
    isAnomaly: bool = False
    info: dict[str, str] = Field(default_factory=dict)


class PolygonHole(BaseModel):
    """
    Represents a hole in a polygon, mapping of protobuf PolygonHole.

    :ivar list[Point2D] coordinates: List of points defining the hole boundary

    Examples::

        # Create a polygon hole
        hole = PolygonHole(
            coordinates=[
                Point2D(x=100, y=100),
                Point2D(x=150, y=100),
                Point2D(x=150, y=150),
                Point2D(x=100, y=150)
            ]
        )
    """

    coordinates: list[Point2D]


class PolygonCoords(BaseModel):
    """
    Represents polygon geometry in WKT format, mapping of protobuf PolygonCoords.

    :ivar list[Point2D] coordinates: List of points defining the polygon boundary
    :ivar list[PolygonHole] holes: List of holes in the polygon

    Examples::

        # Create a polygon with a hole
        outer_ring = [
            Point2D(x=0, y=0),
            Point2D(x=200, y=0),
            Point2D(x=200, y=200),
            Point2D(x=0, y=200)
        ]

        hole = PolygonHole(
            coordinates=[
                Point2D(x=50, y=50),
                Point2D(x=150, y=50),
                Point2D(x=150, y=150),
                Point2D(x=50, y=150)
            ]
        )

        polygon = PolygonCoords(
            coordinates=outer_ring,
            holes=[hole]
        )
    """

    coordinates: list[Point2D]
    holes: list[PolygonHole]


class SpaceUtilizationMetrics(BaseModel):
    """
    Represents metrics for space utilization analysis, mapping of protobuf SpaceUtilizationMetrics.

    :ivar float spaceOccupied: Amount of space currently occupied
    :ivar float freeSpace: Amount of free space available
    :ivar float totalSpace: Total space capacity
    :ivar float spaceUtilization: Space utilization percentage
    :ivar int numExtraPallets: Number of extra pallets that could fit
    :ivar float utilizableFreeSpace: Amount of free space that can be utilized
    :ivar float freeSpaceQuality: Quality score of free space
    :ivar bool isUnsafe: Whether the space utilization is unsafe

    Examples::

        # Create space utilization metrics
        metrics = SpaceUtilizationMetrics(
            spaceOccupied=500.0,
            freeSpace=300.0,
            totalSpace=800.0,
            spaceUtilization=62.5,
            numExtraPallets=15,
            utilizableFreeSpace=250.0,
            freeSpaceQuality=0.8,
            isUnsafe=False
        )

        # Calculate utilization percentage
        utilization = (metrics.spaceOccupied / metrics.totalSpace) * 100
    """

    spaceOccupied: float
    freeSpace: float
    totalSpace: float
    spaceUtilization: float
    numExtraPallets: int
    utilizableFreeSpace: float
    freeSpaceQuality: float
    isUnsafe: bool


class SpaceUtilizationLayouts(BaseModel):
    """
    Represents layout information for space utilization, mapping of protobuf SpaceUtilizationLayouts.

    :ivar list[PolygonCoords] freeSpace: List of polygons representing free space
    :ivar list[PolygonCoords] utilizableFreeSpace: List of polygons representing utilizable free space

    Examples::

        # Create space utilization layouts
        free_space = PolygonCoords(
            coordinates=[
                Point2D(x=0, y=0),
                Point2D(x=100, y=0),
                Point2D(x=100, y=100),
                Point2D(x=0, y=100)
            ]
        )

        layouts = SpaceUtilizationLayouts(
            freeSpace=[free_space],
            utilizableFreeSpace=[free_space]
        )
    """

    freeSpace: list[PolygonCoords]
    utilizableFreeSpace: list[PolygonCoords]


class SpaceUtilization(BaseModel):
    """
    Represents space utilization analysis results, mapping of protobuf SpaceUtilization.

    :ivar str id: Unique identifier for the analysis
    :ivar datetime timestamp: Timestamp of the analysis
    :ivar SpaceUtilizationMetrics metrics: Space utilization metrics
    :ivar list[str] sensors: List of sensors used in the analysis
    :ivar SpaceUtilizationLayouts layouts: Layout information

    Examples::

        # Create space utilization analysis
        metrics = SpaceUtilizationMetrics(
            spaceOccupied=500.0,
            freeSpace=300.0,
            totalSpace=800.0
        )

        layouts = SpaceUtilizationLayouts(
            freeSpace=[free_space_polygon],
            utilizableFreeSpace=[utilizable_space_polygon]
        )

        analysis = SpaceUtilization(
            id="analysis1",
            timestamp=datetime.now(),
            metrics=metrics,
            sensors=["cam1", "cam2"],
            layouts=layouts
        )
    """

    id: str
    timestamp: datetime
    metrics: SpaceUtilizationMetrics
    sensors: list[str] = Field(default_factory=list)
    layouts: SpaceUtilizationLayouts

    class Config:
        # Custom encoder for datetime
        json_encoders = {datetime: convert_datetime_to_iso_8601_with_z_suffix}


class Line(BaseModel):
    """
    Represents a line used for tripwire definition, mapping of protobuf Line.

    :ivar Point2D p1: First endpoint of the line
    :ivar Point2D p2: Second endpoint of the line

    Examples::

        # Create a line for tripwire
        line = Line(
            p1=Point2D(x=100, y=100),
            p2=Point2D(x=200, y=200)
        )

        # Calculate line length
        length = math.sqrt((line.p2.x - line.p1.x)**2 + (line.p2.y - line.p1.y)**2)
    """

    p1: Point2D
    p2: Point2D


class Tripwire(BaseModel):
    """
    Represents a tripwire for object detection, mapping of protobuf Tripwire.

    :ivar str id: Unique identifier for the tripwire
    :ivar Line wire: The line representing the tripwire
    :ivar Line direction: Direction line for tripwire orientation
    :ivar int in_orientation: Orientation for incoming direction
    :ivar int out_orientation: Orientation for outgoing direction
    :ivar list[str] sensors: List of sensors monitoring the tripwire
    :ivar list[str] groups: List of groups associated with the tripwire

    Examples::

        # Create a tripwire
        wire = Line(
            p1=Point2D(x=100, y=100),
            p2=Point2D(x=200, y=200)
        )

        direction = Line(
            p1=Point2D(x=150, y=150),
            p2=Point2D(x=250, y=250)
        )

        tripwire = Tripwire(
            id="trip1",
            wires=[wire],
            direction=direction,
            in_orientation=1,
            out_orientation=2,
            sensors=["cam1", "cam2"]
        )
    """

    id: str
    wires: list[Line]
    direction: Line
    in_orientation: int
    out_orientation: int
    sensors: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)


class ROI(BaseModel):
    """
    Represents a Region of Interest (ROI), mapping of protobuf ROI.

    :ivar str id: Unique identifier for the ROI
    :ivar str type: Type of ROI (e.g., "entrance", "exit", "restricted")
    :ivar list[Point2D] roiCoordinates: List of points defining the ROI boundary
    :ivar list[str] restrictedObjectTypes: List of object types restricted in the ROI
    :ivar list[str] confinedObjectTypes: List of object types confined to the ROI
    :ivar list[str] sensors: List of sensors monitoring the ROI
    :ivar list[str] groups: List of groups associated with the ROI

    Examples::

        # Create a restricted ROI
        roi = ROI(
            id="entrance1",
            type="restricted",
            roiCoordinates=[
                Point2D(x=100, y=100),
                Point2D(x=200, y=100),
                Point2D(x=200, y=200),
                Point2D(x=100, y=200)
            ],
            restrictedObjectTypes=["vehicle"],
            sensors=["cam1"]
        )
    """

    id: str
    type: str = ""
    roiCoordinates: list[Point2D] = Field(default_factory=list)
    restrictedObjectTypes: list[str] = Field(default_factory=list)
    confinedObjectTypes: list[str] = Field(default_factory=list)
    sensors: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)


class SensorInfo(BaseModel):
    """
    Represents detailed sensor information, mapping of protobuf SensorInfo.

    :ivar str id: Unique identifier for the sensor
    :ivar str type: Type of sensor (e.g., "camera", "lidar")
    :ivar Location origin: Origin location of the sensor
    :ivar Location geoLocation: Geographic location of the sensor
    :ivar dict[str, float] translationToGlobalCoordinates: Translation to global coordinates
    :ivar list[Coordinate] imageCoordinates: List of image-space coordinates
    :ivar list[Coordinate] globalCoordinates: List of global coordinates
    :ivar list[dict[str, str]] place: Place information
    :ivar list[ROI] rois: List of ROIs associated with the sensor
    :ivar dict[str, Tripwire] tripwires: Dictionary of tripwires
    :ivar float | None scaleFactor: Scale factor for coordinate conversion
    :ivar dict[str, float] | None coordinates: Sensor coordinates
    :ivar dict[str, Any] | None group: Group information
    :ivar dict[str, Any] | None region: Region information
    :ivar list[dict[str, str]] | None attributes: Sensor attributes
    :ivar list[list[float]] | None intrinsicMatrix: Camera intrinsic matrix
    :ivar list[list[float]] | None extrinsicMatrix: Camera extrinsic matrix
    :ivar list[list[float]] | None cameraMatrix: Camera matrix
    :ivar list[list[float]] | None homography: Homography matrix
    :ivar dict[str, mplPath.Path] | None roiPolygons: ROI polygons

    Examples::

        # Create sensor information
        sensor_info = SensorInfo(
            id="cam1",
            type="camera",
            origin=Location(lat=40.7128, lon=-74.0060),
            geoLocation=Location(lat=40.7128, lon=-74.0060),
            attributes=[
                {"resolution": "1920x1080"},
                {"fps": "30"}
            ]
        )

        # Add ROI
        roi = ROI(
            id="roi1",
            type="entrance",
            roiCoordinates=[Point2D(x=0, y=0), Point2D(x=100, y=100)]
        )
        sensor_info.rois.append(roi)
    """

    id: str
    type: str
    origin: Location = Field(default_factory=Location)
    geoLocation: Location = Field(default_factory=Location)
    translationToGlobalCoordinates: dict[str, float] = Field(default_factory=dict)
    imageCoordinates: list[Coordinate] = Field(default_factory=list)
    globalCoordinates: list[Coordinate] = Field(default_factory=list)
    place: list[dict[str, str]] = Field(default_factory=list)
    rois: list[ROI] = Field(default_factory=list)
    tripwires: dict[str, Tripwire] = Field(default_factory=dict)
    attributes: dict[str, str] = Field(default_factory=dict)
    scaleFactor: float | None = None
    coordinates: dict[str, float] | None = None
    group: dict[str, Any] | None = None
    region: dict[str, Any] | None = None
    intrinsicMatrix: list[list[float]] | None = None
    extrinsicMatrix: list[list[float]] | None = None
    cameraMatrix: list[list[float]] | None = None
    homography: list[list[float]] | None = None
    roiPolygons: dict[str, mplPath.Path] | None = None

    class Config:
        arbitrary_types_allowed = True


class Notification(BaseModel):
    """
    Represents a notification received from a Kafka notification topic.

    :ivar str event_type: Type of event (e.g., "upsert", "delete")
    :ivar datetime timestamp: Timestamp of the notification
    :ivar str message: Notification message content
    :ivar list[SensorInfo] | None sensors: List of sensor information

    Examples::

        # Create a notification
        notification = Notification(
            event_type="upsert",
            timestamp=datetime.now(),
            message="Sensor configuration updated",
            sensors=[
                SensorInfo(
                    id="cam1",
                    type="camera",
                    attributes=[{"status": "active"}]
                )
            ]
        )

        # Access notification information
        print(f"Event type: {notification.event_type}")
        print(f"Message: {notification.message}")
        if notification.sensors:
            print(f"Affected sensors: {len(notification.sensors)}")
    """

    event_type: str
    timestamp: datetime
    message: str = ""
    sensors: list[SensorInfo] | None = None

    class Config:
        # Custom encoder for datetime
        json_encoders = {datetime: convert_datetime_to_iso_8601_with_z_suffix}


class ConfigMessage(BaseModel):
    """
    Decoded representation of a dynamic-config notification.

    Built by :func:`deserialize_config_message` (in
    :mod:`mdx.analytics.core.transform.config.config_listener`) from a
    raw :class:`StreamMessage`, then consumed by
    :meth:`ConfigListener._dispatch`. Lives in the shared schema
    package alongside the other Kafka wire models so it can be imported
    without dragging in the listener's runtime dependencies.

    :ivar str event_type: One of ``"upsert"``, ``"upsert-all"``,
        ``"ack"``, ``"request-config"``.
    :ivar str reference_id: Header value -- correlates request and reply
        across the broker.
    :ivar datetime timestamp: Kafka record timestamp converted to UTC;
        used for monotonic write filtering and as the filename suffix.
    :ivar Any config: ``value.config`` from the body. Deliberately
        typed as :obj:`Any` (not ``dict[str, Any] | None``): the
        dynamic-config validator (``config_validator.validate``)
        isinstance-checks dict-ness at the next hop and returns a
        structured ``failure`` ack for non-dict shapes (``[]``,
        ``"foo"``, etc.). Tightening the type here would short-circuit
        construction with a :class:`pydantic.ValidationError` and drop
        the message before the ack is emitted, so web-api would lose
        its feedback signal for shape violations. The validator is the
        single source of truth for "is this payload applyable".
    :ivar str | None status: ``value.status`` from the body. Typed
        strictly because web-api emits only ``"success"`` /
        ``"failure"`` / ``"partial-success"`` strings or ``null``; a
        non-string here would be a producer bug worth catching at
        construction.
    :ivar str | None error: ``value.error`` from the body. Same
        rationale as ``status``.
    """

    event_type: str
    reference_id: str
    timestamp: datetime
    config: Any
    # ``status`` / ``error`` default to ``None`` so a producer can omit them
    # (common for inbound ``upsert`` with nothing to report yet). Type stays
    # strict -- a non-string non-null value still fails pydantic.
    status: str | None = None
    error: str | None = None


class StreamMessage(BaseModel):
    """
    Represents a message in a streaming system with key-value pairs and headers.

    This class encapsulates the structure of a streaming message, typically used in message
    brokers like Kafka/RedisStreams. It provides both raw string/bytes access and computed properties
    that ensure byte-encoded access to key and value fields.

    :ivar str | bytes | None key: The message key, can be string or bytes. Defaults to None.
    :ivar str | bytes | None value: The message payload, can be string or bytes. Defaults to None.
    :ivar Mapping[str, bytes] headers: Message headers as a mapping of string keys to byte values. Defaults to empty dict.
    :ivar int timestamp: Message timestamp in milliseconds. Defaults to -1 (which means no timestamp is available).
    :raises TypeError: If headers is not a valid mapping type.

    Examples::

        # Create a message with string key and value
        msg = StreamMessage(
            key="user123",
            value="Hello World",
            headers={"content-type": b"text/plain"}
        )

        # Create a message with bytes
        msg_bytes = StreamMessage(
            key=b"user123",
            value=b"Hello World"
        )

        # Access as bytes
        key_bytes = msg.key_bytes  # Always returns bytes
        value_bytes = msg.value_bytes  # Always returns bytes

        # Create message with only value
        simple_msg = StreamMessage(value="Simple message")
    """

    key: str | bytes | None = None
    value: str | bytes | None = None
    headers: Mapping[str, bytes] = Field(default_factory=dict)
    timestamp: int = -1


    @computed_field(return_type=bytes)
    @property
    def key_bytes(self) -> bytes:
        """
        Get the message key as bytes.

        Converts string keys to UTF-8 encoded bytes, or returns the key as-is if already bytes.
        Returns None if the key is None.

        :return bytes: The message key encoded as bytes
        """

        k = self.key
        return k.encode('utf-8') if k and isinstance(k, str) else k  # type: ignore

    @computed_field(return_type=bytes)
    @property
    def value_bytes(self) -> bytes:
        """
        Get the message value as bytes.

        Converts string values to UTF-8 encoded bytes, or returns the value as-is if already bytes.
        Returns None if the value is None.

        :return bytes: The message value encoded as bytes
        """

        v = self.value
        return v.encode('utf-8') if v and isinstance(v, str) else v  # type: ignore
