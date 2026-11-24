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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import List, Optional, Any, Dict, Literal
import re
from urllib.parse import urlparse


class ProjectInfoBase(BaseModel):
    project_id: str
    project_name: str
    project_dir: str
    project_state: str

class ProjectInfo(ProjectInfoBase):
    project_id: str
    project_name: str
    project_dir: str
    project_state: str
    amc_state: str
    vggt_state: str
    postprocess_state: str = "INIT"
    rectification_state: str = "NOT_STARTED"
    rectification_path: Optional[Literal["auto", "manual", "linear"]] = None
    verified: bool = False
    video_directory: Optional[str]
    # Current source, not RTSP session history; clears when all source videos are removed.
    input_source_mode: Optional[Literal["manual", "rtsp"]] = None
    video_files_count: int
    video_files: List[str]
    groundtruth_file: bool
    alignment_file: bool
    layout_file: bool
    layout_filename: Optional[str] = None
    focal_length: List[float]
    amc_results_available: bool
    # World-scaled / post-processed artifacts for Results export & camera params.
    # Independent of amc_results_available (raw AMC dirs) and of shared
    # postprocess_state (a later VGGT run resets that while AMC files remain).
    amc_export_ready: bool = False
    vggt_export_ready: bool = False
    camera_name_map: Dict[str, str]

class ProjectInfoDetailed(ProjectInfo):
    calibration_pid: Optional[int]
    calibration_log: Optional[Any]
    vggt_calibration_pid: Optional[int]
    vggt_calibration_log: Optional[Any]
    postprocess_pid: Optional[int] = None
    postprocess_log: Optional[Any] = None
    rectification_pid: Optional[int] = None
    rectification_log: Optional[Any] = None
    
class StandardResponse(BaseModel):
    code: int
    message: str

class ProjectCreationResponse(ProjectInfoBase):
    code: int
    message: str

class ProjectListResponse(StandardResponse):
    projects: List[ProjectInfo]

class ProjectInfoResponse(StandardResponse):
    project_info: ProjectInfo

class DeleteAllProjectsResponse(StandardResponse):
    projects: List[ProjectInfo]

class DeleteProjectsRequest(BaseModel):
    project_ids: List[str]

class DeleteProjectsResponse(StandardResponse):
    deleted_project_ids: List[str]
    skipped_projects: Dict[str, str]
    projects: List[ProjectInfo]

class AlignmentFileResponse(StandardResponse):
    alignment_file: bool

class LayoutFileResponse(StandardResponse):
    layout_file: bool

class FocalLengthResponse(StandardResponse):
    focal_length: List[float]

class VerifyProjectResponse(StandardResponse):
    project_state: str

class AnnotationsResponse(StandardResponse):
    annotations: Dict

class CalibrateRequest(BaseModel):
    detector_type: Literal["resnet", "transformer"] = "resnet"

class CalibrationResponse(StandardResponse):
    project_state: str
    calibration_pid: Optional[int]

class RectificationResponse(StandardResponse):
    rectification_state: str
    rectification_pid: Optional[int]


class DistortionParams(BaseModel):
    model: str
    k1: float
    k2: float = 0.0


class RectificationCameraState(BaseModel):
    camera_id: str
    video_name: str
    width: int
    height: int
    cx: float
    cy: float
    k1_min: float
    k1_max: float
    k2_min: float
    k2_max: float
    committed: Optional[DistortionParams] = None
    auto_estimate: Optional[DistortionParams] = None
    grid_lines: List[List[List[float]]]
    has_rectified_preview: bool = False


class RectificationCamerasResponse(StandardResponse):
    cameras: List[RectificationCameraState]


class ManualRectificationPreviewRequest(BaseModel):
    model: str
    k1: float
    k2: float = 0.0


class ManualRectificationCommitRequest(BaseModel):
    cameras: Dict[str, DistortionParams]

class EvaluationStatisticsResponse(StandardResponse):
    statistics: Dict
    raw_text: str

class ExportCalibrationResponse(StandardResponse):
    export_file: Optional[str]

class ExportExistsResponse(ExportCalibrationResponse):
    exists: bool


class SystemCapabilitiesResponse(StandardResponse):
    vggt_available: bool
    rtsp_supported: bool
    running_calibration: bool = False

class LicenseInfo(BaseModel):
    name: str

class MetadataResponse(StandardResponse):
    version: str
    name: str
    description: str
    licenseInfo: LicenseInfo

class ConfigParams(BaseModel):
    """Calibration config; rectification fields may be omitted or supplied together with non-null values."""
    model_config = ConfigDict(extra="forbid")

    iterations: int = Field(strict=True, ge=1, le=100_000)  # sv_amc_config.yaml / camera_matrix_config.yaml
    layout_px_per_m: float                          # eval_config.yaml
    virtual_gt_person_height: float                 # eval_config.yaml (virtual-GT overlay pole height, metres)
    virtual_gt_grid_spacing: float                  # eval_config.yaml (virtual-GT floor grid spacing, metres)
    min_length: float                               # mv_amc_config.yaml
    min_moving_dist: float                          # mv_amc_config.yaml
    min_mean_moving_dist: float                     # mv_amc_config.yaml
    min_matching_points: int                        # mv_amc_config.yaml
    min_dtw_score_threshold: float                  # mv_amc_config.yaml
    min_distance_score_threshold: float             # mv_amc_config.yaml
    max_distance_score_hungarian_threshold: float   # mv_amc_config.yaml
    min_matched_tracklets: int                      # mv_amc_config.yaml
    max_matched_tracklets_dist: float               # mv_amc_config.yaml
    min_tracklet_dist: float                        # mv_amc_config.yaml
    min_matched_points: int                         # mv_amc_config.yaml
    inlier_threshold: float                         # mv_amc_config.yaml
    min_inliers: float                              # mv_amc_config.yaml
    reproj_err_threshold: float                     # mv_amc_config.yaml
    skip_frame: int                                 # mv_amc_config.yaml
    timestamp_sec: int                              # mv_amc_config.yaml vggt.timestamp_sec
    vggt_auto_select: bool                            # mv_amc_config.yaml vggt.auto_select
    vggt_max_num_frames: int                          # mv_amc_config.yaml vggt.max_num_frames
    vggt_confidence_score_threshold: float            # mv_amc_config.yaml vggt.confidence_score_threshold

    # Optional as a group: ordinary parameter uploads leave rectification unchanged.
    model: Optional[Literal["simple_radial", "radial", "simple_divisional"]] = None
    k1_min: Optional[float] = Field(default=None, allow_inf_nan=False)
    k1_max: Optional[float] = Field(default=None, allow_inf_nan=False)
    k1_step: Optional[int] = Field(default=None, strict=True, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_rectification_params(self):
        fields = {"model", "k1_min", "k1_max", "k1_step"}
        supplied = fields & self.model_fields_set
        if supplied:
            if supplied != fields or any(getattr(self, key) is None for key in fields):
                raise ValueError("Supply model, k1_min, k1_max, and k1_step together with non-null values")
            if self.k1_min > self.k1_max:
                raise ValueError("k1_min must not exceed k1_max")
        return self


# ---------------------------------------------------------------------------
# RTSP Stream Capture via VIOS
# ---------------------------------------------------------------------------

class RtspStreamConfig(BaseModel):
    """Configuration for a single RTSP camera stream to capture."""
    rtsp_url: str
    camera_name: Optional[str] = None  # friendly name used as video label in calibration
    sensor_id: Optional[str] = None    # Preferred VIOS sensor id; validated via
                                       # GET /sensor/{id}/info. If missing (404),
                                       # this stream is registered using rtsp_url.

    @field_validator("rtsp_url")
    @classmethod
    def rtsp_url_must_be_valid(cls, v: str) -> str:
        v = v.strip()
        if not (v.lower().startswith("rtsp://") or v.lower().startswith("rtsps://")):
            raise ValueError("rtsp_url must start with 'rtsp://' or 'rtsps://'")
        parsed = urlparse(v)
        if not parsed.hostname:
            raise ValueError("rtsp_url must contain a non-empty hostname after the scheme")
        if " " in (parsed.netloc or ""):
            raise ValueError("rtsp_url hostname must not contain spaces")
        try:
            _ = parsed.port  # raises ValueError when port is not a valid integer
        except ValueError:
            raise ValueError("rtsp_url contains an invalid port number")
        return v

    @field_validator("camera_name")
    @classmethod
    def camera_name_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v:  # skip None and empty string (empty string is treated as "not provided")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", v):
                raise ValueError(
                    "camera_name must contain only alphanumeric characters, hyphens, or underscores"
                )
        return v


class RtspCaptureRequest(BaseModel):
    """
    Request body for POST /rtsp/capture/{project_id}.

    Per stream: optional ``sensor_id`` is checked against VIOS; if absent (404)
    or unset, the stream is registered from ``rtsp_url``. After
    ``duration_seconds``, clips are automatically ingested into the project.

    The VIOS server URL is supplied by the ``VIOS_BASE_URL`` environment
    variable at service startup; callers do not (and cannot) set it per-request.
    """
    model_config = ConfigDict(extra="forbid")

    streams: List[RtspStreamConfig]
    duration_seconds: int = Field(ge=60, le=1200)  # 1 to 20 minutes
    vios_token: Optional[str] = None    # Bearer token (if VIOS auth is enabled)
    ssl_verify: bool = False            # Set True only when VIOS uses a trusted cert


class RtspCaptureSession(BaseModel):
    """
    State of a single RTSP capture session.

    Status lifecycle:
        STARTING → RECORDING → STOPPING → INGESTING → INGESTED
        Any stage may transition to ERROR on failure.
        A stop during STARTING transitions to CANCELLED. A stop after the
        minimum recording duration transitions to INGESTING.
    """
    session_id: str
    project_id: str
    status: str                         # STARTING | RECORDING | STOPPING | INGESTING
                                        # INGESTED | CANCELLED | ERROR
    streams: List[RtspStreamConfig]
    duration_seconds: int
    # vios_base_url is deployment-internal and not exposed to clients — it is
    # stored in the private session dict (sess["vios_base_url"]) instead.
    sensor_ids: List[str]               # VIOS sensor UUIDs registered for this session
    recording_start_time: Optional[str] = None   # ISO 8601 UTC
    recording_end_time: Optional[str] = None     # ISO 8601 UTC
    ingested_files: List[str] = []      # project-relative video filenames after ingest
    sensors_started_by_us: List[str] = []    # sensor IDs whose recording AMC MS started
    sensors_registered_by_us: List[str] = [] # sensor IDs AMC MS created in VIOS (to be deregistered on session delete)
    stop_failures: List[str] = []            # sensor IDs that failed to stop recording
    error_message: Optional[str] = None
    created_at: str                     # ISO 8601 UTC


class RtspCaptureResponse(StandardResponse):
    session: RtspCaptureSession


class RtspSessionListResponse(StandardResponse):
    sessions: List[RtspCaptureSession]
