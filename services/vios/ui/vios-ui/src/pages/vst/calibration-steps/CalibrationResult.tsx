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
import React, { useMemo } from 'react';
import { Paper, Typography, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Box } from '@mui/material';
import { Info } from '@mui/icons-material';
import { CalibrationFigure } from './Calibration';
import { Point, convertLatLngToXYMatrix, convertProjectedPoint, haversineDistance, euclideanDistance } from './utils/calibrationMath';
import { matrix, multiply } from 'mathjs';

interface CalibrationResultProps {
    homographyMatrix: number[][];
    calibrationFigure: CalibrationFigure;
    realWorldPolygon: Point[]; // The polygon points in real-world coordinates
    calibrationType: string;
    height: number;
}

const CalibrationResult: React.FC<CalibrationResultProps> = ({
    homographyMatrix,
    calibrationFigure,
    realWorldPolygon,
    calibrationType,
    height,
}) => {
    const polygonReprojectionErrors = useMemo(() => {
        if (!homographyMatrix || !calibrationFigure?.points || !realWorldPolygon) {
            return [];
        }

        const homographyMat = matrix(homographyMatrix);

        return calibrationFigure.points.map((imagePoint, index) => {
            if (index >= realWorldPolygon.length) return 0;

            // Convert image point to matrix coordinates
            const matrixPoint = convertLatLngToXYMatrix(imagePoint, height);

            // Apply homography transformation
            const projectedPoint = convertProjectedPoint(multiply(homographyMat, matrixPoint));

            // Calculate distance to corresponding real-world polygon point
            const realWorldPoint = realWorldPolygon[index];

            if (calibrationType === 'geo' || calibrationType === 'image') {
                return haversineDistance(projectedPoint, realWorldPoint);
            } else {
                return euclideanDistance(projectedPoint, realWorldPoint);
            }
        });
    }, [homographyMatrix, calibrationFigure, realWorldPolygon, calibrationType, height]);

    const getErrorUnit = () => {
        return calibrationType === 'cartesian' ? 'meters' : 'meters';
    };

    if (!calibrationFigure?.points || polygonReprojectionErrors.length === 0) {
        return null;
    }

    return (
        <Paper elevation={2} sx={{ p: 3, mt: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <Info sx={{ mr: 1, color: 'info.main' }} />
                <Typography variant='h6'>Reprojection Validation</Typography>
            </Box>

            {/* Detailed Error Table */}
            <TableContainer component={Paper} variant='outlined'>
                <Table size='small'>
                    <TableHead>
                        <TableRow>
                            <TableCell>
                                <strong>Point #</strong>
                            </TableCell>
                            <TableCell>
                                <strong>Image Coordinates</strong>
                            </TableCell>
                            <TableCell>
                                <strong>Real-World Coordinates</strong>
                            </TableCell>
                            <TableCell>
                                <strong>Reprojection Error</strong>
                            </TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {calibrationFigure.points.map((point, index) => {
                            const realPoint = realWorldPolygon[index];
                            const coordinateText = realPoint ? `(${realPoint.lng.toFixed(6)}, ${realPoint.lat.toFixed(6)})` : 'N/A';

                            return (
                                <TableRow key={index}>
                                    <TableCell>{index + 1}</TableCell>
                                    <TableCell>
                                        <Typography variant='caption'>
                                            ({Math.round(point.lng)}, {Math.round(point.lat)})
                                        </Typography>
                                    </TableCell>
                                    <TableCell>
                                        <Typography variant='caption'>{coordinateText}</Typography>
                                    </TableCell>
                                    <TableCell>
                                        <Typography variant='body2' fontWeight='bold' sx={{ fontFamily: 'monospace' }}>
                                            {polygonReprojectionErrors[index]?.toExponential(6) || 'N/A'} {getErrorUnit()}
                                        </Typography>
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </TableContainer>
        </Paper>
    );
};

export default CalibrationResult;
