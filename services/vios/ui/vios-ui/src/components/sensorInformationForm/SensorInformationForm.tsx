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
import React, { useState } from 'react';
import { Grid2 as Grid, Card, CardContent, CardActions, TextField, Button, CardHeader, Divider } from '@mui/material';
import { Sensor, SensorPosition } from '../../interfaces/interfaces';
import LOG from '../../utils/misc/Logger';
import nvAxios from '../../services/Axios';
import config from '../../config';
import { useSnackbar } from 'notistack';
import { updateSensorsAndStreams } from '../../utils/misc/updateSensorsAndStreams';

const SensorInformationForm: React.FC<{ initialData: Sensor }> = ({ initialData }) => {
    const [formData, setFormData] = useState<Sensor>(initialData);
    const { enqueueSnackbar } = useSnackbar();

    const handleNameChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        setFormData({ ...formData, name: event.target.value });
    };

    const handleTagsChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        setFormData({ ...formData, tags: event.target.value });
    };

    const handlePositionChange = (field: keyof SensorPosition, value: string) => {
        setFormData({
            ...formData,
            position: { ...formData.position, [field]: value },
        });
    };

    const handleSubmit = () => {
        LOG.verbose('Submitting data:', formData);
        nvAxios
            .post(`${config.sensorManagementEndpoint}/api/v1/sensor/${initialData?.sensorId}/info`, formData, {
                headers: { streamId: initialData?.sensorId },
            })
            .then(() => {
                enqueueSnackbar('Sensor information updated successfully', { variant: 'success' });
                updateSensorsAndStreams();
            })
            .catch(() => {
                LOG.error(`Failed to post sensor information for ${initialData?.name}`);
                enqueueSnackbar('Failed to update sensor information', { variant: 'error' });
            });
    };

    return (
        <Card>
            <CardHeader title={'Sensor information'} subheader={`General information about the sensor`} />
            <CardContent>
                <Grid container spacing={2}>
                    <Grid size={{ xs: 12 }}>
                        <TextField fullWidth label='Name' value={formData.name} onChange={handleNameChange} />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <TextField
                            fullWidth
                            label='Tags'
                            value={formData.tags}
                            onChange={handleTagsChange}
                            helperText='Comma-separated tags'
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Divider>SensorPosition</Divider>
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='Depth'
                            value={formData.position.depth}
                            onChange={e => handlePositionChange('depth', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='Direction'
                            value={formData.position.direction}
                            onChange={e => handlePositionChange('direction', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='Field of View'
                            value={formData.position.fieldOfView}
                            onChange={e => handlePositionChange('fieldOfView', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Divider>Read-only Fields</Divider>
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Firmware Version' value={formData.firmwareVersion} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Hardware' value={formData.hardware} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Hardware ID' value={formData.hardwareId} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Sensor ID' value={formData.sensorId} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Sensor IP' value={formData.sensorIp} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Location' value={formData.location} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Manufacturer' value={formData.manufacturer} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='Serial Number' value={formData.serialNumber} disabled />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField fullWidth label='State' value={formData.state} disabled />
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button variant='contained' color='primary' onClick={handleSubmit}>
                    Submit
                </Button>
            </CardActions>
        </Card>
    );
};

export default SensorInformationForm;
