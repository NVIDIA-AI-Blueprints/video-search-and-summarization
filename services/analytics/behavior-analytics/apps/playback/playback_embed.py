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
from mdx.analytics.core.utils.schema_util import dict_visionllm_to_protobuf_visionllm
from mdx.analytics.core.utils.util import (
    add_offset_in_ms,
    extract_sensor_id,
    get_offset_in_ms,
    get_proto_time_now,
)

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d - %(message)s",
    datefmt="%y/%m/%d %H:%M:%S",
    level=logging.INFO,
)
MILLIS_PER_SECOND = 1000


class PlaybackVisionLLM:

    def __init__(
        self,
        config_path: str,
        playback_filepath: str,
        from_upload: bool,
    ) -> None:

        valid_config_path = validate_file_path(config_path)
        self.playback_filepath = validate_file_path(playback_filepath)
        self.from_upload = from_upload

        if not os.path.exists(valid_config_path):
            logging.error(f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
            exit(1)

        if not os.path.exists(self.playback_filepath):
            logging.error(f"ERROR: The indicated playback file `{self.playback_filepath}` does NOT exist.")
            exit(1)

        self.config = AppConfig(**load_json_from_file(valid_config_path))
        logging.info(f"Read config from {valid_config_path}")


    def playback(self):

        playloop = self.config.playback.loop

        sink = get_sink(self.config)

        start_up_delay_sec = self.config.playback.startUpDelaySec
        logging.info(f"Wait {start_up_delay_sec} sec before playback starts ...")

        time.sleep(start_up_delay_sec)
        logging.info("Starting playback ...")

        try:

            data = []
            logging.info(f"Loading data from file: {self.playback_filepath}")

            with open(self.playback_filepath) as f:
                data = [ dict_visionllm_to_protobuf_visionllm(json.loads(line)) \
                    for line in f if line.strip() ]

            # Sort data
            data.sort(key=lambda x: x.end.ToMilliseconds())

            logging.info(f"Got {len(data)} chunks in total.")

            # filter by sensor ##
            sensors = self.config.playback.sensors

            if len(sensors) > 0:
                logging.info(f"Playing back data only for the below sensors: {sensors}")
                data = [ d for d in data if extract_sensor_id(str(d.sensor.id)) in sensors ]

            if not data:
                logging.warning("No records found, please ensure the playback file has records and verify the list of eligible sensors in the config `playbackSensors`.")
                return

            for loop in range(playloop):

                count = 0

                first_record_time = data[0].timestamp
                first_system_time = get_proto_time_now()

                logging.info(f"First_system_time: {first_system_time.ToDatetime()}")

                system_offset_sec = get_offset_in_ms(first_system_time, first_record_time) // 1000

                for record in data:
                    if not self.from_upload:
                        proto_vllm = nvSchema.VisionLLM()
                        proto_vllm.CopyFrom(record)
                        timestamp = add_offset_in_ms(proto_vllm.timestamp, system_offset_sec * 1000)
                        end = add_offset_in_ms(proto_vllm.end, system_offset_sec * 1000)
                        
                        proto_vllm.timestamp.CopyFrom(timestamp)
                        proto_vllm.end.CopyFrom(end)

                        delay_ms = get_offset_in_ms(end, get_proto_time_now())

                        if delay_ms > 0:
                            time.sleep(delay_ms / MILLIS_PER_SECOND)

                    else:
                        proto_vllm = record

                    logging.info(f"Publishing SENSOR: {proto_vllm.sensor.id}, FID: {proto_vllm.endFrameId}, TS: {proto_vllm.end.ToDatetime()}.")

                    sink.write_msg(
                        dest_key = 'embed',
                        message = ProtoBytesSerializer(proto_vllm),
                        key = StrBytesSerializer(proto_vllm.sensor.id)
                    )

                    if not self.from_upload and ((count := count + 1)) % 1000 == 0:
                        time_elapsed_ms = get_offset_in_ms(get_proto_time_now(), first_system_time)
                        msg_rate_sec = (count * MILLIS_PER_SECOND) / time_elapsed_ms
                        logging.info(f"msg Rate = {msg_rate_sec}/s, count = {count}")

                time.sleep(5)
                logging.info(f"Loop = {loop + 1}")

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
        default="configs/fusion_search_analytics_config.json",
        action=ValidateFile,
        help="The input app config file",
    )

    parser.add_argument(
        "--playback-filepath",
        type=str,
        default="",
        help="The input playback data file"
    )

    parser.add_argument(
        "--from-upload",
        action="store_true",
        help="If the playback data is from uploaded video(s) (True) else from live video stream(s) (False); default False.",
    )

    args = parser.parse_args()

    playback_embed_app = PlaybackVisionLLM(
        args.config,
        args.playback_filepath,
        args.from_upload,
    )
    
    playback_embed_app.playback()
