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
 * Settings Component
 * Configure calibration parameters
 */

import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Switch,
  FormControlLabel,
  Box,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Grid,
  Divider,
  Alert,
  CircularProgress,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SaveIcon from '@mui/icons-material/Save';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import DownloadIcon from '@mui/icons-material/Download';
import UploadIcon from '@mui/icons-material/Upload';
import { useApp } from '../context/AppContext';
import { api } from '../services/api';
import type { ConfigParams } from '../types';
import { calibrationOnlyParams } from '../utils/configParams';

interface SettingsProps {
  open: boolean;
  onClose: () => void;
}

// Fallback configuration values (matches actual YAML config files)
const DEFAULT_CONFIG: ConfigParams = {
  // Camera Matrix Config (camera_matrix_config.yaml)
  iterations: 10000,
  
  // Eval Config (eval_config.yaml)
  layout_px_per_m: 24.22,
  virtual_gt_person_height: 1.75,
  virtual_gt_grid_spacing: 1.0,
  
  // MV AMC Config (mv_amc_config.yaml)
  min_length: 90.0,
  min_moving_dist: 2.0,
  min_mean_moving_dist: 0.01,
  min_matching_points: 100,
  min_dtw_score_threshold: 25.0,
  min_distance_score_threshold: 1.2,
  max_distance_score_hungarian_threshold: 0.8,
  min_matched_tracklets: 6,
  max_matched_tracklets_dist: 2.0,
  min_tracklet_dist: 3.0,
  min_matched_points: 100,
  inlier_threshold: 10.0,
  min_inliers: 0.6,
  reproj_err_threshold: 25.0,
  skip_frame: 10,
  timestamp_sec: 0,
  vggt_auto_select: false,
  vggt_max_num_frames: 10000,
  vggt_confidence_score_threshold: 0.2,
};

const Settings: React.FC<SettingsProps> = ({ open, onClose }) => {
  const { showSuccess, showError, selectedProject } = useApp();
  const [config, setConfig] = useState<ConfigParams>(DEFAULT_CONFIG);
  const [originalConfig, setOriginalConfig] = useState<ConfigParams>(DEFAULT_CONFIG);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false);
  const [downloadFilename, setDownloadFilename] = useState('config');
  const [runningCalibration, setRunningCalibration] = useState(false);
  const [checkingCalibration, setCheckingCalibration] = useState(false);
  const settingsLocked = checkingCalibration || runningCalibration;
  // Raw text so clearing the field does not snap to "0" and prefix the next keystrokes.
  const [personHeightInput, setPersonHeightInput] = useState(
    String(DEFAULT_CONFIG.virtual_gt_person_height),
  );
  const [gridSpacingInput, setGridSpacingInput] = useState(
    String(DEFAULT_CONFIG.virtual_gt_grid_spacing),
  );

  // Validate config parameters
  const validateConfigParams = (params: ConfigParams | Record<string, unknown>): string | null => {
    const values = params as Record<string, unknown>;
    const schema: Record<string, 'number' | 'boolean' | 'string'> = {
      iterations: 'number',
      layout_px_per_m: 'number',
      virtual_gt_person_height: 'number',
      virtual_gt_grid_spacing: 'number',
      min_length: 'number',
      min_moving_dist: 'number',
      min_mean_moving_dist: 'number',
      min_matching_points: 'number',
      min_dtw_score_threshold: 'number',
      min_distance_score_threshold: 'number',
      max_distance_score_hungarian_threshold: 'number',
      min_matched_tracklets: 'number',
      max_matched_tracklets_dist: 'number',
      min_tracklet_dist: 'number',
      min_matched_points: 'number',
      inlier_threshold: 'number',
      min_inliers: 'number',
      reproj_err_threshold: 'number',
      skip_frame: 'number',
      timestamp_sec: 'number',
      vggt_auto_select: 'boolean',
      vggt_max_num_frames: 'number',
      vggt_confidence_score_threshold: 'number',
    };

    for (const [key, expectedType] of Object.entries(schema)) {
      if (!(key in values)) {
        return `Missing required parameter: ${key}`;
      }
      const value = values[key];
      if (expectedType === 'number' && (typeof value !== 'number' || isNaN(value))) {
        return `Invalid type for "${key}": expected number`;
      }
      if (expectedType === 'boolean' && typeof value !== 'boolean') {
        return `Invalid type for "${key}": expected boolean`;
      }
      if (expectedType === 'string' && typeof value !== 'string') {
        return `Invalid type for "${key}": expected string`;
      }
    }
    if (!Number.isInteger(values.iterations) || (values.iterations as number) < 1 || (values.iterations as number) > 100000) {
      return 'Iterations must be a whole number between 1 and 100000';
    }
    return null;
  };

  // Open download dialog
  const handleDownloadConfig = () => {
    setDownloadFilename('config');
    setDownloadDialogOpen(true);
  };

  // Perform actual download with custom filename
  const performDownload = () => {
    const finalName = downloadFilename.trim() || 'config';
    const exportData: Record<string, unknown> = {
      iterations: config.iterations,
      layout_px_per_m: config.layout_px_per_m,
      virtual_gt_person_height: config.virtual_gt_person_height,
      virtual_gt_grid_spacing: config.virtual_gt_grid_spacing,
      min_length: config.min_length,
      min_moving_dist: config.min_moving_dist,
      min_mean_moving_dist: config.min_mean_moving_dist,
      min_matching_points: config.min_matching_points,
      min_dtw_score_threshold: config.min_dtw_score_threshold,
      min_distance_score_threshold: config.min_distance_score_threshold,
      max_distance_score_hungarian_threshold: config.max_distance_score_hungarian_threshold,
      min_matched_tracklets: config.min_matched_tracklets,
      max_matched_tracklets_dist: config.max_matched_tracklets_dist,
      min_tracklet_dist: config.min_tracklet_dist,
      min_matched_points: config.min_matched_points,
      inlier_threshold: config.inlier_threshold,
      min_inliers: config.min_inliers,
      reproj_err_threshold: config.reproj_err_threshold,
      skip_frame: config.skip_frame,
      timestamp_sec: config.timestamp_sec,
      vggt_auto_select: config.vggt_auto_select,
      vggt_max_num_frames: config.vggt_max_num_frames,
      vggt_confidence_score_threshold: config.vggt_confidence_score_threshold,
    };
    const blob = new Blob([JSON.stringify(exportData, null, 4)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${finalName}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    setDownloadDialogOpen(false);
    showSuccess('Config downloaded successfully');
  };

  // Handle file upload
  const handleUploadConfig = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (settingsLocked) {
      showError('Cannot change settings while calibration is running');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      return;
    }

    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const content = e.target?.result as string;
        const parsed = JSON.parse(content) as Record<string, unknown>;
        const merged = { ...DEFAULT_CONFIG, ...calibrationOnlyParams(parsed) } as Record<string, unknown>;

        const validationError = validateConfigParams(merged);
        if (validationError) {
          showError(validationError);
          return;
        }

        setConfig(merged as unknown as ConfigParams);
        showSuccess('Config loaded from file successfully');
      } catch {
        showError('Failed to parse JSON file');
      }
    };
    reader.readAsText(file);
    
    // Reset input so same file can be uploaded again
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // Load config and defaults when dialog opens
  useEffect(() => {
    if (open) {
      loadDefaults();
      loadConfig();
    }
  }, [open]);

  // Keep the text buffer in sync when the value changes outside the field (load, reset, import)
  useEffect(() => {
    if (parseFloat(personHeightInput) !== config.virtual_gt_person_height) {
      setPersonHeightInput(String(config.virtual_gt_person_height));
    }
  }, [config.virtual_gt_person_height]);

  useEffect(() => {
    if (parseFloat(gridSpacingInput) !== config.virtual_gt_grid_spacing) {
      setGridSpacingInput(String(config.virtual_gt_grid_spacing));
    }
  }, [config.virtual_gt_grid_spacing]);

  // Settings are shared — read the server's global flag whenever the dialog opens
  useEffect(() => {
    if (!open) {
      setRunningCalibration(false);
      setCheckingCalibration(false);
      return;
    }

    let cancelled = false;
    const checkRunningCalibration = async () => {
      setCheckingCalibration(true);
      try {
        const response = await api.system.getCapabilities();
        if (!cancelled) {
          setRunningCalibration(Boolean(response.running_calibration));
        }
      } catch {
        // Fail closed: don't allow global settings changes without server state.
        if (!cancelled) {
          setRunningCalibration(true);
          showError('Unable to verify calibration status. Settings remain locked.');
        }
      } finally {
        if (!cancelled) {
          setCheckingCalibration(false);
        }
      }
    };

    void checkRunningCalibration();
    return () => {
      cancelled = true;
    };
  }, [open, showError]);

  // Load true default configuration (never changes)
  const loadDefaults = async () => {
    try {
      const response = await api.data.getConfigDefaults() as { config_params?: ConfigParams };
      if (response.config_params) {
        setOriginalConfig(calibrationOnlyParams(response.config_params)); // True defaults
      }
    } catch (error: unknown) {
      console.error('Failed to load default config:', error);
      // Keep hardcoded fallback if loading fails
    }
  };

  // Load current configuration from backend (global config, not project-specific)
  const loadConfig = async () => {
    try {
      setLoading(true);
      const response = await api.data.getConfig() as { config_params?: ConfigParams };
      if (response.config_params) {
        setConfig(calibrationOnlyParams(response.config_params));
      }
    } catch (error: unknown) {
      console.error('Failed to load config:', error);
      // Keep default values if loading fails
      showError('Failed to load configuration');
    } finally {
      setLoading(false);
    }
  };

  // Update field value
  const handleChange = (field: keyof ConfigParams, value: number | boolean | string) => {
    setConfig(prev => ({ ...prev, [field]: value }));
  };

  // Reset to defaults (actual config file values)
  const handleReset = () => {
    if (settingsLocked) {
      showError('Cannot change settings while calibration is running');
      return;
    }
    setConfig(originalConfig);
    showSuccess('Reset to original configuration values');
  };

  // Save configuration
  const handleSave = async () => {
    if (!selectedProject) {
      showError('Please select a project first');
      return;
    }
    if (settingsLocked) {
      showError('Cannot change settings while calibration is running');
      return;
    }
    const validationError = validateConfigParams(config);
    if (validationError) {
      showError(validationError);
      return;
    }

    try {
      setSaving(true);
      await api.data.updateConfig(selectedProject.project_id, calibrationOnlyParams(config));
      showSuccess('Configuration saved successfully');
      onClose();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to save configuration';
      showError(errorMessage);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6">Calibration Settings</Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button
              size="small"
              startIcon={<DownloadIcon />}
              onClick={handleDownloadConfig}
            >
              Download
            </Button>
            <Button
              size="small"
              startIcon={<UploadIcon />}
              onClick={() => fileInputRef.current?.click()}
              disabled={settingsLocked}
            >
              Upload
            </Button>
            <input
              type="file"
              ref={fileInputRef}
              accept=".json"
              style={{ display: 'none' }}
              onChange={handleUploadConfig}
              disabled={settingsLocked}
            />
            <Button
              size="small"
              startIcon={<RestartAltIcon />}
              onClick={handleReset}
              disabled={settingsLocked}
            >
              Reset to Defaults
            </Button>
          </Box>
        </Box>
      </DialogTitle>

      <DialogContent dividers sx={{ maxHeight: '70vh' }}>
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2">
            These are <strong>global settings</strong> that apply to all calibration projects.
            {!selectedProject && ' Select a project to save changes.'}
          </Typography>
        </Alert>
        {settingsLocked && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Calibration is currently running. These settings are shared globally, so all fields are
            read-only until it finishes. Download still works.
          </Alert>
        )}

        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        ) : (
          <>
            {/* Camera Matrix Configuration */}
            <Accordion defaultExpanded>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="subtitle1" fontWeight="bold">
              Camera Matrix Configuration
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Grid container spacing={2}>
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Iterations"
                  type="number"
                  value={config.iterations}
                  onChange={(e) => handleChange('iterations', parseInt(e.target.value) || 0)}
                  helperText="Number of optimization iterations"
                  disabled={settingsLocked}
                />
              </Grid>
            </Grid>
          </AccordionDetails>
        </Accordion>

        {/* Evaluation / Visualization Configuration */}
        <Accordion>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="subtitle1" fontWeight="bold">
              Evaluation Configuration
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Virtual-GT Person Height (m)"
                  type="number"
                  inputProps={{ step: 0.05, min: 0.5, max: 3 }}
                  value={personHeightInput}
                  onChange={(e) => {
                    setPersonHeightInput(e.target.value);
                    const parsed = parseFloat(e.target.value);
                    if (Number.isFinite(parsed)) {
                      handleChange('virtual_gt_person_height', parsed);
                    }
                  }}
                  onBlur={() => setPersonHeightInput(String(config.virtual_gt_person_height))}
                  helperText="Height of red poles in GT Virtual Objects overlays."
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Virtual-GT Grid Spacing (m)"
                  type="number"
                  inputProps={{ step: 0.1, min: 0.1, max: 20 }}
                  value={gridSpacingInput}
                  onChange={(e) => {
                    setGridSpacingInput(e.target.value);
                    const parsed = parseFloat(e.target.value);
                    if (Number.isFinite(parsed)) {
                      handleChange('virtual_gt_grid_spacing', parsed);
                    }
                  }}
                  onBlur={() => setGridSpacingInput(String(config.virtual_gt_grid_spacing))}
                  helperText="Floor-grid cell size (spacing×spacing)."
                  disabled={settingsLocked}
                />
              </Grid>
            </Grid>
          </AccordionDetails>
        </Accordion>

        {/* Multi-View AMC Configuration */}
        <Accordion>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="subtitle1" fontWeight="bold">
              Multi-View AMC Configuration
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Length"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.min_length}
                  onChange={(e) => handleChange('min_length', parseFloat(e.target.value) || 0)}
                  helperText="Minimum tracklet length"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Moving Distance"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.min_moving_dist}
                  onChange={(e) => handleChange('min_moving_dist', parseFloat(e.target.value) || 0)}
                  helperText="Minimum movement distance"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Mean Moving Distance"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.min_mean_moving_dist}
                  onChange={(e) => handleChange('min_mean_moving_dist', parseFloat(e.target.value) || 0)}
                  helperText="Minimum average movement"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Matching Points"
                  type="number"
                  value={config.min_matching_points}
                  onChange={(e) => handleChange('min_matching_points', parseInt(e.target.value) || 0)}
                  helperText="Minimum points for matching"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min DTW Score Threshold"
                  type="number"
                  inputProps={{ step: 0.01, min: 0, max: 1 }}
                  value={config.min_dtw_score_threshold}
                  onChange={(e) => handleChange('min_dtw_score_threshold', parseFloat(e.target.value) || 0)}
                  helperText="DTW score threshold (0-1)"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Distance Score Threshold"
                  type="number"
                  inputProps={{ step: 0.01, min: 0, max: 1 }}
                  value={config.min_distance_score_threshold}
                  onChange={(e) => handleChange('min_distance_score_threshold', parseFloat(e.target.value) || 0)}
                  helperText="Distance score threshold (0-1)"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Max Distance Score Hungarian Threshold"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.max_distance_score_hungarian_threshold}
                  onChange={(e) => handleChange('max_distance_score_hungarian_threshold', parseFloat(e.target.value) || 0)}
                  helperText="Hungarian algorithm threshold"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Matched Tracklets"
                  type="number"
                  value={config.min_matched_tracklets}
                  onChange={(e) => handleChange('min_matched_tracklets', parseInt(e.target.value) || 0)}
                  helperText="Minimum tracklet matches"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Max Matched Tracklets Distance"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.max_matched_tracklets_dist}
                  onChange={(e) => handleChange('max_matched_tracklets_dist', parseFloat(e.target.value) || 0)}
                  helperText="Max distance for matches"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Tracklet Distance"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.min_tracklet_dist}
                  onChange={(e) => handleChange('min_tracklet_dist', parseFloat(e.target.value) || 0)}
                  helperText="Minimum tracklet distance"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Matched Points"
                  type="number"
                  value={config.min_matched_points}
                  onChange={(e) => handleChange('min_matched_points', parseInt(e.target.value) || 0)}
                  helperText="Minimum matched points for calibration"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Inlier Threshold"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.inlier_threshold}
                  onChange={(e) => handleChange('inlier_threshold', parseFloat(e.target.value) || 0)}
                  helperText="RANSAC inlier threshold"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Min Inliers"
                  type="number"
                  inputProps={{ step: 0.01, min: 0, max: 1 }}
                  value={config.min_inliers}
                  onChange={(e) => handleChange('min_inliers', parseFloat(e.target.value) || 0)}
                  helperText="Minimum inlier ratio (0-1)"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Reprojection Error Threshold"
                  type="number"
                  inputProps={{ step: 0.1 }}
                  value={config.reproj_err_threshold}
                  onChange={(e) => handleChange('reproj_err_threshold', parseFloat(e.target.value) || 0)}
                  helperText="Max reprojection error"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Skip Frame"
                  type="number"
                  value={config.skip_frame}
                  onChange={(e) => handleChange('skip_frame', parseInt(e.target.value) || 0)}
                  helperText="Number of frames to skip"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12}>
                <Divider sx={{ my: 1 }} />
                <Typography variant="subtitle2" color="text.secondary" sx={{ mt: 1 }}>
                  VGGT configuration
                </Typography>
              </Grid>
              <Grid item xs={12}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={config.vggt_auto_select}
                      onChange={(e) => handleChange('vggt_auto_select', e.target.checked)}
                      disabled={settingsLocked}
                    />
                  }
                  label="Auto-select frames for calibration"
                />
                <Typography variant="caption" color="text.secondary" display="block" sx={{ ml: 4, mt: -0.5 }}>
                  When enabled, timestamp (sec) is ignored for frame selection
                </Typography>
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Timestamp (sec)"
                  type="number"
                  value={config.timestamp_sec}
                  onChange={(e) => handleChange('timestamp_sec', parseInt(e.target.value, 10) || 0)}
                  helperText="Timestamp in seconds to extract frames for calibration (when auto-select is off)"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Max number of frames"
                  type="number"
                  inputProps={{ min: 0 }}
                  value={config.vggt_max_num_frames}
                  onChange={(e) => handleChange('vggt_max_num_frames', parseInt(e.target.value, 10) || 0)}
                  helperText="Maximum frames to sample; 0 means all frames"
                  disabled={settingsLocked}
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  fullWidth
                  label="Confidence score threshold"
                  type="number"
                  inputProps={{ step: 0.01, min: 0, max: 1 }}
                  value={config.vggt_confidence_score_threshold}
                  onChange={(e) =>
                    handleChange('vggt_confidence_score_threshold', parseFloat(e.target.value) || 0)
                  }
                  helperText="Threshold for confidence score"
                  disabled={settingsLocked}
                />
              </Grid>
            </Grid>
          </AccordionDetails>
        </Accordion>
        <Divider sx={{ my: 2 }} />

        <Alert severity="info">
          <Typography variant="body2">
            These settings affect the calibration algorithm behavior. 
            Change these only if you understand their impact on the calibration process.
          </Typography>
        </Alert>
          </>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={handleSave}
          disabled={saving || !selectedProject || settingsLocked}
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </Button>
      </DialogActions>

      {/* Download Filename Dialog */}
      <Dialog open={downloadDialogOpen} onClose={() => setDownloadDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Download Config</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label="Filename"
            value={downloadFilename}
            onChange={(e) => setDownloadFilename(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && performDownload()}
            helperText=".json extension will be added automatically"
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDownloadDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" startIcon={<DownloadIcon />} onClick={performDownload}>
            Download
          </Button>
        </DialogActions>
      </Dialog>
    </Dialog>
  );
};

export default Settings;
