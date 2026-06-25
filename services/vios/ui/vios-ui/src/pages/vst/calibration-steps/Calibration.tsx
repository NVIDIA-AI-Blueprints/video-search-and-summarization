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
    Divider,
    Card,
    CardContent,
    CircularProgress,
    FormControlLabel,
    Checkbox,
    Chip,
    Slider,
} from '@mui/material';
import Grid2 from '@mui/material/Grid2';
import { useTheme } from '@mui/material/styles';
import { Refresh, Calculate } from '@mui/icons-material';
import { Project } from './types';
import config from '../../../config';
import CalibrationCanvas from './CalibrationCanvas';
import CoordinateInput, { RealWorldCoordinate } from './CoordinateInput';
import CalibrationResult from './CalibrationResult';
import { pushPolygonUpdate, pushEdgeLengthsUpdate, waitForHomography, validateCalibrationPoints, Point } from './utils/calibrationMath';

interface CalibrationProps {
    projectId: number;
    onProjectUpdated?: (project: Project) => void;
}

export interface CalibrationFigure {
    id: string;
    type: 'polygon' | 'polyline';
    points: Array<{ lat: number; lng: number }>;
    class: string;
    color: string;
    tracingOptions?: {
        enabled: boolean;
        trace?: Array<{ lat: number; lng: number }>;
    };
}

export interface CalibrationLabel {
    id: string;
    name: string;
    color: string;
    type: 'polygon' | 'polyline';
    limit: boolean;
    draw: boolean;
    class: string;
}

const Calibration: React.FC<CalibrationProps> = ({ projectId, onProjectUpdated }) => {
    const [project, setProject] = useState<Project | null>(null);
    const [selectedSensorId, setSelectedSensorId] = useState<string>('');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [figures, setFigures] = useState<CalibrationFigure[]>([]);
    const [unfinishedFigure, setUnfinishedFigure] = useState<CalibrationFigure | null>(null);
    const [labels, setLabels] = useState<CalibrationLabel[]>([]);
    const [realWorldCoordinates, setRealWorldCoordinates] = useState<RealWorldCoordinate[]>([]);
    const [homographyMatrix, setHomographyMatrix] = useState<number[][]>([]);
    const [isCalibrated, setIsCalibrated] = useState(false);
    const [isCalculating, setIsCalculating] = useState(false);

    // Filter states - default to show non-calibrated only
    const [showNonCalibratedOnly, setShowNonCalibratedOnly] = useState(true);
    const [showNonValidatedOnly, setShowNonValidatedOnly] = useState(false);

    // Display options
    const [showVertexNumbers, setShowVertexNumbers] = useState(true);
    const [lineWidth, setLineWidth] = useState(8);
    const [dotWidth, setDotWidth] = useState(8);
    const [opacity, setOpacity] = useState(0.4);

    const theme = useTheme();

    useEffect(() => {
        fetchProjectData();
    }, [projectId]);

    useEffect(() => {
        if (project && project.calibrationType) {
            initializeLabels(project.calibrationType);
        }
    }, [project]);

    // Handle filter changes - auto-select appropriate sensor when filters change
    useEffect(() => {
        if (project && project.sensor_set.length > 0) {
            const filteredSensors = project.sensor_set.filter(sensor => {
                let showSensor = true;

                if (showNonCalibratedOnly && sensor.isCalibrated) {
                    showSensor = false;
                }

                if (showNonValidatedOnly && sensor.isValidated) {
                    showSensor = false;
                }

                return showSensor;
            });

            // If current selection is not in filtered list, select first available or clear
            if (selectedSensorId && !filteredSensors.some(sensor => sensor.id === selectedSensorId)) {
                if (filteredSensors.length > 0) {
                    setSelectedSensorId(filteredSensors[0].id);
                } else {
                    setSelectedSensorId('');
                }
            } else if (!selectedSensorId && filteredSensors.length > 0) {
                // Auto-select first sensor if none selected
                setSelectedSensorId(filteredSensors[0].id);
            }
        }
    }, [showNonCalibratedOnly, showNonValidatedOnly, project, selectedSensorId]);

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

            // Auto-select first sensor if available
            if (projectData.sensor_set.length > 0) {
                setSelectedSensorId(projectData.sensor_set[0].id);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch project data');
        } finally {
            setLoading(false);
        }
    };

    const initializeLabels = (calibrationType: string) => {
        if (calibrationType === 'image') {
            // For image calibration type, only allow ROI and tripwire buttons
            setLabels([
                {
                    id: 'roi',
                    name: 'ROI',
                    color: '#00ffff', // Bright cyan for high contrast
                    type: 'polygon',
                    limit: false,
                    draw: true,
                    class: 'roi',
                },
                {
                    id: 'tripwire',
                    name: 'Tripwire Line',
                    color: '#ff00ff', // Bright magenta for high visibility
                    type: 'polyline',
                    limit: false,
                    draw: true,
                    class: 'tripwire',
                },
                {
                    id: 'tripDirection',
                    name: 'Tripwire Direction',
                    color: '#ffff00', // Bright yellow for maximum visibility
                    type: 'polyline',
                    limit: false,
                    draw: true,
                    class: 'tripDirection',
                },
            ]);
        } else if (calibrationType === 'cartesian') {
            setLabels([
                {
                    id: 'cartCalib',
                    name: 'Calibration',
                    color: '#00ff00', // Bright green for calibration polygon
                    type: 'polygon',
                    limit: true,
                    draw: true,
                    class: 'cartCalib',
                },
                {
                    id: 'roi',
                    name: 'ROI',
                    color: '#00ffff', // Bright cyan for high contrast
                    type: 'polygon',
                    limit: false,
                    draw: true,
                    class: 'roi',
                },
                {
                    id: 'tripwire',
                    name: 'Tripwire Line',
                    color: '#ff00ff', // Bright magenta for high visibility
                    type: 'polyline',
                    limit: false,
                    draw: true,
                    class: 'tripwire',
                },
                {
                    id: 'tripDirection',
                    name: 'Tripwire Direction',
                    color: '#ffff00', // Bright yellow for maximum visibility
                    type: 'polyline',
                    limit: false,
                    draw: true,
                    class: 'tripDirection',
                },
            ]);
        }
    };

    const handleSensorChange = (event: SelectChangeEvent) => {
        setSelectedSensorId(event.target.value);
        // Reset calibration data when changing sensors
        setFigures([]);
        setUnfinishedFigure(null);
        setRealWorldCoordinates([]);
        setHomographyMatrix([]);
        setIsCalibrated(false);
    };

    const handleFigureChange = async (eventType: string, figureData: CalibrationFigure) => {
        switch (eventType) {
            case 'new': {
                const newFigures = [...figures, { ...figureData, id: `figure-${Date.now()}` }];
                setFigures(newFigures);
                setUnfinishedFigure(null);

                // Initialize coordinate array for new calibration figure
                if (figureData.class === 'calib' || figureData.class === 'cartCalib') {
                    const newCoordinates = Array(figureData.points.length)
                        .fill(0)
                        .map(() => ({ x: '0', y: '0' }));
                    setRealWorldCoordinates(newCoordinates);

                    // Send polygon to backend when complete
                    if (selectedSensorId) {
                        try {
                            const newFigure = { ...figureData, id: `figure-${Date.now()}` };
                            const sensorHeight = selectedSensor?.height || 1080;
                            const transformedFigures = flipY([newFigure], sensorHeight);
                            await pushPolygonUpdate(selectedSensorId, transformedFigures, config.analyticsUIServerEndpoint);
                        } catch (err) {
                            console.error('Failed to update polygon:', err);
                        }
                    }
                }
                break;
            }
            case 'replace':
                setFigures(prev => prev.map(f => (f.id === figureData.id ? figureData : f)));
                break;
            case 'delete':
                setFigures(prev => prev.filter(f => f.id !== figureData.id));
                // Reset coordinates if calibration figure is deleted
                if (figureData.class === 'calib' || figureData.class === 'cartCalib') {
                    setRealWorldCoordinates([]);
                    setHomographyMatrix([]);
                    setIsCalibrated(false);
                }
                break;
            case 'unfinished':
                setUnfinishedFigure(figureData);
                break;
            default:
                break;
        }
    };

    const handleStartDrawing = (labelId: string) => {
        const label = labels.find(l => l.id === labelId);
        if (!label) return;

        setUnfinishedFigure({
            id: labelId,
            type: label.type,
            points: [],
            class: label.class,
            color: label.color,
        });
    };

    const handleCoordinateChange = async (index: number, coordinate: RealWorldCoordinate) => {
        const newCoordinates = [...realWorldCoordinates];
        newCoordinates[index] = coordinate;
        setRealWorldCoordinates(newCoordinates);

        // Send edge lengths to backend whenever coordinates change
        if (selectedSensorId && figures.length > 0) {
            const calibrationFigure = figures.find(f => f.class === 'calib' || f.class === 'cartCalib');
            if (calibrationFigure) {
                try {
                    const sensorHeight = selectedSensor?.height || 1080;
                    const transformedFigures = flipY([calibrationFigure], sensorHeight);
                    await pushEdgeLengthsUpdate(selectedSensorId, transformedFigures, newCoordinates, config.analyticsUIServerEndpoint);
                } catch (err) {
                    console.error('Failed to update edge lengths:', err);
                }
            }
        }

        // Reset calibration when coordinates change
        setHomographyMatrix([]);
        setIsCalibrated(false);
    };

    const handleCalculateCalibration = async () => {
        if (!selectedSensor || figures.length === 0) {
            setError('Please select a sensor and create calibration figures');
            return;
        }

        const calibrationFigure = figures.find(f => f.class === 'calib' || f.class === 'cartCalib');
        if (!calibrationFigure) {
            setError('No calibration figure found');
            return;
        }

        const imagePoints: Point[] = calibrationFigure.points;
        const validation = validateCalibrationPoints(imagePoints, realWorldCoordinates, project?.calibrationType || 'cartesian');

        if (!validation.isValid) {
            setError(validation.errors.join(', '));
            return;
        }

        setIsCalculating(true);
        setError(null);

        try {
            // Request homography calculation from backend (polygon and edge lengths should already be sent)
            const homographyMatrix = await waitForHomography(
                selectedSensorId!,
                project?.calibrationType || 'cartesian',
                config.analyticsUIServerEndpoint
            );

            if (!homographyMatrix) {
                setError('Failed to calculate homography matrix from backend. Please check your point selections.');
                return;
            }

            setHomographyMatrix(homographyMatrix);
            setIsCalibrated(true);
        } catch (err) {
            setError('Error during calibration calculation: ' + (err instanceof Error ? err.message : 'Unknown error'));
        } finally {
            setIsCalculating(false);
        }
    };

    const handleAcceptCalibration = async () => {
        if (project?.calibrationType === 'image') {
            // For image calibration type, validate directly without requiring calibration step
            if (!selectedSensorId) {
                setError('Please select a sensor');
                return;
            }

            try {
                setError(null);

                // Separate figures by type and apply coordinate transformation
                const roiFigures = figures.filter(f => f.class === 'roi');
                const tripwireFigures = figures.filter(f => f.class === 'tripwire');
                const tripDirectionFigures = figures.filter(f => f.class === 'tripDirection');

                // Apply Y-coordinate flip transformation like ReactJS project does for image calibration
                // This converts from drawing coordinate system (bottom-left origin) to image coordinate system (top-left origin)
                const sensorHeight = selectedSensor?.height || 1080;
                const transformedRoiFigures = flipY(roiFigures, sensorHeight);
                const transformedTripwireFigures = flipY(tripwireFigures, sensorHeight);
                const transformedTripDirectionFigures = flipY(tripDirectionFigures, sensorHeight);

                const payload = {
                    roiPolygon: JSON.stringify(transformedRoiFigures),
                    tripwireLines: JSON.stringify(transformedTripwireFigures),
                    tripDirLines: JSON.stringify(transformedTripDirectionFigures),
                    isCalibrated: true,
                    isValidated: true,
                };

                const sensorResponse = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${selectedSensorId}/`, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        streamId: selectedSensorId,
                    },
                    body: JSON.stringify(payload),
                });

                if (!sensorResponse.ok) {
                    throw new Error(`Failed to update sensor: ${sensorResponse.status} ${sensorResponse.statusText}`);
                }

                // Refresh project data
                await fetchProjectData();

                // Clear drawings after successful validation
                setFigures([]);
                setUnfinishedFigure(null);
                setRealWorldCoordinates([]);
                setHomographyMatrix([]);
                setIsCalibrated(false);
            } catch (err) {
                setError(`Failed to validate sensor: ${err instanceof Error ? err.message : 'Unknown error'}`);
            }
            return;
        }

        // Original cartesian calibration logic
        if (!selectedSensorId || !isCalibrated) {
            setError('Please complete calibration first');
            return;
        }

        try {
            setError(null);

            // Step 0: Ensure inverted image dimensions are set correctly
            // This prevents the server fallback bug that swaps dimensions
            const selectedSensor = project?.sensor_set.find(s => s.id === selectedSensorId);
            if (selectedSensor) {
                const sensorHeight = selectedSensor.height || 1080;
                const sensorWidth = selectedSensor.width || 1920;

                const dimensionResponse = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${selectedSensorId}/`, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        streamId: selectedSensorId,
                    },
                    body: JSON.stringify({
                        invertImHeight: sensorHeight,
                        invertImWidth: sensorWidth,
                        invertImXPad: 0,
                        invertImYPad: 0,
                    }),
                });

                if (!dimensionResponse.ok) {
                    throw new Error(`Failed to set inverted image dimensions: ${dimensionResponse.status} ${dimensionResponse.statusText}`);
                }
            }

            // Step 1: Call invertImage API
            const invertResponse = await fetch(`${config.analyticsUIServerEndpoint}/api/invertImage/${selectedSensorId}/`, {
                headers: { streamId: selectedSensorId },
            });
            if (!invertResponse.ok) {
                throw new Error(`Failed to invert image: ${invertResponse.status} ${invertResponse.statusText}`);
            }

            const invertResult = await invertResponse.text();
            console.log('Image invert response:', invertResult);

            // Step 2: Update sensor with calibration data
            const sensorHeight = selectedSensor?.height || 1080;
            const transformedFigures = flipY(figures, sensorHeight);

            // Separate figures by type
            const calibrationFigures = transformedFigures.filter(f => f.class === 'calib' || f.class === 'cartCalib');
            const roiFigures = transformedFigures.filter(f => f.class === 'roi');
            const tripwireFigures = transformedFigures.filter(f => f.class === 'tripwire');
            const tripDirectionFigures = transformedFigures.filter(f => f.class === 'tripDirection');

            const payload = {
                sensorPolygon: JSON.stringify(calibrationFigures),
                roiPolygon: JSON.stringify(roiFigures),
                tripwireLines: JSON.stringify(tripwireFigures),
                tripDirLines: JSON.stringify(tripDirectionFigures),
                isCalibrated: true,
                isValidated: false,
            };

            const sensorResponse = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${selectedSensorId}/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    streamId: selectedSensorId,
                },
                body: JSON.stringify(payload),
            });

            if (!sensorResponse.ok) {
                throw new Error(`Failed to update sensor: ${sensorResponse.status} ${sensorResponse.statusText}`);
            }

            // Step 3: Refresh project data
            await fetchProjectData();

            // Clear drawings after successful calibration
            setFigures([]);
            setUnfinishedFigure(null);
            setRealWorldCoordinates([]);
            setHomographyMatrix([]);
            setIsCalibrated(false);
        } catch (err) {
            setError(`Failed to save calibration: ${err instanceof Error ? err.message : 'Unknown error'}`);
        }
    };

    const handleRedrawPolygons = () => {
        setFigures([]);
        setUnfinishedFigure(null);
        setRealWorldCoordinates([]);
        setHomographyMatrix([]);
        setIsCalibrated(false);
    };

    const selectedSensor = project?.sensor_set.find(s => s.id === selectedSensorId);

    // Apply flipY transformation like React UI does before sending to backend
    const flipY = (figures: CalibrationFigure[], height: number): CalibrationFigure[] => {
        return figures.map(figure => ({
            ...figure,
            points: figure.points.map(point => ({
                lat: height - point.lat, // Flip Y coordinate like React UI
                lng: point.lng, // Keep X coordinate as-is
            })),
        }));
    };

    if (loading) {
        return (
            <Paper elevation={2} sx={{ p: theme.spacing(3) }}>
                <Typography variant='h5' gutterBottom sx={{ mb: theme.spacing(3), fontWeight: theme.typography.fontWeightBold }}>
                    Calibration
                </Typography>
                <Typography>Loading...</Typography>
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
                Calibration
            </Typography>

            <Typography variant='body1' color='text.secondary' sx={{ mb: theme.spacing(3) }}>
                Calibrate sensors for project "{project.name}" ({project.calibrationType} calibration)
            </Typography>

            {project.sensor_set.length === 0 ? (
                <Alert severity='warning'>No sensors found for this project. Please add sensors before calibration.</Alert>
            ) : (
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
                                        <InputLabel>Select Sensor</InputLabel>
                                        <Select value={selectedSensorId} label='Select Sensor' onChange={handleSensorChange}>
                                            {project.sensor_set
                                                .filter(sensor => {
                                                    // Apply filters based on checkbox states
                                                    let showSensor = true;

                                                    if (showNonCalibratedOnly && sensor.isCalibrated) {
                                                        showSensor = false;
                                                    }

                                                    if (showNonValidatedOnly && sensor.isValidated) {
                                                        showSensor = false;
                                                    }

                                                    return showSensor;
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
                                                                    <Chip
                                                                        label='Calibrated'
                                                                        size='small'
                                                                        color='success'
                                                                        variant='outlined'
                                                                    />
                                                                )}
                                                                {sensor.isValidated && (
                                                                    <Chip
                                                                        label='Validated'
                                                                        size='small'
                                                                        color='primary'
                                                                        variant='outlined'
                                                                    />
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
                                                        checked={showNonCalibratedOnly}
                                                        onChange={e => setShowNonCalibratedOnly(e.target.checked)}
                                                        size='small'
                                                    />
                                                }
                                                label={<Typography variant='body2'>Show non-calibrated only</Typography>}
                                            />
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
                            {/* Instructions */}
                            <Card variant='outlined' sx={{ mb: 3 }}>
                                <CardContent>
                                    <Typography variant='h6' gutterBottom>
                                        Drawing Instructions
                                    </Typography>
                                    <Grid2 container spacing={2}>
                                        <Grid2 size={{ xs: 12, md: 6 }}>
                                            <Typography variant='body2' color='text.secondary'>
                                                • <strong>Left Click:</strong> Add points to shapes
                                                <br />• <strong>Double Click:</strong> Finish polygons (3+ points) and lines (2+ points)
                                                <br />• <strong>Right Click:</strong> Cancel the current drawing
                                                <br />• <strong>Tripwire Lines:</strong> Auto-complete after 2 points
                                            </Typography>
                                        </Grid2>
                                        <Grid2 size={{ xs: 12, md: 6 }}>
                                            <Typography variant='body2' color='text.secondary'>
                                                • <strong>F Key:</strong> Finish drawing current shape
                                                <br />• <strong>Mouse Wheel:</strong> Zoom in/out on the image
                                                <br />• <strong>Drag:</strong> Pan around the image when not drawing
                                            </Typography>
                                        </Grid2>
                                    </Grid2>

                                    {unfinishedFigure && (
                                        <Box sx={{ mt: 2, p: 2, backgroundColor: 'action.hover', borderRadius: 1 }}>
                                            <Typography variant='body2' color='primary' fontWeight='bold'>
                                                Currently drawing: {labels.find(l => l.id === unfinishedFigure.id)?.name} (
                                                {unfinishedFigure.points.length} points)
                                            </Typography>
                                        </Box>
                                    )}
                                </CardContent>
                            </Card>
                            {/* Drawing Controls */}
                            {
                                <Card variant='outlined' sx={{ mb: 3 }}>
                                    <CardContent>
                                        <Typography variant='h6' gutterBottom sx={{ mb: 3 }}>
                                            Drawing Controls
                                        </Typography>

                                        <Grid2 container spacing={3}>
                                            {/* Drawing Tools Section */}
                                            <Grid2 size={{ xs: 12 }}>
                                                <Typography variant='subtitle2' color='text.secondary' sx={{ mb: 2 }}>
                                                    Drawing Tools
                                                </Typography>
                                                <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                                                    {labels.map(label => (
                                                        <Button
                                                            key={label.id}
                                                            variant='outlined'
                                                            onClick={() => handleStartDrawing(label.id)}
                                                            disabled={
                                                                !!unfinishedFigure ||
                                                                (label.limit && figures.some(f => f.class === label.class))
                                                            }
                                                            sx={{
                                                                borderColor: label.color,
                                                                color: label.color,
                                                                minWidth: 140,
                                                                '&:hover': {
                                                                    borderColor: label.color,
                                                                    backgroundColor: theme.palette.action.hover,
                                                                },
                                                                '&:disabled': {
                                                                    opacity: 0.5,
                                                                },
                                                            }}
                                                        >
                                                            Start {label.name}
                                                        </Button>
                                                    ))}
                                                </Box>
                                            </Grid2>

                                            <Grid2 size={{ xs: 12 }}>
                                                <Divider />
                                            </Grid2>

                                            {/* Display Options and Actions Section */}
                                            <Grid2 size={{ xs: 12 }}>
                                                <Box
                                                    sx={{
                                                        display: 'flex',
                                                        gap: 3,
                                                        flexWrap: 'wrap',
                                                        alignItems: 'flex-start',
                                                        justifyContent: 'flex-start',
                                                    }}
                                                >
                                                    {/* Display Options */}
                                                    <Box sx={{ minWidth: 400, maxWidth: 600 }}>
                                                        <Typography variant='subtitle2' color='text.secondary' sx={{ mb: 2 }}>
                                                            Display Options
                                                        </Typography>
                                                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                                            <FormControlLabel
                                                                control={
                                                                    <Checkbox
                                                                        checked={showVertexNumbers}
                                                                        onChange={e => setShowVertexNumbers(e.target.checked)}
                                                                        size='small'
                                                                    />
                                                                }
                                                                label='Show vertex numbers'
                                                            />

                                                            <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                                                                <Box sx={{ minWidth: 120 }}>
                                                                    <Typography variant='body2' sx={{ mb: 1, fontWeight: 500 }}>
                                                                        Line Width: {lineWidth}px
                                                                    </Typography>
                                                                    <Slider
                                                                        value={lineWidth}
                                                                        onChange={(_, value) => setLineWidth(value as number)}
                                                                        min={1}
                                                                        max={16}
                                                                        step={1}
                                                                        size='small'
                                                                        sx={{ width: 100 }}
                                                                    />
                                                                </Box>
                                                                <Box sx={{ minWidth: 120 }}>
                                                                    <Typography variant='body2' sx={{ mb: 1, fontWeight: 500 }}>
                                                                        Dot Size: {dotWidth}px
                                                                    </Typography>
                                                                    <Slider
                                                                        value={dotWidth}
                                                                        onChange={(_, value) => setDotWidth(value as number)}
                                                                        min={2}
                                                                        max={16}
                                                                        step={1}
                                                                        size='small'
                                                                        sx={{ width: 100 }}
                                                                    />
                                                                </Box>
                                                                <Box sx={{ minWidth: 120 }}>
                                                                    <Typography variant='body2' sx={{ mb: 1, fontWeight: 500 }}>
                                                                        Opacity: {(opacity * 100).toFixed(0)}%
                                                                    </Typography>
                                                                    <Slider
                                                                        value={opacity}
                                                                        onChange={(_, value) => setOpacity(value as number)}
                                                                        min={0.2}
                                                                        max={1.0}
                                                                        step={0.1}
                                                                        size='small'
                                                                        sx={{ width: 100 }}
                                                                    />
                                                                </Box>
                                                            </Box>
                                                        </Box>
                                                    </Box>

                                                    {/* Divider */}
                                                    <Divider orientation='vertical' flexItem sx={{ mx: 2 }} />

                                                    {/* Actions */}
                                                    <Box sx={{ minWidth: 200, maxWidth: 250 }}>
                                                        <Typography variant='subtitle2' color='text.secondary' sx={{ mb: 2 }}>
                                                            Actions
                                                        </Typography>
                                                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                                            {/* Only show Calculate Calibration button for cartesian calibration type */}
                                                            {project?.calibrationType !== 'image' && !isCalibrated && (
                                                                <Button
                                                                    variant='outlined'
                                                                    startIcon={
                                                                        isCalculating ? <CircularProgress size={16} /> : <Calculate />
                                                                    }
                                                                    onClick={handleCalculateCalibration}
                                                                    disabled={figures.length === 0 || !!unfinishedFigure || isCalculating}
                                                                    fullWidth
                                                                >
                                                                    {isCalculating ? 'Calculating...' : 'Calculate Calibration'}
                                                                </Button>
                                                            )}

                                                            {/* Accept Calibration / Validate button */}
                                                            {((project?.calibrationType === 'image' && figures.length > 0) ||
                                                                (project?.calibrationType !== 'image' && isCalibrated)) && (
                                                                <Button
                                                                    variant='contained'
                                                                    color='success'
                                                                    onClick={handleAcceptCalibration}
                                                                    fullWidth
                                                                >
                                                                    {project?.calibrationType === 'image'
                                                                        ? 'Validate'
                                                                        : 'Accept Calibration'}
                                                                </Button>
                                                            )}

                                                            {/* Redraw Polygons button */}
                                                            {figures.length > 0 && (
                                                                <Button
                                                                    variant='outlined'
                                                                    startIcon={<Refresh />}
                                                                    onClick={handleRedrawPolygons}
                                                                    fullWidth
                                                                >
                                                                    Redraw Polygons
                                                                </Button>
                                                            )}

                                                            {/* Reset button */}
                                                            <Button
                                                                variant='outlined'
                                                                startIcon={<Refresh />}
                                                                onClick={() => {
                                                                    setFigures([]);
                                                                    setUnfinishedFigure(null);
                                                                    setRealWorldCoordinates([]);
                                                                    setHomographyMatrix([]);
                                                                    setIsCalibrated(false);
                                                                }}
                                                                fullWidth
                                                            >
                                                                Reset All
                                                            </Button>
                                                        </Box>
                                                    </Box>
                                                </Box>
                                            </Grid2>
                                        </Grid2>
                                    </CardContent>
                                </Card>
                            }

                            {/* Main Content Area */}
                            <Grid2 container spacing={3}>
                                {/* Calibration Canvas */}
                                <Grid2 size={{ xs: 12, lg: project?.calibrationType === 'image' ? 12 : 7 }}>
                                    <Card variant='outlined'>
                                        <CardContent sx={{ p: 0 }}>
                                            <Box
                                                sx={{
                                                    height: '600px',
                                                    borderRadius: theme.shape.borderRadius,
                                                }}
                                            >
                                                <CalibrationCanvas
                                                    sensor={selectedSensor}
                                                    figures={figures}
                                                    unfinishedFigure={unfinishedFigure}
                                                    labels={labels}
                                                    onChange={handleFigureChange}
                                                    showVertexNumbers={showVertexNumbers}
                                                    lineWidth={lineWidth}
                                                    dotWidth={dotWidth}
                                                    opacity={opacity}
                                                />
                                            </Box>
                                        </CardContent>
                                    </Card>
                                </Grid2>

                                {/* Coordinate Input Panel - Only show for cartesian calibration type */}
                                {project?.calibrationType !== 'image' && (
                                    <Grid2 size={{ xs: 12, lg: 5 }}>
                                        <CoordinateInput
                                            figures={figures}
                                            coordinates={realWorldCoordinates}
                                            onCoordinateChange={handleCoordinateChange}
                                            calibrationType={project?.calibrationType || 'cartesian'}
                                            disabled={isCalculating || isCalibrated}
                                        />
                                        {isCalibrated && (
                                            <Box sx={{ mt: 2, p: 2, backgroundColor: 'info.light', borderRadius: 1 }}>
                                                <Typography variant='body2' color='info.dark'>
                                                    <strong>Tip:</strong> Use the calibration results below to see which coordinates need
                                                    adjustment for better accuracy.
                                                </Typography>
                                            </Box>
                                        )}
                                    </Grid2>
                                )}

                                {/* Calibration Results - For cartesian: shown when calibrated, For image: shown when figures exist */}
                                {((project?.calibrationType === 'image' && figures.length > 0) ||
                                    (project?.calibrationType !== 'image' && isCalibrated && homographyMatrix.length > 0)) && (
                                    <>
                                        {/* Validation UI */}
                                        <Grid2 size={{ xs: 12 }}>
                                            {project?.calibrationType === 'image' ? (
                                                // Simple validation UI for image calibration type
                                                <Box
                                                    sx={{
                                                        mt: 2,
                                                        p: 2,
                                                        backgroundColor: 'background.paper',
                                                        borderRadius: 1,
                                                        border: '1px solid',
                                                        borderColor: 'divider',
                                                    }}
                                                >
                                                    <Typography variant='h6' gutterBottom>
                                                        Image Calibration Review
                                                    </Typography>
                                                    <Typography variant='body2' color='text.secondary'>
                                                        Review your regions and tripwires. Use the Actions panel to validate when ready.
                                                    </Typography>
                                                </Box>
                                            ) : (
                                                // Full CalibrationResult for cartesian calibration
                                                <CalibrationResult
                                                    homographyMatrix={homographyMatrix}
                                                    calibrationFigure={figures.find(f => f.class === 'calib' || f.class === 'cartCalib')!}
                                                    realWorldPolygon={realWorldCoordinates.map((coord, index) => {
                                                        const calibrationFigure = figures.find(
                                                            f => f.class === 'calib' || f.class === 'cartCalib'
                                                        );
                                                        if (!calibrationFigure || index >= calibrationFigure.points.length) {
                                                            return { lat: 0, lng: 0 };
                                                        }

                                                        // Convert user coordinates to the same format as polygon points
                                                        const x = parseFloat(coord.x) || 0;
                                                        const y = parseFloat(coord.y) || 0;

                                                        if (project?.calibrationType === 'cartesian') {
                                                            // For cartesian, coordinates are in cm, convert to pixel-like coordinates
                                                            return { lat: y, lng: x };
                                                        } else {
                                                            // For geographic, coordinates are lat/lng
                                                            return { lat: y, lng: x };
                                                        }
                                                    })}
                                                    calibrationType={project?.calibrationType || 'cartesian'}
                                                    height={selectedSensor?.height || 720}
                                                />
                                            )}
                                        </Grid2>
                                    </>
                                )}
                            </Grid2>
                        </>
                    )}
                </Box>
            )}
        </Paper>
    );
};

export default Calibration;
