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

import logging
import argparse
import json
import os

from pathlib import Path

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.behavior.state_management_e import StateMgmtEWithTripwire
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path
from mdx.analytics.core.utils.processing_stats import ProcessingStats, BatchStats
from mdx.analytics.core.utils.schema_util import dict_frame_to_protobuf_frame, messages_to_map, nv_frame_to_messages
from mdx.analytics.core.utils.space_utilization import SpaceAnalyzer


logger = logging.getLogger(__name__)


class BatchWarehouse3DApp:

    def __init__(
        self,
        config: AppConfig,
        calibration_path: str,
        output_folder: str
    ) -> None:

        self.config = config
        self.coordinate_system = self.config.app_coordinate_system

        self.calibration = CalibrationE(self.config, calibration_path)
        # self.state_mgmt = StateMgmtEWithTripwire(self.config)
        self.space_analyzer = SpaceAnalyzer(self.config.spaceAnalytics, self.calibration)

        output_path = Path(output_folder)
        output_path.mkdir(exist_ok=True)

        self.space_analysis_file = Path(output_path, 'space_analysis.json')
        if self.space_analysis_file.exists():
            self.space_analysis_file.unlink()
            self.space_analysis_file.touch()

        self.alerts_kml_file_path = str(Path(output_path, 'alert_kml.json'))


    def handle(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:

        batch_messages = [ msg for frame in frames for msg in nv_frame_to_messages(frame) ]

        if not batch_messages:
            logger.debug(f"[Process {stats.worker_id}], batch {stats.batch_id} - No messages to process in batch.")
        else:
            logger.info(f"[Process {stats.worker_id}], batch {stats.batch_id} - \
                        Transformed {len(frames)} frame(s) to {len(batch_messages)} message(s)")

            updated_messages = [ self.calibration.transform(msg) for msg in batch_messages ]

            updated_messages_map = messages_to_map(updated_messages)

            # behaviors = [ 
            #     self.state_mgmt.update_behavior(message_key=sensor_id, messages=msgs)     # type: ignore
            #     for sensor_id, msgs in updated_messages_map.items()
            # ]

            if stats.batch_id % 5:
                _, outputs_dict = self.space_analyzer.analyze(updated_messages_map, pallet_width=1.0)

                with open(self.space_analysis_file, "a", encoding="utf-8") as f:
                    for output in outputs_dict:
                        json.dump(output, f, default=str)
                        f.write("\n")

        stats.update(len(frames))
        logger.info(f"[Process {stats.worker_id}], batch {stats.batch_id} - processing speed = {stats.msgs_per_sec} frames/sec")


    def start(self, playback_file_path: str, batch_size: int = 1000, num_batches: int = -1):

        worker_id = 'batch-spatial-3d-0'
        stats = ProcessingStats(worker_id = worker_id)

        with open(playback_file_path, "r", encoding="utf-8") as f:

            batch_id = 1
            batch_frames = []

            for metadata_str in f:

                while len(batch_frames) < batch_size:
                    metadata = json.loads(metadata_str)
                    batch_frames.append(dict_frame_to_protobuf_frame(metadata))

                batch_stats = BatchStats(worker_id = worker_id, batch_id = batch_id)
                self.handle(batch_frames, batch_stats)

                stats.update(batch_stats.num_msgs)

                if num_batches == -1 or batch_id < num_batches:
                    batch_id += 1
                    batch_frames = []
                    batch_stats = BatchStats(worker_id = worker_id, batch_id = batch_id)
                else:
                    break

            logger.info(f"Finished processing {stats.num_msgs} in file {playback_file_path} in {batch_id} batches")


def _load_config(config_path: str) -> AppConfig:

    file_path = validate_file_path(config_path)

    if os.path.exists(file_path) and os.path.isfile(file_path):
        return AppConfig(**load_json_from_file(file_path))

    else:
        logging.error(f"FATAL: Resolved config file path `{file_path}` does NOT exist.")
        exit(1)


def run():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        type=validate_file_path,
        default="configs/warehouse_3d_config.json",
        action=ValidateFile,
        help="The input app config file",
    )

    parser.add_argument(
        "--calibration",
        type=validate_file_path,
        default="configs/calibration_3d.json",
        action=ValidateFile,
        help="The input calibration file",
    )

    parser.add_argument(
        "--playback_filepath",
        type=validate_file_path,
        required=True,
        action=ValidateFile,
        help="The input ds meta data file",
    )

    parser.add_argument(
        "--output_folder",
        type=str,
        required=True,
        help="alert output folder, will create if not exist",
    )

    parser.add_argument(
        "--batch_size",
        type=int,
        default=1000,
        help="number of frames to process in a batch",
    )

    parser.add_argument(
        "--num_batches",
        type=int,
        default=-1, 
        help="number of batches to run"
    )

    args = parser.parse_args()

    config = _load_config(args.config)

    batch_warehouse_3d_app = BatchWarehouse3DApp(config, args.calibration, args.output_folder)
    batch_warehouse_3d_app.start(args.playback_filepath, args.batch_size, args.num_batches)


if __name__ == '__main__':
    run()
