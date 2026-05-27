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
import {
    Card,
    CardContent,
    CardHeader,
    TextField,
    Button,
    Typography,
    Paper,
    Stack,
    Divider,
    IconButton,
    Chip,
    Box,
    Grid2 as Grid,
} from '@mui/material';
import { Add, Delete } from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';

interface CoordinateManagementProps {
    // ROI Props
    roiImageCoords: { x: number; y: number }[];
    onROICoordChange: (index: number, field: 'x' | 'y') => (event: React.ChangeEvent<HTMLInputElement>) => void;
    onAddROIPoint: () => void;
    onRemoveROIPoint: (index: number) => void;
    onTransformROI: () => void;
    roiTransformResults: {
        imageCoords: { x: number; y: number }[];
        worldCoords: { x: number; y: number; z: number }[];
    } | null;

    // Tripwire Props
    tripwireImageCoords: {
        p1: { x: number; y: number };
        p2: { x: number; y: number };
    };
    onTripwireChange: (point: 'p1' | 'p2', field: 'x' | 'y') => (event: React.ChangeEvent<HTMLInputElement>) => void;
    onTransformTripwire: () => void;
    tripwireTransformResults: {
        imageCoords: { p1: { x: number; y: number }; p2: { x: number; y: number } };
        worldCoords: { p1: { x: number; y: number; z: number }; p2: { x: number; y: number; z: number } };
    } | null;
}

const CoordinateManagement: React.FC<CoordinateManagementProps> = ({
    roiImageCoords,
    onROICoordChange,
    onAddROIPoint,
    onRemoveROIPoint,
    onTransformROI,
    roiTransformResults,
    tripwireImageCoords,
    onTripwireChange,
    onTransformTripwire,
    tripwireTransformResults,
}) => {
    const theme = useTheme();

    return (
        <>
            {/* ROI Coordinate Management */}
            <Grid size={{ xs: 12 }}>
                <Card>
                    <CardHeader title='ROI Coordinate Management' subheader='Define ROI polygon points (image coordinates)' />
                    <CardContent>
                        <Stack spacing={3}>
                            <Box display='flex' justifyContent='space-between' alignItems='center'>
                                <Typography variant='h6'>ROI Points (Pixels)</Typography>
                                <Button variant='outlined' startIcon={<Add />} onClick={onAddROIPoint} size='small'>
                                    Add Point
                                </Button>
                            </Box>

                            {roiImageCoords.map((coord, index) => (
                                <Box key={index} display='flex' alignItems='center' gap={2}>
                                    <Chip
                                        label={`P${index + 1}`}
                                        size='small'
                                        color='primary'
                                        variant='outlined'
                                        sx={{
                                            minWidth: 40,
                                            fontWeight: 'bold',
                                        }}
                                    />
                                    <TextField
                                        label='X'
                                        type='number'
                                        value={coord.x}
                                        onChange={onROICoordChange(index, 'x')}
                                        size='small'
                                        sx={{ width: 100 }}
                                    />
                                    <TextField
                                        label='Y'
                                        type='number'
                                        value={coord.y}
                                        onChange={onROICoordChange(index, 'y')}
                                        size='small'
                                        sx={{ width: 100 }}
                                    />
                                    {roiImageCoords.length > 3 && (
                                        <IconButton
                                            onClick={() => onRemoveROIPoint(index)}
                                            size='small'
                                            color='error'
                                            sx={{
                                                '&:hover': {
                                                    bgcolor: theme.palette.error.light,
                                                },
                                            }}
                                        >
                                            <Delete />
                                        </IconButton>
                                    )}
                                </Box>
                            ))}

                            <Button variant='contained' color='secondary' onClick={onTransformROI} sx={{ alignSelf: 'flex-start' }}>
                                Transform ROI to World Coordinates
                            </Button>

                            {roiTransformResults && (
                                <>
                                    <Divider />
                                    <Typography variant='h6'>ROI Transformation Results</Typography>

                                    <Grid container spacing={2}>
                                        <Grid size={{ xs: 12, md: 6 }}>
                                            <Paper
                                                sx={{
                                                    p: 2,
                                                    bgcolor:
                                                        theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                                    border: `1px solid ${theme.palette.primary.main}`,
                                                    borderLeft: `4px solid ${theme.palette.primary.main}`,
                                                }}
                                            >
                                                <Typography
                                                    variant='subtitle1'
                                                    gutterBottom
                                                    sx={{
                                                        color: theme.palette.primary.main,
                                                        fontWeight: 'bold',
                                                    }}
                                                >
                                                    Image Coordinates (Pixels)
                                                </Typography>
                                                {roiTransformResults.imageCoords.map((coord, index) => (
                                                    <Typography key={index} variant='body2'>
                                                        <strong>P{index + 1}:</strong> X: {coord.x}, Y: {coord.y}
                                                    </Typography>
                                                ))}
                                            </Paper>
                                        </Grid>

                                        <Grid size={{ xs: 12, md: 6 }}>
                                            <Paper
                                                sx={{
                                                    p: 2,
                                                    bgcolor:
                                                        theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                                    border: `1px solid ${theme.palette.success.main}`,
                                                    borderLeft: `4px solid ${theme.palette.success.main}`,
                                                }}
                                            >
                                                <Typography
                                                    variant='subtitle1'
                                                    gutterBottom
                                                    sx={{
                                                        color: theme.palette.success.main,
                                                        fontWeight: 'bold',
                                                    }}
                                                >
                                                    World Coordinates (for API)
                                                </Typography>
                                                {roiTransformResults.worldCoords.map((coord, index) => (
                                                    <Typography key={index} variant='body2'>
                                                        <strong>P{index + 1}:</strong> X: {coord.x.toFixed(3)}, Y: {coord.y.toFixed(3)}
                                                        {coord.z !== undefined && `, Z: ${coord.z.toFixed(3)}`}
                                                    </Typography>
                                                ))}
                                            </Paper>
                                        </Grid>
                                    </Grid>
                                </>
                            )}
                        </Stack>
                    </CardContent>
                </Card>
            </Grid>

            {/* Tripwire Coordinate Management */}
            <Grid size={{ xs: 12 }}>
                <Card>
                    <CardHeader title='Tripwire Coordinate Management' subheader='Define tripwire line points (image coordinates)' />
                    <CardContent>
                        <Stack spacing={3}>
                            <Typography variant='h6'>Tripwire Points (Pixels)</Typography>

                            <Box display='flex' alignItems='center' gap={2}>
                                <Chip
                                    label='P1'
                                    size='small'
                                    color='secondary'
                                    variant='outlined'
                                    sx={{
                                        minWidth: 40,
                                        fontWeight: 'bold',
                                    }}
                                />
                                <TextField
                                    label='X'
                                    type='number'
                                    value={tripwireImageCoords.p1.x}
                                    onChange={onTripwireChange('p1', 'x')}
                                    size='small'
                                    sx={{ width: 100 }}
                                />
                                <TextField
                                    label='Y'
                                    type='number'
                                    value={tripwireImageCoords.p1.y}
                                    onChange={onTripwireChange('p1', 'y')}
                                    size='small'
                                    sx={{ width: 100 }}
                                />
                            </Box>

                            <Box display='flex' alignItems='center' gap={2}>
                                <Chip
                                    label='P2'
                                    size='small'
                                    color='secondary'
                                    variant='outlined'
                                    sx={{
                                        minWidth: 40,
                                        fontWeight: 'bold',
                                    }}
                                />
                                <TextField
                                    label='X'
                                    type='number'
                                    value={tripwireImageCoords.p2.x}
                                    onChange={onTripwireChange('p2', 'x')}
                                    size='small'
                                    sx={{ width: 100 }}
                                />
                                <TextField
                                    label='Y'
                                    type='number'
                                    value={tripwireImageCoords.p2.y}
                                    onChange={onTripwireChange('p2', 'y')}
                                    size='small'
                                    sx={{ width: 100 }}
                                />
                            </Box>

                            <Button variant='contained' color='secondary' onClick={onTransformTripwire} sx={{ alignSelf: 'flex-start' }}>
                                Transform Tripwire to World Coordinates
                            </Button>

                            {tripwireTransformResults && (
                                <>
                                    <Divider />
                                    <Typography variant='h6'>Tripwire Transformation Results</Typography>

                                    <Grid container spacing={2}>
                                        <Grid size={{ xs: 12, md: 6 }}>
                                            <Paper
                                                sx={{
                                                    p: 2,
                                                    bgcolor:
                                                        theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                                    border: `1px solid ${theme.palette.warning.main}`,
                                                    borderLeft: `4px solid ${theme.palette.warning.main}`,
                                                }}
                                            >
                                                <Typography
                                                    variant='subtitle1'
                                                    gutterBottom
                                                    sx={{
                                                        color: theme.palette.warning.main,
                                                        fontWeight: 'bold',
                                                    }}
                                                >
                                                    Image Coordinates (Pixels)
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>P1:</strong> X: {tripwireTransformResults.imageCoords.p1.x}, Y:{' '}
                                                    {tripwireTransformResults.imageCoords.p1.y}
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>P2:</strong> X: {tripwireTransformResults.imageCoords.p2.x}, Y:{' '}
                                                    {tripwireTransformResults.imageCoords.p2.y}
                                                </Typography>
                                            </Paper>
                                        </Grid>

                                        <Grid size={{ xs: 12, md: 6 }}>
                                            <Paper
                                                sx={{
                                                    p: 2,
                                                    bgcolor:
                                                        theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
                                                    border: `1px solid ${theme.palette.info.main}`,
                                                    borderLeft: `4px solid ${theme.palette.info.main}`,
                                                }}
                                            >
                                                <Typography
                                                    variant='subtitle1'
                                                    gutterBottom
                                                    sx={{
                                                        color: theme.palette.info.main,
                                                        fontWeight: 'bold',
                                                    }}
                                                >
                                                    World Coordinates (for API)
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>P1:</strong> X: {tripwireTransformResults.worldCoords.p1.x.toFixed(3)}, Y:{' '}
                                                    {tripwireTransformResults.worldCoords.p1.y.toFixed(3)}
                                                    {tripwireTransformResults.worldCoords.p1.z !== undefined &&
                                                        `, Z: ${tripwireTransformResults.worldCoords.p1.z.toFixed(3)}`}
                                                </Typography>
                                                <Typography variant='body2'>
                                                    <strong>P2:</strong> X: {tripwireTransformResults.worldCoords.p2.x.toFixed(3)}, Y:{' '}
                                                    {tripwireTransformResults.worldCoords.p2.y.toFixed(3)}
                                                    {tripwireTransformResults.worldCoords.p2.z !== undefined &&
                                                        `, Z: ${tripwireTransformResults.worldCoords.p2.z.toFixed(3)}`}
                                                </Typography>
                                            </Paper>
                                        </Grid>
                                    </Grid>
                                </>
                            )}
                        </Stack>
                    </CardContent>
                </Card>
            </Grid>
        </>
    );
};

export default CoordinateManagement;
