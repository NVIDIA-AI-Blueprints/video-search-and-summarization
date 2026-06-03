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
import React, { useState, useEffect } from 'react';
import {
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    TextField,
    FormControl,
    InputLabel,
    Select,
    MenuItem,
    Grid,
    Button,
    CircularProgress,
    Alert,
    Typography,
    Box,
    Accordion,
    AccordionSummary,
    AccordionDetails,
} from '@mui/material';
import { Sensor } from './types';
import config from '../../../config';
import { ExpandMore } from '@mui/icons-material';

interface SensorEditDialogProps {
    open: boolean;
    sensor: Sensor | null;
    onClose: () => void;
    onSuccess?: () => void;
}

interface SensorDetails {
    id: string;
    sensorId?: string;
    sensorName?: string;
    deviceId?: string;
    originLat?: number;
    originLng?: number;
    cardinalDirection?: string;
    fps?: string;
    depth?: string;
    fieldOfView?: string;
    direction?: string;
    videoURL?: string;
    mmsInfo_protocol?: string;
    mmsInfo_type?: string;
    mmsInfo_host?: string;
    corridor_set?: number[];
    intersection_set?: number;
}

const SensorEditDialog: React.FC<SensorEditDialogProps> = ({ open, sensor, onClose, onSuccess }) => {
    const [sensorDetails, setSensorDetails] = useState<SensorDetails | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [editForm, setEditForm] = useState<Partial<SensorDetails>>({});
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        if (open && sensor) {
            fetchSensorDetails();
        }
    }, [open, sensor]);

    const fetchSensorDetails = async () => {
        if (!sensor) return;

        try {
            setLoading(true);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${sensor.id}/`, {
                headers: { streamId: sensor.id },
            });

            if (!response.ok) {
                throw new Error(`Failed to fetch sensor details: ${response.status} ${response.statusText}`);
            }

            const details: SensorDetails = await response.json();
            setSensorDetails(details);

            // Set default values for missing fields
            setEditForm({
                ...details,
                sensorId: details.sensorId || '',
                sensorName: details.sensorName || '',
                deviceId: details.deviceId || '',
                originLat: details.originLat || undefined,
                originLng: details.originLng || undefined,
                cardinalDirection: details.cardinalDirection || 'NW',
                fps: details.fps || '',
                depth: details.depth || '',
                fieldOfView: details.fieldOfView || '',
                direction: details.direction || '',
                videoURL: details.videoURL || '',
                mmsInfo_protocol: details.mmsInfo_protocol || '',
                mmsInfo_type: details.mmsInfo_type || '',
                mmsInfo_host: details.mmsInfo_host || '',
                corridor_set: details.corridor_set || [],
                intersection_set: details.intersection_set || undefined,
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch sensor details');
        } finally {
            setLoading(false);
        }
    };

    const handleInputChange = <K extends keyof SensorDetails>(field: K, value: SensorDetails[K]) => {
        setEditForm(prev => ({
            ...prev,
            [field]: value,
        }));
    };

    const handleSubmit = async () => {
        if (!sensor) return;

        try {
            setSubmitting(true);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${sensor.id}/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    streamId: sensor.id,
                },
                body: JSON.stringify(editForm),
            });

            if (!response.ok) {
                throw new Error(`Failed to update sensor: ${response.status} ${response.statusText}`);
            }

            onSuccess?.();
            onClose();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to update sensor');
        } finally {
            setSubmitting(false);
        }
    };

    const handleCancel = () => {
        setEditForm({});
        setError(null);
        onClose();
    };

    const cardinalDirections = ['NW', 'NNW', 'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW'];

    return (
        <Dialog open={open} onClose={handleCancel} maxWidth='md' fullWidth>
            <DialogTitle>
                <Typography variant='h6' fontWeight='bold'>
                    Edit Sensor Details
                </Typography>
                {sensor && (
                    <Typography variant='body2' color='text.secondary'>
                        Sensor ID: {sensor.sensorId || sensor.id}
                    </Typography>
                )}
            </DialogTitle>

            <DialogContent>
                {error && (
                    <Alert severity='error' sx={{ mb: 2 }}>
                        {error}
                    </Alert>
                )}

                {loading ? (
                    <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
                        <CircularProgress />
                    </Box>
                ) : sensorDetails ? (
                    <Box sx={{ mt: 1 }}>
                        {/* Basic Information */}
                        <Accordion defaultExpanded>
                            <AccordionSummary expandIcon={<ExpandMore />}>
                                <Typography variant='h6' fontWeight='bold'>
                                    Basic Information
                                </Typography>
                            </AccordionSummary>
                            <AccordionDetails>
                                <Grid container spacing={2}>
                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor Id'
                                            value={editForm.sensorId || ''}
                                            onChange={e => handleInputChange('sensorId', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor Name'
                                            value={editForm.sensorName || ''}
                                            onChange={e => handleInputChange('sensorName', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Device Id'
                                            value={editForm.deviceId || ''}
                                            onChange={e => handleInputChange('deviceId', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <FormControl fullWidth margin='normal'>
                                            <InputLabel>Cardinal Direction</InputLabel>
                                            <Select
                                                value={editForm.cardinalDirection || 'NW'}
                                                onChange={e => handleInputChange('cardinalDirection', e.target.value)}
                                                label='Cardinal Direction'
                                            >
                                                {cardinalDirections.map(direction => (
                                                    <MenuItem key={direction} value={direction}>
                                                        {direction}
                                                    </MenuItem>
                                                ))}
                                            </Select>
                                        </FormControl>
                                    </Grid>
                                </Grid>
                            </AccordionDetails>
                        </Accordion>

                        {/* Location Information */}
                        <Accordion defaultExpanded>
                            <AccordionSummary expandIcon={<ExpandMore />}>
                                <Typography variant='h6' fontWeight='bold'>
                                    Location Information
                                </Typography>
                            </AccordionSummary>
                            <AccordionDetails>
                                <Grid container spacing={2}>
                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor Latitude'
                                            type='number'
                                            value={editForm.originLat || ''}
                                            onChange={e => handleInputChange('originLat', parseFloat(e.target.value) || undefined)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor Longitude'
                                            type='number'
                                            value={editForm.originLng || ''}
                                            onChange={e => handleInputChange('originLng', parseFloat(e.target.value) || undefined)}
                                            margin='normal'
                                        />
                                    </Grid>
                                </Grid>
                            </AccordionDetails>
                        </Accordion>

                        {/* Technical Specifications */}
                        <Accordion defaultExpanded>
                            <AccordionSummary expandIcon={<ExpandMore />}>
                                <Typography variant='h6' fontWeight='bold'>
                                    Technical Specifications
                                </Typography>
                            </AccordionSummary>
                            <AccordionDetails>
                                <Grid container spacing={2}>
                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor FPS'
                                            value={editForm.fps || ''}
                                            onChange={e => handleInputChange('fps', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor Depth'
                                            value={editForm.depth || ''}
                                            onChange={e => handleInputChange('depth', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor FOV'
                                            value={editForm.fieldOfView || ''}
                                            onChange={e => handleInputChange('fieldOfView', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Sensor Direction'
                                            value={editForm.direction || ''}
                                            onChange={e => handleInputChange('direction', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>
                                </Grid>
                            </AccordionDetails>
                        </Accordion>

                        {/* Network & Communication */}
                        <Accordion defaultExpanded>
                            <AccordionSummary expandIcon={<ExpandMore />}>
                                <Typography variant='h6' fontWeight='bold'>
                                    Network & Communication
                                </Typography>
                            </AccordionSummary>
                            <AccordionDetails>
                                <Grid container spacing={2}>
                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Video Url'
                                            value={editForm.videoURL || ''}
                                            onChange={e => handleInputChange('videoURL', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='MMS Protocol'
                                            value={editForm.mmsInfo_protocol || ''}
                                            onChange={e => handleInputChange('mmsInfo_protocol', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='MMS Type'
                                            value={editForm.mmsInfo_type || ''}
                                            onChange={e => handleInputChange('mmsInfo_type', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='MMS Host'
                                            value={editForm.mmsInfo_host || ''}
                                            onChange={e => handleInputChange('mmsInfo_host', e.target.value)}
                                            margin='normal'
                                        />
                                    </Grid>
                                </Grid>
                            </AccordionDetails>
                        </Accordion>

                        {/* Traffic Management */}
                        <Accordion defaultExpanded>
                            <AccordionSummary expandIcon={<ExpandMore />}>
                                <Typography variant='h6' fontWeight='bold'>
                                    Traffic Management
                                </Typography>
                            </AccordionSummary>
                            <AccordionDetails>
                                <Grid container spacing={2}>
                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Corridor'
                                            type='number'
                                            value={editForm.corridor_set?.[0] || ''}
                                            onChange={e => {
                                                const value = parseInt(e.target.value);
                                                handleInputChange('corridor_set', value ? [value] : []);
                                            }}
                                            margin='normal'
                                        />
                                    </Grid>

                                    <Grid item xs={12} md={6}>
                                        <TextField
                                            fullWidth
                                            label='Intersection'
                                            type='number'
                                            value={editForm.intersection_set || ''}
                                            onChange={e => handleInputChange('intersection_set', parseInt(e.target.value) || undefined)}
                                            margin='normal'
                                        />
                                    </Grid>
                                </Grid>
                            </AccordionDetails>
                        </Accordion>
                    </Box>
                ) : null}
            </DialogContent>

            <DialogActions>
                <Button onClick={handleCancel} disabled={submitting}>
                    Cancel
                </Button>
                <Button
                    onClick={handleSubmit}
                    variant='contained'
                    disabled={submitting || loading}
                    startIcon={submitting ? <CircularProgress size={16} /> : null}
                >
                    {submitting ? 'Saving...' : 'Save Changes'}
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export default SensorEditDialog;
