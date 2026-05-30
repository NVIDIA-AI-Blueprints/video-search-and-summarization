# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from datetime import datetime, timedelta, timezone

import math
import pytest

from mdx.analytics.core.schema.models import (
    Behavior,
    Bbox,
    Coordinate,
    Location,
    Message,
    Object,
    Sensor,
)
from mdx.analytics.core.transform.detection.stop_detection import StopDetection
from mdx.analytics.core.transform.calibration.calibration_dynamic import CalibrationType


class TestStopDetection:
    @staticmethod
    def _make_image_message(sensor_id: str, object_id: str, ts: datetime, bbox: Bbox | None = None):
        sensor = Sensor(id=sensor_id)
        obj = Object(id=object_id, type="Vehicle")
        if bbox is not None:
            obj.bbox = bbox
        message = Message(
            messageid=f"msg-{sensor_id}-{object_id}-{int(ts.timestamp())}",
            timestamp=ts,
            sensor=sensor,
            object=obj,
        )
        return message

    @staticmethod
    def _make_geo_message(sensor_id: str, object_id: str, ts: datetime, lat: float | None, lon: float | None):
        sensor = Sensor(id=sensor_id)
        obj = Object(id=object_id, type="Vehicle")
        if lat is not None and lon is not None:
            obj.location = Location(lat=lat, lon=lon)
        message = Message(
            messageid=f"msg-{sensor_id}-{object_id}-{int(ts.timestamp())}",
            timestamp=ts,
            sensor=sensor,
            object=obj,
        )
        return message

    @pytest.fixture
    def stop_detection_image(self):
        return StopDetection(CalibrationType.IMAGE)

    @pytest.fixture
    def stop_detection_geo(self):
        return StopDetection(CalibrationType.GEO)

    def test_update_frame_image_adds_center_position(self, stop_detection_image):
        sd = stop_detection_image
        now = datetime.now(timezone.utc)
        bbox = Bbox(leftX=10, topY=20, rightX=30, bottomY=40)
        msg = self._make_image_message("s1", "o1", now, bbox)

        sd.set_time_window_seconds(5)
        sd.update_frame("s1", [msg])

        assert "s1" in sd.position_history
        assert "o1" in sd.position_history["s1"]
        pos = sd.position_history["s1"]["o1"][-1]["position"]
        assert pos.x == pytest.approx((10 + 30) / 2)
        assert pos.y == pytest.approx((20 + 40) / 2)
        assert sd.position_history["s1"]["o1"][-1]["timestamp"] == now

    def test_update_frame_geo_adds_location_position(self, stop_detection_geo):
        sd = stop_detection_geo
        now = datetime.now(timezone.utc)
        lat, lon = 37.0, -122.0
        msg = self._make_geo_message("s1", "o1", now, lat, lon)

        sd.set_time_window_seconds(5)
        sd.update_frame("s1", [msg])

        pos = sd.position_history["s1"]["o1"][-1]["position"]
        assert pos.x == pytest.approx(lon)
        assert pos.y == pytest.approx(lat)

    def test_update_frame_skips_non_vehicle_and_none_object_and_missing_geo(self, stop_detection_geo):
        sd = stop_detection_geo
        now = datetime.now(timezone.utc)

        sensor = Sensor(id="s1")
        msg_none = Message(messageid="m1", timestamp=now, sensor=sensor, object=None)

        obj_person = Object(id="o2", type="Person")
        msg_non_vehicle = Message(messageid="m2", timestamp=now, sensor=sensor, object=obj_person)

        obj_vehicle_no_loc = Object(id="o3", type="Vehicle")
        msg_vehicle_no_loc = Message(messageid="m3", timestamp=now, sensor=sensor, object=obj_vehicle_no_loc)

        sd.update_frame("s1", [msg_none, msg_non_vehicle, msg_vehicle_no_loc])
        assert "s1" not in sd.position_history

    def test_update_frames_processes_only_non_empty_frames(self, stop_detection_image):
        sd = stop_detection_image
        now = datetime.now(timezone.utc)
        bbox = Bbox(leftX=0, topY=0, rightX=2, bottomY=2)
        msg = self._make_image_message("s2", "o1", now, bbox)

        frames = {
            "s2": [("f1", [msg]), ("f2", [])],
            "s3": [("f3", [])],
        }

        sd.set_time_window_seconds(5)
        sd.update_frames(frames)

        assert "s2" in sd.position_history and "o1" in sd.position_history["s2"]
        assert "s3" not in sd.position_history

    def test_cleanup_old_positions_removes_out_of_window(self, stop_detection_image):
        sd = stop_detection_image
        sd.set_time_window_seconds(3)
        sensor_id = "s4"
        object_id = "o1"
        now = datetime.now(timezone.utc)

        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0), now - timedelta(seconds=10))
        sd._update_position(sensor_id, object_id, Coordinate(x=1, y=0), now - timedelta(seconds=4))
        sd._update_position(sensor_id, object_id, Coordinate(x=2, y=0), now)

        history = sd.position_history[sensor_id][object_id]
        assert len(history) == 2
        assert history[0]["timestamp"] == now - timedelta(seconds=4)
        assert history[-1]["timestamp"] == now

    def test_calculate_distance_traveled_returns_expected_values(self, stop_detection_image):
        sd = stop_detection_image
        sensor_id = "s5"
        object_id = "o1"
        now = datetime.now(timezone.utc)

        sd.set_time_window_seconds(10)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0), now)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=1), now + timedelta(seconds=1))
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=2), now + timedelta(seconds=2))

        avg_distance, distance_last_first = sd._calculate_distance_traveled(sensor_id, object_id)
        assert avg_distance == pytest.approx(1.0)
        assert distance_last_first == pytest.approx(2.0)

    def test_calculate_distance_traveled_handles_missing_and_short_history(self, stop_detection_image):
        sd = stop_detection_image
        avg, dlf = sd._calculate_distance_traveled("unknown", "unknown")
        assert math.isinf(avg) and math.isinf(dlf)

        sd._update_position("s", "o", Coordinate(x=0, y=0), datetime.now(timezone.utc))
        avg, dlf = sd._calculate_distance_traveled("s", "o")
        assert math.isinf(avg) and math.isinf(dlf)

    def test_is_vehicle_stopped_by_distance_true_when_meeting_criteria(self, stop_detection_image):
        sd = stop_detection_image
        sd.set_time_window_seconds(2)
        sd.set_stop_distance_threshold(1.0)
        sensor_id = "s6"
        object_id = "o1"
        t0 = datetime.now(timezone.utc)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0), t0)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0.1), t0 + timedelta(seconds=1))
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0.2), t0 + timedelta(seconds=2))

        assert sd.is_vehicle_stopped_by_distance(sensor_id, object_id) is True

    def test_is_vehicle_stopped_by_distance_false_due_to_large_last_first_distance(self, stop_detection_image):
        sd = stop_detection_image
        sd.set_time_window_seconds(2)
        sd.set_stop_distance_threshold(0.1)
        sensor_id = "s7"
        object_id = "o1"
        t0 = datetime.now(timezone.utc)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0), t0)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0.05), t0 + timedelta(seconds=1))
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0.5), t0 + timedelta(seconds=2))

        assert sd.is_vehicle_stopped_by_distance(sensor_id, object_id) is False

    def test_is_vehicle_stopped_by_distance_false_due_to_short_duration_and_none_threshold(self, stop_detection_image):
        sd = stop_detection_image
        sd.set_time_window_seconds(5)
        sd.set_stop_distance_threshold(1.0)
        sensor_id = "s8"
        object_id = "o1"
        t0 = datetime.now(timezone.utc)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0), t0)
        sd._update_position(sensor_id, object_id, Coordinate(x=0, y=0.1), t0 + timedelta(seconds=1))
        assert sd.is_vehicle_stopped_by_distance(sensor_id, object_id) is False

        sd2 = StopDetection(CalibrationType.IMAGE)
        assert sd2.is_vehicle_stopped_by_distance(sensor_id, object_id) is False

    def test_is_vehicle_stopped_by_speed(self):
        now = datetime.now(timezone.utc)
        sensor = Sensor(id="s9")
        obj = Object(id="o1", type="Vehicle")
        beh = Behavior(
            id="b1",
            timestamp=now,
            end=now + timedelta(seconds=3),
            sensor=sensor,
            object=obj,
            timeInterval=3.0,
            speed=0.5,
        )
        sd = StopDetection(CalibrationType.IMAGE)
        assert sd.is_vehicle_stopped_by_speed(beh, speed_threshold=1.0, time_interval_threshold=2.0) is True
        assert sd.is_vehicle_stopped_by_speed(beh, speed_threshold=0.1, time_interval_threshold=2.0) is False
        assert sd.is_vehicle_stopped_by_speed(beh, speed_threshold=1.0, time_interval_threshold=5.0) is False

    def test_remove_object_and_sensor(self, stop_detection_image):
        sd = stop_detection_image
        now = datetime.now(timezone.utc)
        sd._update_position("s1", "o1", Coordinate(x=0, y=0), now)
        sd._update_position("s1", "o2", Coordinate(x=1, y=0), now)
        sd._update_position("s2", "o3", Coordinate(x=2, y=0), now)

        sd._remove_object("s1", "o1")
        assert "o1" not in sd.position_history["s1"]

        sd._remove_object("s2", "o3")
        assert "s2" not in sd.position_history

        sd._remove_sensor("s1")
        assert "s1" not in sd.position_history

    def test_update_live_object_removes_non_live_objects_and_sensors(self, stop_detection_image):
        sd = stop_detection_image
        now = datetime.now(timezone.utc)
        sd._update_position("s1", "o1", Coordinate(x=0, y=0), now)
        sd._update_position("s1", "o2", Coordinate(x=1, y=0), now)
        sd._update_position("s2", "o3", Coordinate(x=2, y=0), now)
        live = [
            "bad-format-entry",
            "s1 #-# o1",
        ]
        sd.update_live_object(live)

        assert "s2" not in sd.position_history
        assert "o1" in sd.position_history["s1"] and "o2" not in sd.position_history["s1"]

    def test_distance_modes_image_geo_and_else_inf(self):
        sd_img = StopDetection(CalibrationType.IMAGE)
        d_img = sd_img._distance(Coordinate(x=0, y=0), Coordinate(x=3, y=4))
        assert d_img == pytest.approx(5.0)

        sd_geo = StopDetection(CalibrationType.GEO)
        d_geo = sd_geo._distance(Coordinate(x=0.0, y=0.0), Coordinate(x=1.0, y=1.0))
        assert d_geo > 0

        sd_else = StopDetection(CalibrationType.CARTESIAN)
        d_else = sd_else._distance(Coordinate(x=0, y=0), Coordinate(x=1, y=1))
        assert math.isinf(d_else)

    def test_update_frame_skips_unknown_coordinate_system(self):
        sd = StopDetection(CalibrationType.CARTESIAN)
        now = datetime.now(timezone.utc)
        bbox = Bbox(leftX=0, topY=0, rightX=2, bottomY=2)
        msg = self._make_image_message("s1", "o1", now, bbox)
        sd.update_frame("s1", [msg])
        assert sd.position_history == {}


