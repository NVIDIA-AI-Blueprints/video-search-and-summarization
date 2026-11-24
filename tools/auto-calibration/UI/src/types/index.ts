/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

/**
 * Type definitions for Auto Magic Calibration UI
 * No 'any' types allowed - all types are strictly defined based on API schema
 */

// ============================================================================
// Project Management Types
// ============================================================================

/**
 * Project state enum matching backend ProjectState
 */
export enum ProjectState {
  INIT = 'INIT',
  READY = 'READY',
  RUNNING = 'RUNNING',
  COMPLETED = 'COMPLETED',
  ERROR = 'ERROR',
}

/**
 * Complete project information from backend
 */
export interface Project {
  project_id: string;
  project_name: string;
  project_dir: string;
  project_state: ProjectState;
  project_state_enum: ProjectState;
  amc_state: string;
  postprocess_state: string;
  rectification_state: 'NOT_STARTED' | 'RUNNING' | 'READY_FOR_REVIEW' | 'COMPLETED' | 'ERROR';
  rectification_path?: 'auto' | 'manual' | 'linear' | null;
  verified: boolean;
  /** Current video source; does not reflect historical RTSP sessions. */
  input_source_mode?: 'manual' | 'rtsp' | null;
  video_files_count: number;
  video_files: string[];
  groundtruth_file: boolean;
  alignment_file: boolean;
  layout_file: boolean;
  layout_filename: string | null;
  focal_length: number[];
  amc_results_available: boolean;
  /** AMC COMPLETED + world-scaled camera params exist for every camera. */
  amc_export_ready?: boolean;
  /** VGGT COMPLETED + world-scaled camera params exist for every camera. */
  vggt_export_ready?: boolean;
  vggt_state: string;
  camera_name_map: Record<string, string>;
}

/**
 * Project creation request
 */
export interface CreateProjectRequest {
  project_name: string;
}

/**
 * System capabilities response
 */
export interface SystemCapabilitiesResponse {
  code: number;
  vggt_available: boolean;
  rtsp_supported?: boolean;
  running_calibration?: boolean;
  message: string;
}

/**
 * Project creation response
 */
export interface CreateProjectResponse {
  code: number;
  project_id: string;
  project_name: string;
  project_dir: string;
  project_state: string;
  message: string;
}

/**
 * List projects response
 */
export interface ListProjectsResponse {
  code: number;
  projects: Project[];
  message: string;
}

/** DELETE /delete_all_projects — `projects` is empty when all removed; otherwise remaining projects (code 1). */
export interface DeleteAllProjectsResponse {
  code: number;
  projects: Project[];
  message: string;
}

export interface DeleteProjectsResponse {
  code: number;
  message: string;
  deleted_project_ids: string[];
  skipped_projects: Record<string, string>;
  projects: Project[];
}

/**
 * Get project info response
 */
export interface GetProjectInfoResponse {
  code: number;
  project_info: Project;
  message: string;
}

/**
 * Update project info request
 */
export interface UpdateProjectInfoRequest {
  project_name: string;
}

/**
 * Generic API response
 */
export interface ApiResponse {
  code: number;
  message: string;
}

export interface RectificationResponse extends ApiResponse {
  rectification_state: 'NOT_STARTED' | 'RUNNING' | 'READY_FOR_REVIEW' | 'COMPLETED' | 'ERROR';
  rectification_pid: number | null;
}

export type DistortionModel = 'simple_radial' | 'radial' | 'simple_divisional';

export interface DistortionParams {
  model: DistortionModel;
  k1: number;
  k2: number;
}

export interface RectificationCameraState {
  camera_id: string;
  video_name: string;
  width: number;
  height: number;
  cx: number;
  cy: number;
  k1_min: number;
  k1_max: number;
  k2_min: number;
  k2_max: number;
  committed: DistortionParams | null;
  auto_estimate: DistortionParams | null;
  grid_lines: number[][][];
  has_rectified_preview: boolean;
}

export interface RectificationCamerasResponse extends ApiResponse {
  cameras: RectificationCameraState[];
}

// ============================================================================
// RTSP capture (VIOS / VST)
// ============================================================================

export interface RtspStreamConfigInput {
  rtsp_url: string;
  camera_name?: string | null;
  sensor_id?: string | null;
}

/** POST /v1/rtsp/capture/{project_id} — VIOS base URL is server env only, not sent here. */
export interface RtspCaptureRequestBody {
  streams: RtspStreamConfigInput[];
  duration_seconds: number;
}

export interface RtspCaptureSession {
  session_id: string;
  project_id: string;
  status: string;
  streams: RtspStreamConfigInput[];
  duration_seconds: number;
  sensor_ids: string[];
  recording_start_time?: string | null;
  recording_end_time?: string | null;
  ingested_files: string[];
  sensors_started_by_us?: string[];
  stop_failures?: string[];
  error_message?: string | null;
  created_at: string;
}

export interface RtspCaptureResponse {
  code: number;
  message: string;
  session: RtspCaptureSession;
}

export interface RtspSessionListResponse {
  code: number;
  message: string;
  sessions: RtspCaptureSession[];
}

// ============================================================================
// Data Management Types
// ============================================================================

/**
 * Upload video files response
 */
export interface UploadVideoFilesResponse {
  code: number;
  video_files: string[];
  video_files_count: number;
  message: string;
}

/**
 * List video files response
 */
export interface ListVideoFilesResponse {
  code: number;
  video_files: string[];
  video_files_count: number;
  message: string;
}

/**
 * Delete video file response
 */
export interface DeleteVideoFileResponse {
  code: number;
  video_files: string[];
  video_files_count: number;
  project_state: string;
  message: string;
}

/**
 * Upload ground truth file response
 */
export interface UploadGtFileResponse {
  code: number;
  groundtruth_file: boolean;
  message: string;
}

/**
 * Upload alignment file response
 */
export interface UploadAlignmentResponse {
  code: number;
  alignment_file: boolean;
  message: string;
}

/**
 * Upload layout file response
 */
export interface UploadLayoutResponse {
  code: number;
  layout_file: boolean;
  message: string;
}

/**
 * Upload focal length response
 */
export interface UploadFocalLengthResponse {
  code: number;
  focal_length: number[];
  message: string;
}

/**
 * Get focal length response
 */
export interface GetFocalLengthResponse {
  code: number;
  focal_length: number[];
  message: string;
}

/**
 * Verify project response
 */
export interface VerifyProjectResponse {
  code: number;
  project_state: string;
  message: string;
}

// ============================================================================
// Calibration Types
// ============================================================================

/**
 * Calibration execution response
 */
export interface CalibrateResponse {
  code: number;
  message: string;
}

/**
 * Camera parameter data structure from YAML
 */
export interface CameraParameters {
  camera_matrix: number[][];
  dist_coeffs: number[];
  rotation_vector: number[];
  translation_vector: number[];
  projection_matrix: number[][];
}

// ============================================================================
// Annotation Types (ROI and Tripwire)
// ============================================================================

/**
 * 2D Point in image coordinates
 */
export interface Point2D {
  x: number;
  y: number;
}

/**
 * 3D Point in world coordinates
 */
export interface Point3D {
  x: number;
  y: number;
  z: number;
}

/**
 * Region of Interest polygon
 */
export interface ROI {
  id: string;
  camera_id: string;
  points: Point2D[];
  label?: string;
  color?: string;
}

/**
 * Tripwire line definition
 */
export interface Tripwire {
  id: string;
  camera_id: string;
  start: Point2D;
  end: Point2D;
  direction?: 'forward' | 'backward' | 'bidirectional';
  label?: string;
  color?: string;
}

/**
 * Per-camera annotation data
 */
export interface CameraAnnotations {
  camera_id: string;
  rois: ROI[];
  tripwires: Tripwire[];
}

/**
 * Complete annotation data structure
 */
export interface AnnotationData {
  project_id: string;
  cameras: CameraAnnotations[];
}

/**
 * Annotation response for a single video
 */
export interface GetAnnotationsResponse {
  code: number;
  annotations: {
    rois: ROI[];
    tripwireLines: Tripwire[];
    tripwireDirections: Tripwire[];
  };
  message: string;
}

/**
 * Response for getting all annotations
 */
export interface GetAllAnnotationsResponse {
  code: number;
  annotations: Record<string, {
    rois: ROI[];
    tripwireLines: Tripwire[];
    tripwireDirections: Tripwire[];
  }>;
  message: string;
}

// ============================================================================
// Configuration Types
// ============================================================================

/**
 * Configuration parameters for calibration
 */
export interface ConfigParams {
  // Camera Matrix Config
  iterations: number;
  
  // Eval Config
  layout_px_per_m: number;
  /** Height (m) of red virtual-GT poles drawn in layout post-processing */
  virtual_gt_person_height: number;
  /** Floor-grid line spacing (m); cells are spacing×spacing */
  virtual_gt_grid_spacing: number;
  
  // MV AMC Config
  min_length: number;
  min_moving_dist: number;
  min_mean_moving_dist: number;
  min_matching_points: number;
  min_dtw_score_threshold: number;
  min_distance_score_threshold: number;
  max_distance_score_hungarian_threshold: number;
  min_matched_tracklets: number;
  max_matched_tracklets_dist: number;
  min_tracklet_dist: number;
  min_matched_points: number;
  inlier_threshold: number;
  min_inliers: number;
  reproj_err_threshold: number;
  skip_frame: number;
  timestamp_sec: number;
  vggt_auto_select: boolean;
  vggt_max_num_frames: number;
  vggt_confidence_score_threshold: number;

  // Rectification settings are optional as a complete group on config updates.
  model?: string;
  k1_min?: number;
  k1_max?: number;
  k1_step?: number;
}

// ============================================================================
// Manual Alignment Types
// ============================================================================

/**
 * Single alignment point set (3 corresponding points)
 */
export interface AlignmentPointSet {
  cam0: Point2D | null;
  cam1: Point2D | null;
  bev: Point2D | null;
}

/**
 * Complete alignment data (4 point sets)
 */
export interface AlignmentData {
  points: AlignmentPointSet[];
}

/**
 * Alignment data JSON format (as stored in backend) - multi-camera: [cam0, cam1, bev] per set
 */
export type AlignmentDataJSON = [
  [[number, number], [number, number], [number, number]],
  [[number, number], [number, number], [number, number]],
  [[number, number], [number, number], [number, number]],
  [[number, number], [number, number], [number, number]]
];

/**
 * Alignment data JSON format for single-camera: [cam, bev] per set (1 camera + layout only)
 */
export type AlignmentDataJSONSingleCam = [
  [[number, number], [number, number]],
  [[number, number], [number, number]],
  [[number, number], [number, number]],
  [[number, number], [number, number]]
];

// ============================================================================
// Export Settings Types
// ============================================================================

/**
 * Export settings for customizing calibration export attributes
 */
export interface ExportSettings {
  depth?: string;
  fieldOfView?: string;
  direction?: string;
  source?: string;
  building?: string;
  room?: string;
}

// ============================================================================
// Results and Export Types
// ============================================================================

/**
 * Intrinsic camera matrix (3x3)
 */
export interface IntrinsicMatrix {
  fx: number;
  fy: number;
  cx: number;
  cy: number;
  skew: number;
  matrix: number[][];
}

/**
 * Extrinsic camera matrix (rotation and translation)
 */
export interface ExtrinsicMatrix {
  rotation: number[][];
  translation: number[];
  matrix: number[][]; // 3x4 [R|t] matrix
}

/**
 * Camera calibration data
 */
export interface CameraCalibration {
  camera_id: number;
  projection_matrix: number[][]; // 3x4 matrix
  intrinsic: IntrinsicMatrix;
  extrinsic: ExtrinsicMatrix;
  distortion_coefficients: number[];
}

/**
 * World coordinate ROI
 */
export interface WorldROI {
  id: string;
  camera_id: string;
  points_image: Point2D[];
  points_world: Point3D[];
  label?: string;
}

/**
 * World coordinate Tripwire
 */
export interface WorldTripwire {
  id: string;
  camera_id: string;
  start_image: Point2D;
  end_image: Point2D;
  start_world: Point3D;
  end_world: Point3D;
  direction?: 'forward' | 'backward' | 'bidirectional';
  label?: string;
}

/**
 * World transformation parameters
 */
export interface WorldTransformation {
  scale: number;
  rotation: number[][];
  translation: number[];
}

/**
 * Video metadata
 */
export interface VideoMetadata {
  fps: number;
  width: number;
  height: number;
  codec: string;
}

/**
 * Complete exported calibration data
 */
export interface ExportedCalibrationData {
  project_name: string;
  project_id: string;
  timestamp: string;
  cameras: CameraCalibration[];
  rois: WorldROI[];
  tripwires: WorldTripwire[];
  world_transformation: WorldTransformation;
  video_metadata: Record<string, VideoMetadata>;
}

// ============================================================================
// UI State Types
// ============================================================================

/**
 * Drawing mode for canvas interactions
 */
export enum DrawingMode {
  NONE = 'NONE',
  ROI = 'ROI',
  TRIPWIRE_LINE = 'TRIPWIRE_LINE',
  TRIPWIRE_DIRECTION = 'TRIPWIRE_DIRECTION',
}

/**
 * Canvas state for annotation tool
 */
export interface CanvasState {
  mode: DrawingMode;
  currentROI: Point2D[];
  currentTripwire: {
    start: Point2D | null;
    end: Point2D | null;
  };
  isDrawing: boolean;
  showAnnotations: boolean;
}

/**
 * Workflow step configuration
 */
export interface WorkflowStep {
  id: string;
  label: string;
  description: string;
  isComplete: boolean;
  isAccessible: boolean;
}

/**
 * File upload state
 */
export interface FileUploadState {
  file: File | null;
  progress: number;
  status: 'idle' | 'uploading' | 'success' | 'error';
  error?: string;
}

/**
 * Multi-file upload state
 */
export interface MultiFileUploadState {
  files: File[];
  uploadProgress: Record<string, number>;
  uploadStatus: Record<string, 'idle' | 'uploading' | 'success' | 'error'>;
  errors: Record<string, string>;
}

// ============================================================================
// Form Types
// ============================================================================

/**
 * Project creation form data
 */
export interface ProjectFormData {
  project_name: string;
  description?: string;
}

/**
 * Focal length configuration form data
 */
export interface FocalLengthFormData {
  focal_lengths: number[];
}

/**
 * Manual alignment form data
 */
export interface ManualAlignmentFormData {
  currentPointSet: number; // 0-3
  selectedPoints: {
    cam0: Point2D | null;
    cam1: Point2D | null;
    bev: Point2D | null;
  };
}

// ============================================================================
// Error Types
// ============================================================================

/**
 * API error response
 */
export interface ApiError {
  status: number;
  statusText: string;
  message: string;
  detail?: string;
}

/**
 * Validation error
 */
export interface ValidationError {
  field: string;
  message: string;
}

// ============================================================================
// Utility Types
// ============================================================================

/**
 * Loading state wrapper
 */
export interface LoadingState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
}

/**
 * Pagination state
 */
export interface PaginationState {
  page: number;
  pageSize: number;
  total: number;
}

/**
 * Sort configuration
 */
export interface SortConfig<T> {
  field: keyof T;
  direction: 'asc' | 'desc';
}

/**
 * Filter configuration
 */
export interface FilterConfig {
  field: string;
  operator: 'equals' | 'contains' | 'startsWith' | 'endsWith' | 'greaterThan' | 'lessThan';
  value: string | number | boolean;
}
