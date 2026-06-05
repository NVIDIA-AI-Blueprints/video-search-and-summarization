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
import { Paper, Typography, Box, TextField, Button, Alert, Skeleton, Grid2 as Grid, Chip } from '@mui/material';
import { Link, CloudDownload, Refresh } from '@mui/icons-material';
import { Project } from './types';
import config from '../../../config';

interface MmsURLConfiguration {
    projectId: number;
    onProjectUpdated?: (project: Project) => void;
}

const MmsURLConfiguration: React.FC<MmsURLConfiguration> = ({ projectId, onProjectUpdated }) => {
    const [project, setProject] = useState<Project | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [importing, setImporting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [successMessage, setSuccessMessage] = useState<string | null>(null);
    const [mmsURL, setMmsURL] = useState<string>('');

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
            setMmsURL(data.mmsURL || '');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch project');
        } finally {
            setLoading(false);
        }
    };

    const handleSaveMmsUrl = async () => {
        if (!project) return;

        try {
            setSaving(true);
            setError(null);
            setSuccessMessage(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/${projectId}/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ mmsURL: mmsURL }),
            });

            if (!response.ok) {
                throw new Error(`Failed to update VST URL: ${response.status} ${response.statusText}`);
            }

            await fetchProject();
            setSuccessMessage('VST URL updated successfully!');
            setTimeout(() => setSuccessMessage(null), 3000);

            if (onProjectUpdated && project) {
                onProjectUpdated({ ...project, mmsURL: mmsURL });
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to update VST URL');
        } finally {
            setSaving(false);
        }
    };

    const handleImportSensors = async () => {
        if (!project) return;

        try {
            setImporting(true);
            setError(null);
            setSuccessMessage(null);

            // First update VST URL
            const updateResponse = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/${projectId}/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ mmsURL: mmsURL }),
            });

            if (!updateResponse.ok) {
                throw new Error(`Failed to update VST URL: ${updateResponse.status} ${updateResponse.statusText}`);
            }

            // Then import sensors
            const importResponse = await fetch(`${config.analyticsUIServerEndpoint}/api/importSensors/${projectId}/`);

            if (!importResponse.ok) {
                throw new Error(`Failed to import sensors: ${importResponse.status} ${importResponse.statusText}`);
            }

            const importResult = await importResponse.text();

            // Fetch updated project data
            await fetchProject();
            setSuccessMessage(`VST URL updated and sensors imported: ${importResult}`);
            setTimeout(() => setSuccessMessage(null), 5000);

            if (onProjectUpdated && project) {
                onProjectUpdated({ ...project, mmsURL: mmsURL });
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to import sensors');
        } finally {
            setImporting(false);
        }
    };

    if (loading) {
        return (
            <Paper elevation={2} sx={{ p: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 3 }}>
                    <Link sx={{ mr: 2, color: 'primary.main' }} />
                    <Typography variant='h5' fontWeight='bold'>
                        VST Configuration
                    </Typography>
                </Box>

                <Grid container spacing={3}>
                    <Grid size={{ xs: 12 }}>
                        <Skeleton variant='text' width='40%' height={24} sx={{ mb: 1 }} />
                        <Skeleton variant='rectangular' width='100%' height={56} sx={{ mb: 2 }} />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Box sx={{ display: 'flex', gap: 2 }}>
                            <Skeleton variant='rectangular' width={120} height={40} />
                            <Skeleton variant='rectangular' width={200} height={40} />
                        </Box>
                    </Grid>
                </Grid>
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
                    <Link sx={{ mr: 2, color: 'primary.main' }} />
                    <Box>
                        <Typography variant='h5' fontWeight='bold'>
                            VST Configuration
                        </Typography>
                        <Typography variant='body2' color='text.secondary'>
                            Video Storage Toolkit URL for sensor integration
                        </Typography>
                    </Box>
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Chip label={project.calibrationType.toUpperCase()} color='primary' size='small' sx={{ fontWeight: 'bold' }} />
                    <Typography variant='caption' color='text.secondary'>
                        {project.sensor_set.length} sensors
                    </Typography>
                </Box>
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

            {/* VST URL Form */}
            <Grid container spacing={3}>
                <Grid size={{ xs: 12 }}>
                    <TextField
                        fullWidth
                        label='VST URL'
                        value={mmsURL}
                        onChange={e => setMmsURL(e.target.value)}
                        helperText='Enter the VST URL to connect and import sensors'
                        placeholder='http://<ip>:30888/vst'
                        variant='outlined'
                    />
                </Grid>
                <Grid size={{ xs: 12 }}>
                    <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                        <Button
                            variant='contained'
                            color='primary'
                            startIcon={<Link />}
                            onClick={handleSaveMmsUrl}
                            disabled={saving || importing || !mmsURL.trim()}
                        >
                            {saving ? 'Saving...' : 'Set VST URL'}
                        </Button>
                        <Button
                            variant='contained'
                            color='secondary'
                            startIcon={<CloudDownload />}
                            onClick={handleImportSensors}
                            disabled={saving || importing || !mmsURL.trim()}
                        >
                            {importing ? 'Importing...' : 'Set VST URL & Import Sensors'}
                        </Button>
                        <Button variant='outlined' startIcon={<Refresh />} onClick={fetchProject} disabled={loading || saving || importing}>
                            Refresh
                        </Button>
                    </Box>
                </Grid>
            </Grid>
        </Paper>
    );
};

export default MmsURLConfiguration;
