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
import threading
from typing import Any

import numpy as np

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Bbox3d, Message, Coordinate, ROI, SensorInfo
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.transform.calibration.calibration import Calibration
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase, CalibrationType
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE
from mdx.analytics.core.transform.calibration.calibration_i import CalibrationI
from mdx.analytics.core.utils.io_utils import load_json_from_file

logger = logging.getLogger(__name__)


class DynamicCalibration(CalibrationBase):
    """
    Dynamic calibration wrapper that handles the transition from no calibration to typed calibration.
    
    This class specifically handles the case where:
    1. Application starts with NO calibration file (None)
    2. A calibration file is added later (any type: image, cartesian, or geo)
    3. The class switches ONCE from no-file to the appropriate implementation
    4. No further changes are allowed after this initial switch
    
    File watching uses the inherited :class:`CalibrationBase.CalibrationFileMonitor`
    (``on_moved`` + dotfile/``.json`` filter, see the parent's docstring for the
    atomic-write contract). The parent dispatches detected file events to
    :meth:`reload_data`, which we override below to add the one-time switch.

    :ivar AppConfig config: Configuration object for the application.
    :ivar CalibrationBase _calibrator: The active calibration strategy being used.
    :ivar bool _started_with_file: Flag indicating if we started with a calibration file.
    :ivar threading.Lock _switch_lock: Lock for thread-safe one-time switching.

    Examples::
        >>> config = AppConfig()
        >>> # Start with no calibration file (uses CalibrationI)
        >>> calibration = DynamicCalibration(config, None)
        >>> # Later, ANY calibration file is added (image, cartesian, or geo)
        >>> # The class switches to the appropriate type (one-time switch)
    """

    def __init__(self, config: AppConfig, calibration_path: str | None) -> None:
        """
        Initialize the dynamic calibration with appropriate implementation.
        
        :param AppConfig config: Configuration object
        :param str | None calibration_path: Initial calibration file path
        """
        self.config = config
        self._started_with_file = calibration_path is not None
        self._switch_lock = threading.Lock()  # Lock to ensure thread-safe one-time switching
        self.observer = None
        
        # Initialize calibrator based on whether we have a calibration file
        if not calibration_path:
            # No calibration file - start with CalibrationI, allow future switch
            logger.info("No calibration file provided, initializing with CalibrationI (switch allowed)")
            self._calibrator = CalibrationI(self.config, None)
        else:
            # Have calibration file - create appropriate instance, no future switches
            logger.info(f"Starting with calibration file: {calibration_path} (no switches allowed)")
            cal_type = self.get_calibration_type(calibration_path)
            self._create_typed_calibration(cal_type, calibration_path)

    def get_calibration_type(self, calibration_path: str | None) -> CalibrationType:
        """
        Get calibration type from file or return default.
        
        :param str | None calibration_path: Path to calibration file
        :return CalibrationType: Calibration type enum
        """
        try:
            data = load_json_from_file(calibration_path)
            cal_type_str = data.get("calibrationType", "").lower()
            
            # Map string to enum
            for cal_type in CalibrationType:
                if cal_type.value == cal_type_str:
                    return cal_type
            
            logger.warning(f"Unknown calibration type '{cal_type_str}' in {calibration_path}, defaulting to IMAGE")
        except Exception as e:
            logger.warning(f"Error reading calibration type from {calibration_path}: {e}, defaulting to IMAGE")
        
        return CalibrationType.IMAGE  # Default fallback
    
    def _create_typed_calibration(self, cal_type: CalibrationType, calibration_path: str | None) -> None:
        """
        Create the appropriate calibration implementation based on type.
        
        :param CalibrationType cal_type: The calibration type enum
        :param str | None calibration_path: Path to the calibration file (optional)
        """
        # Clean up the old implementation if it exists (when switching)
        if hasattr(self, '_calibrator') and self._calibrator:
            self._calibrator.close()

        # Create new implementation based on type
        if cal_type == CalibrationType.CARTESIAN:
            logger.info(f"Creating CalibrationE for {cal_type.value} type")
            self._calibrator = CalibrationE(self.config, calibration_path)
        elif cal_type == CalibrationType.GEO:
            logger.info(f"Creating Calibration for {cal_type.value} type")
            self._calibrator = Calibration(self.config, calibration_path)
        else:  # CalibrationType.IMAGE
            logger.info(f"Creating CalibrationI for {cal_type.value} type")
            self._calibrator = CalibrationI(self.config, calibration_path)
    
    def _handle_calibration_change(self, file_path: str) -> None:
        """
        Handle one-time switch from no calibration to typed calibration.

        This method is only called when we started with no file (checked by :meth:`reload_data`).
        It switches to the appropriate calibration type based on the file content.

        :param str file_path: Path to the new calibration file
        """
        # Get calibration type and create appropriate implementation
        cal_type = self.get_calibration_type(file_path)
        logger.info(f"Switching from no-calibration to {cal_type.value} calibration")
        self._create_typed_calibration(cal_type, file_path)

    def reload_data(self, file_path: str) -> None:
        """
        Apply a calibration file change detected by the inherited watcher.

        Overrides :meth:`CalibrationBase.reload_data` to add the one-time
        switch semantic that defines :class:`DynamicCalibration`:

        * If the instance was constructed with ``calibration_path=None``, the
          first file event triggers a one-time switch to the typed calibration
          (image / cartesian / geo) inferred from the file's ``calibrationType``.
        * For every subsequent event -- and for instances constructed with a
          file -- the call is delegated to the inner ``_calibrator``'s
          ``reload_data`` (which performs the per-type merge/replace via the
          parent's :meth:`update_calibration_info`).

        Thread-safe: the switch decision and ``_calibrator`` replacement run
        under :attr:`_switch_lock` so a burst of file events can't double-switch.

        :param str file_path: Path to the updated calibration file (already
            validated as a dotfile-free ``.json`` by the parent's watcher).
        :return: None
        """
        with self._switch_lock:
            if not self._started_with_file:
                logger.info(f"New calibration file detected (switching from no-file): {file_path}")
                self._handle_calibration_change(file_path)
                self._started_with_file = True
            else:
                logger.info(
                    f"Calibration file change detected, delegating to "
                    f"{self._calibrator.__class__.__name__}"
                )
                self._calibrator.reload_data(file_path)

    # Methods that forward to the current calibration implementation
    
    def transform(self, msg: Message) -> Message:
        """Forward to current calibration implementation."""
        return self._calibrator.transform(msg)
    
    def transform_frame(self, frame: nvSchema.Frame) -> nvSchema.Frame:
        """Forward to current calibration implementation."""
        return self._calibrator.transform_frame(frame)
    
    def filter_frames_by_sensor_id(self, frames: list[nvSchema.Frame]) -> list[nvSchema.Frame]:
        """Forward to current calibration implementation."""
        return self._calibrator.filter_frames_by_sensor_id(frames)
    
    def filter_messages_by_roi(self, messages: list[Message]) -> list[Message]:
        """Forward to current calibration implementation."""
        return self._calibrator.filter_messages_by_roi(messages)
    
    def get_cam_params(self, sensor_id: str) -> dict[str, list[Any]]:
        """Forward to current calibration implementation."""
        return self._calibrator.get_cam_params(sensor_id)
    
    # Methods used by SpaceAnalyzer for 3D operations
    def transform_bbox3d_to_global_rois(self, bbox3d: Bbox3d) -> dict[str, list[Coordinate]]:
        """Forward to current calibration implementation if method exists."""
        if hasattr(self._calibrator, 'transform_bbox3d_to_global_rois'):
            return self._calibrator.transform_bbox3d_to_global_rois(bbox3d)
        return {}
    
    def perspective_transform(self, px: float, py: float, hmatrix: list[list[float]] | None) -> tuple[float, float] | None:
        """Forward to current calibration implementation if method exists."""
        if hasattr(self._calibrator, 'perspective_transform'):
            return self._calibrator.perspective_transform(px, py, hmatrix)
        return (px, py)
    
    def box3d_to_corners3d(self, bbox3d: Bbox3d) -> np.ndarray:
        """Forward to current calibration implementation if method exists."""
        if hasattr(self._calibrator, 'box3d_to_corners3d'):
            return self._calibrator.box3d_to_corners3d(bbox3d)
        return []
    
    # Properties used by external code
    @property
    def sensors(self) -> list[SensorInfo]:
        """Forward to current calibration implementation."""
        return self._calibrator.sensors if hasattr(self._calibrator, 'sensors') else []
    
    @property
    def sensor_map(self) -> dict[str, SensorInfo]:
        """Forward to current calibration implementation."""
        return self._calibrator.sensor_map if hasattr(self._calibrator, 'sensor_map') else {}
    
    @property
    def homography_map_global_roi(self) -> dict[str, tuple[tuple[float, float], tuple[float, float], list[list[float]], list[list[float]]]]:
        """Forward to current calibration implementation."""
        return self._calibrator.homography_map_global_roi if hasattr(self._calibrator, 'homography_map_global_roi') else {}
    
    @property
    def global_buffer_zones(self) -> list[ROI]:
        """Forward to current calibration implementation."""
        return self._calibrator.global_buffer_zones if hasattr(self._calibrator, 'global_buffer_zones') else []

    @property
    def calibration_type(self) -> CalibrationType:
        """Forward to current calibration implementation."""
        return self._calibrator.calibration_type
