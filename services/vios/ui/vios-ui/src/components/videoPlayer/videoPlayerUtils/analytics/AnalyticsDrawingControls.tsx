/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import React from 'react';
import { Box, Typography, ButtonGroup, Button, CircularProgress, alpha, Tooltip } from '@mui/material';
import { Save, Cancel } from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { StreamType } from 'vst-streaming-lib';
import { DrawingMode, CoordinatePoint, TripwireCoordinates } from './AnalyticsTypes';

interface AnalyticsDrawingControlsProps {
    streamType: StreamType;
    drawingMode: DrawingMode;
    isLoadingCalibration: boolean;
    hasCalibrationData: boolean;
    roiPoints: CoordinatePoint[];
    tripwirePoints: TripwireCoordinates | null;
    directionPoints: TripwireCoordinates | null;
    onDrawingModeChange: (mode: DrawingMode) => void;
    onSave: () => void;
    onClear: () => void;
}

const AnalyticsDrawingControls: React.FC<AnalyticsDrawingControlsProps> = ({
    streamType,
    drawingMode,
    isLoadingCalibration,
    hasCalibrationData,
    roiPoints,
    tripwirePoints,
    directionPoints,
    onDrawingModeChange,
    onSave,
    onClear,
}) => {
    const theme = useTheme();

    // Only show for Live and Replay streams
    if (streamType === StreamType.VideoWall) {
        return null;
    }

    const handleModeChange = (mode: DrawingMode) => {
        if (!hasCalibrationData && !isLoadingCalibration) {
            return; // Don't allow mode change without calibration data
        }
        onDrawingModeChange(mode);
    };

    const hasCompletedDrawing = roiPoints.length >= 3 || (tripwirePoints && directionPoints);
    const canDraw = hasCalibrationData && !isLoadingCalibration;

    return (
        <Box
            sx={{
                mb: 2,
                p: 1.5,
                borderRadius: 2,
                backgroundColor: alpha(theme.palette.background.default, 0.5),
                border: `1px solid ${alpha(theme.palette.divider, 0.3)}`,
            }}
        >
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Typography variant='subtitle2' sx={{ fontWeight: 'bold', color: theme.palette.text.primary }}>
                        Draw:
                    </Typography>

                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                        <ButtonGroup variant='outlined' size='medium'>
                            <Tooltip
                                title='Click to add ROI points. Double-click to close the polygon (minimum 3 points required).'
                                arrow
                                placement='top'
                            >
                                <span>
                                    <Button
                                        onClick={() => handleModeChange('roi')}
                                        color={drawingMode === 'roi' ? 'primary' : 'inherit'}
                                        variant={drawingMode === 'roi' ? 'contained' : 'outlined'}
                                        disabled={!canDraw}
                                        sx={{
                                            minWidth: 80,
                                            '&:hover': {
                                                bgcolor: drawingMode === 'roi' ? undefined : alpha(theme.palette.primary.main, 0.1),
                                            },
                                        }}
                                    >
                                        ROI
                                    </Button>
                                </span>
                            </Tooltip>
                            <Tooltip title='Click two points to define the tripwire line.' arrow placement='top'>
                                <span>
                                    <Button
                                        onClick={() => handleModeChange('tripwire-line')}
                                        color={drawingMode === 'tripwire-line' ? 'secondary' : 'inherit'}
                                        variant={drawingMode === 'tripwire-line' ? 'contained' : 'outlined'}
                                        disabled={!canDraw}
                                        sx={{
                                            minWidth: 80,
                                            '&:hover': {
                                                bgcolor:
                                                    drawingMode === 'tripwire-line' ? undefined : alpha(theme.palette.secondary.main, 0.1),
                                            },
                                        }}
                                    >
                                        Line
                                    </Button>
                                </span>
                            </Tooltip>
                            <Tooltip title='Click two points to define the detection direction arrow.' arrow placement='top'>
                                <span>
                                    <Button
                                        onClick={() => handleModeChange('tripwire-direction')}
                                        color={drawingMode === 'tripwire-direction' ? 'warning' : 'inherit'}
                                        variant={drawingMode === 'tripwire-direction' ? 'contained' : 'outlined'}
                                        disabled={!canDraw}
                                        sx={{
                                            minWidth: 80,
                                            '&:hover': {
                                                bgcolor:
                                                    drawingMode === 'tripwire-direction'
                                                        ? undefined
                                                        : alpha(theme.palette.warning.main, 0.1),
                                            },
                                        }}
                                    >
                                        Direction
                                    </Button>
                                </span>
                            </Tooltip>
                        </ButtonGroup>

                        {isLoadingCalibration && <CircularProgress size={20} sx={{ ml: 1 }} />}

                        {!canDraw && !isLoadingCalibration && (
                            <Typography variant='caption' color='text.secondary' sx={{ ml: 1 }}>
                                Calibration data unavailable
                            </Typography>
                        )}
                    </Box>
                </Box>

                {hasCompletedDrawing && (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Button variant='contained' color='success' startIcon={<Save />} onClick={onSave} size='small'>
                            Save
                        </Button>

                        <Button variant='outlined' color='error' startIcon={<Cancel />} onClick={onClear} size='small'>
                            Clear
                        </Button>
                    </Box>
                )}
            </Box>
        </Box>
    );
};

export default AnalyticsDrawingControls;
