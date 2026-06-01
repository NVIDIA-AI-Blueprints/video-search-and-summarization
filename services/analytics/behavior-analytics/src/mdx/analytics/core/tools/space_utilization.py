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

import argparse
import base64
import logging
import os
import time
from typing import Any

import cv2

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE, CalibrationES
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path
from mdx.analytics.core.utils.schema_util import dict_frame_to_protobuf_frame, messages_to_map, nv_frame_to_messages
from mdx.analytics.core.utils.space_utilization import SpaceAnalyzer
from mdx.analytics.core.utils.util import strRGB_to_tupleBGR


class SpaceUtilization:
    """
    Controller module for Space Utilization Analytics.

    :param str config_path: Path to the app config file.
    :param str calibration_path: Path to the calibration file in JSON format. If not provided, calibration will be initialized from API.
    :ivar AppConfig config: Application configuration object.
    :ivar CalibrationE calibration: Calibration module for coordinate transformation.
    :ivar SpaceAnalyzer space_analyzer: Space analysis module.
    :ivar float cam_font_size: Font size for camera annotations.
    :ivar bool in_3d_mode: Whether to use 3D mode for visualization.

    Examples::
        >>> space_utilization_app = SpaceUtilization(config_path, calibration_path)
    """

    def __init__(self, config_path: str, calibration_path: str | None = None) -> None:
        # Make sure the config file exists
        valid_config_path = validate_file_path(config_path)
        if not os.path.exists(valid_config_path):
            logging.error(f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
            exit(1)
        self.config = AppConfig(**load_json_from_file(valid_config_path))
        logging.info(f"Read config from {valid_config_path}\n")
        if calibration_path:
            print("initialize calibration from file")
            self.calibration = CalibrationE(self.config, calibration_file_path=calibration_path)
        else:
            print("initialize calibration from API")
            self.calibration = CalibrationES(self.config)
        self.space_analyzer = SpaceAnalyzer(self.config.space_analytics, self.calibration)
        self.cam_font_size = float(self.config.get_app_config("cameraFontSize", "2"))
        self.in_3d_mode = self.config.in_3d_mode

    def space_analysis_on_demand(
        self, detections: str, pallet_width: float = 1.0, target_zones: list[str] = [], frames_from_file: bool = False
    ) -> list[dict[str, Any]]:
        """
        Analyze space utilization from detection data.

        :param str detections: Detection data or path to JSON file containing detection data.
        :param float pallet_width: Width of pallets in meters. Defaults to 1.0.
        :param list[str] target_zones: List of zone IDs to analyze. If empty, analyzes all zones.
        :param bool frames_from_file: Whether detections parameter is a file path. Defaults to False.
        :return list[dict[str, Any]]: List of dictionaries containing analysis results for each zone.

        Examples::
            >>> outputs = space_utilization_app.space_analysis_on_demand(
            ...     detections="detections.json",
            ...     pallet_width=1.0,
            ...     target_zones=["zone1", "zone2"],
            ...     frames_from_file=True
            ... )
        """
        batch_frames = []
        if frames_from_file:
            detections = load_json_from_file(detections)
        frames_data = detections["bevFrames"]
        for frame_data in frames_data:
            pb_data = dict_frame_to_protobuf_frame(frame_data)
            batch_frames.append(pb_data)

        batch_messages = [msg for frame in batch_frames for msg in nv_frame_to_messages(frame)]
        messages_map = messages_to_map(batch_messages)
        _, outputs_dict = self.space_analyzer.analyze(
            messages_map, pallet_width=pallet_width, target_zones=target_zones
        )

        return outputs_dict

    def projection_3d_on_demand(
        self,
        outputs_dict: list[dict[str, Any]],
        image_path: str,
        sensor_id: str,
        output_size: tuple[int, int] = (960, 540),
        show_unsafe_pallets: bool = True,
        export_base64: bool = True,
        font_size: float | None = None,
        title: str | None = None,
    ) -> Any:
        """
        Project 3D analysis results onto a 2D image.

        :param list[dict[str, Any]] outputs_dict: Analysis results from space_analysis_on_demand.
        :param str image_path: Path to the image file to overlay results on.
        :param str sensor_id: ID of the camera sensor.
        :param tuple[int, int] output_size: Output image dimensions (width, height). Defaults to (960, 540).
        :param bool show_unsafe_pallets: Whether to show unsafe pallets. Defaults to True.
        :param bool export_base64: Whether to return image as base64 string. Defaults to True.
        :param float | None font_size: Font size for annotations. If None, uses default from config.
        :param str | None title: Title to display on image. If None, uses sensor_id.
        :return str | np.ndarray: Base64 encoded image string if export_base64=True, otherwise numpy array.

        Examples::
            >>> image = space_utilization_app.projection_3d_on_demand(
            ...     outputs_dict=analysis_results,
            ...     image_path="camera1.jpg",
            ...     sensor_id="Camera_01",
            ...     show_unsafe_pallets=True
            ... )
        """
        cam_font_size = font_size if font_size else self.cam_font_size
        overlay_image = cv2.imread(image_path)
        for output_dict in outputs_dict:
            buffer_zone_id = output_dict["id"]
            if show_unsafe_pallets:
                unsafe_bbox3ds = output_dict["bboxes"]["unsafeObj"]
                overlay_image = self.space_analyzer.overlay_bbox3d_on_image(
                    unsafe_bbox3ds, overlay_image, sensor_id, buffer_zone_id, cam_font_size=cam_font_size, title=title
                )
            else:
                free_area_coord_list_3d = output_dict["layouts"]["freeSpace"]
                utilizable_free_area_coord_list_3d = output_dict["layouts"]["utilizableFreeSpace"]
                occupied_bbox3ds = output_dict["bboxes"]["occupiedObj"]
                advanced_overlay = self.config.get_bool_app_config("advancedOverlay", "true")
                overlay_image = self.space_analyzer.overlay_free_space_on_image(
                    free_area_coord_list_3d,
                    utilizable_free_area_coord_list_3d,
                    occupied_bbox3ds,
                    overlay_image,
                    sensor_id,
                    buffer_zone_id,
                    cam_font_size=cam_font_size,
                    title=title,
                    advanced_overlay=advanced_overlay,
                )
        overlay_image = cv2.resize(overlay_image, output_size, interpolation=cv2.INTER_LINEAR)
        if export_base64:
            try:
                _, buffer = cv2.imencode(".png", overlay_image)
                if buffer is None:
                    raise ValueError("Failed to encode image")
                base64_string = base64.b64encode(buffer).decode("utf-8")
                return base64_string
            except Exception as e:
                print(f"Error converting to Base64: {e}")
                return None

        return overlay_image

    def viz_3d_detections(
        self,
        mdx_bev_detections: str | dict[str, Any],
        image_path: str,
        sensor_id: str,
        output_size: tuple[int, int] = (960, 540),
        frames_from_file: bool = False,
        color: tuple[int, int, int] = (0, 0, 255),
        export_base64: bool = False,
        font_size: float | None = None,
        title: str | None = None,
    ) -> Any:
        """
        Visualize 3D detections on a 2D image.

        :param str | dict[str, Any] mdx_bev_detections: Bird's eye view detection data or path to JSON file.
        :param str image_path: Path to the image file to overlay detections on.
        :param str sensor_id: ID of the camera sensor.
        :param tuple[int, int] output_size: Output image dimensions (width, height). Defaults to (960, 540).
        :param bool frames_from_file: Whether mdx_bev_detections is a file path. Defaults to False.
        :param tuple[int, int, int] color: BGR color for detection visualization. Defaults to (0, 0, 255).
        :param bool export_base64: Whether to return image as base64 string. Defaults to False.
        :param float | None font_size: Font size for annotations. Defaults to None.
        :param str | None title: Title to display on image. Defaults to None.
        :return str | np.ndarray: Base64 encoded image string if export_base64=True, otherwise numpy array.

        Examples::
            >>> image = space_utilization_app.viz_3d_detections(
            ...     mdx_bev_detections="detections.json",
            ...     image_path="camera1.jpg",
            ...     sensor_id="Camera_01",
            ...     frames_from_file=True
            ... )
        """
        cam_font_size = font_size if font_size else self.cam_font_size
        overlay_image = cv2.imread(image_path)
        batch_frames = []
        if frames_from_file:
            mdx_bev_detections = load_json_from_file(mdx_bev_detections)
        frames_data = mdx_bev_detections["bevFrames"]
        for frame_data in frames_data:
            pb_data = dict_frame_to_protobuf_frame(frame_data)
            batch_frames.append(pb_data)

        batch_messages = [msg for frame in batch_frames for msg in nv_frame_to_messages(frame)]
        messages_map = messages_to_map(batch_messages)
        overlay_image = self.space_analyzer.draw_bboxes(
            messages_map, overlay_image, sensor_id, color=color, cam_font_size=cam_font_size, title=title
        )

        overlay_image = cv2.resize(overlay_image, output_size, interpolation=cv2.INTER_LINEAR)
        if export_base64:
            try:
                _, buffer = cv2.imencode(".png", overlay_image)
                if buffer is None:
                    raise ValueError("Failed to encode image")
                base64_string = base64.b64encode(buffer).decode("utf-8")
                return base64_string
            except Exception as e:
                print(f"Error converting to Base64: {e}")
                return None

        return overlay_image

    def viz_proximity(
        self,
        mdx_proximity_detections: str | dict[str, Any],
        image_path: str,
        sensor_id: str,
        output_size: tuple[int, int] = (960, 540),
        frames_from_file: bool = False,
        export_base64: bool = False,
        title: str | None = None,
        font_size: float | None = None,
    ) -> Any:
        """
        Visualize proximity alerts on a 2D image (Deprecated after GTC2025).

        :param str | dict[str, Any] mdx_proximity_detections: Proximity detection data or path to JSON file.
        :param str image_path: Path to the image file to overlay alerts on.
        :param str sensor_id: ID of the camera sensor.
        :param tuple[int, int] output_size: Output image dimensions (width, height). Defaults to (960, 540).
        :param bool frames_from_file: Whether mdx_proximity_detections is a file path. Defaults to False.
        :param bool export_base64: Whether to return image as base64 string. Defaults to False.
        :param str | None title: Title to display on image. Defaults to None.
        :param float | None font_size: Font size for annotations. Defaults to None.
        :return str | np.ndarray: Base64 encoded image string if export_base64=True, otherwise numpy array.

        Examples::
            >>> image = space_utilization_app.viz_proximity(
            ...     mdx_proximity_detections="proximity.json",
            ...     image_path="camera1.jpg",
            ...     sensor_id="Camera_01",
            ...     frames_from_file=True
            ... )
        """
        ### This function is deprecated after GTC2025, please use viz_frame_alerts instead
        cam_font_size = font_size if font_size else self.cam_font_size
        overlay_image = cv2.imread(image_path)
        if frames_from_file:
            mdx_proximity_detections = load_json_from_file(mdx_proximity_detections)
        frames_data = mdx_proximity_detections["alerts"]
        # assume data is already in order (descending) from mdxAPI
        if len(frames_data) > 0:
            frame_data = frames_data[0]
            overlay_image = self.space_analyzer.draw_proximity(frame_data, overlay_image, sensor_id)
            title_text = title if title else sensor_id
            overlay_image = self.space_analyzer.annotate_title(overlay_image, title_text, cam_font_size)

        overlay_image = cv2.resize(overlay_image, output_size, interpolation=cv2.INTER_LINEAR)
        if export_base64:
            try:
                _, buffer = cv2.imencode(".png", overlay_image)
                if buffer is None:
                    raise ValueError("Failed to encode image")
                base64_string = base64.b64encode(buffer).decode("utf-8")
                return base64_string
            except Exception as e:
                print(f"Error converting to Base64: {e}")
                return None

        return overlay_image

    def viz_frame_alerts(
        self,
        mdx_frame_alerts: str | dict[str, Any],
        image_path: str,
        sensor_id: str,
        output_size: tuple[int, int] = (960, 540),
        frames_from_file: bool = False,
        export_base64: bool = False,
        title: str | None = None,
        font_size: float | None = None,
    ) -> Any:
        """
        Visualize frame alerts including proximity, confined area, and restricted area alerts.

        This method:
        1. Reads and processes frame alerts data
        2. Applies visualization for different alert types:
        - Proximity alerts between objects
        - Confined area violations
        - Restricted area violations
        3. Applies color and thickness settings from config
        4. Adds title and annotations
        5. Resizes and optionally exports as base64

        :param str | dict[str, Any] mdx_frame_alerts: Frame alerts data or path to JSON file containing alerts.
        :param str image_path: Path to the image file to overlay alerts on.
        :param str sensor_id: ID of the camera sensor.
        :param tuple[int, int] output_size: Output image dimensions (width, height). Defaults to (960, 540).
        :param bool frames_from_file: Whether mdx_frame_alerts is a file path. Defaults to False.
        :param bool export_base64: Whether to return image as base64 string. Defaults to False.
        :param str | None title: Title to display on image. If None, uses sensor_id.
        :param float | None font_size: Font size for annotations. If None, uses default from config.
        :return str | np.ndarray: Base64 encoded image string if export_base64=True, otherwise numpy array.

        Examples::
            >>> space_utilization_app = SpaceUtilization(config_path)
            >>> image = space_utilization_app.viz_frame_alerts(
            ...     mdx_frame_alerts="alerts.json",
            ...     image_path="camera1.jpg",
            ...     sensor_id="Camera_01",
            ...     frames_from_file=True
            ... )
            >>> print("Frame alerts visualization completed")
        """
        cam_font_size = font_size if font_size else self.cam_font_size
        overlay_image = cv2.imread(image_path)
        if frames_from_file:
            mdx_frame_alerts = load_json_from_file(mdx_frame_alerts)
        frames_data = mdx_frame_alerts["alerts"]
        # assume data is already in order (descending) from mdxAPI
        if len(frames_data) > 0:
            frame_data = frames_data[0]
            if "proximity" in frame_data:
                center_obj_type = self.config.get_app_config("proximityCenterObjType", "Humanoid")
                thickness = int(self.config.get_app_config("proximityThickness", "3"))
                overlay_image = self.space_analyzer.draw_proximity(
                    frame_data,
                    overlay_image,
                    sensor_id,
                    center_obj_type=center_obj_type,
                    in_3d_mode=self.in_3d_mode,
                    thickness=thickness,
                )
            if "confinedArea" in frame_data:
                bbox_color = strRGB_to_tupleBGR(self.config.get_app_config("confinedBboxColorRGB", "255,128,0"))
                roi_color = strRGB_to_tupleBGR(self.config.get_app_config("confinedROIColorRGB", "255,128,0"))
                bbox_thickness = int(self.config.get_app_config("confinedBboxThickness", "3"))
                roi_thickness = int(self.config.get_app_config("confinedROIThickness", "4"))
                overlay_image = self.space_analyzer.draw_confined_area(
                    frame_data,
                    overlay_image,
                    sensor_id,
                    bbox_color=bbox_color,
                    roi_color=roi_color,
                    in_3d_mode=self.in_3d_mode,
                    bbox_thickness=bbox_thickness,
                    roi_thickness=roi_thickness,
                )
            if "restrictedArea" in frame_data:
                bbox_color = strRGB_to_tupleBGR(self.config.get_app_config("restrictedBboxColorRGB", "153,0,0"))
                roi_color = strRGB_to_tupleBGR(self.config.get_app_config("restrictedROIColorRGB", "153,0,0"))
                bbox_thickness = int(self.config.get_app_config("restrictedBboxThickness", "3"))
                roi_thickness = int(self.config.get_app_config("restrictedROIThickness", "2"))
                overlay_image = self.space_analyzer.draw_restricted_area(
                    frame_data,
                    overlay_image,
                    sensor_id,
                    bbox_color=bbox_color,
                    roi_color=roi_color,
                    in_3d_mode=self.in_3d_mode,
                    bbox_thickness=bbox_thickness,
                    roi_thickness=roi_thickness,
                )
            title_text = title if title else sensor_id
            overlay_image = self.space_analyzer.annotate_title(overlay_image, title_text, cam_font_size)
        overlay_image = cv2.resize(overlay_image, output_size, interpolation=cv2.INTER_LINEAR)
        if export_base64:
            try:
                _, buffer = cv2.imencode(".png", overlay_image)
                if buffer is None:
                    raise ValueError("Failed to encode image")
                base64_string = base64.b64encode(buffer).decode("utf-8")
                return base64_string
            except Exception as e:
                print(f"Error converting to Base64: {e}")
                return None

        return overlay_image


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=validate_file_path,
        default="configs/space_utilization_surf_config.json",
        action=ValidateFile,
        help="The input app config file",
    )
    parser.add_argument(
        "--calibration",
        type=validate_file_path,
        default="configs/calibration_space_utilization.json",
        action=ValidateFile,
        help="The input app calibration file",
    )

    args = parser.parse_args()
    space_utilization_tool = SpaceUtilization(args.config)
    # space_utilization_tool = SpaceUtilization(args.config, args.calibration)

    space_ulization_analysis_time_start = time.time()
    detections = load_json_from_file("configs/bev_frames_sample_data.txt")
    outputs_dict = space_utilization_tool.space_analysis_on_demand(
        detections=detections,
        pallet_width=1.0,
        #    target_zones=['buffer_zone_1', 'buffer_zone_2', 'buffer_zone_3'],
        target_zones=[],
        frames_from_file=False,
    )
    # outputs_dict = space_utilization_tool.space_analysis_on_demand(detections='configs/bev_frames_sample_data.txt', pallet_width=1.0, frames_from_file=True)
    space_ulization_analysis_time_end = time.time()
    space_ulization_analysis_time = round(space_ulization_analysis_time_end - space_ulization_analysis_time_start, 2)
    print(f"space_ulization_analysis_time: {space_ulization_analysis_time} sec")

    projection_3d_time_start = time.time()
    overlay_image = space_utilization_tool.projection_3d_on_demand(
        outputs_dict,
        image_path="configs/camera_0002_sample.png",
        sensor_id="Camera_02",
        show_unsafe_pallets=False,
        export_base64=False,
    )
    projection_3d_time_end = time.time()
    projection_3d_time = round(projection_3d_time_end - projection_3d_time_start, 2)
    print(f"projection_3d_time: {projection_3d_time} sec")
    output_img = "configs/tmp.png"
    cv2.imwrite(output_img, overlay_image)
    print(f"projection image saved to: {output_img}")

    output_image = space_utilization_tool.viz_3d_detections(
        detections, image_path="configs/camera_0001_sample.png", sensor_id="Camera_01", frames_from_file=False
    )
    output_path = "configs/tmp1.png"
    cv2.imwrite(output_path, output_image)
    print(f"projection image saved to: {output_path}")

    # mdx_proximity_detections = load_json_from_file('configs/social_distancing_sample_data.txt')
    mdx_proximity_detections = load_json_from_file("configs/frame_alerts_3d_sample_data.txt")
    output_image = space_utilization_tool.viz_proximity(
        mdx_proximity_detections,
        image_path="configs/camera_0001_sample.png",
        sensor_id="Camera_01",
        frames_from_file=False,
    )
    output_path = "configs/tmp2.png"
    cv2.imwrite(output_path, output_image)
    print(f"projection image saved to: {output_path}")

    ## function for new frame alerts data
    mdx_frame_alerts = load_json_from_file("configs/frame_alerts_3d_sample_data.txt")
    output_image = space_utilization_tool.viz_frame_alerts(
        mdx_frame_alerts, image_path="configs/camera_0001_sample.png", sensor_id="Camera_01", frames_from_file=False
    )
    output_path = "configs/tmp3.png"
    cv2.imwrite(output_path, output_image)
    print(f"projection image saved to: {output_path}")

    # mdx_proximity_detections = load_json_from_file('configs/frame_alerts_2d_sample_data.txt')
    # output_image = space_utilization_tool.viz_frame_alerts(mdx_proximity_detections,
    #                                                     #    image_path="configs/Nth_Street_Cafe_Entrance.jpg",
    #                                                        image_path="configs/Endeavor_Cafeteria.jpg",
    #                                                        sensor_id="Endeavor_Cafeteria",
    #                                                        frames_from_file=False)
    # output_path = 'configs/tmp4.png'
    # cv2.imwrite(output_path, output_image)
    # print(f'projection image saved to: {output_path}')
