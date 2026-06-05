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
import time
import traceback
from collections import defaultdict
from datetime import datetime

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.sink.sink_factory import get_sink
from mdx.analytics.core.stream.sink.sink_base import ProtoBytesSerializer, StrBytesSerializer
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path
from mdx.analytics.core.utils.schema_util import (
    datetime_to_timestamp,
    dict_frame_to_protobuf_frame,
    dict_frame_to_protobuf_frame_legacy,
)
from mdx.analytics.core.utils.util import (
    add_offset_in_ms,
    extract_sensor_id,
    get_offset_in_ms,
    get_proto_time_now,
    get_proto_time_x_minutes_ago,
)

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d - %(message)s",
    datefmt="%y/%m/%d %H:%M:%S",
    level=logging.INFO,
)
MILLIS_PER_SECOND = 1000


class PlaybackFrames:
    """
    Playback Module for reading playback data and send to kafka to mock streaming data

    :param str config_path: path to the app config file
    :param str playback_filepath: path to the playback data file
    :param bool playback_from_json: if playback from PbJson data
    :param bool use_simulated_time: if use simulated timestamp
    ::

        playback_app = PlaybackFrames(config_path, playback_filepath, playback_from_json, use_simulated_time)
    """

    def __init__(
        self,
        config_path: str,
        playback_filepath: str,
        playback_from_json: bool,
    ) -> None:
        # Make sure the config file exists
        valid_config_path = validate_file_path(config_path)
        self.playback_filepath = validate_file_path(playback_filepath)
        if not os.path.exists(valid_config_path):
            logging.error(f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
            exit(1)
        if not os.path.exists(self.playback_filepath):
            logging.error(f"ERROR: The indicated playback file `{self.playback_filepath}` does NOT exist.")
            exit(1)

        self.config = AppConfig(**load_json_from_file(valid_config_path))
        logging.info(f"Read config from {valid_config_path}")

        self.use_simulated_time = self.config.playback.inSimulationMode
        self.playback_from_json = playback_from_json
        logging.info(f"Playback based on simulated timestamp: {self.use_simulated_time}")
        logging.info(f"Playback from {'json' if self.playback_from_json else 'pbjson'}")

    def playback(self):

        start_up_delay_sec = self.config.playback.startUpDelaySec
        logging.info(f"wait {start_up_delay_sec} sec before playback starts ...")
        time.sleep(start_up_delay_sec)
        logging.info("starting playback ...")

        topic = self.config.get_kafka_topic("raw")
        playloop = self.config.playback.loop
        filter_empty_objects = self.config.playback.filterEmptyObjects
        simulation_timedelta_in_min = self.config.playback.simulationTimedeltaInMin
        simulation_base_time = self.config.playback.simulationBaseTime
        simulation_fps = self.config.playback.simulationFps
        sink = get_sink(self.config)

        logging.info(f"kafka server used is: {self.config.kafka.brokers}")
        logging.info(f"will send data to topic: {topic}")
        logging.info(f"playback loop: {playloop}")
        logging.info(f"playback filter empty objects: {filter_empty_objects}")
        if self.use_simulated_time:
            logging.info(f"simulated time: {simulation_timedelta_in_min} mins ago")
            logging.info(f"simulated fps: {simulation_fps}")

        try:
            # Init data, which is list of protobuf frames
            data = []
            logging.info(f"loading data from file: {self.playback_filepath}")
            with open(self.playback_filepath) as f:
                if self.playback_from_json:
                    data = [dict_frame_to_protobuf_frame_legacy(json.loads(line)) for line in f if line.strip()]
                else:
                    data = [dict_frame_to_protobuf_frame(json.loads(line)) for line in f if line.strip()]
            # Sort data
            data.sort(key=lambda x: x.timestamp.ToMilliseconds())
            # Filter the data without objects if needed
            if filter_empty_objects:
                data = [d for d in data if d.objects]

            logging.info(f"Got {len(data)} frames in total.")
            logging.info(f"Got {sum([len(d.objects) for d in data if d.objects])} objects in total.")

            ######################
            ## filter by sensor ##
            ######################
            sensors = self.config.playback.sensors
            if len(sensors) > 0:
                logging.info(f"playing data only from the below sensors: {sensors}")
                data = [d for d in data if extract_sensor_id(str(d.sensorId)) in sensors]

            first_simulated_time = None
            for loop in range(playloop):
                count = 0
                first_record_time = data[0].timestamp
                first_system_time = get_proto_time_now()
                logging.info(f"first_system_time: {first_system_time.ToDatetime()}")
                if not first_simulated_time:
                    # Only for the first loop
                    first_simulated_time = get_proto_time_x_minutes_ago(simulation_timedelta_in_min)
                    if simulation_base_time and self.use_simulated_time:
                        first_simulated_time = datetime_to_timestamp(
                            datetime.fromisoformat(simulation_base_time.replace("Z", "+00:00"))
                        )
                        logging.info("Use mega_simulation_base_time with higher priority")

                logging.info(f"first_simulated_time: {first_simulated_time.ToDatetime()}")
                system_offset_in_ms = get_offset_in_ms(first_system_time, first_record_time)
                simulated_offset_in_ms = get_offset_in_ms(first_simulated_time, first_record_time)
                sensor_index = defaultdict(lambda: -1)

                for frame in data:
                    x = nvSchema.Frame()
                    x.CopyFrom(frame)
                    sensor_index[x.sensorId] += 1
                    send_time = add_offset_in_ms(x.timestamp, system_offset_in_ms)
                    timestamp = send_time
                    if self.use_simulated_time:
                        timestamp = add_offset_in_ms(x.timestamp, simulated_offset_in_ms)
                        send_time = add_offset_in_ms(
                            first_record_time,
                            int(system_offset_in_ms + sensor_index[x.sensorId] / simulation_fps * MILLIS_PER_SECOND),
                        )
                    x.timestamp.CopyFrom(timestamp)

                    delay_in_ms = get_offset_in_ms(send_time, get_proto_time_now())
                    if delay_in_ms > 0:
                        time.sleep(delay_in_ms / MILLIS_PER_SECOND)
                    sink.write_msg(
                        dest_key = 'raw',
                        message = ProtoBytesSerializer(x),
                        key = StrBytesSerializer(x.sensorId)
                    )

                    count += 1
                    if count % 1000 == 0:
                        time_elapsed_in_ms = get_offset_in_ms(get_proto_time_now(), first_system_time)
                        msg_rate_in_sec = count * MILLIS_PER_SECOND / time_elapsed_in_ms
                        delay_sec = get_offset_in_ms(get_proto_time_now(), send_time) / 1000
                        logging.debug(str(x)[:120])
                        logging.info(f"msg Rate = {msg_rate_in_sec}/s, count = {count}, delay = {delay_sec}s")

                time.sleep(5)
                first_simulated_time = add_offset_in_ms(x.timestamp, 5 * MILLIS_PER_SECOND)
                logging.info(f"loop = {loop + 1}")

        except (KeyboardInterrupt, SystemExit, Exception) as exception:
            logging.info("Main process interrupted. Stopping...")
            logging.error("Caught exception: %s", str(exception))
            logging.error("Exception details:\n%s", traceback.format_exc())

        finally:
            if sink:
                sink.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=validate_file_path,
        default="configs/frame_playback_config.json",
        action=ValidateFile,
        help="The input app config file",
    )
    parser.add_argument("--playback-filepath", type=str, default="", help="The input playback data file")
    parser.add_argument(
        "--playback-from-json",
        action="store_true",
        help="If playback from pbjson file (boolean flag)",
    )
    args = parser.parse_args()
    playback_frames_app = PlaybackFrames(
        args.config,
        args.playback_filepath,
        args.playback_from_json,
    )
    playback_frames_app.playback()
