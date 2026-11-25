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
 * Floor-plan-to-3D scale helper.
 * Pick two points on layout.png (Konva canvas with zoom/pan like Global ROIs),
 * enter real-world distance (m), compute layout_px_per_m, save via config API.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import StraightenIcon from '@mui/icons-material/Straighten';
import { useApp } from '../context/AppContext';
import { api } from '../services/api';
import { calibrationOnlyParams } from '../utils/configParams';
import type { ConfigParams, Point2D, Project } from '../types';
import AnnotationCanvas from './AnnotationCanvas';

interface FloorPlanScaleCardProps {
  project: Project;
  disabled?: boolean;
}

const FloorPlanScaleCard: React.FC<FloorPlanScaleCardProps> = ({
  project,
  disabled = false,
}) => {
  const { showSuccess, showError } = useApp();
  const [layoutUrl, setLayoutUrl] = useState('');
  const [loadingLayout, setLoadingLayout] = useState(false);
  const [points, setPoints] = useState<Point2D[]>([]);
  const [distanceMeters, setDistanceMeters] = useState('');
  const [savedPpm, setSavedPpm] = useState<number | null>(null);
  const [manualPpm, setManualPpm] = useState('');
  const [saving, setSaving] = useState(false);

  const loadLayout = useCallback(async () => {
    if (!project.layout_file) {
      setLayoutUrl('');
      return;
    }
    try {
      setLoadingLayout(true);
      const blob = await api.data.getLayoutImage(project.project_id);
      setLayoutUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to load layout image');
    } finally {
      setLoadingLayout(false);
    }
  }, [project.layout_file, project.project_id, showError]);

  const loadSavedPpm = useCallback(async () => {
    try {
      const response = (await api.data.getConfig()) as { config_params?: ConfigParams };
      const ppm = response.config_params?.layout_px_per_m;
      if (typeof ppm === 'number' && Number.isFinite(ppm)) {
        setSavedPpm(ppm);
        setManualPpm(String(ppm));
      }
    } catch {
      // Non-fatal: scale card still works without showing current value
    }
  }, []);

  useEffect(() => {
    loadLayout();
    loadSavedPpm();
    return () => {
      setLayoutUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return '';
      });
    };
  }, [loadLayout, loadSavedPpm]);

  const pixelDistance = useMemo(() => {
    if (points.length !== 2) return null;
    const [a, b] = points;
    return Math.hypot(b.x - a.x, b.y - a.y);
  }, [points]);

  const meters = useMemo(() => {
    const parsed = parseFloat(distanceMeters);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, [distanceMeters]);

  const computedPpm = useMemo(() => {
    if (pixelDistance === null || meters === null) return null;
    return pixelDistance / meters;
  }, [pixelDistance, meters]);

  // Measuring on the layout fills the single editable px/m field.
  useEffect(() => {
    if (computedPpm !== null) {
      setManualPpm(computedPpm.toFixed(6));
    }
  }, [computedPpm]);

  const handleAddMeasurePoint = (x: number, y: number) => {
    if (disabled) return;
    setPoints((prev) => {
      if (prev.length >= 2) return [{ x, y }];
      return [...prev, { x, y }];
    });
  };

  const handleReset = () => {
    setPoints([]);
    setDistanceMeters('');
    setManualPpm(savedPpm !== null ? String(savedPpm) : '');
  };

  const handleSave = async () => {
    if (disabled) {
      showError('Calibration is running. Scale cannot be changed.');
      return;
    }
    const parsed = parseFloat(manualPpm);
    if (isNaN(parsed) || parsed <= 0) {
      showError('Enter a positive layout pixels-per-metre value, or measure two points.');
      return;
    }

    try {
      setSaving(true);
      const response = (await api.data.getConfig()) as { config_params?: ConfigParams };
      const current = response.config_params;
      if (!current) {
        throw new Error('Could not load current configuration');
      }
      const next: ConfigParams = {
        ...current,
        layout_px_per_m: Number(parsed.toFixed(6)),
      };
      await api.data.updateConfig(project.project_id, calibrationOnlyParams(next));
      setSavedPpm(next.layout_px_per_m);
      setManualPpm(String(next.layout_px_per_m));
      showSuccess(`Saved layout scale: ${next.layout_px_per_m.toFixed(4)} px/m`);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to save layout scale');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <StraightenIcon color="primary" />
          <Typography variant="h6">Floor-plan to 3D scale</Typography>
        </Stack>

        <Alert severity="info" sx={{ mb: 2 }} icon={false}>
          <Typography variant="caption">
            Type <strong>layout px/m</strong> directly, or measure two points on the layout and enter
            the real-world distance — the calculated value fills the field.
            <br />
            <strong>Controls:</strong> Scroll to zoom, drag to pan 
          </Typography>
        </Alert>

        {!project.layout_file ? (
          <Alert severity="warning">
            Upload <strong>layout.png</strong> in Step 2: Video Configuration first.
          </Alert>
        ) : (
          <>
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              alignItems={{ xs: 'stretch', sm: 'center' }}
              sx={{ mb: 2 }}
            >
              <Typography variant="body2" color="text.secondary" sx={{ minWidth: 'fit-content' }}>
                Current saved scale:{' '}
                <strong>
                  {savedPpm !== null ? `${savedPpm.toFixed(4)} px/m` : 'not set'}
                </strong>
              </Typography>
              <TextField
                size="small"
                label="Layout px/m"
                type="text"
                inputProps={{ inputMode: 'decimal' }}
                value={manualPpm}
                onChange={(e) => setManualPpm(e.target.value)}
                disabled={disabled || saving}
                helperText="Edit directly or fill via measure below"
                sx={{ flex: 1, minWidth: 160 }}
              />
            </Stack>

            {loadingLayout || !layoutUrl ? (
              <Box
                sx={{
                  height: 500,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  bgcolor: 'action.hover',
                  border: '2px solid',
                  borderColor: 'grey.400',
                  mb: 2,
                }}
              >
                <Typography variant="body2" color="text.secondary">
                  {loadingLayout ? 'Loading layout map...' : 'Layout image not available'}
                </Typography>
              </Box>
            ) : (
              <Box
                sx={{
                  height: 500,
                  width: '100%',
                  border: '2px solid',
                  borderColor: 'grey.400',
                  position: 'relative',
                  overflow: 'hidden',
                  mb: 2,
                  opacity: disabled ? 0.7 : 1,
                }}
              >
                <AnnotationCanvas
                  imageUrl={layoutUrl}
                  rois={[]}
                  tripwires={[]}
                  drawingMode={disabled ? 'none' : 'measure'}
                  showAnnotations
                  readOnly={disabled}
                  measurePoints={points}
                  onAddROIPoint={() => undefined}
                  onFinishROI={() => undefined}
                  onAddTripwirePoint={() => undefined}
                  onAddMeasurePoint={handleAddMeasurePoint}
                />
              </Box>
            )}

            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
              {points.length === 0 && 'Optional: click to place point 1 · scroll to zoom'}
              {points.length === 1 && 'Click to place point 2 · scroll to zoom'}
              {points.length === 2 &&
                `Pixel distance: ${pixelDistance?.toFixed(2)} px — click again to restart`}
            </Typography>

            <TextField
              fullWidth
              size="small"
              label="Real-world distance (meters)"
              type="text"
              inputProps={{ inputMode: 'decimal' }}
              value={distanceMeters}
              onChange={(e) => setDistanceMeters(e.target.value)}
              disabled={disabled || points.length < 2}
              helperText={
                computedPpm !== null
                  ? `Fills Layout px/m above: ${computedPpm.toFixed(4)}`
                  : 'Enter distance between the two points to auto-fill Layout px/m'
              }
              sx={{ mb: 2 }}
            />

            <Stack direction="row" spacing={1}>
              <Button
                variant="outlined"
                startIcon={<RestartAltIcon />}
                onClick={handleReset}
                disabled={
                  disabled ||
                  (points.length === 0 && !distanceMeters && manualPpm === (savedPpm !== null ? String(savedPpm) : ''))
                }
              >
                Reset
              </Button>
              <Button
                fullWidth
                variant="contained"
                startIcon={<SaveIcon />}
                onClick={handleSave}
                disabled={disabled || saving || !manualPpm.trim()}
              >
                {saving ? 'Saving…' : 'Save scale'}
              </Button>
            </Stack>
          </>
        )}
      </CardContent>
    </Card>
  );
};

export default FloorPlanScaleCard;
