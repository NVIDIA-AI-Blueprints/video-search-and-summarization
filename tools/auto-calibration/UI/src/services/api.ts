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
 * API service layer for Auto Magic Calibration
 * All API calls are properly typed - no 'any' types
 */

import { API_BASE_URL, API_ENDPOINTS } from '../constants';
import type {
  ApiResponse,
  ApiError,
  SystemCapabilitiesResponse,
  CreateProjectRequest,
  CreateProjectResponse,
  ListProjectsResponse,
  DeleteAllProjectsResponse,
  DeleteProjectsResponse,
  GetProjectInfoResponse,
  UpdateProjectInfoRequest,
  UploadVideoFilesResponse,
  ListVideoFilesResponse,
  DeleteVideoFileResponse,
  UploadGtFileResponse,
  UploadAlignmentResponse,
  UploadLayoutResponse,
  UploadFocalLengthResponse,
  GetFocalLengthResponse,
  VerifyProjectResponse,
  CalibrateResponse,
  GetAnnotationsResponse,
  GetAllAnnotationsResponse,
  RectificationResponse,
  RectificationCamerasResponse,
  DistortionParams,
  RtspCaptureRequestBody,
  RtspCaptureResponse,
  RtspSessionListResponse,
} from '../types';

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Handle API errors and convert to typed error object
 */
async function handleApiError(response: Response): Promise<never> {
  let errorMessage = response.statusText;
  let detail: string | undefined;

  try {
    const errorData = await response.json();
    if (errorData.detail) {
      const detailStr = typeof errorData.detail === 'string' 
        ? errorData.detail 
        : JSON.stringify(errorData.detail);
      detail = detailStr;
      errorMessage = detailStr;
    } else if (errorData.message) {
      errorMessage = errorData.message;
    }
  } catch {
    // If JSON parsing fails, use statusText
  }

  const error: ApiError = {
    status: response.status,
    statusText: response.statusText,
    message: errorMessage,
    detail,
  };

  throw error;
}

/**
 * Generic fetch wrapper with error handling
 */
async function fetchWithErrorHandling<T>(
  url: string,
  options?: RequestInit
): Promise<T> {
  try {
    const response = await fetch(`${API_BASE_URL}${url}`, {
      ...options,
      headers: {
        ...options?.headers,
      },
    });

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as T;
  } catch (error) {
    if ((error as ApiError).status) {
      throw error;
    }
    
    // Network or other errors
    const apiError: ApiError = {
      status: 0,
      statusText: 'Network Error',
      message: 'Failed to connect to the server. Please check your connection.',
    };
    throw apiError;
  }
}

// ============================================================================
// Project Management API
// ============================================================================

export const projectApi = {
  /**
   * Create a new project
   */
  async createProject(data: CreateProjectRequest): Promise<CreateProjectResponse> {
    const formData = new FormData();
    formData.append('project_name', data.project_name);

    const response = await fetch(`${API_BASE_URL}${API_ENDPOINTS.CREATE_PROJECT}`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as CreateProjectResponse;
  },

  /**
   * List all projects
   */
  async listProjects(): Promise<ListProjectsResponse> {
    return fetchWithErrorHandling<ListProjectsResponse>(API_ENDPOINTS.LIST_PROJECTS);
  },

  /**
   * Get project information
   */
  async getProjectInfo(projectId: string): Promise<GetProjectInfoResponse> {
    return fetchWithErrorHandling<GetProjectInfoResponse>(
      API_ENDPOINTS.GET_PROJECT_INFO(projectId)
    );
  },

  /**
   * Update project information
   */
  async updateProjectInfo(
    projectId: string,
    data: UpdateProjectInfoRequest
  ): Promise<GetProjectInfoResponse> {
    const formData = new FormData();
    formData.append('project_name', data.project_name);

    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.UPDATE_PROJECT_INFO(projectId)}`,
      {
        method: 'PATCH',
        body: formData,
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as GetProjectInfoResponse;
  },

  /**
   * Delete a project
   */
  async deleteProject(projectId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_PROJECT(projectId)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Delete selected projects in one backend request.
   */
  async deleteProjects(projectIds: string[]): Promise<DeleteProjectsResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_PROJECTS}`,
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_ids: projectIds }),
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as DeleteProjectsResponse;
  },

  /**
   * Delete every project that is not blocked (calibration running or RTSP active).
   */
  async deleteAllProjects(): Promise<DeleteAllProjectsResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_ALL_PROJECTS}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as DeleteAllProjectsResponse;
  },

  /**
   * Reset a project
   */
  async resetProject(projectId: string): Promise<GetProjectInfoResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.RESET_PROJECT(projectId)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as GetProjectInfoResponse;
  },
};

// ============================================================================
// Data Management API
// ============================================================================

export const dataApi = {
  /**
   * Upload video files with progress tracking
   */
  async uploadVideoFilesWithProgress(
    projectId: string,
    files: File[],
    onProgress: (percent: number) => void
  ): Promise<UploadVideoFilesResponse> {
    return new Promise((resolve, reject) => {
      const formData = new FormData();
      files.forEach((file) => {
        formData.append('files', file);
      });

      const xhr = new XMLHttpRequest();
      
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          onProgress(percent);
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText) as UploadVideoFilesResponse);
        } else {
          reject(new Error(xhr.responseText || 'Upload failed'));
        }
      };

      xhr.onerror = () => reject(new Error('Network error during upload'));

      xhr.open('POST', `${API_BASE_URL}${API_ENDPOINTS.UPLOAD_VIDEO_FILES(projectId)}`);
      xhr.send(formData);
    });
  },

  /**
   * Upload video files (without progress)
   */
  async uploadVideoFiles(
    projectId: string,
    files: File[]
  ): Promise<UploadVideoFilesResponse> {
    const formData = new FormData();
    files.forEach((file) => {
      formData.append('files', file);
    });

    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.UPLOAD_VIDEO_FILES(projectId)}`,
      {
        method: 'POST',
        body: formData,
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as UploadVideoFilesResponse;
  },

  /**
   * List video files
   */
  async listVideoFiles(projectId: string): Promise<ListVideoFilesResponse> {
    return fetchWithErrorHandling<ListVideoFilesResponse>(
      API_ENDPOINTS.LIST_VIDEO_FILES(projectId)
    );
  },

  /**
   * Delete a video file
   */
  async deleteVideoFile(
    projectId: string,
    videoName: string
  ): Promise<DeleteVideoFileResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_VIDEO_FILE(projectId, videoName)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as DeleteVideoFileResponse;
  },

  /**
   * Upload ground truth file
   */
  async uploadGroundTruthFile(
    projectId: string,
    file: File
  ): Promise<UploadGtFileResponse> {
    const formData = new FormData();
    formData.append('gt_file', file);

    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.UPLOAD_GT_FILE(projectId)}`,
      {
        method: 'POST',
        body: formData,
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as UploadGtFileResponse;
  },

  /**
   * Delete ground truth file
   */
  async deleteGroundTruthFile(projectId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_GT_FILE(projectId)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Upload alignment file (creates or replaces existing alignment)
   * @param coordSpace rectified = UI draw/save; original = CLI / distorted-frame JSON
   */
  async uploadAlignmentFile(
    projectId: string,
    file: File,
    coordSpace: 'rectified' | 'original' = 'original'
  ): Promise<UploadAlignmentResponse> {
    const formData = new FormData();
    formData.append('alignment_file', file);

    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.UPLOAD_ALIGNMENT(projectId)}?coord_space=${coordSpace}`,
      {
        method: 'POST',
        body: formData,
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as UploadAlignmentResponse;
  },

  /**
   * Get alignment JSON for a project
   */
  async getAlignmentFile(projectId: string): Promise<unknown> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_ALIGNMENT(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json();
  },

  /**
   * Delete alignment file
   */
  async deleteAlignmentFile(projectId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_ALIGNMENT(projectId)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Upload layout file
   */
  async uploadLayoutFile(
    projectId: string,
    file: File
  ): Promise<UploadLayoutResponse> {
    const formData = new FormData();
    formData.append('layout_file', file);

    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.UPLOAD_LAYOUT(projectId)}`,
      {
        method: 'POST',
        body: formData,
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as UploadLayoutResponse;
  },

  /**
   * Get layout image
   */
  async getLayoutImage(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_LAYOUT(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Delete layout file
   */
  async deleteLayoutFile(projectId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_LAYOUT(projectId)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Upload focal length data
   */
  async uploadFocalLength(
    projectId: string,
    focalLengths: number[]
  ): Promise<UploadFocalLengthResponse> {
    const formData = new FormData();
    focalLengths.forEach((fl) => {
      formData.append('focal_length', fl.toString());
    });

    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.UPLOAD_FOCAL_LENGTH(projectId)}`,
      {
        method: 'POST',
        body: formData,
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as UploadFocalLengthResponse;
  },

  /**
   * Get focal length data
   */
  async getFocalLength(projectId: string): Promise<GetFocalLengthResponse> {
    return fetchWithErrorHandling<GetFocalLengthResponse>(
      API_ENDPOINTS.GET_FOCAL_LENGTH(projectId)
    );
  },

  /**
   * Delete focal length data
   */
  async deleteFocalLength(projectId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_FOCAL_LENGTH(projectId)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Verify project
   */
  async verifyProject(projectId: string): Promise<VerifyProjectResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.VERIFY_PROJECT(projectId)}`,
      {
        method: 'POST',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as VerifyProjectResponse;
  },

  /**
   * Relaunch calibration (deletes results and re-verifies)
   */
  async relaunchCalibration(projectId: string): Promise<VerifyProjectResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.RELAUNCH_CALIBRATION(projectId)}`,
      {
        method: 'POST',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as VerifyProjectResponse;
  },

  /**
   * Get video frame
   */
  async getVideoFrame(
    projectId: string,
    videoName: string,
    frameIndex: number,
    useRectified: boolean = false
  ): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_VIDEO_FRAME(projectId, videoName, frameIndex, useRectified)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Save annotations (ROIs and tripwires) for a video
   */
  async saveAnnotations(
    projectId: string,
    videoName: string,
    rois: unknown[],
    tripwireLines: unknown[],
    tripwireDirections: unknown[]
  ): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.SAVE_ANNOTATIONS(projectId, videoName)}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          rois,
          tripwireLines,
          tripwireDirections,
        }),
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Get annotations for a specific video
   * @param includeGlobalProjection When true (default), includes projected global ROIs/TWs.
   *   Pass false for Parameters editing so only manually drawn camera annotations are returned.
   * @param resultType Optional 'amc' | 'vggt' for which calibration to use when projecting globals.
   */
  async getAnnotations(
    projectId: string,
    videoName: string,
    useAlignmentData: boolean = false,
    includeGlobalProjection: boolean = true,
    resultType?: 'amc' | 'vggt',
  ): Promise<GetAnnotationsResponse> {
    return fetchWithErrorHandling<GetAnnotationsResponse>(
      API_ENDPOINTS.GET_ANNOTATIONS(
        projectId,
        videoName,
        useAlignmentData,
        includeGlobalProjection,
        resultType,
      )
    );
  },

  /**
   * Get all annotations for a project
   */
  async getAllAnnotations(projectId: string): Promise<GetAllAnnotationsResponse> {
    return fetchWithErrorHandling<GetAllAnnotationsResponse>(
      API_ENDPOINTS.GET_ALL_ANNOTATIONS(projectId)
    );
  },

  /**
   * Delete annotations for a specific video
   */
  async deleteAnnotations(projectId: string, videoName: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_ANNOTATIONS(projectId, videoName)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Get configuration parameters
   */
  async getConfig(): Promise<unknown> {
    return fetchWithErrorHandling(
      API_ENDPOINTS.GET_CONFIG
    );
  },

  /**
   * Get default configuration parameters (true defaults, never change)
   */
  async getConfigDefaults(): Promise<unknown> {
    return fetchWithErrorHandling(
      API_ENDPOINTS.GET_CONFIG_DEFAULTS
    );
  },

  /**
   * Update configuration parameters
   */
  async updateConfig(projectId: string, configParams: unknown): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.UPDATE_CONFIG(projectId)}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(configParams),
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },
};

// ============================================================================
// Calibration API
// ============================================================================

export const calibrationApi = {
  /**
   * Estimate distortion from frame 0. Full videos require explicit generation.
   */
  async startRectification(projectId: string): Promise<RectificationResponse> {
    return fetchWithErrorHandling<RectificationResponse>(
      API_ENDPOINTS.START_RECTIFICATION(projectId),
      { method: 'POST' }
    );
  },

  /**
   * Stage uploaded videos that are already linear under the E2E artifact names.
   */
  async stageAlreadyLinearMedia(projectId: string): Promise<RectificationResponse> {
    return fetchWithErrorHandling<RectificationResponse>(
      API_ENDPOINTS.STAGE_LINEAR_MEDIA(projectId),
      { method: 'POST' }
    );
  },

  async getRectificationStatus(projectId: string): Promise<RectificationResponse> {
    return fetchWithErrorHandling<RectificationResponse>(
      API_ENDPOINTS.RECTIFICATION_STATUS(projectId)
    );
  },

  async getRectificationCameras(projectId: string): Promise<RectificationCamerasResponse> {
    return fetchWithErrorHandling<RectificationCamerasResponse>(
      API_ENDPOINTS.RECTIFICATION_CAMERAS(projectId)
    );
  },

  async previewRectificationFrame(
    projectId: string,
    cameraId: string,
    params: DistortionParams,
  ): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.RECTIFICATION_PREVIEW(projectId, cameraId)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      },
    );
    if (!response.ok) {
      await handleApiError(response);
    }
    return response.blob();
  },

  async startManualRectification(projectId: string): Promise<RectificationResponse> {
    return fetchWithErrorHandling<RectificationResponse>(
      API_ENDPOINTS.RECTIFICATION_MANUAL_START(projectId),
      { method: 'POST' },
    );
  },

  async commitManualRectification(
    projectId: string,
    cameras: Record<string, DistortionParams>,
  ): Promise<RectificationResponse> {
    return fetchWithErrorHandling<RectificationResponse>(
      API_ENDPOINTS.RECTIFICATION_MANUAL_COMMIT(projectId),
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cameras }),
      },
    );
  },

  /**
   * Download zipped output/single_view_results after rectification completes.
   */
  async downloadSingleViewResults(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DOWNLOAD_SINGLE_VIEW_RESULTS(projectId)}`,
    );
    if (!response.ok) {
      await handleApiError(response);
    }
    return response.blob();
  },

  /**
   * Start calibration
   */
  async startCalibration(projectId: string, detectorType: string = 'resnet'): Promise<CalibrateResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.CALIBRATE(projectId)}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ detector_type: detectorType }),
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as CalibrateResponse;
  },

  /**
   * Stop calibration
   */
  async stopCalibration(projectId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.STOP_CALIBRATION(projectId)}`,
      {
        method: 'POST',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Start VGGT calibration
   */
  async startVggtCalibration(projectId: string): Promise<CalibrateResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.VGGT_CALIBRATE(projectId)}`,
      {
        method: 'POST',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as CalibrateResponse;
  },

  /**
   * Generate derived layout artifacts for all available calibration trees.
   */
  async startPostprocess(projectId: string): Promise<CalibrateResponse> {
    return fetchWithErrorHandling<CalibrateResponse>(
      API_ENDPOINTS.POSTPROCESS(projectId),
      { method: 'POST' }
    );
  },

  /**
   * Get overlay image
   */
  async getOverlayImage(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_OVERLAY_IMAGE(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get one per-camera virtual-GT overlay for AMC or VGGT.
   */
  async getVirtualGtOverlay(
    projectId: string,
    cameraId: number,
    resultType: 'amc' | 'vggt'
  ): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_VIRTUAL_GT_OVERLAY(
        projectId,
        cameraId,
        resultType
      )}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get camera parameters
   */
  async getCameraParameters(
    projectId: string,
    cameraId: number
  ): Promise<string> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_CAMERA_PARAMETERS(projectId, cameraId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.text();
  },

  /**
   * Get evaluation metrics image
   */
  async getEvaluationMetrics(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_EVALUATION_LAYOUT(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get evaluation layout image
   */
  async getEvaluationLayout(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_EVALUATION_LAYOUT(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get evaluation statistics
   */
  async getEvaluationStatistics(projectId: string): Promise<unknown> {
    return fetchWithErrorHandling(
      API_ENDPOINTS.GET_EVALUATION_STATISTICS(projectId)
    );
  },

  /**
   * Get VGGT evaluation metrics (ZIP file)
   */
  async getVggtEvaluationMetrics(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_VGGT_EVALUATION_METRICS(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get VGGT evaluation layout image
   */
  async getVggtEvaluationLayout(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_VGGT_EVALUATION_LAYOUT(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get VGGT evaluation statistics
   */
  async getVggtEvaluationStatistics(projectId: string): Promise<unknown> {
    return fetchWithErrorHandling(
      API_ENDPOINTS.GET_VGGT_EVALUATION_STATISTICS(projectId)
    );
  },

  /**
   * Get MV3DT result (ZIP file)
   */
  async getMv3dtResult(projectId: string, resultType: 'amc' | 'vggt' = 'amc'): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_MV3DT_RESULT(projectId, resultType)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Delete calibration results
   */
  async deleteResult(projectId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DELETE_RESULT(projectId)}`,
      {
        method: 'DELETE',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Export calibration with ROI/Tripwire world coordinates (generates export file on server)
   */
  async exportCalibration(
    projectId: string,
    resultType: 'amc' | 'vggt' = 'amc',
    calibrationType: 'cartesian' | 'image' = 'cartesian',
  ): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.EXPORT_CALIBRATION(projectId, resultType, calibrationType)}`,
      { method: 'POST' }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Save edited calibration JSON to project export file (project_{id}_exported.json).
   * So future Full Export AMC loads this edited version.
   */
  async saveExportedCalibration(
    projectId: string,
    resultType: 'amc' | 'vggt' = 'amc',
    jsonContent: string,
    calibrationType: 'cartesian' | 'image' = 'cartesian',
  ): Promise<ApiResponse> {
    // Send raw JSON string so server preserves formatting (e.g. 0.0 stays 0.0, not 0)
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.SAVE_EXPORT_CALIBRATION(projectId, resultType, calibrationType)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'text/plain; charset=utf-8' },
        body: jsonContent,
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.json() as ApiResponse;
  },

  /**
   * Download exported calibration JSON
   */
  async downloadExportedCalibration(
    projectId: string,
    resultType: 'amc' | 'vggt' = 'amc',
    calibrationType: 'cartesian' | 'image' = 'cartesian',
    snapshot: 'exported' | 'projected' = 'exported',
  ): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.DOWNLOAD_EXPORT_CALIBRATION(projectId, resultType, calibrationType, snapshot)}`,
      // Avoid stale JSON in the UI after PUT save (browser HTTP cache).
      { cache: 'no-store' },
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Check if export file exists
   */
  async checkExportExists(
    projectId: string,
    calibrationType: 'cartesian' | 'image' = 'cartesian',
  ): Promise<unknown> {
    return fetchWithErrorHandling(
      API_ENDPOINTS.CHECK_EXPORT_EXISTS(projectId, calibrationType)
    );
  },

  /**
   * Generate ROI/Tripwire BEV visualization
   */
  async generateRoiBev(projectId: string, resultType: 'amc' | 'vggt' = 'amc'): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GENERATE_ROI_BEV(projectId, resultType)}`,
      {
        method: 'POST',
      }
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get VGGT overlay image
   */
  async getVggtOverlayImage(projectId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_VGGT_OVERLAY_IMAGE(projectId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.blob();
  },

  /**
   * Get VGGT camera parameters
   */
  async getVggtCameraParameters(
    projectId: string,
    cameraId: number
  ): Promise<string> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.GET_VGGT_CAMERA_PARAMETERS(projectId, cameraId)}`
    );

    if (!response.ok) {
      await handleApiError(response);
    }

    return await response.text();
  },
};

// ============================================================================
// RTSP capture (VIOS)
// ============================================================================

export const rtspApi = {
  async startCapture(
    projectId: string,
    body: RtspCaptureRequestBody,
  ): Promise<RtspCaptureResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.RTSP_CAPTURE_START(projectId)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
    if (!response.ok) {
      await handleApiError(response);
    }
    return (await response.json()) as RtspCaptureResponse;
  },

  async getCaptureStatus(
    projectId: string,
    sessionId: string,
  ): Promise<RtspCaptureResponse> {
    return fetchWithErrorHandling<RtspCaptureResponse>(
      API_ENDPOINTS.RTSP_CAPTURE_STATUS(projectId, sessionId),
    );
  },

  async stopCapture(projectId: string, sessionId: string): Promise<RtspCaptureResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.RTSP_CAPTURE_STOP(projectId, sessionId)}`,
      { method: 'POST' },
    );
    if (!response.ok) {
      await handleApiError(response);
    }
    return (await response.json()) as RtspCaptureResponse;
  },

  async listSessions(projectId: string): Promise<RtspSessionListResponse> {
    return fetchWithErrorHandling<RtspSessionListResponse>(
      API_ENDPOINTS.RTSP_SESSIONS_LIST(projectId),
    );
  },

  async deleteSession(projectId: string, sessionId: string): Promise<ApiResponse> {
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.RTSP_SESSION_DELETE(projectId, sessionId)}`,
      { method: 'DELETE' },
    );
    if (!response.ok) {
      await handleApiError(response);
    }
    return (await response.json()) as ApiResponse;
  },
};

// ============================================================================
// System API
// ============================================================================

export const systemApi = {
  /**
   * Get system capabilities
   */
  async getCapabilities(): Promise<SystemCapabilitiesResponse> {
    return fetchWithErrorHandling(
      API_ENDPOINTS.SYSTEM_CAPABILITIES
    ) as Promise<SystemCapabilitiesResponse>;
  },
};

// ============================================================================
// Export combined API
// ============================================================================

export const api = {
  system: systemApi,
  project: projectApi,
  data: dataApi,
  calibration: calibrationApi,
  rtsp: rtspApi,
};

export default api;
