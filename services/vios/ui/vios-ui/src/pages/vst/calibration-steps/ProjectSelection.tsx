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
    Paper,
    Typography,
    Grid2 as Grid,
    Card,
    CardContent,
    Chip,
    Box,
    Alert,
    Button,
    Tooltip,
    Skeleton,
    IconButton,
} from '@mui/material';
import { VideoCameraFront, SettingsInputComponent, TaskAlt, CalendarToday, Update, CheckCircle, Delete } from '@mui/icons-material';
import { Project, ProjectStats } from './types';
import ProjectCreateForm from './ProjectCreateForm';
import config from '../../../config';

interface ProjectSelectionProps {
    onProjectSelect?: (project: Project) => void;
    onProjectsLoaded?: (projects: Project[]) => void;
    selectedProjectId?: number;
}

const ProjectSelection: React.FC<ProjectSelectionProps> = ({ onProjectSelect, onProjectsLoaded, selectedProjectId }) => {
    const [projects, setProjects] = useState<Project[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [deletingProjectId, setDeletingProjectId] = useState<number | null>(null);

    // Add helper function to check if project is selectable
    const isProjectSelectable = (project: Project): boolean => {
        return project.calibrationType === 'image' || project.calibrationType === 'cartesian';
    };

    useEffect(() => {
        fetchProjects();
    }, []);

    const fetchProjects = async () => {
        try {
            setLoading(true);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/`);

            if (!response.ok) {
                throw new Error(`Failed to fetch projects: ${response.status} ${response.statusText}`);
            }

            const data: Project[] = await response.json();
            setProjects(data);
            // Call the callback to notify parent about loaded projects
            onProjectsLoaded?.(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch projects');
        } finally {
            setLoading(false);
        }
    };

    const deleteProject = async (projectId: number, projectName: string) => {
        const confirmed = window.confirm(`Are you sure you want to delete the project "${projectName}"? This action cannot be undone.`);

        if (!confirmed) {
            return;
        }

        try {
            setDeletingProjectId(projectId);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/${projectId}/`, {
                method: 'DELETE',
            });

            if (!response.ok) {
                throw new Error(`Failed to delete project: ${response.status} ${response.statusText}`);
            }

            // Refresh the projects list after successful deletion
            await fetchProjects();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to delete project');
        } finally {
            setDeletingProjectId(null);
        }
    };

    const calculateProjectStats = (project: Project): ProjectStats => {
        const totalSensors = project.sensor_set.length;
        const calibratedSensors = project.sensor_set.filter(sensor => sensor.isCalibrated).length;
        const validatedSensors = project.sensor_set.filter(sensor => sensor.isValidated).length;
        const hasMmsUrl = Boolean(project.mmsURL && project.mmsURL.trim() !== '');

        return {
            totalSensors,
            calibratedSensors,
            validatedSensors,
            hasMmsUrl,
        };
    };

    const formatDate = (dateString: string) => {
        return new Date(dateString).toLocaleString();
    };

    const getCalibrationTypeColor = (type: string) => {
        const colors: Record<string, 'default' | 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'> = {
            geo: 'primary',
            cartesian: 'secondary',
            floorplan: 'info',
            mtmc: 'warning',
            image: 'success',
        };
        return colors[type] || 'default';
    };

    if (loading) {
        return (
            <Box sx={{ p: 3 }}>
                <Paper elevation={2} sx={{ p: 3 }}>
                    <Typography variant='h5' gutterBottom sx={{ mb: 3, fontWeight: 'bold' }}>
                        Select a Project
                    </Typography>

                    <Grid container spacing={3}>
                        {/* Create 6 skeleton cards */}
                        {Array.from({ length: 6 }).map((_, index) => (
                            <Grid key={index} size={{ xs: 12, md: 6, lg: 4 }}>
                                <Card sx={{ height: '100%', borderRadius: 3 }}>
                                    <CardContent sx={{ p: 3 }}>
                                        {/* Project Header Skeleton */}
                                        <Box sx={{ mb: 3 }}>
                                            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
                                                <Skeleton variant='text' width='70%' height={32} />
                                            </Box>
                                            <Skeleton variant='rectangular' width={80} height={24} sx={{ borderRadius: 12 }} />
                                        </Box>

                                        {/* Stats Grid Skeleton */}
                                        <Grid container spacing={2} sx={{ mb: 3 }}>
                                            <Grid size={4}>
                                                <Box sx={{ textAlign: 'center', p: 1, borderRadius: 2, bgcolor: 'rgba(0,0,0,0.08)' }}>
                                                    <Skeleton variant='circular' width={24} height={24} sx={{ mx: 'auto', mb: 1 }} />
                                                    <Skeleton variant='text' width='60%' height={24} sx={{ mx: 'auto' }} />
                                                    <Skeleton variant='text' width='80%' height={16} sx={{ mx: 'auto' }} />
                                                </Box>
                                            </Grid>
                                            <Grid size={4}>
                                                <Box
                                                    sx={{ textAlign: 'center', p: 1, borderRadius: 2, bgcolor: 'rgba(76, 175, 80, 0.15)' }}
                                                >
                                                    <Skeleton variant='circular' width={24} height={24} sx={{ mx: 'auto', mb: 1 }} />
                                                    <Skeleton variant='text' width='60%' height={24} sx={{ mx: 'auto' }} />
                                                    <Skeleton variant='text' width='80%' height={16} sx={{ mx: 'auto' }} />
                                                </Box>
                                            </Grid>
                                            <Grid size={4}>
                                                <Box
                                                    sx={{ textAlign: 'center', p: 1, borderRadius: 2, bgcolor: 'rgba(33, 150, 243, 0.15)' }}
                                                >
                                                    <Skeleton variant='circular' width={24} height={24} sx={{ mx: 'auto', mb: 1 }} />
                                                    <Skeleton variant='text' width='60%' height={24} sx={{ mx: 'auto' }} />
                                                    <Skeleton variant='text' width='80%' height={16} sx={{ mx: 'auto' }} />
                                                </Box>
                                            </Grid>
                                        </Grid>

                                        {/* Timestamps Skeleton */}
                                        <Box sx={{ pt: 2, borderTop: '1px solid', borderColor: 'divider' }}>
                                            <Grid container spacing={2}>
                                                <Grid size={6}>
                                                    <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                                        <Skeleton variant='circular' width={16} height={16} sx={{ mr: 1 }} />
                                                        <Box sx={{ flex: 1 }}>
                                                            <Skeleton variant='text' width='60%' height={14} />
                                                            <Skeleton variant='text' width='80%' height={14} />
                                                        </Box>
                                                    </Box>
                                                </Grid>
                                                <Grid size={6}>
                                                    <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                                        <Skeleton variant='circular' width={16} height={16} sx={{ mr: 1 }} />
                                                        <Box sx={{ flex: 1 }}>
                                                            <Skeleton variant='text' width='60%' height={14} />
                                                            <Skeleton variant='text' width='80%' height={14} />
                                                        </Box>
                                                    </Box>
                                                </Grid>
                                            </Grid>
                                        </Box>
                                    </CardContent>
                                </Card>
                            </Grid>
                        ))}
                    </Grid>
                </Paper>
            </Box>
        );
    }

    if (error) {
        return (
            <Paper elevation={2} sx={{ p: 3 }}>
                <Alert severity='error' sx={{ mb: 2 }}>
                    {error}
                </Alert>
                <Button variant='contained' onClick={fetchProjects}>
                    Retry
                </Button>
            </Paper>
        );
    }

    if (projects.length === 0) {
        return (
            <Box sx={{ p: 3 }}>
                {/* Project Creation Form - show when no projects exist */}
                <ProjectCreateForm onProjectCreated={fetchProjects} />

                <Paper elevation={2} sx={{ p: 3, textAlign: 'center' }}>
                    <Typography variant='h6' color='text.secondary'>
                        No projects found
                    </Typography>
                    <Typography variant='body2' color='text.secondary' sx={{ mt: 1 }}>
                        Create a new project to get started with calibration.
                    </Typography>
                </Paper>
            </Box>
        );
    }

    return (
        <Box sx={{ p: 3 }}>
            {/* Project Creation Form */}
            <ProjectCreateForm onProjectCreated={fetchProjects} />

            {/* Projects Grid */}
            <Paper elevation={2} sx={{ p: 3 }}>
                <Typography variant='h5' gutterBottom sx={{ mb: 3, fontWeight: 'bold' }}>
                    Select a Project
                </Typography>

                <Grid container spacing={3}>
                    {projects.map(project => {
                        const stats = calculateProjectStats(project);
                        const isSelected = selectedProjectId === project.id;
                        const isSelectable = isProjectSelectable(project);

                        return (
                            <Grid key={project.id} size={{ xs: 12, md: 6, lg: 4 }}>
                                <Card
                                    sx={{
                                        height: '100%',
                                        cursor: isSelectable ? 'pointer' : 'not-allowed',
                                        border: isSelected ? '2px solid' : '1px solid',
                                        borderColor: isSelected ? 'primary.main' : 'divider',
                                        opacity: isSelectable ? 1 : 0.6,
                                        '&:hover': isSelectable
                                            ? {
                                                  elevation: 4,
                                                  borderColor: 'primary.main',
                                              }
                                            : {},
                                        transition: 'all 0.2s ease-in-out',
                                        position: 'relative',
                                    }}
                                    onClick={() => {
                                        if (isSelectable) {
                                            onProjectSelect?.(project);
                                        }
                                    }}
                                >
                                    <CardContent sx={{ p: 3 }}>
                                        {/* Project Header */}
                                        <Box sx={{ mb: 3 }}>
                                            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
                                                <Typography variant='h6' component='div' sx={{ fontWeight: 'bold', flex: 1 }}>
                                                    {project.name}
                                                </Typography>
                                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                                    {isSelected && <CheckCircle color='primary' />}
                                                    <Tooltip title='Delete Project'>
                                                        <IconButton
                                                            size='small'
                                                            color='error'
                                                            disabled={deletingProjectId === project.id}
                                                            onClick={e => {
                                                                e.stopPropagation();
                                                                deleteProject(project.id, project.name);
                                                            }}
                                                            sx={{
                                                                '&:hover': {
                                                                    backgroundColor: 'error.main',
                                                                    color: 'white',
                                                                },
                                                            }}
                                                        >
                                                            <Delete fontSize='small' />
                                                        </IconButton>
                                                    </Tooltip>
                                                </Box>
                                            </Box>
                                            <Chip
                                                label={project.calibrationType.toUpperCase()}
                                                color={getCalibrationTypeColor(project.calibrationType)}
                                                size='small'
                                                sx={{ fontWeight: 'bold' }}
                                            />
                                        </Box>

                                        {/* Stats Grid */}
                                        <Grid container spacing={2} sx={{ mb: 3 }}>
                                            <Grid size={4}>
                                                <Box sx={{ textAlign: 'center', p: 1, borderRadius: 2, bgcolor: 'rgba(0,0,0,0.08)' }}>
                                                    <VideoCameraFront sx={{ color: 'primary.main', mb: 1 }} />
                                                    <Typography variant='h6' fontWeight='bold'>
                                                        {stats.totalSensors}
                                                    </Typography>
                                                    <Typography variant='caption' color='text.secondary'>
                                                        Sensors
                                                    </Typography>
                                                </Box>
                                            </Grid>
                                            <Grid size={4}>
                                                <Box
                                                    sx={{ textAlign: 'center', p: 1, borderRadius: 2, bgcolor: 'rgba(76, 175, 80, 0.15)' }}
                                                >
                                                    <SettingsInputComponent sx={{ color: 'success.main', mb: 1 }} />
                                                    <Typography variant='h6' fontWeight='bold'>
                                                        {stats.calibratedSensors}
                                                    </Typography>
                                                    <Typography variant='caption' color='text.secondary'>
                                                        Calibrated
                                                    </Typography>
                                                </Box>
                                            </Grid>
                                            <Grid size={4}>
                                                <Box
                                                    sx={{ textAlign: 'center', p: 1, borderRadius: 2, bgcolor: 'rgba(33, 150, 243, 0.15)' }}
                                                >
                                                    <TaskAlt sx={{ color: 'info.main', mb: 1 }} />
                                                    <Typography variant='h6' fontWeight='bold'>
                                                        {stats.validatedSensors}
                                                    </Typography>
                                                    <Typography variant='caption' color='text.secondary'>
                                                        Validated
                                                    </Typography>
                                                </Box>
                                            </Grid>
                                        </Grid>

                                        {/* Timestamps */}
                                        <Box sx={{ pt: 2, borderTop: '1px solid', borderColor: 'divider' }}>
                                            <Grid container spacing={2}>
                                                <Grid size={6}>
                                                    <Tooltip title={formatDate(project.created)}>
                                                        <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                                            <CalendarToday fontSize='small' sx={{ mr: 1, color: 'text.secondary' }} />
                                                            <Box>
                                                                <Typography variant='caption' color='text.secondary' display='block'>
                                                                    Created
                                                                </Typography>
                                                                <Typography variant='caption' fontWeight='medium'>
                                                                    {new Date(project.created).toLocaleDateString()}
                                                                </Typography>
                                                            </Box>
                                                        </Box>
                                                    </Tooltip>
                                                </Grid>
                                                <Grid size={6}>
                                                    <Tooltip title={formatDate(project.modified)}>
                                                        <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                                            <Update fontSize='small' sx={{ mr: 1, color: 'text.secondary' }} />
                                                            <Box>
                                                                <Typography variant='caption' color='text.secondary' display='block'>
                                                                    Modified
                                                                </Typography>
                                                                <Typography variant='caption' fontWeight='medium'>
                                                                    {new Date(project.modified).toLocaleDateString()}
                                                                </Typography>
                                                            </Box>
                                                        </Box>
                                                    </Tooltip>
                                                </Grid>
                                            </Grid>
                                        </Box>
                                    </CardContent>
                                </Card>
                            </Grid>
                        );
                    })}
                </Grid>
            </Paper>
        </Box>
    );
};

export default ProjectSelection;
