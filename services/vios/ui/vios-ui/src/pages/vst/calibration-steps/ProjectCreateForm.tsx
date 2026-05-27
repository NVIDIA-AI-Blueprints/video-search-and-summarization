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
import {
    Paper,
    Typography,
    TextField,
    RadioGroup,
    FormControlLabel,
    Radio,
    Button,
    Box,
    Alert,
    CircularProgress,
    Collapse,
    IconButton,
    Grid2 as Grid,
    Card,
    CardContent,
    useTheme,
} from '@mui/material';
import { Add, ExpandLess, ExpandMore, Rocket } from '@mui/icons-material';
import config from '../../../config';

type CalibrationType = 'geo' | 'cartesian' | 'mtmc' | 'image';

interface ProjectCreateFormProps {
    onProjectCreated?: () => void;
}

const ProjectCreateForm: React.FC<ProjectCreateFormProps> = ({ onProjectCreated }) => {
    const theme = useTheme();
    const [expanded, setExpanded] = useState(false);
    const [projectName, setProjectName] = useState('');
    const [calibrationType, setCalibrationType] = useState<CalibrationType>('cartesian');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState(false);

    const calibrationTypes: { value: CalibrationType; label: string; description: string; disabled?: boolean }[] = [
        {
            value: 'cartesian',
            label: 'Cartesian Calibration',
            description: 'Converts camera pixels to real-world X,Y coordinates for precise distance measurements.',
        },
        {
            value: 'image',
            label: 'Image Calibration',
            description: 'Maps pixel coordinates between different image coordinate systems without physical unit conversion.',
        },
        {
            value: 'geo',
            label: 'GIS Calibration',
            description: 'Transforms camera pixels to geographic lat/lng coordinates for outdoor location tracking.',
            disabled: true,
        },
        {
            value: 'mtmc',
            label: 'MultiCamera Tracking Calibration',
            description: 'Synchronizes multiple camera coordinate systems for cross-camera object tracking.',
            disabled: true,
        },
    ];

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();

        if (!projectName.trim()) {
            setError('Project name is required');
            return;
        }

        try {
            setLoading(true);
            setError(null);
            setSuccess(false);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: projectName.trim(),
                    calibrationType: calibrationType,
                }),
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `Failed to create project: ${response.status} ${response.statusText}`);
            }

            // Success
            setSuccess(true);
            setProjectName('');
            setCalibrationType('cartesian');

            // Collapse the form after successful creation
            setTimeout(() => {
                setExpanded(false);
                setSuccess(false);
            }, 2000);

            // Notify parent component to refresh the project list
            onProjectCreated?.();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to create project');
        } finally {
            setLoading(false);
        }
    };

    const handleReset = () => {
        setProjectName('');
        setCalibrationType('cartesian');
        setError(null);
        setSuccess(false);
    };

    return (
        <Paper elevation={2} sx={{ mb: 3 }}>
            {/* Header */}
            <Box
                sx={{
                    p: 3,
                    bgcolor: expanded ? theme.palette.primary.main : theme.palette.action.hover,
                    color: expanded ? theme.palette.primary.contrastText : theme.palette.text.primary,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    borderRadius: expanded ? '4px 4px 0 0' : '4px',
                    border: '1px solid',
                    borderColor: expanded ? theme.palette.primary.main : theme.palette.divider,
                    transition: 'all 0.2s ease-in-out',
                    '&:hover': {
                        borderColor: theme.palette.primary.main,
                        bgcolor: expanded ? theme.palette.primary.dark : theme.palette.action.selected,
                    },
                }}
                onClick={() => setExpanded(!expanded)}
            >
                <Box sx={{ display: 'flex', alignItems: 'center' }}>
                    <Rocket
                        sx={{
                            mr: 2,
                            fontSize: 28,
                            color: expanded ? theme.palette.primary.contrastText : theme.palette.primary.main,
                        }}
                    />
                    <Box>
                        <Typography variant='h5' sx={{ fontWeight: 'bold' }}>
                            Create New Project
                        </Typography>
                        <Typography variant='body2' sx={{ opacity: 0.8 }}>
                            {expanded ? 'Click to collapse' : 'Click to expand and create a new calibration project'}
                        </Typography>
                    </Box>
                </Box>
                <IconButton
                    sx={{
                        color: expanded ? theme.palette.primary.contrastText : theme.palette.primary.main,
                    }}
                >
                    {expanded ? <ExpandLess /> : <ExpandMore />}
                </IconButton>
            </Box>

            {/* Form Content */}
            <Collapse in={expanded}>
                <Box
                    sx={{
                        p: 3,
                        bgcolor: theme.palette.background.paper,
                        borderTop: '1px solid',
                        borderColor: theme.palette.divider,
                    }}
                >
                    {success && (
                        <Alert severity='success' sx={{ mb: 3 }}>
                            Project created successfully! Refreshing project list...
                        </Alert>
                    )}

                    {error && (
                        <Alert severity='error' sx={{ mb: 3 }}>
                            {error}
                        </Alert>
                    )}

                    <form onSubmit={handleSubmit}>
                        <Grid container spacing={3}>
                            {/* Project Name */}
                            <Grid size={12}>
                                <Typography variant='h5' gutterBottom sx={{ fontWeight: 'bold', mb: 2 }}>
                                    Project Details
                                </Typography>
                                <TextField
                                    fullWidth
                                    label='Project Name'
                                    placeholder='Enter a unique name for your project'
                                    value={projectName}
                                    onChange={e => setProjectName(e.target.value)}
                                    required
                                    disabled={loading}
                                    sx={{ mb: 3 }}
                                />
                            </Grid>

                            {/* Calibration Type */}
                            <Grid size={12}>
                                <Typography variant='h5' gutterBottom sx={{ fontWeight: 'bold', mb: 3 }}>
                                    Calibration Type
                                </Typography>
                                <RadioGroup value={calibrationType} onChange={e => setCalibrationType(e.target.value as CalibrationType)}>
                                    <Grid container spacing={2}>
                                        {calibrationTypes.map(type => (
                                            <Grid key={type.value} size={{ xs: 12, sm: 6 }}>
                                                <Card
                                                    sx={{
                                                        height: '100%',
                                                        cursor: type.disabled ? 'not-allowed' : 'pointer',
                                                        border: calibrationType === type.value ? '2px solid' : '1px solid',
                                                        borderColor:
                                                            calibrationType === type.value
                                                                ? theme.palette.primary.main
                                                                : theme.palette.divider,
                                                        borderRadius: 3,
                                                        transition: 'all 0.2s ease-in-out',
                                                        opacity: type.disabled ? 0.5 : 1,
                                                        '&:hover': !type.disabled
                                                            ? {
                                                                  elevation: 4,
                                                                  borderColor: theme.palette.primary.main,
                                                              }
                                                            : {},
                                                        background:
                                                            calibrationType === type.value
                                                                ? theme.palette.action.selected
                                                                : theme.palette.background.paper,
                                                    }}
                                                    onClick={() => !loading && !type.disabled && setCalibrationType(type.value)}
                                                >
                                                    <CardContent sx={{ p: 3 }}>
                                                        <FormControlLabel
                                                            value={type.value}
                                                            control={<Radio disabled={loading || type.disabled} />}
                                                            label={
                                                                <Box>
                                                                    <Typography variant='h6' fontWeight='bold' sx={{ mb: 1 }}>
                                                                        {type.label}
                                                                        {type.disabled && (
                                                                            <Typography
                                                                                component='span'
                                                                                variant='caption'
                                                                                sx={{ ml: 1, color: 'error.main', fontWeight: 'normal' }}
                                                                            >
                                                                                (Disabled)
                                                                            </Typography>
                                                                        )}
                                                                    </Typography>
                                                                    <Typography variant='body2' color='text.secondary'>
                                                                        {type.description}
                                                                    </Typography>
                                                                </Box>
                                                            }
                                                            sx={{
                                                                width: '100%',
                                                                m: 0,
                                                                '& .MuiFormControlLabel-label': {
                                                                    width: '100%',
                                                                },
                                                            }}
                                                        />
                                                    </CardContent>
                                                </Card>
                                            </Grid>
                                        ))}
                                    </Grid>
                                </RadioGroup>
                            </Grid>

                            {/* Action Buttons */}
                            <Grid size={12}>
                                <Box
                                    sx={{
                                        display: 'flex',
                                        gap: 2,
                                        justifyContent: 'flex-end',
                                        mt: 3,
                                        pt: 3,
                                        borderTop: '1px solid',
                                        borderColor: theme.palette.divider,
                                    }}
                                >
                                    <Button variant='outlined' onClick={handleReset} disabled={loading} sx={{ minWidth: 120 }}>
                                        Reset
                                    </Button>
                                    <Button
                                        type='submit'
                                        variant='contained'
                                        disabled={loading || !projectName.trim()}
                                        startIcon={loading ? <CircularProgress size={20} /> : <Add />}
                                        sx={{ minWidth: 150 }}
                                    >
                                        {loading ? 'Creating...' : 'Create Project'}
                                    </Button>
                                </Box>
                            </Grid>
                        </Grid>
                    </form>
                </Box>
            </Collapse>
        </Paper>
    );
};

export default ProjectCreateForm;
