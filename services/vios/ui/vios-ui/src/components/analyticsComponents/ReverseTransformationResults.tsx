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
import { Card, CardContent, CardHeader, Typography, Paper, Stack, Divider, Grid2 as Grid } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { ReverseTransformResults, ReverseTripwireResults, CalibrationData } from './types';
import { Sensor } from '../../interfaces/interfaces';
import VisualizationCanvas from './VisualizationCanvas';

interface ReverseTransformationResultsProps {
    reversedROIResults: ReverseTransformResults | null;
    reversedTripwireResults: ReverseTripwireResults | null;
    calibrationData: CalibrationData | null;
    selectedSensor: Sensor | null;
    lastTransformedROIId?: string | null;
    lastTransformedTripwireId?: string | null;
}

const ReverseTransformationResults: React.FC<ReverseTransformationResultsProps> = ({
    reversedROIResults,
    reversedTripwireResults,
    calibrationData,
    selectedSensor,
    lastTransformedROIId,
    lastTransformedTripwireId,
}) => {
    const theme = useTheme();

    if (!reversedROIResults && !reversedTripwireResults) {
        return null;
    }

    // Extract frame dimensions from sensor data
    let frameWidth = 1920; // Default values
    let frameHeight = 1080;

    if (calibrationData && selectedSensor) {
        const sensorData = calibrationData.sensors.find(s => s.id === selectedSensor.sensorId);
        if (sensorData?.attributes) {
            const widthAttr = sensorData.attributes.find(attr => attr.name === 'frameWidth');
            const heightAttr = sensorData.attributes.find(attr => attr.name === 'frameHeight');

            if (widthAttr?.value) frameWidth = parseInt(widthAttr.value, 10);
            if (heightAttr?.value) frameHeight = parseInt(heightAttr.value, 10);
        }
    }

    return (
        <Card>
            <CardHeader title='Reverse Transformation Results' subheader='World coordinates converted back to image coordinates' />
            <CardContent>
                <Stack spacing={3}>
                    {/* Visualization Canvas */}
                    <VisualizationCanvas
                        frameWidth={frameWidth}
                        frameHeight={frameHeight}
                        roiImageCoords={reversedROIResults?.imageCoords}
                        tripwireImageCoords={reversedTripwireResults?.imageCoords}
                        roiId={lastTransformedROIId || undefined}
                        tripwireId={lastTransformedTripwireId || undefined}
                        sensor={selectedSensor || undefined}
                        showLiveBackground={true}
                    />
                    {reversedROIResults && (
                        <>
                            <Typography variant='h6'>Reversed ROI Coordinates</Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Paper
                                        sx={{
                                            p: 2,
                                            bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                            border: `1px solid ${theme.palette.error.main}`,
                                            borderLeft: `4px solid ${theme.palette.error.main}`,
                                        }}
                                    >
                                        <Typography
                                            variant='subtitle1'
                                            gutterBottom
                                            sx={{ color: theme.palette.error.main, fontWeight: 'bold' }}
                                        >
                                            Original World Coordinates
                                        </Typography>
                                        {reversedROIResults.worldCoords.map((coord, index) => (
                                            <Typography key={index} variant='body2'>
                                                <strong>P{index + 1}:</strong> X: {coord.x.toFixed(3)}, Y: {coord.y.toFixed(3)}
                                                {coord.z !== undefined && `, Z: ${coord.z.toFixed(3)}`}
                                            </Typography>
                                        ))}
                                    </Paper>
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Paper
                                        sx={{
                                            p: 2,
                                            bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                            border: `1px solid ${theme.palette.secondary.main}`,
                                            borderLeft: `4px solid ${theme.palette.secondary.main}`,
                                        }}
                                    >
                                        <Typography
                                            variant='subtitle1'
                                            gutterBottom
                                            sx={{ color: theme.palette.secondary.main, fontWeight: 'bold' }}
                                        >
                                            Reversed Image Coordinates
                                        </Typography>
                                        {reversedROIResults.imageCoords.map((coord, index) => (
                                            <Typography key={index} variant='body2'>
                                                <strong>P{index + 1}:</strong> X: {coord.x.toFixed(2)}, Y: {coord.y.toFixed(2)}
                                            </Typography>
                                        ))}
                                    </Paper>
                                </Grid>
                            </Grid>
                        </>
                    )}

                    {reversedTripwireResults && (
                        <>
                            <Divider />
                            <Typography variant='h6'>Reversed Tripwire Coordinates</Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Paper
                                        sx={{
                                            p: 2,
                                            bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                            border: `1px solid ${theme.palette.error.main}`,
                                            borderLeft: `4px solid ${theme.palette.error.main}`,
                                        }}
                                    >
                                        <Typography
                                            variant='subtitle1'
                                            gutterBottom
                                            sx={{ color: theme.palette.error.main, fontWeight: 'bold' }}
                                        >
                                            Original World Coordinates
                                        </Typography>
                                        <Typography variant='body2' sx={{ fontWeight: 'bold', color: theme.palette.secondary.main }}>
                                            Wire:
                                        </Typography>
                                        <Typography variant='body2'>
                                            <strong>W1:</strong> X: {reversedTripwireResults.worldCoords.wire.p1.x.toFixed(3)}, Y:{' '}
                                            {reversedTripwireResults.worldCoords.wire.p1.y.toFixed(3)}
                                        </Typography>
                                        <Typography variant='body2'>
                                            <strong>W2:</strong> X: {reversedTripwireResults.worldCoords.wire.p2.x.toFixed(3)}, Y:{' '}
                                            {reversedTripwireResults.worldCoords.wire.p2.y.toFixed(3)}
                                        </Typography>
                                        {reversedTripwireResults.worldCoords.direction && (
                                            <>
                                                <Typography
                                                    variant='body2'
                                                    sx={{ fontWeight: 'bold', color: theme.palette.warning.main, mt: 1 }}
                                                >
                                                    Direction:
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>D1:</strong> X: {reversedTripwireResults.worldCoords.direction.p1.x.toFixed(3)},
                                                    Y: {reversedTripwireResults.worldCoords.direction.p1.y.toFixed(3)}
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>D2:</strong> X: {reversedTripwireResults.worldCoords.direction.p2.x.toFixed(3)},
                                                    Y: {reversedTripwireResults.worldCoords.direction.p2.y.toFixed(3)}
                                                </Typography>
                                            </>
                                        )}
                                    </Paper>
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Paper
                                        sx={{
                                            p: 2,
                                            bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                            border: `1px solid ${theme.palette.secondary.main}`,
                                            borderLeft: `4px solid ${theme.palette.secondary.main}`,
                                        }}
                                    >
                                        <Typography
                                            variant='subtitle1'
                                            gutterBottom
                                            sx={{ color: theme.palette.secondary.main, fontWeight: 'bold' }}
                                        >
                                            Reversed Image Coordinates
                                        </Typography>
                                        <Typography variant='body2' sx={{ fontWeight: 'bold', color: theme.palette.secondary.main }}>
                                            Wire:
                                        </Typography>
                                        <Typography variant='body2'>
                                            <strong>W1:</strong> X: {reversedTripwireResults.imageCoords.wire.p1.x.toFixed(2)}, Y:{' '}
                                            {reversedTripwireResults.imageCoords.wire.p1.y.toFixed(2)}
                                        </Typography>
                                        <Typography variant='body2'>
                                            <strong>W2:</strong> X: {reversedTripwireResults.imageCoords.wire.p2.x.toFixed(2)}, Y:{' '}
                                            {reversedTripwireResults.imageCoords.wire.p2.y.toFixed(2)}
                                        </Typography>
                                        {reversedTripwireResults.imageCoords.direction && (
                                            <>
                                                <Typography
                                                    variant='body2'
                                                    sx={{ fontWeight: 'bold', color: theme.palette.warning.main, mt: 1 }}
                                                >
                                                    Direction:
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>D1:</strong> X: {reversedTripwireResults.imageCoords.direction.p1.x.toFixed(2)},
                                                    Y: {reversedTripwireResults.imageCoords.direction.p1.y.toFixed(2)}
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>D2:</strong> X: {reversedTripwireResults.imageCoords.direction.p2.x.toFixed(2)},
                                                    Y: {reversedTripwireResults.imageCoords.direction.p2.y.toFixed(2)}
                                                </Typography>
                                            </>
                                        )}
                                    </Paper>
                                </Grid>
                            </Grid>
                        </>
                    )}
                </Stack>
            </CardContent>
        </Card>
    );
};

export default ReverseTransformationResults;
