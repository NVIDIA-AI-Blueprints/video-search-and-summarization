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
import unittest
import json
from datetime import datetime, timezone
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema
from mdx.analytics.core.utils import schema_util
from google.protobuf.timestamp_pb2 import Timestamp
from mdx.analytics.core.schema.models import (
    Frame, Message, Sensor, Object, Coordinate, Location, Point2D, Place,
    Behavior, GeoLocation, Point, Embedding, Event, AnalyticsModule
)


class TestSchemaUtils(unittest.TestCase):

    def test_nv_frame_to_messages(self):
        frame = nvSchema.Frame()
        frame.id = "1"
        frame.sensorId = "1"

        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now(timezone.utc))
        frame.timestamp.CopyFrom(timestamp)

        obj1 = nvSchema.Object()
        obj1.id = "1"
        frame.objects.append(obj1)
        obj2 = nvSchema.Object()
        obj2.id = "2"
        frame.objects.append(obj2)

        messages = schema_util.nv_frame_to_messages(frame)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].sensor.id, "1")
        self.assertEqual(messages[0].messageid, "1")
        self.assertIsNotNone(messages[0].object)
        self.assertIsNotNone(messages[1].object)
        obj0 = messages[0].object
        obj1 = messages[1].object
        assert obj0 is not None  # Type narrowing for linter
        assert obj1 is not None  # Type narrowing for linter
        self.assertEqual(obj0.id, "1")
        self.assertEqual(obj1.id, "2")
        self.assertTrue(messages[0].timestamp == schema_util.get_timestamp_from_proto_ts(timestamp))
        self.assertTrue(messages[1].timestamp == schema_util.get_timestamp_from_proto_ts(timestamp))
        self.assertEqual(messages[0].videoPath, "frameId-1")
        self.assertEqual(messages[1].videoPath, "frameId-1")

    def test_dict_frame_to_protobuf_frame_legacy(self):
        one_of_its_raw_data = \
            '{"version": "4.0", "id": 46325465, "@timestamp": "2021-09-23T03:13:10.526Z", \
            "sensorId": "HWY_20_AND_HILL__PTZ__12_9_2018_1_59_59_000_AM_UTC-08_00", \
            "objects": ["-958754206|1047.28|524.645|1117.6|586.751|Vehicle|#|||||||0"]}\n'

        dict_frame = json.loads(one_of_its_raw_data)
        protobuf_frame = schema_util.dict_frame_to_protobuf_frame_legacy(dict_frame)
        messages = schema_util.nv_frame_to_messages(protobuf_frame)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].messageid, "46325465")
        self.assertIsNotNone(messages[0].object)
        obj = messages[0].object
        assert obj is not None  # Type narrowing for linter
        self.assertEqual(obj.id, "-958754206")

    def test_datetime_to_timestamp(self):
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        timestamp = schema_util.datetime_to_timestamp(dt)

        self.assertEqual(timestamp.seconds, 1704110400)
        self.assertEqual(timestamp.nanos, 0)

    def test_datetime_str_to_timestamp(self):
        # Test with Z suffix
        dt_str = "2024-01-01T12:00:00Z"
        timestamp = schema_util.datetime_str_to_timestamp(dt_str)

        self.assertEqual(timestamp.seconds, 1704110400)
        self.assertEqual(timestamp.nanos, 0)

        # Test without Z suffix
        dt_str = "2024-01-01T12:00:00"
        timestamp = schema_util.datetime_str_to_timestamp(dt_str)

        self.assertEqual(timestamp.seconds, 1704110400)
        self.assertEqual(timestamp.nanos, 0)

    def test_get_timestamp_from_proto_ts(self):
        timestamp = Timestamp()
        timestamp.seconds = 1704110400
        timestamp.nanos = 500000000  # 0.5 seconds

        dt = schema_util.get_timestamp_from_proto_ts(timestamp)

        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 1)
        self.assertEqual(dt.hour, 12)
        self.assertEqual(dt.minute, 0)
        self.assertEqual(dt.second, 0)
        self.assertEqual(dt.microsecond, 500000)

    def test_messages_to_map(self):

        # Create test messages
        sensor1 = Sensor(id="sensor1")
        sensor2 = Sensor(id="sensor2")
        obj1 = Object(id="obj1")
        obj2 = Object(id="obj2")

        dt1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        dt3 = datetime(2024, 1, 1, 12, 0, 2, tzinfo=timezone.utc)

        msg1 = Message(messageid="1", sensor=sensor1, object=obj1, timestamp=dt1)
        msg2 = Message(messageid="2", sensor=sensor1, object=obj2, timestamp=dt2)
        msg3 = Message(messageid="3", sensor=sensor2, object=obj1, timestamp=dt3)

        messages = [msg1, msg2, msg3]

        # Test mapping
        message_map = schema_util.messages_to_map(messages)

        self.assertEqual(len(message_map), 3)
        self.assertEqual(len(message_map["sensor1 #-# obj1"]), 1)
        self.assertEqual(len(message_map["sensor1 #-# obj2"]), 1)
        self.assertEqual(len(message_map["sensor2 #-# obj1"]), 1)

    def test_messages_to_map_with_dummy(self):
        # Create test message without object
        sensor = Sensor(id="sensor1")
        msg = Message(messageid="1", sensor=sensor, timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        messages = [msg]

        # Test mapping
        message_map = schema_util.messages_to_map(messages)

        self.assertEqual(len(message_map), 1)
        self.assertEqual(len(message_map["dummy"]), 1)

    def test_group_frames_by_sensor_id(self):

        # Create test protobuf frames
        frame1 = nvSchema.Frame()
        frame1.id = "1"
        frame1.sensorId = "sensor1"
        frame1.version = "3.0"
        
        frame2 = nvSchema.Frame()
        frame2.id = "2"
        frame2.sensorId = "sensor1"
        frame2.version = "3.0"
        
        frame3 = nvSchema.Frame()
        frame3.id = "3"
        frame3.sensorId = "sensor2"
        frame3.version = "3.0"

        frames = [frame1, frame2, frame3]

        # Test grouping
        frame_map = schema_util.group_frames_by_sensor_id(frames)
        self.assertEqual(len(frame_map), 2)
        self.assertEqual(len(frame_map["sensor1"]), 2)
        self.assertEqual(len(frame_map["sensor2"]), 1)

    def test_get_sensor_id_from_behavior_id(self):
        behavior_id = "sensor1 #-# timestamp123"
        sensor_id = schema_util.get_sensor_id_from_behavior_id(behavior_id)

        self.assertEqual(sensor_id, "sensor1")

    def test_nv_coordinate_to_coordinate(self):
        proto_coordinate = nvSchema.Coordinate(x=1.0, y=2.0, z=3.0)
        coordinate = schema_util.nv_coordinate_to_coordinate(proto_coordinate)

        self.assertEqual(coordinate.x, 1.0)
        self.assertEqual(coordinate.y, 2.0)
        self.assertEqual(coordinate.z, 3.0)

    def test_nv_location_to_location(self):
        proto_location = nvSchema.Location(lat=37.7749, lon=-122.4194, alt=0.0)
        location = schema_util.nv_location_to_location(proto_location)

        self.assertEqual(location.lat, 37.7749)
        self.assertEqual(location.lon, -122.4194)
        self.assertEqual(location.alt, 0.0)

    def test_nv_embedding_to_embedding(self):
        vector = [1.0, 2.0, 3.0]
        info = {"key": "value"}
        proto_embedding = nvSchema.Embedding(vector=vector, info=info)

        embedding = schema_util.nv_embedding_to_embedding(proto_embedding)

        self.assertEqual(embedding.vector, vector)
        self.assertEqual(embedding.info, info)

    def test_nv_bbox3d_to_bbox3d(self):
        coordinates = [1.0, 2.0, 3.0]
        confidence = 0.95
        info = {"key": "value"}
        proto_bbox3d = nvSchema.Bbox3d(
            coordinates=coordinates,
            confidence=confidence,
            info=info
        )

        bbox3d = schema_util.nv_bbox3d_to_bbox3d(proto_bbox3d)

        self.assertEqual(bbox3d.coordinates, coordinates)
        self.assertAlmostEqual(bbox3d.confidence, confidence)
        self.assertEqual(bbox3d.info, info)
        self.assertEqual(bbox3d.embeddings, [])

    def test_nv_event_to_event(self):
        proto_event = nvSchema.Event(
            id="event1",
            type="detection",
            info={"confidence": "0.95"}
        )

        event = schema_util.nv_event_to_event(proto_event)

        self.assertEqual(event.id, "event1")
        self.assertEqual(event.type, "detection")
        self.assertEqual(event.info, {"confidence": "0.95"})

    def test_nv_analytics_module_to_analytics_module(self):
        proto_am = nvSchema.AnalyticsModule(
            id="module1",
            description="Test Module",
            source="test",
            version="1.0",
            info={"param": "value"}
        )

        am = schema_util.nv_analytics_module_to_analytics_module(proto_am)

        self.assertEqual(am.id, "module1")
        self.assertEqual(am.description, "Test Module")
        self.assertEqual(am.source, "test")
        self.assertEqual(am.version, "1.0")
        self.assertEqual(am.info, {"param": "value"})

    def test_coordinate_to_nv_coordinate(self):
        coordinate = Coordinate(x=1.0, y=2.0, z=3.0)
        proto_coordinate = schema_util.coordinate_to_nv_coordinate(coordinate)

        self.assertEqual(proto_coordinate.x, 1.0)
        self.assertEqual(proto_coordinate.y, 2.0)
        self.assertEqual(proto_coordinate.z, 3.0)

    def test_location_to_nv_location(self):
        location = Location(lat=37.7749, lon=-122.4194, alt=0.0)
        proto_location = schema_util.location_to_nv_location(location)

        self.assertEqual(proto_location.lat, 37.7749)
        self.assertEqual(proto_location.lon, -122.4194)
        self.assertEqual(proto_location.alt, 0.0)

    def test_dict_bbox_to_protobuf_bbox(self):
        # Test with complete bbox dict
        bbox_dict = {
            "leftX": 10.0,
            "topY": 20.0,
            "rightX": 30.0,
            "bottomY": 40.0
        }
        proto_bbox = schema_util.dict_bbox_to_protobuf_bbox(bbox_dict)

        self.assertEqual(proto_bbox.leftX, 10.0)
        self.assertEqual(proto_bbox.topY, 20.0)
        self.assertEqual(proto_bbox.rightX, 30.0)
        self.assertEqual(proto_bbox.bottomY, 40.0)

        # Test with empty dict
        empty_proto_bbox = schema_util.dict_bbox_to_protobuf_bbox({})
        self.assertEqual(empty_proto_bbox.leftX, 0)
        self.assertEqual(empty_proto_bbox.topY, 0)
        self.assertEqual(empty_proto_bbox.rightX, 0)
        self.assertEqual(empty_proto_bbox.bottomY, 0)

    def test_dict_embedding_to_protobuf_embedding(self):
        # Test with valid embedding dict
        embedding_dict = {
            "vector": [1.0, 2.0, 3.0]
        }
        proto_embedding = schema_util.dict_embedding_to_protobuf_embedding(embedding_dict)
        self.assertEqual(list(proto_embedding.vector), [1.0, 2.0, 3.0])

        # Test with empty dict
        empty_proto_embedding = schema_util.dict_embedding_to_protobuf_embedding({})
        self.assertEqual(list(empty_proto_embedding.vector), [])

    def test_dict_object_to_protobuf_object(self):
        object_dict = {
            "id": "obj1",
            "type": "vehicle",
            "confidence": 0.95,
            "bbox": {
                "leftX": 10.0,
                "topY": 20.0,
                "rightX": 30.0,
                "bottomY": 40.0
            },
            "embedding": {
                "vector": [1.0, 2.0, 3.0]
            },
            "info": {"color": "red"}
        }

        proto_obj = schema_util.dict_object_to_protobuf_object(object_dict)

        self.assertEqual(proto_obj.id, "obj1")
        self.assertEqual(proto_obj.type, "vehicle")
        self.assertAlmostEqual(proto_obj.confidence, 0.95, delta=1e-5)
        self.assertEqual(proto_obj.bbox.leftX, 10.0)
        self.assertEqual(list(proto_obj.embedding.vector), [1.0, 2.0, 3.0])
        self.assertEqual(dict(proto_obj.info), {"color": "red"})

    def test_str_object_to_protobuf_object_legacy(self):
        # Test basic object string
        object_str = "-958754206|1047.288|524.645|1117.6|586.751|Vehicle|#|||||||0"
        proto_obj = schema_util.str_object_to_protobuf_object_legacy(object_str)

        self.assertEqual(proto_obj.id, "-958754206")
        self.assertEqual(proto_obj.type, "Vehicle")
        self.assertAlmostEqual(proto_obj.bbox.leftX, 1047.288, delta=1e-4)
        self.assertAlmostEqual(proto_obj.bbox.topY, 524.645, delta=1e-4)
        self.assertAlmostEqual(proto_obj.bbox.rightX, 1117.6, delta=1e-4)
        self.assertAlmostEqual(proto_obj.bbox.bottomY, 586.751, delta=1e-4)
        self.assertEqual(proto_obj.confidence, 0.0)

    def test_dict_type_metrics_to_protobuf_type_metrics(self):
        metrics_dict = {
            "id": "metric1",
            "type": "vehicle",
            "count": 5
        }

        proto_metrics = schema_util.dict_type_metrics_to_protobuf_type_metrics(metrics_dict)

        self.assertEqual(proto_metrics.id, "metric1")
        self.assertEqual(proto_metrics.type, "vehicle")
        self.assertEqual(proto_metrics.count, 5)

        # Test with missing fields
        empty_metrics = schema_util.dict_type_metrics_to_protobuf_type_metrics({})
        self.assertEqual(empty_metrics.id, "")
        self.assertEqual(empty_metrics.type, "")
        self.assertEqual(empty_metrics.count, 0)

    def test_point_list_to_geo_location(self):
        points = [
            Point2D(x=1.0, y=2.0),
            Point2D(x=3.0, y=4.0),
            Point2D(x=5.0, y=6.0)
        ]

        geo_location = schema_util.point_list_to_geo_location(points)

        self.assertEqual(geo_location.type, "linestring")
        self.assertEqual(len(geo_location.coordinates), 3)
        self.assertEqual(geo_location.coordinates[0].point, [1.0, 2.0])
        self.assertEqual(geo_location.coordinates[1].point, [3.0, 4.0])
        self.assertEqual(geo_location.coordinates[2].point, [5.0, 6.0])

    def test_model_to_embeddings(self):
        # Test with None model
        embeddings = schema_util.model_to_embeddings(None)
        self.assertEqual(embeddings, [])

        # Mock a Model class with centers attribute
        from unittest.mock import Mock
        import numpy as np
        
        mock_model = Mock()
        mock_model.centers = np.array([[1.0, 2.0], [3.0, 4.0]])
        
        embeddings = schema_util.model_to_embeddings(mock_model)

        self.assertEqual(len(embeddings), 2)
        self.assertEqual(embeddings[0].vector, [1.0, 2.0])
        self.assertEqual(embeddings[1].vector, [3.0, 4.0])

    def test_place_to_nv_place(self):
        # Test with None place
        proto_place = schema_util.place_to_nv_place(None)
        self.assertEqual(proto_place.name, "")

        # Test with Place object
        place = Place(name="Test Place")
        proto_place = schema_util.place_to_nv_place(place)
        self.assertEqual(proto_place.name, "Test Place")

        # Test with Place object having empty name
        place = Place(name="")
        proto_place = schema_util.place_to_nv_place(place)
        self.assertEqual(proto_place.name, "")

    def test_dict_frame_to_protobuf_frame(self):
        frame_dict = {
            "version": "1.0",
            "id": "frame1",
            "timestamp": "2024-01-01T12:00:00Z",
            "sensorId": "sensor1",
            "objects": [{
                "id": "obj1",
                "type": "vehicle",
                "bbox": {
                    "leftX": 10.0,
                    "topY": 20.0,
                    "rightX": 30.0,
                    "bottomY": 40.0
                }
            }],
            "info": {"key": "value"}
        }

        proto_frame = schema_util.dict_frame_to_protobuf_frame(frame_dict)

        self.assertEqual(proto_frame.version, "1.0")
        self.assertEqual(proto_frame.id, "frame1")
        self.assertEqual(proto_frame.sensorId, "sensor1")
        self.assertEqual(len(proto_frame.objects), 1)
        self.assertEqual(proto_frame.objects[0].id, "obj1")
        self.assertEqual(dict(proto_frame.info), {"key": "value"})

    def test_dict_bbox3d_to_protobuf_bbox3d(self):
        # Test with complete bbox3d dict
        bbox3d_dict = {
            "coordinates": [1.0, 2.0, 3.0],
            "confidence": 0.95,
            "info": {"key": "value"},
            "embeddings": [{"vector": [4.0, 5.0, 6.0]}]
        }
        proto_bbox3d = schema_util.dict_bbox3d_to_protobuf_bbox3d(bbox3d_dict)

        self.assertEqual(list(proto_bbox3d.coordinates), [1.0, 2.0, 3.0])
        self.assertAlmostEqual(proto_bbox3d.confidence, 0.95)
        self.assertEqual(dict(proto_bbox3d.info), {"key": "value"})
        self.assertEqual(len(proto_bbox3d.embeddings), 1)
        self.assertEqual(list(proto_bbox3d.embeddings[0].vector), [4.0, 5.0, 6.0])

        # Test with empty dict
        empty_proto_bbox3d = schema_util.dict_bbox3d_to_protobuf_bbox3d({})
        self.assertEqual(list(empty_proto_bbox3d.coordinates), [])
        self.assertEqual(empty_proto_bbox3d.confidence, 0.0)
        self.assertEqual(dict(empty_proto_bbox3d.info), {})
        self.assertEqual(len(empty_proto_bbox3d.embeddings), 0)

    def test_frame_to_messages(self):
        # Create a Frame object with objects
        frame = Frame(
            version="1.0",
            id="frame1",
            sensorId="sensor1",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            objects=[
                Object(id="obj1", type="vehicle"),
                Object(id="obj2", type="person")
            ]
        )

        messages = schema_util.frame_to_messages(frame)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].messageid, "frame1")
        self.assertEqual(messages[0].sensor.id, "sensor1")
        self.assertIsNotNone(messages[0].object)
        self.assertIsNotNone(messages[1].object)
        obj0 = messages[0].object
        obj1 = messages[1].object
        assert obj0 is not None  # Type narrowing for linter
        assert obj1 is not None  # Type narrowing for linter
        self.assertEqual(obj0.id, "obj1")
        self.assertEqual(obj1.id, "obj2")
        self.assertEqual(messages[0].videoPath, "frameId-frame1")
        self.assertEqual(messages[1].videoPath, "frameId-frame1")

        # Test frame without objects (simulation mode)
        frame_no_objects = Frame(
            version="1.0",
            id="frame2",
            sensorId="sensor2",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            objects=[]
        )

        messages = schema_util.frame_to_messages(frame_no_objects)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].messageid, "frame2")
        self.assertEqual(messages[0].sensor.id, "sensor2")
        self.assertIsNone(messages[0].object)
        self.assertEqual(messages[0].videoPath, "frameId-frame2")

    # test_group_behaviors_by_behavior_id_timestamp removed - function was removed from schema_util

    def test_convert_behavior_to_protobuf_behavior(self):
        # Create a test behavior with all fields populated
        behavior = Behavior(
            id="behavior1",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
            distance=100.0,
            speed=30.0,
            timeInterval=60.0,
            bearing=45.0,
            direction="Up",
            length=200,
            speedOverTime=[25.0, 30.0, 35.0],
            info={"key": "value"},
            videoPath="video1",
            sensor=Sensor(id="sensor1"),
            object=Object(id="obj1", type="vehicle"),
            place=Place(name="place1"),
            locations=GeoLocation(
                type="linestring",
                coordinates=[Point(point=[1.0, 2.0])]
            ),
            smoothLocations=GeoLocation(
                type="linestring",
                coordinates=[Point(point=[1.1, 2.1])]
            ),
            embeddings=[Embedding(vector=[1.0, 2.0, 3.0])],
            event=Event(id="event1", type="detection"),
            analyticsModule=AnalyticsModule(id="module1", description="test module")
        )

        proto_behavior = schema_util.convert_behavior_to_protobuf_behavior(behavior)

        # Verify all fields were converted correctly
        self.assertEqual(proto_behavior.id, "behavior1")
        self.assertEqual(proto_behavior.distance, 100.0)
        self.assertEqual(proto_behavior.speed, 30.0)
        self.assertEqual(proto_behavior.timeInterval, 60.0)
        self.assertEqual(proto_behavior.bearing, 45.0)
        self.assertEqual(proto_behavior.direction, "Up")
        self.assertEqual(proto_behavior.length, 200.0)
        self.assertEqual(list(proto_behavior.speedOverTime), [25.0, 30.0, 35.0])
        self.assertEqual(dict(proto_behavior.info), {"key": "value"})
        self.assertEqual(proto_behavior.videoPath, "video1")
        self.assertEqual(proto_behavior.sensor.id, "sensor1")
        self.assertEqual(proto_behavior.object.id, "obj1")
        self.assertEqual(proto_behavior.object.type, "vehicle")
        self.assertEqual(proto_behavior.place.name, "place1")
        self.assertEqual(proto_behavior.locations.type, "linestring")
        self.assertEqual(list(proto_behavior.locations.coordinates[0].point), [1.0, 2.0])
        self.assertEqual(proto_behavior.smoothLocations.type, "linestring")
        self.assertEqual(list(proto_behavior.smoothLocations.coordinates[0].point), [1.1, 2.1])
        self.assertEqual(list(proto_behavior.embeddings[0].vector), [1.0, 2.0, 3.0])
        self.assertEqual(proto_behavior.event.id, "event1")
        self.assertEqual(proto_behavior.event.type, "detection")
        self.assertEqual(proto_behavior.analyticsModule.id, "module1")
        self.assertEqual(proto_behavior.analyticsModule.description, "test module")

    def test_nv_point_to_point(self):
        proto_point = extSchema.GeoLocation.Point(point=[1.0, 2.0])
        point = schema_util.nv_point_to_point(proto_point)

        self.assertEqual(point.point, [1.0, 2.0])

    def test_nv_geo_location_to_geo_location(self):
        proto_point1 = extSchema.GeoLocation.Point(point=[1.0, 2.0])
        proto_point2 = extSchema.GeoLocation.Point(point=[3.0, 4.0])
        proto_geo_location = extSchema.GeoLocation(
            type="linestring",
            coordinates=[proto_point1, proto_point2]
        )

        geo_location = schema_util.nv_geo_location_to_geo_location(proto_geo_location)

        self.assertEqual(geo_location.type, "linestring")
        self.assertEqual(len(geo_location.coordinates), 2)
        self.assertEqual(geo_location.coordinates[0].point, [1.0, 2.0])
        self.assertEqual(geo_location.coordinates[1].point, [3.0, 4.0])

    def test_nv_behavior_to_behavior(self):
        # Create a test protobuf behavior
        proto_behavior = extSchema.Behavior(
            id="behavior1",
            distance=100.0,
            speed=30.0,
            timeInterval=60.0,
            bearing=45.0,
            direction="north",
            length=200,
            videoPath="video1",
            speedOverTime=[25.0, 30.0, 35.0],
            edges=["edge1", "edge2"]
        )

        # Set timestamp and end time
        timestamp = schema_util.datetime_to_timestamp(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        end_time = schema_util.datetime_to_timestamp(datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc))
        proto_behavior.timestamp.CopyFrom(timestamp)
        proto_behavior.end.CopyFrom(end_time)

        # Add locations and smooth locations
        proto_behavior.locations.type = "linestring"
        point1 = extSchema.GeoLocation.Point(point=[1.0, 2.0])
        proto_behavior.locations.coordinates.append(point1)

        proto_behavior.smoothLocations.type = "linestring"
        point2 = extSchema.GeoLocation.Point(point=[1.1, 2.1])
        proto_behavior.smoothLocations.coordinates.append(point2)

        # Add sensor, object, event, and analytics module
        proto_behavior.sensor.id = "sensor1"
        proto_behavior.object.id = "obj1"
        proto_behavior.object.type = "vehicle"
        proto_behavior.event.id = "event1"
        proto_behavior.event.type = "detection"
        proto_behavior.analyticsModule.id = "module1"
        proto_behavior.analyticsModule.description = "test module"

        # Convert to behavior object
        behavior = schema_util.nv_behavior_to_behavior(proto_behavior)

        # Verify all fields
        self.assertEqual(behavior.id, "behavior1")
        self.assertEqual(behavior.distance, 100.0)
        self.assertEqual(behavior.speed, 30.0)
        self.assertEqual(behavior.timeInterval, 60.0)
        self.assertEqual(behavior.bearing, 45.0)
        self.assertEqual(behavior.direction, "north")
        self.assertEqual(behavior.length, 200.0)
        self.assertEqual(behavior.videoPath, "video1")
        self.assertEqual(behavior.speedOverTime, [25.0, 30.0, 35.0])
        self.assertEqual(behavior.edges, ["edge1", "edge2"])

        self.assertEqual(behavior.timestamp, datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(behavior.end, datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc))

        self.assertIsNotNone(behavior.locations)
        locations = behavior.locations
        assert locations is not None  # Type narrowing for linter
        self.assertEqual(locations.type, "linestring")
        self.assertEqual(locations.coordinates[0].point, [1.0, 2.0])

        self.assertIsNotNone(behavior.smoothLocations)
        smooth_locations = behavior.smoothLocations
        assert smooth_locations is not None  # Type narrowing for linter
        self.assertEqual(smooth_locations.type, "linestring")
        self.assertEqual(smooth_locations.coordinates[0].point, [1.1, 2.1])

        self.assertEqual(behavior.sensor.id, "sensor1")
        self.assertEqual(behavior.object.id, "obj1")
        self.assertEqual(behavior.object.type, "vehicle")
        self.assertIsNotNone(behavior.event)
        event = behavior.event
        assert event is not None  # Type narrowing for linter
        self.assertEqual(event.id, "event1")
        self.assertEqual(event.type, "detection")
        self.assertEqual(behavior.analyticsModule.id, "module1")
        self.assertEqual(behavior.analyticsModule.description, "test module")

    def test_nv_type_metrics_to_type_metrics(self):
        # Create test protobuf type metrics
        proto_metrics = nvSchema.TypeMetrics(
            id="metrics1",
            type="vehicle",
            count=5,
            objectIds=["obj1", "obj2"],
            info={"key": "value"}
        )

        # Add coordinates
        coord1 = nvSchema.Coordinate(x=1.0, y=2.0, z=3.0)
        coord2 = nvSchema.Coordinate(x=4.0, y=5.0, z=6.0)
        proto_metrics.coordinates.extend([coord1, coord2])

        # Convert to TypeMetrics object
        metrics = schema_util.nv_type_metrics_to_type_metrics(proto_metrics)

        # Verify all fields
        self.assertEqual(metrics.id, "metrics1")
        self.assertEqual(metrics.type, "vehicle")
        self.assertEqual(metrics.count, 5)
        self.assertEqual(metrics.objectIds, ["obj1", "obj2"])
        self.assertEqual(metrics.info, {"key": "value"})
        self.assertEqual(len(metrics.coordinates), 2)
        self.assertEqual(metrics.coordinates[0].x, 1.0)
        self.assertEqual(metrics.coordinates[1].x, 4.0)


    def test_get_datetime_str_from_proto_ts(self):
        # Test converting protobuf timestamp to datetime string
        timestamp = Timestamp()
        timestamp.seconds = 1704110400
        timestamp.nanos = 500000000  # 0.5 seconds
        
        dt_str = schema_util.get_datetime_str_from_proto_ts(timestamp)
        
        self.assertEqual(dt_str, "2024-01-01T12:00:00.500Z")

    def test_nv_frame_to_messages_with_filter(self):
        # Test object filtering in nv_frame_to_messages
        frame = nvSchema.Frame()
        frame.id = "1"
        frame.sensorId = "1"
        
        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now(timezone.utc))
        frame.timestamp.CopyFrom(timestamp)
        
        # Add objects of different types
        obj1 = nvSchema.Object()
        obj1.id = "1"
        obj1.type = "person"
        frame.objects.append(obj1)
        
        obj2 = nvSchema.Object()
        obj2.id = "2"
        obj2.type = "vehicle"
        frame.objects.append(obj2)
        
        # Test with object filter
        messages = schema_util.nv_frame_to_messages(frame, object_filter={"person"})
        self.assertEqual(len(messages), 1)
        self.assertIsNotNone(messages[0].object)
        obj = messages[0].object
        assert obj is not None
        self.assertEqual(obj.type, "person")

    def test_nv_frame_to_messages_simulation_mode(self):
        # Test simulation mode with no objects
        frame = nvSchema.Frame()
        frame.id = "1"
        frame.sensorId = "1"
        
        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now(timezone.utc))
        frame.timestamp.CopyFrom(timestamp)
        
        # Test simulation mode with no objects
        messages = schema_util.nv_frame_to_messages(frame, in_simulation_mode=True)
        self.assertEqual(len(messages), 1)
        self.assertIsNone(messages[0].object)
        self.assertEqual(messages[0].sensor.id, "1")

    def test_nv_pose_to_pose(self):
        # Test converting protobuf pose to pose object
        proto_pose = nvSchema.Pose()
        proto_pose.type = "skeleton"
        
        # Add keypoint
        keypoint = nvSchema.Pose.Keypoint()
        keypoint.name = "head"
        keypoint.coordinates.extend([1.0, 2.0, 3.0])
        keypoint.quaternion.extend([0.0, 0.0, 0.0, 1.0])
        proto_pose.keypoints.append(keypoint)
        
        # Add action
        action = nvSchema.Pose.Action()
        action.type = "walking"
        action.confidence = 0.9
        proto_pose.actions.append(action)
        
        proto_pose.info["extra"] = "data"
        
        pose = schema_util.nv_pose_to_pose(proto_pose)
        
        self.assertEqual(pose.type, "skeleton")
        self.assertEqual(len(pose.keypoints), 1)
        self.assertEqual(pose.keypoints[0].name, "head")
        self.assertEqual(pose.keypoints[0].coordinates, [1.0, 2.0, 3.0])
        self.assertEqual(pose.keypoints[0].quaternion, [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(len(pose.actions), 1)
        self.assertEqual(pose.actions[0].type, "walking")
        self.assertAlmostEqual(pose.actions[0].confidence, 0.9, places=5)
        self.assertEqual(pose.info, {"extra": "data"})

    def test_nv_keypoint_to_keypoint(self):
        # Test converting protobuf keypoint to keypoint object
        proto_keypoint = nvSchema.Pose.Keypoint()
        proto_keypoint.name = "shoulder"
        proto_keypoint.coordinates.extend([10.0, 20.0, 30.0])
        proto_keypoint.quaternion.extend([0.1, 0.2, 0.3, 0.9])
        
        keypoint = schema_util.nv_keypoint_to_keypoint(proto_keypoint)
        
        self.assertEqual(keypoint.name, "shoulder")
        self.assertEqual(keypoint.coordinates, [10.0, 20.0, 30.0])
        # Use assertAlmostEqual for float comparisons due to precision differences
        for expected, actual in zip([0.1, 0.2, 0.3, 0.9], keypoint.quaternion):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_nv_action_to_action(self):
        # Test converting protobuf action to action object
        proto_action = nvSchema.Pose.Action()
        proto_action.type = "running"
        proto_action.confidence = 0.85
        
        action = schema_util.nv_action_to_action(proto_action)
        
        self.assertEqual(action.type, "running")
        self.assertAlmostEqual(action.confidence, 0.85, places=5)

    def test_convert_incident_to_protobuf_incident(self):
        # Test the completely untested convert_incident_to_protobuf_incident function
        from mdx.analytics.core.schema.models import Incident
        
        incident = Incident(
            sensorId="sensor1",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
            objectIds=["obj1", "obj2"],
            frameIds=["frame1", "frame2"],
            category="collision",
            isAnomaly=True,
            place=Place(name="intersection"),
            analyticsModule=AnalyticsModule(
                id="module1",
                description="collision detection",
                source="nvidia",
                version="1.0"
            ),
            embeddings=[Embedding(vector=[1.0, 2.0, 3.0])],
            info={"severity": "high"}
        )
        
        proto_incident = schema_util.convert_incident_to_protobuf_incident(incident)
        
        self.assertEqual(proto_incident.sensorId, "sensor1")
        self.assertEqual(proto_incident.objectIds, ["obj1", "obj2"])
        self.assertEqual(proto_incident.frameIds, ["frame1", "frame2"])
        self.assertEqual(proto_incident.category, "collision")
        self.assertEqual(proto_incident.isAnomaly, True)
        self.assertEqual(proto_incident.place.name, "intersection")
        self.assertEqual(proto_incident.analyticsModule.id, "module1")
        self.assertEqual(proto_incident.analyticsModule.description, "collision detection")
        self.assertEqual(proto_incident.analyticsModule.source, "nvidia")
        self.assertEqual(proto_incident.analyticsModule.version, "1.0")
        self.assertEqual(len(proto_incident.embeddings), 1)
        self.assertEqual(list(proto_incident.embeddings[0].vector), [1.0, 2.0, 3.0])
        self.assertEqual(dict(proto_incident.info), {"severity": "high"})

    def test_convert_incident_to_protobuf_incident_minimal(self):
        # Test incident conversion with minimal fields
        from mdx.analytics.core.schema.models import Incident
        
        incident = Incident(
            sensorId="sensor1",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
            objectIds=["obj1"],
            category="anomaly",
            isAnomaly=False
        )
        
        proto_incident = schema_util.convert_incident_to_protobuf_incident(incident)
        
        self.assertEqual(proto_incident.sensorId, "sensor1")
        self.assertEqual(proto_incident.objectIds, ["obj1"])
        self.assertEqual(proto_incident.frameIds, [])
        self.assertEqual(proto_incident.category, "anomaly")
        self.assertEqual(proto_incident.isAnomaly, False)

    def test_str_object_to_protobuf_object_legacy_with_pose_and_embedding(self):
        # Test the missing pose and embedding branches in str_object_to_protobuf_object_legacy
        object_str_with_pose = ("-958754206|1047.28|524.645|1117.6|586.751|Vehicle|#|||||||0"
                               "|#|pose3D|head,1.0,2.0,3.0,4.0,0.1,0.2,0.3,0.4|shoulder,5.0,6.0,7.0,8.0,0.5,0.6,0.7,0.8"
                               "|#|embedding|1.0,2.0,3.0,4.0,5.0")
        
        proto_obj = schema_util.str_object_to_protobuf_object_legacy(object_str_with_pose)
        
        self.assertEqual(proto_obj.id, "-958754206")
        self.assertEqual(proto_obj.type, "Vehicle")
        self.assertIsNotNone(proto_obj.pose)
        self.assertEqual(proto_obj.pose.type, "pose3D")
        self.assertEqual(len(proto_obj.pose.keypoints), 2)
        self.assertEqual(proto_obj.pose.keypoints[0].name, "head")
        self.assertEqual(list(proto_obj.pose.keypoints[0].coordinates), [1.0, 2.0, 3.0, 4.0])
        # Use assertAlmostEqual for float arrays due to precision differences
        quaternion_expected = [0.1, 0.2, 0.3, 0.4]
        quaternion_actual = list(proto_obj.pose.keypoints[0].quaternion)
        for expected, actual in zip(quaternion_expected, quaternion_actual):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertIsNotNone(proto_obj.embedding)
        self.assertEqual(list(proto_obj.embedding.vector), [1.0, 2.0, 3.0, 4.0, 5.0])

    def test_dict_bbox_to_protobuf_bbox_with_none(self):
        # Test None handling in dict_bbox_to_protobuf_bbox
        from typing import cast
        proto_bbox = schema_util.dict_bbox_to_protobuf_bbox(cast(dict, None))
        self.assertEqual(proto_bbox.leftX, 0)
        self.assertEqual(proto_bbox.topY, 0)
        self.assertEqual(proto_bbox.rightX, 0)
        self.assertEqual(proto_bbox.bottomY, 0)

    def test_dict_bbox3d_to_protobuf_bbox3d_with_none(self):
        # Test None handling in dict_bbox3d_to_protobuf_bbox3d
        from typing import cast
        proto_bbox3d = schema_util.dict_bbox3d_to_protobuf_bbox3d(cast(dict, None))
        self.assertEqual(list(proto_bbox3d.coordinates), [])
        self.assertEqual(proto_bbox3d.confidence, 0.0)
        self.assertEqual(dict(proto_bbox3d.info), {})

    def test_dict_embedding_to_protobuf_embedding_with_none(self):
        # Test None handling in dict_embedding_to_protobuf_embedding  
        from typing import cast
        proto_embedding = schema_util.dict_embedding_to_protobuf_embedding(cast(dict, None))
        self.assertEqual(list(proto_embedding.vector), [])

    def test_convert_behavior_to_protobuf_behavior_edge_cases(self):
        # Test edge cases in convert_behavior_to_protobuf_behavior
        behavior = Behavior(
            id="behavior1",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc),
            sensor=Sensor(id="sensor1"),
            object=Object(id="obj1", type="vehicle"),
            timeInterval=60.0,
            # Test with None values for optional fields
            direction=None,
            analyticsModule=AnalyticsModule(id="", description=""),
            event=Event(id="", type="", info={}),
            videoPath=""
        )
        
        proto_behavior = schema_util.convert_behavior_to_protobuf_behavior(behavior)
        
        self.assertEqual(proto_behavior.id, "behavior1")
        self.assertEqual(proto_behavior.direction, "")  # None becomes empty string
        self.assertEqual(proto_behavior.analyticsModule.id, "")
        self.assertEqual(proto_behavior.analyticsModule.description, "")
        self.assertEqual(proto_behavior.event.id, "")
        self.assertEqual(proto_behavior.event.type, "")
        self.assertEqual(proto_behavior.videoPath, "")

    # Test utility functions that are completely untested
    def test_dict_tripwire_to_tripwire(self):
        # Test with 2 points (single wire)
        tripwire_dict = {
            "id": "tw1",
            "wire": {
                "p1": {"x": 0.0, "y": 0.0},
                "p2": {"x": 10.0, "y": 10.0}
            },
            "direction": {
                "p1": {"x": 5.0, "y": 0.0},
                "p2": {"x": 5.0, "y": 10.0}
            },
            "sensors": ["sensor1"],
            "groups": ["group1"]
        }
        
        tripwire = schema_util.dict_tripwire_to_tripwire(tripwire_dict)
        
        self.assertEqual(tripwire.id, "tw1")
        self.assertEqual(len(tripwire.wires), 1)
        self.assertEqual(tripwire.wires[0].p1.x, 0.0)
        self.assertEqual(tripwire.wires[0].p1.y, 0.0)
        self.assertEqual(tripwire.wires[0].p2.x, 10.0)
        self.assertEqual(tripwire.wires[0].p2.y, 10.0)
        self.assertEqual(tripwire.sensors, ["sensor1"])
        self.assertEqual(tripwire.groups, ["group1"])
    
    def test_dict_tripwire_to_tripwire_multiple_points(self):
        # Test with 4 points (3 wires forming an L-shape)
        tripwire_dict = {
            "id": "tw2",
            "wire": {
                "p1": {"x": 0.0, "y": 0.0},
                "p2": {"x": 10.0, "y": 0.0},
                "p3": {"x": 10.0, "y": 10.0},
                "p4": {"x": 10.0, "y": 20.0}
            },
            "direction": {
                "p1": {"x": 5.0, "y": 5.0},
                "p2": {"x": 15.0, "y": 10.0}
            },
            "sensors": ["sensor1", "sensor2"],
            "groups": []
        }
        
        tripwire = schema_util.dict_tripwire_to_tripwire(tripwire_dict)
        
        self.assertEqual(tripwire.id, "tw2")
        self.assertEqual(len(tripwire.wires), 3)
        
        # Check first wire
        self.assertEqual(tripwire.wires[0].p1.x, 0.0)
        self.assertEqual(tripwire.wires[0].p1.y, 0.0)
        self.assertEqual(tripwire.wires[0].p2.x, 10.0)
        self.assertEqual(tripwire.wires[0].p2.y, 0.0)
        
        # Check second wire
        self.assertEqual(tripwire.wires[1].p1.x, 10.0)
        self.assertEqual(tripwire.wires[1].p1.y, 0.0)
        self.assertEqual(tripwire.wires[1].p2.x, 10.0)
        self.assertEqual(tripwire.wires[1].p2.y, 10.0)
        
        # Check third wire
        self.assertEqual(tripwire.wires[2].p1.x, 10.0)
        self.assertEqual(tripwire.wires[2].p1.y, 10.0)
        self.assertEqual(tripwire.wires[2].p2.x, 10.0)
        self.assertEqual(tripwire.wires[2].p2.y, 20.0)
        
        self.assertEqual(tripwire.sensors, ["sensor1", "sensor2"])
        self.assertEqual(tripwire.groups, [])
    
    def test_dict_tripwire_to_tripwire_complex_polyline(self):
        # Test with complex polyline (Z-shape with 6 points)
        tripwire_dict = {
            "id": "tw3",
            "wire": {
                "p1": {"x": 0.0, "y": 0.0},
                "p2": {"x": 20.0, "y": 0.0},
                "p3": {"x": 20.0, "y": 10.0},
                "p4": {"x": 10.0, "y": 10.0},
                "p5": {"x": 10.0, "y": 20.0},
                "p6": {"x": 30.0, "y": 20.0}
            },
            "direction": {
                "p1": {"x": 15.0, "y": 5.0},
                "p2": {"x": 15.0, "y": 15.0}
            },
            "sensors": [],
            "groups": ["group1", "group2", "group3"]
        }
        
        tripwire = schema_util.dict_tripwire_to_tripwire(tripwire_dict)
        
        self.assertEqual(tripwire.id, "tw3")
        self.assertEqual(len(tripwire.wires), 5)
        
        # Verify the polyline forms a Z-shape
        expected_segments = [
            ((0.0, 0.0), (20.0, 0.0)),
            ((20.0, 0.0), (20.0, 10.0)),
            ((20.0, 10.0), (10.0, 10.0)),
            ((10.0, 10.0), (10.0, 20.0)),
            ((10.0, 20.0), (30.0, 20.0))
        ]
        
        for i, (start, end) in enumerate(expected_segments):
            self.assertEqual(tripwire.wires[i].p1.x, start[0])
            self.assertEqual(tripwire.wires[i].p1.y, start[1])
            self.assertEqual(tripwire.wires[i].p2.x, end[0])
            self.assertEqual(tripwire.wires[i].p2.y, end[1])
        
        self.assertEqual(tripwire.sensors, [])
        self.assertEqual(tripwire.groups, ["group1", "group2", "group3"])

    def test_list_tripwires_to_tripwires(self):
        tripwires_list = [{
            "id": "tw1",
            "wire": {
                "p1": {"x": 0.0, "y": 0.0},
                "p2": {"x": 10.0, "y": 10.0}
            },
            "direction": {
                "p1": {"x": 5.0, "y": 0.0},
                "p2": {"x": 5.0, "y": 10.0}
            }
        }]
        
        tripwires = schema_util.list_tripwires_to_tripwires(tripwires_list)
        
        self.assertEqual(len(tripwires), 1)
        self.assertEqual(tripwires[0].id, "tw1")

    def test_list_tripwires_to_tripwires_map(self):
        tripwires_list = [{
            "id": "tw1",
            "wire": {
                "p1": {"x": 0.0, "y": 0.0},
                "p2": {"x": 10.0, "y": 10.0}
            },
            "direction": {
                "p1": {"x": 5.0, "y": 0.0},
                "p2": {"x": 5.0, "y": 10.0}
            }
        }]
        
        tripwires_map = schema_util.list_tripwires_to_tripwires_map(tripwires_list)
        
        self.assertEqual(len(tripwires_map), 1)
        self.assertEqual(tripwires_map["tw1"].id, "tw1")

    def test_list_attributes_to_attributes_map(self):
        attributes = [
            {"name": "color", "value": "red"},
            {"name": "size", "value": "large"}
        ]
        
        attr_map = schema_util.list_attributes_to_attributes_map(attributes)
        
        self.assertEqual(attr_map, {"color": "red", "size": "large"})

    def test_dict_roi_to_roi(self):
        roi_dict = {
            "id": "roi1",
            "roiCoordinates": [
                Point2D(x=0, y=0),
                Point2D(x=100, y=0),
                Point2D(x=100, y=100),
                Point2D(x=0, y=100)
            ],
            "type": "polygon",
            "restrictedObjectTypes": ["vehicle"],
            "confinedObjectTypes": ["person"],
            "sensors": ["sensor1"],
            "groups": ["group1"]
        }
        
        roi = schema_util.dict_roi_to_roi(roi_dict)
        
        self.assertEqual(roi.id, "roi1")
        self.assertEqual(len(roi.roiCoordinates), 4)
        self.assertEqual(roi.roiCoordinates[0].x, 0)
        self.assertEqual(roi.roiCoordinates[0].y, 0)
        self.assertEqual(roi.type, "polygon")
        self.assertEqual(roi.restrictedObjectTypes, ["vehicle"])
        self.assertEqual(roi.confinedObjectTypes, ["person"])
        self.assertEqual(roi.sensors, ["sensor1"])
        self.assertEqual(roi.groups, ["group1"])

    def test_list_rois_to_rois(self):
        rois_list = [{
            "id": "roi1",
            "roiCoordinates": [Point2D(x=0, y=0), Point2D(x=100, y=100)]
        }]
        
        rois = schema_util.list_rois_to_rois(rois_list)
        
        self.assertEqual(len(rois), 1)
        self.assertEqual(rois[0].id, "roi1")

    def test_dict_coordinate_to_coordinate(self):
        coord_dict = {"x": 1.0, "y": 2.0, "z": 3.0}
        
        coordinate = schema_util.dict_coordinate_to_coordinate(coord_dict)
        
        self.assertEqual(coordinate.x, 1.0)
        self.assertEqual(coordinate.y, 2.0)
        self.assertEqual(coordinate.z, 3.0)

    def test_list_coordinates_to_coordinates(self):
        coords_list = [
            {"x": 1.0, "y": 2.0, "z": 3.0},
            {"x": 4.0, "y": 5.0, "z": 6.0}
        ]
        
        coordinates = schema_util.list_coordinates_to_coordinates(coords_list)
        
        self.assertEqual(len(coordinates), 2)
        self.assertEqual(coordinates[0].x, 1.0)
        self.assertEqual(coordinates[1].x, 4.0)

    def test_dict_point2d_to_point2d(self):
        point_dict = {"x": 10.0, "y": 20.0}
        
        point = schema_util.dict_point2d_to_point2d(point_dict)
        
        self.assertEqual(point.x, 10.0)
        self.assertEqual(point.y, 20.0)

    def test_dict_location_to_location(self):
        location_dict = {"lat": 37.7749, "lng": -122.4194, "alt": 100.0}
        
        location = schema_util.dict_location_to_location(location_dict)
        
        self.assertEqual(location.lat, 37.7749)
        self.assertEqual(location.lon, -122.4194)  # Note: lng becomes lon
        self.assertEqual(location.alt, 100.0)


if __name__ == '__main__':
    unittest.main()
