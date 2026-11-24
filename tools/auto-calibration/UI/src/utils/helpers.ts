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
 * Helper utility functions for Auto Magic Calibration
 * All functions are properly typed
 */

import type {
  AlignmentDataJSON,
  AlignmentDataJSONSingleCam,
  AlignmentPointSet,
  ProjectState,
  Point2D,
} from '../types';

// ============================================================================
// File Size Formatting
// ============================================================================

/**
 * Format file size in bytes to human-readable string
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 Bytes';

  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}

// ============================================================================
// Date/Time Formatting
// ============================================================================

/**
 * Format timestamp to readable date string
 */
export function formatDate(date: Date | string | number): string {
  const d = new Date(date);
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

/**
 * Format timestamp to readable date-time string
 */
export function formatDateTime(date: Date | string | number): string {
  const d = new Date(date);
  return d.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Format duration in milliseconds to readable string
 */
export function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${seconds}s`;
}

// ============================================================================
// Project State Helpers
// ============================================================================

/**
 * Get color for project state
 */
export function getProjectStateColor(state: ProjectState): string {
  const colorMap: Record<ProjectState, string> = {
    INIT: '#9e9e9e',      // Grey
    READY: '#2196f3',     // Blue
    RUNNING: '#ff9800',   // Orange
    COMPLETED: '#4caf50', // Green
    ERROR: '#f44336',     // Red
  };
  return colorMap[state] || '#9e9e9e';
}

/**
 * Get display label for project state
 */
export function getProjectStateLabel(state: ProjectState): string {
  const labelMap: Record<ProjectState, string> = {
    INIT: 'Initializing',
    READY: 'Ready',
    RUNNING: 'Running',
    COMPLETED: 'Completed',
    ERROR: 'Error',
  };
  return labelMap[state] || state;
}

/**
 * Get icon for project state
 */
export function getProjectStateIcon(state: ProjectState): string {
  const iconMap: Record<ProjectState, string> = {
    INIT: '⚪',
    READY: '🔵',
    RUNNING: '🟠',
    COMPLETED: '✅',
    ERROR: '❌',
  };
  return iconMap[state] || '⚪';
}

// ============================================================================
// Camera Name Helpers
// ============================================================================

/**
 * Get camera ID from video filename
 */
export function getCameraIdFromFilename(filename: string): number | null {
  const match = filename.match(/cam_(\d{2})/);
  return match ? parseInt(match[1], 10) : null;
}

/**
 * Format camera ID to video filename
 */
export function formatCameraFilename(cameraId: number): string {
  return `cam_${cameraId.toString().padStart(2, '0')}.mp4`;
}

/**
 * Get camera display name
 */
export function getCameraDisplayName(cameraId: number): string {
  return `Camera ${cameraId}`;
}

// ============================================================================
// Alignment Data Conversion
// ============================================================================

/**
 * Convert alignment data to JSON format for backend.
 * Multi-camera: [cam0, cam1, bev] per set. Single-camera: [cam, bev] per set (1 camera + layout only).
 */
export function alignmentDataToJSON(
  pointSets: AlignmentPointSet[],
  isSingleCam: boolean = false
): AlignmentDataJSON | AlignmentDataJSONSingleCam {
  if (isSingleCam) {
    return pointSets
      .filter((set) => set.cam0 && set.bev)
      .map((set) => [
        [set.cam0!.x, set.cam0!.y],
        [set.bev!.x, set.bev!.y],
      ]) as AlignmentDataJSONSingleCam;
  }
  return pointSets
    .filter((set) => set.cam0 && set.cam1 && set.bev)
    .map((set) => [
      [set.cam0!.x, set.cam0!.y],
      [set.cam1!.x, set.cam1!.y],
      [set.bev!.x, set.bev!.y],
    ]) as AlignmentDataJSON;
}

/**
 * Convert alignment data from JSON format. Detects single-cam (2 points per set) vs multi-cam (3 points per set).
 */
export function alignmentDataFromJSON(
  json: AlignmentDataJSON | AlignmentDataJSONSingleCam | unknown
): AlignmentPointSet[] {
  const arr = Array.isArray(json) ? json : [];
  if (arr.length === 0) return [];
  const first = arr[0];
  const isSingleCam = Array.isArray(first) && first.length === 2;
  if (isSingleCam) {
    return (arr as AlignmentDataJSONSingleCam).map((pointSet) => ({
      cam0: { x: pointSet[0][0], y: pointSet[0][1] },
      cam1: null,
      bev: { x: pointSet[1][0], y: pointSet[1][1] },
    }));
  }
  return (arr as AlignmentDataJSON).map((pointSet) => ({
    cam0: { x: pointSet[0][0], y: pointSet[0][1] },
    cam1: { x: pointSet[1][0], y: pointSet[1][1] },
    bev: { x: pointSet[2][0], y: pointSet[2][1] },
  }));
}

// ============================================================================
// URL Helpers
// ============================================================================

/**
 * Create object URL for file
 */
export function createObjectURL(file: File): string {
  return URL.createObjectURL(file);
}

/**
 * Revoke object URL
 */
export function revokeObjectURL(url: string): void {
  URL.revokeObjectURL(url);
}

/**
 * Download data as file
 */
export function downloadAsFile(
  data: string | Blob,
  filename: string,
  mimeType: string
): void {
  const blob = typeof data === 'string' 
    ? new Blob([data], { type: mimeType })
    : data;
  
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// ============================================================================
// Array Helpers
// ============================================================================

/**
 * Generate array of numbers from 0 to n-1
 */
export function range(n: number): number[] {
  return Array.from({ length: n }, (_, i) => i);
}

/**
 * Chunk array into smaller arrays
 */
export function chunk<T>(array: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < array.length; i += size) {
    chunks.push(array.slice(i, i + size));
  }
  return chunks;
}

// ============================================================================
// Debounce and Throttle
// ============================================================================

/**
 * Debounce function
 */
export function debounce<T extends (...args: Parameters<T>) => ReturnType<T>>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout> | null = null;

  return function executedFunction(...args: Parameters<T>) {
    const later = () => {
      timeout = null;
      func(...args);
    };

    if (timeout !== null) {
      clearTimeout(timeout);
    }
    timeout = setTimeout(later, wait);
  };
}

/**
 * Throttle function
 */
export function throttle<T extends (...args: Parameters<T>) => ReturnType<T>>(
  func: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle: boolean;

  return function executedFunction(...args: Parameters<T>) {
    if (!inThrottle) {
      func(...args);
      inThrottle = true;
      setTimeout(() => {
        inThrottle = false;
      }, limit);
    }
  };
}

// ============================================================================
// Matrix Helpers
// ============================================================================

/**
 * Format matrix for display
 */
export function formatMatrix(matrix: number[][], decimals: number = 6): string {
  return matrix
    .map((row) => 
      row.map((val) => val.toFixed(decimals).padStart(decimals + 4)).join('  ')
    )
    .join('\n');
}

/**
 * Transpose matrix
 */
export function transposeMatrix(matrix: number[][]): number[][] {
  if (matrix.length === 0) return [];
  return matrix[0].map((_, colIndex) => matrix.map((row) => row[colIndex]));
}

// ============================================================================
// Color Helpers
// ============================================================================

/**
 * Convert hex color to RGBA
 */
export function hexToRgba(hex: string, alpha: number = 1): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Get contrasting text color (black or white) for a background color
 */
export function getContrastTextColor(backgroundColor: string): string {
  // Remove # if present
  const hex = backgroundColor.replace('#', '');
  
  // Convert to RGB
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  
  // Calculate luminance
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  
  return luminance > 0.5 ? '#000000' : '#ffffff';
}

// ============================================================================
// Canvas Helpers
// ============================================================================

/**
 * Calculate distance between two points
 */
export function calculateDistance(p1: Point2D, p2: Point2D): number {
  return Math.sqrt(Math.pow(p2.x - p1.x, 2) + Math.pow(p2.y - p1.y, 2));
}

/**
 * Check if point is inside polygon
 */
export function isPointInPolygon(point: Point2D, polygon: Point2D[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i].x;
    const yi = polygon[i].y;
    const xj = polygon[j].x;
    const yj = polygon[j].y;

    const intersect =
      yi > point.y !== yj > point.y &&
      point.x < ((xj - xi) * (point.y - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/**
 * Get point on line closest to target point
 */
export function getClosestPointOnLine(
  point: Point2D,
  lineStart: Point2D,
  lineEnd: Point2D
): Point2D {
  const dx = lineEnd.x - lineStart.x;
  const dy = lineEnd.y - lineStart.y;
  const lengthSquared = dx * dx + dy * dy;

  if (lengthSquared === 0) {
    return { ...lineStart };
  }

  const t = Math.max(
    0,
    Math.min(
      1,
      ((point.x - lineStart.x) * dx + (point.y - lineStart.y) * dy) / lengthSquared
    )
  );

  return {
    x: lineStart.x + t * dx,
    y: lineStart.y + t * dy,
  };
}

// ============================================================================
// Local Storage Helpers
// ============================================================================

/**
 * Save data to local storage with type safety
 */
export function saveToLocalStorage<T>(key: string, data: T): void {
  try {
    localStorage.setItem(key, JSON.stringify(data));
  } catch (error) {
    console.error('Failed to save to local storage:', error);
  }
}

/**
 * Load data from local storage with type safety
 */
export function loadFromLocalStorage<T>(key: string): T | null {
  try {
    const item = localStorage.getItem(key);
    return item ? (JSON.parse(item) as T) : null;
  } catch (error) {
    console.error('Failed to load from local storage:', error);
    return null;
  }
}

/**
 * Remove data from local storage
 */
export function removeFromLocalStorage(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch (error) {
    console.error('Failed to remove from local storage:', error);
  }
}

