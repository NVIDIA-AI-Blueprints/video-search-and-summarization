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
import { Paper, Typography, Box, TextField, Button, Alert, Skeleton, Grid2 as Grid, Divider, Chip } from '@mui/material';
import { LocationOn, Save, Refresh } from '@mui/icons-material';
import { Project } from './types';
import config from '../../../config';

interface ProjectDetailsProps {
    projectId: number;
    onProjectUpdated?: (project: Project) => void;
}

interface ProjectFormData {
    originLat: number;
    originLng: number;
    cityPlace: string;
    roomPlace: string;
}

const ProjectDetails: React.FC<ProjectDetailsProps> = ({ projectId, onProjectUpdated }) => {
    const [project, setProject] = useState<Project | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [successMessage, setSuccessMessage] = useState<string | null>(null);
    const [formData, setFormData] = useState<ProjectFormData>({
        originLat: 0,
        originLng: 0,
        cityPlace: '',
        roomPlace: '',
    });

    useEffect(() => {
        fetchProject();
    }, [projectId]);

    const fetchProject = async () => {
        try {
            setLoading(true);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/${projectId}/`);

            if (!response.ok) {
                throw new Error(`Failed to fetch project: ${response.status} ${response.statusText}`);
            }

            const data: Project = await response.json();
            setProject(data);
            setFormData({
                originLat: data.originLat || 0,
                originLng: data.originLng || 0,
                cityPlace: data.cityPlace || '',
                roomPlace: data.roomPlace || '',
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch project');
        } finally {
            setLoading(false);
        }
    };

    const handleSubmit = async () => {
        if (!project) return;

        try {
            setSaving(true);
            setError(null);
            setSuccessMessage(null);

            // Prepare only the modified fields
            const modifiedFields: Partial<Project> = {};

            if (formData.originLat !== project.originLat) {
                modifiedFields.originLat = formData.originLat;
            }
            if (formData.originLng !== project.originLng) {
                modifiedFields.originLng = formData.originLng;
            }
            if (formData.cityPlace !== project.cityPlace) {
                modifiedFields.cityPlace = formData.cityPlace;
            }
            if (formData.roomPlace !== project.roomPlace) {
                modifiedFields.roomPlace = formData.roomPlace;
            }

            // Only proceed if there are changes
            if (Object.keys(modifiedFields).length === 0) {
                setSuccessMessage('No changes to save.');
                setTimeout(() => setSuccessMessage(null), 2000);
                return;
            }

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/${projectId}/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(modifiedFields),
            });

            if (!response.ok) {
                throw new Error(`Failed to update project: ${response.status} ${response.statusText}`);
            }

            // Fetch updated project data
            await fetchProject();
            setSuccessMessage('Project updated successfully!');

            // Clear success message after 3 seconds
            setTimeout(() => setSuccessMessage(null), 3000);

            // Notify parent component
            if (onProjectUpdated && project) {
                onProjectUpdated({ ...project, ...modifiedFields });
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to update project');
        } finally {
            setSaving(false);
        }
    };

    const handleInputChange = (field: keyof ProjectFormData, value: string | number) => {
        setFormData(prev => ({
            ...prev,
            [field]: value,
        }));
    };

    if (loading) {
        return (
            <Paper elevation={2} sx={{ p: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 3 }}>
                    <LocationOn sx={{ mr: 2, color: 'primary.main' }} />
                    <Typography variant='h5' fontWeight='bold'>
                        Project Details
                    </Typography>
                </Box>

                <Grid container spacing={3}>
                    <Grid size={{ xs: 12, md: 6 }}>
                        <Skeleton variant='text' width='40%' height={24} sx={{ mb: 1 }} />
                        <Skeleton variant='rectangular' width='100%' height={56} sx={{ mb: 2 }} />
                    </Grid>
                    <Grid size={{ xs: 12, md: 6 }}>
                        <Skeleton variant='text' width='40%' height={24} sx={{ mb: 1 }} />
                        <Skeleton variant='rectangular' width='100%' height={56} sx={{ mb: 2 }} />
                    </Grid>
                    <Grid size={{ xs: 12, md: 6 }}>
                        <Skeleton variant='text' width='40%' height={24} sx={{ mb: 1 }} />
                        <Skeleton variant='rectangular' width='100%' height={56} sx={{ mb: 2 }} />
                    </Grid>
                    <Grid size={{ xs: 12, md: 6 }}>
                        <Skeleton variant='text' width='40%' height={24} sx={{ mb: 1 }} />
                        <Skeleton variant='rectangular' width='100%' height={56} sx={{ mb: 2 }} />
                    </Grid>
                </Grid>

                <Divider sx={{ my: 3 }} />
                <Skeleton variant='rectangular' width={120} height={40} />
            </Paper>
        );
    }

    if (error && !project) {
        return (
            <Paper elevation={2} sx={{ p: 3 }}>
                <Alert severity='error' sx={{ mb: 2 }}>
                    {error}
                </Alert>
                <Button variant='contained' onClick={fetchProject} startIcon={<Refresh />}>
                    Retry
                </Button>
            </Paper>
        );
    }

    if (!project) {
        return (
            <Paper elevation={2} sx={{ p: 3, textAlign: 'center' }}>
                <Typography variant='h6' color='text.secondary'>
                    Project not found
                </Typography>
            </Paper>
        );
    }

    return (
        <Paper elevation={2} sx={{ p: 3 }}>
            {/* Header */}
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center' }}>
                    <LocationOn sx={{ mr: 2, color: 'primary.main' }} />
                    <Box>
                        <Typography variant='h5' fontWeight='bold'>
                            Project Details
                        </Typography>
                        <Typography variant='body2' color='text.secondary'>
                            {project.name}
                        </Typography>
                    </Box>
                </Box>
                <Chip label={project.calibrationType.toUpperCase()} color='primary' size='small' sx={{ fontWeight: 'bold' }} />
            </Box>

            {/* Success/Error Messages */}
            {successMessage && (
                <Alert severity='success' sx={{ mb: 2 }}>
                    {successMessage}
                </Alert>
            )}
            {error && (
                <Alert severity='error' sx={{ mb: 2 }}>
                    {error}
                </Alert>
            )}

            {/* Form */}
            <Grid container spacing={3}>
                <Grid size={{ xs: 12, md: 6 }}>
                    <TextField
                        fullWidth
                        label='Origin Latitude'
                        type='number'
                        value={formData.originLat}
                        onChange={e => handleInputChange('originLat', parseFloat(e.target.value) || 0)}
                        inputProps={{
                            step: 0.000001,
                            min: -90,
                            max: 90,
                        }}
                        helperText='Latitude coordinate for the project origin'
                    />
                </Grid>
                <Grid size={{ xs: 12, md: 6 }}>
                    <TextField
                        fullWidth
                        label='Origin Longitude'
                        type='number'
                        value={formData.originLng}
                        onChange={e => handleInputChange('originLng', parseFloat(e.target.value) || 0)}
                        inputProps={{
                            step: 0.000001,
                            min: -180,
                            max: 180,
                        }}
                        helperText='Longitude coordinate for the project origin'
                    />
                </Grid>
                <Grid size={{ xs: 12, md: 6 }}>
                    <TextField
                        fullWidth
                        label='City Place'
                        value={formData.cityPlace}
                        onChange={e => handleInputChange('cityPlace', e.target.value)}
                        helperText='City or urban area for this project'
                    />
                </Grid>
                <Grid size={{ xs: 12, md: 6 }}>
                    <TextField
                        fullWidth
                        label='Room Place'
                        value={formData.roomPlace}
                        onChange={e => handleInputChange('roomPlace', e.target.value)}
                        helperText='Specific room or location within the area'
                    />
                </Grid>
            </Grid>

            {/* Action Buttons */}
            <Divider sx={{ my: 3 }} />
            <Box sx={{ display: 'flex', gap: 2 }}>
                <Button variant='contained' startIcon={<Save />} onClick={handleSubmit} disabled={saving}>
                    {saving ? 'Saving...' : 'Save Changes'}
                </Button>
                <Button variant='outlined' startIcon={<Refresh />} onClick={fetchProject} disabled={loading || saving}>
                    Refresh
                </Button>
            </Box>
        </Paper>
    );
};

export default ProjectDetails;
