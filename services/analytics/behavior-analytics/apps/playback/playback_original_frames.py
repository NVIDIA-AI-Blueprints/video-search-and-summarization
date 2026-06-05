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

"""
Playback frames with original timestamps - no simulation or time adjustment.
"""

import argparse
import json
import logging
import os
import time
import traceback

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.sink.sink_factory import get_sink
from mdx.analytics.core.stream.sink.sink_base import ProtoBytesSerializer, StrBytesSerializer
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path
from mdx.analytics.core.utils.schema_util import dict_frame_to_protobuf_frame
from mdx.analytics.core.utils.util import extract_sensor_id

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d - %(message)s",
    datefmt="%y/%m/%d %H:%M:%S",
    level=logging.INFO,
)
MILLIS_PER_SECOND = 1000


class PlaybackOriginalFrames:
    """
    Playback Module for reading playback data and sending to kafka with original timestamps.
    
    No simulation or time adjustment - frames are sent with their original timestamps,
    with delays between frames to maintain original timing.

    :param str config_path: path to the app config file
    :param str playback_filepath: path to the playback data file
    """

    def __init__(
        self,
        config_path: str,
        playback_filepath: str,
    ) -> None:
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

    def playback(self):
        start_up_delay_sec = self.config.playback.startUpDelaySec
        logging.info(f"wait {start_up_delay_sec} sec before playback starts ...")
        time.sleep(start_up_delay_sec)
        logging.info("starting playback with original timestamps...")

        topic = self.config.get_kafka_topic("raw")
        playloop = self.config.playback.loop
        filter_empty_objects = self.config.playback.filterEmptyObjects
        sink = get_sink(self.config)

        logging.info(f"kafka server used is: {self.config.kafka.brokers}")
        logging.info(f"will send data to topic: {topic}")
        logging.info(f"playback loop: {playloop}")
        logging.info(f"playback filter empty objects: {filter_empty_objects}")

        try:
            # Load data as protobuf frames
            data: list[nvSchema.Frame] = []
            logging.info(f"loading data from file: {self.playback_filepath}")
            with open(self.playback_filepath) as f:
                data = [dict_frame_to_protobuf_frame(json.loads(line)) for line in f if line.strip()]
            
            # Sort by timestamp
            data.sort(key=lambda x: x.timestamp.ToMilliseconds())
            
            # Filter frames without objects if needed
            if filter_empty_objects:
                data = [d for d in data if d.objects]

            logging.info(f"Got {len(data)} frames in total.")
            logging.info(f"Got {sum([len(d.objects) for d in data if d.objects])} objects in total.")

            # Filter by sensor if configured
            sensors = self.config.playback.sensors
            if len(sensors) > 0:
                logging.info(f"playing data only from the below sensors: {sensors}")
                data = [d for d in data if extract_sensor_id(str(d.sensorId)) in sensors]

            if not data:
                logging.warning("No frames to playback after filtering.")
                return

            for loop in range(playloop):
                count = 0
                start_time = time.time()

                for i, frame in enumerate(data):
                    # Send frame with original timestamp (no modification)
                    sink.write_msg(
                        dest_key='raw',
                        message=ProtoBytesSerializer(frame),
                        key=StrBytesSerializer(frame.sensorId)
                    )

                    count += 1
                    
                    # Sleep to maintain original timing between frames
                    if i < len(data) - 1:
                        current_ts_ms = frame.timestamp.ToMilliseconds()
                        next_ts_ms = data[i + 1].timestamp.ToMilliseconds()
                        delay_ms = next_ts_ms - current_ts_ms
                        if delay_ms > 0:
                            time.sleep(delay_ms / MILLIS_PER_SECOND)

                    if count % 100 == 0:
                        elapsed = time.time() - start_time
                        msg_rate = count / elapsed if elapsed > 0 else 0
                        logging.info(f"msg Rate = {msg_rate:.1f}/s, count = {count}")

                logging.info(f"loop = {loop + 1}, sent {count} frames")
                if loop < playloop - 1:
                    time.sleep(5)  # Brief pause between loops

        except (KeyboardInterrupt, SystemExit, Exception) as exception:
            logging.info("Main process interrupted. Stopping...")
            logging.error("Caught exception: %s", str(exception))
            logging.error("Exception details:\n%s", traceback.format_exc())

        finally:
            if sink:
                sink.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Playback frames with original timestamps (no simulation)"
    )
    parser.add_argument(
        "--config",
        type=validate_file_path,
        default="configs/frame_playback_config.json",
        action=ValidateFile,
        help="The input app config file",
    )
    parser.add_argument(
        "--playback-filepath",
        type=str,
        required=True,
        help="The input playback data file (pbjson format)",
    )
    args = parser.parse_args()
    
    playback_app = PlaybackOriginalFrames(
        args.config,
        args.playback_filepath,
    )
    playback_app.playback()
