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
 * Results Step
 * View calibration results and export data
 */

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Alert,
  Tabs,
  Tab,
  Paper,
  Grid,
  Divider,
  CircularProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  ToggleButtonGroup,
  ToggleButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stack,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import DataObjectIcon from '@mui/icons-material/DataObject';
import FolderZipIcon from '@mui/icons-material/FolderZip';
import AssessmentIcon from '@mui/icons-material/Assessment';
import DeleteIcon from '@mui/icons-material/Delete';
import VerifiedIcon from '@mui/icons-material/Verified';
import CloseIcon from '@mui/icons-material/Close';
import ZoomInIcon from '@mui/icons-material/ZoomIn';
import ZoomOutIcon from '@mui/icons-material/ZoomOut';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import EditNoteIcon from '@mui/icons-material/EditNote';
import SaveIcon from '@mui/icons-material/Save';
import VideocamIcon from '@mui/icons-material/Videocam';
import MapIcon from '@mui/icons-material/Map';
import { useApp } from '../../context/AppContext';
import { api } from '../../services/api';
import AnnotationCanvas from '../AnnotationCanvas';
import AnnotationEditor from '../AnnotationEditor';
import GlobalExportSensorToggles from '../GlobalExportSensorToggles';
import ExportMetadataForm from '../ExportMetadataForm';
import JsonCodeEditor, { type JsonParseLocation, tryParseJson } from '../JsonCodeEditor';
import {
  extractSensorMetaEdits,
  extractGlobalRootRoiMetaEdits,
  extractGlobalRootTripwireMetaEdits,
  mergeSensorMetaIntoExport,
  mergeGlobalRootRoiMetaIntoExport,
  mergeGlobalRootTripwireMetaIntoExport,
  type SensorMetaEdit,
  type GlobalRootRoiMetaEdit,
  type GlobalRootTripwireMetaEdit,
} from '../../utils/exportMetadata';
import type { Project, ROI, Tripwire, ConfigParams } from '../../types';
import { AnnotationExportSession, mergeFreshAnnotationGeometry } from '../../utils/annotationExport';
import type { CalibrationExportDoc } from '../calibrationExportSensorAssignment';
import {
  sensorDisplayIdFromVideoFile,
  stringifyExportDoc,
  toggleGlobalFeatureSensorAssignment,
} from '../calibrationExportSensorAssignment';

/** Must match Parameters step and backend annotations bucket key */
const GLOBAL_ROIS_TRIPWIRES_KEY = 'global_rois_tripwires';

interface ResultsProps {
  project: Project;
  onProjectUpdate?: (project: Project) => void;
}

const Results: React.FC<ResultsProps> = ({ project, onProjectUpdate }) => {
  const { showSuccess, showError, vggtAvailable } = useApp();
  const annotationExports = useMemo(() => new AnnotationExportSession({
    download: async type => (await api.calibration.downloadExportedCalibration(
      project.project_id, type, 'cartesian', 'exported',
    )).text(),
    regenerate: type => api.calibration.exportCalibration(project.project_id, type),
    save: (type, text) => api.calibration.saveExportedCalibration(project.project_id, type, text, 'cartesian'),
    backup: {
      load: type => localStorage.getItem(`amc:export-backup:${project.project_id}:${type}`) ?? undefined,
      store: (type, text) => localStorage.setItem(`amc:export-backup:${project.project_id}:${type}`, text),
      clear: type => localStorage.removeItem(`amc:export-backup:${project.project_id}:${type}`),
    },
  }), [project.project_id]);
  const isSingleCam = project.video_files_count === 1;
  const [selectedTab, setSelectedTab] = useState(0);
  const [overlayResultType, setOverlayResultType] = useState<'amc' | 'vggt'>('amc');
  const [overlaySource, setOverlaySource] = useState<'trajectory' | 'virtual_gt'>('trajectory');
  const [virtualGtCamera, setVirtualGtCamera] = useState(0);
  const [virtualGtOverlayUrl, setVirtualGtOverlayUrl] = useState('');
  const [loadingVirtualGtOverlay, setLoadingVirtualGtOverlay] = useState(false);
  const [virtualGtPersonHeight, setVirtualGtPersonHeight] = useState(1.75);
  const [virtualGtGridSpacing, setVirtualGtGridSpacing] = useState(1.0);
  const [cameraResultType, setCameraResultType] = useState<'amc' | 'vggt'>('amc');
  // Exports need post-processed world-scaled artifacts, not raw calibration output.
  // Flags are per pipeline so a later VGGT run that resets postprocess_state does
  // not hide AMC exports whose scaled files are still on disk.
  const amcExportReady = project.amc_export_ready === true;
  const vggtExportReady = project.vggt_export_ready === true;
  const vggtSelectable = vggtAvailable && !isSingleCam;
  const cameraExportReady =
    cameraResultType === 'vggt' ? vggtExportReady : amcExportReady;
  const anyExportReady = amcExportReady || (vggtSelectable && vggtExportReady);
  const [evaluationResultType, setEvaluationResultType] = useState<'amc' | 'vggt'>('amc');
  const [overlayImageUrl, setOverlayImageUrl] = useState<string>('');
  const [vggtOverlayImageUrl, setVggtOverlayImageUrl] = useState<string>('');
  const [evaluationImageUrl, setEvaluationImageUrl] = useState<string>('');
  const [evaluationStats, setEvaluationStats] = useState<Record<string, string> | null>(null);
  const [vggtEvaluationImageUrl, setVggtEvaluationImageUrl] = useState<string>('');
  const [vggtEvaluationStats, setVggtEvaluationStats] = useState<Record<string, string> | null>(null);
  const [cameraParameters, setCameraParameters] = useState<Record<number, string>>({});
  const [vggtCameraParameters, setVggtCameraParameters] = useState<Record<number, string>>({});
  const [loadingOverlay, setLoadingOverlay] = useState(false);
  const [loadingVggtOverlay, setLoadingVggtOverlay] = useState(false);
  const [loadingParams, setLoadingParams] = useState(false);
  const [loadingVggtParams, setLoadingVggtParams] = useState(false);
  const [loadingEvaluation, setLoadingEvaluation] = useState(false);
  const [downloadingMv3dt, setDownloadingMv3dt] = useState(false);
  const [downloadingVggtMv3dt, setDownloadingVggtMv3dt] = useState(false);
  const [deletingResults, setDeletingResults] = useState(false);
  const [exportExists, setExportExists] = useState(false);
  const [vggtExportExists, setVggtExportExists] = useState(false);
  const [downloadingFullExport, setDownloadingFullExport] = useState<'amc' | 'vggt' | null>(null);
  /** Optional fields merged into downloaded *_exported.json (per Full Export). */
  const [exportMetaEdits, setExportMetaEdits] = useState<Record<number, SensorMetaEdit> | null>(null);
  const [exportGlobalRoiEdits, setExportGlobalRoiEdits] = useState<Record<
    number,
    GlobalRootRoiMetaEdit
  > | null>(null);
  const [exportGlobalTwEdits, setExportGlobalTwEdits] = useState<Record<
    number,
    GlobalRootTripwireMetaEdit
  > | null>(null);
  const [exportMetaKind, setExportMetaKind] = useState<'amc' | 'vggt'>('amc');
  const [exportMetaLoading, setExportMetaLoading] = useState(false);
  const [exportMetaSensorLabels, setExportMetaSensorLabels] = useState<string[]>([]);
  /** Full Export opens this dialog first (metadata optional, then Download). */
  const [fullExportDialogOpen, setFullExportDialogOpen] = useState(false);
  /** Manual JSON editor for Full Export; when open, Download uses this text (validated) instead of form-only merge. */
  const [fullExportManualOpen, setFullExportManualOpen] = useState(false);
  const [fullExportManualText, setFullExportManualText] = useState('');
  const [fullExportManualLoading, setFullExportManualLoading] = useState(false);
  const [fullExportManualSaving, setFullExportManualSaving] = useState(false);
  const [resettingFullExport, setResettingFullExport] = useState(false);
  const [resetExportConfirmOpen, setResetExportConfirmOpen] = useState(false);
  const [fullExportManualParseError, setFullExportManualParseError] =
    useState<JsonParseLocation | null>(null);
  /** Manual JSON kept via "Save JSON": becomes the base document for later form edits and download. */
  const [fullExportManualSavedByType, setFullExportManualSavedByType] = useState<
    Partial<Record<'amc' | 'vggt', string>>
  >({});
  /** Editor text as last loaded or saved, so closing only warns about genuinely unsaved edits. */
  const fullExportManualBaselineRef = useRef('');
  const [showingRoiBev, setShowingRoiBev] = useState(false);
  const [roiBevImageUrl, setRoiBevImageUrl] = useState<string>('');
  const [vggtRoiBevImageUrl, setVggtRoiBevImageUrl] = useState<string>('');
  const [bevResultType, setBevResultType] = useState<'amc' | 'vggt'>('amc');
  const [generatingRoiBev, setGeneratingRoiBev] = useState(false);
  
  // Annotation verification state
  const [selectedVideo, setSelectedVideo] = useState<string>('');
  const [videoFiles, setVideoFiles] = useState<string[]>([]);
  const [frameImageUrl, setFrameImageUrl] = useState<string>('');
  const [loadingFrame, setLoadingFrame] = useState(false);
  const [videoAnnotations, setVideoAnnotations] = useState<{
    rois: ROI[];
    tripwireLines: Tripwire[];
    tripwireDirections: Tripwire[];
  }>({ rois: [], tripwireLines: [], tripwireDirections: [] });

  const [roiVerificationTarget, setRoiVerificationTarget] = useState<'camera' | 'global'>('camera');
  const [globalLayoutImageUrl, setGlobalLayoutImageUrl] = useState<string>('');
  const [globalVerificationAnnotations, setGlobalVerificationAnnotations] = useState<{
    rois: ROI[];
    tripwireLines: Tripwire[];
    tripwireDirections: Tripwire[];
  }>({ rois: [], tripwireLines: [], tripwireDirections: [] });
  const [loadingGlobalVerification, setLoadingGlobalVerification] = useState(false);
  
  // BEV zoom/pan state
  const [bevZoom, setBevZoom] = useState(1);
  const [bevPan, setBevPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });

  /** Cached export JSON per AMC/VGGT while ROI verification is open (checkbox edits). */
  const [exportDraftByType, setExportDraftByType] = useState<Partial<Record<'amc' | 'vggt', string>>>({});
  const [exportDraftLoading, setExportDraftLoading] = useState(false);
  const exportDraftLoadedRef = useRef<Set<'amc' | 'vggt'>>(new Set());

  const verificationExportDoc = useMemo((): CalibrationExportDoc | null => {
    const raw = exportDraftByType[bevResultType];
    if (!raw) return null;
    try {
      return JSON.parse(raw) as CalibrationExportDoc;
    } catch {
      return null;
    }
  }, [exportDraftByType, bevResultType]);

  const sensorDisplayIdForVerification = useMemo(
    () =>
      selectedVideo
        ? sensorDisplayIdFromVideoFile(selectedVideo, project.camera_name_map)
        : '',
    [selectedVideo, project.camera_name_map]
  );

  const applyGlobalSensorToggle = useCallback(
    (section: 'rois' | 'tripwires', globalId: string, include: boolean) => {
      const rt = bevResultType;
      setExportDraftByType((prev) => {
        const raw = prev[rt];
        if (!raw) return prev;
        try {
          const doc = JSON.parse(raw) as CalibrationExportDoc;
          const sid = sensorDisplayIdFromVideoFile(selectedVideo, project.camera_name_map);
          const next = toggleGlobalFeatureSensorAssignment(doc, section, globalId, sid, include);
          return { ...prev, [rt]: stringifyExportDoc(next) };
        } catch {
          return prev;
        }
      });
    },
    [bevResultType, selectedVideo, project.camera_name_map]
  );

  useEffect(() => {
    if (!showingRoiBev || !project.project_id) return;
    const rt = bevResultType;
    if (rt === 'amc' && project.amc_state !== 'COMPLETED') return;
    if (rt === 'vggt' && project.vggt_state !== 'COMPLETED') return;
    if (exportDraftLoadedRef.current.has(rt)) return;

    let cancelled = false;
    setExportDraftLoading(true);
    void (async () => {
      try {
        let text: string;
        try {
          const blob = await api.calibration.downloadExportedCalibration(
            project.project_id,
            rt,
            'cartesian',
            'projected',
          );
          text = await blob.text();
        } catch {
          const blob = await api.calibration.downloadExportedCalibration(
            project.project_id,
            rt,
            'cartesian',
            'exported',
          );
          text = await blob.text();
        }
        if (!cancelled) {
          exportDraftLoadedRef.current.add(rt);
          setExportDraftByType((prev) => ({ ...prev, [rt]: text }));
        }
      } catch {
        if (!cancelled) {
          showError(`Could not load ${rt.toUpperCase()} calibration JSON for sensor assignment checkboxes`);
        }
      } finally {
        if (!cancelled) setExportDraftLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showingRoiBev, bevResultType, project.amc_state, project.vggt_state, project.project_id]);

  useEffect(() => {
    if (project.amc_state === 'COMPLETED' || project.vggt_state === 'COMPLETED') {
      checkExportFileExists();
      loadVideoFiles();
    }
  }, [project.project_id, project.amc_state, project.vggt_state]);

  // Auto-select VGGT if AMC failed but VGGT passed
  useEffect(() => {
    if (project.amc_state !== 'COMPLETED' && project.vggt_state === 'COMPLETED') {
      setOverlayResultType('vggt');
      setCameraResultType('vggt');
      setEvaluationResultType('vggt');
      setBevResultType('vggt');
    }
  }, [project.amc_state, project.vggt_state]);

  // Load overlay images when their tab is selected
  useEffect(() => {
    if (overlayResultType === 'amc' && project.amc_state === 'COMPLETED' && !overlayImageUrl) {
      loadOverlayImage();
    } else if (overlayResultType === 'vggt' && project.vggt_state === 'COMPLETED' && !vggtOverlayImageUrl) {
      loadVggtOverlayImage();
    }
  }, [overlayResultType, project.amc_state, project.vggt_state]);

  useEffect(() => {
    if (overlaySource === 'virtual_gt') {
      loadVirtualGtOverlay();
    }
  }, [overlaySource, overlayResultType, virtualGtCamera, project.project_id]);

  useEffect(() => {
    if (overlaySource !== 'virtual_gt') return;
    let cancelled = false;
    const loadVirtualGtConfig = async () => {
      try {
        const response = (await api.data.getConfig()) as { config_params?: ConfigParams };
        const height = response.config_params?.virtual_gt_person_height;
        const spacing = response.config_params?.virtual_gt_grid_spacing;
        if (!cancelled && typeof height === 'number' && Number.isFinite(height) && height > 0) {
          setVirtualGtPersonHeight(height);
        }
        if (!cancelled && typeof spacing === 'number' && Number.isFinite(spacing) && spacing > 0) {
          setVirtualGtGridSpacing(spacing);
        }
      } catch {
        // Keep previous / default values for the caption.
      }
    };
    void loadVirtualGtConfig();
    return () => {
      cancelled = true;
    };
  }, [overlaySource, project.project_id]);

  // Load camera parameters based on result type when tab changes
  useEffect(() => {
    if (cameraResultType === 'amc' && amcExportReady) {
      loadCameraParameters(selectedTab);
    } else if (cameraResultType === 'vggt' && vggtExportReady) {
      loadVggtCameraParameters(selectedTab);
    }
  }, [selectedTab, cameraResultType, amcExportReady, vggtExportReady]);

  // Load evaluation data when result type changes
  useEffect(() => {
    if (project.groundtruth_file) {
      if (evaluationResultType === 'amc' && project.amc_state === 'COMPLETED') {
        if (!evaluationImageUrl) loadEvaluationMetrics();
        if (!evaluationStats) loadEvaluationStatistics();
      } else if (evaluationResultType === 'vggt' && project.vggt_state === 'COMPLETED') {
        if (!vggtEvaluationImageUrl) loadVggtEvaluationMetrics();
        if (!vggtEvaluationStats) loadVggtEvaluationStatistics();
      }
    }
  }, [evaluationResultType, project.amc_state, project.vggt_state, project.groundtruth_file]);

  // Auto-select first video
  useEffect(() => {
    if (videoFiles.length > 0 && !selectedVideo) {
      setSelectedVideo(videoFiles[0]);
    }
  }, [videoFiles]);

  // Load frame and annotations when video / result type changes (camera verification only)
  useEffect(() => {
    if (selectedVideo && showingRoiBev && roiVerificationTarget === 'camera') {
      loadVideoFrame();
      loadVideoAnnotations();
    }
  }, [selectedVideo, showingRoiBev, roiVerificationTarget, bevResultType]);

  // Layout + global annotations (global verification)
  useEffect(() => {
    if (!showingRoiBev || roiVerificationTarget !== 'global' || !project.layout_file) {
      return undefined;
    }
    let cancelled = false;
    const run = async () => {
      setLoadingGlobalVerification(true);
      try {
        const blob = await api.data.getLayoutImage(project.project_id);
        const url = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        setGlobalLayoutImageUrl(prev => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
        const response = await api.data.getAnnotations(
          project.project_id,
          GLOBAL_ROIS_TRIPWIRES_KEY,
          true,
        );
        if (cancelled) return;
        if (response.annotations) {
          setGlobalVerificationAnnotations({
            rois: response.annotations.rois || [],
            tripwireLines: response.annotations.tripwireLines || [],
            tripwireDirections: response.annotations.tripwireDirections || [],
          });
        } else {
          setGlobalVerificationAnnotations({
            rois: [],
            tripwireLines: [],
            tripwireDirections: [],
          });
        }
      } catch {
        if (!cancelled) {
          showError('Failed to load layout or global annotations');
          setGlobalVerificationAnnotations({
            rois: [],
            tripwireLines: [],
            tripwireDirections: [],
          });
        }
      } finally {
        if (!cancelled) setLoadingGlobalVerification(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [showingRoiBev, roiVerificationTarget, project.layout_file, project.project_id]);

  // Cleanup object URLs on unmount
  useEffect(() => {
    return () => {
      if (frameImageUrl) {
        URL.revokeObjectURL(frameImageUrl);
      }
    };
  }, [frameImageUrl]);

  // Load overlay image
  const loadOverlayImage = async () => {
    try {
      setLoadingOverlay(true);
      const blob = await api.calibration.getOverlayImage(project.project_id);
      const url = URL.createObjectURL(blob);
      setOverlayImageUrl(url);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      console.info(apiError.detail || apiError.message || 'AMC overlay image not available');
      setOverlayImageUrl('');
    } finally {
      setLoadingOverlay(false);
    }
  };

  // Load VGGT overlay image
  const loadVggtOverlayImage = async () => {
    try {
      setLoadingVggtOverlay(true);
      const blob = await api.calibration.getVggtOverlayImage(project.project_id);
      const url = URL.createObjectURL(blob);
      setVggtOverlayImageUrl(url);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      console.info(apiError.detail || apiError.message || 'VGGT overlay image not available');
      setVggtOverlayImageUrl('');
    } finally {
      setLoadingVggtOverlay(false);
    }
  };

  const loadVirtualGtOverlay = async () => {
    setLoadingVirtualGtOverlay(true);
    setVirtualGtOverlayUrl(previousUrl => {
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      return '';
    });
    try {
      const blob = await api.calibration.getVirtualGtOverlay(
        project.project_id,
        virtualGtCamera,
        overlayResultType
      );
      setVirtualGtOverlayUrl(URL.createObjectURL(blob));
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      console.info(apiError.detail || apiError.message || 'Virtual-GT overlay not available');
    } finally {
      setLoadingVirtualGtOverlay(false);
    }
  };

  // Load camera parameters
  const loadCameraParameters = async (cameraId: number) => {
    if (cameraParameters[cameraId]) return; // Already loaded

    try {
      setLoadingParams(true);
      const yamlText = await api.calibration.getCameraParameters(project.project_id, cameraId);
      setCameraParameters(prev => ({ ...prev, [cameraId]: yamlText }));
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || `Failed to load camera ${cameraId} parameters`;
      showError(errorMessage);
    } finally {
      setLoadingParams(false);
    }
  };

  // Load VGGT camera parameters
  const loadVggtCameraParameters = async (cameraId: number) => {
    if (vggtCameraParameters[cameraId]) return; // Already loaded

    try {
      setLoadingVggtParams(true);
      const yamlText = await api.calibration.getVggtCameraParameters(project.project_id, cameraId);
      setVggtCameraParameters(prev => ({ ...prev, [cameraId]: yamlText }));
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || `Failed to load VGGT camera ${cameraId} parameters`;
      showError(errorMessage);
    } finally {
      setLoadingVggtParams(false);
    }
  };

  // Export calibration data (must merge API responses locally — React state from setCameraParameters
  // is not updated until after render, so reading cameraParameters after await loadCameraParameters()
  // sees a stale closure and fails on the first attempt.)
  const handleExportData = async () => {
    const isVggt = cameraResultType === 'vggt';
    if (!cameraExportReady) {
      showError(
        isSingleCam
          ? 'AMC finished, but world-scaled camera parameters are missing. SV post-processing runs inside AMC when layout and alignment are present — re-run AMC with those inputs.'
          : isVggt
            ? 'VGGT calibration is complete. Run layout post-processing in the Execute step before exporting VGGT results.'
            : 'AMC calibration is complete. Run layout post-processing in the Execute step before exporting AMC results.',
      );
      return;
    }
    try {
      showSuccess('Loading all camera parameters...');
      if (isVggt) setLoadingVggtParams(true);
      else setLoadingParams(true);

      const merged: Record<number, string> = {};
      for (let i = 0; i < project.video_files_count; i++) {
        const yamlText = isVggt
          ? await api.calibration.getVggtCameraParameters(project.project_id, i)
          : await api.calibration.getCameraParameters(project.project_id, i);
        merged[i] = yamlText;
        if (isVggt) {
          setVggtCameraParameters(prev => ({ ...prev, [i]: yamlText }));
        } else {
          setCameraParameters(prev => ({ ...prev, [i]: yamlText }));
        }
      }

      if (Object.keys(merged).length < project.video_files_count) {
        showError(
          `Only ${Object.keys(merged).length} of ${project.video_files_count} cameras loaded. Please try again.`,
        );
        return;
      }

      const exportData = {
        project_id: project.project_id,
        project_name: project.project_name,
        result_type: cameraResultType.toUpperCase(),
        camera_count: project.video_files_count,
        cameras: merged,
        timestamp: new Date().toISOString(),
      };

      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${project.project_name}_${cameraResultType}_calibration_export.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);

      showSuccess(`${cameraResultType.toUpperCase()} calibration data exported successfully (${project.video_files_count} cameras)`);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to export data';
      showError(errorMessage);
    } finally {
      setLoadingParams(false);
      setLoadingVggtParams(false);
    }
  };

  // Download overlay image
  const handleDownloadOverlay = () => {
    const url = overlaySource === 'virtual_gt'
      ? virtualGtOverlayUrl
      : overlayResultType === 'amc'
        ? overlayImageUrl
        : vggtOverlayImageUrl;
    if (!url) return;

    const link = document.createElement('a');
    link.href = url;
    const suffix = overlaySource === 'virtual_gt'
      ? `virtual_gt_cam_${virtualGtCamera.toString().padStart(2, '0')}`
      : 'trajectory';
    link.download = `${project.project_name}_${overlayResultType}_${suffix}_overlay.png`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showSuccess(`${overlayResultType.toUpperCase()} overlay image downloaded`);
  };

  // Load evaluation metrics (layout image)
  const loadEvaluationMetrics = async () => {
    try {
      setLoadingEvaluation(true);
      const blob = await api.calibration.getEvaluationLayout(project.project_id);
      const url = URL.createObjectURL(blob);
      setEvaluationImageUrl(url);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to load evaluation metrics';
      console.error(errorMessage);
      // Don't show error to user, evaluation might not be available
    } finally {
      setLoadingEvaluation(false);
    }
  };

  // Load evaluation statistics
  const loadEvaluationStatistics = async () => {
    try {
      const response = await api.calibration.getEvaluationStatistics(project.project_id) as any;
      if (response.statistics) {
        setEvaluationStats(response.statistics);
      }
    } catch (error: unknown) {
      console.error('Failed to load evaluation statistics');
      // Don't show error to user, statistics might not be available
    }
  };

  // Load VGGT evaluation metrics (layout image)
  const loadVggtEvaluationMetrics = async () => {
    try {
      setLoadingEvaluation(true);
      const blob = await api.calibration.getVggtEvaluationLayout(project.project_id);
      const url = URL.createObjectURL(blob);
      setVggtEvaluationImageUrl(url);
    } catch (error: unknown) {
      console.error('Failed to load VGGT evaluation metrics');
      // Don't show error to user, evaluation might not be available
    } finally {
      setLoadingEvaluation(false);
    }
  };

  // Load VGGT evaluation statistics
  const loadVggtEvaluationStatistics = async () => {
    try {
      const response = await api.calibration.getVggtEvaluationStatistics(project.project_id) as any;
      if (response.statistics) {
        setVggtEvaluationStats(response.statistics);
      }
    } catch (error: unknown) {
      console.error('Failed to load VGGT evaluation statistics');
      // Don't show error to user, statistics might not be available
    }
  };

  // Download MV3DT result (supports both AMC and VGGT)
  const handleDownloadMv3dt = async (resultType: 'amc' | 'vggt' = 'amc') => {
    const isVggt = resultType === 'vggt';
    const setLoading = isVggt ? setDownloadingVggtMv3dt : setDownloadingMv3dt;
    
    try {
      setLoading(true);
      const blob = await api.calibration.getMv3dtResult(project.project_id, resultType);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      const suffix = isVggt ? '_vggt' : '';
      link.download = `${project.project_name}${suffix}_mv3dt.zip`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      showSuccess(`${resultType.toUpperCase()} MV3DT results downloaded successfully`);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || `Failed to download ${resultType.toUpperCase()} MV3DT results`;
      showError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const fetchExportedCalibrationText = useCallback(
    (rt: 'amc' | 'vggt'): Promise<string> => annotationExports.getExport(rt),
    [annotationExports],
  );

  const applyExportFormMergesToRawText = useCallback(
    (
      textRaw: string,
      resultType: 'amc' | 'vggt',
    ): { text: string; appliedMerge: boolean } => {
      const metadataLoaded =
        exportMetaEdits !== null ||
        exportGlobalRoiEdits !== null ||
        exportGlobalTwEdits !== null;
      const shouldMerge = metadataLoaded && exportMetaKind === resultType;
      if (!shouldMerge) {
        return { text: textRaw, appliedMerge: false };
      }
      try {
        const doc = JSON.parse(textRaw) as Record<string, unknown>;
        let merged = doc;
        if (exportMetaEdits !== null) {
          merged = mergeSensorMetaIntoExport(merged, exportMetaEdits);
        }
        if (exportGlobalRoiEdits !== null) {
          merged = mergeGlobalRootRoiMetaIntoExport(merged, exportGlobalRoiEdits);
        }
        if (exportGlobalTwEdits !== null) {
          merged = mergeGlobalRootTripwireMetaIntoExport(merged, exportGlobalTwEdits);
        }
        return { text: JSON.stringify(merged, null, 2), appliedMerge: true };
      } catch {
        return { text: textRaw, appliedMerge: false };
      }
    },
    [exportMetaEdits, exportGlobalRoiEdits, exportGlobalTwEdits, exportMetaKind],
  );

  /** Re-hydrate the structured metadata fields so they reflect the given export document. */
  const syncExportFormFromText = useCallback((text: string): boolean => {
    try {
      const doc = JSON.parse(text) as Parameters<typeof extractSensorMetaEdits>[0];
      setExportMetaEdits(extractSensorMetaEdits(doc));
      setExportGlobalRoiEdits(
        extractGlobalRootRoiMetaEdits(doc as Parameters<typeof extractGlobalRootRoiMetaEdits>[0]),
      );
      setExportGlobalTwEdits(
        extractGlobalRootTripwireMetaEdits(
          doc as Parameters<typeof extractGlobalRootTripwireMetaEdits>[0],
        ),
      );
      setExportMetaSensorLabels(
        (doc as { sensors?: Array<{ id?: string }> }).sensors?.map((s) => String(s.id ?? '')) ?? [],
      );
      return true;
    } catch {
      return false;
    }
  }, []);

  const openFullExportManualEditor = useCallback(async () => {
    const rt = exportMetaKind;
    setFullExportManualLoading(true);
    try {
      const saved = fullExportManualSavedByType[rt];
      const textRaw = saved ?? (await fetchExportedCalibrationText(rt));
      const { text } = applyExportFormMergesToRawText(textRaw, rt);
      fullExportManualBaselineRef.current = text;
      setFullExportManualParseError(null);
      setFullExportManualText(text);
      setFullExportManualOpen(true);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to load export for manual edit');
    } finally {
      setFullExportManualLoading(false);
    }
  }, [
    exportMetaKind,
    fullExportManualSavedByType,
    fetchExportedCalibrationText,
    applyExportFormMergesToRawText,
    showError,
  ]);

  /**
   * Keeps the manual JSON as the working base for this dialog session so structured-field edits
   * made after closing the editor build on top of it instead of a fresh server document.
   */
  const handleSaveManualJson = useCallback(async () => {
    const parsed = tryParseJson(fullExportManualText);
    if (!parsed.ok) {
      const { line, column, message } = parsed.error;
      setFullExportManualParseError(parsed.error);
      showError(`Cannot save: invalid JSON at line ${line}, column ${column} — ${message}`);
      return;
    }
    const text = JSON.stringify(parsed.value, null, 2);
    setFullExportManualParseError(null);
    setFullExportManualText(text);
    setFullExportManualSaving(true);
    try {
      await api.calibration.saveExportedCalibration(
        project.project_id,
        exportMetaKind,
        text,
        'cartesian',
      );
      fullExportManualBaselineRef.current = text;
      setFullExportManualSavedByType((prev) => ({ ...prev, [exportMetaKind]: text }));
      setExportDraftByType((prev) => ({ ...prev, [exportMetaKind]: text }));
      syncExportFormFromText(text);
      if (exportMetaKind === 'amc') setExportExists(true);
      else setVggtExportExists(true);
      showSuccess(
        `Manual JSON saved to the project ${exportMetaKind.toUpperCase()} export file.`,
      );
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to save manual JSON');
    } finally {
      setFullExportManualSaving(false);
    }
  }, [
    fullExportManualText,
    exportMetaKind,
    project.project_id,
    syncExportFormFromText,
    showError,
    showSuccess,
  ]);

  const closeFullExportManualEditor = useCallback(() => {
    const hasUnsavedEdits = fullExportManualText !== fullExportManualBaselineRef.current;
    if (
      hasUnsavedEdits &&
      !confirm('Close the manual editor and discard unsaved JSON edits? Use "Save JSON" to keep them.')
    ) {
      return;
    }
    setFullExportManualOpen(false);
    setFullExportManualText('');
    setFullExportManualParseError(null);
  }, [fullExportManualText]);

  const loadExportMetadataForEdit = useCallback(
    async (kind: 'amc' | 'vggt') => {
      try {
        setExportMetaLoading(true);
        const text = await fetchExportedCalibrationText(kind);
        const doc = JSON.parse(text) as Parameters<typeof extractSensorMetaEdits>[0];
        const labels =
          (doc as { sensors?: Array<{ id?: string }> }).sensors?.map((s) => String(s.id ?? '')) ?? [];
        setExportMetaSensorLabels(labels);
        setExportMetaEdits(extractSensorMetaEdits(doc));
        setExportGlobalRoiEdits(
          extractGlobalRootRoiMetaEdits(doc as Parameters<typeof extractGlobalRootRoiMetaEdits>[0]),
        );
        setExportGlobalTwEdits(
          extractGlobalRootTripwireMetaEdits(
            doc as Parameters<typeof extractGlobalRootTripwireMetaEdits>[0],
          ),
        );
        setExportMetaKind(kind);
      } catch (error: unknown) {
        const apiError = error as { message?: string; detail?: string };
        showError(apiError.detail || apiError.message || 'Failed to load export for metadata');
      } finally {
        setExportMetaLoading(false);
      }
    },
    [fetchExportedCalibrationText, showError],
  );

  /**
   * Regenerates the active AMC/VGGT export from calibration artifacts, overwriting any
   * Save JSON / structured-metadata edits on disk and clearing dialog edit state.
   */
  const handleResetExportToOriginal = useCallback(async () => {
    const kind = exportMetaKind;
    setResettingFullExport(true);
    try {
      await api.calibration.exportCalibration(project.project_id, kind, 'cartesian');

      setFullExportManualOpen(false);
      setFullExportManualText('');
      setFullExportManualParseError(null);
      fullExportManualBaselineRef.current = '';
      setFullExportManualSavedByType((prev) => {
        const next = { ...prev };
        delete next[kind];
        return next;
      });
      setExportDraftByType((prev) => {
        const next = { ...prev };
        delete next[kind];
        return next;
      });
      exportDraftLoadedRef.current.delete(kind);

      // Load the regenerated file (download after POST so we do not keep a stale edited copy).
      const text = await api.calibration
        .downloadExportedCalibration(project.project_id, kind, 'cartesian', 'exported')
        .then((blob) => blob.text());
      syncExportFormFromText(text);
      setExportDraftByType((prev) => ({ ...prev, [kind]: text }));
      exportDraftLoadedRef.current.add(kind);
      if (kind === 'amc') setExportExists(true);
      else setVggtExportExists(true);

      showSuccess(
        `${kind.toUpperCase()} export reset to the original generated calibration JSON.`,
      );
      setResetExportConfirmOpen(false);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(
        apiError.detail || apiError.message || 'Failed to reset export to original calibration',
      );
    } finally {
      setResettingFullExport(false);
    }
  }, [
    exportMetaKind,
    project.project_id,
    syncExportFormFromText,
    showError,
    showSuccess,
  ]);

  // Load when the dialog opens only (not when toggling AMC/VGGT): optional fields are shared, so edits stay visible for both.
  useEffect(() => {
    if (!fullExportDialogOpen) return;
    void loadExportMetadataForEdit(exportMetaKind);
    // exportMetaKind intentionally omitted: shared metadata edits apply to both AMC and VGGT downloads
  }, [fullExportDialogOpen, loadExportMetadataForEdit]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Close manual buffer when the dialog or AMC/VGGT target changes (different file or fresh open). */
  useEffect(() => {
    setFullExportManualOpen(false);
    setFullExportManualText('');
  }, [exportMetaKind, fullExportDialogOpen]);

  /** Saved manual JSON is a per-dialog-session working base; drop it when the dialog opens or closes. */
  useEffect(() => {
    setFullExportManualSavedByType({});
    setFullExportManualParseError(null);
  }, [fullExportDialogOpen]);

  const openFullExportDialog = () => {
    const amcOk = amcExportReady;
    const vggtOk = vggtSelectable && vggtExportReady;
    setExportMetaKind(amcOk ? 'amc' : vggtOk ? 'vggt' : 'amc');
    setFullExportDialogOpen(true);
  };

  /** Fetches export JSON, merges dialog edits, saves to every applicable *_exported*.json, then downloads the active type. */
  const handleFullExportDownload = async (resultType: 'amc' | 'vggt'): Promise<boolean> => {
    try {
      setDownloadingFullExport(resultType);
      const fresh = await fetchExportedCalibrationText(resultType);
      const fileName = 'calibration.json';
      const savedManual = fullExportManualSavedByType[resultType];
      const verificationDraft = exportDraftByType[resultType];
      const usedVerificationDraft = savedManual === undefined && verificationDraft !== undefined;
      // Saved manual JSON has precedence, followed by the verification draft so
      // global ROI/tripwire checkbox assignments are included in Full Export.
      const textRaw =
        savedManual ??
        (verificationDraft ? mergeFreshAnnotationGeometry(fresh, verificationDraft) : fresh);

      let text: string;
      let mergedForSave: boolean;
      if (fullExportManualOpen) {
        const parsedManual = tryParseJson(fullExportManualText);
        if (!parsedManual.ok) {
          const { line, column, message } = parsedManual.error;
          setFullExportManualParseError(parsedManual.error);
          showError(`Cannot download: invalid JSON at line ${line}, column ${column} — ${message}`);
          return false;
        }
        text = JSON.stringify(parsedManual.value, null, 2);
        mergedForSave = true;
      } else {
        const { text: mergedText, appliedMerge } = applyExportFormMergesToRawText(
          textRaw,
          resultType,
        );
        text = mergedText;
        mergedForSave = appliedMerge || savedManual !== undefined || usedVerificationDraft;
      }

      let saveSummary: { saved: number; total: number } | null = null;
      if (mergedForSave) {
        const saveTargets: ('amc' | 'vggt')[] = [];
        if (amcExportReady) saveTargets.push('amc');
        if (vggtSelectable && vggtExportReady) {
          saveTargets.push('vggt');
        }

        const saveErrors: string[] = [];
        let savedCount = 0;
        for (const target of saveTargets) {
          try {
            const targetFresh = target === resultType ? fresh : await fetchExportedCalibrationText(target);
            const raw =
              target === resultType
                ? textRaw
                : fullExportManualSavedByType[target] ??
                  (exportDraftByType[target]
                    ? mergeFreshAnnotationGeometry(targetFresh, exportDraftByType[target])
                    : targetFresh);
            let mergedDoc = JSON.parse(raw) as Record<string, unknown>;
            if (exportMetaEdits !== null) {
              mergedDoc = mergeSensorMetaIntoExport(mergedDoc, exportMetaEdits);
            }
            if (exportGlobalRoiEdits !== null) {
              mergedDoc = mergeGlobalRootRoiMetaIntoExport(mergedDoc, exportGlobalRoiEdits);
            }
            if (exportGlobalTwEdits !== null) {
              mergedDoc = mergeGlobalRootTripwireMetaIntoExport(mergedDoc, exportGlobalTwEdits);
            }
            const body = target === resultType ? text : JSON.stringify(mergedDoc, null, 2);
            await api.calibration.saveExportedCalibration(
              project.project_id,
              target,
              body,
              'cartesian',
            );
            savedCount += 1;
          } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : String(e);
            saveErrors.push(`${target.toUpperCase()}: ${msg}`);
          }
        }

        if (saveErrors.length > 0) {
          showError(`Some export files could not be updated: ${saveErrors.join('; ')}`);
        }
        saveSummary = { saved: savedCount, total: saveTargets.length };
      }

      const downloadBlob = new Blob([text], { type: 'application/json' });
      const url = URL.createObjectURL(downloadBlob);
      const link = document.createElement('a');
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);

      if (amcExportReady) setExportExists(true);
      if (vggtSelectable && vggtExportReady) {
        setVggtExportExists(true);
      }
      setExportDraftByType((prev) => ({ ...prev, [resultType]: text }));

      // Keep Full Export form in sync with what was saved/downloaded (avoids stale React state
      // and complements cache: 'no-store' on re-fetch after dialog reopens).
      if (mergedForSave) {
        syncExportFormFromText(text);
        setFullExportManualSavedByType((prev) => ({ ...prev, [resultType]: text }));
      }

      if (mergedForSave && saveSummary && saveSummary.total > 0) {
        const { saved, total } = saveSummary;
        if (saved === total && total > 1) {
          showSuccess(`Saved metadata to AMC and VGGT exports and downloaded ${fileName}`);
        } else if (saved === total && total === 1) {
          showSuccess(`Saved metadata to project export and downloaded ${fileName}`);
        } else if (saved > 0) {
          showSuccess(`Downloaded ${fileName} (${saved}/${total} export files updated)`);
        } else {
          showSuccess(`Downloaded ${fileName}`);
        }
      } else {
        showSuccess(`Downloaded ${fileName}`);
      }
      return true;
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to export calibration');
      return false;
    } finally {
      setDownloadingFullExport(null);
    }
  };

  // Delete calibration results
  const handleDeleteResults = async () => {
    if (!confirm('Are you sure you want to delete all calibration results? This action cannot be undone.')) {
      return;
    }

    try {
      setDeletingResults(true);
      await api.calibration.deleteResult(project.project_id);
      showSuccess('Calibration results deleted successfully');
      
      // Clear local state
      setOverlayImageUrl('');
      setVggtOverlayImageUrl('');
      setEvaluationImageUrl('');
      setEvaluationStats(null);
      setVggtEvaluationImageUrl('');
      setVggtEvaluationStats(null);
      setCameraParameters({});
      setVggtCameraParameters({});
      setRoiBevImageUrl('');
      setVggtRoiBevImageUrl('');
      setExportExists(false);
      setVggtExportExists(false);
      exportDraftLoadedRef.current = new Set();
      setExportDraftByType({});
      setExportMetaEdits(null);
      setExportGlobalRoiEdits(null);
      setExportGlobalTwEdits(null);
      setExportMetaSensorLabels([]);
      setFullExportDialogOpen(false);

      // Refresh project state to reflect READY status
      await refreshProjectState();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to delete results';
      showError(errorMessage);
    } finally {
      setDeletingResults(false);
    }
  };

  // Refresh project state from backend
  const refreshProjectState = async () => {
    try {
      const response = await api.project.getProjectInfo(project.project_id);
      if (onProjectUpdate && response.project_info) {
        onProjectUpdate(response.project_info);
      }
    } catch (error) {
      console.error('Failed to refresh project state:', error);
    }
  };

  // Check if export file exists (both AMC and VGGT)
  const checkExportFileExists = async () => {
    // Check AMC export
    try {
      const response = await api.calibration.checkExportExists(project.project_id) as any;
      setExportExists(response.exists || false);
    } catch (error) {
      setExportExists(false);
    }
    
    // Check VGGT export by trying to download it
    if (project.vggt_state === 'COMPLETED') {
      try {
        await api.calibration.downloadExportedCalibration(project.project_id, 'vggt');
        setVggtExportExists(true);
      } catch (error) {
        setVggtExportExists(false);
      }
    }
  };

  // Load video files list
  const loadVideoFiles = async () => {
    try {
      const response = await api.data.listVideoFiles(project.project_id);
      setVideoFiles(response.video_files);
    } catch (error: unknown) {
      console.error('Failed to load video files');
    }
  };

  // Load video frame (uses rectified video for ROI verification)
  const loadVideoFrame = async () => {
    if (!selectedVideo) return;

    try {
      setLoadingFrame(true);
      const blob = await api.data.getVideoFrame(project.project_id, selectedVideo, 0, true);  // use_rectified=true
      const url = URL.createObjectURL(blob);
      
      if (frameImageUrl) {
        URL.revokeObjectURL(frameImageUrl);
      }
      
      setFrameImageUrl(url);
    } catch (error: unknown) {
      console.error('Failed to load video frame');
    } finally {
      setLoadingFrame(false);
    }
  };

  // Load annotations for selected video (uses alignment_data annotations for ROI verification)
  const loadVideoAnnotations = async () => {
    if (!selectedVideo) return;

    try {
      const response = await api.data.getAnnotations(
        project.project_id,
        selectedVideo,
        true, // use_alignment_data
        true, // include_global_projection
        bevResultType, // project globals with AMC or VGGT matrices
      );
      if (response.annotations) {
        setVideoAnnotations({
          rois: response.annotations.rois || [],
          tripwireLines: response.annotations.tripwireLines || [],
          tripwireDirections: response.annotations.tripwireDirections || [],
        });
      }
    } catch (error: unknown) {
      console.error('Failed to load annotations');
      setVideoAnnotations({ rois: [], tripwireLines: [], tripwireDirections: [] });
    }
  };

  // Generate ROI/Tripwire BEV visualization
  const handleGenerateRoiBev = async (resultType: 'amc' | 'vggt' = 'amc') => {
    try {
      setGeneratingRoiBev(true);
      const blob = await api.calibration.generateRoiBev(project.project_id, resultType);
      const url = URL.createObjectURL(blob);
      
      if (resultType === 'vggt') {
        setVggtRoiBevImageUrl(url);
      } else {
        setRoiBevImageUrl(url);
      }
      
      setShowingRoiBev(true);
      setRoiVerificationTarget('camera');
      setBevZoom(1);
      setBevPan({ x: 0, y: 0 });
      showSuccess(`${resultType.toUpperCase()} ROI/Tripwire visualization generated`);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || `Failed to generate ${resultType.toUpperCase()} ROI BEV`;
      showError(errorMessage);
    } finally {
      setGeneratingRoiBev(false);
    }
  };

  // BEV zoom handlers
  const handleBevWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setBevZoom(prev => Math.max(0.5, Math.min(5, prev * delta)));
  };

  const handleBevMouseDown = (e: React.MouseEvent) => {
    setIsPanning(true);
    setPanStart({ x: e.clientX - bevPan.x, y: e.clientY - bevPan.y });
  };

  const handleBevMouseMove = (e: React.MouseEvent) => {
    if (!isPanning) return;
    setBevPan({ x: e.clientX - panStart.x, y: e.clientY - panStart.y });
  };

  const handleBevMouseUp = () => {
    setIsPanning(false);
  };

  const handleBevResetZoom = () => {
    setBevZoom(1);
    setBevPan({ x: 0, y: 0 });
  };

  const handleCloseRoiVerification = () => {
    setShowingRoiBev(false);
    setRoiVerificationTarget('camera');
    exportDraftLoadedRef.current = new Set();
    setExportDraftByType({});
    setExportDraftLoading(false);
    if (globalLayoutImageUrl) {
      URL.revokeObjectURL(globalLayoutImageUrl);
      setGlobalLayoutImageUrl('');
    }
    setGlobalVerificationAnnotations({
      rois: [],
      tripwireLines: [],
      tripwireDirections: [],
    });
  };

  const hasCalibrationResults = project.amc_state === 'COMPLETED' || project.vggt_state === 'COMPLETED';
  // Pixel-space annotation/export only needs staged linear media, not calibration.
  const annotationEditor = (hasCalibrationResults || project.rectification_state === 'COMPLETED') && project.video_files_count > 0 && (
    <Card sx={{ mt: 3, mb: 3 }}>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          ROI & Tripwire Drawing (Optional)
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Draw on <strong>linear camera frames</strong> or the layout map. Annotations save
          automatically. Image-mode JSON export uses pixel coordinates and does not require
          AMC or VGGT calibration. World-coordinate verification and export require calibration
          and layout post-processing.
        </Typography>
        <AnnotationEditor
          project={project}
          saveAnnotation={(key, data) => annotationExports.saveAnnotations(key, () => api.data.saveAnnotations(
            project.project_id, key, data.rois || [], data.tripwireLines || [], data.tripwireDirections || [],
          ))}
          beforeExport={() => annotationExports.waitForSaves()}
          locked={
            project.project_state === 'RUNNING' ||
            project.amc_state === 'RUNNING' ||
            project.vggt_state === 'RUNNING'
          }
          onAnnotationsChanged={() => {
            void loadVideoAnnotations();
            if (project.layout_file && showingRoiBev && roiVerificationTarget === 'global') {
              void api.data
                .getAnnotations(project.project_id, GLOBAL_ROIS_TRIPWIRES_KEY, true)
                .then((response) => {
                  if (response.annotations) {
                    setGlobalVerificationAnnotations({
                      rois: response.annotations.rois || [],
                      tripwireLines: response.annotations.tripwireLines || [],
                      tripwireDirections: response.annotations.tripwireDirections || [],
                    });
                  }
                })
                .catch(() => undefined);
            }
          }}
        />
      </CardContent>
    </Card>
  );

  return (
    <Box>
      {/* Header */}
      <Typography variant="h5" gutterBottom>
        Results & Annotations
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Project: <strong>{project.project_name}</strong>
      </Typography>

      {!hasCalibrationResults && annotationEditor}

      {/* Show results whenever at least one pipeline has completed.
          Aggregate project_state may still be RUNNING (e.g. VGGT running after
          AMC completed) — that must not hide already-finished results. */}
      {!hasCalibrationResults ? (
        <Alert 
          severity={
            project.amc_state === 'RUNNING' || project.vggt_state === 'RUNNING' || project.project_state === 'RUNNING' ? 'info' :
            project.amc_state === 'ERROR' || project.vggt_state === 'ERROR' || project.project_state === 'ERROR' ? 'error' :
            'warning'
          }
        >
          <Typography variant="subtitle2" gutterBottom>
            {(project.amc_state === 'RUNNING' || project.vggt_state === 'RUNNING' || project.project_state === 'RUNNING') && 'Calibration is still running'}
            {(project.amc_state === 'ERROR' || project.vggt_state === 'ERROR' || project.project_state === 'ERROR') &&
              !(project.amc_state === 'RUNNING' || project.vggt_state === 'RUNNING') && 'Calibration failed'}
            {(project.project_state === 'INIT' || project.project_state === 'READY') && 'Calibration results are not available yet'}
          </Typography>
          <Typography variant="body2">
            {(project.amc_state === 'RUNNING' || project.vggt_state === 'RUNNING' || project.project_state === 'RUNNING') &&
              'Please wait for calibration to complete. You can check back in the Execute step.'}
            {(project.amc_state === 'ERROR' || project.vggt_state === 'ERROR' || project.project_state === 'ERROR') &&
              !(project.amc_state === 'RUNNING' || project.vggt_state === 'RUNNING') &&
              'Please check your input files and try again from the Execute step.'}
            {(project.project_state === 'INIT' || project.project_state === 'READY') && (
              project.rectification_state === 'COMPLETED'
                ? 'Use the editor above to draw and export image-coordinate annotations. For world-coordinate results, run calibration in the Execute step.'
                : 'Upload camera videos and complete Rectification, or choose Videos Are Already Linear, to draw and export image-coordinate annotations.'
            )}
          </Typography>
        </Alert>
      ) : (
        <>
          {(project.amc_state === 'RUNNING' || project.vggt_state === 'RUNNING') && (
            <Alert severity="info" sx={{ mb: 2 }}>
              {project.vggt_state === 'RUNNING' && project.amc_state === 'COMPLETED'
                ? 'VGGT is running. Completed AMC results remain available below.'
                : project.amc_state === 'RUNNING' && project.vggt_state === 'COMPLETED'
                  ? 'AMC is running. Completed VGGT results remain available below.'
                  : 'Calibration is running. Return to Execute for live progress; completed results stay available here.'}
            </Alert>
          )}
          {isSingleCam && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Single-camera project. VGGT refinement and MV3DT export are available only for multi-camera calibration.
            </Alert>
          )}
          {/* Overlay Image Section */}
          <Card sx={{ mb: 3 }}>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Typography variant="h6">
                  Overlay Image
                </Typography>
                <Button
                  variant="outlined"
                  startIcon={<DownloadIcon />}
                  onClick={handleDownloadOverlay}
                  disabled={
                    overlaySource === 'virtual_gt'
                      ? !virtualGtOverlayUrl
                      : overlayResultType === 'amc'
                        ? !overlayImageUrl
                        : !vggtOverlayImageUrl
                  }
                  size="small"
                >
                  Download
                </Button>
              </Box>

              <ToggleButtonGroup
                value={overlaySource}
                exclusive
                onChange={(_, value: 'trajectory' | 'virtual_gt' | null) => {
                  if (value) setOverlaySource(value);
                }}
                size="small"
                sx={{ mb: 2 }}
              >
                <ToggleButton value="trajectory">Trajectory Overlay</ToggleButton>
                <ToggleButton value="virtual_gt">GT Virtual Objects</ToggleButton>
              </ToggleButtonGroup>

              {overlaySource === 'virtual_gt' ? (
                <Stack spacing={2}>
                  {/* Calibration method first, then per-camera selection. */}
                  <Tabs
                    value={overlayResultType}
                    onChange={(_: React.SyntheticEvent, v: 'amc' | 'vggt') => setOverlayResultType(v)}
                    sx={{ borderBottom: 1, borderColor: 'divider' }}
                  >
                    <Tab label="AMC Result" value="amc" disabled={project.amc_state !== 'COMPLETED'} />
                    {vggtAvailable && !isSingleCam && (
                      <Tab
                        label="VGGT Result"
                        value="vggt"
                        disabled={project.vggt_state !== 'COMPLETED'}
                      />
                    )}
                  </Tabs>

                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
                    <FormControl size="small" sx={{ maxWidth: 240, minWidth: 160 }}>
                      <InputLabel>Camera</InputLabel>
                      <Select
                        value={virtualGtCamera}
                        label="Camera"
                        onChange={event => setVirtualGtCamera(Number(event.target.value))}
                      >
                        {Array.from({ length: project.video_files_count }, (_, cameraId) => {
                          const camId = `cam_${cameraId.toString().padStart(2, '0')}`;
                          const displayName = project.camera_name_map?.[camId] || `Camera ${cameraId}`;
                          return (
                            <MenuItem key={cameraId} value={cameraId}>
                              {displayName}
                            </MenuItem>
                          );
                        })}
                      </Select>
                    </FormControl>
                    <Typography variant="body2" color="text.secondary">
                      Camera frame with calibrated floor grid (
                      <Box component="span" sx={{ color: 'success.main', fontWeight: 600 }}>green</Box>
                      , {virtualGtGridSpacing} m spacing) and vertical poles (
                      <Box component="span" sx={{ color: 'error.main', fontWeight: 600 }}>red</Box>, configured height{' '}
                      {virtualGtPersonHeight} m
                      ) at each grid intersection, for visual calibration check
                    </Typography>
                  </Stack>
                  {loadingVirtualGtOverlay ? (
                    <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                      <CircularProgress />
                    </Box>
                  ) : virtualGtOverlayUrl ? (
                    <Paper sx={{ p: 2, bgcolor: 'grey.100' }}>
                      <img
                        src={virtualGtOverlayUrl}
                        alt={`${overlayResultType.toUpperCase()} virtual-GT overlay camera ${virtualGtCamera}`}
                        style={{ width: '100%', maxHeight: 500, objectFit: 'contain' }}
                      />
                    </Paper>
                  ) : (
                    <Alert severity="info">
                      {overlayResultType.toUpperCase()} GT virtual-object overlay for camera{' '}
                      {virtualGtCamera} is not available yet. The visualizer integration will
                      populate this per-camera view.
                    </Alert>
                  )}
                </Stack>
              ) : (
                <>
              {/* Trajectory overlay: AMC / VGGT result selection */}
              <Tabs 
                value={overlayResultType} 
                onChange={(_: React.SyntheticEvent, v: 'amc' | 'vggt') => setOverlayResultType(v)} 
                sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
              >
                <Tab label="AMC Result" value="amc" disabled={project.amc_state !== 'COMPLETED'} />
                {vggtAvailable && !isSingleCam && (
                  <Tab 
                    label="VGGT Result" 
                    value="vggt" 
                    disabled={project.vggt_state !== 'COMPLETED'}
                  />
                )}
              </Tabs>

              {overlayResultType === 'amc' ? (
                loadingOverlay ? (
                  <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                    <CircularProgress />
                  </Box>
                ) : overlayImageUrl ? (
                  <Paper sx={{ p: 2, bgcolor: 'grey.100' }}>
                    <img
                      src={overlayImageUrl}
                      alt="AMC Calibration Overlay"
                      style={{
                        width: '100%',
                        maxHeight: 500,
                        objectFit: 'contain',
                      }}
                    />
                  </Paper>
                ) : (
                  <Alert severity="warning">
                    AMC overlay image not available
                  </Alert>
                )
              ) : (
                loadingVggtOverlay ? (
                  <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                    <CircularProgress />
                  </Box>
                ) : vggtOverlayImageUrl ? (
                  <Paper sx={{ p: 2, bgcolor: 'grey.100' }}>
                    <img
                      src={vggtOverlayImageUrl}
                      alt="VGGT Calibration Overlay"
                      style={{
                        width: '100%',
                        maxHeight: 500,
                        objectFit: 'contain',
                      }}
                    />
                  </Paper>
                ) : (
                  <Alert severity="warning">
                    VGGT overlay image not available
                  </Alert>
                )
              )}
                </>
              )}
            </CardContent>
          </Card>

          {/* Evaluation Metrics Section (single- and multi-camera) */}
          {project.groundtruth_file && (
            <Card sx={{ mb: 3 }}>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  <AssessmentIcon sx={{ mr: 1, verticalAlign: 'middle' }} />
                  Evaluation Metrics
                </Typography>
                <Typography variant="body2" color="text.secondary" paragraph>
                  3D points layout visualization showing calibration accuracy against ground truth data.
                </Typography>

                {/* Result Type Selection */}
                    <Tabs
                      value={evaluationResultType}
                      onChange={(_, newValue) => setEvaluationResultType(newValue)}
                      sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
                    >
                      <Tab label="AMC Results" value="amc" disabled={project.amc_state !== 'COMPLETED'} />
                      {vggtAvailable && !isSingleCam && (
                        <Tab 
                          label="VGGT Results" 
                          value="vggt" 
                          disabled={project.vggt_state !== 'COMPLETED'}
                        />
                      )}
                    </Tabs>

                {/* AMC Evaluation */}
                {evaluationResultType === 'amc' && (
                  <>
                    {/* AMC Statistics */}
                    {evaluationStats && (
                      <Paper sx={{ p: 2, mb: 2, bgcolor: 'success.50' }}>
                        <Typography variant="subtitle2" gutterBottom>
                          AMC - L2 Distance Statistics (in m):
                        </Typography>
                        <Grid container spacing={2}>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Average</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {evaluationStats['Average L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Std Dev</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {evaluationStats['Standard deviation of L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Max</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {evaluationStats['Max L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Min</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {evaluationStats['Min L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                        </Grid>
                      </Paper>
                    )}

                    {loadingEvaluation ? (
                      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                        <CircularProgress />
                      </Box>
                    ) : evaluationImageUrl ? (
                      <Paper sx={{ p: 2, bgcolor: 'grey.100' }}>
                        <img
                          src={evaluationImageUrl}
                          alt="AMC Evaluation Metrics"
                          style={{
                            width: '100%',
                            maxHeight: 500,
                            objectFit: 'contain',
                          }}
                        />
                      </Paper>
                    ) : (
                      <Alert severity="info">
                        AMC evaluation metrics not available
                      </Alert>
                    )}
                  </>
                )}

                {/* VGGT Evaluation (multi-camera only) */}
                {!isSingleCam && evaluationResultType === 'vggt' && (
                  <>
                    {/* VGGT Statistics */}
                    {vggtEvaluationStats && (
                      <Paper sx={{ p: 2, mb: 2, bgcolor: 'info.50' }}>
                        <Typography variant="subtitle2" gutterBottom>
                          VGGT - L2 Distance Statistics (in m):
                        </Typography>
                        <Grid container spacing={2}>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Average</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {vggtEvaluationStats['Average L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Std Dev</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {vggtEvaluationStats['Standard deviation of L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Max</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {vggtEvaluationStats['Max L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                          <Grid item xs={6} sm={3}>
                            <Typography variant="caption" color="text.secondary">Min</Typography>
                            <Typography variant="body1" fontWeight="bold">
                              {vggtEvaluationStats['Min L2 distance(m)'] || 'N/A'}
                            </Typography>
                          </Grid>
                        </Grid>
                      </Paper>
                    )}

                    {loadingEvaluation ? (
                      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                        <CircularProgress />
                      </Box>
                    ) : vggtEvaluationImageUrl ? (
                      <Paper sx={{ p: 2, bgcolor: 'grey.100' }}>
                        <img
                          src={vggtEvaluationImageUrl}
                          alt="VGGT Evaluation Metrics"
                          style={{
                            width: '100%',
                            maxHeight: 500,
                            objectFit: 'contain',
                          }}
                        />
                      </Paper>
                    ) : (
                      <Alert severity="info">
                        VGGT evaluation metrics not available
                      </Alert>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          )}

          {/* Camera Parameters Section */}
          <Card sx={{ mb: 3 }}>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Typography variant="h6">
                  Camera Parameters
                </Typography>
                <Button
                  variant="contained"
                  startIcon={<DataObjectIcon />}
                  onClick={handleExportData}
                  disabled={!cameraExportReady}
                  size="small"
                >
                  {`Export ${cameraResultType.toUpperCase()}`}
                </Button>
              </Box>

              {!cameraExportReady && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  {isSingleCam
                    ? project.amc_state === 'COMPLETED'
                      ? 'AMC is complete, but world-scaled camera parameters (camInfo_world.yaml) are missing. SV post-processing runs inside the AMC job when layout and alignment are present — re-run AMC with those inputs.'
                      : 'World-scaled camera parameters are not available yet.'
                    : cameraResultType === 'vggt' && project.vggt_state === 'COMPLETED'
                      ? 'VGGT calibration is complete. Run layout post-processing in the Execute step to generate world-scaled VGGT camera parameters before viewing or exporting them.'
                      : project.amc_state === 'COMPLETED'
                        ? 'AMC calibration is complete. Run layout post-processing in the Execute step to generate world-scaled AMC camera parameters before viewing or exporting them.'
                        : 'Complete calibration first, then run layout post-processing before exporting camera parameters.'}
                </Alert>
              )}

              {/* Result Type Tabs */}
              <Tabs 
                value={cameraResultType} 
                onChange={(_: React.SyntheticEvent, v: 'amc' | 'vggt') => setCameraResultType(v)} 
                sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
              >
                <Tab label="AMC Result" value="amc" disabled={project.amc_state !== 'COMPLETED'} />
                {vggtSelectable && (
                  <Tab 
                    label="VGGT Result" 
                    value="vggt" 
                    disabled={project.vggt_state !== 'COMPLETED'}
                  />
                )}
              </Tabs>

              {/* Camera Selection Tabs */}
              <Tabs value={selectedTab} onChange={(_: React.SyntheticEvent, v: number) => setSelectedTab(v)} sx={{ mb: 2 }}>
                {Array.from({ length: project.video_files_count }, (_: unknown, i: number) => {
                  const camId = `cam_${i.toString().padStart(2, '0')}`;
                  const displayName = project.camera_name_map?.[camId] || `Camera ${i}`;
                  return <Tab key={i} label={displayName} disabled={!cameraExportReady} />;
                })}
              </Tabs>

              {!cameraExportReady ? null : cameraResultType === 'amc' ? (
                loadingParams ? (
                  <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                    <CircularProgress />
                  </Box>
                ) : cameraParameters[selectedTab] ? (
                  <Paper sx={{ p: 2, bgcolor: 'grey.900', color: 'white', overflow: 'auto' }}>
                    <pre style={{ margin: 0, fontSize: '12px', fontFamily: 'monospace' }}>
                      {cameraParameters[selectedTab]}
                    </pre>
                  </Paper>
                ) : (
                  <Alert severity="info">
                    Click on a camera tab to load AMC parameters
                  </Alert>
                )
              ) : (
                loadingVggtParams ? (
                  <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                    <CircularProgress />
                  </Box>
                ) : vggtCameraParameters[selectedTab] ? (
                  <Paper sx={{ p: 2, bgcolor: 'grey.900', color: 'white', overflow: 'auto' }}>
                    <pre style={{ margin: 0, fontSize: '12px', fontFamily: 'monospace' }}>
                      {vggtCameraParameters[selectedTab]}
                    </pre>
                  </Paper>
                ) : (
                  <Alert severity="info">
                    Click on a camera tab to load VGGT parameters
                  </Alert>
                )
              )}
            </CardContent>
          </Card>

          {/* Preserve the calibrated flow: camera parameters, drawing, then verification. */}
          {annotationEditor}

          {/* ROI & Tripwire Verification Section */}
          <Card sx={{ mt: 3 }}>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Box>
                  <Typography variant="h6" gutterBottom>
                    <VerifiedIcon sx={{ mr: 1, verticalAlign: 'middle' }} />
                    ROI & Tripwire Verification
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Use <strong>Camera</strong> for rectified views per stream, or <strong>Global</strong> for layout
                    map pixels (left) and the same world-map BEV (right), including global ROIs/tripwires in purple.
                  </Typography>
                </Box>
                {showingRoiBev && (
                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={<CloseIcon />}
                    onClick={handleCloseRoiVerification}
                  >
                    Close
                  </Button>
                )}
              </Box>

              {!showingRoiBev ? (
                <Button
                  variant="contained"
                  color="primary"
                  startIcon={<VerifiedIcon />}
                  onClick={() =>
                    handleGenerateRoiBev(
                      isSingleCam ? 'amc' : project.amc_state === 'COMPLETED' ? 'amc' : 'vggt',
                    )
                  }
                  disabled={
                    generatingRoiBev ||
                    (isSingleCam
                      ? project.amc_state !== 'COMPLETED'
                      : project.amc_state !== 'COMPLETED' && project.vggt_state !== 'COMPLETED')
                  }
                  fullWidth
                  size="large"
                >
                  {generatingRoiBev ? 'Generating...' : 'Show ROI & Tripwire Verification'}
                </Button>
              ) : (
                <Box>
                  <ToggleButtonGroup
                    value={roiVerificationTarget}
                    exclusive
                    size="small"
                    sx={{ mb: 2 }}
                    onChange={(_, value) => {
                      if (value === null) return;
                      setRoiVerificationTarget(value);
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
                      Upload <strong>layout.png</strong> in Video Configuration to enable global verification on the
                      floor map.
                    </Alert>
                  )}

                  <Grid container spacing={3}>
                    <Grid item xs={12} md={6}>
                      <Paper sx={{ p: 2 }}>
                        {roiVerificationTarget === 'camera' ? (
                          <>
                            <Typography variant="subtitle1" gutterBottom fontWeight="bold">
                              Camera view (rectified)
                            </Typography>
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                              Projected global ROIs/tripwires follow the AMC/VGGT selection on the right.
                            </Typography>

                            <FormControl fullWidth sx={{ mb: 2 }}>
                              <InputLabel>Select Camera</InputLabel>
                              <Select
                                value={selectedVideo}
                                onChange={(e: { target: { value: string } }) => setSelectedVideo(e.target.value)}
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

                            {loadingFrame ? (
                              <Box
                                sx={{
                                  height: 400,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  bgcolor: 'action.hover',
                                  border: '2px solid',
                                  borderColor: 'grey.400',
                                }}
                              >
                                <CircularProgress />
                              </Box>
                            ) : frameImageUrl ? (
                              <Box
                                sx={{
                                  height: 400,
                                  width: '100%',
                                  border: '2px solid',
                                  borderColor: 'grey.400',
                                  position: 'relative',
                                  overflow: 'hidden',
                                }}
                              >
                                <AnnotationCanvas
                                  imageUrl={frameImageUrl}
                                  rois={videoAnnotations.rois}
                                  tripwires={[
                                    ...(videoAnnotations.tripwireLines || []),
                                    ...(videoAnnotations.tripwireDirections || []),
                                  ]}
                                  drawingMode="none"
                                  showAnnotations={true}
                                  onAddROIPoint={() => {}}
                                  onFinishROI={() => {}}
                                  onAddTripwirePoint={() => {}}
                                />
                              </Box>
                            ) : (
                              <Box
                                sx={{
                                  height: 400,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  bgcolor: 'action.hover',
                                  border: '2px solid',
                                  borderColor: 'grey.400',
                                }}
                              >
                                <Typography variant="body2" color="text.secondary">
                                  Select a camera to view
                                </Typography>
                              </Box>
                            )}

                            <GlobalExportSensorToggles
                              doc={verificationExportDoc}
                              sensorDisplayId={sensorDisplayIdForVerification}
                              loading={exportDraftLoading}
                              onToggleRoi={(id, checked) => applyGlobalSensorToggle('rois', id, checked)}
                              onToggleTripwire={(id, checked) =>
                                applyGlobalSensorToggle('tripwires', id, checked)
                              }
                            />

                            <Alert severity="info" sx={{ mt: 2 }} icon={false}>
                              <Typography variant="caption">
                                <strong>ROIs:</strong> Green polygons<br />
                                <strong>Tripwire lines:</strong> Red<br />
                                <strong>Tripwire directions:</strong> Yellow arrows <br />
                                <strong>Global ROIs:</strong> Purple Region <br />
                                <strong>Global Tripwires:</strong> Purple Lines
                              </Typography>
                            </Alert>
                          </>
                        ) : (
                          <>
                            <Typography variant="subtitle1" gutterBottom fontWeight="bold">
                              Layout map (pixel coordinates)
                            </Typography>
                            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                              Global ROIs and tripwires drawn above on the layout map. Right: world
                              projection on the map after export.
                            </Typography>

                            {loadingGlobalVerification ? (
                              <Box
                                sx={{
                                  height: 400,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  bgcolor: 'action.hover',
                                  border: '2px solid',
                                  borderColor: 'grey.400',
                                }}
                              >
                                <CircularProgress />
                              </Box>
                            ) : globalLayoutImageUrl ? (
                              <Box
                                sx={{
                                  height: 400,
                                  width: '100%',
                                  border: '2px solid',
                                  borderColor: 'grey.400',
                                  position: 'relative',
                                  overflow: 'hidden',
                                }}
                              >
                                <AnnotationCanvas
                                  imageUrl={globalLayoutImageUrl}
                                  rois={globalVerificationAnnotations.rois}
                                  tripwires={[
                                    ...(globalVerificationAnnotations.tripwireLines || []),
                                    ...(globalVerificationAnnotations.tripwireDirections || []),
                                  ]}
                                  drawingMode="none"
                                  showAnnotations={true}
                                  onAddROIPoint={() => {}}
                                  onFinishROI={() => {}}
                                  onAddTripwirePoint={() => {}}
                                />
                              </Box>
                            ) : (
                              <Box
                                sx={{
                                  height: 400,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  bgcolor: 'action.hover',
                                  border: '2px solid',
                                  borderColor: 'grey.400',
                                }}
                              >
                                <Typography variant="body2" color="text.secondary">
                                  Could not load layout image
                                </Typography>
                              </Box>
                            )}

                            <Alert severity="info" sx={{ mt: 2 }} icon={false}>
                              <Typography variant="caption">
                                Pixels on <strong>layout.png</strong>. Compare with the BEV on the right (world
                                coordinates).
                                <br />
                                <strong>ROIs:</strong> Green polygons<br />
                                <strong>Tripwire lines:</strong> Red<br />
                                <strong>Tripwire directions:</strong> Yellow arrows<br />
                              </Typography>
                            </Alert>
                          </>
                        )}
                      </Paper>
                    </Grid>

                    {/* Right: Bird's Eye View (world map — all cameras + global) */}
                    <Grid item xs={12} md={6}>
                      <Paper sx={{ p: 2 }}>
                        <Typography variant="subtitle1" fontWeight="bold" sx={{ mb: 1 }}>
                          World map (layout + projection)
                        </Typography>
                        
                        {/* Result Type Tabs */}
                        <Tabs
                          value={bevResultType}
                          onChange={(_, newValue: 'amc' | 'vggt') => {
                            setBevResultType(newValue);
                            if (newValue === 'vggt' && !vggtRoiBevImageUrl) {
                              handleGenerateRoiBev('vggt');
                            }
                          }}
                          sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
                        >
                          <Tab label="AMC" value="amc" disabled={project.amc_state !== 'COMPLETED'} />
                          {vggtAvailable && !isSingleCam && (
                            <Tab 
                              label="VGGT" 
                              value="vggt" 
                              disabled={project.vggt_state !== 'COMPLETED'}
                            />
                          )}
                        </Tabs>
                        
                        <Box sx={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', mb: 1 }}>
                          {/* Zoom Controls */}
                          <Box sx={{ display: 'flex', gap: 1 }}>
                            <Button
                              size="small"
                              variant="outlined"
                              onClick={() => setBevZoom(prev => Math.min(5, prev * 1.2))}
                              disabled={bevZoom >= 5}
                            >
                              <ZoomInIcon fontSize="small" />
                            </Button>
                            <Button
                              size="small"
                              variant="outlined"
                              onClick={() => setBevZoom(prev => Math.max(0.5, prev * 0.8))}
                              disabled={bevZoom <= 0.5}
                            >
                              <ZoomOutIcon fontSize="small" />
                            </Button>
                            <Button
                              size="small"
                              variant="outlined"
                              onClick={handleBevResetZoom}
                            >
                              <RestartAltIcon fontSize="small" />
                            </Button>
                          </Box>
                        </Box>

                        <Typography variant="caption" color="text.secondary" display="block" gutterBottom>
                          Zoom: {Math.round(bevZoom * 100)}% • Scroll to zoom, drag to pan
                        </Typography>

                        {(bevResultType === 'amc' ? roiBevImageUrl : vggtRoiBevImageUrl) ? (
                          <Box
                            sx={{
                              height: 400,
                              bgcolor: 'action.hover',
                              border: '2px solid',
                              borderColor: 'grey.400',
                              overflow: 'hidden',
                              position: 'relative',
                              cursor: isPanning ? 'grabbing' : 'grab',
                            }}
                            onWheel={handleBevWheel}
                            onMouseDown={handleBevMouseDown}
                            onMouseMove={handleBevMouseMove}
                            onMouseUp={handleBevMouseUp}
                            onMouseLeave={handleBevMouseUp}
                          >
                            <Box
                              sx={{
                                width: '100%',
                                height: '100%',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                              }}
                            >
                              <img
                                src={bevResultType === 'amc' ? roiBevImageUrl : vggtRoiBevImageUrl}
                                alt={`${bevResultType.toUpperCase()} ROI/Tripwire BEV Visualization`}
                                style={{
                                  maxWidth: '100%',
                                  maxHeight: '100%',
                                  objectFit: 'contain',
                                  transform: `scale(${bevZoom}) translate(${bevPan.x / bevZoom}px, ${bevPan.y / bevZoom}px)`,
                                  transition: isPanning ? 'none' : 'transform 0.1s',
                                  userSelect: 'none',
                                  pointerEvents: 'none',
                                }}
                                draggable={false}
                              />
                            </Box>
                          </Box>
                        ) : (
                          <Box
                            sx={{
                              height: 400,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              bgcolor: 'action.hover',
                            }}
                          >
                            <CircularProgress />
                          </Box>
                        )}

                      </Paper>
                    </Grid>
                  </Grid>
                </Box>
              )}
            </CardContent>
          </Card>

          {/* Export Section — after verification (projected JSON → full export) */}
          <Card sx={{ mt: 3 }}>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Export Calibration Data
              </Typography>
              <Typography variant="body2" color="text.secondary" paragraph>
                Export complete calibration data. <strong>Full Export</strong> opens a dialog first so you can add
                optional metadata (depth, FOV, direction, place hierarchy, ROI types) merged into the downloaded JSON,
                then download.
              </Typography>

              {(exportExists || vggtExportExists) && (
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                  On disk: {[exportExists && 'AMC', vggtExportExists && 'VGGT'].filter(Boolean).join(' · ')}
                </Typography>
              )}

              {(isSingleCam
                ? project.amc_state === 'COMPLETED' && !amcExportReady
                : (project.amc_state === 'COMPLETED' && !amcExportReady) ||
                  (vggtSelectable &&
                    project.vggt_state === 'COMPLETED' &&
                    !vggtExportReady)) && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  {isSingleCam
                    ? 'AMC is complete, but world-scaled camera parameters (camInfo_world.yaml) are missing. SV post-processing runs inside the AMC job when layout and alignment are present — re-run AMC with those inputs.'
                    : [
                        project.amc_state === 'COMPLETED' && !amcExportReady
                          ? 'AMC calibration is complete — run layout post-processing in the Execute step before exporting AMC results.'
                          : null,
                        vggtSelectable &&
                        project.vggt_state === 'COMPLETED' &&
                        !vggtExportReady
                          ? 'VGGT calibration is complete — run layout post-processing in the Execute step before exporting VGGT results.'
                          : null,
                      ]
                        .filter(Boolean)
                        .join(' ')}
                </Alert>
              )}

              <Grid container spacing={2}>
                <Grid item xs={12} sm={6} md={4}>
                  <Button
                    variant="contained"
                    color="success"
                    startIcon={
                      downloadingFullExport !== null ? (
                        <CircularProgress size={20} color="inherit" />
                      ) : (
                        <DataObjectIcon />
                      )
                    }
                    onClick={() => openFullExportDialog()}
                    disabled={downloadingFullExport !== null || !anyExportReady}
                    fullWidth
                    size="large"
                  >
                    {downloadingFullExport !== null ? 'Downloading…' : 'Full Export'}
                  </Button>
                </Grid>
                {!isSingleCam && (
                  <Grid item xs={12} sm={6} md={4}>
                    <Button
                      variant="outlined"
                      color="primary"
                      startIcon={<FolderZipIcon />}
                      onClick={() => handleDownloadMv3dt('amc')}
                      disabled={downloadingMv3dt || !amcExportReady}
                      fullWidth
                      size="large"
                    >
                      {downloadingMv3dt ? 'Downloading...' : 'MV3DT ZIP AMC'}
                    </Button>
                  </Grid>
                )}
                {vggtSelectable && project.vggt_state === 'COMPLETED' && (
                  <Grid item xs={12} sm={6} md={4}>
                    <Button
                      variant="outlined"
                      color="info"
                      startIcon={<FolderZipIcon />}
                      onClick={() => handleDownloadMv3dt('vggt')}
                      disabled={downloadingVggtMv3dt || !vggtExportReady}
                      fullWidth
                      size="large"
                    >
                      {downloadingVggtMv3dt ? 'Downloading...' : 'MV3DT ZIP VGGT'}
                    </Button>
                  </Grid>
                )}
                <Grid item xs={12} sm={6} md={4}>
                  <Button
                    variant="outlined"
                    color="error"
                    startIcon={<DeleteIcon />}
                    onClick={handleDeleteResults}
                    disabled={deletingResults}
                    fullWidth
                    size="large"
                  >
                    {deletingResults ? 'Deleting...' : 'Delete Results'}
                  </Button>
                </Grid>
              </Grid>

              <Divider sx={{ my: 3 }} />

              <Alert severity="success">
                <Typography variant="subtitle2" gutterBottom>
                  🎉 Calibration Complete!
                </Typography>
                <Typography variant="body2">
                  All calibration results are ready. You can export the data and use it in your applications.
                </Typography>
              </Alert>

              <Divider sx={{ my: 2 }} />

              <Typography variant="caption" color="text.secondary" component="div">
                <strong>Export Options:</strong>
                <ul style={{ margin: '8px 0', paddingLeft: '20px' }}>
                  <li>
                    <strong>Full Export:</strong> Complete calibration JSON (ROI/tripwire world coordinates). Choose
                    AMC or VGGT in the dialog when both are available; optional metadata applies to both exports on save.
                  </li>
                  {!isSingleCam && (
                    <li><strong>MV3DT ZIP:</strong> MV3DT-compatible format for verification (multi-camera only)</li>
                  )}
                </ul>
              </Typography>
            </CardContent>
          </Card>

          <Dialog
            open={fullExportDialogOpen}
            onClose={() => {
              if (
                downloadingFullExport !== null ||
                fullExportManualLoading ||
                fullExportManualSaving ||
                resettingFullExport
              ) return;
              setFullExportDialogOpen(false);
            }}
            maxWidth={fullExportManualOpen ? 'lg' : 'md'}
            fullWidth
            scroll="paper"
          >
            <DialogTitle
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 2,
              }}
            >
              <span>Advanced export metadata</span>
              <Button
                variant="outlined"
                color="warning"
                size="small"
                startIcon={
                  resettingFullExport ? (
                    <CircularProgress size={18} color="inherit" />
                  ) : (
                    <RestartAltIcon />
                  )
                }
                onClick={() => setResetExportConfirmOpen(true)}
                disabled={
                  downloadingFullExport !== null ||
                  fullExportManualLoading ||
                  fullExportManualSaving ||
                  exportMetaLoading ||
                  resettingFullExport ||
                  (exportMetaKind === 'amc' ? !amcExportReady : !vggtExportReady)
                }
              >
                {resettingFullExport ? 'Resetting…' : 'Reset'}
              </Button>
            </DialogTitle>
            <DialogContent dividers>
              {amcExportReady && vggtSelectable && vggtExportReady && (
                <ToggleButtonGroup
                  size="small"
                  value={exportMetaKind}
                  exclusive
                  sx={{ mb: 2 }}
                  disabled={
                    fullExportManualOpen ||
                    downloadingFullExport !== null ||
                    resettingFullExport
                  }
                  onChange={(_, v) => {
                    if (v) setExportMetaKind(v);
                  }}
                >
                  <ToggleButton value="amc">Download / edit AMC export</ToggleButton>
                  <ToggleButton value="vggt">Download / edit VGGT export</ToggleButton>
                </ToggleButtonGroup>
              )}
              {!fullExportManualOpen && fullExportManualSavedByType[exportMetaKind] && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  Saved manual JSON is in use for the {exportMetaKind.toUpperCase()} export. Changes
                  you make below are applied on top of it.
                </Alert>
              )}
              <ExportMetadataForm
                edits={exportMetaEdits}
                sensorLabels={exportMetaSensorLabels}
                loading={exportMetaLoading || resettingFullExport}
                onChange={setExportMetaEdits}
                onLoadFromServer={() => loadExportMetadataForEdit(exportMetaKind)}
                disabled={
                  downloadingFullExport !== null ||
                  exportMetaLoading ||
                  fullExportManualLoading ||
                  fullExportManualOpen ||
                  resettingFullExport
                }
                globalRoiEdits={exportGlobalRoiEdits}
                onGlobalRoiEditsChange={setExportGlobalRoiEdits}
                globalTripwireEdits={exportGlobalTwEdits}
                onGlobalTripwireEditsChange={setExportGlobalTwEdits}
              />
              {fullExportManualOpen && (
                <Box sx={{ mt: 2 }}>
                  <Alert severity="warning" sx={{ mb: 2 }}>
                    <Typography variant="subtitle2" fontWeight={600} gutterBottom>
                      Advanced: full JSON control
                    </Typography>
                    <Typography variant="body2" component="div" sx={{ mb: 1.5 }}>
                      Change this only if you understand the export schema and how your pipeline uses
                      it. It only checks that the text is valid JSON before save or download; it does not
                      verify required fields, types, or consistency. Bad edits can break
                      verification, re-imports, or downstream consumers even when download succeeds.
                    </Typography>
                    <Typography variant="body2" component="div">
                      <strong>Behavior:</strong> While this editor is open, <strong>Download JSON</strong>{' '}
                      uses this text (and writes it to the project export for the active AMC/VGGT
                      type). The structured fields above are ignored for that download. Use{' '}
                      <strong>Save JSON</strong> to keep these edits as the working document, so the
                      structured fields keep building on them after you close the editor.{' '}
                      <strong>Close manual editor</strong> discards anything you have not saved. Use{' '}
                      <strong>Reset</strong> to regenerate the export from
                      calibration results and discard all metadata edits.
                    </Typography>
                  </Alert>
                  {fullExportManualParseError && (
                    <Alert severity="error" sx={{ mb: 1.5 }}>
                      Invalid JSON at line {fullExportManualParseError.line}, column{' '}
                      {fullExportManualParseError.column}: {fullExportManualParseError.message}
                    </Alert>
                  )}
                  <JsonCodeEditor
                    value={fullExportManualText}
                    onChange={setFullExportManualText}
                    onParseErrorChange={setFullExportManualParseError}
                    disabled={
                      downloadingFullExport !== null ||
                      fullExportManualSaving ||
                      resettingFullExport
                    }
                    height={420}
                  />
                </Box>
              )}
            </DialogContent>
            <DialogActions
              sx={{
                px: 3,
                py: 2,
                flexDirection: 'column',
                alignItems: 'stretch',
                gap: 1.5,
              }}
            >
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ width: '100%' }}>
                {fullExportManualOpen ? (
                  <>
                    <Button
                      variant="contained"
                      color="secondary"
                      startIcon={
                        fullExportManualSaving ? (
                          <CircularProgress size={18} color="inherit" />
                        ) : (
                          <SaveIcon />
                        )
                      }
                      onClick={() => void handleSaveManualJson()}
                      disabled={
                        downloadingFullExport !== null ||
                        fullExportManualLoading ||
                        fullExportManualSaving ||
                        resettingFullExport ||
                        fullExportManualParseError !== null
                      }
                    >
                      {fullExportManualSaving ? 'Saving…' : 'Save JSON'}
                    </Button>
                    <Button
                      variant="outlined"
                      onClick={closeFullExportManualEditor}
                      disabled={
                        downloadingFullExport !== null ||
                        fullExportManualLoading ||
                        fullExportManualSaving ||
                        resettingFullExport
                      }
                    >
                      Close manual editor
                    </Button>
                  </>
                ) : (
                  <Button
                    fullWidth
                    variant="outlined"
                    color="secondary"
                    startIcon={
                      fullExportManualLoading ? (
                        <CircularProgress size={18} color="inherit" />
                      ) : (
                        <EditNoteIcon />
                      )
                    }
                    onClick={() => void openFullExportManualEditor()}
                    disabled={
                      downloadingFullExport !== null ||
                      fullExportManualLoading ||
                      exportMetaLoading ||
                      resettingFullExport ||
                      (exportMetaKind === 'amc' ? !amcExportReady : !vggtExportReady)
                    }
                  >
                    {fullExportManualLoading ? 'Loading export…' : 'Full Control'}
                  </Button>
                )}
              </Stack>
              <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ width: '100%' }}>
                <Button
                  onClick={() => setFullExportDialogOpen(false)}
                  disabled={
                    downloadingFullExport !== null ||
                    fullExportManualLoading ||
                    fullExportManualSaving ||
                    resettingFullExport
                  }
                >
                  Cancel
                </Button>
                <Button
                  variant="contained"
                  startIcon={
                    downloadingFullExport !== null ? (
                      <CircularProgress size={18} color="inherit" />
                    ) : (
                      <DataObjectIcon />
                    )
                  }
                  onClick={async () => {
                    const ok = await handleFullExportDownload(exportMetaKind);
                    if (ok) setFullExportDialogOpen(false);
                  }}
                  disabled={
                    downloadingFullExport !== null ||
                    fullExportManualLoading ||
                    fullExportManualSaving ||
                    resettingFullExport ||
                    (fullExportManualOpen && fullExportManualParseError !== null) ||
                    (exportMetaKind === 'amc' ? !amcExportReady : !vggtExportReady)
                  }
                >
                  {downloadingFullExport !== null ? 'Downloading…' : 'Download JSON'}
                </Button>
              </Stack>
            </DialogActions>
          </Dialog>

          <Dialog
            open={resetExportConfirmOpen}
            onClose={() => {
              if (!resettingFullExport) setResetExportConfirmOpen(false);
            }}
            maxWidth="sm"
            fullWidth
          >
            <DialogTitle>Reset to Original Calibration?</DialogTitle>
            <DialogContent>
              <Alert severity="warning" sx={{ mb: 2 }}>
                This action cannot be undone.
              </Alert>
              <Typography>
                Reset the {exportMetaKind.toUpperCase()} export metadata to the original generated
                calibration JSON?
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                All Full Control edits and structured metadata changes—including global ROI,
                tripwire, sensor group, and region metadata—will be discarded. The project export
                file will be regenerated from the calibration results.
              </Typography>
            </DialogContent>
            <DialogActions>
              <Button
                onClick={() => setResetExportConfirmOpen(false)}
                disabled={resettingFullExport}
              >
                Cancel
              </Button>
              <Button
                color="warning"
                variant="contained"
                startIcon={
                  resettingFullExport ? (
                    <CircularProgress size={18} color="inherit" />
                  ) : (
                    <RestartAltIcon />
                  )
                }
                onClick={() => void handleResetExportToOriginal()}
                disabled={resettingFullExport}
              >
                {resettingFullExport ? 'Resetting…' : 'Reset'}
              </Button>
            </DialogActions>
          </Dialog>
        </>
      )}
    </Box>
  );
};

export default Results;
