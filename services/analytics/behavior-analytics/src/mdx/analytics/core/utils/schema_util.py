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

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from google.protobuf.timestamp_pb2 import Timestamp

from mdx.analytics.core.schema.models import (
    ROI,
    AnalyticsModule,
    Bbox,
    Bbox3d,
    Behavior,
    Coordinate,
    Embedding,
    Event,
    Frame,
    GeoLocation,
    Incident,
    Line,
    Location,
    Message,
    Object,
    Place,
    Point,
    Point2D,
    Pose,
    Keypoint,
    Action,
    Sensor,
    Tripwire,
    TypeMetrics,
)
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema
from mdx.analytics.core.utils.crp import Model
from mdx.analytics.core.utils.distance_util import orientation
from mdx.analytics.core.utils.util import iso_to_epoch

logger = logging.getLogger(__name__)


def datetime_to_timestamp(dt: datetime) -> Timestamp:
    """
    Convert a datetime object to a protobuf Timestamp.

    :param datetime dt: datetime object to convert
    :return Timestamp: protobuf Timestamp object

    Examples::
        >>> timestamp = datetime_to_timestamp(datetime.utcnow())
    """
    timestamp = Timestamp()
    timestamp.FromDatetime(dt)
    return timestamp


def datetime_str_to_timestamp(dt_str: str) -> Timestamp:
    """
    Convert a datetime string to a protobuf Timestamp.

    :param str dt: datetime string to convert
    :return Timestamp: protobuf Timestamp object

    Examples::
        >>> timestamp = datetime_str_to_timestamp("2023-07-15T12:34:56.789Z")
    """

    # dt = datetime.fromisoformat(dt_str)
    dt = datetime.fromisoformat(dt_str.replace("Z", ""))
    return datetime_to_timestamp(dt)


def get_timestamp_from_proto_ts(proto_ts: Timestamp) -> datetime:
    """
    Gets timestamp from protobuf timestamp

    :param Timestamp proto_ts: protobuf timestamp
    :return datetime: python datetime with UTC timezone

    Examples::
        >>> dt = get_timestamp_from_proto_ts(proto_ts)
    """
    return proto_ts.ToDatetime().replace(tzinfo=timezone.utc)


def get_datetime_str_from_proto_ts(proto_ts: Timestamp) -> str:
    """
    Gets datetime string from protobuf timestamp

    :param Timestamp proto_ts: protobuf timestamp
    :return str: python datetime string

    Examples::
        >>> dt_str = get_datetime_str_from_proto_ts(proto_ts)
    """
    timestamp_ms = int((proto_ts.seconds + (proto_ts.nanos * (10**-9))) * 1000)
    timestamp_str = f"{datetime.utcfromtimestamp(timestamp_ms / 1000).isoformat(timespec='milliseconds')}Z"
    return timestamp_str


def nv_bbox_to_bbox(proto_bbox: nvSchema.Bbox) -> Bbox:
    """
    Convert a protobuf Bbox to a Bbox object.

    :param nvSchema.Bbox proto_bbox: protobuf Bbox object to convert
    :return Bbox: Bbox object

    Examples::
        >>> bbox = nv_bbox_to_bbox(proto_bbox)
    """
    return Bbox(leftX=proto_bbox.leftX, topY=proto_bbox.topY, rightX=proto_bbox.rightX, bottomY=proto_bbox.bottomY)


def nv_coordinate_to_coordinate(proto_coordinate: nvSchema.Coordinate) -> Coordinate:
    """
    Convert a protobuf Coordinate to a Coordinate object.

    :param nvSchema.Coordinate proto_coordinate: protobuf Coordinate object to convert
    :return Coordinate: Coordinate object

    Examples::
        >>> coordinate = nv_coordinate_to_coordinate(proto_coordinate)
    """
    return Coordinate(x=proto_coordinate.x, y=proto_coordinate.y, z=proto_coordinate.z)


def nv_location_to_location(proto_location: nvSchema.Location) -> Location:
    """
    Convert a protobuf Location to a Location object.

    :param nvSchema.Location proto_location: protobuf Location object to convert
    :return Location: Location object

    Examples::
        >>> location = nv_location_to_location(proto_location)
    """
    return Location(lat=proto_location.lat, lon=proto_location.lon, alt=proto_location.alt)


def nv_point_to_point(proto_point: extSchema.GeoLocation.Point) -> Point:
    """
    Convert a protobuf Point to a Point object.

    :param extSchema.GeoLocation.Point proto_point: protobuf GeoLocation Point object to convert
    :return Point: Point object

    Examples::
        >>> point = nv_point_to_point(proto_point)
    """
    return Point(point=list(proto_point.point))


def nv_geo_location_to_geo_location(proto_geo_location: extSchema.GeoLocation) -> GeoLocation:
    """
    Convert a protobuf GeoLocation to a GeoLocation object.

    :param extSchema.GeoLocation proto_geo_location: protobuf GeoLocation object to convert
    :return GeoLocation: GeoLocation object

    Examples::
        >>> geo_location = nv_geo_location_to_geo_location(proto_geo_location)
    """
    return GeoLocation(
        type=proto_geo_location.type,
        coordinates=[nv_point_to_point(coord) for coord in list(proto_geo_location.coordinates)],
    )


def location_to_nv_location(location: Location) -> nvSchema.Location:
    """
    Convert a Location to a protobuf Location object.

    :param Location location: Location to convert
    :return nvSchema.Location: protobuf Location object

    Examples::
        >>> proto_location = location_to_nv_location(location)
    """
    return nvSchema.Location(lat=location.lat, lon=location.lon, alt=location.alt)


def coordinate_to_nv_coordinate(coordinate: Coordinate) -> nvSchema.Coordinate:
    """
    Convert a coordinate to a protobuf coordinate object.

    :param Coordinate coordinate: Coordinate to convert
    :return nvSchema.Coordinate: protobuf Coordinate object

    Examples::
        >>> proto_coordinate = coordinate_to_nv_coordinate(coordinate)
    """
    return nvSchema.Coordinate(x=coordinate.x, y=coordinate.y, z=coordinate.z)


def nv_embedding_to_embedding(proto_embedding: nvSchema.Embedding) -> Embedding:
    """
    Convert a protobuf Embedding to an Embedding object.

    :param nvSchema.Embedding proto_embedding: protobuf Embedding to convert
    :return Embedding: Embedding object

    Examples::
        >>> embedding = nv_embedding_to_embedding(proto_embedding)
    """
    return Embedding(vector=list(proto_embedding.vector), info=dict(proto_embedding.info))


def nv_bbox3d_to_bbox3d(proto_bbox3d: nvSchema.Bbox3d) -> Bbox3d:
    """
    Convert a protobuf Bbox3d to a Bbox3d object.

    :param nvSchema.Bbox3d proto_bbox3d: protobuf Bbox3d to convert
    :return Bbox3d: Bbox3d object

    Examples::
        >>> bbox3d = nv_bbox3d_to_bbox3d(proto_bbox3d)
    """
    return Bbox3d(
        coordinates=list(proto_bbox3d.coordinates),
        embeddings=[nv_embedding_to_embedding(e) for e in proto_bbox3d.embeddings],
        confidence=proto_bbox3d.confidence,
        info=dict(proto_bbox3d.info),
    )


def nv_object_to_object(proto_obj: nvSchema.Object) -> Object:
    """
    Convert a protobuf Object to an Object object.

    :param nvSchema.Object proto_obj: protobuf Object to convert
    :return Object: Object object

    Examples::
        >>> obj = nv_object_to_object(proto_obj)
    """
    return Object(
        id=proto_obj.id,
        bbox=nv_bbox_to_bbox(proto_obj.bbox),
        type=proto_obj.type,
        confidence=proto_obj.confidence,
        info=dict(proto_obj.info),
        embedding=nv_embedding_to_embedding(proto_obj.embedding),
        speed=proto_obj.speed,
        dir=list(proto_obj.dir),
        pose=nv_pose_to_pose(proto_obj.pose) if proto_obj.HasField("pose") else None,
        coordinate=nv_coordinate_to_coordinate(proto_obj.coordinate),
        location=nv_location_to_location(proto_obj.location),
        bbox3d=nv_bbox3d_to_bbox3d(proto_obj.bbox3d),
    )


def nv_pose_to_pose(proto_pose: nvSchema.Pose) -> Pose:
    """
    Convert a protobuf Pose to a Pose object.

    :param nvSchema.Pose proto_pose: protobuf Pose to convert
    :return Pose: Pose object

    Examples::
        >>> pose = nv_pose_to_pose(proto_pose)
    """
    return Pose(
        type=proto_pose.type,
        keypoints=[nv_keypoint_to_keypoint(kp) for kp in proto_pose.keypoints],
        actions=[nv_action_to_action(action) for action in proto_pose.actions],
        info=dict(proto_pose.info),
    )


def nv_keypoint_to_keypoint(proto_keypoint: nvSchema.Pose.Keypoint) -> Keypoint:
    """
    Convert a protobuf Keypoint to a Keypoint object.

    :param nvSchema.Pose.Keypoint proto_keypoint: protobuf Keypoint to convert
    :return Keypoint: Keypoint object

    Examples::
        >>> keypoint = nv_keypoint_to_keypoint(proto_keypoint)
    """
    return Keypoint(
        name=proto_keypoint.name,
        coordinates=list(proto_keypoint.coordinates),
        quaternion=list(proto_keypoint.quaternion),
    )

def nv_action_to_action(proto_action: nvSchema.Pose.Action) -> Action:
    """
    Convert a protobuf Action to an Action object.

    :param nvSchema.Pose.Action proto_action: protobuf Action to convert
    :return Action: Action object

    Examples::
        >>> action = nv_action_to_action(proto_action)
    """
    return Action(
        type=proto_action.type,
        confidence=proto_action.confidence,
    )

def nv_event_to_event(proto_event: nvSchema.Event) -> Event:
    """
    Convert a protobuf Event to an Event object.

    :param nvSchema.Event proto_event: protobuf Event to convert
    :return Event: Event object

    Examples::
        >>> event = nv_event_to_event(proto_event)
    """
    return Event(id=proto_event.id, type=proto_event.type, info=dict(proto_event.info))


def nv_analytics_module_to_analytics_module(proto_am: nvSchema.AnalyticsModule) -> AnalyticsModule:
    """
    Convert a protobuf AnalyticsModule to an AnalyticsModule object.

    :param nvSchema.AnalyticsModule proto_am: protobuf AnalyticsModule to convert
    :return AnalyticsModule: AnalyticsModule object

    Examples::
        >>> am = nv_analytics_module_to_analytics_module(proto_am)
    """
    return AnalyticsModule(
        id=proto_am.id,
        description=proto_am.description,
        source=proto_am.source,
        version=proto_am.version,
        info=dict(proto_am.info),
    )


def nv_behavior_to_behavior(proto_behavior: extSchema.Behavior) -> Behavior:
    """
    Convert a protobuf Behavior to a Behavior object.

    :param extSchema.Behavior proto_behavior: protobuf Behavior object to convert
    :return Behavior: Behavior object

    Examples::
        >>> behavior = nv_behavior_to_behavior(proto_behavior)
    """
    return Behavior(
        id=proto_behavior.id,
        timestamp=get_timestamp_from_proto_ts(proto_behavior.timestamp),
        end=get_timestamp_from_proto_ts(proto_behavior.end),
        distance=proto_behavior.distance,
        speed=proto_behavior.speed,
        timeInterval=proto_behavior.timeInterval,
        bearing=proto_behavior.bearing,
        direction=proto_behavior.direction,
        length=proto_behavior.length,
        info=dict(proto_behavior.info),
        embeddings=[nv_embedding_to_embedding(e) for e in proto_behavior.embeddings],
        videoPath=proto_behavior.videoPath,
        event=nv_event_to_event(proto_behavior.event),
        analyticsModule=nv_analytics_module_to_analytics_module(proto_behavior.analyticsModule),
        speedOverTime=list(proto_behavior.speedOverTime),
        edges=list(proto_behavior.edges),
        locations=nv_geo_location_to_geo_location(proto_behavior.locations),
        smoothLocations=nv_geo_location_to_geo_location(proto_behavior.smoothLocations),
        object=nv_object_to_object(proto_behavior.object),
        sensor=nv_sensor_to_sensor(proto_behavior.sensor),
        place=nv_place_to_place(proto_behavior.place),
    )


def nv_sensor_to_sensor(proto_sensor: nvSchema.Sensor) -> Sensor:
    """
    Convert a protobuf Sensor to a Sensor object.

    :param nvSchema.Sensor proto_sensor: protobuf Sensor object to convert
    :return Sensor: Sensor object

    Examples::
        >>> sensor = nv_sensor_to_sensor(proto_sensor)
    """
    return Sensor(
        id=proto_sensor.id,
        type=proto_sensor.type,
        description=proto_sensor.description,
        location=nv_location_to_location(proto_sensor.location),
        coordinate=nv_coordinate_to_coordinate(proto_sensor.coordinate),
        info=dict(proto_sensor.info),
    )


def nv_type_metrics_to_type_metrics(proto_type_metrics: nvSchema.TypeMetrics) -> TypeMetrics:
    """
    Convert a protobuf TypeMetrics to a TypeMetrics object.

    :param nvSchema.TypeMetrics proto_type_metrics: protobuf TypeMetrics object to convert
    :return TypeMetrics: TypeMetrics object

    Examples::
        >>> type_metrics = nv_type_metrics_to_type_metrics(proto_type_metrics)
    """
    return TypeMetrics(
        id=proto_type_metrics.id,
        type=proto_type_metrics.type,
        count=proto_type_metrics.count,
        coordinates=[nv_coordinate_to_coordinate(coord) for coord in proto_type_metrics.coordinates],
        objectIds=list(proto_type_metrics.objectIds),
        info=dict(proto_type_metrics.info),
    )


def nv_frame_to_messages(
    frame: nvSchema.Frame, object_filter: set[str] = set(), in_simulation_mode: bool = False
) -> list[Message]:
    """
    Convert a protobuf Frame to a list of Message objects.

    :param nvSchema.Frame frame: protobuf Frame object to convert
    :param set[str] object_filter: set of object types to filter
    :param bool in_simulation_mode: whether in simulation mode
    :return list[Message]: list of Message objects

    Examples::
        >>> messages = nv_frame_to_messages(proto_frame)
    """
    sensor = Sensor(id=frame.sensorId)
    video_path = f"frameId-{frame.id}"
    messages = []

    for proto_obj in frame.objects:
        if not object_filter or proto_obj.type in object_filter:
            message = Message(
                messageid=frame.id,
                timestamp=get_timestamp_from_proto_ts(frame.timestamp),
                sensor=sensor,
                object=nv_object_to_object(proto_obj),
                videoPath=video_path,
            )
            messages.append(message)

    # if no objects, we still need the message for simulation mode usage
    if not messages and in_simulation_mode:
        messages.append(
            Message(
                messageid=frame.id,
                timestamp=get_timestamp_from_proto_ts(frame.timestamp),
                sensor=sensor,
                videoPath=video_path,
            )
        )
    return messages


def frame_to_messages(frame: Frame) -> list[Message]:
    """
    Convert a Frame to a list of Message objects.

    :param Frame frame: Frame object to convert
    :return list[Message]: list of Message objects

    Examples::
        >>> messages = frame_to_messages(frame)
    """
    sensor = Sensor(id=frame.sensorId)
    video_path = f"frameId-{frame.id}"
    messages = []

    for obj in frame.objects:
        message = Message(
            messageid=frame.id,
            timestamp=frame.timestamp,
            sensor=sensor.model_copy(deep=True),
            object=obj.model_copy(deep=True),
            videoPath=video_path,
        )
        messages.append(message)

    # if no objects, we still need the message for simulation mode usage
    if not messages:
        messages.append(Message(messageid=frame.id, timestamp=frame.timestamp, sensor=sensor, videoPath=video_path))
    return messages


def messages_to_map(messages: list[Message]) -> dict[str, list[Message]]:
    """
    Convert list of messages to a dict, the key is msg.sensor.id + " #-# " + msg.object.id
    value is a list of messages share the same key

    :param list[Message] messages: list of message
    :return dict[str, list[Message]]: dict of messages

    Examples::
        >>> message_map = messages_to_map(messages)
    """
    message_dict = defaultdict(list)
    # For msg without object, use dummy as object key for simulation mode usage
    for msg in messages:
        message_dict[msg.sensor.id + " #-# " + msg.object.id if msg.object else "dummy"].append(msg)
    return dict(message_dict)


def group_frames_by_sensor_id(frames: list[nvSchema.Frame]) -> dict[str, list[nvSchema.Frame]]:
    """
    Convert list of frames to a dict, the key is frame.sensorId
    value is a list of frames share of the same sensor

    :param list[nvSchema.Frame] frames: list of frames
    :return dict[str, list[nvSchema.Frame]]: dict of frames

    Examples::
        >>> frame_map = group_frames_by_sensor_id(frames)
    """
    grouped_frames = defaultdict(list)
    for frame in frames:
        grouped_frames[frame.sensorId].append(frame)
    return dict(grouped_frames)


def group_video_embeddings_by_sensor_id(video_embeddings: list[nvSchema.VisionLLM]) -> dict[str, list[nvSchema.VisionLLM]]:
    """
    Convert list of video embeddings to a dict, the key is video_embedding.sensor.id
    value is a list of video embeddings share of the same sensor

    :param list[nvSchema.VisionLLM] video_embeddings: list of video embeddings
    :return dict[str, list[nvSchema.VisionLLM]]: dict of video embeddings

    Examples::
        >>> video_embeddings_map = group_video_embeddings_by_sensor_id(video_embeddings)
    """
    grouped_video_embeddings = defaultdict(list)
    for vid_embed in video_embeddings:
        grouped_video_embeddings[vid_embed.sensor.id].append(vid_embed)
    return dict(grouped_video_embeddings)


def group_messages_by_frame_id(
        messages: list[Message]
    ) -> dict[str, list[tuple[str, list[Message]]]]:
    """
    Group messages by frame id, and get objects in each frame.

    :param list[Message] messages: messages to be grouped
    :return dict[str, list[tuple[str, list[Message]]]]: grouped messages by frame id

    Examples::
        >>> frame_groups = group_messages_by_frame_id(messages)
    """

    frame_dict: dict[str, dict[str, list[Message]]] = defaultdict(lambda: defaultdict(list))
    for msg in messages:
        frame_id = msg.videoPath.split("frameId-")[-1]
        frame_dict[msg.sensor.id][frame_id].append(msg)

    frame_list: dict[str, list[tuple[str, list[Message]]]] = {}
    for sensor_id in frame_dict.keys():
        frames = [(fid, msgs) for fid, msgs in frame_dict[sensor_id].items()]
        frames.sort(key=lambda t: iso_to_epoch(t[1][0].timestamp))
        frame_list[sensor_id] = frames

    return frame_list


def nv_place_to_place(proto_place: nvSchema.Place) -> Place:
    """
    Convert a protobuf Place object to a Place object.

    :param nvSchema.Place proto_place: protobuf Place object to convert
    :return Place: Place object

    Examples::
        >>> place = nv_place_to_place(proto_place)
    """
    return Place(
        id=proto_place.id,
        name=proto_place.name,
        type=proto_place.type,
        location=nv_location_to_location(proto_place.location),
        coordinate=nv_coordinate_to_coordinate(proto_place.coordinate),
        info=dict(proto_place.info),
    )


def place_to_nv_place(place: Place | None) -> nvSchema.Place:
    """
    Convert Place object to a protobuf Place object.

    :param Place | None place: Place object to convert
    :return nvSchema.Place: protobuf Place object

    Examples::
        >>> proto_place = place_to_nv_place(place)
    """
    if not place:
        return nvSchema.Place()
    proto_place = nvSchema.Place(name=place.name if place.name else "")
    return proto_place


# todo: handle all fields in a clean way
def convert_behavior_to_protobuf_behavior(behavior: Behavior) -> extSchema.Behavior:
    """
    Convert Behavior object to a protobuf Behavior object.

    :param Behavior behavior: Behavior object to convert
    :return extSchema.Behavior: protobuf Behavior object

    Examples::
        >>> protobuf_behavior = convert_behavior_to_protobuf_behavior(behavior)
    """
    protobuf_behavior = extSchema.Behavior(
        id=behavior.id,  # 1
        edges=behavior.edges,  # 7
        distance=behavior.distance,  # 8
        speed=behavior.speed,  # 9
        speedOverTime=behavior.speedOverTime,  # 10
        timeInterval=behavior.timeInterval,  # 11
        bearing=behavior.bearing,  # 12
        direction=behavior.direction if behavior.direction else "",  # 13
        length=behavior.length,  # 14
        place=place_to_nv_place(behavior.place),  # 15
        object=object_to_protobuf_object(behavior.object),
    )
    protobuf_behavior.timestamp.CopyFrom(datetime_to_timestamp(behavior.timestamp))  # 2
    protobuf_behavior.end.CopyFrom(datetime_to_timestamp(behavior.end))  # 3

    geo_location = protobuf_behavior.locations  # 5
    if behavior.locations:
        geo_location.type = behavior.locations.type
        for coordinate in behavior.locations.coordinates:
            point = extSchema.GeoLocation.Point(point=coordinate.point)
            geo_location.coordinates.append(point)

    smooth_geo_location = protobuf_behavior.smoothLocations  # 6
    if behavior.smoothLocations:
        smooth_geo_location.type = behavior.smoothLocations.type
        for coordinate in behavior.smoothLocations.coordinates:
            point = extSchema.GeoLocation.Point(point=coordinate.point)
            smooth_geo_location.coordinates.append(point)

    protobuf_behavior.sensor.id = behavior.sensor.id  # 16

    if behavior.analyticsModule:  # 17
        protobuf_behavior.analyticsModule.id = behavior.analyticsModule.id if behavior.analyticsModule.id else ""
        protobuf_behavior.analyticsModule.description = (
            behavior.analyticsModule.description if behavior.analyticsModule.description else ""
        )

    if behavior.event:  # 19
        protobuf_behavior.event.id = behavior.event.id if behavior.event.id else ""
        protobuf_behavior.event.type = behavior.event.type if behavior.event.type else ""
        if behavior.event.info:
            for key in behavior.event.info:
                protobuf_behavior.event.info[key] = behavior.event.info[key]

    if behavior.embeddings:  # 24
        for e in behavior.embeddings:
            embedding = nvSchema.Embedding(vector=e.vector)
            protobuf_behavior.embeddings.append(embedding)

    if behavior.info:  # 25
        for key in behavior.info:
            protobuf_behavior.info[key] = behavior.info[key]

    # logging.debug(f"behavior.videoPath: {behavior.videoPath} type:{type(behavior.videoPath)}")
    protobuf_behavior.videoPath = behavior.videoPath if behavior.videoPath is not None else ""  # 26
    return protobuf_behavior


def convert_incident_to_protobuf_incident(incident: Incident) -> extSchema.Incident:
    """
    Convert Incident object to a protobuf Incident object.

    :param Incident incident: Incident object to convert
    :return extSchema.Incident: protobuf Incident object

    Examples::
        >>> protobuf_incident = convert_incident_to_protobuf_incident(incident)
    """
    protobuf_incident = extSchema.Incident(
        sensorId=incident.sensorId,
        objectIds=incident.objectIds,
        frameIds=incident.frameIds if incident.frameIds else [],
        category=incident.category,
        isAnomaly=incident.isAnomaly,
        place=place_to_nv_place(incident.place),
    )

    protobuf_incident.timestamp.CopyFrom(datetime_to_timestamp(incident.timestamp))
    protobuf_incident.end.CopyFrom(datetime_to_timestamp(incident.end))

    if incident.analyticsModule:
        protobuf_incident.analyticsModule.id = incident.analyticsModule.id if incident.analyticsModule.id else ""
        protobuf_incident.analyticsModule.description = (
            incident.analyticsModule.description if incident.analyticsModule.description else ""
        )
        protobuf_incident.analyticsModule.source = (
            incident.analyticsModule.source if incident.analyticsModule.source else ""
        )
        protobuf_incident.analyticsModule.version = (
            incident.analyticsModule.version if incident.analyticsModule.version else ""
        )
        # Copy the info field from analyticsModule
        if incident.analyticsModule.info:
            for key in incident.analyticsModule.info:
                protobuf_incident.analyticsModule.info[key] = incident.analyticsModule.info[key]

    if incident.embeddings:
        for e in incident.embeddings:
            embedding = nvSchema.Embedding(vector=e.vector)
            protobuf_incident.embeddings.append(embedding)

    if incident.info:
        for key in incident.info:
            protobuf_incident.info[key] = incident.info[key]

    return protobuf_incident


def dict_bbox_to_protobuf_bbox(dict_bbox: dict) -> nvSchema.Bbox:
    """
    Transform dict bbox to protobuf Bbox.

    :param Dict dict_bbox: dict of bbox
    :return nvSchema.Bbox: protobuf Bbox object

    Examples::
        >>> protobuf_bbox = dict_bbox_to_protobuf_bbox(dict_bbox)
    """

    if not dict_bbox:
        return nvSchema.Bbox()
    return nvSchema.Bbox(
        leftX=dict_bbox["leftX"] if "leftX" in dict_bbox else 0,
        topY=dict_bbox["topY"] if "topY" in dict_bbox else 0,
        rightX=dict_bbox["rightX"] if "rightX" in dict_bbox else 0,
        bottomY=dict_bbox["bottomY"] if "bottomY" in dict_bbox else 0,
        embeddings=[dict_embedding_to_protobuf_embedding(e) for e in dict_bbox.get("embeddings", [])],
        info=dict_bbox.get("info", {}),
    )


def dict_bbox3d_to_protobuf_bbox3d(dict_bbox3d: dict) -> nvSchema.Bbox3d:
    """
    Transform dict bbox3d to protobuf Bbox3d.

    :param Dict dict_bbox3d: dict of bbox3d
    :return nvSchema.Bbox3d: protobuf Bbox3d object

    Examples::
        >>> protobuf_bbox3d = dict_bbox3d_to_protobuf_bbox3d(dict_bbox3d)
    """
    if not dict_bbox3d:
        return nvSchema.Bbox3d()
    return nvSchema.Bbox3d(
        coordinates=dict_bbox3d.get("coordinates", []),
        embeddings=[dict_embedding_to_protobuf_embedding(e) for e in dict_bbox3d.get("embeddings", [])],
        confidence=dict_bbox3d.get("confidence", 0.0),
        info=dict_bbox3d.get("info", {}),
    )


def dict_embedding_to_protobuf_embedding(dict_embedding: dict) -> nvSchema.Embedding:
    """
    Transform dict embedding to protobuf Embedding.

    :param Dict dict_embedding: dict of embedding
    :return nvSchema.Embedding: protobuf Embedding object

    Examples::
        >>> protobuf_embedding = dict_embedding_to_protobuf_embedding(dict_embedding)
    """
    if dict_embedding:
        return nvSchema.Embedding(vector=dict_embedding["vector"])
    return nvSchema.Embedding()


def dict_object_to_protobuf_object(dict_object: dict) -> nvSchema.Object:
    """
    Transform dict object to protobuf Object.

    :param Dict dict_object: dict of object
    :return nvSchema.Object: protobuf Object

    Examples::
        >>> protobuf_object = dict_object_to_protobuf_object(object_dict)
    """
    obj = nvSchema.Object(
        id=dict_object["id"],
        bbox=dict_bbox_to_protobuf_bbox(dict_object.get("bbox") or {}),
        bbox3d=dict_bbox3d_to_protobuf_bbox3d(dict_object.get("bbox3d") or {}),
        type=dict_object["type"],
        info=dict_object.get("info", {}),
        embedding=dict_embedding_to_protobuf_embedding(dict_object.get("embedding") or {}),
    )
    if "confidence" in dict_object:
        obj.confidence = dict_object["confidence"]
    return obj


def object_to_protobuf_object(obj: Object) -> nvSchema.Object:
    """
    Transform object to protobuf object

    :param Object obj: object
    :return nvSchema.Object: protobuf object

    Examples::
        >>> protobuf_object = object_to_protobuf_object(obj)
    """
    return nvSchema.Object(
        id=obj.id,
        type=obj.type,
        info=obj.info,
        confidence=obj.confidence,
        speed=obj.speed,
        dir=obj.dir,
        bbox=nvSchema.Bbox(
            leftX=obj.bbox.leftX,
            topY=obj.bbox.topY,
            rightX=obj.bbox.rightX,
            bottomY=obj.bbox.bottomY
        ),
        bbox3d=nvSchema.Bbox3d(
            coordinates=obj.bbox3d.coordinates,
            embeddings=[nvSchema.Embedding(vector=e.vector, info=e.info) for e in obj.bbox3d.embeddings],
            confidence=obj.bbox3d.confidence,
            info=obj.bbox3d.info
        ),
        coordinate=nvSchema.Coordinate(x=obj.coordinate.x, y=obj.coordinate.y, z=obj.coordinate.z),
        embedding=nvSchema.Embedding(vector=obj.embedding.vector, info=obj.embedding.info) if obj.embedding else nvSchema.Embedding(),
        location=nvSchema.Location(lat=obj.location.lat, lon=obj.location.lon, alt=obj.location.alt) if obj.location else nvSchema.Location()
    )


def str_object_to_protobuf_object_legacy(str_object: str) -> nvSchema.Object:
    """
    Transform str object to protobuf Object.

    :param str str_object: str of object (old Smart City schema) e.g. '-958754206|1047.28|524.645|1117.6|586.751|Vehicle|#|||||||0'
    :return nvSchema.Object: protobuf Object

    Examples::
        >>> protobuf_object = str_object_to_protobuf_object_legacy(str_object)
    """

    iSegments = str_object.split("|#|")

    # process primary
    primary = iSegments[0]
    secondary = iSegments[1] if len(iSegments) > 1 else None
    arr = primary.split("|")
    obj_id = arr[0]
    obj_bbox = nvSchema.Bbox(leftX=float(arr[1]), topY=float(arr[2]), rightX=float(arr[3]), bottomY=float(arr[4]))
    obj_type = arr[5]

    # process secondary
    secondary_a = secondary.split("|") if secondary else []
    confidence = float(secondary_a[-1]) if secondary_a else 0.0

    # handle pose, embedding etc
    pose = None
    embedding = None
    if len(iSegments) > 2:
        for segment in iSegments[2:]:
            arr = [x.strip() for x in segment.split("|")]
            segtype = arr[0]
            if segtype == "pose3D":
                keypoints = []
                for ks in arr[1:]:
                    a = ks.split(",")
                    k = nvSchema.Pose.Keypoint(
                        name=a[0], coordinates=[float(x) for x in a[1:5]], quaternion=[float(x) for x in a[5:9]]
                    )
                    keypoints.append(k)
                pose = nvSchema.Pose(type=segtype, keypoints=keypoints)
            elif segtype == "embedding":
                embedding = nvSchema.Embedding(vector=[float(x) for x in arr[1].split(",")])

    return nvSchema.Object(
        id=obj_id, bbox=obj_bbox, type=obj_type, confidence=confidence, pose=pose, embedding=embedding
    )


def dict_type_metrics_to_protobuf_type_metrics(dict_type_metrics: dict[str, Any]) -> nvSchema.TypeMetrics:
    """
    Transform dict type metrics to protobuf TypeMetrics.

    :param dict[str, Any] dict_type_metrics: dict of type metrics
    :return nvSchema.TypeMetrics: protobuf TypeMetrics

    Examples::
        >>> protobuf_type_metrics = dict_type_metrics_to_protobuf_type_metrics(dict_type_metrics)
    """
    return nvSchema.TypeMetrics(
        id=dict_type_metrics.get("id", ""),
        type=dict_type_metrics.get("type", ""),
        count=dict_type_metrics.get("count", 0),
    )


def dict_visionllm_to_protobuf_visionllm(dict_vllm: dict) -> nvSchema.VisionLLM:

    proto_vllm = nvSchema.VisionLLM(
        version=dict_vllm['version'],
        timestamp=datetime_str_to_timestamp(dict_vllm["timestamp"]),
        end=datetime_str_to_timestamp(dict_vllm["end"]),
        startFrameId=dict_vllm['startFrameId'],
        endFrameId=dict_vllm['endFrameId']
    )

    proto_vllm.sensor.id = dict_vllm['sensor']['id']

    if 'llm' in dict_vllm and 'visionEmbeddings' in dict_vllm['llm']:
        for e in dict_vllm['llm']['visionEmbeddings']:
            embedding = nvSchema.Embedding(vector=e['vector'])
            proto_vllm.llm.visionEmbeddings.append(embedding)

    if 'info' in dict_vllm:
        for k, v in dict_vllm['info'].items():
            proto_vllm.info[k] = v

    return proto_vllm


def dict_frame_to_protobuf_frame(dict_frame: dict) -> nvSchema.Frame:
    """
    Transform dict frame to protobuf Frame.

    :param Dict dict_frame: dict of frame
    :return nvSchema.Frame: protobuf Frame object

    Examples::
        >>> protobuf_frame = dict_frame_to_protobuf_frame(dict_frame)
    """
    objects = dict_frame.get("objects")
    fov = dict_frame.get("fov")
    return nvSchema.Frame(
        version=dict_frame["version"],
        id=dict_frame["id"],
        timestamp=datetime_str_to_timestamp(dict_frame["timestamp"]),
        sensorId=dict_frame["sensorId"],
        info=dict_frame.get("info", {}),
        objects=[dict_object_to_protobuf_object(obj) for obj in objects] if objects else [],
        fov=[dict_type_metrics_to_protobuf_type_metrics(m) for m in fov] if fov else [],
    )


def dict_frame_to_protobuf_frame_legacy(dict_frame: dict) -> nvSchema.Frame:
    """
    Transform dict frame to protobuf frame

    :param Dict dict_frame: dict of frame
    :return nvSchema.Frame: protobuf frame

    Examples::
        >>> protobuf_frame = dict_frame_to_protobuf_frame_legacy(dict_frame)
    """
    objects = dict_frame.get("objects")
    objects = [str_object_to_protobuf_object_legacy(obj) for obj in objects] if objects else []
    return nvSchema.Frame(
        version=dict_frame["version"],
        id=str(dict_frame["id"]),
        timestamp=datetime_str_to_timestamp(dict_frame["@timestamp"]),
        sensorId=dict_frame["sensorId"],
        objects=objects,
    )


def model_to_embeddings(model: Model | None) -> list[Embedding]:
    """
    Get normalized embeddings from model.

    :param Model | None model: model holds the clustering result and provide methods to work with the clusters
    :return list[Embedding]: list of Embedding objects

    Examples::
        >>> embeddings = model_to_embeddings(model)
    """
    if not model:
        return []
    return [Embedding(vector=vector.tolist()) for vector in model.centers]


def point_list_to_geo_location(points: list[Point2D]) -> GeoLocation:
    """
    Transform list of points to GeoLocation

    :param list[Point2D] points: list of points
    :return GeoLocation: geo location

    Examples::
        >>> geo_location = point_list_to_geo_location(points)
    """
    geo_location = GeoLocation(type="linestring")
    for pt in points:
        coordinate = Point()
        coordinate.point.extend([pt.x, pt.y])
        geo_location.coordinates.append(coordinate)
    return geo_location


def get_sensor_id_from_behavior_id(behavior_id: str) -> str:
    """
    Get sensor id from behavior id

    :param str behavior_id: behavior id
    :return str: sensor id

    Examples::
        >>> sensor_id = get_sensor_id_from_behavior_id(behavior_id)
    """
    return behavior_id.split(" #-# ")[0]


def dict_tripwire_to_tripwire(tw: dict[str, Any]) -> Tripwire:
    """
    Transform dict tripwire to Tripwire

    :param dict[str, Any] tw: dict of tripwire
    :return Tripwire: tripwire

    Examples::
        >>> tripwire = dict_tripwire_to_tripwire(tw)
    """

    # Collect all points dynamically
    points = []
    i = 1
    while f"p{i}" in tw["wire"]:
        point_data = tw["wire"][f"p{i}"]
        points.append(Point2D(x=point_data["x"], y=point_data["y"]))
        i += 1
    
    # Create lines between consecutive points
    wires = []
    for j in range(len(points) - 1):
        wires.append(Line(p1=points[j], p2=points[j + 1]))

    # Get in and out orientations
    in_orientation=orientation(
        wires,
        Point2D(x=tw["direction"]["p1"]["x"], y=tw["direction"]["p1"]["y"]),
    )
    out_orientation=orientation(
        wires,
        Point2D(x=tw["direction"]["p2"]["x"], y=tw["direction"]["p2"]["y"]),
    )

    if in_orientation == out_orientation:
        raise ValueError(f"In and out orientations are the same for tripwire {tw['id']}")

    # Get Tripwire object
    return Tripwire(
        id=tw["id"],
        wires=wires,
        direction=Line(
            p1=Point2D(x=tw["direction"]["p1"]["x"], y=tw["direction"]["p1"]["y"]),
            p2=Point2D(x=tw["direction"]["p2"]["x"], y=tw["direction"]["p2"]["y"]),
        ),
        in_orientation=in_orientation,
        out_orientation=out_orientation,
        sensors=tw.get("sensors", []),
        groups=tw.get("groups", []),
    )


def list_tripwires_to_tripwires(tripwires: list[dict[str, Any]]) -> list[Tripwire]:
    """
    Convert a list of tripwire dicts to a list of Tripwire objects.

    :param list[dict[str, Any]] tripwires: list of tripwire dicts
    :return list[Tripwire]: list of Tripwire objects

    Examples::
        >>> tripwires = list_tripwires_to_tripwires(tripwire_dicts)
    """
    return [dict_tripwire_to_tripwire(tw) for tw in tripwires]


def list_tripwires_to_tripwires_map(tripwires: list[dict[str, Any]]) -> dict[str, Tripwire]:
    """
    Convert a list of tripwire dicts to a dict mapping tripwire id to Tripwire object.

    :param list[dict[str, Any]] tripwires: list of tripwire dicts
    :return dict[str, Tripwire]: dict mapping tripwire id to Tripwire object

    Examples::
        >>> tripwire_map = list_tripwires_to_tripwires_map(tripwire_dicts)
    """
    return {tw["id"]: dict_tripwire_to_tripwire(tw) for tw in tripwires}


def list_attributes_to_attributes_map(attributes: list[dict[str, str]]) -> dict[str, str]:
    """
    Convert a list of attribute dicts to a dict mapping attribute name to value.

    :param list[dict[str, str]] attributes: list of attribute dicts with 'name' and 'value' keys
    :return dict[str, str]: dict mapping attribute name to value

    Examples::
        >>> attr_map = list_attributes_to_attributes_map(attribute_dicts)
    """
    return {attr["name"]: attr["value"] for attr in attributes}


def dict_roi_to_roi(roi: dict[str, Any]) -> ROI:
    """
    Convert a dict ROI to a ROI object.

    :param dict[str, Any] roi: dict of ROI
    :return ROI: ROI object

    Examples::
        >>> roi_obj = dict_roi_to_roi(roi_dict)
    """
    return ROI(
        id=roi["id"],
        roiCoordinates=roi["roiCoordinates"],
        type=roi.get("type", ""),
        restrictedObjectTypes=roi.get("restrictedObjectTypes", []),
        confinedObjectTypes=roi.get("confinedObjectTypes", []),
        sensors=roi.get("sensors", []),
        groups=roi.get("groups", []),
    )


def list_rois_to_rois(rois: list[dict[str, Any]]) -> list[ROI]:
    """
    Convert a list of ROI dicts to a list of ROI objects.

    :param list[dict[str, Any]] rois: list of ROI dicts
    :return list[ROI]: list of ROI objects

    Examples::
        >>> roi_list = list_rois_to_rois(roi_dicts)
    """
    return [dict_roi_to_roi(roi) for roi in rois]


def dict_coordinate_to_coordinate(coordinate: dict[str, float]) -> Coordinate:
    """
    Convert a dict coordinate to a Coordinate object.

    :param dict[str, float] coordinate: dict with x, y, z keys
    :return Coordinate: Coordinate object

    Examples::
        >>> coord = dict_coordinate_to_coordinate({"x": 1.0, "y": 2.0, "z": 0.0})
    """
    return Coordinate(x=coordinate.get("x", 0), y=coordinate.get("y", 0), z=coordinate.get("z", 0))


def list_coordinates_to_coordinates(coordinates: list[dict[str, float]]) -> list[Coordinate]:
    """
    Convert a list of coordinate dicts to a list of Coordinate objects.

    :param list[dict[str, float]] coordinates: list of coordinate dicts
    :return list[Coordinate]: list of Coordinate objects

    Examples::
        >>> coords = list_coordinates_to_coordinates(coord_dicts)
    """
    return [dict_coordinate_to_coordinate(coordinate) for coordinate in coordinates]


def dict_point2d_to_point2d(point2d: dict[str, float]) -> Point2D:
    """
    Convert a dict point2d to a Point2D object.

    :param dict[str, float] point2d: dict with x, y keys
    :return Point2D: Point2D object

    Examples::
        >>> point = dict_point2d_to_point2d({"x": 1.0, "y": 2.0})
    """
    return Point2D(x=point2d.get("x", 0), y=point2d.get("y", 0))


def dict_location_to_location(location: dict[str, float]) -> Location:
    """
    Convert a dict location to a Location object.

    :param dict[str, float] location: dict with lat, lng, alt keys
    :return Location: Location object

    Examples::
        >>> loc = dict_location_to_location({"lat": 37.0, "lng": -122.0, "alt": 0.0})
    """
    return Location(lat=location.get("lat", 0), lon=location.get("lng", 0), alt=location.get("alt", 0))
