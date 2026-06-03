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
    FormControl,
    InputLabel,
    Select,
    MenuItem,
    SelectChangeEvent,
    Button,
    Card,
    CardContent,
    FormControlLabel,
    Checkbox,
    Chip,
    Divider,
} from '@mui/material';
import Grid2 from '@mui/material/Grid2';
import { useTheme } from '@mui/material/styles';
import { CheckCircle, Refresh } from '@mui/icons-material';
import { Project } from './types';
import config from '../../../config';
import CalibrationCanvas from './CalibrationCanvas';
import { CalibrationFigure, CalibrationLabel } from './Calibration';
import { convertLatLngToXYMatrix, convertProjectedPoint, Point } from './utils/calibrationMath';
import { matrix, multiply } from 'mathjs';

interface ValidationProps {
    projectId: number;
    onProjectUpdated?: (project: Project) => void;
}

interface SensorData {
    id: string;
    sensorId: string;
    imageUrl?: string;
    invertImageUrl?: string;
    homography?: number[][];
    height?: number;
    width?: number;
    isCalibrated?: boolean;
    isValidated?: boolean;
}

const Validation: React.FC<ValidationProps> = ({ projectId, onProjectUpdated }) => {
    const [project, setProject] = useState<Project | null>(null);
    const [selectedSensorId, setSelectedSensorId] = useState<string>('');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [validating, setValidating] = useState(false);

    // Drawing state for original image
    const [figures, setFigures] = useState<CalibrationFigure[]>([]);
    const [unfinishedFigure, setUnfinishedFigure] = useState<CalibrationFigure | null>(null);

    // Projected figures for inverted image
    const [projectedFigures, setProjectedFigures] = useState<CalibrationFigure[]>([]);
    const [projectedUnfinishedFigure, setProjectedUnfinishedFigure] = useState<CalibrationFigure | null>(null);

    // Drawing mode state
    const [selectedDrawingMode, setSelectedDrawingMode] = useState<string | null>(null);
    const [isDrawingMode, setIsDrawingMode] = useState(false);

    // Filter states - default to show non-validated sensors
    const [showNonValidatedOnly, setShowNonValidatedOnly] = useState(true);

    // Display options
    const [showVertexNumbers, setShowVertexNumbers] = useState(true);

    const theme = useTheme();

    const [labels] = useState<CalibrationLabel[]>([
        {
            id: 'validation',
            name: 'Validation',
            color: theme.palette.primary.main,
            type: 'polygon',
            limit: false,
            draw: true,
            class: 'validation',
        },
    ]);

    useEffect(() => {
        fetchProjectData();
    }, [projectId]);

    // Handle filter changes - auto-select appropriate sensor when filters change
    useEffect(() => {
        if (project) {
            const filteredSensors = project.sensor_set.filter(sensor => {
                // Only show calibrated sensors for validation
                if (!sensor.isCalibrated) return false;

                // Apply filter based on validation status
                if (showNonValidatedOnly && sensor.isValidated) {
                    return false;
                }

                return true;
            });

            // If current selection is not in filtered list, select first available or clear
            if (selectedSensorId && !filteredSensors.some(sensor => sensor.id === selectedSensorId)) {
                if (filteredSensors.length > 0) {
                    setSelectedSensorId(filteredSensors[0].id);
                    fetchSensorData(filteredSensors[0].id);
                } else {
                    setSelectedSensorId('');
                }
            } else if (!selectedSensorId && filteredSensors.length > 0) {
                // Auto-select first sensor if none selected
                setSelectedSensorId(filteredSensors[0].id);
                fetchSensorData(filteredSensors[0].id);
            }
        }
    }, [showNonValidatedOnly, project, selectedSensorId]);

    const fetchProjectData = async () => {
        try {
            setLoading(true);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/projects/${projectId}/`);

            if (!response.ok) {
                throw new Error(`Failed to fetch project data: ${response.status} ${response.statusText}`);
            }

            const projectData: Project = await response.json();
            setProject(projectData);
            onProjectUpdated?.(projectData);

            // Auto-select first calibrated sensor if available
            const filteredSensors = projectData.sensor_set.filter(sensor => {
                // Only show calibrated sensors for validation
                if (!sensor.isCalibrated) return false;

                // Apply filter based on validation status
                if (showNonValidatedOnly && sensor.isValidated) {
                    return false;
                }

                return true;
            });

            if (filteredSensors.length > 0) {
                setSelectedSensorId(filteredSensors[0].id);
                await fetchSensorData(filteredSensors[0].id);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch project data');
        } finally {
            setLoading(false);
        }
    };

    const fetchSensorData = async (sensorId: string) => {
        try {
            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${sensorId}/`, {
                headers: { streamId: sensorId },
            });
            if (!response.ok) {
                throw new Error(`Failed to fetch sensor data: ${response.status} ${response.statusText}`);
            }

            const sensorData: SensorData = await response.json();
            console.log('Sensor data for validation:', sensorData);

            // Clear any previous errors when successfully fetching sensor data
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch sensor data');
        }
    };

    const handleSensorChange = async (event: SelectChangeEvent) => {
        const newSensorId = event.target.value;
        setSelectedSensorId(newSensorId);
        setFigures([]);
        setUnfinishedFigure(null);
        setProjectedFigures([]);
        setProjectedUnfinishedFigure(null);
        setError(null); // Clear any previous errors

        if (newSensorId) {
            await fetchSensorData(newSensorId);
        }
    };

    const projectPointsToInvertedImage = (figure: CalibrationFigure, isUnfinished = false): CalibrationFigure | null => {
        const selectedSensor = project?.sensor_set.find(s => s.id === selectedSensorId);
        if (!selectedSensor || !selectedSensor.homography) {
            return null;
        }

        try {
            // Parse the homography matrix from the sensor data
            const homographyData =
                typeof selectedSensor.homography === 'string' ? JSON.parse(selectedSensor.homography) : selectedSensor.homography;
            const homographyMatrix = matrix(homographyData);
            const imageHeight = selectedSensor.height || 720;

            // Apply homography transformation to each point
            const projectedPoints = figure.points.map(point => {
                // Convert point to Point interface format
                const imagePoint: Point = { lat: point.lat, lng: point.lng };

                // Convert to matrix coordinates for homography transformation
                const matrixPoint = convertLatLngToXYMatrix(imagePoint, imageHeight);

                // Apply homography transformation
                const transformedMatrix = multiply(homographyMatrix, matrixPoint);

                // Convert back to lat/lng format
                const projectedPoint = convertProjectedPoint(transformedMatrix);

                return {
                    lat: projectedPoint.lat,
                    lng: projectedPoint.lng,
                };
            });

            return {
                ...figure,
                points: projectedPoints,
                id: isUnfinished ? figure.id : `projected-${figure.id}`,
            };
        } catch (error) {
            console.error('Error applying homography transformation:', error);
            // Fallback to identity transformation if homography fails
            const projectedPoints = figure.points.map(point => ({
                lat: point.lat,
                lng: point.lng,
            }));

            return {
                ...figure,
                points: projectedPoints,
                id: isUnfinished ? figure.id : `projected-${figure.id}`,
            };
        }
    };

    const handleFigureChange = (eventType: string, figureData: CalibrationFigure) => {
        switch (eventType) {
            case 'new': {
                const newFigures = [...figures, { ...figureData, id: `figure-${Date.now()}` }];
                setFigures(newFigures);
                setUnfinishedFigure(null);

                // Clear projected unfinished figure when completing a shape
                setProjectedUnfinishedFigure(null);

                // Stop drawing mode when figure is completed
                setSelectedDrawingMode(null);
                setIsDrawingMode(false);

                // Project to inverted image
                const projectedFigure = projectPointsToInvertedImage({ ...figureData, id: `figure-${Date.now()}` });
                if (projectedFigure) {
                    setProjectedFigures(prev => [...prev, projectedFigure]);
                }
                break;
            }
            case 'replace': {
                setFigures(prev => prev.map(f => (f.id === figureData.id ? figureData : f)));

                // Update projection
                const projectedFigure = projectPointsToInvertedImage(figureData);
                if (projectedFigure) {
                    setProjectedFigures(prev => prev.map(f => (f.id === `projected-${figureData.id}` ? projectedFigure : f)));
                }
                break;
            }
            case 'delete':
                setFigures(prev => prev.filter(f => f.id !== figureData.id));
                setProjectedFigures(prev => prev.filter(f => f.id !== `projected-${figureData.id}`));
                break;
            case 'unfinished': {
                setUnfinishedFigure(figureData);

                // Project unfinished figure
                const projectedUnfinished = projectPointsToInvertedImage(figureData, true);
                if (projectedUnfinished) {
                    setProjectedUnfinishedFigure(projectedUnfinished);
                } else {
                    setProjectedUnfinishedFigure(null);
                }
                break;
            }
            default:
                break;
        }
    };

    const handleAcceptValidation = async () => {
        if (!selectedSensorId) {
            setError('Please select a sensor');
            return;
        }

        if (figures.length === 0) {
            setError('Please draw at least one validation polygon');
            return;
        }

        try {
            setValidating(true);
            setError(null);

            const response = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${selectedSensorId}/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    streamId: selectedSensorId,
                },
                body: JSON.stringify({
                    isValidated: true,
                }),
            });

            if (!response.ok) {
                throw new Error(`Failed to validate sensor: ${response.status} ${response.statusText}`);
            }

            // Refresh project data
            await fetchProjectData();

            // Clear drawings after successful validation
            setFigures([]);
            setUnfinishedFigure(null);
            setProjectedFigures([]);
            setProjectedUnfinishedFigure(null);
            setSelectedDrawingMode(null);
            setIsDrawingMode(false);
        } catch (err) {
            setError(`Failed to validate sensor: ${err instanceof Error ? err.message : 'Unknown error'}`);
        } finally {
            setValidating(false);
        }
    };

    const handleReset = () => {
        setFigures([]);
        setUnfinishedFigure(null);
        setProjectedFigures([]);
        setProjectedUnfinishedFigure(null);
        setSelectedDrawingMode(null);
        setIsDrawingMode(false);
        setError(null);
    };

    const handleStartDrawing = (labelId: string) => {
        const label = labels.find(l => l.id === labelId);
        if (!label) return;

        setSelectedDrawingMode(labelId);
        setIsDrawingMode(true);

        // Start an unfinished figure for this label
        // IMPORTANT: The id must match the labelId for checkIfDrawing to work
        setUnfinishedFigure({
            id: labelId,
            type: label.type,
            points: [],
            class: label.class,
            color: label.color,
        });
    };

    const handleStopDrawing = () => {
        setSelectedDrawingMode(null);
        setIsDrawingMode(false);
        setUnfinishedFigure(null);
        setProjectedUnfinishedFigure(null);
    };

    const selectedSensor = project?.sensor_set.find(s => s.id === selectedSensorId);

    if (loading) {
        return (
            <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
                <Typography variant='h5' gutterBottom sx={{ mb: theme.spacing(3), fontWeight: theme.typography.fontWeightBold }}>
                    Validation
                </Typography>
                <Typography>Loading...</Typography>
            </Paper>
        );
    }

    if (error) {
        return (
            <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
                <Typography variant='h5' gutterBottom sx={{ mb: theme.spacing(3), fontWeight: theme.typography.fontWeightBold }}>
                    Validation
                </Typography>
                <Alert severity='error' sx={{ mb: theme.spacing(2) }}>
                    {error}
                </Alert>
                <Button variant='contained' onClick={fetchProjectData}>
                    Retry
                </Button>
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

    const calibratedSensors = project?.sensor_set.filter(s => s.isCalibrated) || [];
    if (calibratedSensors.length === 0) {
        return (
            <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
                <Typography variant='h5' gutterBottom sx={{ mb: theme.spacing(3), fontWeight: theme.typography.fontWeightBold }}>
                    Validation
                </Typography>
                <Alert severity='warning'>
                    No calibrated sensors found for this project. Please complete calibration before validation.
                </Alert>
            </Paper>
        );
    }

    return (
        <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
            <Typography variant='h5' gutterBottom sx={{ mb: theme.spacing(3), fontWeight: theme.typography.fontWeightBold }}>
                Validation
            </Typography>

            <Typography variant='body1' color='text.secondary' sx={{ mb: theme.spacing(3) }}>
                Validate calibration for project "{project.name}" by drawing polygons and reviewing projections
            </Typography>

            <Box>
                {/* Sensor Selection Section */}
                <Card variant='outlined' sx={{ mb: 3 }}>
                    <CardContent>
                        <Typography variant='h6' gutterBottom sx={{ mb: 2 }}>
                            Sensor Selection
                        </Typography>

                        <Grid2 container spacing={3} alignItems='flex-start'>
                            <Grid2 size={{ xs: 12, md: 6 }}>
                                <FormControl fullWidth>
                                    <InputLabel>Select Calibrated Sensor</InputLabel>
                                    <Select value={selectedSensorId} label='Select Calibrated Sensor' onChange={handleSensorChange}>
                                        {project.sensor_set
                                            .filter(sensor => {
                                                // Only show calibrated sensors for validation
                                                if (!sensor.isCalibrated) return false;

                                                // Apply filter based on validation status
                                                if (showNonValidatedOnly && sensor.isValidated) {
                                                    return false;
                                                }

                                                return true;
                                            })
                                            .map(sensor => (
                                                <MenuItem key={sensor.id} value={sensor.id}>
                                                    <Box
                                                        sx={{
                                                            display: 'flex',
                                                            alignItems: 'center',
                                                            justifyContent: 'space-between',
                                                            width: '100%',
                                                        }}
                                                    >
                                                        <Typography>{sensor.sensorId}</Typography>
                                                        <Box sx={{ display: 'flex', gap: 0.5 }}>
                                                            {sensor.isCalibrated && (
                                                                <Chip label='Calibrated' size='small' color='success' variant='outlined' />
                                                            )}
                                                            {sensor.isValidated && (
                                                                <Chip label='Validated' size='small' color='primary' variant='outlined' />
                                                            )}
                                                        </Box>
                                                    </Box>
                                                </MenuItem>
                                            ))}
                                    </Select>
                                </FormControl>
                            </Grid2>

                            <Grid2 size={{ xs: 12, md: 6 }}>
                                <Box sx={{ pl: { md: 2 }, borderLeft: { md: `1px solid ${theme.palette.divider}` } }}>
                                    <Typography variant='subtitle2' color='text.secondary' sx={{ mb: 1 }}>
                                        Filter Options
                                    </Typography>
                                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
                                        <FormControlLabel
                                            control={
                                                <Checkbox
                                                    checked={showNonValidatedOnly}
                                                    onChange={e => setShowNonValidatedOnly(e.target.checked)}
                                                    size='small'
                                                />
                                            }
                                            label={<Typography variant='body2'>Show non-validated only</Typography>}
                                        />
                                    </Box>
                                </Box>
                            </Grid2>
                        </Grid2>
                    </CardContent>
                </Card>

                {selectedSensor && (
                    <>
                        {/* Warning if inverted image is missing (shouldn't happen for calibrated sensors) */}
                        {!selectedSensor.invertImageUrl && (
                            <Alert severity='warning' sx={{ mb: 3 }}>
                                No inverted image available for this calibrated sensor. There may be an issue with the calibration data.
                            </Alert>
                        )}

                        {/* Action Controls */}
                        <Card variant='outlined' sx={{ mb: 3 }}>
                            <CardContent>
                                <Typography variant='h6' gutterBottom sx={{ mb: 2 }}>
                                    Validation Actions
                                </Typography>
                                <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
                                    <Button
                                        variant='contained'
                                        color='success'
                                        startIcon={<CheckCircle />}
                                        onClick={handleAcceptValidation}
                                        disabled={figures.length === 0 || validating || !selectedSensor.invertImageUrl}
                                    >
                                        {validating ? 'Validating...' : 'Accept Validation'}
                                    </Button>
                                    <Button variant='outlined' startIcon={<Refresh />} onClick={handleReset}>
                                        Reset
                                    </Button>
                                    {selectedSensor.isValidated && (
                                        <Chip label='✓ This sensor is already validated' color='success' variant='outlined' />
                                    )}
                                    {!selectedSensor.invertImageUrl && (
                                        <Typography variant='body2' color='text.secondary'>
                                            Validation requires a calibrated sensor with an inverted image
                                        </Typography>
                                    )}
                                </Box>
                            </CardContent>
                        </Card>

                        {/* Drawing Controls */}
                        <Card variant='outlined' sx={{ mb: 3 }}>
                            <CardContent>
                                <Typography variant='h6' gutterBottom sx={{ mb: 2 }}>
                                    Drawing Controls
                                </Typography>
                                <Box
                                    sx={{
                                        display: 'flex',
                                        gap: 2,
                                        alignItems: 'center',
                                        flexWrap: 'wrap',
                                        mb: 2,
                                    }}
                                >
                                    {labels.map(label => (
                                        <Button
                                            key={label.id}
                                            variant={selectedDrawingMode === label.id ? 'contained' : 'outlined'}
                                            onClick={() => handleStartDrawing(label.id)}
                                            disabled={!label.draw || !selectedSensor.invertImageUrl}
                                            sx={{
                                                backgroundColor: selectedDrawingMode === label.id ? label.color : 'transparent',
                                                borderColor: label.color,
                                                color: selectedDrawingMode === label.id ? 'white' : label.color,
                                                '&:hover': {
                                                    backgroundColor: selectedDrawingMode === label.id ? label.color : `${label.color}20`,
                                                    borderColor: label.color,
                                                    color: selectedDrawingMode === label.id ? 'white' : label.color,
                                                },
                                            }}
                                        >
                                            Start {label.name}
                                        </Button>
                                    ))}
                                    {isDrawingMode && (
                                        <Button variant='outlined' color='secondary' onClick={handleStopDrawing}>
                                            Stop Drawing
                                        </Button>
                                    )}
                                    <Divider orientation='vertical' flexItem />
                                    <FormControlLabel
                                        control={
                                            <Checkbox
                                                checked={showVertexNumbers}
                                                onChange={e => setShowVertexNumbers(e.target.checked)}
                                                size='small'
                                            />
                                        }
                                        label='Show vertex numbers'
                                        sx={{ whiteSpace: 'nowrap' }}
                                    />
                                </Box>
                                {isDrawingMode && selectedDrawingMode && (
                                    <Box sx={{ p: 2, backgroundColor: 'action.hover', borderRadius: 1 }}>
                                        <Typography variant='body2' color='primary' fontWeight='bold'>
                                            Drawing Mode: {labels.find(l => l.id === selectedDrawingMode)?.name}
                                            {unfinishedFigure && ` (${unfinishedFigure.points.length} points)`}
                                        </Typography>
                                        <Typography variant='body2' color='text.secondary' sx={{ mt: 1 }}>
                                            Click on the left image to add points. Double-click to finish the shape.
                                        </Typography>
                                    </Box>
                                )}
                            </CardContent>
                        </Card>

                        {/* Instructions */}
                        <Card variant='outlined' sx={{ mb: 3 }}>
                            <CardContent>
                                <Typography variant='h6' gutterBottom>
                                    Validation Instructions
                                </Typography>
                                <Grid2 container spacing={2}>
                                    <Grid2 size={{ xs: 12, md: 6 }}>
                                        <Typography variant='body2' color='text.secondary'>
                                            • <strong>Left Image:</strong> Draw validation polygons on the original image
                                            <br />• <strong>Right Image:</strong> Review how your polygons are projected onto the inverted
                                            image
                                        </Typography>
                                    </Grid2>
                                    <Grid2 size={{ xs: 12, md: 6 }}>
                                        <Typography variant='body2' color='text.secondary'>
                                            • <strong>Drawing:</strong> Use the same controls as calibration (left click to add points,
                                            double click to finish)
                                            <br />• <strong>Validation:</strong> If the projections look accurate, click "Accept Validation"
                                        </Typography>
                                    </Grid2>
                                </Grid2>
                            </CardContent>
                        </Card>

                        {/* Main Content Area - Side-by-side Images */}
                        <Grid2 container spacing={3}>
                            {/* Original Image with Drawing */}
                            <Grid2 size={{ xs: 12, lg: 6 }}>
                                <Card variant='outlined'>
                                    <CardContent sx={{ p: 2 }}>
                                        <Typography variant='h6' sx={{ mb: 2 }}>
                                            Original Image - Draw Here
                                        </Typography>
                                        <Box sx={{ height: '600px', borderRadius: 1 }}>
                                            <CalibrationCanvas
                                                sensor={selectedSensor}
                                                figures={figures}
                                                unfinishedFigure={unfinishedFigure}
                                                labels={labels}
                                                onChange={handleFigureChange}
                                                showVertexNumbers={showVertexNumbers}
                                            />
                                        </Box>
                                    </CardContent>
                                </Card>
                            </Grid2>

                            {/* Inverted Image with Projections */}
                            <Grid2 size={{ xs: 12, lg: 6 }}>
                                <Card variant='outlined'>
                                    <CardContent sx={{ p: 2 }}>
                                        <Typography variant='h6' sx={{ mb: 2 }}>
                                            Inverted Image - View Projections
                                        </Typography>
                                        <Box sx={{ height: '600px', borderRadius: 1 }}>
                                            {selectedSensor.invertImageUrl ? (
                                                <CalibrationCanvas
                                                    sensor={{
                                                        ...selectedSensor,
                                                        imageUrl: selectedSensor.invertImageUrl,
                                                    }}
                                                    figures={projectedFigures}
                                                    unfinishedFigure={projectedUnfinishedFigure}
                                                    labels={labels}
                                                    onChange={() => {}} // Read-only
                                                    readOnly={true}
                                                    showVertexNumbers={showVertexNumbers}
                                                />
                                            ) : (
                                                <Box
                                                    sx={{
                                                        height: '100%',
                                                        display: 'flex',
                                                        alignItems: 'center',
                                                        justifyContent: 'center',
                                                        backgroundColor: 'grey.100',
                                                        borderRadius: 1,
                                                    }}
                                                >
                                                    <Typography color='text.secondary'>No inverted image available</Typography>
                                                </Box>
                                            )}
                                        </Box>
                                    </CardContent>
                                </Card>
                            </Grid2>
                        </Grid2>
                    </>
                )}
            </Box>
        </Paper>
    );
};

export default Validation;
