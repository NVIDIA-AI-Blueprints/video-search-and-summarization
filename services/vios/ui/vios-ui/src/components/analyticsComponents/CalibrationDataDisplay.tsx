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
import { Card, CardContent, CardHeader, Typography, Paper, Stack, Box, Grid2 as Grid } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Sensor } from '../../interfaces/interfaces';

interface CalibrationData {
    sensors: Array<{
        id: string;
        origin: {
            lat: number;
            lng: number;
        };
        homography?: number[][];
        imageCoordinates?: { x: number; y: number }[];
        globalCoordinates?: { x: number; y: number }[];
        scaleFactor?: number;
        [key: string]: unknown;
    }>;
    calibrationType: string;
    version: string;
    [key: string]: unknown;
}

interface CalibrationDataDisplayProps {
    calibrationData: CalibrationData;
    selectedSensor: Sensor;
}

const CalibrationDataDisplay: React.FC<CalibrationDataDisplayProps> = ({ calibrationData, selectedSensor }) => {
    const theme = useTheme();

    const sensorData = calibrationData.sensors.find(s => s.id === selectedSensor.sensorId);

    if (!sensorData) {
        return (
            <Card>
                <CardContent>
                    <Typography color='error'>Sensor data not found</Typography>
                </CardContent>
            </Card>
        );
    }

    const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;

    return (
        <Card>
            <CardHeader
                title='Calibration Data'
                subheader={`Loaded for sensor: ${selectedSensor.name} (${selectedSensor.sensorId}) - ${is2DCalibration ? '2D' : '3D'} Calibration`}
            />
            <CardContent>
                <Stack spacing={2}>
                    <Box>
                        <Typography variant='h6' gutterBottom>
                            Origin Point
                        </Typography>
                        <Paper
                            sx={{
                                p: 2,
                                bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                border: `1px solid ${theme.palette.divider}`,
                            }}
                        >
                            <Typography variant='body2'>
                                <strong>Latitude:</strong> {sensorData.origin.lat}
                            </Typography>
                            <Typography variant='body2'>
                                <strong>Longitude:</strong> {sensorData.origin.lng}
                            </Typography>
                        </Paper>
                    </Box>

                    {!is2DCalibration && sensorData.homography && (
                        <Box>
                            <Typography variant='h6' gutterBottom>
                                Homography Matrix
                            </Typography>
                            <Paper
                                sx={{
                                    p: 2,
                                    bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                    border: `1px solid ${theme.palette.divider}`,
                                }}
                            >
                                <pre
                                    style={{
                                        margin: 0,
                                        fontSize: '12px',
                                        color: theme.palette.text.primary,
                                        fontFamily: 'monospace',
                                    }}
                                >
                                    {JSON.stringify(sensorData.homography, null, 2)}
                                </pre>
                            </Paper>
                        </Box>
                    )}

                    {/* 2D Calibration Specific Information */}
                    {is2DCalibration && (
                        <>
                            <Box>
                                <Typography variant='h6' gutterBottom>
                                    2D Calibration Parameters
                                </Typography>
                                <Paper
                                    sx={{
                                        p: 2,
                                        bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                        border: `1px solid ${theme.palette.divider}`,
                                    }}
                                >
                                    <Typography variant='body2'>
                                        <strong>Scale Factor:</strong> {sensorData.scaleFactor || 'Not specified'}
                                    </Typography>
                                    <Typography variant='body2'>
                                        <strong>Image Reference Points:</strong> {sensorData.imageCoordinates?.length || 0} points
                                    </Typography>
                                    <Typography variant='body2'>
                                        <strong>Global Reference Points:</strong> {sensorData.globalCoordinates?.length || 0} points
                                    </Typography>
                                </Paper>
                            </Box>

                            {sensorData.imageCoordinates && sensorData.globalCoordinates && (
                                <Box>
                                    <Typography variant='h6' gutterBottom>
                                        Reference Point Mapping
                                    </Typography>
                                    <Paper
                                        sx={{
                                            p: 2,
                                            bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                            border: `1px solid ${theme.palette.divider}`,
                                        }}
                                    >
                                        <Grid container spacing={2}>
                                            <Grid size={{ xs: 12, md: 6 }}>
                                                <Typography variant='subtitle2' gutterBottom>
                                                    Image Coordinates (Pixels)
                                                </Typography>
                                                {sensorData.imageCoordinates.map((coord, index) => (
                                                    <Typography key={index} variant='caption' display='block'>
                                                        P{index + 1}: ({coord.x.toFixed(2)}, {coord.y.toFixed(2)})
                                                    </Typography>
                                                ))}
                                            </Grid>
                                            <Grid size={{ xs: 12, md: 6 }}>
                                                <Typography variant='subtitle2' gutterBottom>
                                                    Global Coordinates (World)
                                                </Typography>
                                                {sensorData.globalCoordinates.map((coord, index) => (
                                                    <Typography key={index} variant='caption' display='block'>
                                                        P{index + 1}: ({coord.x.toFixed(3)}, {coord.y.toFixed(3)})
                                                    </Typography>
                                                ))}
                                            </Grid>
                                        </Grid>
                                    </Paper>
                                </Box>
                            )}
                        </>
                    )}
                </Stack>
            </CardContent>
        </Card>
    );
};

export default CalibrationDataDisplay;
