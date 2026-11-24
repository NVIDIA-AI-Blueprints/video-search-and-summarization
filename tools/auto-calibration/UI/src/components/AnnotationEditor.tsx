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
 * Annotation Editor
 * Draw ROIs and tripwires on rectified camera frames or the layout map.
 * Used on the Results step once linear media is ready, with or without calibration.
 */

import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Grid,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Button,
  Alert,
  Chip,
  Stack,
  Divider,
  ToggleButtonGroup,
  ToggleButton,
} from '@mui/material';
import DrawIcon from '@mui/icons-material/Draw';
import TimelineIcon from '@mui/icons-material/Timeline';
import MapIcon from '@mui/icons-material/Map';
import VideocamIcon from '@mui/icons-material/Videocam';
import DeleteIcon from '@mui/icons-material/Delete';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import { useApp } from '../context/AppContext';
import { api } from '../services/api';
import { downloadAsFile } from '../utils/helpers';
import type { Project, ROI, Tripwire } from '../types';
import AnnotationCanvas from './AnnotationCanvas';

interface AnnotationEditorProps {
  project: Project;
  /** When true, drawing/save is disabled (e.g. calibration running). */
  locked?: boolean;
  /** Called after annotations are saved/deleted so parents (Results) can refresh overlays. */
  onAnnotationsChanged?: () => void;
  /** Shared with exports so they can wait for serialized annotation saves. */
  saveAnnotation?: (videoName: string, data: AnnotationData) => Promise<unknown>;
  beforeExport?: () => Promise<void>;
}

interface AnnotationData {
  rois: ROI[];
  tripwireLines: Tripwire[];
  tripwireDirections: Tripwire[];
}

type DrawingMode = 'none' | 'roi' | 'tripwire' | 'tripwire-direction';

type AnnotationTarget = 'camera' | 'global';

/** Server annotations.json key for layout-space ROIs/tripwires (must match backend consumers). */
const GLOBAL_ROIS_TRIPWIRES_KEY = 'global_rois_tripwires';

const AnnotationEditor: React.FC<AnnotationEditorProps> = ({
  project,
  locked = false,
  onAnnotationsChanged,
  saveAnnotation,
  beforeExport,
}) => {
  const { showSuccess, showError } = useApp();
  const [selectedCamera, setSelectedCamera] = useState<string>('');
  const [annotations, setAnnotations] = useState<Record<string, AnnotationData>>({});
  const [drawingMode, setDrawingMode] = useState<DrawingMode>('none');
  const [showAnnotations, setShowAnnotations] = useState(true);
  const [videoFiles, setVideoFiles] = useState<string[]>([]);
  const [frameImageUrl, setFrameImageUrl] = useState<string>('');
  const [loadingFrame, setLoadingFrame] = useState(false);
  const [annotationTarget, setAnnotationTarget] = useState<AnnotationTarget>('camera');
  const [layoutImageUrl, setLayoutImageUrl] = useState<string>('');
  const [loadingLayout, setLoadingLayout] = useState(false);
  const [exportingImageCalibration, setExportingImageCalibration] = useState(false);

  const annotationsLocked = locked;

  const getActiveAnnotationKey = (): string | null => {
    if (annotationTarget === 'global') {
      return project.layout_file ? GLOBAL_ROIS_TRIPWIRES_KEY : null;
    }
    return selectedCamera || null;
  };

  const ownerIdForNewShapes = (): string =>
    annotationTarget === 'global' ? GLOBAL_ROIS_TRIPWIRES_KEY : (selectedCamera || '');

  useEffect(() => {
    loadVideoFiles();
    loadAllAnnotations();
  }, [project.project_id]);

  // Drop any active drawing tool while locked
  useEffect(() => {
    if (annotationsLocked) {
      setDrawingMode('none');
    }
  }, [annotationsLocked]);

  // Auto-select first camera when videos load
  useEffect(() => {
    if (videoFiles.length > 0 && !selectedCamera) {
      setSelectedCamera(videoFiles[0]);
    }
  }, [videoFiles]);

  // Load frame and annotations when camera changes
  useEffect(() => {
    if (selectedCamera) {
      loadVideoFrame();
      loadCameraAnnotations(selectedCamera);
    }
  }, [selectedCamera]);

  // Load video frame (rectified / linear staged media)
  const loadVideoFrame = async () => {
    if (!selectedCamera) return;

    try {
      setLoadingFrame(true);
      const blob = await api.data.getVideoFrame(project.project_id, selectedCamera, 0, true);
      const url = URL.createObjectURL(blob);
      
      if (frameImageUrl) {
        URL.revokeObjectURL(frameImageUrl);
      }
      
      setFrameImageUrl(url);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to load video frame';
      showError(errorMessage);
    } finally {
      setLoadingFrame(false);
    }
  };

  // Cleanup object URLs on unmount
  useEffect(() => {
    return () => {
      if (frameImageUrl) {
        URL.revokeObjectURL(frameImageUrl);
      }
    };
  }, [frameImageUrl]);

  // Load layout.png for global ROI / tripwire mode
  useEffect(() => {
    if (annotationTarget !== 'global' || !project.layout_file) {
      setLayoutImageUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return '';
      });
      return undefined;
    }
    let cancelled = false;
    setLoadingLayout(true);
    api.data
      .getLayoutImage(project.project_id)
      .then((blob) => {
        if (cancelled) return;
        setLayoutImageUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return URL.createObjectURL(blob);
        });
      })
      .catch((error: unknown) => {
        const apiError = error as { message?: string; detail?: string };
        showError(apiError.detail || apiError.message || 'Failed to load layout image');
      })
      .finally(() => {
        if (!cancelled) setLoadingLayout(false);
      });
    return () => {
      cancelled = true;
    };
  }, [annotationTarget, project.layout_file, project.project_id]);

  // Load global annotations when switching to global target
  useEffect(() => {
    if (annotationTarget !== 'global' || !project.layout_file) return;
    (async () => {
      try {
        const response = await api.data.getAnnotations(
          project.project_id,
          GLOBAL_ROIS_TRIPWIRES_KEY,
        );
        if (response.annotations) {
          setAnnotations((prev) => ({
            ...prev,
            [GLOBAL_ROIS_TRIPWIRES_KEY]: {
              rois: response.annotations.rois || [],
              tripwireLines: response.annotations.tripwireLines || [],
              tripwireDirections: response.annotations.tripwireDirections || [],
            },
          }));
        }
      } catch {
        console.log('No global annotations yet');
      }
    })();
  }, [annotationTarget, project.layout_file, project.project_id]);

  // Load video files
  const loadVideoFiles = async () => {
    try {
      const response = await api.data.listVideoFiles(project.project_id);
      setVideoFiles(response.video_files);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to load videos';
      showError(errorMessage);
    }
  };

  // Load all annotations from server
  const loadAllAnnotations = async () => {
    try {
      const response = await api.data.getAllAnnotations(project.project_id);
      if (response.annotations) {
        const transformedAnnotations: Record<string, AnnotationData> = {};
        Object.keys(response.annotations).forEach((videoName) => {
          const serverData = response.annotations[videoName];
          transformedAnnotations[videoName] = {
            rois: serverData.rois || [],
            tripwireLines: serverData.tripwireLines || [],
            tripwireDirections: serverData.tripwireDirections || [],
          };
        });
        setAnnotations(transformedAnnotations);
      }
    } catch {
      console.log('No annotations data yet');
    }
  };

  // Load annotations for specific camera (raw drawings only — no projected global overlays)
  const loadCameraAnnotations = async (videoName: string) => {
    try {
      const response = await api.data.getAnnotations(
        project.project_id,
        videoName,
        false,
        false,
      );
      if (response.annotations) {
        setAnnotations(prev => ({
          ...prev,
          [videoName]: {
            rois: response.annotations.rois || [],
            tripwireLines: response.annotations.tripwireLines || [],
            tripwireDirections: response.annotations.tripwireDirections || [],
          },
        }));
      }
    } catch {
      console.log(`No annotations for ${videoName} yet`);
    }
  };

  /** Pixel-space ROIs/tripwires only; same JSON shape as cartesian export with empty calibration fields. */
  const handleExportImageCalibrationJson = async () => {
    try {
      setExportingImageCalibration(true);
      await beforeExport?.();
      await api.calibration.exportCalibration(project.project_id, 'amc', 'image');
      const blob = await api.calibration.downloadExportedCalibration(
        project.project_id,
        'amc',
        'image',
      );
      downloadAsFile(blob, `${project.project_name}_image_mode_exported.json`, 'application/json');
      showSuccess('Image-mode calibration JSON exported');
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to export image calibration JSON');
    } finally {
      setExportingImageCalibration(false);
    }
  };

  // Add ROI point
  const handleAddROIPoint = (x: number, y: number) => {
    const key = getActiveAnnotationKey();
    if (!key || annotationsLocked || drawingMode !== 'roi') return;

    const bucket = annotations[key] || {
      rois: [],
      tripwireLines: [],
      tripwireDirections: [],
    };
    const currentROI = bucket.rois.find(r => r.id === 'drawing');
    const owner = ownerIdForNewShapes();

    if (currentROI) {
      const updatedROI = {
        ...currentROI,
        points: [...currentROI.points, { x, y }],
      };

      setAnnotations({
        ...annotations,
        [key]: {
          ...bucket,
          rois: bucket.rois.map(r => (r.id === 'drawing' ? updatedROI : r)),
        },
      });
    } else {
      const newROI: ROI = {
        id: 'drawing',
        camera_id: owner,
        points: [{ x, y }],
        color: '#00ff00',
      };

      setAnnotations({
        ...annotations,
        [key]: {
          ...bucket,
          rois: [...bucket.rois, newROI],
        },
      });
    }
  };

  // Save annotations to server
  const saveAnnotationsToServer = async (videoName: string, annotationData: AnnotationData) => {
    if (annotationsLocked) return;
    try {
      if (saveAnnotation) {
        await saveAnnotation(videoName, annotationData);
      } else {
        await api.data.saveAnnotations(
          project.project_id,
          videoName,
          annotationData.rois || [],
          annotationData.tripwireLines || [],
          annotationData.tripwireDirections || []
        );
      }
      onAnnotationsChanged?.();
      return true;
    } catch (error) {
      console.error('Failed to auto-save annotations:', error);
      const apiError = error as { detail?: string; message?: string };
      showError(apiError.detail || apiError.message || 'Failed to save annotations. Please retry before exporting.');
      return false;
    }
  };

  // Finish ROI
  const handleFinishROI = async () => {
    const key = getActiveAnnotationKey();
    if (!key || annotationsLocked) return;

    const bucket = annotations[key];
    if (!bucket) return;

    const drawingROI = bucket.rois.find(r => r.id === 'drawing');
    if (!drawingROI || drawingROI.points.length < 3) {
      showError('ROI must have at least 3 points');
      return;
    }

    const finishedROI: ROI = {
      ...drawingROI,
      id: `roi-${Date.now()}`,
    };

    const updatedAnnotations: AnnotationData = {
      ...bucket,
      rois: [...bucket.rois.filter(r => r.id !== 'drawing'), finishedROI],
      tripwireLines: bucket.tripwireLines || [],
      tripwireDirections: bucket.tripwireDirections || [],
    };

    setAnnotations({
      ...annotations,
      [key]: updatedAnnotations,
    });

    const saved = await saveAnnotationsToServer(key, updatedAnnotations);

    setDrawingMode('none');
    if (saved) showSuccess('ROI saved');
  };

  // Add tripwire point (for both regular and direction)
  const handleAddTripwirePoint = async (x: number, y: number) => {
    const key = getActiveAnnotationKey();
    if (
      !key ||
      annotationsLocked ||
      (drawingMode !== 'tripwire' && drawingMode !== 'tripwire-direction')
    ) {
      return;
    }

    const bucket = annotations[key] || {
      rois: [],
      tripwireLines: [],
      tripwireDirections: [],
    };
    const owner = ownerIdForNewShapes();

    const isDirection = drawingMode === 'tripwire-direction';
    const targetArray = isDirection ? bucket.tripwireDirections : bucket.tripwireLines;
    const currentTripwire = targetArray.find(t => t.id === 'drawing');

    if (!currentTripwire) {
      const newTripwire: Tripwire = {
        id: 'drawing',
        camera_id: owner,
        start: { x, y },
        end: { x, y },
        color: isDirection ? '#ffff00' : '#ff0000',
        direction: isDirection ? 'forward' : undefined,
      };

      const updatedAnnotations: AnnotationData = {
        rois: bucket.rois || [],
        tripwireLines: isDirection ? bucket.tripwireLines || [] : [...targetArray, newTripwire],
        tripwireDirections: isDirection ? [...targetArray, newTripwire] : bucket.tripwireDirections || [],
      };

      setAnnotations({
        ...annotations,
        [key]: updatedAnnotations,
      });
    } else {
      const timestamp = Date.now();
      const finishedTripwire: Tripwire = {
        id: isDirection ? `tripdir-${timestamp}` : `tripwire-${timestamp}`,
        camera_id: owner,
        start: currentTripwire.start,
        end: { x, y },
        color: isDirection ? '#ffff00' : '#ff0000',
        direction: isDirection ? 'forward' : undefined,
      };

      const updatedAnnotations: AnnotationData = {
        rois: bucket.rois || [],
        tripwireLines: isDirection
          ? bucket.tripwireLines || []
          : [...targetArray.filter(t => t.id !== 'drawing'), finishedTripwire],
        tripwireDirections: isDirection
          ? [...targetArray.filter(t => t.id !== 'drawing'), finishedTripwire]
          : bucket.tripwireDirections || [],
      };

      setAnnotations({
        ...annotations,
        [key]: updatedAnnotations,
      });

      const saved = await saveAnnotationsToServer(key, updatedAnnotations);

      setDrawingMode('none');
      if (saved) showSuccess(isDirection ? 'Tripwire direction saved' : 'Tripwire line saved');
    }
  };

  // Delete annotation
  const handleDeleteAnnotation = async (type: 'roi' | 'tripwire' | 'tripwire-direction', id: string) => {
    const key = getActiveAnnotationKey();
    if (!key || annotationsLocked) return;

    const bucket = annotations[key];
    if (!bucket) return;

    let updatedAnnotations = { ...bucket };

    if (type === 'roi') {
      updatedAnnotations.rois = bucket.rois.filter(r => r.id !== id);
    } else if (type === 'tripwire') {
      updatedAnnotations.tripwireLines = (bucket.tripwireLines || []).filter(t => t.id !== id);
    } else if (type === 'tripwire-direction') {
      updatedAnnotations.tripwireDirections = (bucket.tripwireDirections || []).filter(t => t.id !== id);
    }

    setAnnotations({
      ...annotations,
      [key]: updatedAnnotations,
    });

    const saved = await saveAnnotationsToServer(key, updatedAnnotations);

    if (saved) showSuccess(`${type === 'roi' ? 'ROI' : type === 'tripwire-direction' ? 'Tripwire direction' : 'Tripwire line'} deleted`);
  };

  // Reset canvas
  const handleResetCanvas = async () => {
    const key = getActiveAnnotationKey();
    if (!key || annotationsLocked) return;

    const emptyAnnotations: AnnotationData = {
      rois: [],
      tripwireLines: [],
      tripwireDirections: [],
    };

    setAnnotations({
      ...annotations,
      [key]: emptyAnnotations,
    });

    const saved = await saveAnnotationsToServer(key, emptyAnnotations);

    setDrawingMode('none');
    if (saved) showSuccess('Canvas reset');
  };

  const activeKey = getActiveAnnotationKey();
  const currentAnnotations = activeKey
    ? annotations[activeKey] || { rois: [], tripwireLines: [], tripwireDirections: [] }
    : { rois: [], tripwireLines: [], tripwireDirections: [] };
  
  // Combine tripwires for canvas display (canvas expects single array)
  const allTripwires = [
    ...(currentAnnotations.tripwireLines || []),
    ...(currentAnnotations.tripwireDirections || [])
  ];

  return (
    <Box>
      {annotationsLocked && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Calibration is running. ROI and tripwire drawing is view-only until it finishes.
        </Alert>
      )}

      {/* No videos alert */}
      {videoFiles.length === 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          No videos available. Complete Video Configuration first.
        </Alert>
      )}

      {videoFiles.length > 0 && (
        <Grid container spacing={3}>
          {/* Left Panel - Camera Selection & Drawing */}
          <Grid item xs={12} md={8}>
            <Card>
              <CardContent>
                <Typography variant="subtitle2" gutterBottom>
                  Annotation target
                </Typography>
                <ToggleButtonGroup
                  value={annotationTarget}
                  exclusive
                  size="small"
                  sx={{ mb: 2 }}
                  onChange={(_, value) => {
                    if (value === null) return;
                    setDrawingMode('none');
                    setAnnotationTarget(value);
                  }}
                >
                  <ToggleButton value="camera">
                    <VideocamIcon sx={{ mr: 0.5 }} fontSize="small" />
                    Camera
                  </ToggleButton>
                  <ToggleButton value="global" disabled={!project.layout_file}>
                    <MapIcon sx={{ mr: 0.5 }} fontSize="small" />
                    Global ROIs / tripwires
                  </ToggleButton>
                </ToggleButtonGroup>

                {!project.layout_file && (
                  <Alert severity="info" sx={{ mb: 2 }}>
                    Upload <strong>layout.png</strong> in Video Configuration to draw global ROIs and tripwires on the floor map.
                  </Alert>
                )}

                {/* Camera Selection */}
                {annotationTarget === 'camera' && (
                  <FormControl fullWidth sx={{ mb: 2 }}>
                    <InputLabel>Select Camera</InputLabel>
                    <Select
                      value={selectedCamera}
                      onChange={(e) => {
                        setSelectedCamera(e.target.value);
                        setDrawingMode('none');
                      }}
                      label="Select Camera"
                    >
                      {videoFiles.map((video, index) => {
                        const camId = `cam_${index.toString().padStart(2, '0')}`;
                        const displayName = project.camera_name_map?.[camId] || video;
                        return (
                          <MenuItem key={video} value={video}>
                            {displayName}
                          </MenuItem>
                        );
                      })}
                    </Select>
                  </FormControl>
                )}

                {/* Drawing Tools */}
                <Box sx={{ mb: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    Drawing Tools
                  </Typography>
                  <Stack direction="row" spacing={1} flexWrap="wrap">
                    <ToggleButtonGroup
                      value={drawingMode}
                      exclusive
                      onChange={(_, value) => value !== null && setDrawingMode(value)}
                      size="small"
                      disabled={
                        annotationsLocked ||
                        (annotationTarget === 'global' &&
                          (!project.layout_file || loadingLayout || !layoutImageUrl))
                      }
                    >
                      <ToggleButton value="roi">
                        <DrawIcon sx={{ mr: 1 }} />
                        Draw ROI
                      </ToggleButton>
                      <ToggleButton value="tripwire">
                        <TimelineIcon sx={{ mr: 1 }} />
                        Draw Tripwire
                      </ToggleButton>
                      <ToggleButton value="tripwire-direction">
                        <TimelineIcon sx={{ mr: 1 }} />
                        Tripwire Direction
                      </ToggleButton>
                    </ToggleButtonGroup>

                    <Button
                      variant="outlined"
                      size="small"
                      onClick={() => setShowAnnotations(!showAnnotations)}
                      startIcon={showAnnotations ? <VisibilityIcon /> : <VisibilityOffIcon />}
                    >
                      {showAnnotations ? 'Hide' : 'Show'}
                    </Button>

                    <Button
                      variant="outlined"
                      size="small"
                      color="error"
                      onClick={handleResetCanvas}
                      startIcon={<RestartAltIcon />}
                      disabled={
                        annotationsLocked ||
                        !activeKey ||
                        (currentAnnotations.rois.length === 0 && allTripwires.length === 0)
                      }
                    >
                      Reset
                    </Button>
                  </Stack>
                </Box>

                {/* Konva Canvas — video frame or layout map */}
                {annotationTarget === 'camera' &&
                  (loadingFrame ? (
                    <Box
                      sx={{
                        height: 500,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: 'action.hover',
                        border: '2px solid',
                        borderColor: 'grey.400',
                      }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        Loading video frame...
                      </Typography>
                    </Box>
                  ) : frameImageUrl ? (
                    <Box
                      sx={{
                        height: 500,
                        width: '100%',
                        border: '2px solid',
                        borderColor: 'grey.400',
                        position: 'relative',
                        overflow: 'hidden',
                      }}
                    >
                      <AnnotationCanvas
                        imageUrl={frameImageUrl}
                        rois={currentAnnotations.rois}
                        tripwires={allTripwires}
                        drawingMode={drawingMode}
                        showAnnotations={showAnnotations}
                        readOnly={annotationsLocked}
                        onAddROIPoint={handleAddROIPoint}
                        onFinishROI={handleFinishROI}
                        onAddTripwirePoint={handleAddTripwirePoint}
                      />
                    </Box>
                  ) : (
                    <Box
                      sx={{
                        height: 500,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: 'action.hover',
                        border: '2px solid',
                        borderColor: 'grey.400',
                      }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        {selectedCamera ? 'No video frame available' : 'Select a camera to start'}
                      </Typography>
                    </Box>
                  ))}

                {annotationTarget === 'global' &&
                  (loadingLayout ? (
                    <Box
                      sx={{
                        height: 500,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: 'action.hover',
                        border: '2px solid',
                        borderColor: 'grey.400',
                      }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        Loading layout map...
                      </Typography>
                    </Box>
                  ) : layoutImageUrl ? (
                    <Box
                      sx={{
                        height: 500,
                        width: '100%',
                        border: '2px solid',
                        borderColor: 'grey.400',
                        position: 'relative',
                        overflow: 'hidden',
                      }}
                    >
                      <AnnotationCanvas
                        imageUrl={layoutImageUrl}
                        rois={currentAnnotations.rois}
                        tripwires={allTripwires}
                        drawingMode={drawingMode}
                        showAnnotations={showAnnotations}
                        readOnly={annotationsLocked}
                        onAddROIPoint={handleAddROIPoint}
                        onFinishROI={handleFinishROI}
                        onAddTripwirePoint={handleAddTripwirePoint}
                      />
                    </Box>
                  ) : (
                    <Box
                      sx={{
                        height: 500,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: 'action.hover',
                        border: '2px solid',
                        borderColor: 'grey.400',
                      }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        Layout image not available
                      </Typography>
                    </Box>
                  ))}

                <Alert severity="info" sx={{ mt: 2 }}>
                  <Typography variant="caption">
                    <strong>Camera mode:</strong> draw on the <strong>rectified</strong> frame<br />
                    <strong>ROI:</strong> Click to add points (min 3), press F or right-click to finish<br />
                    <strong>Tripwire:</strong> Click twice (start & end) - red line, auto-saves<br />
                    <strong>Tripwire Direction:</strong> Draw the yellow arrow across the tripwire line
                    from inside to outside. The first point (first click) is inside; the second point
                    (second click) is outside.<br />
                    <strong>Controls:</strong> Scroll to zoom, drag to pan, Show/Hide to toggle annotations
                  </Typography>
                </Alert>
              </CardContent>
            </Card>
          </Grid>

          {/* Right Panel - Annotations List & Export */}
          <Grid item xs={12} md={4}>
            {/* Current Annotations */}
            <Card sx={{ mb: 2 }}>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Current Annotations
                </Typography>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  {annotationTarget === 'global'
                    ? 'Global (layout map)'
                    : selectedCamera
                      ? (() => {
                          const index = videoFiles.indexOf(selectedCamera);
                          const camId = `cam_${index.toString().padStart(2, '0')}`;
                          return project.camera_name_map?.[camId] || selectedCamera;
                        })()
                      : 'Select a camera'}
                </Typography>

                <Divider sx={{ my: 2 }} />

                {/* ROIs */}
                <Typography variant="subtitle2" gutterBottom>
                  ROIs ({currentAnnotations.rois.filter(r => r.id !== 'drawing').length})
                </Typography>
                {currentAnnotations.rois.filter(r => r.id !== 'drawing').map((roi) => (
                  <Box key={roi.id} sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Chip
                      label={`ROI (${roi.points.length} points)`}
                      size="small"
                      sx={{ bgcolor: roi.color, color: 'white' }}
                    />
                    <Button
                      size="small"
                      color="error"
                      disabled={annotationsLocked}
                      onClick={() => handleDeleteAnnotation('roi', roi.id)}
                    >
                      <DeleteIcon fontSize="small" />
                    </Button>
                  </Box>
                ))}

                {currentAnnotations.rois.find(r => r.id === 'drawing') && (
                  <Chip
                    label={`Drawing... (${currentAnnotations.rois.find(r => r.id === 'drawing')?.points.length || 0} points)`}
                    size="small"
                    color="warning"
                  />
                )}

                <Divider sx={{ my: 2 }} />

                {/* Tripwire Lines */}
                <Typography variant="subtitle2" gutterBottom>
                  Tripwire Lines ({(currentAnnotations.tripwireLines || []).filter(t => t.id !== 'drawing').length})
                </Typography>
                {(currentAnnotations.tripwireLines || []).filter(t => t.id !== 'drawing').map((tripwire) => (
                  <Box key={tripwire.id} sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Chip
                      label="Tripwire Line"
                      size="small"
                      sx={{ bgcolor: '#ff0000', color: 'white' }}
                    />
                    <Button
                      size="small"
                      color="error"
                      disabled={annotationsLocked}
                      onClick={() => handleDeleteAnnotation('tripwire', tripwire.id)}
                    >
                      <DeleteIcon fontSize="small" />
                    </Button>
                  </Box>
                ))}

                {(currentAnnotations.tripwireLines || []).find(t => t.id === 'drawing') && drawingMode === 'tripwire' && (
                  <Chip
                    label="Drawing Tripwire Line..."
                    size="small"
                    color="warning"
                  />
                )}

                <Divider sx={{ my: 2 }} />

                {/* Tripwire Directions */}
                <Typography variant="subtitle2" gutterBottom>
                  Tripwire Directions ({(currentAnnotations.tripwireDirections || []).filter(t => t.id !== 'drawing').length})
                </Typography>
                {(currentAnnotations.tripwireDirections || []).filter(t => t.id !== 'drawing').map((tripwire) => (
                  <Box key={tripwire.id} sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Chip
                      label="Tripwire Direction →"
                      size="small"
                      sx={{ bgcolor: '#ffff00', color: 'black' }}
                    />
                    <Button
                      size="small"
                      color="error"
                      disabled={annotationsLocked}
                      onClick={() => handleDeleteAnnotation('tripwire-direction', tripwire.id)}
                    >
                      <DeleteIcon fontSize="small" />
                    </Button>
                  </Box>
                ))}

                {(currentAnnotations.tripwireDirections || []).find(t => t.id === 'drawing') && drawingMode === 'tripwire-direction' && (
                  <Chip
                    label="Drawing Direction..."
                    size="small"
                    color="warning"
                  />
                )}
              </CardContent>
            </Card>

            {/* Image-mode export (pixel ROIs / tripwires) */}
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Export image-mode JSON
                </Typography>
                <Alert severity="info" sx={{ mb: 2 }} icon={false}>
                  <Typography variant="caption">
                    Export ROIs and tripwires in <strong>pixel coordinates</strong> (<strong>calibrationType</strong>:{' '}
                    <strong>image</strong>).
                  </Typography>
                </Alert>
                <Button
                  fullWidth
                  variant="outlined"
                  color="primary"
                  startIcon={<FileDownloadIcon />}
                  onClick={handleExportImageCalibrationJson}
                  disabled={exportingImageCalibration}
                >
                  {exportingImageCalibration ? 'Exporting…' : 'Export image-mode JSON'}
                </Button>
              </CardContent>
            </Card>
          </Grid>
        </Grid>
      )}

      <Alert severity="success" sx={{ mt: 2 }}>
        <Typography variant="subtitle2">
          Annotations save automatically as you draw on rectified frames (camera) or the layout map (global).
          After edits, regenerate ROI/Tripwire Verification if you need an updated BEV.
        </Typography>
      </Alert>
    </Box>
  );
};

export default AnnotationEditor;

