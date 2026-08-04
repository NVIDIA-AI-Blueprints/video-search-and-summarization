# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from dataclasses import dataclass, field
from logging import info
from typing import Dict, List, Optional, Union
import json
import os
from urllib.parse import urlparse, urlunparse


@dataclass
class Sensor:
    name: str
    url: str
    group_id: str|None = None
    region: str|None = None


@dataclass
class SensorMapping:
    sensors: Dict[str, Sensor] = field(default_factory=dict)

    @staticmethod
    def _get_sensor_id_to_rtsp_mapping(sb_output: List[Dict]) -> Dict:
        sensor_id_to_rtsp_mapping = {}
        for grp in sb_output:
            for rtsp_url in grp["rtspURLs"]:
                sensor_id_to_rtsp_mapping[rtsp_url["name"]] = rtsp_url["url"]
        return sensor_id_to_rtsp_mapping

    @staticmethod
    def _format_ip_in_url(
        url: str,
    ):  # TODO: extend to support multi-pod sensor bridge deployments
        env_ip = os.environ.get("SENSOR_BRIDGE_RTSP_SERVICE_NAME")
        if not env_ip:
            return url
        parsed_url = urlparse(url)
        host_parts = parsed_url.netloc.split(":")
        host_parts[0] = env_ip
        new_netloc = ":".join(host_parts)
        updated_url = urlunparse(parsed_url._replace(netloc=new_netloc))
        return updated_url

    @classmethod
    def generate(
        cls,
        sb_output: List[Dict],
        calibration_data: Dict | None,
        logger,
        info_source: str,
    ) -> "SensorMapping":
        logger.debug(f"Generating sensor mapping with source={info_source}")
        logger.debug(f"Input data: {len(sb_output)} items, has_calibration={bool(calibration_data)}")
        mapping = cls()
        if info_source == "file":
            logger.debug("Processing sensor data from file (camera_name, rtsp_url, group_id?, region?)")
            cal_sensors_by_id = {}
            if calibration_data and "sensors" in calibration_data:
                for cal_sensor in calibration_data["sensors"]:
                    cal_sensors_by_id[cal_sensor.get("id")] = cal_sensor
            for file_sensor in sb_output:
                if not isinstance(file_sensor, dict):
                    logger.warning(f"Skipping invalid file sensor entry: not a dict")
                    continue
                camera_name = file_sensor.get("camera_name")
                rtsp_url = file_sensor.get("rtsp_url")
                if not camera_name or not rtsp_url:
                    logger.warning(
                        f"Skipping file sensor entry missing camera_name or rtsp_url: {file_sensor}"
                    )
                    continue
                group_id = file_sensor.get("group_id")
                region = file_sensor.get("region")
                if (group_id is None or region is None) and camera_name in cal_sensors_by_id:
                    cal = cal_sensors_by_id[camera_name]
                    if group_id is None and "group" in cal and "name" in cal["group"]:
                        group_id = cal["group"]["name"]
                    if region is None and "region" in cal and "place" in cal:
                        pls = [
                            p["value"]
                            for p in cal["place"]
                            if p.get("name") == cal.get("region", {}).get("placeLevel")
                        ]
                        if pls:
                            region = str(pls[0])
                sensor_url = cls._format_ip_in_url(rtsp_url)
                sensor = Sensor(
                    name=camera_name,
                    url=sensor_url,
                    group_id=group_id,
                    region=region,
                )
                mapping.sensors[sensor.name] = sensor
                logger.debug(f"Added file sensor {camera_name} to mapping")
        elif info_source == "msb":
            logger.debug("Processing sensor data from MSB")
            sensor_id_rtsp_url_mapping = cls._get_sensor_id_to_rtsp_mapping(sb_output)
            logger.debug(f"Extracted {len(sensor_id_rtsp_url_mapping)} RTSP URL mappings from MSB output")
            if calibration_data:
                logger.debug(f"Processing {len(calibration_data.get('sensors', []))} sensors from calibration data")
                for sensor in calibration_data["sensors"]:
                    sensor_id = sensor["id"]
                    sensor_group = sensor["group"]["name"]
                    sensor_region = str(
                        [
                            pls["value"]
                            for pls in sensor["place"]
                            if pls["name"] == sensor["region"]["placeLevel"]
                        ][0]
                    )
                    if sensor_id not in sensor_id_rtsp_url_mapping:
                        logger.warning(
                            f"{sensor_id} from calibration data not found in sensor bridge output, hence not adding it to the sensor mapping."
                        )
                        continue

                    else:
                        logger.debug(f"Mapping sensor {sensor_id} with group={sensor_group}, region={sensor_region}")
                        sensor_url = cls._format_ip_in_url(
                            sensor_id_rtsp_url_mapping[sensor_id]
                        )
                        logger.debug(f"Formatted sensor URL: {sensor_url}")
                        sensor = Sensor(
                            name=sensor_id,
                            group_id=sensor_group,
                            region=sensor_region,
                            url=sensor_url,
                        )
                        mapping.sensors[sensor.name] = sensor
                        logger.debug(f"Added sensor {sensor_id} to mapping")
                [
                    logger.warning(
                        f"{sb_sensor} from sensor bridge output is not present in calibration file, hence not adding it to the sensor mapping."
                    )
                    for sb_sensor in sensor_id_rtsp_url_mapping.keys()
                    if sb_sensor not in mapping.sensors
                ]
            else:
                logger.debug("No calibration data, creating sensors without region/group info")
                for sensor_id, sensor_url in sensor_id_rtsp_url_mapping.items():
                    logger.debug(f"Creating sensor {sensor_id} without calibration data")
                    sensor_url = cls._format_ip_in_url(sensor_url)
                    sensor = Sensor(
                        name=sensor_id,
                        url=sensor_url
                    )
                    mapping.sensors[sensor.name] = sensor
                    logger.debug(f"Added sensor {sensor_id} to mapping")
        elif info_source == "nvstreamer":
            logger.debug("Processing sensor data from Nvstreamer (VST format)")
            # Handle VST (Video Streaming Toolkit) data format
            for vst_item in sb_output:
                logger.debug(f"Processing VST item: {vst_item.get('event', {}).get('camera_id', 'unknown')}")
                if 'event' in vst_item:
                    event = vst_item['event']
                    sensor_id = event.get('camera_id') or event.get('camera_name')
                    sensor_url = event.get('camera_url')
                    
                    if sensor_id and sensor_url:
                        logger.debug(f"Processing VST sensor: {sensor_id}, url: {sensor_url}")
                        # Extract group_id and region from calibration_data if available
                        group_id = None
                        region = None
                        
                        if calibration_data and "sensors" in calibration_data:
                            logger.debug(f"Looking for calibration data for sensor {sensor_id}")
                            # Find the matching sensor in calibration data (it's a list)
                            matching_sensor = None
                            for cal_sensor in calibration_data["sensors"]:
                                if cal_sensor.get("id") == sensor_id:
                                    matching_sensor = cal_sensor
                                    logger.debug(f"Found matching calibration data for sensor {sensor_id}")
                                    break
                            
                            if matching_sensor:
                                # Extract group_id
                                if "group" in matching_sensor and "name" in matching_sensor["group"]:
                                    group_id = matching_sensor["group"]["name"]
                                    logger.debug(f"Extracted group_id: {group_id}")
                                
                                # Extract region
                                if "region" in matching_sensor and "placeLevel" in matching_sensor["region"]:
                                    region_list = [
                                        pls["value"]
                                        for pls in matching_sensor.get("place", [])
                                        if pls["name"] == matching_sensor["region"]["placeLevel"]
                                    ]
                                    if region_list:
                                        region = str(region_list[0])
                                        logger.debug(f"Extracted region: {region}")
                        
                        logger.debug(f"Creating VST sensor with group_id={group_id}, region={region}")
                        sensor = Sensor(
                            name=sensor_id,
                            url=sensor_url,
                            group_id=group_id,
                            region=region
                        )
                        mapping.sensors[sensor.name] = sensor
                        logger.debug(f"Added VST sensor {sensor_id} to mapping")
                    else:
                        logger.warning(
                            f"Skipping VST item due to missing camera_id or camera_url: {vst_item}"
                        )
                else:
                    logger.warning(
                        f"Skipping VST item due to missing event field: {vst_item}"
                    )
        else:
            raise ValueError(
                f"Invalid info_source: {info_source}. Must be 'msb', 'nvstreamer', or 'file'."
            )
        logger.debug(f"Generated sensor mapping with {len(mapping.sensors)} sensors")
        return mapping

    def get_sensor_info(self, sensor_name: str) -> Sensor:
        sensor = self.sensors.get(sensor_name)
        if sensor:
            return sensor
        return None

    def get_group_names(self) -> List[str]:
        return list(
            set(
                f"{sensor.region}|{sensor.group_id}" for sensor in self.sensors.values()
            )
        )

    def get_sensor_names(self) -> List[Dict[str, str]]:
        return [
            f"{sensor.name}|{sensor.group_id}|{sensor.url}"
            for sensor in self.sensors.values()
        ]

    def save_to_file(self, file_path: str):
        with open(file_path, "w") as f:
            json.dump(self.__dict__, f, default=lambda o: o.__dict__)

    @classmethod
    def load_from_file(cls, file_path: str) -> Union["SensorMapping", None]:
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                data = json.load(f)
            mapping = cls()
            for sensor_data in data.get("sensors", {}).values():
                sensor = Sensor(**sensor_data)
                mapping.sensors[sensor.name] = sensor
            return mapping
        return None
