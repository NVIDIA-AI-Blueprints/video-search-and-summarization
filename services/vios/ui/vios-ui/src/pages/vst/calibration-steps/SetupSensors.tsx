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
    Box,
    Alert,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Button,
    Chip,
    Avatar,
    Skeleton,
    CircularProgress,
    Card,
    CardContent,
    LinearProgress,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { CloudUpload, Edit, CheckCircle, Error } from '@mui/icons-material';
import { Project, Sensor } from './types';
import config from '../../../config';
import { useImageUpload } from './hooks/useImageUpload';
import { ImageUploadDialog } from './ImageUploadModule';
import SensorEditDialog from './SensorEditDialog';

interface SensorCalibrationProps {
    projectId: number;
    onProjectUpdated?: (project: Project) => void;
}

const SetupSensors: React.FC<SensorCalibrationProps> = ({ projectId, onProjectUpdated }) => {
    const [project, setProject] = useState<Project | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [uploadDialog, setUploadDialog] = useState<{ open: boolean; sensor: Sensor | null }>({ open: false, sensor: null });
    const [editDialog, setEditDialog] = useState<{ open: boolean; sensor: Sensor | null }>({ open: false, sensor: null });

    const theme = useTheme();

    const { uploadProgress, uploadImage, isUploading } = useImageUpload({
        onSuccess: () => {
            fetchProjectData();
            setUploadDialog({ open: false, sensor: null });
        },
        onError: (errorMessage: string) => {
            setError(errorMessage);
        },
    });

    useEffect(() => {
        fetchProjectData();
    }, [projectId]);

    const fetchProjectData = async () => {
        try {
            setLoading(true);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/${projectId}/`);

            if (!response.ok) {
                const errorMsg = `Failed to fetch project data: ${response.status} ${response.statusText}`;
                throw new globalThis.Error(errorMsg);
            }

            const projectData: Project = await response.json();
            setProject(projectData);
            onProjectUpdated?.(projectData);
        } catch (err) {
            setError('Failed to fetch project data');
        } finally {
            setLoading(false);
        }
    };

    const handleUploadClick = (sensor: Sensor) => {
        setUploadDialog({ open: true, sensor });
    };

    const handleFileSelect = async (file: File, sensor: Sensor) => {
        await uploadImage(sensor.id, file);
    };

    const handleEditSensor = (sensor: Sensor) => {
        setEditDialog({ open: true, sensor });
    };

    const getImageUrl = (sensor: Sensor): string => {
        if (!sensor.imageUrl) {
            return '';
        }
        // Remove leading slash if present to avoid double slashes
        const cleanImageUrl = sensor.imageUrl.startsWith('/') ? sensor.imageUrl.substring(1) : sensor.imageUrl;
        return `${config.analyticsUIServerEndpoint}/${cleanImageUrl}`;
    };

    const getCalibrationStatus = (sensor: Sensor) => {
        return {
            calibrated: {
                label: sensor.isCalibrated ? 'Calibrated' : 'Not Calibrated',
                color: (sensor.isCalibrated ? 'success' : 'error') as 'success' | 'error',
                icon: sensor.isCalibrated ? <CheckCircle /> : <Error />,
            },
            validated: {
                label: sensor.isValidated ? 'Validated' : 'Not Validated',
                color: (sensor.isValidated ? 'success' : 'error') as 'success' | 'error',
                icon: sensor.isValidated ? <CheckCircle /> : <Error />,
            },
        };
    };

    if (loading) {
        return (
            <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
                <Typography variant='h5' gutterBottom sx={{ mb: theme.spacing(3), fontWeight: theme.typography.fontWeightBold }}>
                    Setup Sensors
                </Typography>

                <TableContainer>
                    <Table>
                        <TableHead>
                            <TableRow>
                                <TableCell sx={{ fontWeight: theme.typography.fontWeightBold }}>Sensor</TableCell>
                                <TableCell sx={{ fontWeight: theme.typography.fontWeightBold }}>Upload</TableCell>
                                <TableCell sx={{ fontWeight: theme.typography.fontWeightBold }}>Status</TableCell>
                                <TableCell sx={{ fontWeight: theme.typography.fontWeightBold }}>Action</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {Array.from({ length: 5 }).map((_, index) => (
                                <TableRow key={index}>
                                    <TableCell>
                                        <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                            <Skeleton
                                                variant='rectangular'
                                                width={80}
                                                height={60}
                                                sx={{ mr: theme.spacing(2), borderRadius: theme.shape.borderRadius }}
                                            />
                                            <Skeleton variant='text' width={150} height={24} />
                                        </Box>
                                    </TableCell>
                                    <TableCell>
                                        <Skeleton
                                            variant='rectangular'
                                            width={100}
                                            height={36}
                                            sx={{ borderRadius: theme.shape.borderRadius }}
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: theme.spacing(1) }}>
                                            <Skeleton
                                                variant='rectangular'
                                                width={120}
                                                height={24}
                                                sx={{ borderRadius: theme.spacing(1.5) }}
                                            />
                                            <Skeleton
                                                variant='rectangular'
                                                width={120}
                                                height={24}
                                                sx={{ borderRadius: theme.spacing(1.5) }}
                                            />
                                        </Box>
                                    </TableCell>
                                    <TableCell>
                                        <Skeleton
                                            variant='rectangular'
                                            width={120}
                                            height={36}
                                            sx={{ borderRadius: theme.shape.borderRadius }}
                                        />
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </TableContainer>
            </Paper>
        );
    }

    if (error) {
        return (
            <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
                <Alert variant='filled' severity='error'>
                    {error}
                </Alert>
            </Paper>
        );
    }

    if (!project) {
        return (
            <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
                <Alert severity='info'>No project data available</Alert>
            </Paper>
        );
    }

    return (
        <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
            <Typography variant='h5' gutterBottom sx={{ mb: theme.spacing(3), fontWeight: theme.typography.fontWeightBold }}>
                Setup Sensors
            </Typography>

            <Typography variant='body1' color='text.secondary' sx={{ mb: theme.spacing(3) }}>
                Review and configure sensors for project "{project.name}"
            </Typography>

            {project.sensor_set.length === 0 ? (
                <Alert severity='info' sx={{ mb: 3 }}>
                    No sensors found for this project. Please add sensors to continue with calibration.
                </Alert>
            ) : (
                <Box>
                    {/* Project Overview Widgets */}
                    <Box sx={{ mb: 3 }}>
                        <Typography variant='h6' gutterBottom sx={{ mb: 2 }}>
                            Project Overview
                        </Typography>
                        <Box
                            sx={{
                                display: 'grid',
                                gridTemplateColumns: { xs: 'repeat(2, 1fr)', sm: 'repeat(3, 1fr)', md: 'repeat(5, 1fr)' },
                                gap: 2,
                            }}
                        >
                            {/* Total Sensors Widget */}
                            <Card
                                variant='outlined'
                                sx={{
                                    textAlign: 'center',
                                    p: theme.spacing(2),
                                    backgroundColor: theme.palette.primary.light + '10',
                                    borderColor: theme.palette.primary.light,
                                    '&:hover': {
                                        backgroundColor: theme.palette.primary.light + '20',
                                        borderColor: theme.palette.primary.main,
                                    },
                                }}
                            >
                                <Typography variant='h3' color='primary.main' sx={{ fontWeight: theme.typography.fontWeightBold, mb: 0.5 }}>
                                    {project.sensor_set.length}
                                </Typography>
                                <Typography variant='body2' color='text.secondary' sx={{ fontWeight: theme.typography.fontWeightMedium }}>
                                    Total Sensors
                                </Typography>
                            </Card>

                            {/* Calibrated Widget */}
                            <Card
                                variant='outlined'
                                sx={{
                                    textAlign: 'center',
                                    p: theme.spacing(2),
                                    backgroundColor: theme.palette.success.light + '10',
                                    borderColor: theme.palette.success.light,
                                    '&:hover': {
                                        backgroundColor: theme.palette.success.light + '20',
                                        borderColor: theme.palette.success.main,
                                    },
                                }}
                            >
                                <Typography variant='h3' color='success.main' sx={{ fontWeight: theme.typography.fontWeightBold, mb: 0.5 }}>
                                    {project.sensor_set.filter(s => s.isCalibrated).length}
                                </Typography>
                                <Typography variant='body2' color='text.secondary' sx={{ fontWeight: theme.typography.fontWeightMedium }}>
                                    Calibrated
                                </Typography>
                            </Card>

                            {/* Validated Widget */}
                            <Card
                                variant='outlined'
                                sx={{
                                    textAlign: 'center',
                                    p: theme.spacing(2),
                                    backgroundColor: theme.palette.info.light + '10',
                                    borderColor: theme.palette.info.light,
                                    '&:hover': {
                                        backgroundColor: theme.palette.info.light + '20',
                                        borderColor: theme.palette.info.main,
                                    },
                                }}
                            >
                                <Typography variant='h3' color='info.main' sx={{ fontWeight: theme.typography.fontWeightBold, mb: 0.5 }}>
                                    {project.sensor_set.filter(s => s.isValidated).length}
                                </Typography>
                                <Typography variant='body2' color='text.secondary' sx={{ fontWeight: theme.typography.fontWeightMedium }}>
                                    Validated
                                </Typography>
                            </Card>

                            {/* Created Date Widget */}
                            <Card
                                variant='outlined'
                                sx={{
                                    textAlign: 'center',
                                    p: theme.spacing(2),
                                    backgroundColor: theme.palette.secondary.light + '10',
                                    borderColor: theme.palette.secondary.light,
                                    '&:hover': {
                                        backgroundColor: theme.palette.secondary.light + '20',
                                        borderColor: theme.palette.secondary.main,
                                    },
                                }}
                            >
                                <Typography
                                    variant='h3'
                                    color='secondary.main'
                                    sx={{ fontWeight: theme.typography.fontWeightBold, mb: 0.5, fontSize: '1.5rem' }}
                                >
                                    {project.created ? new Date(project.created).toLocaleDateString() : 'N/A'}
                                </Typography>
                                <Typography variant='body2' color='text.secondary' sx={{ fontWeight: theme.typography.fontWeightMedium }}>
                                    Created
                                </Typography>
                            </Card>

                            {/* Modified Date Widget */}
                            <Card
                                variant='outlined'
                                sx={{
                                    textAlign: 'center',
                                    p: theme.spacing(2),
                                    backgroundColor: theme.palette.warning.light + '10',
                                    borderColor: theme.palette.warning.light,
                                    '&:hover': {
                                        backgroundColor: theme.palette.warning.light + '20',
                                        borderColor: theme.palette.warning.main,
                                    },
                                }}
                            >
                                <Typography
                                    variant='h3'
                                    color='warning.main'
                                    sx={{ fontWeight: theme.typography.fontWeightBold, mb: 0.5, fontSize: '1.5rem' }}
                                >
                                    {project.modified ? new Date(project.modified).toLocaleDateString() : 'N/A'}
                                </Typography>
                                <Typography variant='body2' color='text.secondary' sx={{ fontWeight: theme.typography.fontWeightMedium }}>
                                    Modified
                                </Typography>
                            </Card>
                        </Box>
                    </Box>

                    {/* Sensors Table Card */}
                    <Card variant='outlined'>
                        <CardContent sx={{ p: 0 }}>
                            <Box sx={{ p: 3, pb: 0 }}>
                                <Typography variant='h6' gutterBottom>
                                    Sensor Configuration
                                </Typography>
                            </Box>
                            <TableContainer>
                                <Table>
                                    <TableHead>
                                        <TableRow>
                                            <TableCell sx={{ fontWeight: theme.typography.fontWeightBold, fontSize: '1rem', pl: 3 }}>
                                                Sensor
                                            </TableCell>
                                            <TableCell sx={{ fontWeight: theme.typography.fontWeightBold, fontSize: '1rem' }}>
                                                Image Upload
                                            </TableCell>
                                            <TableCell sx={{ fontWeight: theme.typography.fontWeightBold, fontSize: '1rem' }}>
                                                Status
                                            </TableCell>
                                            <TableCell sx={{ fontWeight: theme.typography.fontWeightBold, fontSize: '1rem', pr: 3 }}>
                                                Actions
                                            </TableCell>
                                        </TableRow>
                                    </TableHead>
                                    <TableBody>
                                        {project.sensor_set.map(sensor => {
                                            const status = getCalibrationStatus(sensor);
                                            const imageUrl = getImageUrl(sensor);
                                            const sensorUploading = isUploading(sensor.id);
                                            const progressValue =
                                                uploadProgress && typeof uploadProgress === 'object' && sensor.id in uploadProgress
                                                    ? Number((uploadProgress as Record<string, number>)[sensor.id])
                                                    : 0;

                                            return (
                                                <TableRow key={sensor.id} hover>
                                                    <TableCell sx={{ pl: 3 }}>
                                                        <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                                            <Avatar
                                                                src={imageUrl}
                                                                alt={sensor.sensorName || 'Sensor Image'}
                                                                variant='rounded'
                                                                sx={{
                                                                    width: 80,
                                                                    height: 60,
                                                                    mr: theme.spacing(2),
                                                                    bgcolor: 'grey.200',
                                                                    border: imageUrl ? 'none' : `2px dashed ${theme.palette.grey[400]}`,
                                                                }}
                                                            >
                                                                {!imageUrl && (sensor.sensorName?.charAt(0) || '?')}
                                                            </Avatar>
                                                            <Box>
                                                                <Typography
                                                                    variant='subtitle1'
                                                                    sx={{ fontWeight: theme.typography.fontWeightBold }}
                                                                >
                                                                    {sensor.sensorId || sensor.id}
                                                                </Typography>
                                                                {sensor.sensorName && (
                                                                    <Typography variant='body2' color='text.secondary'>
                                                                        {sensor.sensorName}
                                                                    </Typography>
                                                                )}
                                                                <Typography variant='caption' color='text.secondary'>
                                                                    {imageUrl ? 'Image uploaded' : 'No image uploaded'}
                                                                </Typography>
                                                            </Box>
                                                        </Box>
                                                    </TableCell>
                                                    <TableCell>
                                                        <Button
                                                            variant={imageUrl ? 'outlined' : 'contained'}
                                                            size='small'
                                                            startIcon={sensorUploading ? <CircularProgress size={16} /> : <CloudUpload />}
                                                            onClick={() => handleUploadClick(sensor)}
                                                            disabled={sensorUploading}
                                                            sx={{ minWidth: 140 }}
                                                        >
                                                            {sensorUploading ? 'Uploading...' : imageUrl ? 'Replace Image' : 'Upload Image'}
                                                        </Button>
                                                        {sensorUploading && progressValue > 0 && (
                                                            <Box sx={{ mt: 1 }}>
                                                                <LinearProgress
                                                                    variant='determinate'
                                                                    value={progressValue}
                                                                    sx={{ height: 4 }}
                                                                />
                                                                <Typography variant='caption' color='text.secondary'>
                                                                    {Math.round(progressValue)}%
                                                                </Typography>
                                                            </Box>
                                                        )}
                                                    </TableCell>
                                                    <TableCell>
                                                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: theme.spacing(1) }}>
                                                            <Chip
                                                                icon={status.calibrated.icon}
                                                                label={status.calibrated.label}
                                                                color={status.calibrated.color}
                                                                variant='outlined'
                                                                size='small'
                                                                sx={{
                                                                    fontWeight: theme.typography.fontWeightBold,
                                                                    justifyContent: 'flex-start',
                                                                }}
                                                            />
                                                            <Chip
                                                                icon={status.validated.icon}
                                                                label={status.validated.label}
                                                                color={status.validated.color}
                                                                variant='outlined'
                                                                size='small'
                                                                sx={{
                                                                    fontWeight: theme.typography.fontWeightBold,
                                                                    justifyContent: 'flex-start',
                                                                }}
                                                            />
                                                        </Box>
                                                    </TableCell>
                                                    <TableCell sx={{ pr: 3 }}>
                                                        <Button
                                                            variant='outlined'
                                                            size='small'
                                                            startIcon={<Edit />}
                                                            onClick={() => handleEditSensor(sensor)}
                                                            sx={{ minWidth: 120 }}
                                                        >
                                                            Edit Sensor
                                                        </Button>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                            </TableContainer>
                        </CardContent>
                    </Card>
                </Box>
            )}

            {/* Upload Dialog */}
            <ImageUploadDialog
                open={uploadDialog.open}
                sensor={uploadDialog.sensor}
                uploadProgress={uploadProgress}
                onClose={() => setUploadDialog({ open: false, sensor: null })}
                onFileSelect={handleFileSelect}
            />

            {/* Edit Sensor Dialog */}
            <SensorEditDialog
                open={editDialog.open}
                sensor={editDialog.sensor}
                onClose={() => setEditDialog({ open: false, sensor: null })}
                onSuccess={() => {
                    fetchProjectData();
                    setEditDialog({ open: false, sensor: null });
                }}
            />
        </Paper>
    );
};

export default SetupSensors;
