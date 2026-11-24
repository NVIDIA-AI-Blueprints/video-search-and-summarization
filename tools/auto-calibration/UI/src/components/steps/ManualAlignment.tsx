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
 * Manual Alignment Step
 * Create alignment data by selecting corresponding points
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Grid,
  Button,
  Alert,
  LinearProgress,
  Paper,
  Chip,
  Stack,
  IconButton,
  Tooltip,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import DeleteIcon from '@mui/icons-material/Delete';
import UndoIcon from '@mui/icons-material/Undo';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import SaveIcon from '@mui/icons-material/Save';
import ZoomInIcon from '@mui/icons-material/ZoomIn';
import ZoomOutIcon from '@mui/icons-material/ZoomOut';
import ZoomOutMapIcon from '@mui/icons-material/ZoomOutMap';
import AddIcon from '@mui/icons-material/Add';
import VisibilityIcon from '@mui/icons-material/Visibility';
import EditIcon from '@mui/icons-material/Edit';
import { useApp } from '../../context/AppContext';
import { api } from '../../services/api';
import { MIN_CAMERAS_REQUIRED } from '../../constants';
import { alignmentDataFromJSON } from '../../utils/helpers';
import type { Project, AlignmentPointSet, AlignmentDataJSON, AlignmentDataJSONSingleCam } from '../../types';
import { ProjectState } from '../../types';

interface ManualAlignmentProps {
  project: Project;
  onProjectUpdate: (project: Project) => void;
}

type AlignmentToolMode = 'create' | 'view' | 'edit';

type AlignmentImage = 'cam0' | 'cam1' | 'bev';

/**
 * State captured before a user action so Undo can revert exactly that action.
 * Editing starts with an empty history, which is what keeps Undo from reaching back
 * into already-saved points the user has not touched in this session.
 */
interface AlignmentUndoEntry {
  pointSets: AlignmentPointSet[];
  currentSetIndex: number;
  currentImage: AlignmentImage;
}

const EMPTY_POINT_SETS: AlignmentPointSet[] = [
  { cam0: null, cam1: null, bev: null },
  { cam0: null, cam1: null, bev: null },
  { cam0: null, cam1: null, bev: null },
  { cam0: null, cam1: null, bev: null },
];

const ALIGNMENT_COLORS = ['#00ff00', '#0000ff', '#ff0000', '#ffff00']; // Green, Blue, Red, Yellow

const ManualAlignment: React.FC<ManualAlignmentProps> = ({
  project,
  onProjectUpdate,
}) => {
  const { showSuccess, showError } = useApp();
  const isSingleCam = project.video_files_count === 1; // 1 camera + 1 BEV (layout) only
  const [pointSets, setPointSets] = useState<AlignmentPointSet[]>([]);
  const [currentImage, setCurrentImage] = useState<AlignmentImage>('cam0');
  const [currentSetIndex, setCurrentSetIndex] = useState(0);
  const [undoStack, setUndoStack] = useState<AlignmentUndoEntry[]>([]);
  const [cam0FrameUrl, setCam0FrameUrl] = useState<string>('');
  const [cam1FrameUrl, setCam1FrameUrl] = useState<string>('');
  const [layoutImageUrl, setLayoutImageUrl] = useState<string>('');
  const [concatenatedImageUrl, setConcatenatedImageUrl] = useState<string>('');
  const [imageWidths, setImageWidths] = useState({ cam0: 0, cam1: 0, layout: 0, total: 0 });
  const [imageHeight, setImageHeight] = useState(0);
  const [loading, setLoading] = useState(false);
  const [createMode, setCreateMode] = useState(false);
  const [toolMode, setToolMode] = useState<AlignmentToolMode>('create');
  const [zoom, setZoom] = useState(1);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const alignmentLocked =
    project.project_state === ProjectState.RUNNING ||
    project.amc_state === 'RUNNING' ||
    project.vggt_state === 'RUNNING';
  const viewOnly = toolMode === 'view' || alignmentLocked;

  useEffect(() => {
    if (alignmentLocked && createMode && toolMode !== 'view') {
      setToolMode('view');
    }
  }, [alignmentLocked, createMode, toolMode]);

  useEffect(() => {
    // Initialize point sets if not already done
    if (pointSets.length === 0) {
      setPointSets([...EMPTY_POINT_SETS]);
    }
  }, []);

  // Load images when entering create mode
  useEffect(() => {
    if (createMode) {
      loadImages();
    }
  }, [createMode]);

  // Concatenate images when loaded: single-cam = cam0 + layout; multi-cam = cam0 + cam1 + layout
  useEffect(() => {
    if (isSingleCam && cam0FrameUrl && layoutImageUrl) {
      concatenateImagesSingleCam();
    } else if (!isSingleCam && cam0FrameUrl && cam1FrameUrl && layoutImageUrl) {
      concatenateImages();
    }
  }, [isSingleCam, cam0FrameUrl, cam1FrameUrl, layoutImageUrl]);

  const closeAlignmentTool = () => {
    setCreateMode(false);
    setToolMode('create');
    setZoom(1);
    setCurrentSetIndex(0);
    setCurrentImage('cam0');
    setUndoStack([]);
  };

  // Call before mutating points so Undo reverts this action and nothing else.
  const pushUndoSnapshot = () => {
    setUndoStack(previous => [...previous, { pointSets: [...pointSets], currentSetIndex, currentImage }]);
  };

  const openAlignmentTool = async (mode: AlignmentToolMode) => {
    if (mode !== 'view' && alignmentLocked) {
      showError('Calibration is running. Alignment cannot be changed.');
      return;
    }
    if (project.video_files_count < MIN_CAMERAS_REQUIRED) {
      showError(`At least ${MIN_CAMERAS_REQUIRED} video(s) required.`);
      return;
    }
    if (!project.layout_file) {
      showError('Layout image is required. Please upload layout in Step 2.');
      return;
    }

    try {
      setLoading(true);
      // A new session starts with no history; saved points are not undoable actions.
      setUndoStack([]);

      if (mode === 'create') {
        setPointSets([...EMPTY_POINT_SETS]);
        setCurrentSetIndex(0);
        setCurrentImage('cam0');
      } else {
        const json = await api.data.getAlignmentFile(project.project_id);
        const loaded = alignmentDataFromJSON(json);
        if (loaded.length === 0) {
          throw new Error('Alignment file is empty or invalid.');
        }

        const looksSingleCam = loaded.every((set) => set.cam1 === null);
        if (isSingleCam && !looksSingleCam) {
          throw new Error(
            'Alignment JSON looks multi-camera, but this project has a single camera.',
          );
        }
        if (!isSingleCam && looksSingleCam) {
          throw new Error(
            'Alignment JSON looks single-camera, but this project has multiple cameras.',
          );
        }

        setPointSets(loaded);
        setCurrentSetIndex(0);
        setCurrentImage('cam0');
      }

      setToolMode(mode);
      setCreateMode(true);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(
        apiError.detail || apiError.message || 'Failed to open alignment data',
      );
    } finally {
      setLoading(false);
    }
  };

  // Load images for alignment: single-cam = cam_00 + layout only; multi-cam = cam_00 + cam_01 + layout
  const loadImages = async () => {
    try {
      setLoading(true);
      if (project.video_files.length > 1) setCam1FrameUrl(''); // reset when switching to multi
      if (project.video_files.length > 0) {
        const cam0Blob = await api.data.getVideoFrame(project.project_id, 'cam_00.mp4', 0, true);
        setCam0FrameUrl(URL.createObjectURL(cam0Blob));
      }
      if (!isSingleCam && project.video_files.length > 1) {
        const cam1Blob = await api.data.getVideoFrame(project.project_id, 'cam_01.mp4', 0, true);
        setCam1FrameUrl(URL.createObjectURL(cam1Blob));
      }
      if (project.layout_file) {
        const layoutBlob = await api.data.getLayoutImage(project.project_id);
        setLayoutImageUrl(URL.createObjectURL(layoutBlob));
      }
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to load images';
      showError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  // Concatenate 3 images side-by-side into single canvas
  const concatenateImages = async () => {
    try {
      // Load images
      const cam0Img = new Image();
      const cam1Img = new Image();
      const layoutImg = new Image();

      await Promise.all([
        new Promise((resolve, reject) => {
          cam0Img.onload = resolve;
          cam0Img.onerror = reject;
          cam0Img.src = cam0FrameUrl;
        }),
        new Promise((resolve, reject) => {
          cam1Img.onload = resolve;
          cam1Img.onerror = reject;
          cam1Img.src = cam1FrameUrl;
        }),
        new Promise((resolve, reject) => {
          layoutImg.onload = resolve;
          layoutImg.onerror = reject;
          layoutImg.src = layoutImageUrl;
        }),
      ]);

      // Target height for all images
      const targetHeight = 500;

      // Calculate widths to maintain aspect ratio
      const cam0Width = Math.round((cam0Img.width / cam0Img.height) * targetHeight);
      const cam1Width = Math.round((cam1Img.width / cam1Img.height) * targetHeight);
      const layoutWidth = Math.round((layoutImg.width / layoutImg.height) * targetHeight);
      const totalWidth = cam0Width + cam1Width + layoutWidth;

      // Create canvas
      const canvas = document.createElement('canvas');
      canvas.width = totalWidth;
      canvas.height = targetHeight;
      const ctx = canvas.getContext('2d');
      
      if (!ctx) return;

      // Draw all 3 images side by side
      ctx.drawImage(cam0Img, 0, 0, cam0Width, targetHeight);
      ctx.drawImage(cam1Img, cam0Width, 0, cam1Width, targetHeight);
      ctx.drawImage(layoutImg, cam0Width + cam1Width, 0, layoutWidth, targetHeight);

      // Add labels
      ctx.font = 'bold 24px Arial';
      ctx.strokeStyle = 'black';
      ctx.lineWidth = 3;
      ctx.fillStyle = 'white';
      
      ctx.strokeText('Camera 0', 15, 35);
      ctx.fillText('Camera 0', 15, 35);
      
      ctx.strokeText('Camera 1', cam0Width + 15, 35);
      ctx.fillText('Camera 1', cam0Width + 15, 35);
      
      ctx.strokeText('Layout Map', cam0Width + cam1Width + 15, 35);
      ctx.fillText('Layout Map', cam0Width + cam1Width + 15, 35);

      // Add vertical dividers
      ctx.strokeStyle = 'white';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cam0Width, 0);
      ctx.lineTo(cam0Width, targetHeight);
      ctx.stroke();
      
      ctx.beginPath();
      ctx.moveTo(cam0Width + cam1Width, 0);
      ctx.lineTo(cam0Width + cam1Width, targetHeight);
      ctx.stroke();

      // Convert to blob URL
      canvas.toBlob((blob) => {
        if (blob) {
          setConcatenatedImageUrl(URL.createObjectURL(blob));
        }
      });

      // Store image widths and original dimensions for coordinate conversion
      setImageWidths({ 
        cam0: cam0Width, 
        cam1: cam1Width, 
        layout: layoutWidth,
        total: totalWidth
      });
      setImageHeight(targetHeight);

      // Store original dimensions for pixel conversion
      (window as any).alignmentImageMeta = {
        cam0: { width: cam0Img.width, height: cam0Img.height, displayWidth: cam0Width, displayHeight: targetHeight },
        cam1: { width: cam1Img.width, height: cam1Img.height, displayWidth: cam1Width, displayHeight: targetHeight },
        layout: { width: layoutImg.width, height: layoutImg.height, displayWidth: layoutWidth, displayHeight: targetHeight },
      };

    } catch (error) {
      console.error('Failed to concatenate images:', error);
      showError('Failed to create alignment canvas');
    }
  };

  // Single-camera: concatenate only Camera + Layout (2 panels: 1 camera, 1 BEV)
  const concatenateImagesSingleCam = async () => {
    try {
      const cam0Img = new Image();
      const layoutImg = new Image();
      await Promise.all([
        new Promise<void>((resolve, reject) => {
          cam0Img.onload = () => resolve();
          cam0Img.onerror = reject;
          cam0Img.src = cam0FrameUrl;
        }),
        new Promise<void>((resolve, reject) => {
          layoutImg.onload = () => resolve();
          layoutImg.onerror = reject;
          layoutImg.src = layoutImageUrl;
        }),
      ]);
      const targetHeight = 500;
      const cam0Width = Math.round((cam0Img.width / cam0Img.height) * targetHeight);
      const layoutWidth = Math.round((layoutImg.width / layoutImg.height) * targetHeight);
      const totalWidth = cam0Width + layoutWidth;
      const canvas = document.createElement('canvas');
      canvas.width = totalWidth;
      canvas.height = targetHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.drawImage(cam0Img, 0, 0, cam0Width, targetHeight);
      ctx.drawImage(layoutImg, cam0Width, 0, layoutWidth, targetHeight);
      ctx.font = 'bold 24px Arial';
      ctx.strokeStyle = 'black';
      ctx.lineWidth = 3;
      ctx.fillStyle = 'white';
      ctx.strokeText('Camera', 15, 35);
      ctx.fillText('Camera', 15, 35);
      ctx.strokeText('Layout Map', cam0Width + 15, 35);
      ctx.fillText('Layout Map', cam0Width + 15, 35);
      ctx.strokeStyle = 'white';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cam0Width, 0);
      ctx.lineTo(cam0Width, targetHeight);
      ctx.stroke();
      canvas.toBlob((blob) => {
        if (blob) setConcatenatedImageUrl(URL.createObjectURL(blob));
      });
      setImageWidths({ cam0: cam0Width, cam1: 0, layout: layoutWidth, total: totalWidth });
      setImageHeight(targetHeight);
      (window as any).alignmentImageMeta = {
        cam0: { width: cam0Img.width, height: cam0Img.height, displayWidth: cam0Width, displayHeight: targetHeight },
        cam1: { width: 0, height: 0, displayWidth: 0, displayHeight: targetHeight },
        layout: { width: layoutImg.width, height: layoutImg.height, displayWidth: layoutWidth, displayHeight: targetHeight },
      };
    } catch (error) {
      console.error('Failed to create single-camera alignment canvas:', error);
      showError('Failed to create alignment canvas');
    }
  };

  // Handle image click (single-cam: cam0 -> bev only; multi-cam: cam0 -> cam1 -> bev)
  const handleImageClick = (image: AlignmentImage, x: number, y: number) => {
    if (viewOnly) return;
    const currentLabel = currentImage === 'cam0' ? (isSingleCam ? 'Camera' : 'Camera 0') : currentImage === 'cam1' ? 'Camera 1' : 'Layout Map';
    if (image !== currentImage) {
      showError(`Please click on ${currentLabel} first`);
      return;
    }

    pushUndoSnapshot();

    const newPointSets = [...pointSets];
    const currentSet = { ...newPointSets[currentSetIndex] };
    
    currentSet[image] = { x, y };
    newPointSets[currentSetIndex] = currentSet;
    setPointSets(newPointSets);

    // Move to next image: single-cam = cam0 -> bev; multi-cam = cam0 -> cam1 -> bev
    if (image === 'cam0') {
      setCurrentImage(isSingleCam ? 'bev' : 'cam1');
    } else if (image === 'cam1') {
      setCurrentImage('bev');
    } else if (image === 'bev') {
      // Move to next set
      if (currentSetIndex < pointSets.length - 1) {
        setCurrentSetIndex(currentSetIndex + 1);
        setCurrentImage('cam0');
      } else {
        // Current set complete, ready to add more or save
        setCurrentImage('cam0');
        const totalComplete = isSingleCam
          ? pointSets.filter(s => s.cam0 && s.bev).length
          : pointSets.filter(s => s.cam0 && s.cam1 && s.bev).length;
        if (totalComplete >= 4) {
          showSuccess(`${totalComplete} point sets complete! You can add more or save the alignment data.`);
        } else {
          showSuccess(`Set ${currentSetIndex + 1} complete. Continue to complete at least 4 sets.`);
        }
      }
    }
  };

  /**
   * Revert the most recent action of this session. Scanning the point sets for the last
   * filled slot instead would delete already-saved points in edit mode, where every slot
   * is populated before the user touches anything.
   */
  const handleUndo = () => {
    if (viewOnly) return;
    const previous = undoStack[undoStack.length - 1];
    if (!previous) {
      showError('Nothing to undo');
      return;
    }

    setPointSets(previous.pointSets);
    setCurrentSetIndex(previous.currentSetIndex);
    setCurrentImage(previous.currentImage);
    setUndoStack(stack => stack.slice(0, -1));
  };

  // Reset all points
  const handleReset = () => {
    if (viewOnly) return;
    pushUndoSnapshot();
    setPointSets([...EMPTY_POINT_SETS]);
    setCurrentSetIndex(0);
    setCurrentImage('cam0');
    showSuccess('All points reset');
  };

  // Convert to backend JSON format: single-cam = [cam, bev] per set; multi-cam = [cam0, cam1, bev]
  const convertToJSON = (): AlignmentDataJSON | AlignmentDataJSONSingleCam => {
    if (isSingleCam) {
      return pointSets
        .filter(set => set.cam0 && set.bev)
        .map(set => [[set.cam0!.x, set.cam0!.y], [set.bev!.x, set.bev!.y]]) as AlignmentDataJSONSingleCam;
    }
    return pointSets
      .filter(set => set.cam0 && set.cam1 && set.bev)
      .map(set => [
        [set.cam0!.x, set.cam0!.y],
        [set.cam1!.x, set.cam1!.y],
        [set.bev!.x, set.bev!.y],
      ]) as AlignmentDataJSON;
  };

  // Check if minimum required points are complete (4 sets; single-cam = cam+bev, multi = cam0+cam1+bev)
  const isComplete = () => {
    const completeSets = isSingleCam
      ? pointSets.filter(set => set.cam0 && set.bev).length
      : pointSets.filter(set => set.cam0 && set.cam1 && set.bev).length;
    return completeSets >= 4;
  };

  // Add new point set
  const handleAddPointSet = () => {
    if (viewOnly) return;
    pushUndoSnapshot();
    setPointSets([...pointSets, { cam0: null, cam1: null, bev: null }]);
    setCurrentSetIndex(pointSets.length);
    setCurrentImage('cam0');
    showSuccess('New point set added - continue selecting points');
  };

  // Save alignment data
  const handleSaveAlignment = async () => {
    if (alignmentLocked) {
      showError('Calibration is running. Alignment cannot be changed.');
      return;
    }
    if (toolMode === 'view') {
      return;
    }
    const completedSets = getCompletedSets();
    if (completedSets < 4) {
      showError('Please complete at least 4 point sets before saving');
      return;
    }

    try {
      setLoading(true);
      
      const alignmentData = convertToJSON();
      const blob = new Blob([JSON.stringify(alignmentData, null, 2)], { type: 'application/json' });
      const file = new File([blob], 'alignment_data.json', { type: 'application/json' });
      
      const response = await api.data.uploadAlignmentFile(project.project_id, file, 'rectified');
      showSuccess(response.message || 'Alignment data saved successfully');
      
      // Refresh project and exit create mode
      await refreshProject();
      closeAlignmentTool();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to save alignment data';
      showError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  // Upload alignment file
  const handleAlignmentUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (alignmentLocked) {
      showError('Calibration is running. Alignment upload is not allowed.');
      event.target.value = '';
      return;
    }
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.json')) {
      showError('Alignment file must be a JSON file');
      return;
    }

    try {
      setLoading(true);
      await api.data.uploadAlignmentFile(project.project_id, file, 'original');
      showSuccess('Alignment file uploaded successfully');
      await refreshProject();
      // Open for visual inspection using the uploaded reference
      await openAlignmentTool('view');
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to upload alignment';
      showError(errorMessage);
    } finally {
      setLoading(false);
      event.target.value = '';
    }
  };

  // Delete alignment
  const handleDeleteAlignment = async () => {
    if (alignmentLocked) {
      showError('Calibration is running. Alignment deletion is not allowed.');
      return;
    }
    try {
      await api.data.deleteAlignmentFile(project.project_id);
      
      // Reset ALL state to prevent NaN errors
      setCam0FrameUrl('');
      setCam1FrameUrl('');
      setLayoutImageUrl('');
      setConcatenatedImageUrl('');
      setImageWidths({ cam0: 0, cam1: 0, layout: 0, total: 0 });
      setImageHeight(0);
      
      // Reset to minimum 4 point sets (user can add more with "Add More Points" button)
      setPointSets([...EMPTY_POINT_SETS]);
      
      setCurrentSetIndex(0);
      setCurrentImage('cam0');
      setZoom(1);
      closeAlignmentTool();
      
      showSuccess('Alignment file deleted successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to delete alignment';
      showError(errorMessage);
    }
  };

  // Refresh project
  const refreshProject = async () => {
    try {
      const response = await api.project.getProjectInfo(project.project_id);
      onProjectUpdate(response.project_info);
    } catch (error: unknown) {
      console.error('Failed to refresh project:', error);
    }
  };

  // Videos can land while this step is unmounted (notably RTSP ingest), so the cached
  // video count would otherwise keep the "upload videos in Step 2" gate up.
  useEffect(() => {
    void refreshProject();
  }, [project.project_id]);

  // Get progress percentage (single-cam: 2 points per set; multi-cam: 3 per set)
  const getProgress = () => {
    let completed = 0;
    const pointsPerSet = isSingleCam ? 2 : 3;
    pointSets.forEach(set => {
      if (set.cam0) completed++;
      if (!isSingleCam && set.cam1) completed++;
      if (set.bev) completed++;
    });
    const total = pointSets.length * pointsPerSet;
    return total === 0 ? 0 : (completed / total) * 100;
  };

  // Get completed sets count (single-cam: cam+bev; multi-cam: cam0+cam1+bev)
  const getCompletedSets = () => {
    return isSingleCam
      ? pointSets.filter(set => set.cam0 && set.bev).length
      : pointSets.filter(set => set.cam0 && set.cam1 && set.bev).length;
  };

  // Zoom controls
  const handleZoomIn = () => {
    setZoom(prev => Math.min(prev + 0.25, 3));
  };

  const handleZoomOut = () => {
    setZoom(prev => Math.max(prev - 0.25, 0.5));
  };

  const handleResetZoom = () => {
    setZoom(1);
  };

  // Handle canvas click
  const handleCanvasClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (viewOnly) return;
    const canvas = canvasRef.current;
    if (!canvas || !concatenatedImageUrl) return;

    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    // Convert to unzoomed coordinates
    const x = clickX / zoom;
    const y = clickY / zoom;

    // Ignore clicks outside the canvas (e.g. dark area to the right of Layout Map in single-cam)
    if (x < 0 || x > imageWidths.total || y < 0 || y > imageHeight) {
      return;
    }

    const meta = (window as any).alignmentImageMeta;
    if (!meta) return;

    let targetImage: 'cam0' | 'cam1' | 'bev';
    let imageX = 0;
    let imageY = 0;

    if (isSingleCam) {
      // Single-cam: only 2 regions — Camera (left) and Layout Map (right)
      if (x < imageWidths.cam0) {
        targetImage = 'cam0';
        const scaleX = meta.cam0.width / meta.cam0.displayWidth;
        const scaleY = meta.cam0.height / meta.cam0.displayHeight;
        imageX = x * scaleX;
        imageY = y * scaleY;
      } else {
        targetImage = 'bev';
        const localX = x - imageWidths.cam0;
        const scaleX = meta.layout.width / meta.layout.displayWidth;
        const scaleY = meta.layout.height / meta.layout.displayHeight;
        imageX = localX * scaleX;
        imageY = y * scaleY;
      }
    } else {
      if (x < imageWidths.cam0) {
        targetImage = 'cam0';
        const scaleX = meta.cam0.width / meta.cam0.displayWidth;
        const scaleY = meta.cam0.height / meta.cam0.displayHeight;
        imageX = x * scaleX;
        imageY = y * scaleY;
      } else if (x < imageWidths.cam0 + imageWidths.cam1) {
        targetImage = 'cam1';
        const localX = x - imageWidths.cam0;
        const scaleX = meta.cam1.width / meta.cam1.displayWidth;
        const scaleY = meta.cam1.height / meta.cam1.displayHeight;
        imageX = localX * scaleX;
        imageY = y * scaleY;
      } else {
        targetImage = 'bev';
        const localX = x - imageWidths.cam0 - imageWidths.cam1;
        const scaleX = meta.layout.width / meta.layout.displayWidth;
        const scaleY = meta.layout.height / meta.layout.displayHeight;
        imageX = localX * scaleX;
        imageY = y * scaleY;
      }
    }

    const currentLabel = currentImage === 'cam0' ? (isSingleCam ? 'Camera' : 'Camera 0') : currentImage === 'cam1' ? 'Camera 1' : 'Layout Map';
    if (targetImage !== currentImage) {
      showError(`Please click on ${currentLabel}`);
      return;
    }

    handleImageClick(targetImage, imageX, imageY);
  };

  return (
    <Box>
      {/* Header */}
      <Typography variant="h5" gutterBottom>
        Manual Alignment
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Project: <strong>{project.project_name}</strong>
      </Typography>

      {alignmentLocked && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          Calibration is running. Manual alignment is read-only until it finishes.
        </Alert>
      )}

      {/* Alignment Status */}
      <Paper sx={{ p: 2, mb: 3, bgcolor: 'background.paper' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Typography variant="subtitle2">
            Alignment Status:
          </Typography>
          {project.alignment_file ? (
            <Chip
              icon={<CheckCircleIcon />}
              label="Alignment data exists"
              color="success"
              size="small"
            />
          ) : (
            <Chip
              label="No alignment data"
              color="default"
              size="small"
            />
          )}
        </Box>
      </Paper>

      {/* Alignment Already Exists */}
      {project.alignment_file && !createMode ? (
        <Card>
          <CardContent>
            <Alert severity="success" sx={{ mb: 2 }}>
              Alignment data already exists. View it for visual inspection, then edit
              points from the viewer if needed, or delete and start over.
            </Alert>

            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <Button
                variant="contained"
                startIcon={<VisibilityIcon />}
                onClick={() => openAlignmentTool('view')}
                disabled={loading || project.video_files_count < MIN_CAMERAS_REQUIRED || !project.layout_file}
              >
                View Alignment
              </Button>
              <Button
                variant="outlined"
                color="error"
                startIcon={<DeleteIcon />}
                onClick={handleDeleteAlignment}
                disabled={alignmentLocked || loading}
              >
                Delete Alignment Data
              </Button>
            </Stack>
          </CardContent>
        </Card>
      ) : (
        /* Create/Upload Alignment */
        <Box>
          {/* Prerequisites Check: 1+ video and layout (single-cam = 1 camera + BEV; multi = 2 cameras + BEV) */}
          {project.video_files_count < MIN_CAMERAS_REQUIRED ? (
            <Alert severity="warning">
              At least {MIN_CAMERAS_REQUIRED} video(s) required. Please upload videos in Step 2.
            </Alert>
          ) : !project.layout_file ? (
            <Alert severity="warning">
              Layout image is required. Please upload layout in Step 2.
            </Alert>
          ) : (
            <>
              {/* Upload or Create Options */}
              {!createMode ? (
                <Grid container spacing={3}>
                  <Grid item xs={12} md={6}>
                    <Card>
                      <CardContent>
                        <Typography variant="h6" gutterBottom>
                          Upload Existing Alignment
                        </Typography>
                        <Typography variant="body2" color="text.secondary" paragraph>
                          If you already have an alignment JSON from the CLI / original
                          (pre-rectification) frames, upload it here. It is converted to
                          rectified coordinates when linear media is ready. You can inspect
                          and edit after upload.
                        </Typography>
                        <Button
                          component="label"
                          variant="contained"
                          startIcon={<CloudUploadIcon />}
                          disabled={loading || alignmentLocked}
                          fullWidth
                        >
                          Upload alignment_data.json
                          <input
                            type="file"
                            hidden
                            accept=".json"
                            onChange={handleAlignmentUpload}
                          />
                        </Button>
                      </CardContent>
                    </Card>
                  </Grid>

                  <Grid item xs={12} md={6}>
                    <Card>
                      <CardContent>
                        <Typography variant="h6" gutterBottom>
                          Create Alignment Interactively
                        </Typography>
                        <Typography variant="body2" color="text.secondary" paragraph>
                          {isSingleCam
                            ? 'Select 4 corresponding points on Camera and Layout Map (1 camera + BEV).'
                            : 'Select 4 corresponding points across Camera 0, Camera 1, and Layout images.'}
                        </Typography>
                        <Button
                          variant="contained"
                          onClick={() => openAlignmentTool('create')}
                          disabled={alignmentLocked || loading}
                          fullWidth
                        >
                          Open Alignment Tool
                        </Button>
                      </CardContent>
                    </Card>
                  </Grid>
                </Grid>
              ) : (
                /* Interactive Alignment Tool */
                <Box>
                  {/* Progress */}
                  <Card sx={{ mb: 3 }}>
                    <CardContent>
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                        <Typography variant="h6">
                          {toolMode === 'view'
                            ? `Viewing alignment: ${getCompletedSets()} / ${pointSets.length} sets`
                            : `Progress: ${getCompletedSets()} / ${pointSets.length} sets${
                                getCompletedSets() >= 4 ? ' (Ready to save)' : ' (Min 4 required)'
                              }`}
                        </Typography>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Chip
                            label={
                              toolMode === 'view'
                                ? 'View only'
                                : toolMode === 'edit'
                                  ? 'Editing'
                                  : 'Creating'
                            }
                            color={toolMode === 'view' ? 'default' : 'primary'}
                            size="small"
                          />
                          <Chip
                            label={`${Math.round(getProgress())}% Complete`}
                            color={isComplete() ? 'success' : 'primary'}
                          />
                        </Stack>
                      </Box>
                      <LinearProgress variant="determinate" value={getProgress()} />
                      {!viewOnly && (
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                          Click on: <strong>
                            {currentImage === 'cam0' ? (isSingleCam ? 'Camera' : 'Camera 0') : currentImage === 'cam1' ? 'Camera 1' : 'Layout Map'}
                          </strong> (Point set {currentSetIndex + 1})
                        </Typography>
                      )}
                    </CardContent>
                  </Card>

                  {/* Instructions */}
                  <Alert severity="info" sx={{ mb: 3 }}>
                    <Typography variant="body2">
                      {viewOnly ? (
                        <>
                          <strong>View mode:</strong> Existing alignment points are shown on the
                          camera and layout images. Zoom to inspect. Use Edit Alignment to modify
                          points{toolMode === 'view' && !alignmentLocked ? ', or Close to return.' : '.'}
                        </>
                      ) : isSingleCam ? (
                        <>
                          <strong>How to use:</strong> Click the same physical point on Camera (left) then Layout Map (right). Repeat for 4 point sets. Choose points on the ground plane visible in both images.
                        </>
                      ) : (
                        <>
                          <strong>How to use:</strong> Click the same physical point sequentially on Camera 0 (left), Camera 1 (center), and Layout Map (right). Repeat for at least 4 different points. Choose points on the ground plane that are visible in all 3 images.
                        </>
                      )}
                      <br />
                      <strong>Zoom:</strong> Use buttons below to zoom. Scroll within canvas when zoomed to navigate.
                    </Typography>
                  </Alert>

                  {/* Zoom Controls */}
                  <Card sx={{ mb: 3 }}>
                    <CardContent>
                      <Stack direction="row" spacing={2} alignItems="center" justifyContent="center">
                        <Tooltip title="Zoom Out">
                          <span>
                            <IconButton onClick={handleZoomOut} disabled={zoom <= 0.5} color="primary">
                              <ZoomOutIcon />
                            </IconButton>
                          </span>
                        </Tooltip>
                        <Chip label={`Zoom: ${Math.round(zoom * 100)}%`} color="primary" />
                        <Tooltip title="Zoom In">
                          <span>
                            <IconButton onClick={handleZoomIn} disabled={zoom >= 3} color="primary">
                              <ZoomInIcon />
                            </IconButton>
                          </span>
                        </Tooltip>
                        <Tooltip title="Reset Zoom">
                          <Button variant="outlined" size="small" startIcon={<ZoomOutMapIcon />} onClick={handleResetZoom}>
                            Reset (100%)
                          </Button>
                        </Tooltip>
                      </Stack>
                    </CardContent>
                  </Card>

                  {/* Single Concatenated Canvas */}
                  <Card sx={{ mb: 3 }}>
                    <CardContent>
                      <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                        {viewOnly
                          ? 'Existing alignment points overlaid on camera and layout images'
                          : `Click on ${
                              currentImage === 'cam0'
                                ? isSingleCam
                                  ? 'Camera (left)'
                                  : 'Camera 0 (left)'
                                : currentImage === 'cam1'
                                  ? 'Camera 1 (center)'
                                  : 'Layout Map (right)'
                            } to place point`}
                      </Typography>
                      <Paper
                        sx={{
                          bgcolor: 'grey.900',
                          display: 'flex',
                          alignItems: 'flex-start',
                          justifyContent: 'flex-start',
                          position: 'relative',
                          cursor: viewOnly ? 'default' : 'crosshair',
                          border: '2px solid',
                          borderColor: 'grey.600',
                          overflow: 'auto',
                          maxHeight: 700,
                        }}
                        onClick={viewOnly ? undefined : handleCanvasClick}
                      >
                        {loading ? (
                          <Box sx={{ width: '100%', p: 4, textAlign: 'center' }}>
                            <Typography color="grey.400">Loading images...</Typography>
                          </Box>
                        ) : concatenatedImageUrl ? (
                          <Box sx={{ position: 'relative', display: 'inline-block' }}>
                            <canvas
                              ref={canvasRef}
                              style={{
                                width: `${imageWidths.total * zoom}px`,
                                height: `${imageHeight * zoom}px`,
                                display: 'block',
                                imageRendering: 'pixelated',
                              }}
                              width={imageWidths.total}
                              height={imageHeight}
                            />
                            <img
                              src={concatenatedImageUrl}
                              alt="Concatenated"
                              style={{ display: 'none' }}
                              onLoad={(e) => {
                                const canvas = canvasRef.current;
                                if (canvas) {
                                  const ctx = canvas.getContext('2d');
                                  if (ctx) {
                                    ctx.drawImage(e.currentTarget, 0, 0);
                                  }
                                }
                              }}
                            />
                            {/* Draw all points */}
                            <svg 
                              style={{
                                position: 'absolute',
                                top: 0,
                                left: 0,
                                width: `${imageWidths.total * zoom}px`,
                                height: `${imageHeight * zoom}px`,
                                pointerEvents: 'none',
                              }}
                            >
                              {pointSets.map((set, idx) => {
                                const points: Array<{x: number, y: number, region: 'cam0' | 'cam1' | 'bev'}> = [];
                                const meta = (window as any).alignmentImageMeta;
                                if (!meta) return null;

                                // Convert all points to display coordinates
                                if (set.cam0) {
                                  const displayX = (set.cam0.x / meta.cam0.width) * imageWidths.cam0 * zoom;
                                  const displayY = (set.cam0.y / meta.cam0.height) * imageHeight * zoom;
                                  points.push({ x: displayX, y: displayY, region: 'cam0' });
                                }
                                if (set.cam1) {
                                  const displayX = imageWidths.cam0 * zoom + (set.cam1.x / meta.cam1.width) * imageWidths.cam1 * zoom;
                                  const displayY = (set.cam1.y / meta.cam1.height) * imageHeight * zoom;
                                  points.push({ x: displayX, y: displayY, region: 'cam1' });
                                }
                                if (set.bev) {
                                  const displayX = (imageWidths.cam0 + imageWidths.cam1) * zoom + (set.bev.x / meta.layout.width) * imageWidths.layout * zoom;
                                  const displayY = (set.bev.y / meta.layout.height) * imageHeight * zoom;
                                  points.push({ x: displayX, y: displayY, region: 'bev' });
                                }

                                return points.map((pt, ptIdx) => (
                                  <g key={`${idx}-${ptIdx}`}>
                                    <circle cx={pt.x} cy={pt.y} r={8} fill={ALIGNMENT_COLORS[idx % ALIGNMENT_COLORS.length]} stroke="white" strokeWidth={2} />
                                    <text x={pt.x + 12} y={pt.y - 12} fill="white" fontSize="16" fontWeight="bold" stroke="black" strokeWidth={1}>{idx + 1}</text>
                                  </g>
                                ));
                              })}
                            </svg>
                          </Box>
                        ) : (
                          <Box sx={{ width: '100%', p: 4, textAlign: 'center' }}>
                            <Typography color="grey.400">Preparing canvas...</Typography>
                          </Box>
                        )}
                      </Paper>
                    </CardContent>
                  </Card>

                  {/* OLD 3-Column Grid - REMOVE EVERYTHING BELOW UNTIL Action Buttons */}
                  <div style={{ display: 'none' }}>
                  <Grid container spacing={2}>
                    {/* Camera 0 */}
                    <Grid item xs={12} md={4}>
                      <Card>
                        <CardContent>
                          <Typography variant="h6" gutterBottom>
                            Camera 0
                          </Typography>
                          <Paper
                            sx={{
                              height: 300,
                              bgcolor: 'action.hover',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              position: 'relative',
                              cursor: currentImage === 'cam0' ? 'crosshair' : 'default',
                              border: currentImage === 'cam0' ? '3px solid' : '1px solid',
                              borderColor: currentImage === 'cam0' ? 'primary.main' : 'grey.400',
                              overflow: 'hidden',
                            }}
                            onClick={(e) => {
                              if (!cam0FrameUrl) return;
                              
                              const container = e.currentTarget;
                              const img = container.querySelector('img');
                              if (!img) return;
                              
                              // Get actual rendered image dimensions
                              const imgRect = img.getBoundingClientRect();
                              
                              // Calculate click position relative to image (not container)
                              const x = e.clientX - imgRect.left;
                              const y = e.clientY - imgRect.top;
                              
                              // Scale to original image dimensions
                              const scaleX = img.naturalWidth / imgRect.width;
                              const scaleY = img.naturalHeight / imgRect.height;
                              
                              const imageX = x * scaleX;
                              const imageY = y * scaleY;
                              
                              handleImageClick('cam0', imageX, imageY);
                            }}
                          >
                            {cam0FrameUrl ? (
                              <>
                                <img
                                  id="cam0-img"
                                  src={cam0FrameUrl}
                                  alt="Camera 0"
                                  style={{
                                    maxWidth: '100%',
                                    maxHeight: '100%',
                                    objectFit: 'contain',
                                  }}
                                />
                                {/* Draw points */}
                                <svg 
                                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                                  ref={(svg) => {
                                    if (!svg) return;
                                    const img = svg.parentElement?.querySelector('img');
                                    if (!img) return;
                                    
                                    // Position SVG to match image
                                    const containerRect = svg.parentElement?.getBoundingClientRect();
                                    const imgRect = img.getBoundingClientRect();
                                    
                                    if (containerRect && imgRect) {
                                      const offsetX = imgRect.left - containerRect.left;
                                      const offsetY = imgRect.top - containerRect.top;
                                      svg.style.left = `${offsetX}px`;
                                      svg.style.top = `${offsetY}px`;
                                      svg.style.width = `${imgRect.width}px`;
                                      svg.style.height = `${imgRect.height}px`;
                                    }
                                  }}
                                >
                                  {pointSets.map((set, idx) => {
                                    if (!set.cam0) return null;
                                    const img = document.querySelector('#cam0-img') as HTMLImageElement;
                                    if (!img) return null;
                                    
                                    const imgRect = img.getBoundingClientRect();
                                    const scaleX = imgRect.width / img.naturalWidth;
                                    const scaleY = imgRect.height / img.naturalHeight;
                                    
                                    const displayX = set.cam0.x * scaleX;
                                    const displayY = set.cam0.y * scaleY;
                                    
                                    return (
                                      <g key={idx}>
                                        <circle cx={displayX} cy={displayY} r={6} fill={ALIGNMENT_COLORS[idx % ALIGNMENT_COLORS.length]} stroke="white" strokeWidth={2} />
                                        <text x={displayX + 10} y={displayY - 10} fill="white" fontSize="14" fontWeight="bold" stroke="black" strokeWidth={0.5}>{idx + 1}</text>
                                      </g>
                                    );
                                  })}
                                </svg>
                              </>
                            ) : (
                              <Typography variant="body2" color="text.secondary">
                                {loading ? 'Loading...' : 'Camera 0 frame'}
                              </Typography>
                            )}
                          </Paper>
                        </CardContent>
                      </Card>
                    </Grid>

                    {/* Camera 1 */}
                    <Grid item xs={12} md={4}>
                      <Card>
                        <CardContent>
                          <Typography variant="h6" gutterBottom>
                            Camera 1
                          </Typography>
                          <Paper
                            sx={{
                              height: 300,
                              bgcolor: 'action.hover',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              position: 'relative',
                              cursor: currentImage === 'cam1' ? 'crosshair' : 'default',
                              border: currentImage === 'cam1' ? '3px solid' : '1px solid',
                              borderColor: currentImage === 'cam1' ? 'primary.main' : 'grey.400',
                              overflow: 'hidden',
                            }}
                            onClick={(e) => {
                              if (!cam1FrameUrl) return;
                              
                              const container = e.currentTarget;
                              const img = container.querySelector('img');
                              if (!img) return;
                              
                              // Get actual rendered image dimensions
                              const imgRect = img.getBoundingClientRect();
                              
                              // Calculate click position relative to image (not container)
                              const x = e.clientX - imgRect.left;
                              const y = e.clientY - imgRect.top;
                              
                              // Scale to original image dimensions
                              const scaleX = img.naturalWidth / imgRect.width;
                              const scaleY = img.naturalHeight / imgRect.height;
                              
                              const imageX = x * scaleX;
                              const imageY = y * scaleY;
                              
                              handleImageClick('cam1', imageX, imageY);
                            }}
                          >
                            {cam1FrameUrl ? (
                              <>
                                <img
                                  id="cam1-img"
                                  src={cam1FrameUrl}
                                  alt="Camera 1"
                                  style={{
                                    maxWidth: '100%',
                                    maxHeight: '100%',
                                    objectFit: 'contain',
                                  }}
                                />
                                {/* Draw points */}
                                <svg 
                                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                                  ref={(svg) => {
                                    if (!svg) return;
                                    const img = svg.parentElement?.querySelector('img');
                                    if (!img) return;
                                    
                                    // Position SVG to match image
                                    const containerRect = svg.parentElement?.getBoundingClientRect();
                                    const imgRect = img.getBoundingClientRect();
                                    
                                    if (containerRect && imgRect) {
                                      const offsetX = imgRect.left - containerRect.left;
                                      const offsetY = imgRect.top - containerRect.top;
                                      svg.style.left = `${offsetX}px`;
                                      svg.style.top = `${offsetY}px`;
                                      svg.style.width = `${imgRect.width}px`;
                                      svg.style.height = `${imgRect.height}px`;
                                    }
                                  }}
                                >
                                  {pointSets.map((set, idx) => {
                                    if (!set.cam1) return null;
                                    const img = document.querySelector('#cam1-img') as HTMLImageElement;
                                    if (!img) return null;
                                    
                                    const imgRect = img.getBoundingClientRect();
                                    const scaleX = imgRect.width / img.naturalWidth;
                                    const scaleY = imgRect.height / img.naturalHeight;
                                    
                                    const displayX = set.cam1.x * scaleX;
                                    const displayY = set.cam1.y * scaleY;
                                    
                                    return (
                                      <g key={idx}>
                                        <circle cx={displayX} cy={displayY} r={6} fill={ALIGNMENT_COLORS[idx % ALIGNMENT_COLORS.length]} stroke="white" strokeWidth={2} />
                                        <text x={displayX + 10} y={displayY - 10} fill="white" fontSize="14" fontWeight="bold" stroke="black" strokeWidth={0.5}>{idx + 1}</text>
                                      </g>
                                    );
                                  })}
                                </svg>
                              </>
                            ) : (
                              <Typography variant="body2" color="text.secondary">
                                {loading ? 'Loading...' : 'Camera 1 frame'}
                              </Typography>
                            )}
                          </Paper>
                        </CardContent>
                      </Card>
                    </Grid>

                    {/* Layout Map */}
                    <Grid item xs={12} md={4}>
                      <Card>
                        <CardContent>
                          <Typography variant="h6" gutterBottom>
                            Layout Map (BEV)
                          </Typography>
                          <Paper
                            sx={{
                              height: 300,
                              bgcolor: 'action.hover',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              position: 'relative',
                              cursor: currentImage === 'bev' ? 'crosshair' : 'default',
                              border: currentImage === 'bev' ? '3px solid' : '1px solid',
                              borderColor: currentImage === 'bev' ? 'primary.main' : 'grey.400',
                              overflow: 'hidden',
                            }}
                            onClick={(e) => {
                              if (!layoutImageUrl) return;
                              
                              const container = e.currentTarget;
                              const img = container.querySelector('img');
                              if (!img) return;
                              
                              // Get actual rendered image dimensions
                              const imgRect = img.getBoundingClientRect();
                              
                              // Calculate click position relative to image (not container)
                              const x = e.clientX - imgRect.left;
                              const y = e.clientY - imgRect.top;
                              
                              // Scale to original image dimensions
                              const scaleX = img.naturalWidth / imgRect.width;
                              const scaleY = img.naturalHeight / imgRect.height;
                              
                              const imageX = x * scaleX;
                              const imageY = y * scaleY;
                              
                              handleImageClick('bev', imageX, imageY);
                            }}
                          >
                            {layoutImageUrl ? (
                              <>
                                <img
                                  id="layout-img"
                                  src={layoutImageUrl}
                                  alt="Layout Map"
                                  style={{
                                    maxWidth: '100%',
                                    maxHeight: '100%',
                                    objectFit: 'contain',
                                  }}
                                />
                                {/* Draw points */}
                                <svg 
                                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
                                  ref={(svg) => {
                                    if (!svg) return;
                                    const img = svg.parentElement?.querySelector('img');
                                    if (!img) return;
                                    
                                    // Position SVG to match image
                                    const containerRect = svg.parentElement?.getBoundingClientRect();
                                    const imgRect = img.getBoundingClientRect();
                                    
                                    if (containerRect && imgRect) {
                                      const offsetX = imgRect.left - containerRect.left;
                                      const offsetY = imgRect.top - containerRect.top;
                                      svg.style.left = `${offsetX}px`;
                                      svg.style.top = `${offsetY}px`;
                                      svg.style.width = `${imgRect.width}px`;
                                      svg.style.height = `${imgRect.height}px`;
                                    }
                                  }}
                                >
                                  {pointSets.map((set, idx) => {
                                    if (!set.bev) return null;
                                    const img = document.querySelector('#layout-img') as HTMLImageElement;
                                    if (!img) return null;
                                    
                                    const imgRect = img.getBoundingClientRect();
                                    const scaleX = imgRect.width / img.naturalWidth;
                                    const scaleY = imgRect.height / img.naturalHeight;
                                    
                                    const displayX = set.bev.x * scaleX;
                                    const displayY = set.bev.y * scaleY;
                                    
                                    return (
                                      <g key={idx}>
                                        <circle cx={displayX} cy={displayY} r={6} fill={ALIGNMENT_COLORS[idx % ALIGNMENT_COLORS.length]} stroke="white" strokeWidth={2} />
                                        <text x={displayX + 10} y={displayY - 10} fill="white" fontSize="14" fontWeight="bold" stroke="black" strokeWidth={0.5}>{idx + 1}</text>
                                      </g>
                                    );
                                  })}
                                </svg>
                              </>
                            ) : (
                              <Typography variant="body2" color="text.secondary">
                                {loading ? 'Loading...' : 'Layout image'}
                              </Typography>
                            )}
                          </Paper>
                        </CardContent>
                      </Card>
                    </Grid>
                  </Grid>
                  </div>

                  {/* Action Buttons */}
                  <Box sx={{ mt: 3, display: 'flex', gap: 2, justifyContent: 'space-between', flexWrap: 'wrap' }}>
                    <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
                      {!viewOnly && (
                        <>
                          <Button
                            variant="outlined"
                            startIcon={<UndoIcon />}
                            onClick={handleUndo}
                            disabled={undoStack.length === 0}
                          >
                            Undo
                          </Button>
                          <Button
                            variant="outlined"
                            color="error"
                            startIcon={<RestartAltIcon />}
                            onClick={handleReset}
                          >
                            Reset All
                          </Button>
                        </>
                      )}
                      <Button
                        variant="outlined"
                        onClick={closeAlignmentTool}
                      >
                        {viewOnly ? 'Close' : 'Cancel'}
                      </Button>
                      {toolMode === 'view' && !alignmentLocked && (
                        <Button
                          variant="contained"
                          startIcon={<EditIcon />}
                          onClick={() => setToolMode('edit')}
                        >
                          Edit Points
                        </Button>
                      )}
                      {!viewOnly && getCompletedSets() >= 4 && (
                        <Button
                          variant="outlined"
                          color="primary"
                          startIcon={<AddIcon />}
                          onClick={handleAddPointSet}
                        >
                          Add More Points
                        </Button>
                      )}
                    </Stack>

                    {!viewOnly && (
                      <Button
                        variant="contained"
                        color="success"
                        startIcon={<SaveIcon />}
                        onClick={handleSaveAlignment}
                        disabled={!isComplete() || loading}
                      >
                        {toolMode === 'edit' ? 'Save Changes' : 'Save Alignment'} ({getCompletedSets()} sets)
                      </Button>
                    )}
                  </Box>
                </Box>
              )}
            </>
          )}
        </Box>
      )}
    </Box>
  );
};

export default ManualAlignment;
