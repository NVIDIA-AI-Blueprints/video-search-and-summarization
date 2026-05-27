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
import { Box, Typography, FormControl, InputLabel, Select, MenuItem, Chip, alpha, Divider } from '@mui/material';
import { SelectChangeEvent } from '@mui/material/Select';
import { useTheme } from '@mui/material/styles';
import { StreamType } from 'vst-streaming-lib';
import { CalibrationData } from './AnalyticsTypes';

interface ExistingROI {
    id: string;
    roiCoordinates: Array<{ x: number; y: number; z: number }>;
}

interface ExistingTripwire {
    id: string;
    wire: {
        p1: { x: number; y: number };
        p2: { x: number; y: number };
    };
    direction?: {
        p1: { x: number; y: number };
        p2: { x: number; y: number };
    };
}

interface AnalyticsExistingItemsSelectorProps {
    streamType: StreamType;
    calibrationData: CalibrationData | null;
    selectedSensor?: { sensorId: string; name?: string };
    selectedROIIds: string[];
    selectedTripwireIds: string[];
    onROISelectionChange: (selectedIds: string[]) => void;
    onTripwireSelectionChange: (selectedIds: string[]) => void;
    isLoadingCalibration: boolean;
}

const AnalyticsExistingItemsSelector: React.FC<AnalyticsExistingItemsSelectorProps> = ({
    streamType,
    calibrationData,
    selectedSensor,
    selectedROIIds,
    selectedTripwireIds,
    onROISelectionChange,
    onTripwireSelectionChange,
    isLoadingCalibration,
}) => {
    const theme = useTheme();

    // Only show for Live and Replay streams
    if (streamType === StreamType.VideoWall) {
        return null;
    }

    // Don't show if no calibration data or sensor
    if (!calibrationData || !selectedSensor || isLoadingCalibration) {
        return null;
    }

    // Get existing ROIs and Tripwires for the selected sensor
    const sensorData = calibrationData.sensors.find(s => s.id === selectedSensor.sensorId);
    const existingROIs: ExistingROI[] = sensorData?.rois || [];
    const existingTripwires: ExistingTripwire[] = sensorData?.tripwires || [];

    // Don't show if no existing items
    if (existingROIs.length === 0 && existingTripwires.length === 0) {
        return null;
    }

    const handleROIChange = (event: SelectChangeEvent<string[]>) => {
        const value = event.target.value;
        onROISelectionChange(typeof value === 'string' ? value.split(',') : value);
    };

    const handleTripwireChange = (event: SelectChangeEvent<string[]>) => {
        const value = event.target.value;
        onTripwireSelectionChange(typeof value === 'string' ? value.split(',') : value);
    };

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
            {/* Calibration Type Display */}
            <Box sx={{ mb: 2 }}>
                <Typography variant='subtitle2' color='text.primary' sx={{ fontWeight: 600, mb: 0.5 }}>
                    Calibration Information
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                    <Chip
                        label={calibrationData.calibrationType}
                        size='small'
                        variant='outlined'
                        color='info'
                        sx={{
                            fontWeight: 500,
                            bgcolor: alpha(theme.palette.info.main, 0.1),
                            borderColor: alpha(theme.palette.info.main, 0.3),
                            color: theme.palette.info.main,
                        }}
                    />
                    <Chip
                        label={`${existingROIs.length} ROIs`}
                        size='small'
                        variant='outlined'
                        color='primary'
                        sx={{
                            fontWeight: 500,
                            bgcolor: alpha(theme.palette.primary.main, 0.1),
                            borderColor: alpha(theme.palette.primary.main, 0.3),
                            color: theme.palette.primary.main,
                        }}
                    />
                    <Chip
                        label={`${existingTripwires.length} Tripwires`}
                        size='small'
                        variant='outlined'
                        color='secondary'
                        sx={{
                            fontWeight: 500,
                            bgcolor: alpha(theme.palette.secondary.main, 0.1),
                            borderColor: alpha(theme.palette.secondary.main, 0.3),
                            color: theme.palette.secondary.main,
                        }}
                    />
                </Box>
            </Box>

            <Divider sx={{ mb: 2 }} />

            <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                {/* ROI Selector */}
                {existingROIs.length > 0 && (
                    <FormControl sx={{ minWidth: 200, flex: 1 }} size='small'>
                        <InputLabel id='existing-roi-label'>ROIs</InputLabel>
                        <Select
                            labelId='existing-roi-label'
                            multiple
                            value={selectedROIIds}
                            onChange={handleROIChange}
                            label='ROIs'
                            renderValue={selected => (
                                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                                    {selected.map(value => (
                                        <Chip
                                            key={value}
                                            label={value}
                                            size='small'
                                            color='primary'
                                            sx={{
                                                bgcolor: alpha(theme.palette.primary.main, 0.2),
                                                color: theme.palette.primary.main,
                                            }}
                                        />
                                    ))}
                                </Box>
                            )}
                            sx={{
                                '& .MuiOutlinedInput-root': {
                                    bgcolor: theme.palette.background.paper,
                                },
                            }}
                        >
                            {existingROIs.map(roi => (
                                <MenuItem key={roi.id} value={roi.id}>
                                    <Typography variant='body2'>
                                        {roi.id} ({roi.roiCoordinates.length} points)
                                    </Typography>
                                </MenuItem>
                            ))}
                        </Select>
                    </FormControl>
                )}

                {/* Tripwire Selector */}
                {existingTripwires.length > 0 && (
                    <FormControl sx={{ minWidth: 200, flex: 1 }} size='small'>
                        <InputLabel id='existing-tripwire-label'>Tripwires</InputLabel>
                        <Select
                            labelId='existing-tripwire-label'
                            multiple
                            value={selectedTripwireIds}
                            onChange={handleTripwireChange}
                            label='Tripwires'
                            renderValue={selected => (
                                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                                    {selected.map(value => (
                                        <Chip
                                            key={value}
                                            label={value}
                                            size='small'
                                            color='secondary'
                                            sx={{
                                                bgcolor: alpha(theme.palette.secondary.main, 0.2),
                                                color: theme.palette.secondary.main,
                                            }}
                                        />
                                    ))}
                                </Box>
                            )}
                            sx={{
                                '& .MuiOutlinedInput-root': {
                                    bgcolor: theme.palette.background.paper,
                                },
                            }}
                        >
                            {existingTripwires.map(tripwire => (
                                <MenuItem key={tripwire.id} value={tripwire.id}>
                                    <Typography variant='body2'>
                                        {tripwire.id} {tripwire.direction ? '(with direction)' : '(line only)'}
                                    </Typography>
                                </MenuItem>
                            ))}
                        </Select>
                    </FormControl>
                )}
            </Box>

            {(selectedROIIds.length > 0 || selectedTripwireIds.length > 0) && (
                <>
                    <Divider sx={{ my: 1 }} />
                    <Typography variant='caption' color='text.secondary'>
                        Selected items will be displayed on the video as overlays (converted from world coordinates)
                    </Typography>
                </>
            )}
        </Box>
    );
};

export default AnalyticsExistingItemsSelector;
