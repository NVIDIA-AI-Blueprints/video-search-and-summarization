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
import { Card, CardContent, CardHeader, Typography, Paper, Stack, Box, Button, Chip, Grid2 } from '@mui/material';
import { useTheme } from '@mui/material/styles';

interface ExistingROI {
    id: string;
    roiCoordinates: { x: number; y: number; z?: number }[];
    sensors?: string[];
    groups?: string[];
    type?: string;
    restrictedObjectTypes?: string[];
    confinedObjectTypes?: string[];
}

interface ExistingTripwire {
    id: string;
    wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
    direction?: { p1: { x: number; y: number }; p2: { x: number; y: number } };
}

interface ExistingDataDisplayProps {
    existingROIs: ExistingROI[];
    existingTripwires: ExistingTripwire[];
    onReverseTransformROI: (index: number) => void;
    onReverseTransformTripwire: (index: number) => void;
}

const ExistingDataDisplay: React.FC<ExistingDataDisplayProps> = ({
    existingROIs,
    existingTripwires,
    onReverseTransformROI,
    onReverseTransformTripwire,
}) => {
    const theme = useTheme();

    if (existingROIs.length === 0 && existingTripwires.length === 0) {
        return null;
    }

    return (
        <Card>
            <CardHeader title='Existing ROI & Tripwire Data' subheader='Data retrieved from calibration API' />
            <CardContent>
                <Grid2 container spacing={3}>
                    {existingROIs.length > 0 && (
                        <Grid2 size={{ xs: 12, md: existingTripwires.length > 0 ? 6 : 12 }}>
                            <Box>
                                <Typography variant='h6' gutterBottom>
                                    Existing ROIs ({existingROIs.length})
                                </Typography>
                                {existingROIs.map((roi, index) => (
                                    <Paper
                                        key={index}
                                        sx={{
                                            p: 3,
                                            mb: 2,
                                            bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[50],
                                            border: `1px solid ${theme.palette.divider}`,
                                            '&:hover': {
                                                bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                                borderColor: theme.palette.primary.main,
                                            },
                                        }}
                                    >
                                        <Stack spacing={2}>
                                            <Box display='flex' justifyContent='space-between' alignItems='center'>
                                                <Box display='flex' alignItems='center' gap={1}>
                                                    <Typography
                                                        variant='subtitle1'
                                                        fontWeight='bold'
                                                        sx={{ color: theme.palette.text.primary }}
                                                    >
                                                        {roi.id}
                                                    </Typography>
                                                    <Chip label='ROI' size='small' color='info' variant='outlined' />
                                                    {roi.type && (
                                                        <Chip
                                                            label={roi.type}
                                                            size='small'
                                                            color={roi.type === 'hazard_zone' ? 'error' : 'info'}
                                                        />
                                                    )}
                                                </Box>
                                                <Button
                                                    variant='contained'
                                                    color='primary'
                                                    size='small'
                                                    onClick={() => onReverseTransformROI(index)}
                                                    sx={{
                                                        minWidth: 'auto',
                                                        px: 2,
                                                    }}
                                                >
                                                    Convert to Image
                                                </Button>
                                            </Box>

                                            <Box>
                                                <Typography variant='body2' color='text.secondary' gutterBottom>
                                                    <strong>Coordinates:</strong> {roi.roiCoordinates.length} points
                                                </Typography>
                                                <Box
                                                    sx={{
                                                        maxHeight: 200,
                                                        overflow: 'auto',
                                                        bgcolor:
                                                            theme.palette.mode === 'dark'
                                                                ? theme.palette.grey[800]
                                                                : theme.palette.grey[100],
                                                        p: 2,
                                                        borderRadius: 1,
                                                        border: `1px solid ${theme.palette.divider}`,
                                                    }}
                                                >
                                                    {roi.roiCoordinates.map((coord, coordIndex) => (
                                                        <Typography
                                                            key={coordIndex}
                                                            variant='caption'
                                                            display='block'
                                                            sx={{
                                                                fontFamily: 'monospace',
                                                                color: theme.palette.text.secondary,
                                                            }}
                                                        >
                                                            P{coordIndex + 1}: X: {coord.x.toFixed(3)}, Y: {coord.y.toFixed(3)}, Z:{' '}
                                                            {coord.z?.toFixed(3) || '0.000'}
                                                        </Typography>
                                                    ))}
                                                </Box>
                                            </Box>

                                            {roi.sensors && roi.sensors.length > 0 && (
                                                <Box
                                                    sx={{
                                                        p: 2,
                                                        bgcolor:
                                                            theme.palette.mode === 'dark'
                                                                ? theme.palette.grey[800]
                                                                : theme.palette.grey[100],
                                                        borderRadius: 1,
                                                        border: `1px solid ${theme.palette.divider}`,
                                                    }}
                                                >
                                                    <Typography variant='body2' color='text.secondary'>
                                                        <strong>Sensors:</strong>
                                                    </Typography>
                                                    <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                                                        {roi.sensors.map((sensor, sensorIndex) => (
                                                            <Chip
                                                                key={sensorIndex}
                                                                label={sensor}
                                                                size='small'
                                                                variant='outlined'
                                                                color='primary'
                                                            />
                                                        ))}
                                                    </Box>
                                                </Box>
                                            )}

                                            {roi.groups && roi.groups.length > 0 && (
                                                <Box
                                                    sx={{
                                                        p: 2,
                                                        bgcolor:
                                                            theme.palette.mode === 'dark'
                                                                ? theme.palette.grey[800]
                                                                : theme.palette.grey[100],
                                                        borderRadius: 1,
                                                        border: `1px solid ${theme.palette.divider}`,
                                                    }}
                                                >
                                                    <Typography variant='body2' color='text.secondary'>
                                                        <strong>Groups:</strong>
                                                    </Typography>
                                                    <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                                                        {roi.groups.map((group, groupIndex) => (
                                                            <Chip key={groupIndex} label={group} size='small' color='secondary' />
                                                        ))}
                                                    </Box>
                                                </Box>
                                            )}

                                            {roi.restrictedObjectTypes && roi.restrictedObjectTypes.length > 0 && (
                                                <Box
                                                    sx={{
                                                        p: 2,
                                                        bgcolor:
                                                            theme.palette.mode === 'dark'
                                                                ? theme.palette.error.dark
                                                                : theme.palette.error.light,
                                                        borderRadius: 1,
                                                        border: `1px solid ${theme.palette.error.main}`,
                                                    }}
                                                >
                                                    <Typography variant='body2' color='text.primary'>
                                                        <strong>Restricted Objects:</strong>
                                                    </Typography>
                                                    <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                                                        {roi.restrictedObjectTypes.map((objType, objIndex) => (
                                                            <Chip key={objIndex} label={objType} size='small' color='error' />
                                                        ))}
                                                    </Box>
                                                </Box>
                                            )}

                                            {roi.confinedObjectTypes && roi.confinedObjectTypes.length > 0 && (
                                                <Box
                                                    sx={{
                                                        p: 2,
                                                        bgcolor:
                                                            theme.palette.mode === 'dark'
                                                                ? theme.palette.success.dark
                                                                : theme.palette.success.light,
                                                        borderRadius: 1,
                                                        border: `1px solid ${theme.palette.success.main}`,
                                                    }}
                                                >
                                                    <Typography variant='body2' color='text.primary'>
                                                        <strong>Confined Objects:</strong>
                                                    </Typography>
                                                    <Box sx={{ mt: 1, display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                                                        {roi.confinedObjectTypes.map((objType, objIndex) => (
                                                            <Chip key={objIndex} label={objType} size='small' color='success' />
                                                        ))}
                                                    </Box>
                                                </Box>
                                            )}
                                        </Stack>
                                    </Paper>
                                ))}
                            </Box>
                        </Grid2>
                    )}

                    {existingTripwires.length > 0 && (
                        <Grid2 size={{ xs: 12, md: existingROIs.length > 0 ? 6 : 12 }}>
                            <Box>
                                <Typography variant='h6' gutterBottom>
                                    Existing Tripwires ({existingTripwires.length})
                                </Typography>
                                {existingTripwires.map((tripwire, index) => (
                                    <Paper
                                        key={index}
                                        sx={{
                                            p: 3,
                                            mb: 2,
                                            bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[50],
                                            border: `1px solid ${theme.palette.divider}`,
                                            '&:hover': {
                                                bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                                borderColor: theme.palette.secondary.main,
                                            },
                                        }}
                                    >
                                        <Stack spacing={2}>
                                            <Box display='flex' justifyContent='space-between' alignItems='center'>
                                                <Box display='flex' alignItems='center' gap={1}>
                                                    <Typography
                                                        variant='subtitle1'
                                                        fontWeight='bold'
                                                        sx={{ color: theme.palette.text.primary }}
                                                    >
                                                        {tripwire.id}
                                                    </Typography>
                                                    <Chip label='Tripwire' size='small' color='warning' variant='outlined' />
                                                </Box>
                                                <Button
                                                    variant='contained'
                                                    color='secondary'
                                                    size='small'
                                                    onClick={() => onReverseTransformTripwire(index)}
                                                    sx={{
                                                        minWidth: 'auto',
                                                        px: 2,
                                                    }}
                                                >
                                                    Convert to Image
                                                </Button>
                                            </Box>

                                            <Box
                                                sx={{
                                                    bgcolor:
                                                        theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                                    p: 2,
                                                    borderRadius: 1,
                                                    border: `1px solid ${theme.palette.divider}`,
                                                }}
                                            >
                                                <Typography
                                                    variant='body2'
                                                    color='text.secondary'
                                                    sx={{
                                                        fontFamily: 'monospace',
                                                        color: theme.palette.text.secondary,
                                                    }}
                                                >
                                                    <strong>P1:</strong> X: {tripwire.wire.p1.x.toFixed(3)}, Y:{' '}
                                                    {tripwire.wire.p1.y.toFixed(3)}
                                                </Typography>
                                                <Typography
                                                    variant='body2'
                                                    color='text.secondary'
                                                    sx={{
                                                        fontFamily: 'monospace',
                                                        color: theme.palette.text.secondary,
                                                    }}
                                                >
                                                    <strong>P2:</strong> X: {tripwire.wire.p2.x.toFixed(3)}, Y:{' '}
                                                    {tripwire.wire.p2.y.toFixed(3)}
                                                </Typography>
                                            </Box>
                                        </Stack>
                                    </Paper>
                                ))}
                            </Box>
                        </Grid2>
                    )}
                </Grid2>
            </CardContent>
        </Card>
    );
};

export default ExistingDataDisplay;
