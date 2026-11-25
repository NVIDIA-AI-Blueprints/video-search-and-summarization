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
 * Per-sensor checkboxes: include this camera in root global ROI/tripwire `sensors` lists (export JSON).
 */

import React from 'react';
import {
  Box,
  Typography,
  FormGroup,
  FormControlLabel,
  Checkbox,
  CircularProgress,
} from '@mui/material';
import type { CalibrationExportDoc } from './calibrationExportSensorAssignment';
import { isSensorAssignedToGlobal } from './calibrationExportSensorAssignment';

export interface GlobalExportSensorTogglesProps {
  doc: CalibrationExportDoc | null;
  sensorDisplayId: string;
  loading?: boolean;
  onToggleRoi: (globalRoiId: string, checked: boolean) => void;
  onToggleTripwire: (globalTwId: string, checked: boolean) => void;
}

const GlobalExportSensorToggles: React.FC<GlobalExportSensorTogglesProps> = ({
  doc,
  sensorDisplayId,
  loading,
  onToggleRoi,
  onToggleTripwire,
}) => {
  if (loading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 1 }}>
        <CircularProgress size={18} />
        <Typography variant="caption" color="text.secondary">
          Loading export for assignments…
        </Typography>
      </Box>
    );
  }

  if (!doc) {
    return null;
  }

  const sensor = doc.sensors?.find((s) => s.id === sensorDisplayId);
  const projRois = sensor?.projectedGlobalRois ?? [];
  const projTw = sensor?.projectedGlobalTripwires ?? [];

  if (projRois.length === 0 && projTw.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary" display="block" sx={{ py: 0.5 }}>
        No global ROIs/tripwires in this export for this camera.
      </Typography>
    );
  }

  return (
    <Box sx={{ mt: 1.5, mb: 1 }}>
      <Typography variant="caption" color="text.secondary" display="block" gutterBottom>
        The above sensor is included in sensors list of below Global ROIs and TWs . Check/Uncheck to include/exclude it from the export.
      </Typography>
      <FormGroup>
        {projRois.map((pr) => {
          const checked = isSensorAssignedToGlobal(doc, 'rois', pr.id, sensorDisplayId);
          return (
            <FormControlLabel
              key={`roi-${pr.id}`}
              control={
                <Checkbox
                  size="small"
                  checked={checked}
                  onChange={(_, c) => onToggleRoi(pr.id, c)}
                />
              }
              label={<Typography variant="body2">Global ROI: {pr.id}</Typography>}
            />
          );
        })}
        {projTw.map((pt) => {
          const checked = isSensorAssignedToGlobal(doc, 'tripwires', pt.id, sensorDisplayId);
          return (
            <FormControlLabel
              key={`tw-${pt.id}`}
              control={
                <Checkbox
                  size="small"
                  checked={checked}
                  onChange={(_, c) => onToggleTripwire(pt.id, c)}
                />
              }
              label={<Typography variant="body2">Global tripwire: {pt.id}</Typography>}
            />
          );
        })}
      </FormGroup>
    </Box>
  );
};

export default GlobalExportSensorToggles;
