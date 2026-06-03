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
import React, { useState, useEffect, useRef } from 'react';
import { Box, Card, CardContent, TextField, Typography, Grid2 as Grid, Skeleton } from '@mui/material';
import { LoadingButton } from '@mui/lab';
import { useSnackbar } from 'notistack';
import { Sensor } from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';
import { updateSensorsAndStreams } from '../../utils/misc/updateSensorsAndStreams';

interface MediaConfigurationFormProps {
    initialData: Sensor;
}

const MediaConfigurationForm: React.FC<MediaConfigurationFormProps> = ({ initialData }) => {
    const [sensorInfo, setSensorInfo] = useState<Sensor | null>(null);
    const [loading, setLoading] = useState(false);
    const { enqueueSnackbar } = useSnackbar();

    // Refs for form fields
    const nameRef = useRef<HTMLInputElement>(null);
    const tagsRef = useRef<HTMLInputElement>(null);
    const directionRef = useRef<HTMLInputElement>(null);
    const depthRef = useRef<HTMLInputElement>(null);
    const fieldOFViewRef = useRef<HTMLInputElement>(null);
    const originLatitudeRef = useRef<HTMLInputElement>(null);
    const originLongitudeRef = useRef<HTMLInputElement>(null);
    const geoLocationLatitudeRef = useRef<HTMLInputElement>(null);
    const geoLocationLongitudeRef = useRef<HTMLInputElement>(null);
    const coordinateXRef = useRef<HTMLInputElement>(null);
    const coordinateYRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        const fetchSensorInfo = async () => {
            try {
                setLoading(true);
                const response = await nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/sensor/${initialData.sensorId}/info`, {
                    headers: { streamId: initialData.sensorId },
                });
                setSensorInfo(response.data);
                enqueueSnackbar(`Success - Retrieved info for file: ${initialData.name}`, {
                    variant: 'success',
                    anchorOrigin: {
                        horizontal: 'right',
                        vertical: 'bottom',
                    },
                });
            } catch (error) {
                LOG.error(`Failed to fetch sensor info for ${initialData.sensorId}`);
                enqueueSnackbar(`Error - Failed to retrieve info for file: ${initialData.name}`, {
                    variant: 'error',
                    anchorOrigin: {
                        horizontal: 'right',
                        vertical: 'bottom',
                    },
                });
            } finally {
                setLoading(false);
            }
        };

        fetchSensorInfo();
    }, [initialData.sensorId]);

    const handleSubmit = async () => {
        if (!sensorInfo) return;

        const updatedInfo = {
            ...sensorInfo,
            name: nameRef.current?.value || sensorInfo.name,
            tags: tagsRef.current?.value || sensorInfo.tags,
            position: {
                ...sensorInfo.position,
                direction: directionRef.current?.value || sensorInfo.position.direction,
                depth: depthRef.current?.value || sensorInfo.position.depth,
                fieldOfView: fieldOFViewRef.current?.value || sensorInfo.position.fieldOfView,
                origin: {
                    latitude: originLatitudeRef.current?.value || sensorInfo.position.origin.latitude,
                    longitude: originLongitudeRef.current?.value || sensorInfo.position.origin.longitude,
                },
                geoLocation: {
                    latitude: geoLocationLatitudeRef.current?.value || sensorInfo.position.geoLocation.latitude,
                    longitude: geoLocationLongitudeRef.current?.value || sensorInfo.position.geoLocation.longitude,
                },
                coordinates: {
                    x: coordinateXRef.current?.value || sensorInfo.position.coordinates.x,
                    y: coordinateYRef.current?.value || sensorInfo.position.coordinates.y,
                },
            },
        };

        try {
            await nvAxios.post(`${config.sensorManagementEndpoint}/api/v1/sensor/${initialData.sensorId}/info`, updatedInfo, {
                headers: {
                    streamId: initialData.sensorId,
                },
            });
            setSensorInfo(updatedInfo);
            await updateSensorsAndStreams();
            enqueueSnackbar(`Success - Updated file info for ${sensorInfo.name}`, {
                variant: 'success',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });
        } catch (error) {
            LOG.error(`Failed to update sensor info for ${initialData.sensorId}`);
            enqueueSnackbar(`Error - Failed to update file info for ${sensorInfo?.name}`, {
                variant: 'error',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });
        }
    };

    if (!sensorInfo) {
        return (
            <Grid container spacing={3}>
                <Grid size={{ xs: 12 }}>
                    <Card sx={{ height: '100%' }}>
                        <CardContent>
                            <Typography variant='h4' gutterBottom>
                                <Skeleton width='50%' />
                            </Typography>

                            <Box sx={{ mb: 4 }}>
                                <Typography
                                    variant='subtitle1'
                                    sx={{
                                        mb: 2,
                                        color: 'text.secondary',
                                        fontWeight: 500,
                                    }}
                                >
                                    <Skeleton width='40%' />
                                </Typography>
                                <Grid container spacing={2}>
                                    <Grid size={{ xs: 12 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 12 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                </Grid>
                            </Box>

                            <Box sx={{ mb: 4 }}>
                                <Typography
                                    variant='subtitle1'
                                    sx={{
                                        mb: 2,
                                        color: 'text.secondary',
                                        fontWeight: 500,
                                    }}
                                >
                                    <Skeleton width='40%' />
                                </Typography>
                                <Grid container spacing={2}>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 12 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                </Grid>
                            </Box>

                            <Box sx={{ mb: 4 }}>
                                <Typography
                                    variant='subtitle1'
                                    sx={{
                                        mb: 2,
                                        color: 'text.secondary',
                                        fontWeight: 500,
                                    }}
                                >
                                    <Skeleton width='40%' />
                                </Typography>
                                <Grid container spacing={2}>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                </Grid>
                            </Box>

                            <Box sx={{ mb: 4 }}>
                                <Typography
                                    variant='subtitle1'
                                    sx={{
                                        mb: 2,
                                        color: 'text.secondary',
                                        fontWeight: 500,
                                    }}
                                >
                                    <Skeleton width='40%' />
                                </Typography>
                                <Grid container spacing={2}>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                </Grid>
                            </Box>

                            <Box sx={{ mb: 4 }}>
                                <Typography
                                    variant='subtitle1'
                                    sx={{
                                        mb: 2,
                                        color: 'text.secondary',
                                        fontWeight: 500,
                                    }}
                                >
                                    <Skeleton width='40%' />
                                </Typography>
                                <Grid container spacing={2}>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 12, md: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                </Grid>
                            </Box>

                            <Box
                                sx={{
                                    mt: 4,
                                    display: 'flex',
                                    justifyContent: 'flex-start',
                                }}
                            >
                                <Skeleton width={120} height={42} />
                            </Box>
                        </CardContent>
                    </Card>
                </Grid>
            </Grid>
        );
    }

    return (
        <Grid container spacing={3}>
            <Grid size={{ xs: 12 }}>
                <Card sx={{ height: '100%' }}>
                    <CardContent>
                        <Typography variant='h4' gutterBottom>
                            Media Configuration
                        </Typography>

                        <Box sx={{ mb: 4 }}>
                            <Typography
                                variant='subtitle1'
                                sx={{
                                    mb: 2,
                                    color: 'text.secondary',
                                    fontWeight: 500,
                                }}
                            >
                                General Information
                            </Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12 }}>
                                    <TextField
                                        name='name'
                                        label='Name'
                                        defaultValue={sensorInfo.name}
                                        inputRef={nameRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                                <Grid size={{ xs: 12 }}>
                                    <TextField
                                        name='tags'
                                        label='Tags'
                                        defaultValue={sensorInfo.tags}
                                        inputRef={tagsRef}
                                        fullWidth
                                        size='small'
                                        helperText='Comma-separated tags'
                                    />
                                </Grid>
                            </Grid>
                        </Box>

                        <Box sx={{ mb: 4 }}>
                            <Typography
                                variant='subtitle1'
                                sx={{
                                    mb: 2,
                                    color: 'text.secondary',
                                    fontWeight: 500,
                                }}
                            >
                                Position Information
                            </Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='direction'
                                        label='Direction'
                                        defaultValue={sensorInfo.position.direction}
                                        inputRef={directionRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='depth'
                                        label='Depth'
                                        defaultValue={sensorInfo.position.depth}
                                        inputRef={depthRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                                <Grid size={{ xs: 12 }}>
                                    <TextField
                                        name='fieldOfView'
                                        label='Field of View'
                                        defaultValue={sensorInfo.position.fieldOfView}
                                        inputRef={fieldOFViewRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                            </Grid>
                        </Box>

                        <Box sx={{ mb: 4 }}>
                            <Typography
                                variant='subtitle1'
                                sx={{
                                    mb: 2,
                                    color: 'text.secondary',
                                    fontWeight: 500,
                                }}
                            >
                                Origin Coordinates
                            </Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='originLatitude'
                                        label='Origin Latitude'
                                        defaultValue={sensorInfo.position.origin.latitude}
                                        inputRef={originLatitudeRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='originLongitude'
                                        label='Origin Longitude'
                                        defaultValue={sensorInfo.position.origin.longitude}
                                        inputRef={originLongitudeRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                            </Grid>
                        </Box>

                        <Box sx={{ mb: 4 }}>
                            <Typography
                                variant='subtitle1'
                                sx={{
                                    mb: 2,
                                    color: 'text.secondary',
                                    fontWeight: 500,
                                }}
                            >
                                Geo Location
                            </Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='geoLocationLatitude'
                                        label='Geo Location Latitude'
                                        defaultValue={sensorInfo.position.geoLocation.latitude}
                                        inputRef={geoLocationLatitudeRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='geoLocationLongitude'
                                        label='Geo Location Longitude'
                                        defaultValue={sensorInfo.position.geoLocation.longitude}
                                        inputRef={geoLocationLongitudeRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                            </Grid>
                        </Box>

                        <Box sx={{ mb: 4 }}>
                            <Typography
                                variant='subtitle1'
                                sx={{
                                    mb: 2,
                                    color: 'text.secondary',
                                    fontWeight: 500,
                                }}
                            >
                                Local Coordinates
                            </Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='coordinateX'
                                        label='Coordinate X'
                                        defaultValue={sensorInfo.position.coordinates.x}
                                        inputRef={coordinateXRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        name='coordinateY'
                                        label='Coordinate Y'
                                        defaultValue={sensorInfo.position.coordinates.y}
                                        inputRef={coordinateYRef}
                                        fullWidth
                                        size='small'
                                    />
                                </Grid>
                            </Grid>
                        </Box>

                        {sensorInfo != null && (
                            <Box
                                sx={{
                                    mt: 4,
                                    display: 'flex',
                                    justifyContent: 'flex-start',
                                }}
                            >
                                <LoadingButton
                                    size='large'
                                    type='submit'
                                    variant='contained'
                                    onClick={handleSubmit}
                                    loading={loading}
                                    sx={{ minWidth: 120 }}
                                >
                                    Save Changes
                                </LoadingButton>
                            </Box>
                        )}
                    </CardContent>
                </Card>
            </Grid>
        </Grid>
    );
};

export default MediaConfigurationForm;
