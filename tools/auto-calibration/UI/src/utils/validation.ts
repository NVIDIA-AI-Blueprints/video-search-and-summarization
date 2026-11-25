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
 * Validation utilities for Auto Magic Calibration
 * All functions are properly typed
 */

import {
  PROJECT_NAME_VALIDATION,
  VIDEO_NAMING_PATTERN,
  MIN_CAMERAS_REQUIRED,
  MAX_FILE_SIZES,
  ACCEPTED_VIDEO_TYPES,
  ACCEPTED_IMAGE_TYPES,
  ACCEPTED_JSON_TYPES,
  ACCEPTED_ZIP_TYPES,
  REQUIRED_VIDEO_WIDTH,
  REQUIRED_VIDEO_HEIGHT,
} from '../constants';
import type { ValidationError, Project, ProjectState } from '../types';

// ============================================================================
// Project Validation
// ============================================================================

/**
 * Validate project name
 */
export function validateProjectName(name: string): ValidationError | null {
  if (!name || name.length < PROJECT_NAME_VALIDATION.MIN_LENGTH) {
    return {
      field: 'project_name',
      message: `Project name must be at least ${PROJECT_NAME_VALIDATION.MIN_LENGTH} characters`,
    };
  }

  if (name.length > PROJECT_NAME_VALIDATION.MAX_LENGTH) {
    return {
      field: 'project_name',
      message: `Project name must be at most ${PROJECT_NAME_VALIDATION.MAX_LENGTH} characters`,
    };
  }

  if (!PROJECT_NAME_VALIDATION.PATTERN.test(name)) {
    return {
      field: 'project_name',
      message: PROJECT_NAME_VALIDATION.ERROR_MESSAGE,
    };
  }

  return null;
}

/**
 * Check if project is ready for calibration
 */
export function isProjectReadyForCalibration(project: Project): boolean {
  return (
    project.video_files_count >= MIN_CAMERAS_REQUIRED &&
    project.layout_file &&
    project.alignment_file
  );
}

/**
 * Check if project can be edited
 */
export function canEditProject(project: Project): boolean {
  return project.project_state !== 'RUNNING' as ProjectState;
}

// ============================================================================
// File Validation
// ============================================================================

/**
 * Validate video file
 */
export function validateVideoFile(file: File): ValidationError | null {
  // Check file size
  if (file.size === 0) {
    return {
      field: 'video_file',
      message: 'Invalid video file: file is empty.',
    };
  }

  if (file.size > MAX_FILE_SIZES.VIDEO) {
    return {
      field: 'video_file',
      message: `Video file must be smaller than ${MAX_FILE_SIZES.VIDEO / (1024 * 1024)} MB`,
    };
  }

  // Check file type
  const fileType = file.type.toLowerCase();
  const isValidType = ACCEPTED_VIDEO_TYPES.some((type) => type === fileType);
  
  if (!isValidType) {
    return {
      field: 'video_file',
      message: 'Invalid video file type. Accepted: MP4',
    };
  }

  return null;
}

/**
 * Read video resolution from a local File via browser metadata (no upload).
 */
export function getVideoFileResolution(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;

    const cleanup = () => {
      URL.revokeObjectURL(url);
      video.removeAttribute('src');
      video.load();
    };

    video.onloadedmetadata = () => {
      const width = video.videoWidth;
      const height = video.videoHeight;
      cleanup();
      if (!width || !height) {
        reject(
          new Error(
            `Could not determine resolution for '${file.name}' (reported ${width}x${height})`,
          ),
        );
        return;
      }
      resolve({ width, height });
    };

    video.onerror = () => {
      cleanup();
      reject(
        new Error(
          `Failed to read resolution for video '${file.name}'. Ensure the file is a valid MP4.`,
        ),
      );
    };

    video.src = url;
  });
}

/**
 * Check ISO base media file format signature without relying on browser codecs.
 * MP4 normally has an `ftyp` box first, but valid files can contain leading boxes.
 * Traverse only box headers so large leading boxes do not require reading their payloads.
 */
async function hasMp4ContainerHeader(file: File): Promise<boolean> {
  let offset = 0;

  for (let boxesRead = 0; boxesRead < 128 && offset + 8 <= file.size; boxesRead += 1) {
    const header = new Uint8Array(await file.slice(offset, offset + 16).arrayBuffer());
    if (header.length < 8) return false;
    const view = new DataView(header.buffer, header.byteOffset, header.byteLength);
    let boxSize = view.getUint32(0);
    let boxHeaderSize = 8;
    const isFileTypeBox =
      header[4] === 0x66 &&
      header[5] === 0x74 &&
      header[6] === 0x79 &&
      header[7] === 0x70;

    if (boxSize === 1) {
      if (header.length < 16 || view.getUint32(8) !== 0) return false;
      boxSize = view.getUint32(12);
      boxHeaderSize = 16;
    }
    if (boxSize < boxHeaderSize || offset + boxSize > file.size) return false;

    if (isFileTypeBox) {
      return boxSize >= boxHeaderSize + 8 && (boxSize - boxHeaderSize) % 4 === 0;
    }

    offset += boxSize;
  }

  return false;
}

/**
 * Ensure a local video file is exactly 1920x1080. Returns an error message or null.
 *
 * Browsers cannot decode every valid MP4 (for example H.264 High 4:4:4), so a
 * codec decode failure for a file with an MP4 container header is left to the
 * server's container-level resolution check. Files without that header still
 * receive an immediate client-side error.
 */
export async function validateVideoResolution(file: File): Promise<string | null> {
  try {
    const { width, height } = await getVideoFileResolution(file);
    if (width !== REQUIRED_VIDEO_WIDTH || height !== REQUIRED_VIDEO_HEIGHT) {
      return (
        `Video '${file.name}' has resolution ${width}x${height}; ` +
        `required resolution is ${REQUIRED_VIDEO_WIDTH}x${REQUIRED_VIDEO_HEIGHT}.`
      );
    }
    return null;
  } catch (error: unknown) {
    try {
      if (await hasMp4ContainerHeader(file)) {
        return null;
      }
    } catch {
      // Fall through: a file that cannot be read locally cannot be verified as MP4.
    }

    return error instanceof Error
      ? error.message
      : `Failed to read resolution for video '${file.name}'. Ensure the file is a valid MP4.`;
  }
}

/**
 * Partition files into those that pass 1080p and those that fail, with reasons.
 */
export async function partitionVideosByResolution(files: File[]): Promise<{
  accepted: File[];
  rejected: { file: File; reason: string }[];
}> {
  const results = await Promise.all(
    files.map(async (file) => {
      const typeError = validateVideoFile(file);
      if (typeError) {
        return { file, reason: typeError.message };
      }
      return { file, reason: await validateVideoResolution(file) };
    }),
  );

  const accepted: File[] = [];
  const rejected: { file: File; reason: string }[] = [];
  for (const { file, reason } of results) {
    if (reason) {
      rejected.push({ file, reason });
    } else {
      accepted.push(file);
    }
  }

  return { accepted, rejected };
}

/**
 * Validate video file name
 */
export function validateVideoFileName(fileName: string): boolean {
  return VIDEO_NAMING_PATTERN.test(fileName);
}

/**
 * Validate image file
 */
export function validateImageFile(file: File): ValidationError | null {
  // Check file size
  if (file.size > MAX_FILE_SIZES.IMAGE) {
    return {
      field: 'image_file',
      message: `Image file must be smaller than ${MAX_FILE_SIZES.IMAGE / (1024 * 1024)} MB`,
    };
  }

  // Check file type
  const fileType = file.type.toLowerCase();
  const isValidType = ACCEPTED_IMAGE_TYPES.some((type) => type === fileType);
  
  if (!isValidType) {
    return {
      field: 'image_file',
      message: 'Invalid image file type. Accepted: PNG, JPG, JPEG',
    };
  }

  return null;
}

/**
 * Validate JSON file
 */
export function validateJSONFile(file: File): ValidationError | null {
  // Check file size
  if (file.size > MAX_FILE_SIZES.JSON) {
    return {
      field: 'json_file',
      message: `JSON file must be smaller than ${MAX_FILE_SIZES.JSON / (1024 * 1024)} MB`,
    };
  }

  // Check file type
  const fileType = file.type.toLowerCase();
  const fileName = file.name.toLowerCase();
  const isValidType = ACCEPTED_JSON_TYPES.some((type) => type === fileType) || 
                      fileName.endsWith('.json');
  
  if (!isValidType) {
    return {
      field: 'json_file',
      message: 'Invalid file type. Must be a JSON file',
    };
  }

  return null;
}

/**
 * Validate ZIP file
 */
export function validateZIPFile(file: File): ValidationError | null {
  // Check file size
  if (file.size > MAX_FILE_SIZES.ZIP) {
    return {
      field: 'zip_file',
      message: `ZIP file must be smaller than ${MAX_FILE_SIZES.ZIP / (1024 * 1024)} MB`,
    };
  }

  // Check file type
  const fileType = file.type.toLowerCase();
  const fileName = file.name.toLowerCase();
  const isValidType = ACCEPTED_ZIP_TYPES.some((type) => type === fileType) || 
                      fileName.endsWith('.zip');
  
  if (!isValidType) {
    return {
      field: 'zip_file',
      message: 'Invalid file type. Must be a ZIP file',
    };
  }

  return null;
}

// ============================================================================
// Focal Length Validation
// ============================================================================

/**
 * Validate focal length array
 */
export function validateFocalLengths(
  focalLengths: number[],
  expectedCount: number
): ValidationError | null {
  // Empty array is valid (optional focal lengths)
  if (focalLengths.length === 0) {
    return null;
  }

  // Must match camera count
  if (focalLengths.length !== expectedCount) {
    return {
      field: 'focal_length',
      message: `Focal length count (${focalLengths.length}) must match video count (${expectedCount})`,
    };
  }

  // All values must be positive
  const hasNegative = focalLengths.some((fl) => fl <= 0);
  if (hasNegative) {
    return {
      field: 'focal_length',
      message: 'All focal length values must be positive',
    };
  }

  return null;
}

/**
 * Parse focal length string input
 */
export function parseFocalLengthInput(input: string): number[] | ValidationError {
  const trimmed = input.trim();
  
  // Empty string is valid
  if (trimmed === '') {
    return [];
  }

  const parts = trimmed.split(',').map((s) => s.trim());
  const numbers: number[] = [];

  for (const part of parts) {
    const num = parseFloat(part);
    if (isNaN(num)) {
      return {
        field: 'focal_length',
        message: `Invalid number: "${part}"`,
      };
    }
    numbers.push(num);
  }

  return numbers;
}

// ============================================================================
// Annotation Validation
// ============================================================================

/**
 * Validate ROI polygon (must have at least 3 points)
 */
export function validateROI(points: { x: number; y: number }[]): ValidationError | null {
  if (points.length < 3) {
    return {
      field: 'roi',
      message: 'ROI must have at least 3 points',
    };
  }

  return null;
}

/**
 * Validate tripwire (must have exactly 2 points)
 */
export function validateTripwire(
  start: { x: number; y: number } | null,
  end: { x: number; y: number } | null
): ValidationError | null {
  if (!start || !end) {
    return {
      field: 'tripwire',
      message: 'Tripwire must have both start and end points',
    };
  }

  // Check if points are different
  if (start.x === end.x && start.y === end.y) {
    return {
      field: 'tripwire',
      message: 'Tripwire start and end points must be different',
    };
  }

  return null;
}

// ============================================================================
// Alignment Validation
// ============================================================================

/**
 * Validate alignment data (must have 4 complete point sets).
 * Multi-camera: each set needs cam0, cam1, bev. Single-camera: each set needs cam (cam0) and bev only.
 */
export function validateAlignmentData(
  pointSets: Array<{
    cam0: { x: number; y: number } | null;
    cam1: { x: number; y: number } | null;
    bev: { x: number; y: number } | null;
  }>,
  isSingleCam: boolean = false
): ValidationError | null {
  if (pointSets.length !== 4) {
    return {
      field: 'alignment',
      message: 'Alignment must have exactly 4 point sets',
    };
  }

  for (let i = 0; i < pointSets.length; i++) {
    const set = pointSets[i];
    if (isSingleCam) {
      if (!set.cam0 || !set.bev) {
        return {
          field: 'alignment',
          message: `Point set ${i + 1} is incomplete (need Camera + Layout)`,
        };
      }
    } else {
      if (!set.cam0 || !set.cam1 || !set.bev) {
        return {
          field: 'alignment',
          message: `Point set ${i + 1} is incomplete`,
        };
      }
    }
  }

  return null;
}

// ============================================================================
// Multiple File Validation
// ============================================================================

/**
 * Validate multiple files at once
 */
export function validateFiles(
  files: File[],
  validator: (file: File) => ValidationError | null
): Record<string, ValidationError> {
  const errors: Record<string, ValidationError> = {};

  files.forEach((file) => {
    const error = validator(file);
    if (error) {
      errors[file.name] = error;
    }
  });

  return errors;
}

/**
 * Check if any validation errors exist
 */
export function hasValidationErrors(errors: Record<string, ValidationError>): boolean {
  return Object.keys(errors).length > 0;
}
