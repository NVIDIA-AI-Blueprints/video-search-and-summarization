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
 * Constants and configuration for Auto Magic Calibration UI
 */

import { API_BASE_URL } from '../config';

// ============================================================================
// API Configuration
// ============================================================================

/**
 * Base API URL - imported from config.tsx
 * To change the API URL, update src/config.tsx
 */
export { API_BASE_URL };

/**
 * API endpoints
 */
export const API_ENDPOINTS = {
  // System
  SYSTEM_CAPABILITIES: '/system/capabilities',
  
  // Project Management
  CREATE_PROJECT: '/create_project',
  LIST_PROJECTS: '/list_projects',
  GET_PROJECT_INFO: (projectId: string) => `/get_project_info/${projectId}`,
  UPDATE_PROJECT_INFO: (projectId: string) => `/update_project_info/${projectId}`,
  DELETE_PROJECT: (projectId: string) => `/delete_project/${projectId}`,
  DELETE_PROJECTS: '/delete_projects',
  DELETE_ALL_PROJECTS: '/delete_all_projects',
  RESET_PROJECT: (projectId: string) => `/reset_project/${projectId}`,

  // Data Management
  UPLOAD_VIDEO_FILES: (projectId: string) => `/upload_video_files/${projectId}`,
  LIST_VIDEO_FILES: (projectId: string) => `/list_video_files/${projectId}`,
  DELETE_VIDEO_FILE: (projectId: string, videoName: string) => `/delete_video_file/${projectId}/${videoName}`,
  UPLOAD_GT_FILE: (projectId: string) => `/upload_gt_file/${projectId}`,
  DELETE_GT_FILE: (projectId: string) => `/delete_gt_file/${projectId}`,
  UPLOAD_ALIGNMENT: (projectId: string) => `/upload_alignment/${projectId}`,
  GET_ALIGNMENT: (projectId: string) => `/get_alignment/${projectId}`,
  DELETE_ALIGNMENT: (projectId: string) => `/delete_alignment/${projectId}`,
  UPLOAD_LAYOUT: (projectId: string) => `/upload_layout/${projectId}`,
  GET_LAYOUT: (projectId: string) => `/get_layout/${projectId}`,
  DELETE_LAYOUT: (projectId: string) => `/delete_layout/${projectId}`,
  UPLOAD_FOCAL_LENGTH: (projectId: string) => `/upload_focal_length/${projectId}`,
  GET_FOCAL_LENGTH: (projectId: string) => `/get_focal_length/${projectId}`,
  DELETE_FOCAL_LENGTH: (projectId: string) => `/delete_focal_length/${projectId}`,
  VERIFY_PROJECT: (projectId: string) => `/verify_project/${projectId}`,
  RELAUNCH_CALIBRATION: (projectId: string) => `/relaunch_calibration/${projectId}`,
  START_RECTIFICATION: (projectId: string) => `/rectification/${projectId}`,
  RECTIFICATION_STATUS: (projectId: string) => `/rectification/${projectId}`,
  GET_RECTIFICATION_LOG: (projectId: string) => `/rectification/${projectId}/log`,
  STREAM_RECTIFICATION_LOG: (projectId: string) =>
    `/rectification/${projectId}/log/stream`,
  STAGE_LINEAR_MEDIA: (projectId: string) => `/linear_media/${projectId}`,
  /** Per-camera rectification metadata for manual tuning UI */
  RECTIFICATION_CAMERAS: (projectId: string) => `/rectification/${projectId}/cameras`,
  RECTIFICATION_PREVIEW: (projectId: string, cameraId: string) =>
    `/rectification/${projectId}/preview/${cameraId}`,
  RECTIFICATION_MANUAL_START: (projectId: string) =>
    `/rectification/${projectId}/manual/start`,
  RECTIFICATION_MANUAL_COMMIT: (projectId: string) => `/rectification/${projectId}/manual`,
  DOWNLOAD_SINGLE_VIEW_RESULTS: (projectId: string) =>
    `/rectification/${projectId}/single_view_results`,
  GET_VIDEO_FRAME: (projectId: string, videoName: string, frameIndex: number, useRectified: boolean = false) => 
    `/video_frame/${projectId}/${videoName}/${frameIndex}${useRectified ? '?use_rectified=true' : ''}`,

  // Annotations (ROI/Tripwire)
  SAVE_ANNOTATIONS: (projectId: string, videoName: string) => `/annotations/${projectId}/${videoName}`,
  GET_ANNOTATIONS: (
    projectId: string,
    videoName: string,
    useAlignmentData: boolean = false,
    includeGlobalProjection: boolean = true,
    resultType?: 'amc' | 'vggt',
  ) => {
    const params = new URLSearchParams();
    if (useAlignmentData) {
      params.set('use_alignment_data', 'true');
    }
    if (!includeGlobalProjection) {
      params.set('include_global_projection', 'false');
    }
    if (resultType) {
      params.set('result_type', resultType);
    }
    const query = params.toString();
    return `/annotations/${projectId}/${videoName}${query ? `?${query}` : ''}`;
  },
  GET_ALL_ANNOTATIONS: (projectId: string) => `/annotations/${projectId}`,
  DELETE_ANNOTATIONS: (projectId: string, videoName: string) => `/annotations/${projectId}/${videoName}`,

  // Configuration
  GET_CONFIG: '/config',
  GET_CONFIG_DEFAULTS: '/config/defaults',
  UPDATE_CONFIG: (projectId: string) => `/config/${projectId}`,

  // Calibration
  CALIBRATE: (projectId: string) => `/calibrate/${projectId}`,
  STOP_CALIBRATION: (projectId: string) => `/stop_calibration/${projectId}`,
  VGGT_CALIBRATE: (projectId: string) => `/vggt/calibrate/${projectId}`,
  POSTPROCESS: (projectId: string) => `/postprocess/${projectId}`,
  GET_OVERLAY_IMAGE: (projectId: string) => `/result/${projectId}/overlay_image`,
  GET_VIRTUAL_GT_OVERLAY: (
    projectId: string,
    cameraId: number,
    resultType: 'amc' | 'vggt',
  ) =>
    `/result/${projectId}/virtual_gt_overlay/${cameraId}?result_type=${resultType}`,
  GET_CAMERA_PARAMETERS: (projectId: string, cameraId: number) => 
    `/result/${projectId}/camera_parameters/${cameraId}`,
  GET_EVALUATION_LAYOUT: (projectId: string) => `/result/${projectId}/evaluation_layout`,
  GET_EVALUATION_STATISTICS: (projectId: string) => `/result/${projectId}/evaluation_statistics`,
  GET_VGGT_EVALUATION_METRICS: (projectId: string) => `/vggt_results/${projectId}/evaluation_metrics`,
  GET_VGGT_EVALUATION_LAYOUT: (projectId: string) => `/vggt_results/${projectId}/evaluation_layout`,
  GET_VGGT_EVALUATION_STATISTICS: (projectId: string) => `/vggt_results/${projectId}/evaluation_statistics`,
  GET_MV3DT_RESULT: (projectId: string, resultType: string = 'amc') => 
    `/result/${projectId}/mv3dt_result?result_type=${resultType}`,
  DELETE_RESULT: (projectId: string) => `/result/${projectId}`,
  EXPORT_CALIBRATION: (
    projectId: string,
    resultType: string = 'amc',
    calibrationType: string = 'cartesian',
  ) =>
    `/result/${projectId}/export_calibration?result_type=${resultType}&calibration_type=${calibrationType}`,
  DOWNLOAD_EXPORT_CALIBRATION: (
    projectId: string,
    resultType: string = 'amc',
    calibrationType: string = 'cartesian',
    snapshot: 'exported' | 'projected' = 'exported',
  ) =>
    `/result/${projectId}/export_calibration?result_type=${resultType}&calibration_type=${calibrationType}&snapshot=${snapshot}`,
  SAVE_EXPORT_CALIBRATION: (
    projectId: string,
    resultType: string = 'amc',
    calibrationType: string = 'cartesian',
  ) =>
    `/result/${projectId}/export_calibration?result_type=${resultType}&calibration_type=${calibrationType}`,
  CHECK_EXPORT_EXISTS: (projectId: string, calibrationType: string = 'cartesian') =>
    `/result/${projectId}/export_exists?calibration_type=${calibrationType}`,
  GENERATE_ROI_BEV: (projectId: string, resultType: string = 'amc') => 
    `/result/${projectId}/generate_roi_bev?result_type=${resultType}`,
  GET_VGGT_OVERLAY_IMAGE: (projectId: string) => `/vggt_results/${projectId}/overlay_image`,
  GET_VGGT_CAMERA_PARAMETERS: (projectId: string, cameraId: number) => 
    `/vggt_results/${projectId}/camera_parameters/${cameraId}`,
  
  // Log Streaming (SSE)
  STREAM_CALIBRATION_LOG: (projectId: string, type: 'amc' | 'vggt') => 
    `/calibrate/${projectId}/log/${type}/stream`,
  
  // Get calibration log file
  GET_CALIBRATION_LOG: (projectId: string, type: 'amc' | 'vggt') =>
    `/${type}/calibrate/${projectId}/log`,

  // RTSP capture via VIOS (optional backend)
  RTSP_CAPTURE_START: (projectId: string) => `/rtsp/capture/${projectId}`,
  RTSP_CAPTURE_STATUS: (projectId: string, sessionId: string) =>
    `/rtsp/capture/${projectId}/${encodeURIComponent(sessionId)}`,
  RTSP_CAPTURE_STOP: (projectId: string, sessionId: string) =>
    `/rtsp/capture/${projectId}/${encodeURIComponent(sessionId)}/stop`,
  RTSP_SESSIONS_LIST: (projectId: string) => `/rtsp/sessions/${projectId}`,
  RTSP_SESSION_DELETE: (projectId: string, sessionId: string) =>
    `/rtsp/session/${projectId}/${encodeURIComponent(sessionId)}`,
} as const;

/** Must match server MIN_RTSP_DURATION_SECONDS (rtsp_support main.py). */
export const RTSP_MIN_DURATION_SECONDS = 60;
/** Must match server MAX_RTSP_DURATION_SECONDS (main.py). */
export const RTSP_MAX_DURATION_SECONDS = 20 * 60;

// ============================================================================
// Workflow Configuration
// ============================================================================

/**
 * Workflow step IDs
 */
export enum WorkflowStepId {
  PROJECT_SETUP = 'project-setup',
  VIDEO_CONFIGURATION = 'video-configuration',
  PARAMETERS = 'parameters',
  RECTIFICATION = 'rectification',
  MANUAL_ALIGNMENT = 'manual-alignment',
  EXECUTE = 'execute',
  RESULTS = 'results',
}

/**
 * Workflow steps configuration
 */
export const WORKFLOW_STEPS = [
  {
    id: WorkflowStepId.PROJECT_SETUP,
    label: 'Step 1: Project Setup',
    shortLabel: 'Project Setup',
    description: 'Create and manage calibration projects',
  },
  {
    id: WorkflowStepId.VIDEO_CONFIGURATION,
    label: 'Step 2: Video Configuration',
    shortLabel: 'Video Configuration',
    description: 'Upload videos, ground truth, and layout data',
  },
  {
    id: WorkflowStepId.PARAMETERS,
    label: 'Step 3: Parameters',
    shortLabel: 'Parameters',
    description: 'Set floor-plan scale and optional focal lengths',
  },
  {
    id: WorkflowStepId.RECTIFICATION,
    label: 'Step 4: Rectification',
    shortLabel: 'Rectification',
    description: 'Configure distortion settings and generate linear camera media',
  },
  {
    id: WorkflowStepId.MANUAL_ALIGNMENT,
    label: 'Step 5: Manual Alignment',
    shortLabel: 'Manual Alignment',
    description: 'Create alignment data by selecting corresponding points',
  },
  {
    id: WorkflowStepId.EXECUTE,
    label: 'Step 6: Execute',
    shortLabel: 'Execute',
    description: 'Run the calibration pipeline',
  },
  {
    id: WorkflowStepId.RESULTS,
    label: 'Step 7: Results',
    shortLabel: 'Results',
    description: 'Draw ROIs/tripwires on rectified frames, verify, and export',
  },
] as const;

// ============================================================================
// File Upload Configuration
// ============================================================================

/**
 * Accepted video file types
 */
export const ACCEPTED_VIDEO_TYPES = [
  'video/mp4',
  'video/avi',
  'video/quicktime',
  'video/x-matroska',
] as const;

/**
 * Accepted video file extensions
 */
export const ACCEPTED_VIDEO_EXTENSIONS = ['.mp4'] as const;

/**
 * Accepted image file types for layout
 */
export const ACCEPTED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/jpg'] as const;

/**
 * Accepted image file extensions
 */
export const ACCEPTED_IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg'] as const;

/**
 * Accepted JSON file types
 */
export const ACCEPTED_JSON_TYPES = ['application/json'] as const;

/**
 * Accepted JSON file extensions
 */
export const ACCEPTED_JSON_EXTENSIONS = ['.json'] as const;

/**
 * Accepted ZIP file types for ground truth
 */
export const ACCEPTED_ZIP_TYPES = ['application/zip', 'application/x-zip-compressed'] as const;

/**
 * Accepted ZIP file extensions
 */
export const ACCEPTED_ZIP_EXTENSIONS = ['.zip'] as const;

/**
 * Maximum file sizes (in bytes)
 */
export const MAX_FILE_SIZES = {
  VIDEO: 500 * 1024 * 1024, // 500 MB
  IMAGE: 10 * 1024 * 1024, // 10 MB
  JSON: 1 * 1024 * 1024, // 1 MB
  ZIP: 1024 * 1024 * 1024, // 1 GB
} as const;

/**
 * Required input video resolution (must match server/pipeline)
 */
export const REQUIRED_VIDEO_WIDTH = 1920;
export const REQUIRED_VIDEO_HEIGHT = 1080;

// ============================================================================
// Annotation Configuration
// ============================================================================

/**
 * Default colors for annotations
 */
export const ANNOTATION_COLORS = {
  ROI: '#00ff00', // Green
  TRIPWIRE: '#ff0000', // Red
  SELECTED: '#ffff00', // Yellow
  HOVER: '#00ffff', // Cyan
} as const;

/**
 * Point marker configuration
 */
export const POINT_MARKER = {
  RADIUS: 5,
  STROKE_WIDTH: 2,
  LABEL_OFFSET: 10,
} as const;

/**
 * Line configuration
 */
export const LINE_CONFIG = {
  STROKE_WIDTH: 2,
  DASH_ARRAY: [5, 5], // For dashed lines
} as const;

// ============================================================================
// Manual Alignment Configuration
// ============================================================================

/**
 * Number of point sets required for alignment
 */
export const REQUIRED_ALIGNMENT_POINTS = 4;

/**
 * Colors for alignment points
 */
export const ALIGNMENT_POINT_COLORS = [
  '#00ff00', // Green - Point 1
  '#0000ff', // Blue - Point 2
  '#ff0000', // Red - Point 3
  '#ffff00', // Yellow - Point 4
] as const;

/**
 * Alignment image labels
 */
export const ALIGNMENT_IMAGE_LABELS = {
  CAM0: 'Camera 0',
  CAM1: 'Camera 1',
  BEV: 'Layout Map (BEV)',
} as const;

// ============================================================================
// Calibration Configuration
// ============================================================================

/**
 * Calibration polling interval (in milliseconds)
 */
export const CALIBRATION_POLL_INTERVAL = 3000;

/**
 * Maximum calibration time (in milliseconds) - 30 minutes
 */
export const MAX_CALIBRATION_TIME = 30 * 60 * 1000;

// ============================================================================
// UI Configuration
// ============================================================================

/**
 * Snackbar duration (in milliseconds)
 */
export const SNACKBAR_DURATION = 6000;

/**
 * Debounce delay for form inputs (in milliseconds)
 */
export const INPUT_DEBOUNCE_DELAY = 300;

/**
 * Table pagination options
 */
export const TABLE_ROWS_PER_PAGE_OPTIONS = [5, 10, 25, 50] as const;

/**
 * Default table rows per page
 */
export const DEFAULT_TABLE_ROWS_PER_PAGE = 10;

// ============================================================================
// Validation Rules
// ============================================================================

/**
 * Project name validation
 */
export const PROJECT_NAME_VALIDATION = {
  MIN_LENGTH: 3,
  MAX_LENGTH: 50,
  PATTERN: /^[a-zA-Z0-9_-]+$/,
  ERROR_MESSAGE: 'Project name must be 3-50 characters and contain only letters, numbers, hyphens, and underscores',
} as const;

/**
 * Video naming convention
 */
export const VIDEO_NAMING_PATTERN = /^cam_\d{2}\.mp4$/;

/**
 * Minimum number of cameras required (1 for single-camera, 2+ for multi-camera calibration)
 */
export const MIN_CAMERAS_REQUIRED = 1;

// ============================================================================
// Error Messages
// ============================================================================

/**
 * Common error messages
 */
export const ERROR_MESSAGES = {
  NETWORK_ERROR: 'Network error. Please check your connection and try again.',
  SERVER_ERROR: 'Server error. Please try again later.',
  UNAUTHORIZED: 'Unauthorized. Please log in again.',
  NOT_FOUND: 'Resource not found.',
  VALIDATION_ERROR: 'Validation error. Please check your input.',
  FILE_TOO_LARGE: 'File is too large. Please choose a smaller file.',
  INVALID_FILE_TYPE: 'Invalid file type. Please choose a valid file.',
  PROJECT_NOT_SELECTED: 'Please select a project first.',
  INSUFFICIENT_VIDEOS: 'At least 2 videos are required for calibration.',
  MISSING_REQUIRED_FILES: 'Required files are missing. Please upload all required files.',
  ALIGNMENT_INCOMPLETE: 'Alignment data is incomplete. Please complete all 4 point sets.',
  CALIBRATION_RUNNING: 'Calibration is already running.',
  CALIBRATION_FAILED: 'Calibration failed. Please check the logs and try again.',
} as const;

/**
 * Success messages
 */
export const SUCCESS_MESSAGES = {
  PROJECT_CREATED: 'Project created successfully',
  PROJECT_UPDATED: 'Project updated successfully',
  PROJECT_DELETED: 'Project deleted successfully',
  PROJECT_RESET: 'Project reset successfully',
  VIDEO_UPLOADED: 'Video uploaded successfully',
  VIDEO_DELETED: 'Video deleted successfully',
  GT_UPLOADED: 'Ground truth uploaded successfully',
  GT_DELETED: 'Ground truth deleted successfully',
  ALIGNMENT_UPLOADED: 'Alignment file uploaded successfully',
  ALIGNMENT_DELETED: 'Alignment file deleted successfully',
  ALIGNMENT_SAVED: 'Alignment data saved successfully',
  LAYOUT_UPLOADED: 'Layout file uploaded successfully',
  LAYOUT_DELETED: 'Layout file deleted successfully',
  FOCAL_LENGTH_SAVED: 'Focal length saved successfully',
  FOCAL_LENGTH_DELETED: 'Focal length deleted successfully',
  ANNOTATIONS_SAVED: 'Annotations saved successfully',
  PROJECT_VERIFIED: 'Project verified successfully',
  CALIBRATION_STARTED: 'Calibration started successfully',
  CALIBRATION_COMPLETED: 'Calibration completed successfully',
  DATA_EXPORTED: 'Data exported successfully',
} as const;
