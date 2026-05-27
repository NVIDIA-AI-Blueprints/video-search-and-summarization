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
import { Box, Stack, Chip, alpha } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { DrawingMode, CoordinatePoint, TripwireCoordinates, CalibrationData } from './AnalyticsTypes';

interface AnalyticsDrawingStatusProps {
    drawingMode: DrawingMode;
    roiPoints: CoordinatePoint[];
    tripwirePoints: TripwireCoordinates | null;
    directionPoints: TripwireCoordinates | null;
    calibrationData: CalibrationData | null;
}

const AnalyticsDrawingStatus: React.FC<AnalyticsDrawingStatusProps> = ({
    drawingMode,
    roiPoints,
    tripwirePoints,
    directionPoints,
    calibrationData,
}) => {
    const theme = useTheme();

    if (drawingMode === 'none') {
        return null;
    }

    return (
        <Box sx={{ mt: 2 }}>
            <Stack direction='row' spacing={2} sx={{ mb: 1 }} alignItems='center' justifyContent='space-between'>
                <Box display='flex' gap={1} flexWrap='wrap'>
                    <Chip
                        label={`ROI (${roiPoints.length} points)`}
                        color={roiPoints.length >= 3 ? 'success' : 'default'}
                        size='small'
                        sx={{
                            bgcolor:
                                roiPoints.length >= 3 ? alpha(theme.palette.success.main, 0.2) : alpha(theme.palette.primary.main, 0.1),
                            color: roiPoints.length >= 3 ? theme.palette.success.main : theme.palette.primary.main,
                        }}
                    />
                    <Chip
                        label={`Tripwire ${tripwirePoints ? '✓' : '✗'}`}
                        color={tripwirePoints ? 'success' : 'default'}
                        size='small'
                        sx={{
                            bgcolor: tripwirePoints ? alpha(theme.palette.success.main, 0.2) : alpha(theme.palette.secondary.main, 0.1),
                            color: tripwirePoints ? theme.palette.success.main : theme.palette.secondary.main,
                        }}
                    />
                    <Chip
                        label={`Direction ${directionPoints ? '✓' : '✗'}`}
                        color={directionPoints ? 'success' : 'default'}
                        size='small'
                        sx={{
                            bgcolor: directionPoints ? alpha(theme.palette.success.main, 0.2) : alpha(theme.palette.warning.main, 0.1),
                            color: directionPoints ? theme.palette.success.main : theme.palette.warning.main,
                        }}
                    />
                </Box>

                {calibrationData && (
                    <Chip
                        label={`Calibration: ${calibrationData.calibrationType}`}
                        color='info'
                        size='small'
                        sx={{
                            bgcolor: alpha(theme.palette.info.main, 0.2),
                            color: theme.palette.info.main,
                        }}
                    />
                )}
            </Stack>
        </Box>
    );
};

export default AnalyticsDrawingStatus;
