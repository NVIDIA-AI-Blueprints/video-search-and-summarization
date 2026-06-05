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
from mdx.analytics.core.stream.state.behavior.state_management import StateMgmt
from mdx.analytics.core.transform.calibration.calibration import Calibration
from mdx.analytics.core.utils.anomaly_util import AnomalyDetector
from mdx.analytics.core.utils.crs import CoordinateReferenceSystem
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path
from mdx.analytics.core.utils.processing_stats import ProcessingStats, BatchStats
from mdx.analytics.core.utils.schema_util import dict_frame_to_protobuf_frame_legacy, messages_to_map, nv_frame_to_messages


logger = logging.getLogger(__name__)


class BatchSmartCityApp:

    def __init__(
        self,
        config: AppConfig,
        calibration_path: str,
        output_folder: str
    ) -> None:

        self.config = config
        self.coordinate_system = self.config.app_coordinate_system

        self.calibration = Calibration(self.config, calibration_path)
        self.state_mgmt = StateMgmt(self.config)
        self.crs = CoordinateReferenceSystem(config.coordinateReferenceSystem)
        self.anomaly_detector = AnomalyDetector(self.config)

        output_path = Path(output_folder)
        output_path.mkdir(exist_ok=True)

        self.alerts_output_file = Path(output_path, 'alert_behavior.json')
        if self.alerts_output_file.exists():
            self.alerts_output_file.unlink()
            self.alerts_output_file.touch()

        self.alerts_kml_file_path = str(Path(output_path, 'alert_kml.json'))


    def handle(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:

        batch_frames_filtered = self.calibration.filter_frames_by_sensor_id(frames) 

        batch_messages = [ msg for frame in batch_frames_filtered for msg in nv_frame_to_messages(frame) ]

        if not batch_messages:
            logger.debug(f"[Process {stats.worker_id}], batch {stats.batch_id} - No messages to process in batch.")
        else:
            logger.info(f"[Process {stats.worker_id}], batch {stats.batch_id} - \
                        Transformed {len(frames)} frame(s) to {len(batch_messages)} message(s)")

            updated_messages = [ self.calibration.transform(msg) for msg in batch_messages ]
            updated_messages = self.calibration.filter_messages_by_roi(updated_messages) 

            updated_messages_map = messages_to_map(updated_messages)

            behaviors = [ 
                self.state_mgmt.update_behavior(message_key=sensor_id, messages=msgs, crs=self.crs)     # type: ignore
                for sensor_id, msgs in updated_messages_map.items()
            ]

            logger.info(f"[Process {stats.worker_id}], batch {stats.batch_id} - created a total of {len(behaviors)} behavior(s)")

            _, anomalies = self.anomaly_detector.detect_batch(behaviors, self.crs)
            logger.info(f"[Process {stats.worker_id}], batch {stats.batch_id} - a total of {len(anomalies)} anomaly(s) detected")

            alerts = {}
            alerts_trajectory = []

            for alert in anomalies:
                if (old_alert := alerts.get(alert.id)):
                    old_alert["last"] = alert
                else:
                    alerts[alert.id] = {
                        "first": alert,
                        "last": alert
                    }

                    alerts_trajectory.append([(pt.point[1], pt.point[0]) for pt in alert.smoothLocations.coordinates]) # type: ignore

                    with open(self.alerts_output_file, "a", encoding="utf-8") as f:
                        json.dump(alert.model_dump(), f, default=str)
                        f.write("\n")

            self.crs.write_list_of_trajectory_latlon_to_kml(self.alerts_kml_file_path, alerts_trajectory)


        stats.update(len(frames))
        logger.info(f"[Process {stats.worker_id}], batch {stats.batch_id} - processing speed = {stats.msgs_per_sec} frames/sec")


    def start(self, playback_file_path: str, batch_size: int = 1000, num_batches: int = -1):

        worker_id = 'batch-its-0'
        stats = ProcessingStats(worker_id = worker_id)

        with open(playback_file_path, "r", encoding="utf-8") as f:

            batch_id = 1
            batch_frames = []

            for metadata_str in f:

                while len(batch_frames) < batch_size:
                    metadata = json.loads(metadata_str)
                    batch_frames.append(dict_frame_to_protobuf_frame_legacy(metadata))

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
        default="configs/smart_city_config.json",
        action=ValidateFile,
        help="The input app config file",
    )

    parser.add_argument(
        "--calibration",
        type=validate_file_path,
        default="configs/calibration_smart_city_v3.0.json",
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

    batch_smart_city_app = BatchSmartCityApp(config, args.calibration, args.output_folder)
    batch_smart_city_app.start(args.playback_filepath, args.batch_size, args.num_batches)


if __name__ == '__main__':
    run()
