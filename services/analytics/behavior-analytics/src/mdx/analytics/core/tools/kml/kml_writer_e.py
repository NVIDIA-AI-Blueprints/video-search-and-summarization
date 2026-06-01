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

import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Behavior, Coordinate, Location, Message
from mdx.analytics.core.schema.trajectory.trajectory_e import TrajectoryE
from mdx.analytics.core.tools.kml.kml_writer_base import KmlWriterBase
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE
from mdx.analytics.core.transform.event.tripwire_event import TripwireEvent
from mdx.analytics.core.utils.distance_util import get_lat_lon_coord
from mdx.analytics.core.utils.io_utils import validate_file_path
from mdx.analytics.core.utils.schema_util import dict_frame_to_protobuf_frame_legacy, messages_to_map, nv_frame_to_messages
from mdx.analytics.core.utils.util import time_x_minutes_ago

logging.basicConfig(format="%(asctime)s.%(msecs)03d - %(message)s", datefmt="%y/%m/%d %H:%M:%S", level=logging.INFO)
# This config for tripwire_min_points is specific to the lab148 dataset to generate the KML file.
CONFIG = {"sensors": [{"id": "Camera D1", "configs": [{"name": "tripwire_min_points", "value": "2"}]}]}


class KmlWriterE(KmlWriterBase):
    """
    A class for writing trajectory and tripwire data to KML format.

    This class provides functionality for:
    - Converting trajectory and tripwire data to KML format
    - Processing input data from JSON files
    - Applying calibration transformations
    - Generating KML output with trajectories and tripwires
    - Managing coordinate transformations
    - Handling tripwire events

    :ivar str input_path: Path to the input JSON file containing trajectory data.
    :ivar str output_path: Path where the output KML file will be written.
    :ivar AppConfig config: Application configuration object.
    :ivar CalibrationE calibration: Calibration module for coordinate transformation.
    :ivar TripwireEvent tripwires_e: Module for processing tripwire events.

    Examples::
        >>> writer = KmlWriterE("input.json", "output.kml", "calibration.json")
        >>> writer.main()
        >>> print("KML file created successfully")
    """

    def __init__(self, input_path: str, output_path: str, calibration_path: str) -> None:
        """
        Initialize the KML writer with input and output paths.

        This method:
        1. Validates input file existence
        2. Sets up configuration
        3. Initializes calibration and tripwire modules

        :param str input_path: Path to the input JSON file containing trajectory data.
        :param str output_path: Path where the output KML file will be written.
        :param str calibration_path: Path to the calibration configuration file.
        :return: None
        :raises SystemExit: If the input file does not exist.

        Examples::
            >>> writer = KmlWriterE("input.json", "output.kml", "calibration.json")
            >>> print(f"Initialized KML writer with input: {writer.input_path}")
        """
        # Make sure the input file exists
        self.input_path = validate_file_path(input_path)
        if not os.path.exists(self.input_path):
            logging.error(f"ERROR: The indicated input file `{self.input_path}` does NOT exist.")
            exit(1)

        self.output_path = validate_file_path(output_path)
        self.config = AppConfig(**CONFIG)

        # Instantiate modules
        self.calibration = CalibrationE(self.config, calibration_path)
        self.tripwires_e = TripwireEvent(self.config, self.calibration)

    def main(self) -> None:
        """
        Main execution method for the KML writer.

        This method:
        1. Reads and processes input data
        2. Applies calibration transformations
        3. Groups messages by vehicle ID
        4. Creates trajectories from messages
        5. Processes tripwire events
        6. Writes the final KML output

        :return: None

        Examples::
            >>> writer = KmlWriterE("input.json", "output.kml", "calibration.json")
            >>> writer.main()
            >>> print("KML file generated successfully")
        """
        # Read data to frames
        with open(self.input_path) as f:
            data = [dict_frame_to_protobuf_frame_legacy(json.loads(line)) for line in f if line.strip()]

        data.sort(key=lambda x: x.timestamp.ToMilliseconds())
        frames = [d for d in data if d.objects]

        # Get messages from protobuf frames in each batch
        messages = [msg for frame in frames for msg in nv_frame_to_messages(frame)]
        messages = [self.calibration.transform(msg) for msg in messages if msg.sensor and msg.sensor.id == "Camera D1"]

        # Group messages by key(sensorId + "#-#"" + objectId), value is a list of messages
        messages_map = messages_to_map(messages)

        # Process trajectories
        trajectory_list = []
        for vehicle_id, msgs in messages_map.items():
            print(f"object: {vehicle_id}, msgs count: {len(msgs)}")
            sorted_msgs = sorted(msgs, key=lambda x: x.timestamp)
            trajectory_message = self.create_trajectory_message(vehicle_id, sorted_msgs)
            trajectory_list.append(trajectory_message)

        print(f"Total trajectories: {len(trajectory_list)}")

        originLat = 42.49
        originLon = -91.66
        scale = 100

        trips = {}
        for trip in trajectory_list:
            events = self.tripwires_e.get_events(trip)
            if len(events) > 1:
                print(trip.id, trip.length, trip.timeInterval, len(events))

            locations = [
                get_lat_lon_coord(
                    Location(lat=originLat, lon=originLon, alt=0),
                    Coordinate(x=scale * coord.point[0], y=scale * coord.point[1], z=0),
                )
                for coord in trip.locations.coordinates
            ]

            eventString = self.get_events_string(events)
            if len(events) > 1:
                trips[(trip.id, eventString)] = TrajectoryE(
                    id=trip.id, start=trip.timestamp, end=trip.end, points=locations
                )

        # Add tripwires
        tripwires = {}
        for x in self.tripwires_e.calibration.sensor_map["Camera D1"].tripwires.values():
            wire = x.wires[0]
            locations = [
                get_lat_lon_coord(
                    Location(lat=originLat, lon=originLon, alt=0),
                    Coordinate(x=scale * wire.p1.x, y=scale * wire.p1.y, z=0),
                ),
                get_lat_lon_coord(
                    Location(lat=originLat, lon=originLon, alt=0),
                    Coordinate(x=scale * wire.p2.x, y=scale * wire.p2.y, z=0),
                ),
            ]

            tripwires[("-" + x.id, "wire")] = TrajectoryE(
                id=x.id, start=time_x_minutes_ago(1), end=datetime.now(timezone.utc), points=locations
            )

        # Write to KML
        self.writeOriginal1({**trips, **tripwires}, self.output_path)

    def get_events_string(self, events: list[Behavior]) -> str:
        """
        Generate a string representation of events grouped by ID and type.

        This method:
        1. Counts total number of events
        2. Groups events by ID
        3. Counts events by type within each ID group
        4. Formats the results into a string

        :param list[Behavior] events: List of behavior events to process.
        :return str: A string containing the total number of events and a dictionary
                mapping event IDs to their type counts.

        Examples::
            >>> writer = KmlWriterE("input.json", "output.kml", "calibration.json")
            >>> events = [Behavior(event=Event(id="1", type="type1"))]
            >>> result = writer.get_events_string(events)
            >>> print(result)  # Output: "Total events: 1, Events by ID: {'1': {'type1': 1}}"
        """
        # Get the number of events
        event_length = len(events)

        # Map events to their 'event' attribute
        mapped_events = [event.event for event in events]

        # Group by 'id'
        grouped_by_id = defaultdict(list)
        for event in mapped_events:
            grouped_by_id[event.id].append(event)

        # Process each group
        result = {}
        for key, group in grouped_by_id.items():
            # Group by 'type'
            grouped_by_type = defaultdict(list)
            for event in group:
                grouped_by_type[event.type].append(event)

            # Map to type and count
            counts = {type_: len(events) for type_, events in grouped_by_type.items()}

            # Add to result
            result[key] = str(counts)

        # Combine length and result
        event_string = f"{event_length} {result}"
        return event_string

    def create_trajectory_message(self, vehicle_id: str, messages: list[Message]) -> Behavior:
        """
        Create a trajectory message from a list of messages.

        This method:
        1. Sorts messages by timestamp
        2. Extracts coordinates from messages
        3. Calculates trajectory metrics
        4. Creates a behavior message with trajectory information

        :param str vehicle_id: ID of the vehicle to create trajectory for.
        :param list[Message] messages: List of messages containing vehicle position data.
        :return Behavior: Behavior message containing trajectory information.

        Examples::
            >>> writer = KmlWriterE("input.json", "output.kml", "calibration.json")
            >>> messages = [Message(timestamp=1000, location=Location(x=1, y=1))]
            >>> trajectory = writer.create_trajectory_message("vehicle1", messages)
            >>> print(f"Created trajectory with {len(trajectory.locations.coordinates)} points")
        """
        points = [msg.object.coordinate for msg in messages]
        tr = TrajectoryE(id=vehicle_id, start=messages[0].timestamp, end=messages[-1].timestamp, points=points)
        last_message = messages[-1]

        return Behavior(
            id=tr.id,
            timestamp=tr.start,
            end=tr.end,
            locations=tr.geo_location,
            distance=tr.distance,
            speed=tr.speed,
            length=len(points),
            speedOverTime=tr.speed_over_time,
            timeInterval=tr.time_interval,
            bearing=tr.bearing,
            direction=tr.direction,
            place=last_message.place,
            sensor=last_message.sensor,
            object=last_message.object,
            event=last_message.event,
            videoPath=last_message.videoPath,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default="data/metromind-ppl-raw.json", help="The input data file path")
    parser.add_argument("--output", type=str, default="data/metromind-ppl-raw.kml", help="The output data file path")
    parser.add_argument(
        "--calibration", type=str, default="configs/calibration_lab148_v0.2a.json", help="The input calibration file"
    )
    args = parser.parse_args()
    writer = KmlWriterE(args.input, args.output, args.calibration)
    writer.main()
