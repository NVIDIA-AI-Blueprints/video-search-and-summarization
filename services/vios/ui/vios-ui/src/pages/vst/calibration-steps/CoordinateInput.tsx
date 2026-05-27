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
import { Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Paper, Typography, Box } from '@mui/material';
import { CalibrationFigure } from './Calibration';

export interface RealWorldCoordinate {
    x: string;
    y: string;
}

interface CoordinateInputProps {
    figures: CalibrationFigure[];
    coordinates: RealWorldCoordinate[];
    onCoordinateChange: (index: number, coordinate: RealWorldCoordinate) => void;
    calibrationType: string;
    disabled?: boolean;
}

const CoordinateInput: React.FC<CoordinateInputProps> = ({
    figures,
    coordinates,
    onCoordinateChange,
    calibrationType,
    disabled = false,
}) => {
    // Get the calibration figure (first figure that matches calibration types)
    const calibrationFigure = figures.find(f => f.class === 'calib' || f.class === 'cartCalib');

    const handleCoordinateChange = (index: number, field: 'x' | 'y', value: string) => {
        const newCoordinate = { ...(coordinates[index] || { x: '0', y: '0' }) };
        newCoordinate[field] = value;
        onCoordinateChange(index, newCoordinate);
    };

    const getCoordinateLabels = () => {
        if (calibrationType === 'cartesian') {
            return { x: 'X Coordinate (cm)', y: 'Y Coordinate (cm)' };
        } else {
            return { x: 'Longitude', y: 'Latitude' };
        }
    };

    const labels = getCoordinateLabels();
    const hasPoints = calibrationFigure && calibrationFigure.points.length > 0;

    return (
        <Paper elevation={2} sx={{ p: 3 }}>
            <Typography variant='h6' gutterBottom>
                Real World Coordinates
            </Typography>
            <Typography variant='body2' color='text.secondary' sx={{ mb: 2 }}>
                {calibrationType === 'cartesian'
                    ? 'Enter the real-world coordinates (in centimeters) for each calibration point:'
                    : 'Enter the geographic coordinates for each calibration point:'}
            </Typography>

            <TableContainer component={Paper} variant='outlined'>
                <Table size='small'>
                    <TableHead>
                        <TableRow>
                            <TableCell>
                                <strong>Point</strong>
                            </TableCell>
                            <TableCell>
                                <strong>Image Pixel</strong>
                            </TableCell>
                            <TableCell>
                                <strong>{labels.x}</strong>
                            </TableCell>
                            <TableCell>
                                <strong>{labels.y}</strong>
                            </TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {hasPoints ? (
                            calibrationFigure.points.map((point, index) => (
                                <TableRow key={index}>
                                    <TableCell>{index}</TableCell>
                                    <TableCell>
                                        <Typography variant='caption' color='text.secondary'>
                                            ({Math.round(point.lng)}, {Math.round(point.lat)})
                                        </Typography>
                                    </TableCell>
                                    <TableCell>
                                        <TextField
                                            size='small'
                                            type='number'
                                            value={coordinates[index]?.x || '0'}
                                            onChange={e => handleCoordinateChange(index, 'x', e.target.value)}
                                            disabled={disabled}
                                            placeholder={calibrationType === 'cartesian' ? '0' : '-122.4194'}
                                            inputProps={{
                                                style: { fontSize: '0.875rem' },
                                            }}
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <TextField
                                            size='small'
                                            type='number'
                                            value={coordinates[index]?.y || '0'}
                                            onChange={e => handleCoordinateChange(index, 'y', e.target.value)}
                                            disabled={disabled}
                                            placeholder={calibrationType === 'cartesian' ? '0' : '37.7749'}
                                            inputProps={{
                                                style: { fontSize: '0.875rem' },
                                            }}
                                        />
                                    </TableCell>
                                </TableRow>
                            ))
                        ) : (
                            <TableRow>
                                <TableCell colSpan={4} align='center' sx={{ py: 4 }}>
                                    <Typography variant='body2' color='text.secondary'>
                                        Please draw calibration points on the image to add coordinate entries.
                                    </Typography>
                                </TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </TableContainer>

            {calibrationType === 'cartesian' && (
                <Box sx={{ mt: 2 }}>
                    <Typography variant='caption' color='text.secondary'>
                        <strong>Note:</strong> For Cartesian calibration, the first point should be the bottom-left corner of your reference
                        area. Coordinates should be in centimeters from your chosen origin point.
                    </Typography>
                </Box>
            )}

            {calibrationType === 'image' && (
                <Box sx={{ mt: 2 }}>
                    <Typography variant='caption' color='text.secondary'>
                        <strong>Note:</strong> For geographic calibration, you need at least 8 points. Enter the precise latitude and
                        longitude for each corresponding point.
                    </Typography>
                </Box>
            )}
        </Paper>
    );
};

export default CoordinateInput;
