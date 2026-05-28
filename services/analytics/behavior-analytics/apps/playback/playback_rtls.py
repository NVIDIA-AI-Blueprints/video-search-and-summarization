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
from datetime import datetime, timedelta, timezone

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.sink.sink_kafka import SinkKafka
from mdx.analytics.core.stream.sink.sink_base import JsonBytesSerializer, JsonStrSerializer, StrBytesSerializer
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path
from mdx.analytics.core.utils.util import convert_datetime_to_iso_8601_with_z_suffix

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d - %(message)s",
    datefmt="%y/%m/%d %H:%M:%S",
    level=logging.INFO,
)


def json_serializer(object_instance):
    if isinstance(object_instance, datetime):
        return convert_datetime_to_iso_8601_with_z_suffix(object_instance)
    raise TypeError("Type not serializable")


class PlaybackRtls:
    """
    Playback Module for reading playback data and send to kafka to mock streaming data

    :param str config_path: path to the app config file
    :param str playback_filepath: path to the playback data file
    :param bool use_simulated_time: if use simulated timestamp
    ::

        playback_app = PlaybackRtls(config_path, playback_filepath, use_simulated_time)
    """

    def __init__(self, config_path: str, playback_filepath: str, use_simulated_time: bool) -> None:
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
        self.sink_kafka = SinkKafka(self.config)
        self.use_simulated_time = use_simulated_time
        logging.info(f"Playback based on simulated timestamp: {self.use_simulated_time}")

    def playback(self):
        topic = self.config.get_kafka_topic("rtls")
        playloop = self.config.playback.loop
        simulation_timedelta_in_min = self.config.playback.simulationTimedeltaInMin
        logging.info(f"kafka server used is: {self.config.kafka.brokers}")
        logging.info(f"will send data to topic: {topic}")
        logging.info(f"playback loop: {playloop}")
        if self.use_simulated_time:
            logging.info(f"simulated time: {simulation_timedelta_in_min} mins ago")

        try:
            # Init data, which is list of json rtls
            data = []
            logging.info(f"loading data from file: {self.playback_filepath}")
            with open(self.playback_filepath) as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    rtls_obj = json.loads(line_str)
                    rtls_obj["timestamp"] = datetime.strptime(
                        rtls_obj["timestamp"].replace("Z", "+0000"),
                        "%Y-%m-%dT%H:%M:%S.%f%z",
                    )
                    data.append(rtls_obj)
            # Sort data
            data.sort(key=lambda x: x["timestamp"].timestamp())

            logging.info(f"Got {len(data)} rtls objects in total.")

            for loop in range(playloop):
                count = 0
                first_record_time = data[0]["timestamp"]
                first_system_time = datetime.now(timezone.utc)
                first_simulated_time = first_system_time - timedelta(minutes=simulation_timedelta_in_min)
                system_offset_in_ms = (first_system_time - first_record_time).total_seconds() * 1000
                simulated_offset_in_ms = (first_simulated_time - first_record_time).total_seconds() * 1000

                for obj in data:
                    send_time = obj["timestamp"] + timedelta(milliseconds=system_offset_in_ms)
                    timestamp = send_time
                    if self.use_simulated_time:
                        timestamp = obj["timestamp"] + timedelta(milliseconds=simulated_offset_in_ms)
                    obj["timestamp"] = timestamp

                    delay_in_s = (send_time - datetime.now(timezone.utc)).total_seconds()
                    if delay_in_s > 0:
                        time.sleep(delay_in_s)

                    self.sink_kafka.write_msg(
                        dest_key = 'rtls',
                        message = JsonBytesSerializer(obj),
                        key = StrBytesSerializer(obj['place'])
                    )

                    count += 1
                    if count % 1000 == 0:
                        time_elapsed_in_s = (datetime.now(timezone.utc) - first_system_time).total_seconds()
                        msg_rate_in_sec = count / time_elapsed_in_s
                        delay_sec = (datetime.now(timezone.utc) - send_time).total_seconds()
                        logging.debug(JsonStrSerializer(obj)[:100])
                        logging.info(f"msg Rate = {msg_rate_in_sec}/s, count = {count}, delay = {delay_sec}s")
                time.sleep(5)
                logging.info(f"loop = {loop + 1}")

        except Exception as e:
            logging.error(f"An error occurred: {e}")

        finally:
            if self.sink_kafka:
                self.sink_kafka.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=validate_file_path,
        default="configs/rtls_amr_playback_config.json",
        action=ValidateFile,
        help="The input app config file",
    )
    parser.add_argument("--playback-filepath", type=str, default="", help="The input playback data file")
    parser.add_argument(
        "--use-simulated-time",
        action="store_true",
        help="Use simulated timestamp (boolean flag)",
    )
    args = parser.parse_args()
    playback_rtls_app = PlaybackRtls(args.config, args.playback_filepath, args.use_simulated_time)
    playback_rtls_app.playback()
