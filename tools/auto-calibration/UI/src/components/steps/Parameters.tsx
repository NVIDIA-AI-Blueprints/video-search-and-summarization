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
 * Parameters Step
 * Floor-plan scale and optional focal lengths (ROI/TW drawing lives on Results).
 */

import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  TextField,
  Alert,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import { useApp } from '../../context/AppContext';
import { api } from '../../services/api';
import type { Project } from '../../types';
import { ProjectState } from '../../types';
import FloorPlanScaleCard from '../FloorPlanScaleCard';

interface ParametersProps {
  project: Project;
  onProjectUpdate: (project: Project) => void;
}

const Parameters: React.FC<ParametersProps> = ({ project, onProjectUpdate }) => {
  const { showSuccess, showError } = useApp();
  const [focalLengthInput, setFocalLengthInput] = useState<string>('');
  const [loading, setLoading] = useState(false);
  // Starts locked: unknown until the capabilities read below returns.
  const [runningCalibration, setRunningCalibration] = useState(true);

  const parametersLocked =
    project.project_state === ProjectState.RUNNING ||
    project.amc_state === 'RUNNING' ||
    project.vggt_state === 'RUNNING';

  // layout_px_per_m lives in the shared config the server refuses to update while any
  // job runs, so the scale card locks on the service-wide flag, not just this project.
  const scaleLocked = parametersLocked || runningCalibration;

  useEffect(() => {
    loadFocalLength();
  }, [project.project_id]);

  // Read once on entering the step; the server holds the shared config lock service-wide.
  useEffect(() => {
    let cancelled = false;
    const checkRunningCalibration = async () => {
      try {
        const response = await api.system.getCapabilities();
        if (!cancelled) setRunningCalibration(Boolean(response.running_calibration));
      } catch {
        // Fail closed: without server state, assume a job holds the shared config.
        if (!cancelled) setRunningCalibration(true);
      }
    };

    void checkRunningCalibration();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadFocalLength = async () => {
    try {
      const response = await api.data.getFocalLength(project.project_id);
      if (response.focal_length.length > 0) {
        setFocalLengthInput(response.focal_length.join(', '));
      }
    } catch {
      console.log('No focal length data yet');
    }
  };

  const refreshProject = async () => {
    try {
      const response = await api.project.getProjectInfo(project.project_id);
      onProjectUpdate(response.project_info);
    } catch (error: unknown) {
      console.error('Failed to refresh project:', error);
    }
  };

  const handleSaveFocalLength = async () => {
    const trimmed = focalLengthInput.trim();

    if (trimmed === '') {
      try {
        await api.data.deleteFocalLength(project.project_id);
        showSuccess('Focal length cleared');
        await refreshProject();
      } catch (error: unknown) {
        const apiError = error as { message?: string; detail?: string };
        showError(apiError.detail || apiError.message || 'Failed to clear focal length');
      }
      return;
    }

    const parts = trimmed.split(',').map((s) => s.trim());
    const numbers: number[] = [];

    for (const part of parts) {
      const num = parseFloat(part);
      if (isNaN(num) || num <= 0) {
        showError(`Invalid focal length: "${part}". Must be a positive number.`);
        return;
      }
      numbers.push(num);
    }

    if (numbers.length !== project.video_files_count) {
      showError(
        `Focal length count (${numbers.length}) must match video count (${project.video_files_count})`,
      );
      return;
    }

    try {
      setLoading(true);
      await api.data.uploadFocalLength(project.project_id, numbers);
      showSuccess('Focal length saved successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to save focal length');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box>
      <Typography variant="h5" gutterBottom>
        Parameters Configuration
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Project: <strong>{project.project_name}</strong>
      </Typography>

      {(parametersLocked || runningCalibration) && (
        <Alert severity="info" sx={{ mb: 3 }}>
          Calibration is running. Layout scale is shared across all projects, so it is view-only
          until that job finishes.
        </Alert>
      )}

      <Box sx={{ mb: 3, maxWidth: 900, mx: 'auto' }}>
        <FloorPlanScaleCard project={project} disabled={scaleLocked} />
      </Box>

      <Box sx={{ maxWidth: 900, mx: 'auto' }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Focal Length (Optional)
            </Typography>
            <Alert severity="info" sx={{ mb: 2 }} icon={false}>
              <Typography variant="caption">
                Provide {project.video_files_count} focal length values (one per camera), separated by
                commas. Leave empty if unknown.
              </Typography>
            </Alert>

            <TextField
              fullWidth
              label="Focal Lengths"
              placeholder="e.g., 1269.01, 1099.50, 1099.50, 1099.50"
              value={focalLengthInput}
              onChange={(e) => setFocalLengthInput(e.target.value)}
              helperText={`${focalLengthInput.split(',').filter((s) => s.trim()).length} / ${project.video_files_count} values`}
              sx={{ mb: 2 }}
            />

            <Button
              fullWidth
              variant="contained"
              startIcon={<SaveIcon />}
              onClick={handleSaveFocalLength}
              disabled={loading}
            >
              Save Focal Length
            </Button>
          </CardContent>
        </Card>
      </Box>
    </Box>
  );
};

export default Parameters;
